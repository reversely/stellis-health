/** Durable ordinary-account call transcripts and actual confirmation receipts. */
(function(root){
'use strict';
async function open(){
 const d=root.StellisDom,dialog=d.el('dialog',null,'dashboard-dialog call-history widget-paper');dialog.setAttribute('aria-label','Call history');
 const header=d.el('div',null,'tracker-dialog-head'),heading=d.el('h2','Call history'),close=d.button('Close','coach-button is-quiet');close.onclick=()=>dialog.close();header.append(heading,close);const body=d.el('div',null,'call-history-body');dialog.append(header,body);const opener=document.activeElement;document.body.append(dialog);dialog.addEventListener('close',()=>{dialog.remove();opener?.focus({preventScroll:true});},{once:true});dialog.showModal();body.textContent='Loading calls…';
 try{const calls=await d.api('/coach/calls');body.replaceChildren();if(!calls.length)body.append(d.el('p','No calls yet. Your conversations will appear here.'));
 for(const call of calls){const row=d.el('article',null,'call-history-row');const view=d.button(`${new Date(call.started_at).toLocaleString()}, ${call.kind==='morning'?'Daily check-in':'Call with Stellis'}`,'coach-button is-quiet');view.onclick=()=>detail(call.id);row.append(view,d.el('p',`${call.messages} messages, ${call.saved_actions} confirmed logs${call.review_day?', Reviewed '+call.review_day:''}`));body.append(row);}
 }catch(e){body.textContent=e.message;}
 async function detail(id){try{const call=await d.api('/coach/calls/'+id);body.replaceChildren();const back=d.button('All calls','coach-button is-quiet');back.onclick=()=>{dialog.close();open();};body.append(back,d.el('p','Recorded '+new Date(call.started_at).toLocaleString()));if(call.context.today)body.append(d.el('p','Review date: '+call.context.today));if(call.context.source_context){const source=call.context.source_context,proof=d.el('details');proof.append(d.el('summary','Source at call time'),d.el('blockquote',source.quote));const link=d.el('a',source.title,'chat-source-link');link.href=source.target;proof.append(link);body.append(proof);}
 const log=d.el('ol',null,'call-history-log');for(const turn of call.turns){if(!turn.text)continue;const row=d.el('li',null,turn.role==='person'?'is-person':'is-agent');row.append(d.el('strong',turn.role==='person'?'You':'Stellis'),d.el('p',turn.text));log.append(row);}body.append(log);
 for(const action of call.saved_actions||[]){body.append(d.el('p',`${action.entries.length} entries confirmed, ${new Date(action.saved_at).toLocaleString()}${action.undo_applied?', Undo applied':''}`));}
 const again=d.button('Call again','coach-button is-primary');again.onclick=async()=>{try{const handoff=call.context.source_context?await d.api('/source-handoffs/'+call.context.source_context.id):null;dialog.close();root.StellisCoachCall.open({kind:call.kind,chat:false,referenceDay:call.context.today,handoff});}catch(e){body.append(d.el('p',e.message));}};body.append(again);
 }catch(e){body.append(d.el('p',e.message));}}
}
root.StellisCallHistory={open};
})(window);
