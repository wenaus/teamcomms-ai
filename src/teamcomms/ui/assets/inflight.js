/* Inflight controls reuse the common editor and revision/draft shell. */
'use strict';
class TeamCommsInflight {
  constructor(prefix,api,identity,showError) {
    this.prefix=prefix;this.api=api;this.identity=identity;this.showError=showError;
    this.mode=location.pathname.slice(prefix.length).startsWith('/inflight');
    this.$=id=>document.getElementById(id);this.pending=null;
  }
  setup(callbacks) {
    this.callbacks=callbacks;
    this.claimUI=new TeamCommsClaims(this);
    if(!this.mode)return;
    const $=this.$;
    $('entries-view').querySelector('h1').textContent='Inflight';
    document.querySelector('label[for=query]').textContent='Search work';
    $('query').placeholder='Title or description';
    $('kind-filter').innerHTML='<option value="operator">Operator work</option><option value="all">Include internal</option><option value="internal">Internal only</option>';
    $('kind-filter').setAttribute('aria-label','Visibility');
    $('status-filter').innerHTML='<option value="live">Live</option><option value="done">Done</option><option value="all">All work</option>';
    $('empty-title').textContent='Work that keeps its owner';
    $('empty-detail').textContent='Create work, coordination or an incident. Track progress, handoffs and completion evidence across sessions.';
    $('entry-status').closest('label').hidden=true;
    $('restore-version').hidden=true;
    const panel=document.createElement('section');panel.id='work-controls';panel.className='work-controls';
    panel.innerHTML=`<div id="work-summary" role="status"></div>
      <div class="row"><label id="work-form-label">Form <select id="work-form"><option>work</option><option>coordination</option><option>incident</option></select></label>
      <label>Visibility <select id="work-visibility"><option value="operator">Operator</option><option value="internal">Internal</option></select></label></div>
      <label>Completion criteria <textarea id="work-criteria" maxlength="4000" rows="3"></textarea></label>
      <details id="work-actions" hidden><summary>Progress, handoff and evidence</summary><button id="work-refresh" type="button">Refresh work</button>
      <div class="row"><label>Update <select id="work-action"><option value="active">Start / resume</option><option value="blocked">Blocked</option><option value="completed">Complete</option><option value="failed">Failed</option><option value="canceled">Cancel</option><option value="reopen">Reopen</option></select></label><button id="work-transition">Record update</button></div>
      <label>Progress, blocker, outcome or reopen reason <textarea id="work-reason" maxlength="4000" rows="2"></textarea></label>
      <label>Completion evidence (one item or link per line) <textarea id="work-evidence" rows="2"></textarea></label>
      <details><summary>Executor and ownership handoff</summary><p id="work-handoff"></p>
      <div class="row"><label>Participant <select id="work-person"><option value="">None</option></select></label><label>Registered executor session (optional) <input id="work-session"></label><button id="work-assign">Assign executor</button></div>
      <label>Handoff reason <input id="work-offer-reason" maxlength="4000"></label><button id="work-offer">Offer ownership</button>
      <button id="work-accept">Accept handoff</button><button id="work-reject">Decline handoff</button><button id="work-cancel-offer">Cancel offer</button><p id="work-people-note"></p></details>
      </details><div id="claim-controls"></div><div id="work-links"></div><div id="work-outcome"></div>
      <details id="work-new-links"><summary>Source and task relationships</summary>
      <label>Source document Entry ID <input id="work-source"></label><label>Saved source revision <input id="work-source-revision" type="number" min="1"></label>
      <label>Parent work ID <input id="work-parent"></label><label>Prerequisite work IDs (one per line) <textarea id="work-dependencies" rows="2"></textarea></label>
      <button id="work-graph" type="button" hidden>Update task relationships</button></details>`;
    $('new-fields').after(panel);
    for(const id of ['work-criteria','work-visibility']) $(id).addEventListener('input',callbacks.changed);
    $('work-refresh').onclick=()=>this.callbacks.refresh().catch(e=>this.showError(e.message));
    $('work-transition').onclick=()=>{
      const state=$('work-action').value,reason=$('work-reason').value;
      const change=state==='reopen'?{action:'reopen',reason}:['active','blocked'].includes(state)?{action:'progress',state,blockers:reason}:{action:'transition',state,outcome:reason,evidence:$('work-evidence').value.split('\n').map(s=>s.trim()).filter(Boolean)};
      this.act(change);
    };
    $('work-assign').onclick=()=>this.act({action:'assign_executor',participant_id:$('work-person').value||null,session_id:$('work-session').value||null});
    $('work-offer').onclick=()=>this.act({action:'offer_handoff',successor_id:$('work-person').value,reason:$('work-offer-reason').value});
    for(const [button,action] of [['work-accept','accept_handoff'],['work-reject','reject_handoff'],['work-cancel-offer','cancel_handoff']]) $(button).onclick=()=>this.act({action,handoff_id:this.entry.work.handoff.handoff_id});
    $('work-graph').onclick=()=>this.act({action:'graph',parent_id:$('work-parent').value||null,dependencies:$('work-dependencies').value.split('\n').map(s=>s.trim()).filter(Boolean)});
    if(this.identity.scopes.includes('directory:read'))this.loadPeople().catch(e=>this.showError(e.message));
  }
  async loadPeople() {
    const $=this.$;
    const result=await this.api('/api/participants?limit=100');
    for(const person of result.participants.filter(p=>p.active)) {
      const option=document.createElement('option');option.value=person.participant_id;option.textContent=person.name+' · '+person.kind;$('work-person').append(option);
    }
    if(result.next_offset!==null&&result.next_offset!==undefined)$('work-people-note').textContent='Showing the first 100 participants. Other participants remain available through the work API.';
  }
  canEdit(entry) {
    return this.identity.scopes.includes('inflight:write')&&(!entry || (!['completed','failed','canceled'].includes(entry.work.state)&&
      (this.identity.role==='admin'||[entry.work.owner_id,entry.work.executor_id].includes(this.identity.participant_id))));
  }
  display(entry,pinned=false) {
    if(!this.mode)return;
    this.entry=entry;this.pinned=pinned;const $=this.$,w=entry?.work;
    $('work-actions').hidden=!entry;
    $('work-form-label').hidden=!!entry;
    $('work-source').disabled=!!entry;$('work-source-revision').disabled=!!entry;
    $('work-graph').hidden=!entry;
    $('work-new-links').hidden=pinned;
    const manager=this.identity.scopes.includes('inflight:write')&&!pinned&&(!entry||this.identity.role==='admin'||w.owner_id===this.identity.participant_id);
    $('work-criteria').disabled=!manager;$('work-visibility').disabled=!manager;
    if(!entry){$('claim-controls').replaceChildren();for(const id of ['work-source','work-source-revision','work-parent','work-dependencies'])$(id).value='';$('work-summary').textContent='You remain accountable until a named successor accepts a handoff.';return;}
    $('work-summary').textContent=`${w.form} · ${w.state} · Owner ${entry.owner_name||w.owner_id} · Executor ${entry.executor_name||w.executor_id||'unassigned'}`+
      (!pinned&&entry.current_presence&&!entry.current_presence.owner_online?' · owner has no online session':'');
    $('work-parent').value=w.parent_id||'';$('work-dependencies').value=w.dependencies.join('\n');
    $('work-person').value=w.executor_id||'';$('work-session').value=w.session_id||'';
    $('work-handoff').textContent=w.handoff?`Ownership offered to ${w.handoff.successor_id}: ${w.handoff.reason}${w.handoff.expires_at?' · expires '+w.handoff.expires_at:''}. Current owner remains accountable.`:'No pending handoff.';
    const successor=!pinned&&w.handoff?.successor_id===this.identity.participant_id;
    $('work-accept').hidden=!successor;$('work-reject').hidden=!successor;$('work-cancel-offer').hidden=!(manager&&w.handoff);
    for(const id of ['work-assign','work-offer','work-graph'])$(id).disabled=!manager||['completed','failed','canceled'].includes(w.state);
    $('work-transition').disabled=pinned||(!manager&&!this.canEdit(entry))||!!entry.current_claim;
    this.claimUI.display(entry,pinned);
    $('work-outcome').textContent=[w.blockers&&'Blocker: '+w.blockers,w.outcome&&'Outcome: '+w.outcome,...w.evidence].filter(Boolean).join('\n');
    $('work-links').replaceChildren();
    const link=(path,text)=>{const a=document.createElement('a');a.href=this.prefix+path;a.textContent=text;$('work-links').append(a,document.createTextNode(' '));};
    if(w.parent_id)link('/inflight/'+w.parent_id,'Parent work');
    for(const id of w.dependencies)link('/inflight/'+id,'Prerequisite');
    for(const ref of entry.reference_paths||entry.state.relations.map(r=>({path:'/entries/'+r.entry_id+(r.revision?'?revision='+r.revision:''),relation:r.relation,revision:r.revision})))link(ref.path,ref.relation+(ref.revision?' v'+ref.revision:''));
    if(!pinned)this.api('/api/inflight?'+new URLSearchParams({parent_id:entry.entry_id,view:'all',visibility:'all',limit:100})).then(result=>{
      if(this.entry?.entry_id!==entry.entry_id)return;
      for(const child of result.entries)link(child.current_path,child.title+' · '+child.state);
      if(result.next_offset!==null)$('work-links').append(document.createTextNode('More subtasks available through the API.'));
    }).catch(e=>this.showError(e.message));
  }
  async request(path,payload) {
    // Keep a byte-equivalent logical request after an uncertain response. The
    // pending request is private to this browser tab and authenticated identity.
    const key='teamcomms:'+this.prefix+':'+this.identity.participant_id+':work-pending';
    let pending;
    try {pending=JSON.parse(sessionStorage.getItem(key)||'null');}catch{pending=this.pending;}
    const signature=JSON.stringify({path,payload});
    if(pending&&pending.signature!==signature){
      const prior=JSON.parse(pending.signature);
      try {
        const resolved=await this.api(prior.path,pending.request);
        try{sessionStorage.removeItem(key);}catch{}this.pending=null;
        const savedPath=resolved.current_path||(resolved.claim?.entry_id?'/inflight/'+resolved.claim.entry_id:null);
        if(savedPath){const link=document.createElement('a');link.href=this.prefix+savedPath;
          link.textContent='Open the work saved by your previous request';this.$('work-controls').prepend(link);}
        throw new Error('The previous uncertain request succeeded. Open that work before submitting different changes.');
      }catch(error){
        if(error.status&&error.status<500){try{sessionStorage.removeItem(key);}catch{}this.pending=null;}
        throw error;
      }
    }
    if(!pending)pending={signature,request:{...payload,operation_id:crypto.randomUUID()}};
    this.pending=pending;
    try {sessionStorage.setItem(key,JSON.stringify(pending));}catch{/* Existing draft recovery reports storage failure. */}
    let result;
    try {result=await this.api(path,pending.request);}
    catch(error){
      if(error.status&&error.status<500){try{sessionStorage.removeItem(key);}catch{}this.pending=null;}
      throw error;
    }
    try{sessionStorage.removeItem(key);}catch{}this.pending=null;return result;
  }
  async act(change) {
    if(this.running)return;
    if(this.callbacks.dirty()){this.showError('Save or reconcile your description draft before changing work state.');return;}
    this.running=true;
    try {
      const e=this.entry;
      await this.request('/api/inflight/mutate',{entry_id:e.entry_id,expected_revision:e.revision,expected_generation:e.work.generation,change});
      await this.callbacks.refresh();
    }catch(e){this.showError(e.message+' Your input is retained. Refresh the work before resolving a stale revision.');}
    finally{this.running=false;}
  }
  async save(entry,revision,fields) {
    const {criteria,visibility,...state}=fields;
    if(entry){
      const change={action:'edit',changes:state};delete change.changes.status;
      if(criteria!==entry.work.criteria)change.criteria=criteria;
      if(visibility!==entry.work.visibility)change.visibility=visibility;
      return this.request('/api/inflight/mutate',{entry_id:entry.entry_id,expected_revision:revision,expected_generation:entry.work.generation,change});
    }
    const $=this.$,source=$('work-source').value;
    state.relations=source?[{entry_id:source,revision:$('work-source-revision').value?Number($('work-source-revision').value):null,relation:'source'}]:[];
    return this.request('/api/inflight',{state,criteria,visibility,form:$('work-form').value,parent_id:$('work-parent').value||null,
      dependencies:$('work-dependencies').value.split('\n').map(s=>s.trim()).filter(Boolean)});
  }
}
