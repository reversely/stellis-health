/** App-owned Dash component inside the existing navigation and capture shell. */
(() => {
 let frame=null,host=null,measures=[],demo=null,themeWatch=null,lastPayload='',ready=false,pendingCategory=null,notebookId=null,refreshRequest=0,loaded=false;
 const theme=()=>document.documentElement.dataset.theme==='light'?'light':'dark';
 const cameraKey=()=>`stellis-camera-test:${demo?.generation||'ordinary'}`;
 const cameraResults=()=>{try{return JSON.parse(sessionStorage.getItem(cameraKey())||'[]')}catch{return []}};
 function paintCamera(){
  const node=host?.querySelector('.dash-camera-results');if(!node)return;
  node.replaceChildren();if(!demo?.active)return;
  const points=cameraResults();if(!points.length)return;
  const title=document.createElement('h3');title.textContent='Camera readings';node.append(title);
  for(const p of points.slice(-3)){const row=document.createElement('p');const m=measures.find(m=>m.key===p.measure);row.textContent=`${m?.label||p.measure}: ${p.value} ${m?.unit||''} — Camera estimate`;node.append(row);}
  const clear=window.StellisDom.button('Clear camera readings','coach-button');clear.onclick=()=>{sessionStorage.removeItem(cameraKey());paintCamera()};node.append(clear);
 }
 function paintSuggestions(suggestions){
  const target=host?.querySelector('.measurement-suggestions');if(!target)return;
  target.replaceChildren();target.hidden=!suggestions.length;if(!suggestions.length)return;
  const dom=window.StellisDom;target.append(dom.el('h2','Suggested from your reports'),dom.el('p','Approved results join this notebook’s measurements'));
  for(const metric of suggestions){
   const item=dom.el('article',null,'measurement-suggestion');item.append(dom.el('h3',metric.label));
   for(const candidate of metric.candidates){
    const row=dom.el('div',null,'measurement-suggestion-row');row.append(dom.el('span',`${candidate.value} ${candidate.unit}, ${candidate.date}`));
    const review=dom.el('a','Review result','coach-button is-quiet');review.href=candidate.target+(candidate.target.includes('?')?'&':'?')+'fact='+encodeURIComponent(candidate.id);row.append(review);item.append(row);
   }
   target.append(item);
  }
 }
 function publish(category){
  if(!frame?.contentWindow||!loaded)return;
  if(category!=null)pendingCategory=category;
  category=category??(!ready?pendingCategory:null);
  const tokens=getComputedStyle(document.documentElement);
  const palette=Object.fromEntries(['ink','muted','line','series'].map(role=>[role,tokens.getPropertyValue('--widget-'+role).trim()]));
  const payload={stellisMeasurements:true,measures,palette,theme:theme(),demo:Boolean(demo?.active),today:window.StellisDemoDate||window.StellisCalendarDates.formatDate(new Date()),category};
  const key=JSON.stringify(payload);if(key===lastPayload&&category==null)return;lastPayload=key;
  frame.contentWindow.postMessage(payload,location.origin);
 }
 async function refresh(){
  const request=++refreshRequest,scope=notebookId;
  const data=await window.StellisDom.api('/readings'+(scope?'?notebook_id='+encodeURIComponent(scope):''));
  const state=await window.StellisDom.api('/demo/status');
  if(request!==refreshRequest||scope!==notebookId)return;
  measures=data.measures.map(window.StellisTrends.dailyTotals);demo=state;loaded=true;
  publish(pendingCategory);if(ready)pendingCategory=null;paintCamera();paintSuggestions(data.suggestions||[]);
 }
 async function manual(key){
  const dom=window.StellisDom,dialog=dom.el('dialog',null,'capture-dialog'),title=dom.el('h2','Log a reading');
  title.id='dash-entry-title';dialog.setAttribute('aria-labelledby',title.id);
  const close=dom.button('Close','coach-button is-quiet');close.onclick=()=>dialog.close();
  const body=dom.el('div');dialog.append(title,close,body);document.body.append(dialog);
  dialog.addEventListener('close',()=>{dialog.remove();frame?.focus()},{once:true});
  await window.StellisTrends.entryForm(body,notebookId,async()=>{dialog.close();await refresh()},key);
  dialog.showModal();body.querySelector('input')?.focus();
 }
 async function camera(){
  if(!demo)await refresh();
  if(!demo?.active){window.StellisCameraPulse.open(refresh,measures.find(m=>m.key==='pulse'),{saveReading:json=>window.StellisDom.api('/readings',{method:'POST',json:{...json,notebook_id:notebookId}})});return;}
  const pending=[];
  window.StellisCameraPulse.open(async()=>{sessionStorage.setItem(cameraKey(),JSON.stringify([...cameraResults(),...pending]));paintCamera()},null,{showComparison:false,saveReading:async point=>{pending.push(point);return point}});
 }
 window.addEventListener('message',async event=>{
  if(event.origin!==location.origin||event.source!==frame?.contentWindow||!event.data?.stellisMeasurements)return;
  const {action,height,path,measure}=event.data;
  try{
   if(action==='ready'){if(!ready){ready=true;lastPayload='';publish(pendingCategory);if(loaded)pendingCategory=null}}
   else if(action==='resize'&&Number.isFinite(height))frame.style.height=`${Math.max(480,Math.min(height+8,30000))}px`;
   else if(action==='manual')await manual(measure);
   else if(action==='camera')await camera();
   else if(action==='delete'){if(measures.some(m=>(m.entries||m.points).some(p=>p.id===event.data.id&&p.source!=='record'))){await window.StellisDom.api('/readings/'+encodeURIComponent(event.data.id),{method:'DELETE'});await refresh()}}
   else if(action==='navigate'&&/^\/(document|page|notebook|conversation)\/[\w%.-]+(?:[?#].*)?$/.test(path)){history.pushState({},'',path);window.dispatchEvent(new PopStateEvent('popstate'))}
  }catch(error){host.querySelector('[role=status]').textContent=error.message}
 });
 window.addEventListener('stellis-demo-date',()=>publish());
 for(const event of ['health-facts-changed','health-capture-saved'])window.addEventListener(event,()=>{if(host?.checkVisibility())refresh().catch(()=>{});});
 window.addEventListener('focus',()=>{if(host?.checkVisibility())refresh().catch(()=>{});});
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&host?.checkVisibility())refresh().catch(()=>{});});
 window.StellisDashTrends={async mount(container,options={}){
  if(host!==container){frame?.remove();frame=null;}
  host=container;notebookId=options.notebookId||null;loaded=false;
  if(!frame||!frame.isConnected){
   ready=false;lastPayload='';frame=document.createElement('iframe');frame.title='Measurements';frame.src='/components/measurements/';frame.className='dash-measurements-frame';
   const status=document.createElement('p');status.setAttribute('role','status');
   const results=document.createElement('section');results.className='dash-camera-results';results.setAttribute('aria-label','Camera readings');
   const actions=window.StellisDom.el('div',null,'measurement-actions');
   // One primary camera action and the quieter manual entry (style guide, Camera readings); both outlined controls, never bare text.
   const capture=window.StellisDom.button(null,'invert-button measurement-camera');capture.append(window.StellisDom.icon('camera'),window.StellisDom.el('span','Log vital signs'));capture.onclick=camera;
   const add=window.StellisDom.button(null,'coach-button measurement-manual');add.append(window.StellisDom.icon('pencil'),window.StellisDom.el('span','Log manually'));add.onclick=()=>manual();actions.append(capture,add);
   const suggestions=window.StellisDom.el('section',null,'measurement-suggestions');suggestions.setAttribute('aria-label','Suggested measurements');
   host.replaceChildren(actions,status,suggestions,frame,results);
  }
  if(!themeWatch){themeWatch=new MutationObserver(()=>publish());themeWatch.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']})}
  const query=new URLSearchParams(location.search),key=query.get('measure');
  pendingCategory=key||'all';
  await refresh();
  // Home's "Log <measure>" links arrive as ?log=<key> and open that entry straight away.
  const log=query.get('log');
  if(log&&measures.some(m=>m.key===log)){query.delete('log');history.replaceState(history.state,'',location.pathname+(query.size?'?'+query:''));await manual(log);}
 },refresh,openCamera:async(options={})=>{notebookId=options.notebookId||null;demo=await window.StellisDom.api('/demo/status');await camera();}};
})();
