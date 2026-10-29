const test = require('node:test');
const assert = require('node:assert/strict');
const {isHexColour, inkFor, fillFor, countTodos, buildItems, filterItems, latestSummary} = require('../src/stellis/static/notebook-items.js');

test('isHexColour accepts only #RRGGBB', () => {
  assert.ok(isHexColour('#45ADA8')); assert.ok(isHexColour('#abcdef'));
  for (const bad of ['45ADA8', '#45ADA', '#45ADA8F', '#GGGGGG', '', null]) assert.equal(isHexColour(bad), false);
});

test('inkFor picks the higher-contrast house ink', () => {
  assert.equal(inkFor('#495575'), '#F7F5F6');
  assert.equal(inkFor('#45ADA8'), '#27222B');
  assert.equal(inkFor('#FFFF00'), '#27222B');
  assert.equal(inkFor('nonsense'), '#F7F5F6');
});

test('custom notebook backgrounds keep normal text contrast at least 4.5:1', () => {
  const luminance = hex => {
    const linear = [1,3,5].map(i => parseInt(hex.slice(i,i+2),16)/255)
      .map(c => c <= .04045 ? c/12.92 : ((c+.055)/1.055)**2.4);
    return linear[0]*.2126+linear[1]*.7152+linear[2]*.0722;
  };
  assert.equal(inkFor('#547980'), '#FFFFFF');
  for (let r=0;r<=255;r+=17) for (let g=0;g<=255;g+=17) for (let b=0;b<=255;b+=17) {
    const background='#'+[r,g,b].map(c=>c.toString(16).padStart(2,'0')).join('');
    const fill=fillFor(background),ink=inkFor(background);
    assert.notEqual(ink.toUpperCase(),'#000000',background);
    const levels=[luminance(fill),luminance(ink)].sort((a,b)=>b-a);
    assert((levels[0]+.05)/(levels[1]+.05)>=4.5,background);
  }
});

test('countTodos sums open and done per source', () => {
  const group = (id, n) => ({source: {id}, items: Array.from({length: n}, () => ({}))});
  const counts = countTodos([group('a', 2), group('b', 1)], [group('a', 3)]);
  assert.deepEqual(counts.get('a'), {open: 2, done: 3});
  assert.deepEqual(counts.get('b'), {open: 1, done: 0});
  assert.equal(counts.get('c'), undefined);
});

test('buildItems merges meetings and notes newest first with tags and counts', () => {
  const items = buildItems({
    conversations: [{id: 'c1', title: 'Kickoff', created_at: '2026-10-01T10:00:00Z', recorded_at: '2026-10-03T10:00:00Z'}],
    pages: [{id: 'p1', title: 'Plan', updated_at: '2026-10-05T10:00:00Z', category_ids: ['t1']},
            {id: 'p2', title: 'Old', updated_at: '2026-09-01T10:00:00Z'}],
    tagsByConversation: new Map([['c1', ['t2']]]),
    counts: new Map([['p1', {open: 1, done: 2}]]),
  });
  assert.deepEqual(items.map(item => item.id), ['p1', 'c1', 'p2']);
  assert.deepEqual(items[0].todos, {open: 1, done: 2});
  assert.deepEqual(items[1].categoryIds, ['t2']);
  assert.deepEqual(items[2].todos, {open: 0, done: 0});
  assert.deepEqual(filterItems(items, 'page').map(item => item.id), ['p1', 'p2']);
  assert.deepEqual(filterItems(items, 'conversation').map(item => item.id), ['c1']);
  assert.equal(filterItems(items, 'all').length, 3);
});

test('latestSummary takes the newest meeting with an overview, unmodified', () => {
  const meetings = [{id: 'a', title: 'New', created_at: '2026-10-02T00:00:00Z'}, {id: 'b', title: 'Old', created_at: '2026-10-01T00:00:00Z'}];
  const text = '  We chose the **teal** design.  ';
  assert.deepEqual(latestSummary(meetings, new Map([['b', text]])), {id: 'b', title: 'Old', date: '2026-10-01T00:00:00Z', text});
  assert.equal(latestSummary(meetings, new Map()), null);
  assert.equal(latestSummary([], new Map()), null);
});

test('document entries appear in Records and retain their recorded dates', () => {
  const items = buildItems({documents:[{id:'scan',title:'Lab attachment',record_date:'2026-01-03',created_at:'2026-01-05'}]});
  assert.equal(items[0].kind,'document');
  assert.equal(items[0].date,'2026-01-03');
  assert.equal(filterItems(items,'document')[0].id,'scan');
});
