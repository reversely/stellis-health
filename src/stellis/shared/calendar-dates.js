/** Calendar-date arithmetic shared by health tracking and appointment views. */
(function(root) {
  'use strict';
  const MILLISECONDS_PER_DAY = 86400000;

  function formatDate(date) {
    return [date.getFullYear(), String(date.getMonth()+1).padStart(2,'0'),
      String(date.getDate()).padStart(2,'0')].join('-');
  }

  function localClock(value, timeZone) {
    if (!value) return '';
    return new Intl.DateTimeFormat('en-GB', {hour:'2-digit', minute:'2-digit', hourCycle:'h23', ...(timeZone?{timeZone}:{})}).format(new Date(value));
  }

  function parseDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) throw new RangeError('Calendar dates use YYYY-MM-DD');
    const parsed = new Date(Number(match[1]), Number(match[2])-1, Number(match[3]));
    if (formatDate(parsed) !== value) throw new RangeError('Calendar date does not exist');
    return parsed;
  }

  function addDays(date, count) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate()+count);
  }

  function shiftDate(value, count) {
    return formatDate(addDays(parseDate(value), count));
  }

  function dayNumber(value) {
    const date = typeof value === 'string' ? parseDate(value) : value;
    return Date.UTC(date.getFullYear(),date.getMonth(),date.getDate()) / MILLISECONDS_PER_DAY;
  }

  function daysBetween(later, earlier) {
    return dayNumber(later) - dayNumber(earlier);
  }

  function monthDates(month, firstWeekday=1) {
    const firstDate = parseDate(month+'-01');
    const leadingDays = (firstDate.getDay()-firstWeekday+7)%7;
    const gridStart = addDays(firstDate,-leadingDays);
    return Array.from({length:42},(_,index)=>formatDate(addDays(gridStart,index)));
  }

  /** Index intersecting intervals once, retaining every event on an overlapping day. */
  function indexEvents(events, firstDate, lastDate) {
    const byDate = new Map();
    for (const event of events) {
      const intervalStart = event.startDate > firstDate ? event.startDate : firstDate;
      const intervalEnd = (event.endDate || event.startDate) < lastDate ? (event.endDate || event.startDate) : lastDate;
      for (let date=intervalStart; date<=intervalEnd; date=shiftDate(date,1)) {
        if (!byDate.has(date)) byDate.set(date,[]);
        byDate.get(date).push(event);
      }
    }
    return byDate;
  }

  const exported = {formatDate,localClock,parseDate,addDays,shiftDate,dayNumber,daysBetween,monthDates,indexEvents};
  root.StellisCalendarDates = exported;
  if (typeof module !== 'undefined') module.exports = exported;
})(typeof window !== 'undefined' ? window : globalThis);
