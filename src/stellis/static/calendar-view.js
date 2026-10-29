/** Shared month view for dated records, overlapping health cycles and appointments. */
(function(root) {
  'use strict';
  const dates = root.StellisCalendarDates;
  const element = (tag,text,className) => root.StellisDom.el(tag,text,className);

  function createDateHeader({dates:headerDates,className,cellClassName,getLabel,getDetail,leadingCell=false,gridHeader=false}) {
    const header=element('div',null,className);if(gridHeader)header.setAttribute('role','row');
    if(leadingCell)header.append(element('span'));
    for(const date of headerDates) {
      const cell=element('div',null,cellClassName);if(gridHeader)cell.setAttribute('role','columnheader');
      if(getDetail)cell.append(element('strong',getLabel(date)),element('span',getDetail(date)));
      else cell.textContent=getLabel(date);
      header.append(cell);
    }
    return header;
  }

  /**
   * Render date intervals without knowing what a health cycle or appointment means.
   * Events supply id, startDate, endDate, trackId and label. Tracks supply id, label
   * and a semantic colour. Selection receives the date and every intersecting event.
   */
  function createMonth(options={}) {
    let calendarEvents = options.events || [];
    let displayedMonth = options.month || dates.formatDate(new Date()).slice(0,7);
    let focusedDate = options.selectedDate || dates.formatDate(new Date());
    const locale = options.locale;
    const firstWeekday = options.firstWeekday ?? 1;
    const tracks = new Map((options.tracks || []).map(track=>[track.id,track]));
    const view = element('section',null,'calendar-month');
    view.setAttribute('aria-label',options.label || 'Calendar');
    const header = element('div',null,'calendar-month-header');
    const title = element('h4');
    const grid = element('div',null,'calendar-month-grid');
    grid.setAttribute('role','grid');
    const today = options.today || root.StellisDemoDate || dates.formatDate(new Date());
    const dateLabel = value => dates.parseDate(value).toLocaleDateString(locale,{weekday:'long',year:'numeric',month:'long',day:'numeric'});

    function navigationButton(label,icon,offset) {
      const button = root.StellisDom.button(null,'coach-button calendar-month-navigation');
      button.setAttribute('aria-label',label);button.append(root.StellisDom.icon(icon));
      button.addEventListener('click',()=>changeMonth(offset));return button;
    }
    function changeMonth(offset, moveFocus=false) {
      const firstDate = dates.parseDate(displayedMonth+'-01');
      firstDate.setMonth(firstDate.getMonth()+offset);
      displayedMonth = dates.formatDate(firstDate).slice(0,7);
      focusedDate = displayedMonth+'-01';
      render(moveFocus);options.onMonthChange?.(displayedMonth,{focusedDate});
    }
    function focusDate(value) {
      focusedDate = value;
      const nextMonth = value.slice(0,7);
      if (nextMonth !== displayedMonth) {displayedMonth=nextMonth;options.onMonthChange?.(displayedMonth,{focusedDate});}
      render(true);
    }
    function onDateKey(event,date) {
      const offsets={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7};
      if (Object.hasOwn(offsets,event.key)) {
        event.preventDefault();focusDate(dates.shiftDate(date,offsets[event.key]));
      } else if (event.key==='PageUp' || event.key==='PageDown') {
        event.preventDefault();changeMonth(event.key==='PageUp'?-1:1,true);
      } else if (event.key==='Home' || event.key==='End') {
        event.preventDefault();
        const weekdayOffset=(dates.parseDate(date).getDay()-firstWeekday+7)%7;
        focusDate(dates.shiftDate(date,event.key==='Home'?-weekdayOffset:6-weekdayOffset));
      }
    }
    function render(moveFocus=false) {
      const monthDays = dates.monthDates(displayedMonth,firstWeekday);
      const eventsByDate = dates.indexEvents(calendarEvents,monthDays[0],monthDays.at(-1));
      const monthLabel=dates.parseDate(displayedMonth+'-01').toLocaleDateString(locale,{month:'long',year:'numeric'});
      title.textContent=monthLabel;grid.setAttribute('aria-label',monthLabel);grid.replaceChildren();
      if(!monthDays.includes(focusedDate))focusedDate=displayedMonth+'-01';
      const weekdays=createDateHeader({dates:monthDays.slice(0,7),className:'calendar-month-week',cellClassName:'calendar-month-weekday',gridHeader:true,
        getLabel:date=>dates.parseDate(date).toLocaleDateString(locale,{weekday:'short'})});
      grid.append(weekdays);
      for(let weekIndex=0;weekIndex<6;weekIndex++) {
        const week=element('div',null,'calendar-month-week');week.setAttribute('role','row');
        for(const date of monthDays.slice(weekIndex*7,weekIndex*7+7)) {
          const events=eventsByDate.get(date)||[];
          const cell=element('div',null,'calendar-month-cell');cell.setAttribute('role','gridcell');cell.setAttribute('aria-selected',String(date===focusedDate));
          const button=root.StellisDom.button(null,'calendar-month-day');
          button.dataset.date=date;button.tabIndex=date===focusedDate?0:-1;
          button.setAttribute('aria-label',[dateLabel(date),...events.map(event=>event.label)].join(' / '));
          button.classList.toggle('is-outside-month',date.slice(0,7)!==displayedMonth);
          button.classList.toggle('is-future',date>today);
          if(date===today)button.setAttribute('aria-current','date');
          button.setAttribute('aria-disabled',String(options.isDateEnabled ? !options.isDateEnabled(date,events) : !options.onSelectDate));
          button.append(element('span',String(dates.parseDate(date).getDate()),'calendar-month-number'));
          if(events.length) {
            const track=tracks.get(events[0].trackId);
            button.classList.add('has-events');
            button.style.setProperty('--calendar-event-color',events[0].color || track?.color || 'var(--ink)');
            const markers=element('span',null,'calendar-month-markers');markers.setAttribute('aria-hidden','true');
            for(const event of events) {
              const marker=element('span');marker.style.background=event.color || tracks.get(event.trackId)?.color || 'var(--ink)';markers.append(marker);
            }
            button.append(markers);
          }
          button.addEventListener('click',()=>{
            if(button.getAttribute('aria-disabled')==='true')return;
            focusedDate=date;
            for(const dayButton of grid.querySelectorAll('[data-date]')) {
              const selected=dayButton.dataset.date===date;dayButton.tabIndex=selected?0:-1;
              dayButton.parentElement.setAttribute('aria-selected',String(selected));
            }
            options.onSelectDate?.({date,events});
          });
          button.addEventListener('keydown',event=>onDateKey(event,date));cell.append(button);week.append(cell);
        }
        grid.append(week);
      }
      if(moveFocus)view.querySelector(`[data-date="${focusedDate}"]`)?.focus();
    }
    header.append(title,navigationButton('Previous month','chevron-left',-1),navigationButton('Next month','chevron-right',1));
    view.append(header,grid);
    if(tracks.size) {
      const legend=element('div',null,'calendar-month-legend');
      for(const track of tracks.values()) {
        const entry=element('span',null,'calendar-month-key');
        const swatch=element('span');swatch.style.background=track.color || 'var(--ink)';swatch.setAttribute('aria-hidden','true');
        entry.append(swatch,document.createTextNode(track.label));legend.append(entry);
      }
      view.append(legend);
    }
    render();
    return {element:view,updateEvents(events){calendarEvents=events;render(view.contains(document.activeElement));},getMonth:()=>displayedMonth};
  }
  root.StellisCalendarView={createMonth,createDateHeader};
})(window);
