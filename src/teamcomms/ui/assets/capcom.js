/* Shared topic feed, current component state and explicit attention controls. */
'use strict';
class TeamCommsCapcom {
  constructor(prefix,api,identity,error){Object.assign(this,{prefix,api,identity,error});this.topic=null;this.pending=null;this.busy=false;}
  el(tag,text,parent,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;if(parent)parent.append(e);return e;}
  button(text,parent,action){const b=this.el('button',text,parent);b.onclick=()=>action().catch(e=>this.error(e.message));return b;}
  field(text,parent,tag='input'){const l=this.el('label',text,parent),e=this.el(tag,undefined,l);return e;}
  async mutate(path,body){
    if(this.busy)throw new Error('A change is still being saved.');this.busy=true;
    const key='teamcomms:'+this.prefix+':'+this.identity.participant_id+':capcom-pending',signature=JSON.stringify({path,body});
    try{
      let pending=this.pending;try{pending=JSON.parse(sessionStorage.getItem(key)||'null')||pending;}catch(e){this.error('Browser recovery unavailable: '+e.message);}
      if(pending&&pending.signature!==signature){await this.api(pending.path,pending.body);this.pending=null;sessionStorage.removeItem(key);throw new Error('The previous uncertain change succeeded. Refresh before making another change.');}
      pending=pending||{signature,path,body:{operation_id:crypto.randomUUID(),...body}};this.pending=pending;
      try{sessionStorage.setItem(key,JSON.stringify(pending));}catch(e){this.error('Browser recovery unavailable; keep this tab open: '+e.message);}
      let result;try{result=await this.api(path,pending.body);}catch(e){if(e.status&&e.status<500){this.pending=null;sessionStorage.removeItem(key);}throw e;}
      this.pending=null;sessionStorage.removeItem(key);return result;
    }finally{this.busy=false;}
  }
  refs(text){return text.split('\n').map(s=>s.trim()).filter(Boolean).map(s=>{const [entry_id,revision]=s.split('@');return {entry_id,revision:revision?Number(revision):null};});}
  async start(){
    document.title='Capcom · TeamComms';document.querySelector('nav a[href="'+this.prefix+'/capcom"]').setAttribute('aria-current','page');document.querySelectorAll('main>section').forEach(e=>e.hidden=true);
    const root=this.el('section',undefined,document.querySelector('main'),'layout capcom');root.id='capcom-view';
    const aside=this.el('aside',undefined,root);this.el('h1','Capcom',aside);
    const form=this.el('form',undefined,aside),query=this.field('Find topic',form);query.id='capcom-query';
    const view=this.field('View',form,'select');for(const [v,t] of [['attention','Attention'],['followed','Following'],['all','All topics']]){const o=this.el('option',t,view);o.value=v;}
    const params=new URLSearchParams(location.search);query.value=params.get('query')||'';view.value=params.get('view')||'attention';
    this.el('button','Search',form);form.onsubmit=e=>{e.preventDefault();location.href=this.prefix+'/capcom?'+new URLSearchParams({query:query.value,view:view.value});};
    this.topicList=this.el('div',undefined,aside);this.topicList.id='capcom-topics';
    this.moreTopics=this.button('More topics',aside,()=>this.list(this.next));this.moreTopics.hidden=true;
    if(this.identity.scopes.includes('capcom:write')){
      const create=this.el('details',undefined,aside);this.el('summary','Create topic',create);
      const key=this.field('Topic key',create),title=this.field('Title',create);key.id='topic-key';title.id='topic-title';
      this.button('Create topic',create,async()=>{const r=await this.mutate('/api/capcom/topics',{key:key.value,title:title.value});location.href=this.prefix+r.path;});
    }
    this.panel=this.el('article',undefined,root);this.panel.id='capcom-thread';
    const id=location.pathname.slice(this.prefix.length).match(/^\/capcom\/([0-9a-f-]{36})$/)?.[1];
    await this.list(0);if(id)await this.open(id);else this.el('p','Open a topic to see its current work, decisions and retained history.',this.panel);
    await this.attention(aside);
  }
  async list(offset){
    const q=new URLSearchParams(location.search);q.set('offset',offset||0);q.set('limit',25);
    const r=await this.api('/api/capcom/topics?'+q);if(!offset)this.topicList.replaceChildren();
    for(const t of r.topics){const a=this.el('a',(t.unread?'● ':'')+t.title,this.topicList,'entry-row');a.href=this.prefix+t.path;this.el('small',t.key+(t.following?' · following':''),a);}
    this.next=r.next_offset;this.moreTopics.hidden=this.next===null;
  }
  async open(id){
    const generation=(this.openGeneration||0)+1;this.openGeneration=generation;this.panel.inert=true;this.panel.setAttribute('aria-busy','true');try{const t=await this.api('/api/capcom/topics/read?topic_id='+id);if(this.openGeneration!==generation)return;this.topic=t;this.panel.replaceChildren();
    const top=this.el('div',undefined,this.panel,'section-head');this.el('h2',t.title,top);this.button('Refresh',top,()=>this.open(id));
    this.el('p',t.key+' · '+(t.unread?'Unread changes':'Read')+' · topic revision '+t.revision,this.panel);
    const write=this.identity.scopes.includes('capcom:write');
    if(write){
      this.button(t.following?'Unfollow':'Follow',top,async()=>{await this.mutate('/api/capcom/follow',{topic_id:id,following:!t.following});await this.open(id);await this.list(0);});
      this.button('Mark topic read',top,async()=>{await this.mutate('/api/capcom/follow',{topic_id:id,read_sequence:t.last_sequence});await this.open(id);await this.list(0);});
    }
    const current=this.el('section',undefined,this.panel);current.id='capcom-current';this.el('h3','Current work',current);
    if(!t.current_work.length)this.el('p','No linked work.',current);
    for(const w of t.current_work){const row=this.el('div',undefined,current,'capcom-card');const a=this.el('a',w.title,row);a.href=this.prefix+w.path;
      this.el('p',w.work.state+' · owner '+w.work.owner_id+' · current revision '+w.revision,row);
      if(w.work.blockers)this.el('p','Current blocker: '+w.work.blockers,row);
      if(w.work.outcome)this.el('p','Outcome: '+w.work.outcome,row);
      for(const evidence of w.work.evidence)this.el('p',evidence,row);
      if(w.work.parent_id){const parent=this.el('a','Parent work',row);parent.href=this.prefix+'/inflight/'+w.work.parent_id;}
      for(const dep of w.work.dependencies){const a=this.el('a','Prerequisite '+dep,row);a.href=this.prefix+'/inflight/'+dep;}
    }
    for(const s of t.sampled_state){const row=this.el('div',undefined,current,'capcom-card');this.el('strong','Sampled state · '+s.source,row);this.el('small','Observed '+s.observed_at,row);this.el('p',s.content,row);}
    const decisions=this.el('section',undefined,this.panel);decisions.id='capcom-decisions';this.el('h3','Outstanding decisions',decisions);
    for(const d of t.decisions){const row=this.el('div',undefined,decisions,'capcom-card');this.el('p',d.content,row);
      if(write&&(this.identity.role==='admin'||d.author_id===this.identity.participant_id)){const text=this.field('Resolution',row,'textarea');this.button('Resolve decision',row,async()=>{await this.mutate('/api/capcom/decisions',{notice_id:d.notice_id,expected_revision:d.revision,resolution:text.value});await this.open(id);});}}
    if(!t.decisions.length)this.el('p','No outstanding decision.',decisions);
    if(t.more_decisions)this.el('p','More decisions are available in the paged history.',decisions);
    const docs=this.el('details',undefined,this.panel);this.el('summary','Documents and Dialog ('+(t.documents.length+t.dialog.length)+')',docs);
    for(const d of t.documents){const a=this.el('a',d.title+' · revision '+d.revision,docs);a.href=this.prefix+d.path;this.el('pre',d.content,docs);}
    for(const d of t.dialog){this.el('p',d.role+' · '+d.phase+' · '+d.host+' · '+d.occurred_at+' · source '+d.source_id,docs);this.el('pre',d.content+(d.truncated?'\n[Excerpt truncated]':''),docs);}
    if(t.unavailable_references)this.el('p',t.unavailable_references+' references are unavailable under your current access.',docs);
    if(write&&(this.identity.role==='admin'||t.owner_id===this.identity.participant_id)){
      const edit=this.el('details',undefined,this.panel);this.el('summary','Edit title and linked records',edit);
      const title=this.field('Title',edit);title.value=t.title;
      const refs=this.field('Entry or work UUID, optionally @revision; one per line',edit,'textarea');refs.value=t.links.entries.map(r=>r.entry_id+(r.revision?'@'+r.revision:'')).join('\n');refs.id='capcom-refs';
      const dialog=this.field('Dialog event IDs; one per line',edit,'textarea');dialog.value=t.links.dialog_event_ids.join('\n');
      this.button('Save topic',edit,async()=>{await this.mutate('/api/capcom/topics/update',{topic_id:id,expected_revision:t.revision,title:title.value,links:{entries:this.refs(refs.value),dialog_event_ids:dialog.value.split('\n').filter(s=>s.trim()).map(Number)}});await this.open(id);});
    }
    if(write)this.composer(this.panel,id);
    this.el('h3','Event and decision history',this.panel);this.el('small','Historical notices preserve their original observation time. They do not set current work state.',this.panel);
    this.feed=this.el('div',undefined,this.panel);this.feed.id='capcom-feed';this.moreNotices=this.button('Earlier notices',this.panel,()=>this.notices(id,this.before));await this.notices(id);if(this.openGeneration!==generation)return;
    const conversation=this.el('details',undefined,this.panel);this.el('summary','Canonical topic conversation',conversation);
    let offset=0,seen=[];const container=this.el('div',undefined,conversation);
    const load=this.button('Load conversation',conversation,async()=>{const r=await this.api('/api/capcom/conversation?'+new URLSearchParams({topic_id:id,offset,limit:25}));for(const m of r.messages){seen.push(m.message_id);this.el('strong',(m.author.session_name||m.author.name)+' · '+m.created_at,container);this.el('pre',m.content,container);for(const d of m.deliveries)this.el('small',d.session_id+' · '+d.state+' · '+(d.considered_at?'considered':'consideration unconfirmed'),container);this.el('small','Message '+m.message_id+(m.reply_to?' · reply to '+m.reply_to:''),container);}offset=r.next_offset;load.hidden=offset===null;load.textContent='More conversation';});
    if(write)this.button('Mark displayed conversation read',conversation,async()=>{for(let start=0;start<seen.length;start+=100)await this.mutate('/api/capcom/follow',{topic_id:id,message_ids:seen.slice(start,start+100)});await this.open(id);await this.list(0);});
    }finally{if(this.openGeneration===generation){this.panel.inert=false;this.panel.setAttribute('aria-busy','false');}}
  }
  composer(parent,id){
    const d=this.el('details',undefined,parent);this.el('summary','Add a notice or decision',d);
    const kind=this.field('Kind',d,'select');for(const v of ['event','state','progress','result','decision','discussion','routine'])this.el('option',v,kind);
    const urgency=this.field('Urgency',d,'select');for(const v of ['normal','urgent','alarm','routine'])this.el('option',v,urgency);
    const content=this.field('Notice',d,'textarea');content.id='capcom-content';content.maxLength=8000;
    const source=this.field('Source',d);source.maxLength=240;
    this.el('small','Saved in this topic. Severity alone does not notify an AI session.',d);
    const notify=this.field('Notify LLM',d);notify.type='checkbox';notify.id='capcom-notify-llm';
    notify.disabled=!this.identity.scopes.includes('comms:write');
    const selection=this.el('div',undefined,d);selection.hidden=true;
    const reason=this.field('Reason for requesting model attention',selection);reason.maxLength=1000;
    const recipients=this.field('Recipients',selection,'select');recipients.multiple=true;recipients.size=4;
    const load=this.button('Refresh recipients',selection,async()=>{
      const [directory,people]=await Promise.all([this.api('/api/comms/sessions?limit=100'),this.api('/api/participants?limit=200')]);
      if(directory.next_offset!==null||people.next_offset!==null)throw new Error('Directory is too large for this selector. Use Notify LLM from a script with an explicit audience.');
      const ai=new Set(people.participants.filter(p=>p.kind==='ai').map(p=>p.participant_id));recipients.replaceChildren();
      for(const s of directory.sessions.filter(s=>ai.has(s.participant_id))){const option=this.el('option',s.name+' · '+s.host+' · '+s.state,recipients);option.value=s.session_id;}
    });
    notify.onchange=()=>{selection.hidden=!notify.checked;if(notify.checked)load.click();};
    const record=this.button('Record notice',d,async()=>{const body={notice_id:this.pending?.body?.notice_id||crypto.randomUUID(),topic_id:id,kind:kind.value,urgency:urgency.value,content:content.value,source:source.value,observed_at:this.pending?.body?.observed_at||new Date().toISOString()};
      if(notify.checked){const session_ids=Array.from(recipients.selectedOptions).map(o=>o.value);if(!reason.value.trim()||!session_ids.length)throw new Error('Notify LLM requires a reason and selected recipients.');Object.assign(body,{notify_llm:true,notify_llm_reason:reason.value,audience:{session_ids}});}
      await this.mutate('/api/capcom/notices',body);content.value='';await this.open(id);await this.list(0);});
    notify.addEventListener('change',()=>{record.textContent=notify.checked?'Record notice and Notify LLM':'Record notice';});
  }
  async notices(id,before){
    const generation=this.openGeneration,feed=this.feed,more=this.moreNotices;const r=await this.api('/api/capcom/notices?'+new URLSearchParams({topic_id:id,limit:25,...(before?{before}:{})}));if(this.openGeneration!==generation||feed!==this.feed)return;if(!before)feed.replaceChildren();
    const routine=this.el('details',undefined,this.feed);let count=0;const summary=this.el('summary','Routine coordination',routine);
    for(const n of r.notices){const folded=!n.notify_llm&&n.kind==='routine'&&n.urgency==='routine';const row=this.el('div',undefined,folded?routine:this.feed,'capcom-card');if(folded)count++;
      row.id='notice-'+n.notice_id;this.el('strong',n.kind+' · '+n.urgency+' · '+n.author_name,row);this.el('small','Observed '+n.observed_at+' · recorded '+n.created_at+(n.source?' · '+n.source:''),row);this.el('p',n.content,row,'capcom-content');
      if(n.notify_llm)this.el('p','Notify LLM · '+n.notify_llm_reason,row);
      if(n.decision)this.el('p',n.decision.resolved_at?'Resolved: '+n.decision.resolution:'Decision open',row);
      for(const delivery of n.deliveries)this.el('small',delivery.session_id+' · '+delivery.state+' · presentation '+delivery.presentation.disposition+(delivery.presentation.due_at?' until '+delivery.presentation.due_at:'')+(delivery.presentation.coalesced_into?' into '+delivery.presentation.coalesced_into:'')+' · '+(delivery.considered_at?'considered':'consideration unconfirmed'),row);
    }
    summary.textContent='Routine coordination ('+count+')';routine.hidden=!count;this.before=r.next_before;this.moreNotices.hidden=this.before===null;
  }
  async attention(parent){
    if(!this.identity.scopes.includes('capcom:write'))return;
    const d=this.el('details',undefined,parent);this.el('summary','Session attention and subscription',d);
    const session=this.field('Owned session UUID',d);const mode=this.field('Routine presentation',d,'select');for(const v of ['immediate','record','batch'])this.el('option',v,mode);
    const seconds=this.field('Batch seconds (5–300)',d);seconds.type='number';seconds.value=30;
    const quiet=this.field('Quiet until (local time)',d);quiet.type='datetime-local';let policy=null;
    const status=this.el('p','Opt-in receivers apply these controls. Alarms and human instructions bypass routine filtering.',d);
    this.button('Load policy',d,async()=>{policy=await this.api('/api/capcom/attention?session_id='+encodeURIComponent(session.value));mode.value=policy.routine_mode;seconds.value=policy.batch_seconds;quiet.value=policy.quiet_until?new Date(new Date(policy.quiet_until).getTime()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16):'';status.textContent=policy.receiver_support?'Receiver supports attention controls.':'Receiver has not enabled attention_controls; its delivery remains immediate.';});
    this.button('Save policy',d,async()=>{if(!policy||policy.session_id!==session.value)throw new Error('Load this owned session policy first.');policy=await this.mutate('/api/capcom/attention',{session_id:session.value,expected_revision:policy.revision,routine_mode:mode.value,batch_seconds:Number(seconds.value),quiet_until:quiet.value?new Date(quiet.value).toISOString():null});status.textContent='Policy saved · revision '+policy.revision+(policy.receiver_support?'':' · receiver support not enabled');});
    this.button('Subscribe session to this topic',d,async()=>{if(!this.topic)throw new Error('Open a topic first.');await this.mutate('/api/capcom/follow',{topic_id:this.topic.topic_id,following:true,session_id:session.value});status.textContent='Session subscribed to '+this.topic.key;});
  }
}
