/* Explicit claims and resource coverage inside the shared Inflight view. */
'use strict';
class TeamCommsClaims {
  constructor(work){this.work=work;this.resources=null;this.running=false;}
  async action(path,payload){
    const w=this.work;
    if(this.running||w.running)return;
    if(w.callbacks.dirty()){w.showError('Save or reconcile your draft first.');return;}
    this.running=true;
    w.$('claim-controls').querySelectorAll('button').forEach(b=>b.disabled=true);
    try{await w.request('/api/inflight'+path,payload);this.resources=null;await w.callbacks.refresh();}
    catch(e){w.showError(e.message+' Your input is retained.');}
    finally{this.running=false;w.$('claim-controls').querySelectorAll('button').forEach(b=>b.disabled=false);}
  }
  display(entry,pinned){
    const w=this.work,$=w.$,box=$('claim-controls');box.replaceChildren();
    if(pinned){if(entry.work.claim_id)box.textContent='Saved claim reference: '+entry.work.claim_id;return;}
    const current=entry.current_claim,offer=entry.current_offer;
    const title=document.createElement('h3');title.textContent='Claims and reservations';box.append(title);
    const info=document.createElement('p');
    info.textContent=current?`${current.holder_name} · ${current.state} · lease until ${new Date(current.deadline).toLocaleString()}. ${current.resources.length} resources retained. ${current.guard_run_ids.length?'A guarded command is active or awaiting reconciliation.':''}`:
      offer?`Offer open until ${new Date(offer.expires_at).toLocaleString()} · ${offer.policy} · ${offer.resource_ids.length} resources.`:'No active execution claim. The accountable owner is retained.';
    box.append(info);
    const canWrite=w.identity.scopes.includes('inflight:write'),manager=canWrite&&(w.identity.role==='admin'||entry.work.owner_id===w.identity.participant_id);
    const button=(label,handler)=>{const b=document.createElement('button');b.textContent=label;b.disabled=this.running;b.onclick=handler;box.append(b);return b;};
    const textarea=(label)=>{const l=document.createElement('label');l.textContent=label;const t=document.createElement('textarea');t.maxLength=4000;t.rows=2;l.append(t);box.append(l);return t;};
    if(current){
      for(const resource of current.resources){const p=document.createElement('p');p.textContent=`${resource.name} · ${resource.protection} · ${resource.coverage} · custodian ${resource.custodian_name}`;box.append(p);}
      const holder=canWrite&&current.holder_id===w.identity.participant_id;
      const reason=textarea('Progress, outcome, or confirmation that preceding work stopped');
      const evidence=textarea('Evidence (one item or link per line)');
      const update=(action,extra={})=>this.action('/claims/update',{claim_id:current.claim_id,expected_generation:current.generation,
        expected_revision:entry.revision,action,reason:reason.value,outcome:reason.value,
        evidence:evidence.value.split('\n').map(s=>s.trim()).filter(Boolean),...extra});
      if(holder){
        button('Renew lease',()=>this.action('/claims/update',{claim_id:current.claim_id,expected_generation:current.generation,action:'renew'}));
        button('Record progress',()=>update('progress',{state:'active'}));button('Report blocker',()=>update('progress',{state:'blocked'}));
        button('Complete with evidence',()=>update('complete'));button('Release stopped work',()=>update('release'));
      }
      if(manager)button('Confirm preceding work stopped',()=>update('confirm_stopped'));
      return;
    }
    if(['completed','failed','canceled'].includes(entry.work.state))return;
    if(offer&&canWrite&&offer.eligible_participant_ids.includes(w.identity.participant_id)){
      button('Claim offered work',()=>this.action('/claims',{offer_id:offer.offer_id,expected_revision:entry.revision,
        expected_generation:entry.work.generation,session_id:$('work-session').value||null}));
      const help=document.createElement('small');help.textContent='Uses the optional registered session selected under Executor and ownership handoff.';box.append(help);
    }
    if(!manager)return;
    const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Offer work and its resources';details.append(summary);box.append(details);
    const label=document.createElement('label');label.textContent='Execution policy ';const policy=document.createElement('select');
    for(const [value,text] of [['advisory','Advisory'],['guarded','Guarded entrypoints']]){const option=document.createElement('option');option.value=value;option.textContent=text;policy.append(option);}label.append(policy);details.append(label);
    const resourceBox=document.createElement('div');details.append(resourceBox);
    const render=items=>{resourceBox.replaceChildren();for(const r of items){const l=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.value=r.resource_id;l.append(check,document.createTextNode(' '+r.name+' · '+r.protection+(r.reservation?' · held':'')));resourceBox.append(l);}};
    if(this.resources)render(this.resources);
    else w.api('/api/inflight/resources?limit=100').then(result=>{this.resources=result.resources;if(w.entry?.entry_id===entry.entry_id)render(this.resources);
      if(result.next_offset!==null)resourceBox.append(document.createTextNode('Additional resources are available through the API.'));}).catch(e=>w.showError(e.message));
    const offerButton=document.createElement('button');offerButton.textContent='Offer to selected participant';offerButton.disabled=this.running;details.append(offerButton);
    offerButton.onclick=()=>this.action('/offers',{entry_id:entry.entry_id,expected_revision:entry.revision,expected_generation:entry.work.generation,
      eligible_participant_ids:[$('work-person').value||w.identity.participant_id],resource_ids:[...resourceBox.querySelectorAll('input:checked')].map(x=>x.value),
      expires_at:new Date(Date.now()+3600000).toISOString(),lease_seconds:300,policy:policy.value});
    const note=document.createElement('p');note.textContent='Uses the participant selected under Executor and ownership handoff, or yourself when none is selected. Offer expires in one hour; claims renew within five minutes. All selected resources are acquired together.';details.append(note);
  }
}
