/** Tracked items from the registry: cycle, pill packs, devices and medicine courses. */
(function(root) {
'use strict';
const getDomHelpers = () => root.StellisDom;
const createElement = (tag, text, className) => getDomHelpers().el(tag, text, className);
const requestTracker = (path, options) => getDomHelpers().api('/reproductive' + path, options);
const calendarDates=root.StellisCalendarDates;
const formatCalendarDate=calendarDates.formatDate;
const currentDate=()=>root.StellisDemoDate||formatCalendarDate(new Date());
const daysBetween=calendarDates.daysBetween;
const shiftCalendarDate=calendarDates.shiftDate;
const formatDateLabel = date => new Date(date+'T12:00:00').toLocaleDateString([], {month:'short',day:'numeric',year:'numeric'});
const createButton = (label, onActivate, className='coach-button') => {
  const button=getDomHelpers().button(label,className);button.addEventListener('click',onActivate);return button;
};
const createIconButton = (label, icon, onActivate) => {
  const button=createButton(null,onActivate,'coach-button tracker-icon-button');
  button.setAttribute('aria-label',label);button.append(getDomHelpers().icon(icon));return button;
};
// Kinds with a dedicated view here; other registered kinds use the course view.
const BESPOKE = new Set(['period','pill','larc']);
const formatAmount = value => Number(value).toLocaleString([], {maximumFractionDigits:3});
// "Every other day" reads mid-sentence; a capital follows only names such as "Mon".
const lowerFirst = text => text ? text[0].toLowerCase()+text.slice(1) : '';
const weekdayLabel = date => new Date(date+'T12:00:00').toLocaleDateString([], {weekday:'long',month:'short',day:'numeric'});
let nextId=0;
// Retain the selected tracker while the dashboard refreshes its sections.
let activeKind=null;
const mounted=new WeakMap();

function mount(host,options={}) {
  mounted.get(host)?.();
  const instance=++nextId;
  let records=[], observations=[], referenceDay=currentDate(), current=null, busy=false;
  const trackerViews={};
  let selectedDose=new Map();
  const pendingPeriodDays=new Map();
  const tabs=new Map();
  const status=createElement('p',null,'tracker-status');status.setAttribute('role','status');
  const reminders=createElement('div',null,'tracker-reminders');reminders.setAttribute('aria-live','polite');
  const navigation=createElement('div',null,'tracker-navigation');
  const tablist=createElement('div',null,'tracker-tabs');tablist.setAttribute('role','tablist');tablist.setAttribute('aria-label','Tracking');
  const add=createButton('',()=>openForm(current));
  const panel=createElement('section',null,'tracker-panel');panel.id=`tracker-panel-${instance}`;
  panel.setAttribute('role','tabpanel');panel.tabIndex=-1;
  /** Builds one tab per registered kind whose preference gate is met. */
  function buildTabs(kinds, preferences) {
    for(const kind of kinds.filter(kind=>!kind.gate||preferences[kind.gate]===true)) {
      trackerViews[kind.key]={label:kind.plural, add:kind.add, title:kind.label, schema:kind};
      const tab=createButton(kind.plural,()=>selectTab(kind.key),'tracker-tab');
      tab.id=`tracker-tab-${instance}-${kind.key}`;
      tab.setAttribute('role','tab');tab.setAttribute('aria-controls',panel.id);
      tab.addEventListener('keydown',event=>{
        const keys=Object.keys(trackerViews), index=keys.indexOf(kind.key), last=keys.length-1;
        const target=event.key==='ArrowRight'?keys[(index+1)%keys.length]:event.key==='ArrowLeft'?keys[(index+last)%keys.length]:event.key==='Home'?keys[0]:event.key==='End'?keys[last]:null;
        if(target){event.preventDefault();selectTab(target);tabs.get(target).focus();}
      });
      tabs.set(kind.key,tab);tablist.append(tab);
    }
    tablist.hidden=tabs.size<2;
  }
  navigation.append(tablist);
  const sectionHead=host.closest('.dashboard-reproductive')?.querySelector('.dashboard-section-head');
  const logItems=createButton('Log',()=>root.StellisTrackerLog.open({onSaved:()=>{load().catch(()=>{});options.onChange?.();}}),'coach-button tracker-log-open');
  logItems.prepend(getDomHelpers().icon('checks'));
  if(sectionHead){sectionHead.lastElementChild.replaceWith(add);add.before(logItems);}else navigation.append(logItems,add);
  // How reminders behave is said beside the reminder time in the form, not under every tracker.
  host.replaceChildren(navigation,status,reminders,panel);

  function selectTab(kind) {
    current=kind;activeKind=kind;
    for(const [key,tab] of tabs) {tab.setAttribute('aria-selected',String(key===kind));tab.tabIndex=key===kind?0:-1;}
    panel.setAttribute('aria-labelledby',tabs.get(kind).id);
    add.replaceChildren(getDomHelpers().icon('plus'),document.createTextNode(trackerViews[kind].add));
    paint();
  }
  async function load() {
    const [result,daily]=await Promise.all([getDomHelpers().api('/trackers?today='+currentDate()),getDomHelpers().api('/health/observations')]);
    if(!host.isConnected)return;
    const outcomeDays=(record,status)=>Object.keys(record.outcomes).filter(day=>record.outcomes[day].status===status);
    // Kinds whose preference gate is off stay out of view and reminders.
    records=result.records.filter(record=>trackerViews[record.kind]).map(record=>({...record,doses:outcomeDays(record,'taken'),skipped:outcomeDays(record,'skipped')}));
    observations=daily.observations;paint();checkReminders();
  }
  async function mutate(operation) {
    if(busy)return false;
    busy=true;panel.setAttribute('aria-busy','true');
    try {await operation();await load();options.onChange?.();status.textContent='';return true;}
    catch(error){status.textContent=error.message;return false;}
    finally {busy=false;panel.removeAttribute('aria-busy');}
  }
  const entries = kind => records.filter(record=>record.kind===kind).sort((a,b)=>b.start.localeCompare(a.start));
  function heading(title, subtitle) {
    const block=createElement('div',null,'tracker-heading');block.append(createElement('h3',title));
    if(subtitle)block.append(createElement('p',subtitle,'tracker-muted'));return block;
  }
  function empty(title, description, kind) {
    const block=createElement('div',null,'tracker-empty');
    const symbol=createElement('div',null,'tracker-empty-icon');symbol.append(getDomHelpers().icon(trackerViews[kind].schema.icon));
    block.append(symbol,heading(title,description),createButton(trackerViews[kind].add,()=>openForm(kind),'coach-button is-primary'));
    return block;
  }
  function metric(label, value) {
    const block=createElement('div',null,'tracker-metric');block.append(createElement('dt',label),createElement('dd',value));return block;
  }
  function createPackProgress(value,label,progress) {
    const figure=createElement('div',null,'tracker-ring');
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 160 160');svg.setAttribute('aria-hidden','true');
    for(const [className,length] of [['tracker-ring-track',1],['tracker-ring-value',Math.min(1,Math.max(0,progress||0))]]) {
      const circle=document.createElementNS(svg.namespaceURI,'circle');
      for(const [key,val] of Object.entries({cx:80,cy:80,r:68,fill:'none',pathLength:1}))circle.setAttribute(key,String(val));
      circle.setAttribute('class',className);circle.setAttribute('stroke-dasharray',`${length} 1`);
      if(length===0)circle.setAttribute('visibility','hidden');
      svg.append(circle);
    }
    const text=createElement('div',null,'tracker-ring-text');
    const number=createElement('strong',String(value));
    const caption=createElement('span',label);
    text.append(number,caption);
    figure.setAttribute('role','img');
    figure.setAttribute('aria-label',`${value} ${label}`);
    figure.append(svg,text);return figure;
  }
  function paint() {
    add.hidden=current==='period'||!entries(current).length;
    add.classList.toggle('is-primary',current==='period');
    panel.replaceChildren();
    if(current==='period')paintCycle();else if(current==='pill')paintPills();else if(current==='larc')paintDevices();else paintCourses(current);
  }
  function paintDailyLog() {
    const daily=createElement('section',null,'tracker-daily');
    daily.setAttribute('aria-label','Daily log');
    const title=createElement('div',null,'tracker-daily-title');
    title.append(heading(referenceDay===currentDate()?'Today':formatDateLabel(referenceDay),
      referenceDay===currentDate()?formatDateLabel(referenceDay):null));
    if(referenceDay!==currentDate())title.append(createButton('Today',()=>{referenceDay=currentDate();paint();}));
    const dateLabel=createElement('label','Log date','tracker-log-date');
    const dateInput=createElement('input');dateInput.type='date';dateInput.value=referenceDay;dateInput.max=currentDate();
    dateInput.addEventListener('change',()=>{if(dateInput.value&&dateInput.value<=currentDate()){referenceDay=dateInput.value;paint();}});
    dateLabel.append(dateInput);title.append(dateLabel);
    daily.append(title);
    const navigation=createElement('div',null,'tracker-day-navigation');
    navigation.append(createButton('Previous week',()=>{referenceDay=shiftCalendarDate(referenceDay,-7);paint();}));
    const next=createButton('Next week',()=>{referenceDay=shiftCalendarDate(referenceDay,Math.min(7,daysBetween(currentDate(),referenceDay)));paint();});
    next.disabled=referenceDay===currentDate();navigation.append(next);daily.append(navigation);
    const dates=createElement('div',null,'tracker-day-strip');dates.setAttribute('aria-label','Log period days');
    for(let offset=-6;offset<=0;offset++) {
      const day=shiftCalendarDate(referenceDay,offset);
      const saved=observations.find(item=>item.day===day&&item.track_id==='periods');
      const value=pendingPeriodDays.has(day)?pendingPeriodDays.get(day):saved?.value;
      const hasFlow=Boolean(value&&value!=='No bleeding');
      const choice=createButton(null,async()=>{
        if(pendingPeriodDays.has(day))return;
        const nextValue=hasFlow?null:'Had flow';
        pendingPeriodDays.set(day,nextValue);paint();
        panel.querySelector(`[data-log-day="${day}"]`)?.focus();
        try {
          await getDomHelpers().api('/health/observations',{method:'PUT',json:{day,track_id:'periods',value:nextValue}});
          observations=observations.filter(item=>item.day!==day||item.track_id!=='periods');
          if(nextValue)observations.push({day,track_id:'periods',value:nextValue});
          status.textContent=`${formatDateLabel(day)} ${nextValue?'saved':'removed'}`;
          options.onChange?.();
        }catch(error){status.textContent=error.message;}
        finally {
          const focused=panel.querySelector('[data-log-day]:focus')?.dataset.logDay;
          pendingPeriodDays.delete(day);paint();
          if(focused)panel.querySelector(`[data-log-day="${focused}"]`)?.focus();
        }
      },'tracker-day-choice');
      const parsed=calendarDates.parseDate(day);
      const capsule=createElement('span',null,'tracker-day-capsule');
      capsule.append(createElement('span',null,'tracker-day-circle'));
      choice.append(createElement('span',parsed.toLocaleDateString([],{weekday:'short'})),createElement('strong',String(parsed.getDate())),capsule);
      choice.dataset.logDay=day;choice.setAttribute('aria-label',`${formatDateLabel(day)}: ${hasFlow?'Remove period':'Log period'}`);choice.setAttribute('aria-pressed',String(hasFlow));
      choice.setAttribute('aria-busy',String(pendingPeriodDays.has(day)));
      if(day===currentDate())choice.setAttribute('aria-current','date');
      dates.append(choice);
    }
    daily.append(dates);
    // The next period is the overview figure below; repeating it here gave the page three copies.
    const pending=createElement('div',null,'tracker-daily-pending');
    const logged=createElement('details',null,'tracker-daily-logged');
    const loggedSummary=createElement('summary','Logged');logged.append(loggedSummary);
    let loggedCount=0;
    async function confirm(row,operation,focusLabel) {
      if(busy)return;
      busy=true;row.setAttribute('aria-busy','true');
      for(const button of row.querySelectorAll('button'))button.disabled=true;
      try {
        await operation();
        row.classList.add('is-confirmed');
        await new Promise(resolve=>setTimeout(resolve,root.matchMedia('(prefers-reduced-motion: reduce)').matches?0:180));
        await load();options.onChange?.();
        status.textContent=focusLabel+' saved';
        const review=panel.querySelector('.tracker-daily-logged');
        review.open=true;review.querySelector('summary').focus();
      }catch(error){status.textContent=error.message;for(const button of row.querySelectorAll('button'))button.disabled=false;}
      finally{busy=false;}
    }
    function addItem(label,value,choices,saveValue) {
      const row=createElement('article',null,'tracker-daily-item');
      row.append(createElement('h4',label));
      const actions=createElement('div',null,'tracker-daily-actions');
      if(value) {
        row.append(createElement('p',value,'tracker-daily-value'));
        actions.append(createButton('Undo',()=>confirm(row,()=>saveValue(null),label),'coach-button is-quiet'));
        loggedCount++;logged.append(row);
      }else {
        for(const choice of choices)actions.append(createButton(choice,()=>confirm(row,()=>saveValue(choice),label)));
        pending.append(row);
      }
      row.append(actions);
    }
    // Flow is a detail of a period day: it appears only once the day is ticked in the strip above (#232).
    const flow=observations.find(item=>item.day===referenceDay&&item.track_id==='periods');
    if(flow&&flow.value!=='No bleeding'){
      const row=createElement('article',null,'tracker-daily-item tracker-flow');row.append(createElement('h4','Flow'));
      const group=createElement('div',null,'tracker-daily-actions');group.setAttribute('role','group');group.setAttribute('aria-label',`Flow on ${formatDateLabel(referenceDay)}`);
      for(const choice of ['Spotting','Light','Moderate','Heavy']){
        const option=createButton(choice,()=>confirm(row,()=>getDomHelpers().api('/health/observations',{method:'PUT',json:{day:referenceDay,track_id:'periods',value:choice}}),'Flow'),'coach-button tracker-flow-choice');
        option.setAttribute('aria-pressed',String(flow.value===choice));group.append(option);
      }
      row.append(group);daily.append(row);
    }
    for(const pack of entries('pill')) {
      const elapsed=daysBetween(referenceDay,pack.start);
      if(elapsed<0||elapsed%(pack.active+pack.break_days)>=pack.active)continue;
      // One tick per pill (Apple Health medications): ticked is taken; unticked is skipped by definition.
      const taken=pack.doses.includes(referenceDay),row=createElement('article',null,'tracker-daily-item');row.append(createElement('h4',pack.name));
      const tick=createButton(null,()=>confirm(row,()=>requestTracker('/'+pack.id+'/dose',{method:'PUT',json:{day:referenceDay,taken:false,status:taken?null:'taken'}}),pack.name),'log-tick');
      tick.append(getDomHelpers().icon('check'));tick.setAttribute('aria-label',pack.name);tick.setAttribute('aria-pressed',String(taken));
      const actions=createElement('div',null,'tracker-daily-actions');actions.append(tick);row.append(actions);
      if(taken){loggedCount++;logged.append(row);}else pending.append(row);
    }
    if(!pending.children.length&&loggedCount)pending.append(createElement('p','All logged for this day','tracker-muted'));
    loggedSummary.textContent=`Logged (${loggedCount})`;logged.hidden=!loggedCount;
    daily.append(pending,logged,createButton('Edit period history',()=>openForm('period'),'coach-button'));
    panel.append(daily);
  }
  function paintCycle() {
    paintDailyLog();
    const periodRecords=entries('period');
    const recordedDays=observations.filter(item=>item.track_id==='periods'&&item.value!=='No bleeding').map(item=>item.day).sort();
    const dailyEpisodes=[];
    for(const day of recordedDays) {
      if(periodRecords.some(period=>period.start<=day&&day<=(period.end||currentDate())))continue;
      const previous=dailyEpisodes.at(-1);
      // An unlogged day does not establish that a period ended.
      const ended=previous&&observations.some(item=>item.track_id==='periods'&&item.value==='No bleeding'&&item.day>previous.end&&item.day<day);
      if(previous&&!ended)previous.end=day;
      else dailyEpisodes.push({start:day,end:day});
    }
    const cycleEpisodes=[...periodRecords,...dailyEpisodes].sort((a,b)=>b.start.localeCompare(a.start));
    const overview=createElement('div',null,'tracker-overview tracker-cycle-overview tracker-cycle-summary');
    if(!cycleEpisodes.length) {
      overview.append(heading('Cycle tracking','Log flow above to start your history'));
    }else {
      const cycleSummary=root.StellisHealthCycles.summarizeCycle({id:'menstrual-cycle',label:'menstrual cycle',
        episodes:cycleEpisodes.map(period=>({id:period.id,startDate:period.start,endDate:period.end,trackingGapBefore:period.tracking_gap_before}))},currentDate());
      const latestPeriod=cycleEpisodes[0], averageCycleLength=cycleSummary.averageLength;
      const periodIsOngoing=!latestPeriod.end;
      if(root.StellisDemoDate&&cycleSummary.day>60)overview.append(heading('Period history','Only selected periods are logged in this demo'));
      else overview.append(periodFigure(latestPeriod,cycleSummary));
      const metrics=createElement('dl',null,'tracker-metrics');
      if(averageCycleLength) {
        // The figure above already gives the next period; the metrics add only the average it comes from.
        metrics.append(metric('Average cycle',`${averageCycleLength} days`));
      }else metrics.append(metric('Last period',formatDateLabel(latestPeriod.start)));
      overview.append(metrics);
      if(!averageCycleLength)overview.append(createElement('p','Predictions start after two periods','tracker-muted'));
      if(periodIsOngoing)overview.append(createButton('End period',()=>openForm('period',latestPeriod,{end:currentDate()})));
      if(averageCycleLength)overview.append(createElement('p','Estimates cannot determine fertile days','tracker-footnote'));
    }
    panel.append(overview);
    if(periodRecords.length) {
      const history=createElement('div',null,'tracker-history');history.append(createElement('h4','Period history'));
      for(const period of periodRecords) {
        const row=createButton(null,()=>openForm('period',period),'tracker-history-row');
        const text=createElement('span');text.append(createElement('strong',formatDateLabel(period.start)),createElement('span',`${period.flow} flow${period.end?' / '+(daysBetween(period.end,period.start)+1)+' days':' / Ongoing'}`,'tracker-muted'));
        if(period.note)text.append(createElement('span',period.note,'tracker-muted'));
        row.append(text,getDomHelpers().icon('chevron-right'));history.append(row);
      }
      panel.append(history);
    }
  }
  function paintPills() {
    const packs=entries('pill');
    if(!packs.length){panel.append(empty('Pill tracking','Add your prescribed pack schedule','pill'));return;}
    for(const pack of packs) {
      const total=pack.active+pack.break_days, elapsed=daysBetween(currentDate(),pack.start);
      const index=elapsed<0?0:elapsed%total, packStart=elapsed<0?pack.start:shiftCalendarDate(currentDate(),-index);
      const selected=selectedDose.get(pack.id)||currentDate();
      const chosen=selected>=packStart&&selected<shiftCalendarDate(packStart,total)?selected:currentDate();
      const chosenIndex=daysBetween(chosen,packStart), taken=new Set(pack.doses), isTaken=taken.has(chosen);
      const layout=createElement('article',null,'tracker-workspace tracker-pack');layout.dataset.record=pack.id;
      const overview=createElement('div',null,'tracker-overview');
      overview.append(heading(pack.name,`${pack.active} active / ${pack.break_days} break days`));
      overview.append(createPackProgress(elapsed<0?0:index+1,`of ${total} days`,elapsed<0?0:(index+1)/total));
      const selectedLabel=chosen===currentDate()?'Today':formatDateLabel(chosen);
      overview.append(createElement('p',elapsed<0?'Starts '+formatDateLabel(pack.start):`${selectedLabel} / ${chosenIndex>=pack.active?'Break day':isTaken?'Taken':'Not taken'}`,'tracker-dose-status'));
      if(elapsed>=0&&chosen<=currentDate()&&chosenIndex>=0&&chosenIndex<pack.active) {
        // One tick for the chosen day: ticked is taken; unticked is skipped by definition.
        const log=createButton(null,async()=>{
          log.disabled=true;
          await mutate(()=>requestTracker('/'+pack.id+'/dose',{method:'PUT',json:{day:chosen,taken:!isTaken}}));
          const target=panel.querySelector(`[data-record="${pack.id}"] .tracker-dose-action`);
          if(target)target.focus();else log.disabled=false;
        },'log-tick tracker-dose-action');
        log.append(getDomHelpers().icon('check'));log.setAttribute('aria-label',`${pack.name}, ${selectedLabel}`);log.setAttribute('aria-pressed',String(isTaken));
        const actions=createElement('div',null,'tracker-dose-actions');actions.append(log);
        overview.append(actions);
      }
      overview.append(createElement('p',pack.reminder?'Reminder '+pack.time:'Reminders off','tracker-muted'),createButton('Edit pack',()=>openForm('pill',pack),'coach-button is-quiet'));
      const visual=createElement('div',null,'tracker-pack-visual');
      const head=heading('Pill pack',formatDateLabel(packStart)+' to '+formatDateLabel(shiftCalendarDate(packStart,total-1)));
      const grid=createElement('div',null,'tracker-pill-grid');grid.setAttribute('aria-label','Pill pack days');
      for(let i=0;i<total;i++) {
        const date=shiftCalendarDate(packStart,i), breakDay=i>=pack.active, logged=taken.has(date);
        const label=`Day ${i+1} / ${formatDateLabel(date)} / ${breakDay?'Break':logged?'Taken':date>currentDate()?'Upcoming':'Not taken'}`;
        const cell=createButton(null,()=>{selectedDose.set(pack.id,date);paint();panel.querySelector(`[data-record="${pack.id}"] [data-day="${date}"]`)?.focus();},'tracker-pill');
        cell.disabled=date>currentDate()||elapsed<0;cell.dataset.day=date;cell.setAttribute('aria-label',label);cell.setAttribute('aria-pressed',String(date===chosen));
        cell.append(createElement('span',String(i+1),'tracker-pill-number'));
        if(logged)cell.append(getDomHelpers().icon('check'));
        if(breakDay)cell.classList.add('is-break');
        if(logged)cell.classList.add('is-taken');
        if(date===currentDate())cell.setAttribute('aria-current','date');
        grid.append(cell);
      }
      const legend=createElement('div',null,'tracker-legend');
      legend.append(createElement('span','Taken','tracker-key is-taken'),createElement('span','Active','tracker-key is-active'),createElement('span','Break','tracker-key is-break'));
      const history=createElement('details',null,'tracker-dose-history');const summary=createElement('summary','Dose history');summary.append(getDomHelpers().icon('chevron-down'));history.append(summary);
      // An unticked day is skipped by definition, so the history lists the doses taken.
      if(!pack.doses.length)history.append(createElement('p','No doses logged','tracker-muted'));
      else {const list=createElement('ul');for(const dose of [...pack.doses].sort().reverse())list.append(createElement('li',formatDateLabel(dose)));history.append(list);}
      visual.append(head,grid,legend,history);layout.append(overview,visual);panel.append(layout);
    }
  }
  function paintDevices() {
    const devices=entries('larc');
    if(!devices.length){panel.append(empty('Long-acting contraception','Track an IUD or implant renewal','larc'));return;}
    for(const [title,items] of [['Active devices',devices.filter(record=>!record.end)],['Device history',devices.filter(record=>record.end)]]) {
      if(!items.length)continue;
      const section=createElement('div',null,'tracker-device-group');section.append(createElement('h3',title));
      for(const device of items) {
        const row=createElement('article',null,'tracker-device');
        const info=heading(device.name,device.type);
        const remaining=daysBetween(device.renewal,currentDate());
        const dates=createElement('dl',null,'tracker-device-dates');
        dates.append(metric('Inserted',formatDateLabel(device.start)),metric(device.end?'Removed':'Renewal date',formatDateLabel(device.end||device.renewal)));
        const due=createElement('div',null,'tracker-device-due');
        if(!device.end) {due.append(createElement('strong',remaining<0?'Renewal date passed':remaining===0?'Due today':remaining+' days'),createElement('span',remaining>0?'until renewal':'Review your renewal date','tracker-muted'));}
        else due.append(createElement('span','Removed','tracker-muted'));
        const edit=createButton('Edit device',()=>openForm('larc',device));
        row.append(info,dates,due,edit);section.append(row);
      }
      panel.append(section);
    }
  }

  /** Today's dose state in words, from the server summary. */
  /** "Period day N" during a period; otherwise the next predicted period (#232; never "Day N of cycle"). */
  function periodFigure(latest,summary) {
    const today=currentDate(),day=daysBetween(today,latest.start)+1;
    const inPeriod=latest.start<=today&&(latest.end?today<=latest.end:day<=7);
    if(inPeriod)return root.StellisHealthDates.createFigure({value:day,unit:'Period day',context:'',caption:'Started '+formatDateLabel(latest.start),ariaLabel:`Period day ${day}, started ${formatDateLabel(latest.start)}`});
    if(summary.estimatedNextStart&&summary.estimatedNextStart>today){
      const until=daysBetween(summary.estimatedNextStart,today);
      return root.StellisHealthDates.createFigure({value:until,unit:'Next period in',context:until===1?'day':'days',caption:'Around '+formatDateLabel(summary.estimatedNextStart),ariaLabel:`Next period in ${until} ${until===1?'day':'days'} · around ${formatDateLabel(summary.estimatedNextStart)}`});
    }
    return heading('Last period','Started '+formatDateLabel(latest.start));
  }
  function courseState(record) {
    const summary=record.summary, today=currentDate();
    if(record.start>today)return 'Starts '+formatDateLabel(record.start);
    if(record.end&&record.end<today)return 'Ended '+formatDateLabel(record.end);
    if(summary.today_status==='taken')return 'Taken today';
    if(summary.expected_today)return 'Due today';
    if(summary.loggable_today)return 'Take as needed';
    return 'Not a dose day'+(summary.next_expected?'. Next dose '+weekdayLabel(summary.next_expected):'');
  }
  /** A start-to-end bar with calculated and planned markers and today's position. */
  function courseTrack(record) {
    const today=currentDate(), dates=record.summary.derived.map(item=>item.date);
    const last=[record.end,today,...dates].filter(Boolean).sort().at(-1);
    const span=Math.max(1,daysBetween(last,record.start));
    const position=date=>`${Math.min(100,Math.max(0,daysBetween(date,record.start)/span*100))}%`;
    const track=createElement('div',null,'tracker-course-track');
    const bar=createElement('div',null,'tracker-course-bar');bar.setAttribute('aria-hidden','true');
    const done=createElement('span',null,'tracker-course-done');done.style.width=position(today);bar.append(done);
    for(const item of record.summary.derived) {
      const marker=createElement('span',null,`tracker-course-marker is-${item.status}`);marker.style.left=position(item.date);marker.title=item.title;bar.append(marker);
    }
    const now=createElement('span',null,'tracker-course-now');now.style.left=position(today);bar.append(now);
    const labels=createElement('div',null,'tracker-course-labels');
    labels.append(createElement('span','Started '+formatDateLabel(record.start)),
      createElement('span',record.end?'Ends '+formatDateLabel(record.end):'Ongoing'));
    track.append(bar,labels);return track;
  }
  /** A dated pregnancy: weeks and days, trimester, due date and any booked birth. */
  function pregnancyCard(kind,record) {
    const p=record.summary.pregnancy, card=createElement('article',null,'tracker-course');card.dataset.record=record.id;
    const head=createElement('div',null,'tracker-course-head');
    head.append(heading(`${p.weeks} weeks, ${p.days} day${p.days===1?'':'s'}`,p.trimester),createButton('Edit',()=>openForm(kind,record),'coach-button is-quiet'));
    const metrics=createElement('dl',null,'tracker-metrics');
    metrics.append(metric('Due date',formatDateLabel(p.due)));
    if(p.booked_birth)metrics.append(metric('Booked birth',formatDateLabel(p.booked_birth)));
    card.append(head,metrics,createElement('p',p.basis_text,'tracker-muted'));
    return card;
  }
  function paintCourses(kind) {
    const view=trackerViews[kind], items=entries(kind);
    if(!items.length){panel.append(empty(view.schema.plural,view.schema.description,kind));return;}
    for(const record of items) {
      if(record.summary.pregnancy){panel.append(pregnancyCard(kind,record));continue;}
      if(record.kind==='treatment'){const card=createElement('article',null,'tracker-course');card.append(heading(record.name,record.phase),createElement('p','Started '+formatDateLabel(record.start)),createButton('Edit',()=>openForm(kind,record),'coach-button is-quiet'));panel.append(card);continue;}
      const summary=record.summary, card=createElement('article',null,'tracker-course');card.dataset.record=record.id;
      const head=createElement('div',null,'tracker-course-head');
      const dose=record.dose_amount!=null?`${formatAmount(record.dose_amount)} ${record.dose_unit}`:'';
      head.append(heading(record.name,[dose,lowerFirst(summary.schedule_text)].filter(Boolean).join(', ')),
        createButton('Edit',()=>openForm(kind,record),'coach-button is-quiet'));
      const today=createElement('div',null,'tracker-course-today');
      today.append(createElement('p',courseState(record),'tracker-dose-status'));
      if(summary.loggable_today) {
        const actions=createElement('div',null,'tracker-dose-actions');
        const save=async state=>{
          if(await mutate(()=>getDomHelpers().api(`/trackers/records/${encodeURIComponent(record.id)}/outcomes/${currentDate()}`,{method:'PUT',json:{status:state}})))
            panel.querySelector(`[data-record="${record.id}"] .tracker-dose-action`)?.focus();
        };
        // One tick for today: ticked is taken; unticked is skipped by definition.
        const on=summary.today_status==='taken',tick=createButton(null,()=>save(on?null:'taken'),'log-tick tracker-dose-action');
        tick.append(getDomHelpers().icon('check'));tick.setAttribute('aria-label',`${record.name||'Dose'}, today`);tick.setAttribute('aria-pressed',String(on));actions.append(tick);
        today.append(actions);
      }
      const metrics=createElement('dl',null,'tracker-metrics');
      metrics.append(metric('Doses logged',String(summary.taken_count)));
      const elapsed=daysBetween(record.end&&record.end<currentDate()?record.end:currentDate(),record.start)+1;
      if(elapsed>0)metrics.append(metric('Duration',elapsed<14?`${elapsed} day${elapsed===1?'':'s'}`:`${Math.floor(elapsed/7)} weeks`));
      card.append(head,today,metrics);
      const takenDays=Object.entries(record.outcomes).filter(([,outcome])=>outcome.status==='taken').sort().reverse();
      // Unticked days are skipped by definition, so the history lists the doses taken.
      if(takenDays.length){const history=createElement('details',null,'tracker-dose-history');history.append(createElement('summary','Dose history'));for(const [day,outcome] of takenDays)history.append(createElement('p',`${formatDateLabel(day)}${outcome.dose_amount?', '+outcome.dose_amount+' '+outcome.dose_unit:''}`));card.append(history);}
      if(record.regimes?.length){const versions=createElement('details',null,'tracker-dose-history');versions.append(createElement('summary','Course changes'));for(const v of record.regimes){versions.append(createElement('p',`${formatDateLabel(v.effective_date)}, ${v.data.dose_amount} ${v.data.dose_unit}`),createElement('blockquote',v.source.quote));if(v.source.document_id){const link=createElement('a','Original instruction','chat-source-link');link.href='/document/'+v.source.document_id+'?page='+(v.source.page||1);versions.append(link);}}card.append(versions);}
      // A track needs an end point: an end date or a calculated date.
      if(record.end||summary.derived.length)card.append(courseTrack(record));
      const upcoming=summary.derived.filter(item=>item.date>=currentDate());
      if(upcoming.length) {
        const list=createElement('ul',null,'tracker-course-upcoming');list.setAttribute('aria-label','Coming up');
        for(const item of upcoming) {
          const row=createElement('li');
          row.append(createElement('time',formatDateLabel(item.date)),createElement('span',item.title),
            createElement('span',item.status[0].toUpperCase()+item.status.slice(1),`status-block is-${item.status}`));
          list.append(row);
        }
        card.append(list);
      }
      if(record.source_document_id) {
        const source=createElement('a',`From ${record.source_title}${record.source_page?', page '+record.source_page:''}`,'tracker-course-source');
        source.href=`/document/${encodeURIComponent(record.source_document_id)}${record.source_page?'?page='+record.source_page:''}`;
        card.append(source);
      }
      panel.append(card);
    }
  }

  function openForm(kind, existing=null, overrides={}) {
    if(!BESPOKE.has(kind)) {
      return root.StellisTrackerForm.open(trackerViews[kind].schema,existing,{host,
        onSaved:async()=>{await load();options.onChange?.();status.textContent='';},
        onDelete:async()=>{await getDomHelpers().api('/trackers/records/'+encodeURIComponent(existing.id),{method:'DELETE'});await load();options.onChange?.();}});
    }
    const trigger=document.activeElement;
    const dialog=createElement('dialog',null,'tracker-dialog');
    const title=createElement('h2',(existing?'Edit ':'Add ')+trackerViews[kind].title.toLowerCase());title.id=`tracker-dialog-${++nextId}`;
    dialog.setAttribute('aria-labelledby',title.id);
    const header=createElement('div',null,'tracker-dialog-head');header.append(title,createIconButton('Close','x',()=>dialog.close()));
    const form=createElement('form',null,'tracker-editor');
    const fields=createElement('div',null,'tracker-fields');
    const controls={};
    const defaults={flow:'Moderate',type:'Hormonal IUD',start:currentDate(),active:21,break_days:7,time:'09:00',notice:30,...existing,...overrides};
    function field(key,label,type='text',options=null,wide=false) {
      const wrapper=createElement('label',label,'tracker-field'+(wide?' is-wide':''));
      const input=createElement(options?'select':key==='note'?'textarea':'input');
      input.id=`tracker-input-${++nextId}`;wrapper.htmlFor=input.id;wrapper.dataset.field=key;input.name=key;
      input.setAttribute('aria-label',label);
      if(options)for(const option of options){const node=createElement('option',option);node.value=option;input.append(node);}
      else if(key!=='note')input.type=type;
      if(type==='checkbox'){input.checked=!!defaults[key];wrapper.classList.add('is-checkbox');}
      else input.value=defaults[key]??'';
      if(type==='number'){input.min=key==='active'?'1':'0';input.max='365';}
      if(type==='text'&&!options)input.maxLength=key==='note'?500:120;
      if(type==='date'&&(key==='end'||(key==='start'&&kind!=='pill')))input.max=currentDate();
      input.required=!['end','note','reminder','tracking_gap_before'].includes(key);
      if(key==='note')input.rows=3;
      wrapper.append(input);fields.append(wrapper);controls[key]=input;return input;
    }
    if(kind!=='period')field('name',kind==='pill'?'Pill name':'Device name','text',null,true);
    field('start',kind==='period'?'Period start':kind==='pill'?'Pack start':'Insertion date','date');
    if(kind==='period') {
      field('end','Period end','date');
      field('tracking_gap_before','Tracking resumed after a gap','checkbox',null,true);
      const group=createElement('fieldset',null,'tracker-choices is-wide');group.append(createElement('legend','Flow'));
      const choiceButtons=[];
      controls.flow={value:defaults.flow,type:'text'};
      for(const flow of ['Spotting','Light','Moderate','Heavy']) {
        const choice=createButton(flow,()=>{controls.flow.value=flow;for(const button of choiceButtons)button.setAttribute('aria-pressed',String(button===choice));},'coach-button tracker-choice');
        choice.setAttribute('aria-pressed',String(flow===defaults.flow));choiceButtons.push(choice);group.append(choice);
      }
      fields.append(group);field('note','Symptoms and notes','text',null,true);
    }
    if(kind==='pill') {
      const presets=createElement('fieldset',null,'tracker-choices is-wide');presets.append(createElement('legend','Pack schedule'));
      const buttons=[];
      for(const [label,active,breakDays] of [['21 + 7',21,7],['24 + 4',24,4],['Continuous',28,0]]) {
        const choice=createButton(label,()=>{controls.active.value=active;controls.break_days.value=breakDays;refreshPresets();},'coach-button tracker-choice');
        choice.setAttribute('aria-label',label==='Continuous'?'Continuous schedule':`${active} active and ${breakDays} break days`);
        presets.append(choice);buttons.push({choice,active,breakDays});
      }
      fields.append(presets);
      field('active','Active pills','number');field('break_days','Break days','number');
      function refreshPresets(){for(const {choice,active,breakDays} of buttons)choice.setAttribute('aria-pressed',String(Number(controls.active.value)===active&&Number(controls.break_days.value)===breakDays));}
      controls.active.oninput=refreshPresets;controls.break_days.oninput=refreshPresets;refreshPresets();
      field('time','Reminder time','time');
    }
    if(kind==='larc') {
      field('type','Device type','text',['Hormonal IUD','Copper IUD','Implant']);
      field('renewal','Renewal date','date');field('notice','Reminder notice in days','number');
      if(existing)field('end','Removal date','date');
      fields.append(createElement('p','Use the renewal date agreed with your clinician','tracker-muted is-wide'));
    }
    if(kind!=='period') {
      field('reminder','In-app reminders','checkbox',null,true);
      fields.append(createElement('p','Reminders appear while My Health is open','tracker-footnote is-wide'));
    }
    const error=createElement('p',null,'tracker-form-error');error.setAttribute('role','alert');
    const footer=createElement('div',null,'tracker-dialog-actions');
    const save=createElement('button','Save','coach-button is-primary');save.type='submit';
    const cancel=createButton('Cancel',()=>dialog.close());
    if(existing) {
      const remove=createButton('Delete',async()=>{
        if(!root.confirm('Delete this tracker entry?'))return;
        remove.disabled=true;
        if(await mutate(()=>requestTracker('/'+existing.id,{method:'DELETE'})))dialog.close();
        else {error.textContent=status.textContent;remove.disabled=false;}
      },'coach-button is-danger');footer.append(remove);
    }
    footer.append(cancel,save);form.append(fields,error,footer);dialog.append(header,form);document.body.append(dialog);
    dialog.addEventListener('close',()=>{dialog.remove();if(trigger?.isConnected)trigger.focus();else tabs.get(current).focus();},{once:true});
    form.onsubmit=async event=>{
      event.preventDefault();save.disabled=true;error.textContent='';
      const data={...existing};for(const key of ['id','kind','doses','skipped','outcomes','summary','created_at','source_title'])delete data[key];
      for(const [key,input] of Object.entries(controls))data[key]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;
      try {
        await requestTracker('/'+kind+(existing?'?record_id='+encodeURIComponent(existing.id):''),{method:'POST',json:data});
        await load();options.onChange?.();dialog.close();status.textContent='';
      }catch(err){error.textContent=err.message;save.disabled=false;}
    };
    dialog.showModal();
  }
  function checkReminders() {
    if(!host.isConnected)return;
    const time=new Date().toTimeString().slice(0,5);
    const due=records.filter(record=>record.reminder&&!record.end&&(record.kind==='pill'
      ?daysBetween(currentDate(),record.start)>=0&&daysBetween(currentDate(),record.start)%(record.active+record.break_days)<record.active&&!record.doses.includes(currentDate())&&!(record.skipped||[]).includes(currentDate())&&time>=record.time
      :record.kind==='larc'?daysBetween(record.renewal,currentDate())<=record.notice
      :Boolean(record.summary?.expected_today&&!record.summary.today_status&&(!record.time||time>=record.time))));
    reminders.replaceChildren();
    for(const record of due) {
      const banner=createElement('div',null,'tracker-reminder');
      const text=createElement('span',record.name+(record.kind==='larc'?' renewal is due':' is due'));
      const view=createButton('View',()=>{selectTab(record.kind);panel.focus();},'coach-button');banner.append(getDomHelpers().icon('clock'),text,view);reminders.append(banner);
    }
  }
  status.textContent='Loading tracking records';
  const ready=(async()=>{
    const [kinds,preferences]=await Promise.all([getDomHelpers().api('/trackers/kinds'),
      options.preferences?Promise.resolve(options.preferences):getDomHelpers().api('/health/preferences')]);
    if(!host.isConnected)return;
    buildTabs(kinds.kinds,preferences);
    selectTab(trackerViews[activeKind]?activeKind:Object.keys(trackerViews)[0]);
    await load();
    // Open on a tab with records: an empty Treatment phases tab must not read as "nothing tracked" beside four medicines.
    // Periods always holds the daily log, so it is never "empty".
    if(current!=='period'&&!entries(current).length){const first=Object.keys(trackerViews).find(kind=>entries(kind).length);if(first)selectTab(first);}
  })();
  ready.then(()=>{status.textContent='';}).catch(error=>status.replaceChildren(createElement('span',error.message),createButton('Try again',()=>load().then(()=>{status.textContent='';}).catch(err=>{status.textContent=err.message;}))));
  let day=currentDate();
  const timer=setInterval(()=>{
    if(!host.isConnected){clearInterval(timer);return;}
    if(day!==currentDate()){day=currentDate();paint();}checkReminders();
  },30000);
  mounted.set(host,()=>clearInterval(timer));
  return {
    ready,
    /** Whether anything is tracked yet; My Health leaves the section out until then. */
    hasRecords:()=>records.length>0||observations.length>0,
    createPeriod:async()=>{await ready;if(!trackerViews.period)return;referenceDay=currentDate();selectTab('period');panel.querySelector('.tracker-daily')?.scrollIntoView({block:'center'});panel.querySelector('.tracker-daily button[aria-pressed="true"]')?.focus();},
    openRecord:async recordId=>{await ready;if(recordId.startsWith('day:')){referenceDay=recordId.slice(4);await load();selectTab('period');panel.querySelector('.tracker-daily')?.scrollIntoView({block:'center'});return;}await load();const record=records.find(record=>record.id===recordId);if(record&&trackerViews[record.kind]){selectTab(record.kind);openForm(record.kind,record);}},
    destroy:()=>{clearInterval(timer);host.replaceChildren();},
  };
}
root.StellisReproductive={mount};
})(window);
