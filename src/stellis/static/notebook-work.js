(() => {
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;};
  const labels={needs_you:'Needs you',working:'Working',done:'Done'};
  let dispose=()=>{};
  async function api(path,method='GET',body){const response=await fetch('/api/v1'+path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Could not update notebook');return data;}
  const link=(text,path)=>{const node=el('a',text);node.href=path;return node;};
  async function mount(host,book,onChange){
    dispose();host.replaceChildren();let stopped=false,timer,signature='',policyRevision=0;
    dispose=()=>{stopped=true;clearTimeout(timer);};
    const layout=el('div',null,'notebook-work'),center=el('div',null,'notebook-work-main'),aside=el('aside',null,'notebook-work-context');aside.setAttribute('aria-label','Notebook instructions');
    const feed=el('div',null,'notebook-activity-feed'),status=el('p',null,'notebook-work-status');status.role='status';
    const filters=el('div',null,'notebook-work-filters');filters.setAttribute('role','group');filters.setAttribute('aria-label','Activity status');let filter='all',current;
    for(const [id,name] of [['all','All'],...Object.entries(labels)]){const button=el('button',name);button.type='button';button.dataset.filter=id;button.setAttribute('aria-pressed',String(id===filter));button.onclick=()=>{filter=id;renderFeed();};filters.append(button);}
    const review=el('button','Review sources');review.type='button';review.title='Review up to 12 saved sources for useful comparisons';
    review.onclick=async()=>{review.disabled=true;status.textContent='Reviewing up to 12 saved sources…';try{await api('/notebooks/'+book.id+'/review-sources','POST');await refresh();status.textContent='Source review ready. Long sources use an excerpt.';}catch(error){status.textContent=error.message;}finally{review.disabled=false;}};
    filters.append(review);
    aside.append(el('h3','Stellis can use'),el('p','Everything in this notebook'),el('h3','Notebook instructions'));
    const policy=el('form',null,'notebook-policy'),policyInput=el('textarea'),save=el('button','Save instructions'),policyStatus=el('p');policyInput.setAttribute('aria-label','Notebook instructions');policyInput.maxLength=4000;policyStatus.role='status';save.type='submit';policy.append(policyInput,save,policyStatus);aside.append(policy);
    policy.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{const result=await api('/notebooks/'+book.id+'/instructions','PUT',{instructions:policyInput.value,revision:policyRevision});policyRevision=result.revision;policyStatus.textContent='Instructions saved';}catch(error){policyStatus.textContent=error.message;}finally{save.disabled=false;}};
    center.append(filters,feed,status);layout.append(center,aside);host.append(layout);
    function action(card,title,run){const button=el('button',title);button.type='button';button.onclick=async()=>{button.disabled=true;try{await run();await refresh();await onChange?.();}catch(error){status.textContent=error.message;}finally{button.disabled=false;}};card.append(button);}
    function renderFeed(){if(!current)return;for(const button of filters.querySelectorAll('[data-filter]'))button.setAttribute('aria-pressed',String(button.dataset.filter===filter));const items=current.items.filter(item=>filter==='all'||item.state===filter);feed.replaceChildren();
      // No activity yet: no status filters and no placeholder line, only Review sources (owner, 2026-10-28).
      for(const button of filters.querySelectorAll('[data-filter]'))button.hidden=!current.items.length;
      if(!items.length)return;
      for(const item of items){const card=el('article',null,'notebook-activity');card.dataset.activityId=item.id;const head=el('div',null,'notebook-activity-head');head.append(window.StellisAgentAvatar.avatarElement(document,{seed:item.agent_id||item.author,size:28}),el('strong',item.author),el('span',item.detail==='Stopped or interrupted'?'Stopped':item.detail==='Request failed'?'Failed':labels[item.state]),el('time',new Date(item.created_at).toLocaleString()));card.append(head);
        if(item.artifact){card.append(window.StellisAgentChat.artifactCard(item.artifact,()=>refresh(),false));}
        else if(item.kind!=='suggestion'||item.suggestion.operation!=='add_todo'){card.append(item.kind==='job'?window.StellisAgentChat.messageBody(item.text):el('p',item.text));if(item.detail)card.append(el('p',item.detail,'notebook-work-detail'));}
        const actions=el('div',null,'notebook-work-actions');
        if(item.kind==='suggestion'){
          if(item.suggestion.operation==='add_todo')card.append(window.StellisAgentChat.suggestionCard(item.suggestion,async()=>{await refresh();await onChange?.();}));
          else {
            for(const evidence of item.suggestion.evidence||[])card.append(el('blockquote',evidence.quote));
            action(actions,'Update task',()=>api('/suggestions/accept','POST',{ids:[item.id]}));
            action(actions,'Dismiss',()=>api('/suggestions/reject','POST',{ids:[item.id]}));
          }
        }
        if(item.kind==='comparison'){
          const comparison=item.comparison;
          for(const source of comparison.candidate.sources){const quote=el('blockquote',source.quote,'comparison-evidence');quote.append(link(source.title,source.target));card.append(quote);}
          if(['pending','retry'].includes(comparison.state)){
            action(actions,comparison.state==='retry'?'Try comparison again':'Compare',()=>api('/notebooks/'+book.id+'/comparisons/'+item.id+'/run','POST'));
            action(actions,'Dismiss',()=>api('/notebooks/'+book.id+'/comparisons/'+item.id+'/dismiss','POST'));
          }
          if(comparison.state==='running')action(actions,'Stop',()=>api('/agents/'+item.agent_id+'/jobs/'+comparison.job_id+'/stop','POST'));
        }
        if(item.kind==='filing'){
          const details=el('details'),summary=el('summary','Filed sources'),list=el('ul');details.append(summary,list);
          for(const source of item.filing.items){const row=el('li');row.append(source.available?link(source.title,source.target||'/page/'+source.page_id):el('span',source.title+' (deleted)'));list.append(row);}
          card.append(details);
          if(!item.filing.undone_at)action(actions,'Undo',()=>api('/notebooks/'+book.id+'/filings/'+item.id+'/undo','POST'));
          else actions.append(el('span','Filing undone'));
        }
        if(item.kind==='job'&&item.state==='working')action(actions,'Stop',()=>api('/agents/'+item.agent_id+'/jobs/'+item.id+'/stop','POST'));
        if(item.kind!=='filing')actions.append(link(item.kind==='suggestion'?(item.suggestion.source_target?'Open task note':'Open source'):'Open thread',item.target));card.append(actions);feed.append(card);
      }
    }
    async function refresh(){if(stopped)return;try{const data=await api('/notebooks/'+book.id+'/activity');if(stopped)return;current=data;const next=JSON.stringify(data.items);if(next!==signature){signature=next;renderFeed();}}catch(error){if(!stopped)status.textContent=error.message;}}
    try{const data=await api('/notebooks/'+book.id+'/activity');if(stopped)return;current=data;policyInput.value=data.instructions;policyRevision=data.revision;
      signature=JSON.stringify(data.items);renderFeed();
    }catch(error){status.textContent=error.message;}
    async function poll(){if(stopped||location.pathname!='/notebook/'+book.id)return;await refresh();timer=setTimeout(poll,3000);}timer=setTimeout(poll,3000);
  }
  async function home(){try{const books=await api('/notebooks/work');for(const book of books){const card=document.querySelector(`#notebook-groups a[href="/notebook/${book.id}"]`)?.closest('.notebook-card');if(!card)continue;card.querySelector('.notebook-work-preview')?.remove();const preview=el('div',null,'notebook-work-preview');const counts=[];if(book.counts.needs_you)counts.push(book.counts.needs_you+' need review');if(book.counts.working)counts.push(book.counts.working+' working');if(counts.length)preview.append(el('strong',counts.join(', ')));if(book.latest)preview.append(el('p',book.latest.text));if(preview.childElementCount)card.append(preview);}}catch{/* Home remains usable if activity is unavailable. */}}
  setInterval(()=>{if(location.pathname==='/'&&!document.hidden)home();},5000);
  window.StellisNotebookWork={mount,home,stop:()=>dispose()};
})();
