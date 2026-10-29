const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const context = {window: {}, TextEncoder, URL};
vm.runInNewContext(fs.readFileSync('src/stellis/shared/snapshot-format.js', 'utf8'), context);
const {partsFor, assemble, sourceUrl} = context.window.StellisSnapshot;
const source = Array.from({length: 23}, (_, index) => ({
  id: `passage-${index}`, start_ms: index * 1000, end_ms: index * 1000 + 900,
  text: `Words ${index}`, speaker_display_name: 'Speaker A', speaker_uncertain: false,
}));
const parts = partsFor('snapshot', source, [{segment_id: 'passage-11', quote: 'Words 11', start_offset: 0, end_offset: 8}]);
assert.equal(parts.length, 3);
assert.deepEqual(Array.from(parts, part => part.segments.length), [10, 10, 3]);
assert.equal(parts[1].segments[1].highlights[0].quote, 'Words 11');
const ids = ['part-0', 'part-1', 'part-2'];
const manifest = {kind: 'conversation_manifest', snapshot_id: 'snapshot', part_ids: ids, segment_count: 23};
const entries = new Map(parts.map((part, index) => [ids[index], part]));
assert.equal(assemble(manifest, entries).length, 23);
assert.equal(assemble(manifest, new Map([...entries].slice(0, 2))), null);
assert.equal(assemble({...manifest, part_ids: ['part-1', 'part-0', 'part-2']}, entries), null);
assert.equal(assemble({...manifest, part_ids: ['part-0', 'part-0', 'part-2']}, entries), null);
assert.equal(assemble({...manifest, segment_count: 22}, entries), null);
assert.equal(assemble(manifest, new Map([...entries].map(([id, part]) =>
  [id, id === 'part-1' ? {...part, snapshot_id: 'other'} : part]))), null);
assert.equal(assemble(manifest, new Map([...entries].map(([id, part]) =>
  [id, id === 'part-1' ? {...part, segments: [{...part.segments[0], id: 'passage-0'}, ...part.segments.slice(1)]} : part]))), null);
assert.equal(sourceUrl({...manifest, source_conversation_id:'conversation', source_origin:'http://127.0.0.1:8767'},
  source[0]),'http://127.0.0.1:8767/conversation/conversation?at=passage-0');
assert.equal(sourceUrl({...manifest, source_conversation_id:'conversation'},source[0]),
  'http://127.0.0.1:8765/conversation/conversation?at=passage-0');
for(const source_origin of ['https://example.com:8765','http://127.0.0.1:8767/path','http://127.0.0.1',
  'http://127.0.0.1:8767@evil.example:8767','']){
  assert.equal(sourceUrl({...manifest, source_conversation_id:'conversation',source_origin},source[0]),null);
}
console.log('Snapshot format checks passed');
