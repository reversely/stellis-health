/** Source-quoted care review, separate from accepted measurements. */
(function(root){
'use strict';
async function mount(host,kind,identifier){
 const d=root.StellisDom, base=`/care-review/${encodeURIComponent(kind)}/${encodeURIComponent(identifier)}`;
 const section=d.el('section',null,'routine-review widget-paper');section.setAttribute('aria-label','Review care changes');host.append(section);
 const status=d.el('p','Preparing care review…');status.setAttribute('role','status');
 section.append(d.el('h3','Care changes'),status);
 // Hidden until there is something to review or a failure to retry; the report's found block leads here (#258).
 section.hidden=true;
 // A model reply that fails or doesn't validate is retried once before the panel shows the error.
 let retried=false;
 async function load(prepare=false){try{const data=await d.api(base+(prepare?'/prepare':''),prepare?{method:'POST'}:{});if(!section.isConnected)return;if(data.state==='failed')throw new Error(data.detail);if(data.state==='preparing'){status.textContent='Preparing care review…';setTimeout(()=>{if(section.isConnected)load(true);},1500);return;}if(data.state==='not_prepared'){await load(true);return;}paint(data);}catch(error){if(prepare&&!retried){retried=true;setTimeout(()=>{if(section.isConnected)load(true);},2000);return;}status.textContent=error.message;section.hidden=false;if(/used its AI credits|allowance is used up/.test(error.message))return;const retry=d.button('Retry care review','coach-button is-quiet');retry.onclick=()=>{retry.remove();status.textContent='Preparing care review…';load(true);};section.append(retry);}}
 function instruction(p,disclosure=true){const proof=d.el(disclosure?'details':'div');if(disclosure)proof.append(d.el('summary','Original instruction'));proof.append(d.el('blockquote',p.quote));if(p.identity_evidence){const identity=p.identity_evidence;proof.append(d.el('p',`Matched the saved course through “${identity.alias}” in its prescription.`),d.el('blockquote',identity.quote));const prior=d.el('a','Open saved prescription','chat-source-link');prior.href=`/document/${identity.document_id}?page=${identity.part}`;proof.append(prior);}if(kind==='document'){const link=d.el('a',`Open source, Page ${p.part}`,'chat-source-link');link.href=`/document/${identifier}?page=${p.part}`;proof.append(link);}return proof;}
 const day=value=>new Date(value+'T12:00:00').toLocaleDateString([],{day:'numeric',month:'short',year:'numeric'});
 function paint(data){
  section.replaceChildren(d.el('h3','Care changes'),status);
  // The report's chat offers Review care changes only while there is a change to review or undo.
  const reviewable=data.state==='applied'||(!['dismissed','undone'].includes(data.state)&&data.proposals.length>0);section.hidden=!reviewable;
  section.dispatchEvent(new CustomEvent('routine-review-state',{bubbles:true,detail:{reviewable}}));
  if(data.state==='needs_date'){status.textContent='Confirm the visit or test date to review care changes.';return;}
  if(data.state==='dismissed'||data.state==='undone'){status.textContent=data.state==='undone'?'Care changes undone.':'Care review dismissed.';return;}
  if(data.state==='applied'){status.textContent=`${data.receipt.saved} care ${data.receipt.saved===1?'item':'items'} approved ${new Date(data.receipt.saved_at).toLocaleDateString([],{day:'numeric',month:'short',year:'numeric'})}`;const undo=d.button('Undo changes','coach-button is-quiet');undo.onclick=async()=>{undo.disabled=true;try{await d.api(`/care-review/${data.id}/undo`,{method:'POST'});root.dispatchEvent(new CustomEvent('health-capture-saved'));await load();}catch(e){status.textContent=e.message;undo.disabled=false;}};section.append(undo);const chosen=data.receipt.selected?new Set(data.receipt.selected):data.receipt.saved===data.proposals.length?new Set(data.proposals.map(p=>p.id)):new Set();if(chosen.size){const history=d.el('details');history.append(d.el('summary','Approved instructions'));for(const p of data.proposals.filter(p=>chosen.has(p.id))){const row=d.el('article',null,'routine-review-item');row.append(d.el('h4',p.data.name||p.data.title||p.kind),d.el('p',`Approved for ${day(p.effective_date)}${p.data.end?', last dose '+day(p.data.end):''}`),instruction(p,false));history.append(row);}section.append(history);}return;}
  if(!data.proposals.length){status.textContent='No explicit care changes found in this source.';return;}
  status.textContent='Proposed from the original source. Review each item; nothing changes until you approve.';
  const boxes=[];
  for(const p of data.proposals){const row=d.el('article',null,'routine-review-item');const label=d.el('label',null,'routine-review-choice');const box=d.el('input');box.type='checkbox';box.checked=true;boxes.push([box,p.id]);label.append(box,d.el('strong',`${({add:'Add',change:'Change',keep:'Keep',event:p.data.status==='scheduled'?'Booked':'Planned',timing:'Calculate after dose'})[p.action]}, ${p.data.name||p.data.title||p.kind}`));
   const fields=d.el('dl',null,'routine-review-values');
   const value=p.data,rows=[];
   if(p.action==='timing')rows.push(['Medicine',value.medicine_name||'Selected course'],['Event',value.title],['Interval',`${value.hours} hours after confirmed dose`],['Dose date',value.anchor_day]);
   else if(p.action==='event')rows.push(['When',value.precision==='month'?new Date(value.starts_at+'T12:00:00').toLocaleDateString([],{month:'long',year:'numeric'}):value.starts_at]);
   else if(p.kind==='medicine'){
    rows.push(['Dose',`${value.dose_amount} ${value.dose_unit}`],['Schedule',value.schedule_type==='every'?`Every ${value.every_days} days`:value.schedule_type==='weekdays'?value.weekdays.map(n=>['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][n]).join(', '):value.schedule_type==='as_needed'?'As needed':'Daily'],['Status',value.end&&value.end<p.effective_date?'Completed':value.course_state||'active']);
    if(value.start)rows.push(['Started',value.start]);if(value.end)rows.push(['Last dose',value.end]);
    if(value.units_per_fill)rows.push(['Each fill',`${value.units_per_fill} ${value.supply_unit||'units'}, ${value.repeats||0} repeats`]);
    if(value.time)rows.push(['Reminder',value.time]);
   }else rows.push(['Phase',value.phase||p.kind],['Start',value.start||value.basis_date]);
   if(value.note)rows.push(['Notes',value.note]);for(const [label,value] of rows)fields.append(d.el('dt',label),d.el('dd',String(value)));

   const old=p.before?.dose_amount;if(p.action==='change'&&old!==p.data.dose_amount)fields.prepend(d.el('dt','Previous dose'),d.el('dd',`${old} ${p.before.dose_unit}`));
   const effective=p.action==='event'?(p.data.precision==='month'?new Date(p.data.starts_at+'T12:00:00').toLocaleDateString([],{month:'long',year:'numeric'}):p.data.starts_at):p.effective_date;row.append(label);if(p.action!=='event')row.append(d.el('p',`Effective ${effective}${p.edited?', Edited by you':''}`));row.append(fields);if(p.action==='add'||p.action==='change'){const edit=d.button('Edit proposal','coach-button is-quiet');edit.onclick=async()=>{try{const schemas=await d.api('/trackers/kinds');await root.StellisTrackerForm.open(schemas.kinds.find(k=>k.key===p.kind),p.data,{onSubmit:values=>d.api(`/care-review/${data.id}/items/${p.id}`,{method:'PUT',json:{data:values}}),onSaved:()=>load()});}catch(e){status.textContent=e.message;}};row.append(edit);}row.append(instruction(p));section.append(row);
  }
  const actions=d.el('div',null,'document-actions');const approve=d.button('Approve selected care','coach-button is-primary'),dismiss=d.button('Not now','coach-button is-quiet');
  approve.onclick=async()=>{approve.disabled=true;try{await d.api(`/care-review/${data.id}/apply`,{method:'POST',json:{selected:boxes.filter(([b])=>b.checked).map(([,id])=>id)}});root.dispatchEvent(new CustomEvent('health-capture-saved'));await load();}catch(e){status.textContent=e.message;approve.disabled=false;}};
  dismiss.onclick=async()=>{try{await d.api(`/care-review/${data.id}/dismiss`,{method:'POST'});await load();}catch(e){status.textContent=e.message;}};actions.append(approve,dismiss);section.append(actions);
 }
 await load();
}
root.StellisRoutineReview={mount};
})(window);
