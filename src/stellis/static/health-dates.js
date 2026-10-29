/** Named calendar anchors use calendar days rather than elapsed 24-hour blocks. */
(function(root) {
  'use strict';
  const dates=root.StellisCalendarDates;
  function summarizeMilestone(milestone,today=root.StellisDemoDate||dates.formatDate(new Date())) {
    const anchor=milestone.actual_date||milestone.target_date;
    const difference=dates.daysBetween(anchor,today);
    const confirmed=Boolean(milestone.actual_date);
    let context;
    if(confirmed)context='since '+milestone.title;
    else if(difference<0)context='past '+(milestone.date_kind==='estimated'?'estimate':'scheduled date');
    else context='until '+milestone.title;
    return {value:Math.abs(difference),unit:Math.abs(difference)===1?'day':'days',
      context,anchor,confirmed,today:difference===0,
      dateLabel:confirmed?'Confirmed':milestone.date_kind==='estimated'?'Estimated':'Scheduled'};
  }
  function createFigure({value,unit,context,caption,ariaLabel}) {
    const el=root.StellisDom.el;
    const figure=el('figure',null,'health-date-figure');
    figure.setAttribute('role','img');
    figure.setAttribute('aria-label',ariaLabel||`${value} ${unit} ${context} / ${caption}`);
    figure.append(el('span',unit,'health-date-label'),el('strong',String(value),'health-date-number'),
      el('span',context,'health-date-context'),el('figcaption',caption));
    return figure;
  }
  const exported={summarizeMilestone,createFigure};
  root.StellisHealthDates=exported;
  if(typeof module!=='undefined')module.exports=exported;
})(typeof window!=='undefined'?window:globalThis);
