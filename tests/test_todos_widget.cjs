const test = require('node:test');
const assert = require('node:assert/strict');
const {groupTodos, appendTodoMarkdown, rowDate} = require('../src/stellis/static/todos-widget.js');

const item = id => ({id, text: id, status: 'open'});
const groups = [
  {source: {kind: 'page', id: 'p1', title: 'Loose', notebook_id: null}, items: [item('a')]},
  {source: {kind: 'conversation', id: 'c1', title: 'Kickoff', notebook_id: 'n1'}, items: [item('b')]},
  {source: {kind: 'page', id: 'p2', title: 'Plan', notebook_id: 'n1'}, items: [item('c')]},
];
const books = [{id: 'n1', name: 'Design', color: '#123456'}];

test('all scope groups by notebook and keeps the source', () => {
  const out = groupTodos(groups, books, 'all');
  assert.deepEqual(out.map(g => g.notebook?.name ?? null), [null, 'Design']);
  assert.deepEqual(out[1].items.map(i => [i.id, i.source.title]), [['b', 'Kickoff'], ['c', 'Plan']]);
});
test('a notebook scope stays flat', () => {
  const out = groupTodos(groups.slice(1), books, 'n1');
  assert.equal(out.length, 1);
  assert.equal(out[0].notebook, null);
});
test('appendTodoMarkdown adds one open line', () => {
  assert.equal(appendTodoMarkdown('', ' Buy  milk '), '- [ ] Buy milk\n');
  assert.equal(appendTodoMarkdown('- [ ] a ^tabc123\n', 'b'), '- [ ] a ^tabc123\n- [ ] b\n');
  assert.equal(appendTodoMarkdown('Notes here\n', 'b'), 'Notes here\n\n- [ ] b\n');
});
test('rowDate shows the date that matches the status', () => {
  assert.equal(rowDate({status: 'open'}), null);
  assert.deepEqual(rowDate({status: 'open', due_date: '2026-10-20'}), {value: '2026-10-20', label: 'Due'});
  assert.equal(rowDate({status: 'done', completed_at: 'x'}).label, 'Completed');
  assert.equal(rowDate({status: 'canceled', canceled_at: 'x'}).label, 'Canceled');
});
