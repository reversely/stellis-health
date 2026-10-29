// Natural-language dates in a task line (chrono-node, ADR 0059), resolved by findDue.
const test = require('node:test');
const assert = require('node:assert/strict');

const NOW = new Date(2026, 9, 18, 10, 0); // Sunday 18 October 2026, 10:00 local

test('a date and time are found in a task line', async () => {
  const {findDue} = await import('../src/stellis/static/notes-tasks.js');
  assert.deepEqual(findDue('Send spec tomorrow at 3pm', NOW), {phrase: 'tomorrow at 3pm', date: '2026-10-19', time: '15:00'});
});

test('a date without a time has no time', async () => {
  const {findDue} = await import('../src/stellis/static/notes-tasks.js');
  const due = findDue('Pay invoice on Oct 24', NOW);
  assert.equal(due.date, '2026-10-24');
  assert.equal(due.time, '');
});

test('a weekday resolves forward from the typing day', async () => {
  const {findDue} = await import('../src/stellis/static/notes-tasks.js');
  assert.equal(findDue('Call back friday', NOW).date, '2026-10-23');
});

test('text with no date gives nothing', async () => {
  const {findDue} = await import('../src/stellis/static/notes-tasks.js');
  assert.equal(findDue('Review the budget', NOW), null);
});
