// Copy that Home's health overview and goals show.
const test = require('node:test');
const assert = require('node:assert/strict');
const goals = require('../src/stellis/static/goals.js');
const trends = require('../src/stellis/static/trends.js');

const TO_GOAL = 'to reach this\u00a0week\'s\u00a0goal!';
const walk = {times_per_week: 4, this_week: 2, done_today: false, noun: ['walk', 'walks']};

test('encouragement asks for the logs that reach a weekly goal', () => {
  assert.deepEqual(goals.encouragement(walk, 4),
                   ['Log', '2', 'more', 'walks', TO_GOAL]);
  assert.deepEqual(goals.encouragement({...walk, this_week: 3}, 4),
                   ['Log', '1', 'more', 'walk', TO_GOAL]);
  assert.deepEqual(goals.encouragement({...walk, this_week: 3, done_today: true}, 4),
                   ['Logged today. Log', '1', 'more', 'walk', TO_GOAL]);
  assert.deepEqual(goals.encouragement({...walk, this_week: 4}, 4), ['Goal reached for this\u00a0week. Well\u00a0done!']);
  assert.deepEqual(goals.encouragement({...walk, this_week: 1}, 2), ['', '1', 'logged so far this week. Keep going!']);
  assert.deepEqual(goals.encouragement({...walk, this_week: 0}, 2), ['Start your week with a', 'walk', 'today!']);
});

test('frequency, reminder times and overview context read as plain sentences', () => {
  assert.equal(goals.frequencyText(7), 'Every day');
  assert.equal(goals.frequencyText(1), 'Once a week');
  assert.equal(goals.frequencyText(3), '3 times a week');
  assert.equal(goals.clockText('20:00'), '8:00 pm');
  assert.equal(goals.clockText('07:30'), '7:30 am');
  assert.equal(goals.clockText('00:15'), '12:15 am');
  assert.equal(goals.contextText({status: 'usual', points: []}), 'Within usual range');
  assert.equal(goals.contextText({status: 'unknown', points: [{}, {}, {}, {}]}),
               'Two more readings show usual range');
  assert.equal(goals.contextText({status: 'unknown', points: [{}, {}, {}, {}, {}]}),
               'One more reading shows usual range');
});

test('Trends charts a sleep total per day and keeps the entries', () => {
  const sleep = {daily: 'sum', points: [
    {id: 'a', time: '2026-10-21T06:00:00+00:00', value: 6, source: 'self'},
    {id: 'b', time: '2026-10-21T15:00:00+00:00', value: 1, source: 'self'},
  ]};
  const charted = trends.dailyTotals(sleep);
  assert.deepEqual(charted.points.map((p) => [p.value, p.id]), [[7, null]]);
  assert.equal(charted.entries.length, 2);
});
