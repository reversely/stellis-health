/** Shared calendar behavior across cycles, timezones and overlapping event types. */
const assert=require('node:assert/strict');
const dates=require('../src/stellis/shared/calendar-dates.js');
const cycles=require('../src/stellis/static/health-cycles.js');
assert.equal(dates.shiftDate('2024-02-28',1),'2024-02-29');
assert.equal(dates.shiftDate('2024-02-29',1),'2024-03-01');
assert.equal(dates.shiftDate('2026-12-31',1),'2027-01-01');
assert.equal(dates.daysBetween('2026-03-09','2026-03-07'),2);
assert.equal(dates.daysBetween('2026-11-02','2026-10-31'),2);
assert.throws(()=>dates.parseDate('2026-02-30'),RangeError);
assert.equal(dates.monthDates('2026-02',1)[0],'2026-01-26');
assert.equal(dates.monthDates('2026-02',0)[0],'2026-02-01');
assert.equal(dates.monthDates('2026-02').length,42);
const events=[
 {id:'first',trackId:'cycle-one',startDate:'2026-02-01',endDate:'2026-02-03'},
 {id:'second',trackId:'cycle-two',startDate:'2026-02-02',endDate:'2026-02-04'},
 {id:'appointment',trackId:'visits',startDate:'2026-02-02'},
 {id:'outside',trackId:'cycle-one',startDate:'2027-01-01',endDate:'2027-01-04'},
];
const indexed=dates.indexEvents(events,'2026-02-01','2026-02-28');
assert.deepEqual(indexed.get('2026-02-02').map(event=>event.id),['first','second','appointment']);
assert.equal(indexed.size,4);
const summaries=cycles.summarizeCycles([
 {id:'cycle-one',label:'Treatment cycle',episodes:[{startDate:'2026-01-01',endDate:'2026-01-05'},{startDate:'2026-01-22'}]},
 {id:'cycle-two',label:'Recovery cycle',episodes:[{startDate:'2026-01-20'}]},
 {id:'empty',label:'Another cycle',episodes:[]},
],'2026-01-25');
assert.equal(summaries[0].day,4);
assert.equal(summaries[0].averageLength,21);
assert.equal(summaries[0].estimatedNextStart,'2026-02-12');
assert.equal(summaries[1].day,6);
assert.equal(summaries[1].estimatedNextStart,null);
assert.equal(summaries[2].episodeCount,0);
console.log('Shared dates, multiple cycles, overlapping events and independent summaries passed');
// A break in tracking is not evidence of an unusually long menstrual cycle.
const resumed=cycles.summarizeCycle({id:'periods',label:'Periods',episodes:[
 {startDate:'2023-06-12',endDate:'2023-06-16'},
 {startDate:'2026-01-12',endDate:'2026-01-16',trackingGapBefore:true},
 {startDate:'2026-02-10',endDate:'2026-02-14'},
]},'2026-02-12');
assert.equal(resumed.averageLength,29);
assert.equal(resumed.intervalCount,1);
assert.equal(resumed.day,3);
assert.equal(resumed.estimatedNextStart,'2026-03-11');
// Reopening a historical dose must retain its original local clock across UTC/day/DST boundaries.
assert.equal(dates.localClock('2024-06-12T01:30:00+00:00','America/Toronto'),'21:30');
assert.equal(dates.localClock('2024-01-12T02:30:00+00:00','America/Toronto'),'21:30');
assert.equal(dates.localClock('2024-06-12T04:05:00+00:00','America/Toronto'),'00:05');
assert.equal(dates.localClock('', 'America/Toronto'),'');
