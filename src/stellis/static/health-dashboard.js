/** Composes source-linked health previews without generating clinical claims. */
(function(root) {
'use strict';
const dom = () => root.StellisDom;
const el = (tag, text, cls) => dom().el(tag, text, cls);
const button = (text, action, cls = 'coach-button is-quiet') => {
  const node = dom().button(text, cls);
  node.addEventListener('click', action);
  return node;
};
/** The history section's two destinations, in its header: one control per destination. */
const historyLinks = () => {
  const links = el('nav',null,'dashboard-head-links'); links.setAttribute('aria-label','Health history views');
  links.append(link('Timeline','/timeline','coach-button'),link('Records','/records','coach-button'));
  return links;
};
const link = (text, href, cls = '') => {
  const node = el('a', text, cls); node.href = href; return node;
};
const day = value => {
  if (!value) return '';
  const date = new Date(String(value).length === 10 ? value + 'T12:00:00' : value);
  return Number.isFinite(date.getTime()) ? date.toLocaleDateString([], {month:'short', day:'numeric', year:'numeric'}) : '';
};
let dialogId = 0;
/** Brings the goals on My Health into view once they have rendered (they load after the page). */
function openGoals(tries = 40) {
  // #home-goals opens with the vital signs; the goals start at their own heading.
  const title=document.querySelector('#goals-title');
  if (!title && tries > 0) { setTimeout(()=>openGoals(tries-1),125); return; }
  const goals=title?.closest('.coach-section-head')?.parentElement||document.querySelector('#home-goals');
  title?.scrollIntoView({block:'start'});
  (goals.querySelector('.coach-form input, .coach-form select')||goals.querySelector('button'))?.focus({preventScroll:true});
}
if (new URLSearchParams(root.location?.search||'').get('goals')==='1') root.addEventListener?.('load',()=>openGoals());
const dateKey = date => date?root.StellisCalendarDates.formatDate(date):(root.StellisDemoDate||root.StellisCalendarDates.formatDate(new Date()));

/** Uses the current data order unless the person saved an explicit selection. */
function selected(items, keys, field = 'id') {
  const index = new Map(items.map(item => [item[field], item]));
  return keys.map(key => index.get(key)).filter(Boolean).slice(0,3);
}

function sparkline(points) {
  const values = points.slice(-20).map(point => point.value).filter(Number.isFinite);
  if (values.length < 2) return null;
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 200 40'); svg.setAttribute('class','dashboard-sparkline');
  svg.setAttribute('aria-hidden','true');
  const min = Math.min(...values), span = Math.max(...values) - min || 1;
  const path = document.createElementNS(svg.namespaceURI,'polyline');
  path.setAttribute('points',values.map((value,i) => `${4+i*192/(values.length-1)},${36-(value-min)*32/span}`).join(' '));
  svg.append(path); return svg;
}

/** The same notebook card on the dashboard and setup completion. */
function notebookCard(book, {collection, recent, preview=false}={}) {
  const card=preview?el('article',null,'dashboard-book'):link('', '/notebook/'+encodeURIComponent(book.id),'dashboard-book');
  card.append(dom().icon('notebook'),el('strong',book.name));
  if(collection&&collection.name.toLocaleLowerCase()!==book.name.toLocaleLowerCase())card.append(el('span',collection.name,'dashboard-meta'));
  if(recent?.title||book.description)card.append(el('small',recent?.title||book.description));
  return card;
}

class Dashboard {
  constructor(host, library) {
    this.host = host; this.books = library.books; this.collections = library.collections;
    this.sections = new Map(); this.data = {}; this.destroyed = false;
    const grid = el('div',null,'dashboard-grid');
    for (const [key,title,action] of [
      ['overview','Health Overview',link('Full summary','/summary')],
      ['upcoming','Calendar',link('Calendar settings','/settings')],
      ['reproductive','Tracking',el('span')],
      ['history','Health History',historyLinks()],
    ]) {
      const section = el('section',null,'dashboard-section dashboard-'+key+(key==='upcoming'?' dashboard-calendar':'')+(key==='overview'?' widget-paper':''));
      const head = el('div',null,'dashboard-section-head');
      const titleNode = el('h2',title); titleNode.id = 'dashboard-'+key+'-title';
      section.setAttribute('aria-labelledby',titleNode.id); head.append(titleNode,action);
      const body = el('div',null,'dashboard-body'); body.append(el('p','Loading','dashboard-empty'));
      section.append(head,body); grid.append(section); this.sections.set(key,body);
    }
    this.status = el('p',null,'dashboard-status'); this.status.setAttribute('role','status');
    this.careHost=el('section',null,'home-care-content');this.careHost.id='home-care-content';
    const workspace=el('div',null,'home-care-layout');workspace.append(this.careHost);
    // A call opened while Home rebuilds (Call Stellis right after Go to My Health) keeps its workspace.
    const liveCall=document.querySelector('#home-call-workspace .coach-call')?.parentElement;
    const callHost=liveCall||el('aside',null,'home-call-workspace');
    if(!liveCall){callHost.id='home-call-workspace';callHost.hidden=true;}else workspace.classList.add('has-active-call');
    workspace.append(callHost);
    // Calendar, tracking and history follow the library on My Health; nothing is collapsed (#231).
    document.getElementById('home-more')?.remove();const supplementary=el('div',null,'home-supplementary');supplementary.id='home-more';supplementary.append(grid);
    // For you now leads Home: the most pressing items across the notebooks (GET /home/focus).
    this.focus=el('section',null,'home-focus widget-paper');this.focus.setAttribute('aria-labelledby','home-focus-title');
    const focusTitle=el('h2','For you now');focusTitle.id='home-focus-title';
    // Until its items arrive the card says it is loading, never a bare heading.
    const focusLoading=el('p','Loading…','page-loading');focusLoading.setAttribute('role','status');
    this.focus.append(focusTitle,focusLoading);
    // Latest results: one tile per test, so what the records already say is visible at a glance.
    this.results=el('section',null,'home-results');this.results.setAttribute('aria-labelledby','home-results-title');this.results.hidden=true;
    const resultsHead=el('div',null,'home-results-head');const resultsTitle=el('h2','Latest results');resultsTitle.id='home-results-title';
    resultsHead.append(resultsTitle,link('All measurements','/trends','home-focus-action'));
    this.resultTiles=el('ul',null,'home-result-tiles');this.results.append(resultsHead,this.resultTiles);
    host.replaceChildren(this.status,this.focus,this.results,workspace);(document.getElementById('dashboard-tools')||host).after(supplementary);
  }

  async load() {
    const paths = {preferences:'/dashboard/preferences', summary:'/summary', facts:'/facts',
      readings:'/readings', timeline:'/timeline', healthPreferences:'/health/preferences',
      focus:'/home/focus?today='+dateKey()};
    const request = (this.request || 0) + 1; this.request = request;
    const results = await Promise.allSettled(Object.values(paths).map(path => dom().api(path)));
    if (this.destroyed || this.request !== request) return;
    Object.keys(paths).forEach((key,i) => {this.data[key] = results[i].status === 'fulfilled' ? results[i].value : null;});
    this.preferences = this.data.preferences;
    this.entries = this.data.timeline?.entries || [];
    this.measures = (this.data.readings?.measures || []).map(root.StellisTrends.dailyTotals);
    this.paint();
    this.paintFocus();
    this.paintResults();
    await root.StellisHomeCare?.mount(this.careHost);
    await this.tracker?.ready;

  }

  /** Ranked items from the person's own data; the last one is always an offer to chat. */
  paintFocus() {
    const items=this.data.focus?.items;
    this.focus.hidden=!items;
    if(!items)return;
    this.focus.classList.toggle('is-start',Boolean(this.data.focus.empty));
    if(this.data.focus.empty){this.paintStart();return;}
    const title=this.focus.querySelector('#home-focus-title');title.textContent='For you now';
    // The chat offer is the one primary action; every row comes from the person's own data.
    const list=root.StellisChatUI.ideas(items.map(item=>item.kind==='chat'?{...item,action:{...item.action,primary:true}}:item));
    this.focus.replaceChildren(title,list);
  }

  /** No records yet: Ask Stellis leads, with first steps chosen on the server from what is missing. */
  paintStart() {
    const title=this.focus.querySelector('#home-focus-title');title.textContent='How can Stellis help?';
    const intro=el('p','Ask about a symptom, a result or a visit, or start with one of these.','home-start-intro');
    const form=el('form',null,'home-start-ask');const box=el('div');
    const input=el('textarea');input.maxLength=2000;input.placeholder='Ask Stellis about your health';input.setAttribute('aria-label','Ask Stellis');
    const send=root.StellisChatUI.sendButton(el('button','Send'));send.type='submit';
    box.append(input,send);root.StellisChatUI.composer(box,input);form.append(box);
    input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();form.requestSubmit();}});
    form.addEventListener('submit',event=>{event.preventDefault();const question=input.value.trim();if(question)location.assign('/chat?ask='+encodeURIComponent(question));});
    const ideasTitle=el('h3','Ideas','home-start-ideas-title');
    // The chat offer is the composer above; the remaining focus items join the first steps.
    // A short list keeps My Health short (owner, 2026-10-27): the server orders them, the first five show.
    const items=[...(this.data.focus.ideas||[]),...this.data.focus.items.filter(item=>item.kind!=='chat')].slice(0,5);
    const list=root.StellisChatUI.ideas(items,(item,action)=>this.runIdea(item,action));
    this.focus.replaceChildren(title,intro,form,ideasTitle,list);
  }

  /** Runs a first-step idea that acts in the page rather than following a link. */
  async runIdea(item,action) {
    const command=item.action.command;
    if(command==='upload')document.querySelector('#global-add')?.click();
    else if(command==='camera')root.StellisDashTrends?.openCamera({});
    // Example records arrive a year at a time, so Stellis's organizing can be watched.
    else if(command==='example'){
      try{await root.StellisExampleWorkspace.open();}
      catch(error){this.status.textContent=error.message;}
    }
  }

  /** Accepted results from the person's records (GET /home/focus), newest per test, flagged first. */
  paintResults() {
    const tiles=this.data.focus?.results||[];
    this.results.hidden=!tiles.length;
    this.resultTiles.replaceChildren(...tiles.map(tile=>{
      const item=el('li',null,'home-result'+(tile.tone==='alert'?' is-alert':''));
      const card=link('',tile.href,'home-result-card widget-paper');
      const head=el('span',null,'home-result-head');
      const mark=el('span',null,'home-result-icon');mark.append(dom().icon(tile.tone==='alert'?'alert-circle':'activity'));
      head.append(mark,el('span',tile.name,'home-result-name'));
      const figure=el('span',null,'home-result-figure');figure.append(el('strong',tile.value),el('span',tile.unit,'home-result-unit'));
      const meta=el('span',null,'home-result-meta');
      if(tile.status)meta.append(el('span',tile.status,'home-result-status'));
      meta.append(el('span',day(tile.date)));
      card.append(head,figure);
      const line=sparkline(tile.points);if(line)card.append(line);
      card.append(meta);
      if(tile.previous)card.append(el('span',`Was ${tile.previous.value} on ${day(tile.previous.date)}`,'home-result-previous'));
      card.setAttribute('aria-label',[tile.name,tile.value,tile.unit,tile.status,day(tile.date)].filter(Boolean).join(' '));
      item.append(card);return item;
    }));
  }

  empty(body, text, label, href) {
    body.append(el('p',text,'dashboard-empty'),link(label,href,'dashboard-link'));
  }

  paint() {
    this.calendarView?.destroy();this.tracker?.destroy();
    for (const body of this.sections.values()) body.replaceChildren();
    const prefs = this.preferences || {notebooks:[],measurements:[],care_contexts:[]};
    this.overview();
    const periodsEnabled=this.data.healthPreferences?.experiences_periods===true;
    // Registry kinds gate themselves on preferences; medicine courses show for everyone.
    // Sections with nothing in them stay out of My Health (owner, 2026-10-27).
    const trackingSection=this.sections.get('reproductive').closest('section');trackingSection.hidden=true;
    this.tracker=root.StellisReproductive.mount(this.sections.get('reproductive'),
      {preferences:this.data.healthPreferences||{},onChange:()=>{trackingSection.hidden=!this.tracker?.hasRecords();this.calendarView?.reload();}});
    const tracker=this.tracker;tracker.ready.then(()=>{if(this.tracker===tracker)trackingSection.hidden=!tracker.hasRecords();}).catch(()=>{trackingSection.hidden=false;});
    const calendarHost=this.sections.get('upcoming'),calendarSection=calendarHost.closest('section');calendarSection.hidden=!periodsEnabled;
    this.calendarView=root.StellisHealthCalendar.mount(calendarHost,{onEvents:events=>{calendarSection.hidden=!periodsEnabled&&!events.length;},date:new URLSearchParams(root.location.search).get('day')||undefined,onSelectEvent:event=>{
      if(['reproductive','tracker'].includes(event.source_type)&&this.tracker)this.tracker.openRecord(event.source_id);
      else if(event.target)location.href=event.target;
      else this.eventDetail({title:event.title,date:event.start||event.start_date,status:'Scheduled',source:event.kind,meeting:event.appointment});
    }});
    const calendarHeader=calendarHost.previousElementSibling;
    calendarHeader.querySelector('[data-calendar-log]')?.remove();
    if(periodsEnabled){const log=button('Log period',()=>{trackingSection.hidden=false;this.tracker.createPeriod(this.calendarView.getSelectedDate());},'coach-button is-primary');log.dataset.calendarLog='';calendarHeader.append(log);}
    const history = this.sections.get('history');
    const past = this.entries.filter(item => item.date <= dateKey()).slice(0,5);
    if (!this.data.timeline) this.empty(history,'Health history could not load','Open records','/records');
    else if (!past.length) history.closest('section').hidden = true;
    else {
      history.closest('section').hidden = false;
      // One row per entry: the name and what was recorded, with the date at the right.
      const list = el('ul',null,'dashboard-list dashboard-history-list');
      for (const item of past) {
        const row = el('li'); const a = link(item.title,item.target);
        const detail = item.kind === 'fact' ? item.fact_kind.replaceAll('_',' ') : item.detail || item.kind;
        const words = el('span',null,'dashboard-history-words');
        words.append(a,el('span',[detail,item.source].filter(Boolean).join(', '),'dashboard-meta'));
        row.append(words,el('time',day(item.date)));
        list.append(row);
      }
      history.append(list);
    }
    this.status.replaceChildren();
    if (!this.preferences) {
      this.status.append('Dashboard choices could not load ',button('Try again',()=>this.load()));
    }
  }

  overview() {
    const body = this.sections.get('overview');
    body.replaceChildren();
    const summary = this.data.summary?.summary;
    if (summary) {
      const preview = root.StellisSummary.sections(summary.content,summary.citations).find(section=>section.heading==='Overview');
      const paragraph = el('p',null,'dashboard-summary');
      for (const part of preview?.paragraphs[0] || []) {
        if (part.citation) {
          const cite = link(String(part.citation.n),part.citation.target,'nb-cite');
          cite.setAttribute('aria-label','Source '+part.citation.n+': '+part.citation.title); paragraph.append(cite);
        } else paragraph.append(part.text);
      }
      if (paragraph.childNodes.length) body.append(paragraph,dom().accuracyNote('summaries','Written by a model'));
    }
    const facts = this.data.facts?.facts || [];
    const groups = el('div',null,'dashboard-fact-groups');
    for (const [kind,title] of [['condition','Conditions'],['medication','Medications'],['allergy','Allergies']]) {
      const items = facts.filter(fact=>fact.kind===kind).slice(0,3);
      if (!items.length) continue;
      const group = el('div'); group.append(el('h3',title));
      const list = el('ul');
      for (const fact of items) {
        const row = el('li'); row.append(link([fact.name,fact.value,fact.unit].filter(Boolean).join(' '),fact.target));
        if (fact.date) row.append(el('small',day(fact.date))); list.append(row);
      }
      group.append(list); groups.append(group);
    }
    if (groups.childElementCount) body.append(groups);
    const contexts = selected(this.books,this.preferences?.care_contexts || []);
    if (contexts.length) {
      const active = el('div',null,'dashboard-contexts'); active.append(el('h3','Active care'));
      for (const book of contexts) {
        const a = link(book.name,'/notebook/'+encodeURIComponent(book.id),'dashboard-context');
        if (book.description) a.append(el('small',book.description)); active.append(a);
      }
      body.append(active);
    }
    // Without a summary, facts or active care there is nothing to show; no placeholder box (#231).
    body.closest('section').hidden = !body.childElementCount;
  }

  bookList(body, books) {
    if (!books.length) {
      body.append(el('p','Choose notebooks for your dashboard','dashboard-empty'),button('Choose notebooks',()=>{body.closest('dialog')?.close();this.customize();})); return;
    }
    const grid = el('div',null,'dashboard-book-grid');
    for (const book of books) {
      const parent = this.collections.find(item=>item.id===book.collection_id);
      const recent = this.entries.find(item=>item.notebook_id===book.id);
      const a = notebookCard(book,{collection:parent,recent});
      grid.append(a);
    }
    body.append(grid);
  }

  measurements(measures) {
    const body = this.sections.get('measurements');
    if (!this.data.readings) {this.empty(body,'Measurements could not load','Open trends','/trends');return;}
    const head = el('div',null,'dashboard-measure-actions');
    head.append(button('Choose measurements',()=>this.customize()));
    if (root.StellisCameraPulse) head.append(root.StellisCameraPulse.logButton(()=>this.load(),this.measures,'dashboard-camera'));
    body.append(head);
    if (!measures.length) {body.append(el('p','Choose up to three measurements','dashboard-empty'));return;}
    const grid = el('div',null,'dashboard-measure-grid');
    for (const measure of measures) {
      const card = el('article',null,'dashboard-measure widget-paper');
      const point = measure.points.at(-1);
      const detail = link(measure.label,point?.notebook_id?'/notebook/'+encodeURIComponent(point.notebook_id)+'?tab=measurements&measure='+encodeURIComponent(measure.key):'/trends?measure='+encodeURIComponent(measure.key));
      card.append(detail);
      if (point) {
        card.append(el('strong',root.StellisTrendChart.formatValue(point,measure.decimals)+' '+measure.unit),el('time',day(point.time)));
        const graphic = sparkline(measure.points); if (graphic) card.append(graphic);
      } else card.append(el('p','No readings yet','dashboard-empty'));
      if(!measure.record_only)card.append(link('Log '+measure.label.toLowerCase(),'/trends?log='+encodeURIComponent(measure.key),'dashboard-link'));
      grid.append(card);
    }
    body.append(grid);
  }

  dialog(title) {
    const node = el('dialog',null,'dashboard-dialog');
    const opener = document.activeElement;
    const head = el('div',null,'dashboard-section-head');
    const heading = el('h2',title); heading.id = 'dashboard-dialog-'+(++dialogId);
    node.setAttribute('aria-labelledby',heading.id);
    const close = button('Close',()=>node.close()); head.append(heading,close); node.append(head);
    node.addEventListener('close',()=>{
      node.remove();
      const target = opener?.isConnected && opener.getClientRects().length ? opener : document.querySelector('#library-more > summary');
      target?.focus();
    },{once:true}); document.body.append(node); node.showModal();
    return node;
  }
  notebooks() {this.bookList(this.dialog('My Notebooks'),this.books);}
  eventDetail(event) {
    const dialog = this.dialog(event.title); dialog.append(el('p',day(event.date)),el('p',event.status+' — '+event.source));
    if (event.meeting?.location) dialog.append(el('p',event.meeting.location));
    if(event.meeting)for(const [label,method] of [['Notes','openNotes'],['Notebook','openNotebook'],['Record','recordMeeting']])dialog.append(button(label,()=>{dialog.close();root.StellisCalendar[method](event.meeting);}));
    dialog.append(link('Calendar settings','/settings'));
  }

  customize() {
    // A click before the preferences arrive opens the dialog once they do, instead of doing nothing.
    if (!this.preferences) {this.load().then(()=>{if(this.preferences)this.customize();});return;}
    const dialog = this.dialog('Dashboard'); const form = el('form');
    const choices = {};
    for (const [key,title,items] of [
      // Notebooks and vital signs are always on My Health (#231); only active care is chosen.
      ['care_contexts','Active care',this.books.map(book=>[book.id,book.name])],
    ]) {
      const group = el('fieldset'); group.append(el('legend',title),el('p','Choose up to three','dashboard-meta')); choices[key] = [];
      for (const [value,label] of items) {
        const box = el('input'); box.type='checkbox'; box.value=value; box.checked=this.preferences[key].includes(value);
        choices[key].push(box); const row = el('label',null,'dashboard-choice'); row.append(box,document.createTextNode(label)); group.append(row);
      }
      if (!items.length) group.append(button('Add a notebook',()=>{dialog.close();document.getElementById('new-notebook').click();}));
      form.append(group);
    }
    const status = el('p'); status.setAttribute('role','status');
    const save = dom().button('Save','invert-button'); save.type='submit'; form.append(status,save);
    form.addEventListener('submit',async event=>{
      event.preventDefault(); const values = Object.fromEntries(Object.entries(choices).map(([key,boxes])=>[key,boxes.filter(box=>box.checked).map(box=>box.value)]));
      if (Object.values(values).some(items=>items.length>3)) {status.textContent='Choose up to three items in each section';return;}
      save.disabled=true;
      try {this.preferences=await dom().api('/dashboard/preferences',{method:'PUT',json:{...this.preferences,...values}});dialog.close();this.paint();}
      catch(error) {status.textContent=error.message;save.disabled=false;}
    });
    dialog.append(form);
  }
}
let current;
async function mount(host, library) {
  if (current) {current.destroyed=true;current.calendarView?.destroy();current.tracker?.destroy();}
  // What follows the dashboard waits for its first paint, so the library below never jumps down the page as it fills.
  const main = host.closest('.home-main'); main?.classList.add('is-loading');
  current = new Dashboard(host,library);
  try { await current.load(); } finally { main?.classList.remove('is-loading'); }
}
root.addEventListener?.('stellis:health-preferences-changed',()=>{if(current&&!current.destroyed)current.load();});
root.StellisHealthDashboard = {notebookCard,mount,reload:library=>{if(current&&library){current.books=library.books;current.collections=library.collections;}return current?.load();},focusCycle:day=>current?.tracker?.openRecord('day:'+day),focusCalendar:day=>current?.calendarView?.focusDate(day),customize:()=>current?.customize(),selected};
if (typeof module !== 'undefined') module.exports = {selected};
})(typeof window !== 'undefined' ? window : globalThis);
