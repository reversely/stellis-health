/** Explicitly reviewed questions, attached to actual booked visits. */
(function(root){
'use strict';
function mount(host,item){
 const d=root.StellisDom,p=item.follow_up;if(!p||p.state==='dismissed')return;
 const section=d.el('section',null,'report-followup');section.setAttribute('aria-label','Visit question');host.append(section);
 const heading=d.el('h4','Take to your visit'),status=d.el('p');status.setAttribute('role','status');section.append(heading,status);
 function saved(receipt){section.replaceChildren(heading,d.el('p',receipt.question),d.el('p','Saved to your visit preparation.'));const link=d.el('a','Open preparation note','coach-button is-quiet');link.href='/page/'+receipt.page_id;section.append(link);}
 if(p.state==='accepted'){saved(p);return;}
 if(item.stale){status.textContent='The source changed. Ask again before adding this question.';return;}
 status.textContent='Suggested from this conversation. Edit the question and choose its visit before saving.';
 const questionLabel=d.el('label','Question'),question=d.el('textarea');question.rows=3;question.maxLength=240;question.value=p.question;questionLabel.append(question);
 const visitLabel=d.el('label','Booked visit'),visit=d.el('select');visit.append(new Option('Choose a booked visit',''));visitLabel.append(visit);
 const actions=d.el('div',null,'document-actions'),approve=d.button('Add question','coach-button is-primary'),dismiss=d.button('Not now','coach-button is-quiet'),refresh=d.button('Refresh visits','coach-button is-quiet');approve.disabled=true;
 const newVisit=d.button('Add visit','coach-button is-quiet');newVisit.onclick=()=>root.StellisCapture.open('appointment');
 async function visits(){try{const result=await d.api('/care-events');const selected=visit.value;visit.replaceChildren(new Option('Choose a booked visit',''));for(const event of result.events.filter(e=>e.status==='scheduled'))visit.append(new Option(`${new Date(event.starts_at).toLocaleString()}, ${event.title}`,event.id));visit.value=selected;approve.disabled=!visit.value;if(visit.options.length===1)status.textContent='No booked visits yet. Add the visit, then refresh this list.';}catch(e){status.textContent=e.message;}}
 refresh.onclick=visits;visit.onchange=()=>{approve.disabled=!visit.value;};
 approve.onclick=async()=>{approve.disabled=true;try{const receipt=await d.api(`/report-followups/${p.id}/approve`,{method:'POST',json:{visit_id:visit.value,question:question.value,time_zone:Intl.DateTimeFormat().resolvedOptions().timeZone}});Object.assign(p,receipt);saved(receipt);root.dispatchEvent(new Event('health-capture-saved'));}catch(e){status.textContent=e.message;approve.disabled=!visit.value;}};
 dismiss.onclick=async()=>{try{await d.api(`/report-followups/${p.id}/dismiss`,{method:'POST'});p.state='dismissed';section.remove();}catch(e){status.textContent=e.message;}};
 const proof=d.el('details');proof.append(d.el('summary','Why this question'),d.el('blockquote',p.quote));const link=d.el('a','Original source','chat-source-link');link.href='/document/'+p.source_document_id+'?page='+p.page;proof.append(link);
 actions.append(approve,dismiss,newVisit,refresh);section.append(questionLabel,visitLabel,proof,actions);visits();
}
root.StellisReportFollowups={mount};
})(window);
