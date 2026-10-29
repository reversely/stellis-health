const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const context = {window: {}};
vm.runInNewContext(fs.readFileSync('src/stellis/shared/notes-merge.js', 'utf8'), context);
const plain = value => JSON.parse(JSON.stringify(value));
const {stripTaskIds, sameIgnoringIds, mergeWithRemote} = context.window.StellisNotesMerge;

assert.equal(stripTaskIds('- [ ] call Sam ^tab12cd\n- [x] done ^t000000  \ntext ^tab12cd more'),
  '- [ ] call Sam\n- [x] done  \ntext ^tab12cd more');
assert.equal(stripTaskIds('not an id ^tABC123\nshort ^tab12'), 'not an id ^tABC123\nshort ^tab12');
assert.ok(sameIgnoringIds('- [ ] a ^tab12cd', '- [ ] a'));
assert.ok(!sameIgnoringIds('- [ ] a ^tab12cd', '- [ ] b'));

// An ID-only difference adopts the server text and never concatenates.
assert.deepEqual(plain(mergeWithRemote('- [ ] a ^tab12cd', '- [ ] a')), {action:'adopt', markdown:'- [ ] a ^tab12cd'});
// The draft has an extra edit on top of the server text: keep it, no duplicate.
assert.deepEqual(plain(mergeWithRemote('- [ ] a ^tab12cd', '- [ ] a\n- [ ] b')), {action:'keep', markdown:'- [ ] a\n- [ ] b'});
assert.equal(mergeWithRemote('x', 'y', true).action, 'keep');
assert.deepEqual(plain(mergeWithRemote('server', 'local')), {action:'combine', markdown:'server\n\n---\n\nlocal'});
