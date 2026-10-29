(() => {
  const root=document.querySelector('#daily-brief');if(!root)return;
  const refresh=document.querySelector('#daily-brief-refresh'),stop=document.querySelector('#daily-brief-stop');
  const status=document.querySelector('#daily-brief-status'),content=document.querySelector('#daily-brief-content');
  const coverage=document.querySelector('#daily-brief-coverage'),thread=document.querySelector('#daily-brief-thread');
  let signature='',pending=false,actionError='';
  async function api(method='GET',suffix=''){
    const response=await fetch('/api/v1/daily-brief'+suffix,{method});const data=await response.json();
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Could not load the daily brief. Try again.');return data;
  }
  function render(data){
    const running=data.state==='running';refresh.disabled=running||pending;stop.hidden=!running;
    refresh.textContent=data.brief?'Refresh':'Prepare brief';
    status.textContent=actionError||(running?'Preparing your brief…':data.error||(!data.brief?'Stellis can review your check-ins, notebooks and upcoming appointments.':'Updated '+new Date(data.brief.created_at).toLocaleString()));
    const next=JSON.stringify(data.brief);
    if(next!==signature){signature=next;content.replaceChildren();if(data.brief)content.append(window.StellisAgentChat.messageBody(data.brief.content));}
    thread.hidden=!data.brief;if(data.brief)thread.href='/chat/'+data.brief.agent_id+'#message-'+data.brief.id;
    const snapshot=data.coverage;
    coverage.textContent=snapshot?['As of '+new Date(snapshot.as_of).toLocaleString(),snapshot.tasks+' open task'+(snapshot.tasks===1?'':'s'),snapshot.notebooks+' notebook'+(snapshot.notebooks===1?'':'s'),snapshot.calendar_connected&&!snapshot.warnings.length?snapshot.events+' events in the next 24 hours':'Upcoming events unavailable',
      ...(!snapshot.calendar_connected?['Calendar not connected']:[]),...snapshot.warnings,
      ...([snapshot.tasks,snapshot.notebooks,snapshot.events].some(n=>n>snapshot.limit_per_group)?['Based on up to '+snapshot.limit_per_group+' items per group']:[])].join('. '):'';
  }
  async function load(){if(location.pathname!=='/'||document.hidden||pending)return;try{render(await api());}catch(error){status.textContent=error.message;}}
  refresh.onclick=async()=>{actionError='';pending=true;refresh.disabled=true;status.textContent='Preparing your brief…';try{render(await api('POST'));}catch(error){actionError=error.message;status.textContent=actionError;refresh.disabled=false;}finally{pending=false;await load();}};
  stop.onclick=async()=>{stop.disabled=true;try{await api('POST','/stop');await load();}catch(error){status.textContent=error.message;}finally{stop.disabled=false;}};
  load();setInterval(load,3000);
})();
