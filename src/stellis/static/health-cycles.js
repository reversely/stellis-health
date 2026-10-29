/** Summaries and figures for named health cycles with recorded episodes. */
(function(root) {
  'use strict';
  const calendarDates=root.StellisCalendarDates;

  function summarizeCycle(cycle,today=root.StellisDemoDate||calendarDates.formatDate(new Date())) {
    const episodes=[...cycle.episodes].filter(episode=>episode.startDate<=today).sort((first,second)=>second.startDate.localeCompare(first.startDate));
    if(!episodes.length)return {id:cycle.id,label:cycle.label,episodeCount:0};
    const latestEpisode=episodes[0];
    const intervals=episodes.slice(0,-1).flatMap((episode,index)=>episode.trackingGapBefore?[]:[calendarDates.daysBetween(episode.startDate,episodes[index+1].startDate)]);
    const averageLength=intervals.length?Math.round(intervals.reduce((sum,length)=>sum+length,0)/intervals.length):null;
    return {
      id:cycle.id,label:cycle.label,episodeCount:episodes.length,latestEpisode,
      day:calendarDates.daysBetween(today,latestEpisode.startDate)+1,
      averageLength,intervalCount:intervals.length,
      estimatedNextStart:averageLength?calendarDates.shiftDate(latestEpisode.startDate,averageLength):null,
    };
  }

  function summarizeCycles(cycles,today) {
    return cycles.map(cycle=>summarizeCycle(cycle,today));
  }

  function createDayFigure({day,label,startDate,locale}) {
    const startLabel=calendarDates.parseDate(startDate).toLocaleDateString(locale,{month:'short',day:'numeric',year:'numeric'});
    return root.StellisHealthDates.createFigure({value:day,unit:'Day',context:'of '+label,
      caption:'Started '+startLabel,ariaLabel:`Day ${day} of ${label} / Started ${startLabel}`});
  }

  const exported={summarizeCycle,summarizeCycles,createDayFigure};
  root.StellisHealthCycles=exported;
  if(typeof module!=='undefined')module.exports=exported;
})(typeof window!=='undefined'?window:globalThis);
