/* Shared browser shell and revision-aware editor. Apache-2.0. */
'use strict';
(async () => {
  const $ = id => document.getElementById(id);
  const config=JSON.parse(document.querySelector('meta[name=teamcomms-config]').content);
  const prefix=config.prefix, media=matchMedia('(prefers-color-scheme: dark)');
  const showError=message=>{ $('notice').textContent=message; $('notice').hidden=false; };
  const clearError=()=>{ $('notice').hidden=true; };
  function storageGet(key) { try { return localStorage.getItem(key); } catch(e) { showError('Browser storage unavailable: '+e.message); return null; } }
  function storageSet(key,value) { try { localStorage.setItem(key,value); return true; } catch(e) { showError('Local recovery could not be saved. Download your draft. '+e.message); return false; } }
  const pref='teamcomms:'+prefix+':';
  function theme() {
    const choice=$('theme').value;
    document.documentElement.dataset.theme=choice==='system'?(media.matches?'dark':'light'):choice;
    if (editor) editor.cm.setOption('theme',document.documentElement.dataset.theme==='dark'?'material-darker':'default');
  }
  let editor=null;
  const savedTheme=storageGet(pref+'theme');
  $('theme').value=['system','light','dark'].includes(savedTheme)?savedTheme:'system'; theme();
  $('theme').onchange=()=>{storageSet(pref+'theme',$('theme').value);theme();}; media.addEventListener('change',theme);
  async function api(path,data) {
    const options={credentials:'same-origin',cache:'no-store',redirect:'error',headers:{Accept:'application/json'}};
    if (data!==undefined) {
      options.method='POST'; options.body=JSON.stringify(data); options.headers['Content-Type']='application/json';
      if (config.csrf_url) {
        const csrfResponse=await fetch(config.csrf_url,{credentials:'same-origin',cache:'no-store',redirect:'error'});
        if (!csrfResponse.ok) throw new Error('Browser authentication expired or unavailable. Sign in again; your draft is retained.');
        const csrf=await csrfResponse.json();
        if (!/^X-[A-Za-z0-9-]+$/.test(csrf.header_name) || typeof csrf.token!=='string') throw new Error('Invalid browser CSRF configuration');
        options.headers[csrf.header_name]=csrf.token;
      }
    }
    const response=await fetch(prefix+path,options);
    let value;
    try { value=await response.json(); } catch { throw new Error(`Service returned HTTP ${response.status} without JSON. Your draft is retained.`); }
    if (!response.ok || value.error) { const error=new Error(value.error||`HTTP ${response.status}`); error.status=response.status; throw error; }
    return value;
  }
  let identity;
  try { identity=await api('/api/whoami'); } catch(e) { showError(e.message); return; }
  $('identity').textContent=`${identity.name} · ${identity.team_name}`;
  if(config.csrf_url) {
    try { const r=await fetch(config.csrf_url,{credentials:'same-origin',cache:'no-store',redirect:'error'});if(!r.ok)throw new Error('Browser CSRF setup failed.'); }
    catch(e){showError(e.message);}
  }
  if(location.pathname.slice(prefix.length).startsWith('/capcom')){try{await new TeamCommsCapcom(prefix,api,identity,showError).start();}catch(e){showError(e.message);}return;}
  const workUI=new TeamCommsInflight(prefix,api,identity,showError), isWork=workUI.mode;
  const canWrite=identity.scopes.includes(isWork?'inflight:write':'entries:write');
  const readPath=isWork?'/api/inflight/read?':'/api/entries/read?';
  let entry=null, base=null, revision=null, busy=false, loading=false, blocked=false, remote=null;
  let isPouch=false, pinned=false;
  const documentPath=()=>prefix+(isPouch?'/pouch':(isWork?'/inflight/':'/entries/')+entry.entry_id);
  function links() {
    $('permalink').href=documentPath()+'?revision='+revision;$('permalink').hidden=false;
    $('current-document').href=documentPath();$('current-document').hidden=!pinned;
    $('export-saved').hidden=!isPouch;
    let sourceLink=$('create-source-work');
    if(!sourceLink){sourceLink=document.createElement('a');sourceLink.id='create-source-work';$('export-saved').after(sourceLink);}
    sourceLink.hidden=!isPouch||!identity.scopes.includes('inflight:write');sourceLink.textContent='Create work from this revision';sourceLink.href=prefix+'/inflight?'+new URLSearchParams({source_entry:entry.entry_id,source_revision:revision});
  }
  function writable() {
    const allowed=canWrite&&!pinned&&(!isWork||workUI.canEdit(entry));
    editor.cm.setOption('readOnly',!allowed);
    for(const id of ['title','tags','entry-status','priority','autosave','replace-one','rebase','take-remote','recover','save']) $(id).disabled=!allowed;
    document.querySelectorAll('[data-edit]').forEach(b=>b.disabled=!allowed);
    if(isWork)workUI.display(entry,pinned);
  }
  let recovered=null, selectedVersion=null, timer=null, listOffset=null, historyOffset=null, previewNumber=0;
  let tab;
  try { tab=sessionStorage.getItem(pref+'tab')||crypto.randomUUID();sessionStorage.setItem(pref+'tab',tab); }
  catch(e) { tab=crypto.randomUUID();showError('Per-tab storage unavailable: '+e.message); }
  const draftPrefix=pref+identity.team_id+':'+identity.participant_id+':draft:';
  const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  const editable=state=>({title:state.title,content:state.content,tags:state.tags,status:state.status,priority:state.priority,...(isWork?{criteria:state.metadata?.inflight?.criteria||'',visibility:state.metadata?.inflight?.visibility||'operator'}:{})});
  const fields=()=>({title:$('title').value,content:editor.value,tags:[...new Set($('tags').value.split(',').map(s=>s.trim()).filter(Boolean))],status:$('entry-status').value,priority:$('priority').value?Number($('priority').value):null,...(isWork?{criteria:$('work-criteria').value,visibility:$('work-visibility').value}:{})});
  const dirty=()=>base!==null&&!same(fields(),base);
  const draftKey=()=>draftPrefix+(entry?.entry_id||(isWork?'new-work':'new'))+':'+tab;
  function status(text,state='saved') { $('save-state').textContent=text;$('save-state').dataset.state=state; }
  function retain() {
    if (!editor || !base || pinned) return;
    storageSet(draftKey(),JSON.stringify({entry_id:entry?.entry_id||null,revision,fields:fields(),base,kind:$('new-kind').value,slug:$('slug').value,ts:Date.now()}));
  }
  function changed() {
    if (loading) return;
    $('restore-version').disabled=!canWrite||pinned||dirty()||busy||blocked;
    retain();
    if (blocked) status('Conflicted — draft retained','conflicted');
    else status(dirty()?'Unsaved — recovery draft kept':'Saved',dirty()?'unsaved':'saved');
    clearTimeout(timer);
    if ($('autosave').checked && entry && dirty() && !blocked && canWrite && !pinned) timer=setTimeout(()=>save(),1800);
  }
  editor=new TeamCommsEditor($('content'),changed,()=>save(),()=>findOpen(),showError); theme();
  if(!canWrite) {
    editor.cm.setOption('readOnly',true);
    for(const id of ['title','tags','entry-status','priority','autosave','replace-one','rebase','take-remote','recover']) $(id).disabled=true;
    document.querySelectorAll('[data-edit]').forEach(b=>b.disabled=true);
  }
  function fill(values,resetUndo=false) {
    loading=true;
    $('title').value=values.title;editor.value=values.content;$('tags').value=values.tags.join(', ');
    $('entry-status').value=values.status;$('priority').value=values.priority??'';
    if(isWork){$('work-criteria').value=values.criteria||'';$('work-visibility').value=values.visibility||'operator';}
    if(resetUndo) editor.cm.clearHistory();
    loading=false;
  }
  $('autosave').checked=storageGet(pref+'autosave')==='true';
  $('autosave').onchange=()=>{storageSet(pref+'autosave',String($('autosave').checked));changed();};
  for(const id of ['title','tags','entry-status','priority','slug','new-kind']) $(id).addEventListener('input',changed);
  for(const button of document.querySelectorAll('[data-edit]')) button.onclick=()=>{setView('source');editor.action(button.dataset.edit);};
  function download(value,name='teamcomms-draft.md',type='text/markdown;charset=utf-8') {
    const url=URL.createObjectURL(new Blob([value],{type}));
    const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  $('export').onclick=()=>download(editor.value,(entry?.slug||'teamcomms-draft')+'.md');
  $('export-saved').onclick=async()=>{
    try {
      const saved=await api('/api/pouch/export?'+new URLSearchParams({revision}));
      download(JSON.stringify({...saved,revision_url:location.origin+prefix+saved.revision_path},null,2)+'\n',`pouch-revision-${saved.revision}.json`,'application/json');
    }catch(e){showError(e.message);}
  };
  function offerRecovery() {
    if(pinned){$('recovery').hidden=true;return;}
    const candidates=[];
    try {
      for(let i=0;i<localStorage.length;i++) {
        const key=localStorage.key(i);
        if(!key.startsWith(draftPrefix+(entry?.entry_id||(isWork?'new-work':'new'))+':'))continue;
        try { const d=JSON.parse(localStorage.getItem(key));
          if(d?.fields && d.base && !same(d.fields,d.base) && !same(d.fields,base)) candidates.push(d);
        } catch(e) {showError('A saved recovery draft could not be read: '+e.message);}
      }
    }catch(e){showError('Recovery drafts unavailable: '+e.message);}
    recovered=candidates.sort((a,b)=>b.ts-a.ts)[0]||null;
    $('recovery').hidden=!recovered;
    if(recovered)$('recovery-label').textContent=`Recovery draft from ${new Date(recovered.ts).toLocaleString()} (based on revision ${recovered.revision??'new'}). `;
  }
  $('recover').onclick=()=>{
    const d=recovered;if(!d)return;
    fill(d.fields);$('slug').value=d.slug||$('slug').value;$('new-kind').value=d.kind||'note';
    $('recovery').hidden=true;
    if(entry && d.revision!==revision){blocked=true;remote=entry;$('conflict').hidden=false;$('conflict-label').textContent=`Recovered draft used revision ${d.revision}; server is revision ${revision}. Compare before saving.`;}
    changed();setView('source');editor.cm.focus();
  };
  $('download-recovery').onclick=()=>download(recovered.fields.content,'recovered-draft.md');
  $('keep-current').onclick=()=>{ $('recovery').hidden=true; };
  async function openEntry(id,options={}) {
    if(busy)return;
    if(dirty()&&!confirm('Leave this entry? Your unsaved draft remains available for recovery.'))return;
    if(dirty())retain();clearTimeout(timer);
    busy=true;
    let result;
    try {
      const params={max_content_length:40000};if(id)params.entry_id=id;
      if(options.revision!==undefined&&options.revision!==null)params.revision=options.revision;
      result=await api((options.pouch?'/api/pouch?':readPath)+new URLSearchParams(params));
    }
    finally {busy=false;}
    if(dirty())retain();
    isPouch=!!options.pouch;pinned=options.revision!==undefined&&options.revision!==null;
    entry=result;revision=result.revision;base=editable(result.state);blocked=false;remote=null;
    fill(base,true);$('empty').hidden=true;$('editor-pane').hidden=false;$('new-fields').hidden=true;
    $('entry-label').textContent=`${isPouch?'Pouch':result.slug||result.kind} · revision ${revision}${pinned?' · read only':''}`;
    links();
    $('conflict').hidden=true;$('comparison').hidden=true;$('history').hidden=false;
    writable();
    clearError();status(pinned?'Saved revision — read only':'Saved');history.replaceState(null,'',documentPath()+(pinned?'?revision='+revision:''));
    document.title=(base.title||'Untitled')+' · TeamComms';
    offerRecovery();await listHistory();await setView(storageGet(pref+'view')||'source');
    editor.cm.refresh();
  }
  $('new-entry').onclick=()=>{
    if(busy || (dirty()&&!confirm('Start a new entry? The current draft will remain recoverable.')))return;
    if(dirty())retain();clearTimeout(timer);entry=null;revision=null;blocked=false;remote=null;isPouch=false;pinned=false;writable();
    base={title:'',content:'',tags:[],status:'active',priority:null,...(isWork?{criteria:'',visibility:'operator'}:{})};fill(base,true);
    $('slug').value='note-'+crypto.randomUUID();$('new-kind').value='note';
    $('empty').hidden=true;$('editor-pane').hidden=false;$('new-fields').hidden=isWork;
    $('history').hidden=true;$('comparison').hidden=true;$('conflict').hidden=true;
    $('entry-label').textContent=isWork?'New work':'New entry';$('permalink').hidden=true;$('current-document').hidden=true;$('export-saved').hidden=true;status('New — save to create','unsaved');
    history.replaceState(null,'',prefix+(isWork?'/inflight':'/entries'));offerRecovery();setView('source');$('title').focus();
  };
  $('new-entry').disabled=!canWrite;
  async function detectConflict() {
    if(!entry)return;
    remote=await api(readPath+new URLSearchParams({entry_id:entry.entry_id,max_content_length:40000}));
    blocked=true;$('conflict').hidden=false;
    $('conflict-label').textContent=`Your base is revision ${revision}; latest is revision ${remote.revision}, by ${remote.author_id}, ${new Date(remote.created_at).toLocaleString()}.`;
    status('Conflicted — draft retained','conflicted');
  }
  async function save() {
    if(busy||blocked||!base||!canWrite||pinned||(isWork&&(!workUI.canEdit(entry)||workUI.running)))return;
    if(entry&&!dirty()){status('Saved');return;}
    clearTimeout(timer);retain();const sent=fields();
    if(sent.content.length>40000){showError('This entry exceeds 40,000 characters. Download the draft or divide it into entries.');return;}
    const previousDraftKey=draftKey();
    let saved=false;
    busy=true;status('Saving…','saving');$('save').disabled=true;
    try {
      const request=entry?{entry_id:entry.entry_id,expected_revision:revision,changes:sent}:{kind:$('new-kind').value,slug:$('slug').value,state:sent};
      const result=isWork?await workUI.save(entry,revision,sent):await api(entry?'/api/entries/update':'/api/entries',request);
      entry=isWork?result:{...entry,...result,state:{...entry?.state,...sent}};revision=result.revision;base=sent;saved=true;
      $('new-fields').hidden=true;$('history').hidden=false;$('entry-label').textContent=`${entry.slug||entry.kind} · revision ${revision}`;
      links();if(isWork)workUI.display(entry,pinned);
      history.replaceState(null,'',documentPath());
      clearError();
      if(previousDraftKey!==draftKey())storageSet(previousDraftKey,JSON.stringify({fields:sent,base:sent,ts:Date.now()}));
      retain();status(dirty()?'Unsaved — newer typing retained':'Saved',dirty()?'unsaved':'saved');
      await Promise.all([listHistory(),listEntries()]).catch(e=>showError('Saved; history/list refresh unavailable: '+e.message));
    } catch(e) {
      retain();status('Save failed — draft retained','error');showError(e.message);
      if(e.status===409 && entry) {try{await detectConflict();}catch(err){showError(e.message+' Latest revision unavailable: '+err.message);blocked=true;}}
      else if(!entry) showError(e.message+' New-entry retry uses the same slug to prevent duplicates. Search that slug if the response was lost.');
    } finally {busy=false;$('save').disabled=!canWrite||(isWork&&!workUI.canEdit(entry));if(saved&&dirty()&&$('autosave').checked)timer=setTimeout(()=>save(),1800);}
  }
  $('save').onclick=save;
  async function setView(mode) {
    storageSet(pref+'view',mode);
    const rendered=mode==='rendered';$('source').hidden=rendered;$('rendered').hidden=!rendered;
    $('source-mode').setAttribute('aria-pressed',String(!rendered));$('rendered-mode').setAttribute('aria-pressed',String(rendered));
    const n=++previewNumber;
    if(!rendered){editor.cm.refresh();return;}
    $('rendered').textContent='Rendering…';
    try {
      const result=await api('/api/entries/render',{content:editor.value});
      if(n!==previewNumber)return;
      $('rendered').innerHTML=result.html;
      $('rendered').querySelectorAll('a').forEach(a=>{a.target='_blank';a.rel='noopener noreferrer';});
      Prism.highlightAllUnder($('rendered'));
      $('rendered').querySelectorAll('.arithmatex').forEach(el=>{
        const text=el.textContent;const display=text.startsWith('\\[');
        katex.render(text.slice(2,-2),el,{displayMode:display,throwOnError:false,trust:false});
      });
    }catch(e){$('rendered').textContent='Preview unavailable. Source and draft are intact.';showError(e.message);}
  }
  $('source-mode').onclick=()=>setView('source');$('rendered-mode').onclick=()=>setView('rendered');
  function findOpen(){setView('source');$('find-bar').hidden=false;$('find-text').focus();}
  $('find-toggle').onclick=findOpen;
  $('find-next').onclick=()=>{$('find-status').textContent=editor.find($('find-text').value)?'Match selected':'No match';};
  $('find-text').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('find-next').click();}};
  $('replace-one').onclick=()=>{if(editor.cm.getSelection()===$('find-text').value && $('find-text').value)editor.cm.replaceSelection($('replace-text').value,'around','+input');else $('find-status').textContent='Find and select a match first';};
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();save();}});
  async function listEntries(more=false) {
    const params={limit:25,offset:more?listOffset:0};
    for(const [key,id] of (isWork?[['query','query'],['visibility','kind-filter'],['view','status-filter']]:[['query','query'],['kind','kind-filter'],['status','status-filter']]))if($(id).value)params[key]=$(id).value;
    const result=await api((isWork?'/api/inflight?':'/api/entries?')+new URLSearchParams(params));
    if(!more)$('entry-list').replaceChildren();
    for(const item of result.entries){
      const a=document.createElement('a');a.className='entry-row';a.href=prefix+(isWork?'/inflight/':'/entries/')+item.entry_id;
      a.textContent=item.title||item.slug||'Untitled';const small=document.createElement('small');
      small.textContent=isWork?`${item.state} · ${item.owner_name} · v${item.revision}${item.blockers?' · '+item.blockers:''}`:`${item.kind} · v${item.revision} · ${new Date(item.modified_at).toLocaleString()}`;a.append(small);
      if(entry?.entry_id===item.entry_id)a.setAttribute('aria-current','page');
      a.onclick=e=>{e.preventDefault();clearError();openEntry(item.entry_id).catch(e=>showError(e.message));};$('entry-list').append(a);
    }
    if(!result.entries.length&&!more)$('entry-list').textContent='No entries match.';
    listOffset=result.next_offset;$('more-entries').hidden=listOffset===null;
  }
  $('search-form').onsubmit=e=>{e.preventDefault();listEntries().catch(e=>showError(e.message));};
  $('more-entries').onclick=()=>listEntries(true).catch(e=>showError(e.message));
  async function listHistory(more=false) {
    if(!entry)return;
    const result=await api((isWork?'/api/inflight/changes?':'/api/entries/revisions?')+new URLSearchParams({entry_id:entry.entry_id,limit:25,offset:more?historyOffset:0}));
    if(!more)$('revision-list').replaceChildren();
    for(const r of result.revisions){
      const row=document.createElement('div');row.className='revision';const button=document.createElement('button');button.textContent='Compare v'+r.revision;
      button.onclick=()=>compare(r.revision).catch(e=>showError(e.message));
      const text=document.createElement('small');text.textContent=`${new Date(r.created_at).toLocaleString()} · ${r.author_id}${r.restored_from?' · restored':''}${r.notice?' · '+r.notice:''}`;
      const link=document.createElement('a');link.href=documentPath()+'?revision='+r.revision;link.textContent='Open saved v'+r.revision;
      row.append(button,link,text);$('revision-list').append(row);
    }
    historyOffset=result.next_offset;$('more-revisions').hidden=historyOffset===null;
  }
  $('more-revisions').onclick=()=>listHistory(true).catch(e=>showError(e.message));
  async function compare(number) {
    const id=entry.entry_id, draft=editor.value;
    const [version,result]=await Promise.all([
      api(readPath+new URLSearchParams({entry_id:id,revision:number,max_content_length:40000})),
      api('/api/entries/compare',{entry_id:id,revision:number,content:draft,...(isWork?{component:'inflight'}:{})})]);
    if(entry?.entry_id!==id)return;
    selectedVersion=version;$('comparison').hidden=false;
    $('comparison-title').textContent=`Revision ${number} · ${version.state.title||'Untitled'}`;
    $('version-content').textContent=version.state.content;$('draft-content').textContent=draft;$('diff').textContent=result.diff||'No content differences.';
    $('restore-version').disabled=!canWrite||pinned||dirty()||busy||blocked;
    $('restore-version').title=dirty()?'Save or reconcile your active draft before restoring a revision.':'';
    $('comparison').scrollIntoView({block:'nearest'});
  }
  $('close-comparison').onclick=()=>{$('comparison').hidden=true;};
  $('show-remote').onclick=()=>compare(remote.revision).catch(e=>showError(e.message));
  $('rebase').onclick=()=>{
    if(!remote||busy||!confirm('Use your draft on top of the latest revision? Review the comparison first. Your next save will replace the editable fields; other metadata and relations are preserved.'))return;
    revision=remote.revision;base=editable(remote.state);entry=remote;blocked=false;$('conflict').hidden=true;remote=null;writable();changed();
  };
  $('take-remote').onclick=()=>{
    if(!remote||busy)return;
    retain();storageSet(draftKey()+':before-latest',JSON.stringify({entry_id:entry.entry_id,revision,fields:fields(),base,ts:Date.now()}));
    entry=remote;revision=remote.revision;base=editable(remote.state);fill(base);blocked=false;remote=null;$('conflict').hidden=true;retain();status('Latest loaded — prior draft remains recoverable');
    writable();offerRecovery();setView(storageGet(pref+'view')||'source');
  };
  $('restore-version').onclick=async()=>{
    if(!canWrite||pinned||!selectedVersion||busy||blocked||dirty()||!confirm(`Restore all fields from revision ${selectedVersion.revision} as a new revision? Current history remains available.`))return;
    const target=selectedVersion,before=fields();busy=true;
    try {
      const result=await api('/api/entries/restore',{entry_id:entry.entry_id,expected_revision:revision,revision:target.revision});
      base=editable(target.state);entry={...result,state:target.state};revision=result.revision;
      if(same(fields(),before))fill(base);
      clearError();retain();status(dirty()?'Restored — newer typing retained':'Restored as revision '+revision,dirty()?'unsaved':'saved');
      $('entry-label').textContent=`${entry.slug||entry.kind} · revision ${revision}`;
      links();
      await Promise.all([listHistory(),listEntries()]).catch(e=>showError('Restored; history/list refresh unavailable: '+e.message));$('comparison').hidden=true;
    } catch(e) {
      showError(e.message);
      if(e.status===409)try{await detectConflict();}catch(error){showError(error.message);blocked=true;}
    } finally {busy=false;}
  };
  window.addEventListener('beforeunload',e=>{if(dirty()){retain();e.preventDefault();e.returnValue='';}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&dirty())retain();});
  async function sessions(){
    const result=await api('/api/comms/sessions?limit=100');$('session-list').replaceChildren();
    for(const s of result.sessions){const row=document.createElement('div');row.className='session';row.textContent=`${s.name} · ${s.host} · ${s.client} · ${s.online?'online':'offline'} · ${s.state||''} · ${s.delivery_mode||''} · ${s.session_id}`;$('session-list').append(row);}
    if(result.next_offset!==null){const p=document.createElement('p');p.textContent='Showing the first 100 sessions.';$('session-list').append(p);}
  }
  $('refresh-sessions').onclick=()=>sessions().catch(e=>showError(e.message));
  async function dialog(){
    const params={limit:40};if($('dialog-host').value)params.host=$('dialog-host').value;
    const result=await api('/api/dialog?'+new URLSearchParams(params));$('dialog-list').replaceChildren();
    for(const d of result.events||[]){const row=document.createElement('div');row.className='dialog-record';const h=document.createElement('strong');
      h.textContent=`${d.host||''} · ${d.role||d.actor_role||''} · ${d.phase||''} · ${d.occurred_at||''}`;
      const attribution=document.createElement('small');attribution.textContent=JSON.stringify(d.attribution||{})+(d.truncated?' · content truncated':'')+(d.message_id?' · message '+d.message_id:'');
      const body=document.createElement('pre');body.textContent=d.content||'';row.append(h,attribution,body);$('dialog-list').append(row);}
    const p=document.createElement('p');p.textContent=(result.truncated?'More history is available through the Dialog API. ':'')+(result.coverage?.detail||'Bounded opt-in history; coverage may be incomplete.');$('dialog-list').append(p);
  }
  $('dialog-form').onsubmit=e=>{e.preventDefault();dialog().catch(e=>showError(e.message));};
  for(const a of document.querySelectorAll('header a')) a.addEventListener('click',e=>{if(busy){e.preventDefault();showError('A save is in progress. Your draft is retained.');}});
  async function openPouch() {
    try {await openEntry(null,{pouch:true,revision:new URLSearchParams(location.search).get('revision')});}
    catch(e){
      if(e.status!==404||location.search)throw e;
      $('empty-title').textContent='The team’s Pouch';
      $('empty-detail').textContent='One shared working document, with authorship and preserved revisions. Create it empty to begin.';
      $('initialize-pouch').hidden=!canWrite;
    }
  }
  $('initialize-pouch').onclick=async()=>{
    $('initialize-pouch').disabled=true;
    try {await api('/api/pouch/initialize',{});await openPouch();await listEntries();}
    catch(e){showError(e.message);}
    finally{$('initialize-pouch').disabled=false;}
  };
  workUI.setup({changed,dirty,refresh:async()=>{await openEntry(entry.entry_id);await listEntries();}});
  try {
    const path=location.pathname.slice(prefix.length);
    document.querySelectorAll('nav a').forEach(a=>{if(path.startsWith(new URL(a.href).pathname.slice(prefix.length)))a.setAttribute('aria-current','page');});
    if(path==='/sessions'){$('entries-view').hidden=true;$('directory-view').hidden=false;await sessions();}
    else if(path==='/dialog'){$('entries-view').hidden=true;$('dialog-view').hidden=false;await dialog();}
    else {await listEntries();if(path==='/pouch')await openPouch();else {const id=path.match(/^\/(?:entries|inflight)\/([0-9a-f-]{36})$/)?.[1];if(id)await openEntry(id,{revision:new URLSearchParams(location.search).get('revision')});else if(isWork&&new URLSearchParams(location.search).has('source_entry')){const q=new URLSearchParams(location.search);$('new-entry').click();$('work-source').value=q.get('source_entry');$('work-source-revision').value=q.get('source_revision')||'';}}}
  }catch(e){showError(e.message);}
})();
