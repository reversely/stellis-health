const test = require('node:test');
const assert = require('node:assert/strict');
const {formatListDate, getDateFormat, setDateFormat} = require('../src/stellis/shared/list-date.js');

const now = new Date(2026, 9, 18, 15, 30); // Sunday, Oct 18 2026
const f = (value, format) => formatListDate(value, {now, format});
const memory = () => {const data = {}; return {getItem: key => data[key] ?? null, setItem: (key, value) => {data[key] = value;}};};

test('relative format', () => {
  assert.equal(f('2026-10-18'), 'Today');
  assert.equal(f('2026-10-19'), 'Tomorrow');
  assert.equal(f('2026-10-20'), 'Tuesday');
  assert.equal(f('2026-10-24'), 'Saturday');
  assert.equal(f('2026-10-25'), 'Oct 25');
  assert.equal(f('2026-10-17'), 'Oct 17');
  assert.equal(f('2027-01-05'), 'Jan 5, 2027');
});
test('timestamps use the local day', () => assert.equal(f(new Date(2026, 9, 19, 9).toISOString()), 'Tomorrow'));
test('short and iso formats ignore relativity', () => {
  assert.equal(f('2026-10-18', 'short'), 'Oct 18');
  assert.equal(f('2026-10-19', 'iso'), '2026-10-19');
});
test('an invalid date is empty', () => assert.equal(f('nonsense'), ''));
test('the setting defaults to relative and survives bad storage', () => {
  const storage = memory();
  assert.equal(getDateFormat(storage), 'relative');
  assert.ok(setDateFormat('iso', storage));
  assert.equal(getDateFormat(storage), 'iso');
  assert.ok(!setDateFormat('weird', storage));
  assert.equal(getDateFormat({getItem() {throw new Error('blocked');}}), 'relative');
  assert.ok(!setDateFormat('short', {setItem() {throw new Error('blocked');}}));
});
