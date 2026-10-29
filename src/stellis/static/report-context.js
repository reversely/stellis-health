/** Original-report selection and source-anchored explanations. */
(function(root) {
'use strict';
/** Render the same saved, cited answer in a report question or native call. */
function answer(item, onSource=null) {
 const dom=root.StellisDom, node=dom.el('div',null,'report-answer');
 function inline(host,text){
  for(const token of text.split(/(\[\[\d+\]\]|\*\*[^*]+\*\*)/)){
   const match=/^\[\[(\d+)\]\]$/.exec(token);
   const citation=match&&item.citations.find(c=>c.n===Number(match[1]));
   if(citation){
    const link=dom.el('a',String(citation.n),'report-citation');link.href=citation.target;if(new URL(link.href).origin!==location.origin){link.target='_blank';link.rel='noopener noreferrer';}
    link.title=citation.title+'\n'+citation.excerpt;link.setAttribute('aria-label',`Source ${citation.n}: ${citation.title}`);
    if(onSource)link.addEventListener('click',event=>onSource(event,citation));host.append(link);
   }else if(/^\*\*[^*]+\*\*$/.test(token))host.append(dom.el('strong',token.slice(2,-2)));
   else host.append(document.createTextNode(token));
  }
 }
 for(const block of item.answer.split(/\n\s*\n/)){
  if(!block.trim())continue;
  const lines=block.split('\n');
  if(lines.every(line=>/^\s*[-*]\s+/.test(line))){
   const list=dom.el('ul');for(const line of lines){const row=dom.el('li');inline(row,line.replace(/^\s*[-*]\s+/,''));list.append(row);}node.append(list);
  }else{const paragraph=dom.el('p');inline(paragraph,block.replace(/^#{1,6}\s+/gm,''));node.append(paragraph);}
 }
 const sources=dom.el('nav',null,'chat-source-links');sources.setAttribute('aria-label','Answer sources');
 for(const citation of item.citations){
  const link=dom.el('a',`${citation.n}. ${citation.title}${citation.page?', page '+citation.page:''}`,'chat-source-link');link.href=citation.target;if(new URL(link.href).origin!==location.origin){link.target='_blank';link.rel='noopener noreferrer';}
  if(onSource)link.addEventListener('click',event=>onSource(event,citation));sources.append(link);
 }
 if(sources.children.length)node.append(sources);
 return node;
}
const SUMMARY = 'What does this report say?';
const PAGE_QUESTIONS = [SUMMARY, 'Which results are outside the usual range?', 'What should I ask at my next appointment?'];

function mount(host, record, initialPage, factsHost = null) {
  const dom = root.StellisDom;
  const base = `/documents/${encodeURIComponent(record.id)}`;
  const workspace = dom.el('section', null, 'report-workspace');
  workspace.setAttribute('aria-label', 'Understand this report');
  const viewer = dom.el('div', null, 'report-viewer');
  const tools = dom.el('div', null, 'document-actions');
  const pageSelect = dom.el('select'); pageSelect.setAttribute('aria-label', 'Report page');
  for (const p of record.pages) pageSelect.append(new Option(`Page ${p.number}`, p.number));
  pageSelect.value = String(initialPage || 1);
  const zoomOut = dom.button('−'); zoomOut.setAttribute('aria-label', 'Zoom out');
  const zoomIn = dom.button('+'); zoomIn.setAttribute('aria-label', 'Zoom in');
  const rotate = dom.button('Rotate');
  const retry = dom.button('Retry page'); retry.hidden = true;
  const multiple=dom.button('Select multiple','coach-button is-quiet');multiple.setAttribute('aria-pressed','false');let multiPick=false;multiple.onclick=()=>{multiPick=!multiPick;multiple.setAttribute('aria-pressed',String(multiPick));};
  // The page itself leads: a page picker only when there are pages to pick, the view's own controls
  // behind ⋯ (owner, 2026-10-29: no rows of controls before the report).
  pageSelect.hidden = record.pages.length < 2;
  const view = dom.el('details', null, 'more-menu report-view-more');
  const viewButton = dom.el('summary'); viewButton.setAttribute('aria-label', 'View options'); viewButton.title = 'View options'; viewButton.append(dom.icon('dots'));
  const viewItems = dom.el('div', null, 'more-menu-items');
  zoomOut.textContent = 'Zoom out'; zoomIn.textContent = 'Zoom in';
  viewItems.append(zoomIn, zoomOut, rotate, multiple); view.append(viewButton, viewItems);
  tools.append(pageSelect, view, retry);
  const status = dom.el('p', '', 'report-status'); status.setAttribute('role', 'status');
  const scroll = dom.el('div', null, 'report-scroll');
  const stage = dom.el('div', null, 'report-stage');
  const image = dom.el('img'); image.alt = record.title;
  const overlay = dom.el('div', null, 'report-regions');
  const paper = dom.el('div', null, 'report-paper');
  paper.append(image, overlay); stage.append(paper); scroll.append(stage); viewer.append(tools, status, scroll);
  const aside = dom.el('aside', null, 'report-explanation');
  aside.setAttribute('aria-label', 'Report explanation');
  const conversation=dom.el('section',null,'report-conversation widget-paper');
  conversation.setAttribute('aria-label','Ask Stellis about this report');
  const conversationHead=dom.el('div',null,'report-conversation-head');
  const identity=dom.el('div',null,'report-conversation-identity');
  identity.append(root.StellisAgentAvatar.avatarElement(document,{seed:'stellis',coordinator:true,size:40}),dom.el('h3','Ask Stellis'));
  // The call is a phone symbol beside send, inside the composer, never an orphaned button below it (#258).
  const callButton=dom.button(null,'report-call-icon');callButton.append(dom.icon('phone'));callButton.setAttribute('aria-label','Call about report');callButton.title='Call about report';callButton.disabled=true;
  conversationHead.append(identity);
  const context=dom.el('p','Select a line on the report to discuss it','report-status');
  const callHost=dom.el('div',null,'report-call-host');
  let reportCall=null;
  // Until a line is chosen, the panel shows only the prompt; the quote and its actions follow a selection.
  const selected = dom.el('blockquote'); selected.hidden = true;
  const messages = dom.el('div', null, 'report-messages');
  messages.setAttribute('aria-live', 'polite');messages.setAttribute('role','log');messages.setAttribute('aria-label','Report conversation');messages.tabIndex=0;
  const form = dom.el('form', null, 'report-question');
  const label = dom.el('label', 'Message Stellis');
  const input = dom.el('textarea'); input.id = 'report-question-'+record.id;
  label.htmlFor = input.id; input.rows = 2; input.maxLength = 2000;
  input.placeholder = 'Message Stellis';
  const send = window.StellisChatUI.sendButton(dom.button('Send', 'coach-button is-primary')); send.type = 'submit'; send.disabled = true;
  const explainStatus = dom.el('p', '', 'report-status'); explainStatus.setAttribute('role', 'status');
  label.classList.add('visually-hidden');
  const box=dom.el('div',null,'report-input');box.append(input,callButton,send);window.StellisChatUI.composer(box,input);
  form.append(label,box,explainStatus);
  const newMessages=dom.button('New messages','chat-new-messages');newMessages.hidden=true;
  let following=true;
  const follow=()=>{const exchanges=messages.querySelectorAll('.report-exchange');if(root.StellisChatUI?.showNewest)root.StellisChatUI.showNewest(messages,exchanges[exchanges.length-1]);else messages.scrollTop=messages.scrollHeight;following=true;newMessages.hidden=true;};
  newMessages.onclick=follow;
  messages.addEventListener('scroll',()=>{following=messages.scrollHeight-messages.scrollTop-messages.clientHeight<64;if(following)newMessages.hidden=true;});
  const selection = dom.el('section', null, 'report-selection widget-paper');
  const selectionFacts = dom.el('div', null, 'report-selection-facts');
  const correct = dom.button('Correct transcription', 'coach-button is-quiet');
  correct.addEventListener('click',()=>{
    const details=document.querySelector('.document-extracted-details');if(details)details.open=true;
    const page=document.querySelector(`#document-page-${pageSelect.value}`);
    const edit=[...page?.querySelectorAll('button')||[]].find(b=>b.textContent==='Edit text');
    edit?.click();page?.scrollIntoView({block:'center'});
  });
  correct.hidden=!record.pages.some(p=>p.text_source==='ocr'||p.text_source==='manual');
  const ask = dom.el('a', 'Continue in chat', 'coach-button is-quiet');
  ask.href = '/chat';
  ask.addEventListener('click', async event => {
    if (event.metaKey || event.ctrlKey || !root.StellisNavigate) return;
    event.preventDefault();
    if(!map||!region)return;
    const source=await dom.api(base+'/handoff',{method:'POST',json:{map_id:map.id,region_id:region.id,purpose:'chat'}});
    await root.StellisNavigate('/chat?source_context='+encodeURIComponent(source.id));
    root.StellisChatDocument?.open({documentId:record.id,page:source.page,region:source.region_id});
  });
  selection.append(dom.el('h3', 'Selected source'), selectionFacts, correct);
  const sourceContext=dom.el('div',null,'chat-source-context');const routines=dom.button('Discuss care routines','coach-button is-quiet');routines.disabled=true;routines.onclick=async()=>{try{if(!map||!region)return;const handoff=await dom.api(base+'/handoff',{method:'POST',json:{map_id:map.id,region_id:region.id,purpose:'care'}});beginNativeCall({kind:'call',handoff,referenceDay:record.record_date||null});}catch(e){explainStatus.textContent=e.message;}};
  const reviewCare=dom.button('Review care changes','coach-button is-quiet');reviewCare.onclick=()=>{if(innerWidth<=760||chat.mode==='expanded')chat.setMode('minimized');/* On a phone the chat covers the page; the review needs it out of the way (#258). */const panel=viewer.querySelector('.routine-review');panel?.scrollIntoView({block:'start'});panel?.setAttribute('tabindex','-1');panel?.focus({preventScroll:true});};
  ask.hidden=routines.hidden=reviewCare.hidden=true;
  viewer.addEventListener('routine-review-state',event=>{reviewCare.hidden=!event.detail.reviewable;careReviewable=Boolean(event.detail.reviewable);paintFound();});
  // Continue in chat, Discuss care routines and Review care changes sit behind ⋯ in the header (#258).
  const more=dom.el('details',null,'more-menu report-more');const moreButton=dom.el('summary');moreButton.setAttribute('aria-label','More');moreButton.title='More';moreButton.append(dom.icon('dots'));
  const contextActions=dom.el('div',null,'chat-source-actions more-menu-items');contextActions.append(ask,routines,reviewCare);more.append(moreButton,contextActions);
  const syncMore=()=>{more.hidden=[ask,routines,reviewCare].every(n=>n.hidden);};new MutationObserver(syncMore).observe(contextActions,{subtree:true,attributes:true,attributeFilter:['hidden']});syncMore();
  for(const item of [ask,routines,reviewCare])item.addEventListener('click',()=>{more.open=false;});
  conversationHead.append(more);
  context.hidden=true;
  // × beside the chosen line returns the conversation to the whole report.
  const clear=dom.button('×','report-clear');clear.setAttribute('aria-label','Ask about the whole report');clear.title='Ask about the whole report';clear.onclick=()=>selectPage();
  new MutationObserver(()=>{clear.hidden=selected.hidden;}).observe(selected,{attributes:true,attributeFilter:['hidden']});clear.hidden=true;
  const quote=dom.el('div',null,'report-quote');quote.append(selected,clear);
  sourceContext.append(context,quote);
  const footer=dom.el('div',null,'report-chat-footer');footer.append(form);
  // What Stellis found, beside its summary: each suggestion is one press and none blocks reading or
  // asking (owner, 2026-10-29). Results reach Measurements only by this approval (#191).
  const found=dom.el('section',null,'report-found');found.setAttribute('aria-label','Found on this report');
  let careReviewable=false;
  const day=value=>new Date(`${value}T12:00:00`).toLocaleDateString([],{day:'numeric',month:'long',year:'numeric'});
  function foundRow(text,detail,action){
    const row=dom.el('div',null,'report-found-row');const words=dom.el('div');words.append(dom.el('p',text,'report-found-text'));
    if(detail)words.append(dom.el('p',detail,'report-found-detail'));row.append(words);if(action)row.append(action);return row;
  }
  function paintFound(){
    const rows=[];
    const suggestion=record.date_suggestion||{};
    if(!record.record_date){
      if(suggestion.date){
        const quote=suggestion.candidates?.find(c=>c.date===suggestion.date);
        const confirm=dom.button('Confirm','coach-button is-quiet');
        confirm.onclick=async()=>{confirm.disabled=true;try{Object.assign(record,await dom.api(base,{method:'PATCH',json:{record_date:suggestion.date}}));window.dispatchEvent(new Event('health-capture-saved'));paintFound();}catch(e){explainStatus.textContent=e.message;confirm.disabled=false;}};
        rows.push(foundRow(`Dated ${day(suggestion.date)}`,quote?`Page ${quote.page}: ${quote.quote}`:null,confirm));
      }else{
        const pick=dom.el('form',null,'report-found-date');const input=dom.el('input');input.type='date';input.required=true;input.setAttribute('aria-label','Visit or test date');
        const save=dom.button('Save','coach-button is-quiet');save.type='submit';pick.append(input,save);
        pick.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;try{Object.assign(record,await dom.api(base,{method:'PATCH',json:{record_date:input.value}}));window.dispatchEvent(new Event('health-capture-saved'));paintFound();}catch(e){explainStatus.textContent=e.message;save.disabled=false;}});
        rows.push(foundRow('No date found','Add the visit or test date',pick));
      }
    }
    const waiting=facts.filter(f=>f.status==='suggested'),saved=facts.filter(f=>f.status==='accepted');
    if(waiting.length){
      const add=dom.button('Add to Measurements','coach-button is-quiet');
      add.onclick=async()=>{add.disabled=true;try{await dom.api(`${base}/facts/accept`,{method:'POST'});window.dispatchEvent(new Event('health-facts-changed'));window.dispatchEvent(new Event('health-capture-saved'));await refreshEvidence();}catch(e){explainStatus.textContent=e.message;add.disabled=false;}};
      rows.push(foundRow(`${waiting.length} result${waiting.length===1?'':'s'}`,waiting.map(f=>`${f.name} ${f.value} ${f.unit||''}`.trim()).join(', '),add));
    }else if(saved.length){
      const view=dom.el('a','View','coach-button is-quiet');view.href='/trends';
      rows.push(foundRow(`${saved.length} result${saved.length===1?'':'s'} in Measurements`,null,view));
    }
    if(careReviewable){const review=dom.button('Review','coach-button is-quiet');review.onclick=()=>reviewCare.click();rows.push(foundRow('Care changes','Medicines or treatment this report changes',review));}
    found.replaceChildren(...(rows.length?[dom.el('h4','Found on this report'),...rows]:[]));found.hidden=!rows.length;
  }
  conversation.append(conversationHead,found,sourceContext,callHost,messages,newMessages,footer);
  // A phone starts with the chat minimized, so what Stellis found sits on the page above the report there.
  const narrow=matchMedia('(max-width: 760px)');
  const placeFound=()=>{if(narrow.matches)workspace.prepend(found);else conversation.insertBefore(found,sourceContext);};
  aside.append(conversation);
  viewer.append(selection);if(factsHost)viewer.append(factsHost);
  workspace.append(viewer,aside);host.append(workspace);
  placeFound();narrow.addEventListener('change',placeFound);
  root.StellisRoutineReview?.mount(viewer,'document',record.id);
  const chat=root.StellisChatWindow.attach(conversation,{head:conversationHead,title:'Ask Stellis'});
  // No Ask Stellis in the page tools: the chat is open beside the report, or its own restore button shows (#258).
  conversation.addEventListener('chat-window-mode',()=>{if(following)requestAnimationFrame(follow);});
  let map = null, region = null, exchanges = [], serial = 0, scale = 1, rotation = 0, busy = false;
  let configured = false;
  const buttons = new Map();const selectedIds=new Set();
  let facts = [];
  const pending=new Map();
  const sourceKey=()=>map&&region?map.id+':'+region.id:null;
  const drafts=new Map();
  let lastSource=null;
  const normal = text => String(text).toLowerCase().replace(/\s+/g,' ').trim();
  const matches = (item,fact) => item.regions?item.regions.some(r=>matches(r,fact)):Number(pageSelect.value)===fact.page && normal(item.text).startsWith(normal(fact.name)) &&
    (normal(fact.quote).includes(normal(item.text)) || record.pages.find(p=>p.number===fact.page)?.text_source==='manual');
  function paintEvidence() {
    buttons.forEach((button,id)=>button.classList.toggle('has-fact',facts.some(f=>matches(map.regions.find(r=>r.id===id),f))));
    if(!region)return;
    selectionFacts.replaceChildren(...facts.filter(f=>matches(region,f)).map(f=>{
      const row=dom.el('div',null,'report-selected-result');
      row.append(dom.el('p',`${f.name}: ${f.value} ${f.unit} / ${f.date} / ${f.status==='accepted'?'Approved and saved to health history':'Awaiting your approval'}`,'report-status'));
      if(f.status==='accepted'&&f.measurement){const link=dom.el('a','View in Measurements','coach-button is-quiet');link.href='/trends?measure='+encodeURIComponent(f.measurement);row.append(link);}
      if(f.status==='suggested'){
        const actions=dom.el('div',null,'document-actions');
        for(const [label,path,method] of [['Approve and save',`/facts/${f.id}/accept`,'POST'],['Dismiss',`/facts/${f.id}`,'DELETE']]){
          const button=dom.button(label,'coach-button '+(method==='POST'?'is-primary':'is-quiet'));
          button.addEventListener('click',async()=>{button.disabled=true;try{await dom.api(path,{method});window.dispatchEvent(new Event('health-facts-changed'));window.dispatchEvent(new Event('health-capture-saved'));await refreshEvidence();}catch(error){explainStatus.textContent=error.message;}finally{button.disabled=false;}});actions.append(button);
        }row.append(actions);
      }return row;
    }));
    selection.hidden=!selectionFacts.children.length && correct.hidden;
  }
  async function refreshEvidence(){try{facts=(await dom.api(`${base}/facts`)).facts;if(map)paintEvidence();paintFound();}catch{}}
  host.addEventListener('report-focus-fact',async event=>{
    const fact=event.detail;pageSelect.value=String(fact.page);
    if(!map||map.page!==fact.page)await load();
    const found=map?.regions.find(r=>matches(r,fact));if(found)select(found,true);
  });
  window.addEventListener('health-facts-changed',()=>{if(host.isConnected)refreshEvidence();});
  function size() {
    stage.style.width = `${scale*100}%`;
    const ratio = image.naturalWidth / image.naturalHeight || 1;
    stage.style.aspectRatio = String(rotation % 180 ? 1/ratio : ratio);
    paper.style.width = `${rotation % 180 ? ratio*100 : 100}%`;
    paper.style.transformOrigin = '0 0';
    paper.style.transform = ({0: 'none', 90: 'rotate(90deg) translateY(-100%)',
      180: 'rotate(180deg) translate(-100%, -100%)',
      270: 'rotate(270deg) translateX(-100%)'})[rotation];
  }
  function select(item, focus=false, additive=false){
    const old=region?.id;
    if(lastSource)drafts.set(lastSource,input.value);
    if(additive){if(selectedIds.has(item.id))selectedIds.delete(item.id);else if(selectedIds.size<20)selectedIds.add(item.id);}
    else{selectedIds.clear();selectedIds.add(item.id);}
    const chosen=map.regions.filter(r=>selectedIds.has(r.id));
    region=chosen.length===1?chosen[0]:chosen.length?{id:chosen.map(r=>r.id).join(','),text:chosen.map(r=>r.text).join('\n'),regions:chosen}:null;
    lastSource=sourceKey();input.value=drafts.get(lastSource)||'';following=true;
    selected.textContent=region?.text||'';selected.hidden=ask.hidden=routines.hidden=selection.hidden=!region;
    buttons.forEach((button,id)=>{button.classList.toggle('is-selected',selectedIds.has(id));button.setAttribute('aria-pressed',String(selectedIds.has(id)));});
    if(reportCall&&old!==region?.id){reportCall.close();reportCall=null;explainStatus.textContent='Call ended. A different source is selected.';}
    context.textContent=`${record.title}, page ${pageSelect.value}${chosen.length>1?', '+chosen.length+' selected parts':''}`;
    callButton.disabled=routines.disabled=!configured||!region;send.disabled=busy||!configured||!region;
    renderHistory();paintEvidence();
    if(focus)buttons.get(item.id)?.scrollIntoView({block:'nearest',inline:'nearest'});
  }

  // The report opens on Stellis's summary of the whole page; questions typed then are about the whole
  // page until a line is chosen (owner, 2026-10-29: the upload shows the report and summarizes it).
  function selectPage(){
    if(!map)return;
    if(lastSource)drafts.set(lastSource,input.value);
    selectedIds.clear();region={id:'page',text:record.title};
    lastSource=sourceKey();input.value=drafts.get(lastSource)||'';following=true;
    selected.hidden=ask.hidden=routines.hidden=selection.hidden=true;
    buttons.forEach(button=>{button.classList.remove('is-selected');button.setAttribute('aria-pressed','false');});
    context.textContent=`${record.title}, page ${pageSelect.value}`;
    callButton.disabled=routines.disabled=!configured;send.disabled=busy||!configured;
    renderHistory();
  }
  function showAnswer(item) {
    const block = dom.el('article', null, 'report-exchange');
    // Stellis's own summary is not shown as a question the person asked.
    if (!(item.region_id === 'page' && item.question === SUMMARY)) block.append(dom.el('p', item.question, 'report-user-question'));
    block.append(dom.el('strong','Stellis','report-speaker'));
    if (item.stale) block.append(dom.el('p', 'Saved explanation, source text has changed.', 'document-empty'));
    const content = answer(item,(event,citation)=>{
      const url=new URL(citation.target,location.origin);
      if(!event.metaKey&&!event.ctrlKey&&url.pathname===`/document/${record.id}`&&url.searchParams.has('map')){
        event.preventDefault();pageSelect.value=url.searchParams.get('page')||'1';
        load(url.searchParams.get('map'),url.searchParams.get('region'));
      }
    });
    block.append(content);
    root.StellisReportFollowups?.mount(block,item);
    if(!item.follow_up&&!item.stale){const prepare=dom.button('Prepare for visit','coach-button is-quiet');const progress=dom.el('p',null,'report-status');prepare.onclick=async()=>{prepare.disabled=true;progress.textContent='Preparing a source-backed question…';try{const result=await dom.api('/report-exchanges/'+item.id+'/visit-question',{method:'POST'});if(result.follow_up){item.follow_up=result.follow_up;prepare.remove();progress.remove();root.StellisReportFollowups?.mount(block,item);}else progress.textContent='No additional unresolved question found in these records.';}catch(e){progress.textContent=e.message;prepare.disabled=false;}};block.append(prepare,progress);}
    const trace = root.StellisAnswerTrace?.link({traceId: item.trace_id});
    if (trace) block.append(trace);
    messages.append(block);
  }
  function questionIdeas(questions,heading){
    const ideas=dom.el('div',null,'report-ideas');ideas.append(dom.el('p',heading,'chat-empty'));
    for(const question of questions){const idea=dom.button(question,'report-idea');idea.onclick=()=>sendQuestion(question);ideas.append(idea);}
    return ideas;
  }
  function renderHistory() {
    const top=messages.scrollTop,auto=following;
    messages.replaceChildren();
    if(map&&region){
      exchanges.filter(e=>e.map_id===map.id&&e.region_id===region.id).forEach(showAnswer);
      const work=pending.get(sourceKey());
      if(work){
        const turn=dom.el('article',null,'report-exchange is-pending');
        if(!(region.id==='page'&&work.question===SUMMARY))turn.append(dom.el('p',work.question,'report-user-question'));
        turn.append(dom.el('strong','Stellis','report-speaker'));
        // While Stellis writes, three dots hold the answer's place; an error replaces them with the message.
        if(work.error){const status=dom.el('p',work.error,'report-pending-status');status.role='status';turn.append(status);}
        else turn.append(window.StellisChatUI.typing('Reading your report and related records'));
        if(work.error){const retry=dom.button('Retry reply','coach-button is-quiet');retry.onclick=()=>sendQuestion(work.question);turn.append(retry);}
        messages.append(turn);
      }
      // Under the report's own summary, the next questions to ask (#258).
      const shown=exchanges.filter(e=>e.map_id===map.id&&e.region_id===region.id);
      if(region.id==='page'&&!work&&shown.length&&shown.every(e=>e.question===SUMMARY))messages.append(questionIdeas(PAGE_QUESTIONS.slice(1),'Ask next'));
    }
    // An empty chat says what to do: pick a line, then ask; with a line chosen, questions to tap (#258).
    if(!messages.children.length){
     if(!region)messages.append(dom.el('p','Select a line on the report, such as a result, to ask about it.','chat-empty'));
     else{
      const whole=region.id==='page';
      messages.append(questionIdeas(whole?PAGE_QUESTIONS:['What does this result mean for me?','Is it in the usual range?','What should I ask my doctor about it?'],whole?'Ask about this report':'Ask about this line'));
     }
    }
    if(auto)requestAnimationFrame(follow);else{messages.scrollTop=top;newMessages.hidden=false;}
  }
  async function load(wantedMap = null, wantedRegion = null) {
    const current = ++serial;
    overlay.inert = true;
    if(lastSource)drafts.set(lastSource,input.value);lastSource=null;
    map = null; region = null; selectedIds.clear();buttons.clear(); overlay.replaceChildren(); messages.replaceChildren(window.StellisChatUI.typing('Opening the report'));
    if(reportCall){reportCall.close();reportCall=null;}
    selection.hidden=true;context.textContent='Select a line on the report to discuss it';callButton.disabled=true;
    selected.textContent = ''; selected.hidden = ask.hidden = routines.hidden = true; send.disabled = true;
    status.textContent = ''; retry.hidden = true; stage.classList.add('is-loading');
    const number = Number(pageSelect.value);
    image.removeAttribute('src');
    image.onerror = () => { if (current === serial) { stage.classList.remove('is-loading'); status.textContent = 'The original page could not be opened.'; retry.hidden = false; } };
    try {
      const result = await dom.api(`${base}/pages/${number}/map`);
      if (current !== serial || !workspace.isConnected) return;
      map = result;
      image.src = `/api/v1${base}/pages/${number}/original`;
      for (const item of map.regions) {
        const button = dom.button(item.text, 'report-region');
        button.title = item.text; button.setAttribute('aria-label', `Explain: ${item.text}`);
        button.setAttribute('aria-pressed', 'false');
        const [x,y,w,h] = item.box;
        Object.assign(button.style, {left: `${x*100}%`, top: `${y*100}%`, width: `${w*100}%`, height: `${h*100}%`});
        button.addEventListener('click', event => {
          select(item,false,multiPick||event.shiftKey);
          // Choosing a line is choosing to ask about it: a minimized chat (a phone's default) opens (#258).
          if(chat.mode==='minimized')chat.restore();
          // Selection is evidence review. Inference starts only with Explain.
        });
        buttons.set(item.id, button); overlay.append(button);
      }
      status.textContent = map.regions.length ? '' : 'No readable regions found on this page.';
      await refreshEvidence();
      if (wantedMap && wantedMap !== map.id) {
        status.textContent = 'The source text changed. Select a region for a new explanation.';
      } else if (wantedRegion !== null && wantedRegion !== 'page') {
        const wanted=String(wantedRegion).split(',');let first=true;for(const item of map.regions.filter(r=>wanted.includes(r.id))){select(item,false,!first);first=false;}
      }
      if (!region && map.regions.length) selectPage();
    } catch (error) {
      if (current === serial) { image.src = `/api/v1${base}/pages/${number}/original`; status.textContent = error.message; retry.hidden = false; }
    }
  }
  function beginNativeCall(options){
    reportCall?.close();const wasOpen=sourceContext.open;sourceContext.open=false;conversation.classList.add('has-active-call');
    const closed=options.onClose;reportCall=root.StellisCoachCall.open({...options,host:callHost,onClose:()=>{reportCall=null;conversation.classList.remove('has-active-call');sourceContext.open=wasOpen;closed?.();}});
  }
  function startReportCall(){
    if(!map||!region||!configured)return;
    beginNativeCall({kind:'report',
      source:{documentId:record.id,mapId:map.id,regionId:region.id},onRestart:startReportCall,
      onAction:message=>{if(message.action==='report_answer'){exchanges.push(message.exchange);renderHistory();}},
      onClose:()=>{reportCall=null;form.hidden=false;messages.hidden=false;ask.hidden=false;callButton.hidden=false;conversation.classList.remove('has-report-call');renderHistory();}
    });
    form.hidden=true;messages.hidden=true;ask.hidden=true;callButton.hidden=true;conversation.classList.add('has-report-call');
    const fitCall=()=>{
      if(!callHost.firstElementChild)return;
      callHost.style.setProperty('--report-call-height',Math.max(260,conversation.clientHeight-conversationHead.offsetHeight-sourceContext.offsetHeight-32)+'px');
    };
    requestAnimationFrame(fitCall);
  }
  callButton.addEventListener('click',startReportCall);
  async function sendQuestion(question){
    if(!map||!region||busy||!configured||!question.trim())return;
    const anchor={map_id:map.id,region_id:region.id},key=sourceKey();
    busy=true;send.disabled=true;callButton.disabled=true;following=true;
    // The page stays where it is (owner, 2026-10-29: a page never jumps to its middle); the answer scrolls
    // inside the chat.
    pending.set(key,{question});explainStatus.textContent='';renderHistory();
    try{
      const result=await dom.api(`${base}/explanations`,{method:'POST',json:{...anchor,question}});
      if(!workspace.isConnected)return;
      exchanges.push(result);pending.delete(key);renderHistory();
    }catch(error){pending.set(key,{question,error:error.message});if(workspace.isConnected)renderHistory();}
    finally{busy=false;send.disabled=!region||!configured;callButton.disabled=!region||!configured;}
  }
  form.addEventListener('submit',event=>{
    event.preventDefault();const question=input.value.trim();
    if(!question||busy||!region||!configured)return;
    input.value='';drafts.set(sourceKey(),'');input.style.height='';sendQuestion(question);
  });
  input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();form.requestSubmit();}});
  input.addEventListener('input',()=>{if(sourceKey())drafts.set(sourceKey(),input.value);input.style.height='auto';input.style.height=Math.min(120,input.scrollHeight)+'px';});
  image.addEventListener('load', () => { stage.classList.remove('is-loading'); size(); overlay.inert = false; });
  pageSelect.addEventListener('change', () => {scale = 1; rotation = 0; size(); load();});
  retry.addEventListener('click', () => load());
  zoomOut.addEventListener('click', () => {scale = Math.max(1, scale-0.25); size();});
  zoomIn.addEventListener('click', () => {scale = Math.min(3, scale+0.25); size();});
  rotate.addEventListener('click', () => {rotation = (rotation+90)%360; size();});
  async function init() {
    try {
      const [saved, capabilities] = await Promise.all([dom.api(`${base}/explanations`), dom.api('/capabilities')]);
      if (!workspace.isConnected) return;
      exchanges = saved.exchanges; configured = Boolean(capabilities.ai?.configured);
      if (!configured) explainStatus.textContent = 'Questions need AI set up in Settings';
      const params = new URLSearchParams(location.search);
      const requested = params.get('map');
      const latest = requested ? exchanges.filter(e => e.map_id === requested &&
          (!params.has('region') || e.region_id === params.get('region'))).at(-1) : exchanges.at(-1);
      const targetMap = params.get('map') || latest?.map_id;
      const targetRegion = params.get('region') ?? latest?.region_id ?? null;
      if (!initialPage && latest) pageSelect.value = String(latest.page);
      await load(targetMap, targetRegion);
      const fact=facts.find(f=>f.id===params.get('fact'));
      if(fact){pageSelect.value=String(fact.page);if(map.page!==fact.page)await load();const found=map.regions.find(r=>matches(r,fact));if(found)select(found,true);}
      if (configured && region?.id === 'page' && !exchanges.some(e => e.map_id === map.id && e.region_id === 'page')) sendQuestion(SUMMARY);
      // Historical answers remain readable when the original page text has changed.
      if (latest && !region) {
        selected.textContent = latest.selection.text;
        showAnswer(latest);
      }
    } catch (error) { status.textContent = error.message; retry.hidden = false; }
  }
  init();
  selection.hidden = true;renderHistory();
}
root.StellisReportContext = {mount,answer};
})(window);
