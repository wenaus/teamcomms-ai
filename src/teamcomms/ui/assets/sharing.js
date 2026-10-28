'use strict';
(async()=>{
  const config=JSON.parse(document.querySelector('meta[name=teamcomms-config]').content);
  const $=id=>document.getElementById(id);
  async function api(path,body){
    const options={credentials:'same-origin',cache:'no-store',redirect:'error',headers:{Accept:'application/json'}};
    if(body!==undefined){
      if(!config.csrf_url)throw new Error('Open this page through your signed-in team website.');
      const response=await fetch(config.csrf_url,{credentials:'same-origin',cache:'no-store',redirect:'error'});
      if(!response.ok)throw new Error('Sign in again before choosing sharing.');
      const csrf=await response.json();
      if(!/^X-[A-Za-z0-9-]+$/.test(csrf.header_name)||typeof csrf.token!=='string')throw new Error('Browser security setup failed.');
      options.headers[csrf.header_name]=csrf.token;
      options.headers['Content-Type']='application/json';options.method='POST';options.body=JSON.stringify(body);
    }
    const response=await fetch(config.prefix+path,options),value=await response.json();
    if(!response.ok||value.error)throw new Error(value.error||`HTTP ${response.status}`);
    return value;
  }
  function show(state){
    $('session').textContent=state.session_name;
    $('notice').textContent=state.notice;
    $('status').textContent=state.human_choice==='share'?'Your messages are shared from your latest choice onward.':
      state.human_choice==='private'?'Your typed messages are kept private. Visible AI replies are shared.':
      'No human choice has been made. Your typed messages are kept private. Visible AI replies are shared.';
    $('private').textContent=state.human_choice==='share'?'Stop sharing my messages':'Keep my messages private';
  }
  try{
    const identity=await api('/api/whoami');
    show(await api('/api/dialog/sharing?session_id='+encodeURIComponent(config.session_id)));
    if(identity.kind!=='human'){
      $('authority').textContent='Only this session’s signed-in human operator can choose. An AI or peer cannot answer for them.';
      return;
    }
    $('choices').hidden=false;
    let pending=null;
    async function choose(choice){
      // Retain the exact operation for a retry after an uncertain response.
      if(!pending||pending.choice!==choice)pending={session_id:config.session_id,operation_id:crypto.randomUUID(),choice};
      $('share').disabled=$('private').disabled=true;
      try{await api('/api/dialog/sharing/choice',pending);
        show(await api('/api/dialog/sharing?session_id='+encodeURIComponent(config.session_id)));pending=null;}
      catch(error){$('status').textContent=error.message;}
      finally{$('share').disabled=$('private').disabled=false;}
    }
    $('share').onclick=()=>choose('share');$('private').onclick=()=>choose('private');
    $('authority').textContent='This choice applies only to this session. It does not import earlier typed messages.';
  }catch(error){$('status').textContent=error.message;}
})();
