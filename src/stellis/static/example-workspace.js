/** Historical fixture workspaces have account controls, never a second app shell. */
(() => {
 const YEARS = ['2027', '2028', '2029', '2030', '2031'];

 async function settings() {
  const host=document.getElementById('example-workspace-settings');
  host.replaceChildren();host.hidden=true;
  const dom=window.StellisDom;
  const state=await dom.api('/demo/status').catch(()=>null);
  if(!state?.active)return false;
  host.hidden=false;
  host.append(dom.el('h3','Example records'),dom.el('p','You are viewing fictional example records in an isolated workspace. Your own records are separate.'));
  const add=dom.button('Add example records','coach-button is-primary');add.onclick=()=>open();
  const organized=dom.button('How Stellis organized them','coach-button is-quiet');organized.onclick=()=>open({history:true});
  const back=dom.button('Return to my records','coach-button is-quiet');
  const reset=dom.button('Clear example records','coach-button is-quiet');
  const status=dom.el('p');status.role='status';
  for(const [button,path,method] of [[back,'/demo/leave','POST'],[reset,'/demo/history','DELETE']])button.onclick=async()=>{
   button.disabled=true;try{await dom.api(path,{method});location.assign('/');}catch(error){status.textContent=error.message;button.disabled=false;}
  };
  const actions=dom.el('div',null,'settings-actions');actions.append(add,organized,back,reset);host.append(actions,status);
  return true;
 }

 /** The next year to add, or null when all five are filed. */
 function nextYear(state) {
  return YEARS.find(year => !state.years?.[year]?.filed) || null;
 }

 // While example records are open, every page says so, offers the next year and removes them in one click.
 async function banner() {
  const dom=window.StellisDom;
  const state=await dom.api('/demo/status').catch(()=>null);
  document.getElementById('example-workspace-bar')?.remove();
  if(!state?.active)return;
  const bar=dom.el('aside',null,'example-bar');bar.id='example-workspace-bar';bar.setAttribute('aria-label','Example records');
  const mark=dom.el('span',null,'example-bar-icon');mark.append(dom.icon('sparkles'));
  // A notch at the top of the page: the label, the next year and one-click removal, level with Add.
  const words=dom.el('p',null,'example-bar-words');words.append(dom.el('strong','Example records'));
  const year=nextYear(state);
  const add=dom.button(year?`Add ${year} records`:'Sorting history','coach-button example-bar-add');
  add.onclick=()=>open(year?{}:{history:true});
  // Back to my records stays in Settings; the bar carries the one-click removal.
  const remove=dom.button('Remove','coach-button example-bar-remove');remove.setAttribute('aria-label','Remove example records');
  const status=dom.el('span',null,'example-bar-status');status.setAttribute('role','status');
  remove.onclick=async()=>{
   remove.disabled=true;try{await dom.api('/demo/history',{method:'DELETE'});location.assign('/');}catch(error){status.textContent=error.message;remove.disabled=false;}
  };
  const [story,guide]=await Promise.all([dom.api('/demo/story-date').catch(()=>null),fetch('/static/maya/guide.json').then(r=>r.json()).catch(()=>null)]);
  // The year steps show once there are years to step through.
  const steps=story&&Object.values(state.years||{}).some(y=>y.filed)?stepper(story,guide,status):null;
  bar.append(mark,words,...(steps?[steps]:[]),add,remove,status);
  document.body.prepend(bar);
 }

 /**
  * Steps through Maya's years (#252, #258): one click moves the example workspace to the end of the
  * previous or next year and reloads, so every page shows her records as they stood then. The label
  * names the year and what happened in it, from the example records' guide.
  */
 function stepper(story,guide,status) {
  const dom=window.StellisDom;
  const first=Number(YEARS[0]),last=Number(YEARS.at(-1));
  // The story date is the morning after the year it shows, so Jan 1, 2032 shows the whole of 2031.
  const shown=Math.min(last,Math.max(first,new Date(Date.parse(story.date+'T12:00:00Z')-864e5).getUTCFullYear()));
  const chapter=guide?.chapters?.find(c=>Number(c.year)===shown);
  const group=dom.el('div',null,'example-bar-years');group.setAttribute('role','group');group.setAttribute('aria-label','Maya’s year');
  const move=async year=>{
   for(const node of group.querySelectorAll('button'))node.disabled=true;
   try{await dom.api('/demo/story-date',{method:'PUT',json:{date:`${year+1}-01-01`}});location.reload();}
   catch(error){status.textContent=error.message;for(const node of group.querySelectorAll('button'))node.disabled=false;}
  };
  const arrow=(icon,year,name)=>{const button=dom.button(null,'example-bar-step');button.append(dom.icon(icon));button.setAttribute('aria-label',name);button.disabled=year<first||year>last;button.onclick=()=>move(year);return button;};
  const words=dom.el('span',null,'example-bar-year');words.append(dom.el('strong',String(shown)),...(chapter?[dom.el('span',chapter.title)]:[]));
  group.append(arrow('chevron-left',shown-1,`Show ${shown-1}`),words,arrow('chevron-right',shown+1,`Show ${shown+1}`));
  return group;
 }

 /** The pipeline every report goes through, in the words the dialog shows beside each stage. */
 const STAGES = [
  ['read', 'Read', 'Each page becomes text Stellis can cite'],
  ['date', 'Date', 'The date on each report places it on the timeline'],
  ['file', 'File', 'Stellis picks a notebook for each report'],
  ['extract', 'Extract', 'Results and care instructions come from each line'],
 ];
 const shortDay = iso => iso ? new Date(iso + 'T12:00:00').toLocaleDateString([], {day: 'numeric', month: 'short'}) : '';

 /** What a finished stage found, in one line. */
 function found(stage, counts, d) {
  if (stage === 'read') return `${d.plural(counts.records || 0, 'report')}, ${d.plural(counts.readings || 0, 'home reading')}`;
  if (stage === 'date') return counts.first ? `${shortDay(counts.first)} to ${shortDay(counts.last)}` : '';
  if (stage === 'file') {
   const total = (counts.created || 0) + (counts.grown || 0);
   if (!total) return 'No notebook changes';
   return [counts.created ? `${counts.created} new` : '', counts.grown ? `${counts.grown} grown` : ''].filter(Boolean).join(', ') + (total === 1 ? ' notebook' : ' notebooks');
  }
  return `${d.plural(counts.results || 0, 'result')}, ${d.plural(counts.care || 0, 'care change')}`;
 }

 /**
  * Adds Maya's example records a year at a time, on one screen (#258): one click per year's pack,
  * the pipeline shown as it runs, then where to look and what to ask. The records are fictional,
  * so nothing asks the visitor to confirm a date, a notebook or a figure.
  * @param {{history: (boolean|undefined)}=} options
  */
 async function open(options = {}) {
  const d=window.StellisDom;
  await d.api('/demo/enter',{method:'POST'});
  const dialog=d.el('dialog',null,'dashboard-dialog example-dialog example-pack widget-paper');dialog.setAttribute('aria-labelledby','example-pack-title');
  const head=d.el('div',null,'tracker-dialog-head');const title=d.el('h2','Maya’s example records');title.id='example-pack-title';
  const close=d.button('Close','coach-button is-quiet');close.onclick=()=>dialog.close();
  head.append(title,close);
  const body=d.el('div',null,'example-dialog-body');const status=d.el('p',null,'example-dialog-status');status.setAttribute('role','status');
  dialog.append(head,body,status);document.body.append(dialog);
  // Closing refreshes the page behind: new notebooks, results, care and history appear without a reload.
  dialog.addEventListener('close',async()=>{dialog.remove();await window.reloadLibrary?.();window.dispatchEvent(new Event('health-capture-saved'));await window.StellisHomeCare?.reload();await banner();});
  dialog.showModal();
  let state=await d.api('/demo/status'),polling=false;

  // A next step closes the dialog and opens its page in place, as the app's own links do.
  const go=href=>{dialog.close();window.history.pushState({},'',href);window.dispatchEvent(new PopStateEvent('popstate'));};
  const link=(text,href,cls='example-next-link')=>{const a=d.el('a',text,cls);a.href=href;a.onclick=event=>{if(event.metaKey||event.ctrlKey)return;event.preventDefault();go(href);};return a;};

  function render(year,pack) {
   const filed=YEARS.filter(y=>state.years?.[y]?.filed);
   const intro=d.el('p','Maya Chen is fictional. Each year’s pack holds her reports and home readings; Stellis handles them the way it handles yours.','example-pack-intro');
   const chips=d.el('ol',null,'example-pack-years');chips.setAttribute('aria-label','Example years');
   for(const y of YEARS){const chip=d.el('li',null,'example-pack-year');if(filed.includes(y)){chip.classList.add('is-done');chip.append(d.icon('check'));}chip.append(y);if(y===year)chip.setAttribute('aria-current','step');chips.append(chip);}
   const stages=d.el('ol',null,'example-pipeline');
   const at=pack?STAGES.findIndex(([key])=>key===pack.stage):-1;
   STAGES.forEach(([key,name,what],index)=>{
    const done=pack&&(pack.stage==='done'||index<at),running=pack&&!pack.error&&index===at&&pack.stage!=='done',failed=pack?.error&&index===at;
    const row=d.el('li',null,'example-stage'+(done?' is-done':running?' is-running':failed?' is-failed':''));
    const mark=d.el('span',null,'example-stage-mark');mark.append(d.icon(done?'check':failed?'alert-circle':'circle'));
    const words=d.el('div',null,'example-stage-words');words.append(d.el('strong',name),d.el('span',done?found(key,pack.counts,d):failed?pack.error:what));
    row.append(mark,words);stages.append(row);
   });
   const nodes=[intro,chips,stages];
   if(pack?.stage==='done'&&pack.next)nodes.push(nextSteps(pack.year,pack.next));
   const actions=d.el('div',null,'settings-actions');
   const busy=pack&&!pack.error&&pack.stage!=='done';
   const upcoming=YEARS.find(y=>!state.years?.[y]?.filed&&y!==(pack?.stage==='done'?pack.year:null));
   if(busy){const wait=d.button(`Adding ${year}…`,'coach-button is-primary');wait.disabled=true;actions.append(wait);}
   else if(pack?.error){actions.append(start(year,'Try again'));}
   else if(upcoming){actions.append(start(upcoming,`Add ${upcoming} pack`));}
   const organized=d.button('How Stellis organized them','coach-button is-quiet');organized.onclick=history;
   if(filed.length)actions.append(organized);
   nodes.push(actions);
   body.replaceChildren(...nodes);
  }

  /** Where to look once a year is in, and what to ask: each line opens the page it names. */
  function nextSteps(year,next) {
   const box=d.el('section',null,'example-next');box.append(d.el('h3',`${year} is in. Try next`));
   const list=d.el('ul',null,'example-next-list');
   const item=(icon,node)=>{const row=d.el('li');const glyph=d.el('span',null,'example-next-icon');glyph.append(d.icon(icon));row.append(glyph,node);list.append(row);};
   if(next.report)item('file-text',link(`See how Stellis read ${next.report.title}`,next.report.href));
   for(const question of next.questions)item('message-2',link(`Ask: ${question.text}`,question.href));
   if(next.notebooks.length){const row=d.el('span',null,'example-next-books');row.append('Notebooks: ');next.notebooks.forEach((book,i)=>{if(i)row.append(', ');row.append(link(book.name,book.href));});item('notebook',row);}
   item('activity',link('See the measurements over the years','/trends'));
   item('calendar',link('See the timeline','/timeline'));
   box.append(list);return box;
  }

  function start(year,label) {
   const button=d.button(label,'coach-button is-primary');
   button.onclick=async()=>{button.disabled=true;status.textContent='';
    try{render(year,await d.api(`/demo/years/${year}/add`,{method:'POST'}));poll(year);}
    catch(error){status.textContent=error.message;button.disabled=false;}};
   return button;
  }

  async function poll(year) {
   if(polling)return;polling=true;
   try{
    for(;;){
     const pack=await d.api(`/demo/years/${year}/add`);if(!dialog.isConnected)return;
     if(pack.stage==='done'){state=await d.api('/demo/status');await window.reloadLibrary?.();}
     render(year,pack);
     if(pack.stage==='done'||pack.error)return;
     await new Promise(r=>setTimeout(r,1200));
    }
   }finally{polling=false;}
  }

  function roundView(round) {
   const block=d.el('section',null,'records-group');
   block.append(d.el('h3',`${round.year}: ${d.plural(round.records,'record')}, ${d.plural(round.biometrics?.readings||0,'home measurement')}`));
   const list=d.el('ul',null,'records-group-list');
   for(const book of round.created)list.append(line('New',book));
   for(const book of round.grown)list.append(line(`+${book.added}`,book));
   if(!list.children.length)list.append(d.el('li','No notebooks changed'));
   block.append(list,d.el('p',`${d.plural(round.notebooks,'notebook')} in ${`${round.families} ${round.families===1?'family':'families'}`}`,'dashboard-meta'));
   return block;
  }
  function line(tag,book) {
   const row=d.el('li',null,'example-row');
   row.append(d.el('strong',`${book.family||'No family'} › ${book.name}`),d.el('span',tag,'records-tag'));
   if(book.reason)row.append(d.el('p',book.reason,'records-reason example-row-reason'));
   return row;
  }

  async function history() {
   title.textContent='How Stellis organized the records';
   const rounds=await d.api('/demo/history');
   const nodes=rounds.length?rounds.map(roundView):[d.el('p','No years added yet.')];
   const back=d.button('Back','coach-button is-quiet');back.onclick=()=>{title.textContent='Maya’s example records';show();};
   const actions=d.el('div',null,'settings-actions');actions.append(back);
   body.replaceChildren(...nodes,actions);
  }

  /** The newest year that is running or just finished, or the next one to add. */
  async function show() {
   const [current,...packs]=await Promise.all([d.api('/demo/status'),...YEARS.map(year=>d.api(`/demo/years/${year}/add`))]);
   state=current;
   const active=packs.find(pack=>pack.running||pack.error||(pack.stage!=='done'&&pack.stage!=='waiting'));
   if(active){render(active.year,active);if(!active.error)poll(active.year);return;}
   const last=[...packs].reverse().find(pack=>pack.stage==='done');
   render(last?last.year:YEARS[0],last||null);
  }

  if(options.history)return history();
  await show();
 }

 window.StellisExampleWorkspace={settings,banner,open};
 // /?examples=add or /?examples=history opens the dialog directly, for a shared link or a test.
 function start(){banner();const view=new URLSearchParams(location.search).get('examples');if(view==='add'||view==='history')open(view==='history'?{history:true}:{});}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
