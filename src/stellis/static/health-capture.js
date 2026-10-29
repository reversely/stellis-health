/** Shared health capture: a record opens its own page; readings and appointments save here. */
(() => {
'use strict';
const ACTIONS = {'log':'Log Items','photo':'Take Photo','reading':'Enter Reading','upload':'Upload Record','record':'Record Visit','appointment':'Add Appointment','checkin':'Check-in'};
const UNITS = {weight:['kg','lb'],blood_pressure:['mmHg'],blood_glucose:['mg/dL','mmol/L'],temperature:['°C','°F']};
const el = (tag,text,cls) => window.StellisDom.el(tag,text,cls);
const api = (...args) => window.StellisDom.api(...args);
const btn = (text,run,cls='coach-button is-quiet') => {const b=window.StellisDom.button(text,cls);b.addEventListener('click',run);return b;};
const field = (host,label,node) => {const wrap=el('label',null,'capture-field');node.setAttribute('aria-label',label);wrap.append(el('span',label),node);host.append(wrap);return node;};
const input = (type,required=true) => {const n=el('input');n.type=type;n.required=required;return n;};
let active;
function context() {
  const match=location.pathname.match(/^\/notebook\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : document.querySelector('.agent-composer-context select')?.value || null;
}
function modal(title) {
  const opener=active?.captureOpener || document.activeElement;
  active?.close();
  const dialog=el('dialog',null,'dashboard-dialog health-capture'); active=dialog;
  dialog.captureOpener=opener;
  const head=el('div',null,'drawer-head');const heading=el('h2',title);heading.id='health-capture-title';
  dialog.setAttribute('aria-labelledby',heading.id);
  head.append(heading,btn('Close',()=>dialog.close()));
  const body=el('div',null,'capture-body');dialog.append(head,body);document.body.append(dialog);dialog.showModal();
  dialog.addEventListener('close',()=>{dialog.cleanup?.();dialog.remove();if(active===dialog){active=null;(opener?.isConnected && opener.getClientRects().length ? opener : document.querySelector('#global-add'))?.focus();}},{once:true});
  return {dialog,body};
}
function saved(body,text,target) {
  body.replaceChildren(el('p',text));
  const a=el('a','View saved entry','coach-button dashboard-link');a.href=target;body.append(a);
  window.reloadLibrary?.().catch(()=>{});
  window.dispatchEvent(new Event('health-capture-saved'));
}
async function open(action=null,options={}) {
  if(action && !Object.hasOwn(ACTIONS,action))return;
  const notebookId=Object.hasOwn(options,'notebookId') ? options.notebookId : context();
  // The demo can't record (the server refuses demo recordings), so recording opens the upload, where
  // the example visit recording is offered.
  if(action==='record'&&document.body.classList.contains('is-demo-guest'))action='upload';
  if(action==='record'){active?.close();window.startHealthRecording(notebookId);return;}
  if(action==='log'){active?.close();window.StellisTrackerLog.open({notebookId});return;}
  const {dialog,body}=modal(ACTIONS[action] || 'Add');
  if(!action) {
    const choices=el('div',null,'capture-choices');
    for(const [key,label] of Object.entries(ACTIONS))choices.append(btn(label,()=>open(key,{notebookId}),'coach-button'));
    body.append(choices);return;
  }
  const status=el('p',null,'capture-status');status.role='status';body.append(status);
  try {
    const books=(await api('/notebooks')).notebooks.filter(book=>!book.archived_at);
    if(!dialog.open)return;
    const notebook=el('select');notebook.append(new Option('General health history',''),...books.map(b=>new Option(b.name,b.id)));notebook.value=notebookId || '';
    // An upload files where it was opened; Stellis suggests a notebook on the report page instead of asking first.
    if(action!=='upload')field(body,'Notebook',notebook);
    const content=el('div',null,'capture-content');body.append(content);
    const done=(text,target)=>saved(body,text,target);
    if(action==='reading') {
      const paint=()=>{const entry=el('div');content.replaceChildren(entry);window.StellisTrends.entryForm(entry,notebook.value,()=>done('Reading saved','/trends')).catch(e=>status.textContent=e.message);};
      notebook.addEventListener('change',paint);paint();
    } else if(action==='checkin') {
      let widget;
      const paint=()=>{widget?.destroy();widget=window.mountTodos(content,{scope:notebook.value || 'none',onChange:()=>window.dispatchEvent(new Event('health-capture-saved'))});};
      notebook.addEventListener('change',paint);dialog.cleanup=()=>widget?.destroy();paint();
    } else if(action==='appointment') appointment(content,notebook,done);
    else upload(content,notebook,action==='photo',done);
  } catch(error){status.textContent=error.message;body.append(btn('Try again',()=>open(action,{notebookId})));}
}
function appointment(host,notebook,done) {
  const form=el('form',null,'capture-form');
  const title=field(form,'Title',input('text'));title.maxLength=180;
  const when=field(form,'When',input('datetime-local'));
  const status=el('select');status.append(new Option('Scheduled','scheduled'),new Option('Planned','planned'));field(form,'Status',status);
  const note=field(form,'Note',el('textarea'));note.maxLength=1000;
  submit(form,'Save appointment',async()=>{const item=await api('/care-events',{method:'POST',json:{title:title.value,starts_at:new Date(when.value).toISOString(),status:status.value,note:note.value,notebook_id:notebook.value || null}});done('Appointment saved',item.target);});host.append(form);
}
function submit(form,label,run) {
  const error=el('p',null,'capture-error');error.role='alert';
  const save=btn(label,()=>{},'coach-button is-primary');save.type='submit';form.append(error,save);
  form.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;error.textContent='';try{await run();}catch(e){error.textContent=e.message;save.disabled=false;}});
}
function upload(host,notebook,photo,done) {
  const kind=el('select');
  if(photo){kind.append(new Option('Medical document','document'),new Option('Scale','weight'),new Option('Blood pressure monitor','blood_pressure'),new Option('Glucose meter','blood_glucose'),new Option('Temperature display','temperature'));field(host,'Photo type',kind);}
  const file=input('file');file.accept=photo?'image/jpeg,image/png':window.StellisRecordUpload.ACCEPT;
  if(photo)file.setAttribute('capture','environment');
  field(host,photo?'Photo':'Record',file);
  const examples=photo?null:window.StellisRecordUpload.exampleChoices(false,files=>{
    const chosen=new DataTransfer();chosen.items.add(files[0]);file.files=chosen.files;file.dispatchEvent(new Event('change'));});
  if(examples)host.append(examples);
  const status=el('p');status.role='status';host.append(status);
  const review=el('div');host.append(review);
  // A record goes straight to its page: choosing the file is the only press (owner, 2026-10-29: no
  // confirmation steps between the upload and the report). A photo of a device still reads its value here.
  if(!photo){
    file.addEventListener('change',async()=>{
      if(!file.files.length)return;
      file.disabled=true;examples?.querySelectorAll('button').forEach(b=>b.disabled=true);status.textContent='Uploading';
      try {
        const result=await window.StellisRecordUpload.uploadFile(file.files[0],notebook.value || null);
        if(!['added','duplicate'].includes(result.outcome))throw new Error(result.message);
        const record=result.record;
        if(result.kind==='document' && record.status==='needs_text') {
          status.textContent='Reading text';
          try {await api('/documents/'+encodeURIComponent(record.id)+'/pages/1/read',{method:'POST'});}
          catch { /* The report page keeps Read text and its retry. */ }
        }
        window.reloadLibrary?.().catch(()=>{});window.dispatchEvent(new CustomEvent('health-capture-saved',{detail:{id:record.id,kind:result.kind}}));
        if(host.isConnected)location.assign((result.kind==='audio'?'/conversation/':'/document/')+encodeURIComponent(record.id));
      } catch(error){status.textContent=error.message;file.disabled=false;examples?.querySelectorAll('button').forEach(b=>b.disabled=false);}
    });
    return;
  }
  const uploadButton=btn('Read photo',async()=>{
    if(!file.files.length){file.reportValidity();return;}
    const measure=kind.value;const book=notebook.value || null;
    uploadButton.disabled=true;file.disabled=true;kind.disabled=true;notebook.disabled=true;status.textContent='Uploading';review.replaceChildren();
    try {
      const result=await window.StellisRecordUpload.uploadFile(file.files[0],book);
      if(!['added','duplicate'].includes(result.outcome))throw new Error(result.message);
      const record=result.record;const target=(result.kind==='audio'?'/conversation/':'/document/')+encodeURIComponent(record.id);
      if(measure==='document'){
        if(record.status==='needs_text') {
          status.textContent='Reading text';
          try {await api('/documents/'+encodeURIComponent(record.id)+'/pages/1/read',{method:'POST'});}
          catch { /* The report page keeps Read text and its retry. */ }
        }
        window.reloadLibrary?.().catch(()=>{});
        if(host.isConnected)location.assign(target);return;
      }
      status.textContent='Reading photo';
      let extracted;
      try {extracted=await api('/photo-readings/'+encodeURIComponent(record.id)+'/extract?measure='+measure,{method:'POST'});}
      catch {extracted={candidates:[]};}
      if(!host.isConnected)return;
      status.textContent=extracted.candidates.length?'Review the reading before saving':'Enter the reading shown in your photo';
      photoReview(review,record.id,measure,book,extracted.candidates,done);
      const source=el('a','Source photo');source.href=target;source.target='_blank';source.rel='noopener';review.prepend(source);
    } catch(error){status.textContent=error.message;}
    finally {uploadButton.disabled=false;file.disabled=false;kind.disabled=false;notebook.disabled=Boolean(review.querySelector('form'));}
  },'coach-button is-primary');host.append(uploadButton);
}
function photoReview(host,documentId,measure,notebookId,candidates,done) {
  const form=el('form',null,'capture-form');const value=field(form,measure==='blood_pressure'?'Top number':'Value',input('number'));value.step='any';
  const value2=measure==='blood_pressure'?field(form,'Bottom number',input('number')):null;
  const unit=el('select');unit.append(...UNITS[measure].map(u=>new Option(u,u)));field(form,'Unit',unit);
  const when=field(form,'When',input('datetime-local'));when.value=window.StellisTrends.localInputValue(new Date());
  const choose=c=>{value.value=c?.value ?? '';if(value2)value2.value=c?.value2 ?? '';unit.value=c?.unit || UNITS[measure][0];};choose(candidates[0]);
  if(candidates.length>1){const choices=el('select');choices.append(...candidates.map((c,i)=>new Option(c.quote,String(i))));field(form,'Detected readings',choices);choices.addEventListener('change',()=>choose(candidates[Number(choices.value)]));}
  const captureId=crypto.randomUUID();
  submit(form,'Save reading',async()=>{await api('/photo-readings',{method:'POST',json:{document_id:documentId,measure,value:Number(value.value),value2:value2?Number(value2.value):null,unit:unit.value,taken_at:new Date(when.value).toISOString(),notebook_id:notebookId,capture_id:captureId}});done('Reading saved','/trends?measure='+measure);});host.append(form);
}
async function showEvent(identifier) {
  const {body}=modal('Appointment');
  try {
    const item=(await api('/care-events')).events.find(e=>e.id===identifier);if(!item)throw new Error('Appointment not found');
    body.append(el('h3',item.title),el('p',item.precision==='month'?new Date(item.starts_at+'T12:00:00').toLocaleDateString([],{month:'long',year:'numeric'}):item.precision==='day'?new Date(item.starts_at+'T12:00:00').toLocaleDateString():new Date(item.starts_at).toLocaleString()),el('p',item.status==='scheduled'?'Scheduled':'Planned'));
    if(item.note)body.append(el('p',item.note));
    const preparation=await api('/care-events/'+encodeURIComponent(identifier)+'/preparation');
    if(preparation.page_id){const section=el('section');section.append(el('h3','Questions for this visit'));for(const question of preparation.questions)section.append(el('p',question.text));const link=el('a','Open preparation note','coach-button is-quiet');link.href='/page/'+preparation.page_id;section.append(link);body.append(section);}
    if(item.source_quote)body.append(el('blockquote',item.source_quote));
    if(item.source_document_id){const source=el('a','Original instruction','chat-source-link');source.href='/document/'+item.source_document_id+'?page='+(item.source_page||1);body.append(source);}
    if(item.notebook_id){const a=el('a','Open notebook');a.href='/notebook/'+encodeURIComponent(item.notebook_id);body.append(a);}
    const error=el('p');error.role='alert';
    body.append(btn('Delete appointment',async()=>{try{await api('/care-events/'+encodeURIComponent(identifier),{method:'DELETE'});saved(body,'Appointment deleted','/');}catch(e){error.textContent=e.message;}},'coach-button is-quiet'),error);
  } catch(e){body.append(el('p',e.message));}
}
window.StellisCapture={open};
const trigger=document.querySelector('#global-add');
const narrow=matchMedia('(max-width:1000px)');
const toolbar=el('nav');toolbar.setAttribute('aria-label','Health capture');toolbar.id='health-capture-toolbar';document.body.append(toolbar);
function placeTrigger(){(narrow.matches?document.querySelector('#compact-nav'):toolbar).append(trigger);}
narrow.addEventListener('change',placeTrigger);placeTrigger();
trigger.addEventListener('click',()=>open());
window.StellisHealthCapture={open};
document.addEventListener('click',event=>{const a=event.target.closest('a[href^="/?event="]');if(a&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey&&event.button===0){event.preventDefault();event.stopPropagation();showEvent(new URL(a.href).searchParams.get('event'));}},true);
const event=new URLSearchParams(location.search).get('event');if(event)showEvent(event);
})();
