const test = require('node:test');
const assert = require('node:assert/strict');
const {agentName, todoTextFor, describe, matchLine, evidenceLabel} = require('../src/stellis/static/suggestions.js');

test('agentName names the Transcript Agent and titles other authors', () => {
  assert.equal(agentName('transcript-agent'), 'Transcript Agent');
  assert.equal(agentName('calendar-helper'), 'Calendar helper');
});
test('todoTextFor reads the line that carries the ID', () => {
  const md = '## To-dos\n\n- [ ] Draft the plan ^tabc123\n- [x] Read the brief ^tdef456\n';
  assert.equal(todoTextFor(md, 'tdef456'), 'Read the brief');
  assert.equal(todoTextFor(md, 'tzzzzzz'), null);
  assert.equal(todoTextFor(md, null), null);
});
test('describe puts the change in words', () => {
  assert.deepEqual(describe({operation: 'add_todo', values: {text: ' Buy  milk '}}), {verb: 'Add to-do', text: 'Buy milk'});
  assert.deepEqual(describe({operation: 'set_todo_status', values: {status: 'done'}}, 'Draft'), {verb: 'Mark done', text: 'Draft'});
  assert.equal(describe({operation: 'set_todo_status', values: {status: 'canceled'}}, 'x').verb, 'Cancel');
});
test('matchLine prefers an exact line and refuses an ambiguous partial', () => {
  assert.equal(matchLine(['Draft the plan', 'Draft the plan today'], 'draft the plan'), 0);
  assert.equal(matchLine(['Draft the plan Tuesday 9:30 AM', 'Other'], 'Draft the plan'), 0);
  assert.equal(matchLine(['Draft a', 'Draft b'], 'Draft'), -1);
  assert.equal(matchLine(['a'], ''), -1);
});
test('evidenceLabel falls back when the segment does not resolve', () => {
  assert.equal(evidenceLabel(null), 'Open transcript');
  assert.equal(evidenceLabel({start_ms: 125000}), '2:05');
});
