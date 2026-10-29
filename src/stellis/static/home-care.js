/** Source-derived care context and joined actions; every write uses canonical APIs. */
(() => {
 const dom=()=>window.StellisDom,el=(tag,text,cls)=>dom().el(tag,text,cls);
 const button=(text,run,cls='coach-button is-quiet')=>{const b=dom().button(text,cls);b.onclick=run;return b};
 const dateLabel=day=>new Date(day+'T12:00:00').toLocaleDateString([],{weekday:'short',day:'numeric',month:'short'});
 let current;
 // A source is a quiet sparkle-and-info mark: hover or focus names it, and it opens the original.
 const sourceHint=source=>{const hint=el('a',null,'home-source-hint');hint.href=source.target;hint.setAttribute('aria-label','Source: '+source.title);hint.append(dom().icon('sparkles'),dom().icon('info-circle'));const tip=el('span',source.title,'home-source-tip');tip.setAttribute('role','tooltip');tip.setAttribute('aria-hidden','true');hint.append(tip);return hint;};
 class HomeCare {
  constructor(host){this.host=host;this.day=new URLSearchParams(location.search).get('day')||window.StellisCalendarDates.formatDate(new Date());this.request=0;this.alive=true;this.status=el('div',null,'home-care-status');this.status.setAttribute('role','status');this.body=el('div');this.goalDraft=el('section',null,'home-goal-draft');this.goalDraft.id='home-goal-draft';this.goalDraft.hidden=true;host.replaceChildren(this.status,this.body);}
  async load(){const ticket=++this.request;try{const data=await dom().api('/home/care?'+new URLSearchParams({day:this.day,utc_offset:String(-new Date().getTimezoneOffset())}));if(!this.alive||ticket!==this.request)return;this.data=data;this.paint();}catch(e){this.status.textContent=e.message;}}
  // One tick per dose (Apple Health medications): ticked is taken; an unticked dose is skipped by definition.
  async tick(item,day,on){try{const goal=item.id.startsWith('goal:');if(goal)await dom().api('/goals/'+encodeURIComponent(item.id.slice(5))+'/days/'+day,{method:'PUT',json:{done:on}});else await dom().api('/trackers/entries',{method:'POST',json:{day,entries:[{item:item.id,value:on?'taken':'skipped'}]}});await this.load();window.dispatchEvent(new Event('health-capture-saved'));return true;}catch(e){this.status.textContent=e.message;return false;}}
  async editContext(context){const [schema,records]=await Promise.all([dom().api('/trackers/kinds'),dom().api('/trackers?today='+this.day)]);const record=records.records.find(r=>r.id===context.id);if(record)await window.StellisTrackerForm.open(schema.kinds.find(k=>k.key===record.kind),record,{onSaved:async()=>{await this.load();window.dispatchEvent(new Event('health-capture-saved'));}});}
  // A section with nothing in it is left out (owner, 2026-10-27): no empty-state cards on My Health.
  context(){const data=this.data;if(!data.active&&!data.contexts.length)return null;const card=el('section',null,'home-context widget-paper');// The selector says what it does: which treatment phase or course Home counts days for (#231).
   card.setAttribute('aria-label','Counting days for');const picker=el('label','Counting days for','home-context-picker'),select=el('select');select.setAttribute('aria-label','Counting days for');select.append(new Option('Choose automatically',''));for(const c of data.contexts)select.append(new Option([...new Set([c.title,c.name].filter(Boolean))].join(', '),c.id));select.value=data.active?.id||'';select.onchange=async()=>{await dom().api('/home/context',{method:'PUT',json:{tracker_id:select.value||null}});await this.load();};picker.append(select);card.append(picker);
   const c=data.active;if(!c)return card;
   const title=el('h2',c.name||c.title,'home-context-title');if(c.source)title.append(sourceHint(c.source));card.append(title);
   const ring=Boolean(c.total&&c.progress!=null);const figure=el('div',null,'home-context-ring'+(ring?'':' is-plain'));figure.setAttribute('role','img');figure.setAttribute('aria-label',`${c.caption} ${c.number}${c.total?' of '+c.total:''}. ${c.detail}`);
   const text=el('div',null,'home-ring-text');text.append(el('span',c.caption),el('strong',String(c.number)));if(c.total)text.append(el('span','of '+c.total));
   if(ring){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 200 200');svg.setAttribute('aria-hidden','true');
    for(const [name,progress] of [['home-ring-track',1],['home-ring-progress',c.progress]]){const circle=document.createElementNS(svg.namespaceURI,'circle');for(const [key,value]of Object.entries({cx:100,cy:100,r:82,fill:'none',pathLength:1}))circle.setAttribute(key,String(value));circle.setAttribute('class',name);circle.setAttribute('stroke-dasharray',`${progress} 1`);svg.append(circle);}
    figure.append(svg);}
   // Without a ring, the detail ("days around Nov 10") belongs under its number, not a block below.
   if(ring){figure.append(text);card.append(figure,el('p',c.detail,'home-context-phase'));}else{text.append(el('span',c.detail,'home-ring-detail'));figure.append(text);card.append(figure);}
   const dates=el('dl',null,'home-context-dates');dates.append(el('dt',c.kind==='period'?'Last period':'Started'),el('dd',dateLabel(c.start)));if(c.end)dates.append(el('dt',c.kind==='pregnancy'?'Due date':'Through'),el('dd',dateLabel(c.end)));card.append(dates);
const actions=el('div',null,'home-context-actions');if(c.notebook_id){const link=el('a','Open notebook','coach-button is-quiet');link.href='/notebook/'+c.notebook_id+'?tab=overview';actions.append(link);}actions.append(button('Edit',()=>this.editContext(c)));card.append(actions);return card;
  }
  today(){const data=this.data,section=el('section',null,'home-today widget-paper');const ticked=state=>state==='taken'||state==='done';
   const head=el('div',null,'home-care-heading');head.append(el('h2',data.is_today?'Today':dateLabel(data.date)));if(data.items.length)head.append(el('span',`${data.items.filter(i=>ticked(i.state.status)).length} of ${data.items.length}`,'home-care-count'));section.append(head);
   if(!data.items.length)return null;
   const now=window.StellisCalendarDates.formatDate(new Date()),cells=new Map(data.history.map(h=>[h.id,h.cells]));
   const table=el('table',null,'home-dose-grid');table.setAttribute('aria-label','Doses and goals by day');const top=el('tr');top.append(el('td'));
   for(const day of data.days){const th=el('th',String(new Date(day+'T12:00:00').getDate()));th.scope='col';th.setAttribute('aria-label',dateLabel(day));th.title=dateLabel(day);if(day===data.date)th.className='is-selected';top.append(th);}
   const body=el('tbody');for(const item of data.items){const row=el('tr',null,'home-today-row');row.dataset.item=item.id;const name=el('th');name.scope='row';const title=el('div',null,'home-dose-name');title.append(el('strong',item.title));
    if(item.source)title.append(sourceHint(item.source));
    name.append(title);if(item.control.type==='status'&&item.detail)name.append(el('span',item.detail,'home-dose-amount'));row.append(name);
    for(const cell of cells.get(item.id)||[]){const td=el('td');if(!['not_due','unavailable'].includes(cell.state)){const on=ticked(cell.state),pill=dom().button(null,'home-dose-pill'+(cell.date===data.date?' is-selected':''));pill.setAttribute('aria-pressed',String(on));pill.setAttribute('aria-label',`${item.title}, ${dateLabel(cell.date)}`);pill.disabled=cell.date>now;pill.onclick=async()=>{if(pill.getAttribute('aria-busy')==='true')return;
     // The tick shows at once and the pointer stays as it is; the save follows, and only a failure undoes it (#258).
     const next=pill.getAttribute('aria-pressed')!=='true';pill.setAttribute('aria-pressed',String(next));pill.setAttribute('aria-busy','true');
     const saved=await this.tick(item,cell.date,next);if(!saved&&pill.isConnected){pill.setAttribute('aria-pressed',String(!next));pill.removeAttribute('aria-busy');}};td.append(pill);}row.append(td);}
    body.append(row);}
   const thead=el('thead');thead.append(top);table.append(thead,body);const scroll=el('div',null,'home-dose-scroll');scroll.append(table);section.append(scroll);return section;
  }
  aside(){const section=el('aside',null,'home-care-aside');const calendar=el('section',null,'home-care-calendar');// Booked and calculated care for the next two weeks; the date picker and the month calendar choose days.
   calendar.append(el('h2','Coming up'));// Nothing booked: no placeholder box.
   section.hidden=!this.data.upcoming.length;for(const event of this.data.upcoming.slice(0,5)){const row=el('div',null,'home-care-event');row.append(el('span',event.status==='scheduled'?'Booked':event.status==='calculated'?'Calculated':'Planned'),el('strong',event.title),el('time',dateLabel(event.date)+(event.time?', '+event.time:'')));if(event.target){const a=el('a','Details','coach-button is-quiet home-event-details');a.href=event.target;row.append(a);}calendar.append(row);}section.append(calendar);
   return section;
  }
  paint(){const toolbar=el('div',null,'home-care-toolbar');const date=el('label','Date'),input=el('input');input.type='date';input.value=this.day;input.onchange=()=>{this.day=input.value;const url=new URL(location.href);url.searchParams.set('day',this.day);history.replaceState({},'',url);this.load();window.StellisHealthDashboard?.focusCalendar(this.day);};date.append(input);// The daily check-in rings on My Health and Call Stellis calls on demand; the board only keeps the history (#231, one control per action).
   const calls=button('Call history',()=>window.StellisCallHistory.open());calls.prepend(dom().icon('clock'));toolbar.append(date,calls);const parts=[this.context(),this.today(),this.aside()].filter(part=>part&&!part.hidden);
   // The goal draft stays mounted for a call's fill_goal action, even when no section shows.
   const layout=el('div',null,'home-care-board');layout.dataset.parts=String(parts.length);layout.append(...parts);this.body.replaceChildren(...(parts.length?[toolbar,layout]:[]),this.goalDraft);}
 }
 window.StellisHomeCare={async mount(host){if(current?.host!==host){if(current)current.alive=false;current=new HomeCare(host);}await current.load();return current;},reload:()=>current?.alive?current.load():null,get date(){return current?.day;}};
})();
