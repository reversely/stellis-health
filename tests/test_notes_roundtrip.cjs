// Loads Markdown into a headless Lexical editor and exports it unchanged.
const test = require('node:test');
const assert = require('node:assert/strict');

const FIXTURE = [
  '# Title', '', '## Section', '', '### Detail', '',
  'A paragraph with **bold**, *italic*, ~~struck~~, `code`, and a [link](https://example.com/a).', '',
  '> A quoted line', '', '```', 'const x = 1;', 'return x;', '```', '', '---', '',
  '- one', '- two', '', '1. first', '2. second', '',
  '- [ ] open task', '- [x] done task', '',
  '![shot](stellis-image:123e4567-e89b-12d3-a456-426614174000)', '',
  '| Name | Role |', '| --- | --- |', '| Ada | Lead |', '| Sam | Support |',
].join('\n');

async function roundTrip(markdown) {
  const {createEditor} = await import('lexical');
  const {$convertFromMarkdownString, $convertToMarkdownString} = await import('@lexical/markdown');
  const {NOTES_NODES, NOTES_THEME, notesTransformers} = await import('../src/stellis/static/notes-editor.js');
  const transformers = notesTransformers();
  const editor = createEditor({namespace: 'RoundTrip', nodes: NOTES_NODES, theme: NOTES_THEME, onError(error) {throw error;}});
  editor.update(() => $convertFromMarkdownString(markdown, transformers), {discrete: true});
  let output = '';
  editor.getEditorState().read(() => {output = $convertToMarkdownString(transformers);});
  return output;
}

test('the full fixture exports unchanged', async () => {
  assert.equal(await roundTrip(FIXTURE), FIXTURE);
});

for (const [name, markdown] of Object.entries({
  headings: '# One\n\n## Two\n\n### Three',
  'task lists': '- [ ] open\n- [x] done',
  quote: '> quoted words',
  'code fence': '```\nconst a = 1;\n\nconst b = 2;\n```',
  divider: 'Above\n\n---\n\nBelow',
  strikethrough: 'Some ~~struck~~ text',
  'inline code': 'Run `npm test` now',
  links: 'See [docs](https://example.com/docs) now.',
  images: '![](stellis-image:123e4567-e89b-12d3-a456-426614174000)',
  'pipe table': '| A | B |\n| --- | --- |\n| 1 | 2 |',
})) test(`${name} round-trip`, async () => assert.equal(await roundTrip(markdown), markdown));

const TASKS = '- [ ] open ^tab12cd\n- [x] done ^t9z8y7x\n- [-] canceled ^tqq11aa\n- [ ] new line';

async function loaded(markdown) {
  const {createEditor, $getRoot} = await import('lexical');
  const {$convertFromMarkdownString, $convertToMarkdownString} = await import('@lexical/markdown');
  const {NOTES_NODES, NOTES_THEME, notesTransformers} = await import('../src/stellis/static/notes-editor.js');
  const transformers = notesTransformers();
  const editor = createEditor({namespace: 'Tasks', nodes: NOTES_NODES, theme: NOTES_THEME, onError(error) {throw error;}});
  editor.update(() => $convertFromMarkdownString(markdown, transformers), {discrete: true});
  const read = fn => { let value; editor.getEditorState().read(() => { value = fn(); }); return value; };
  return {editor, read, $getRoot, markdown: () => read(() => $convertToMarkdownString(transformers))};
}

test('task IDs and the canceled marker round-trip', async () => {
  assert.equal((await loaded(TASKS)).markdown(), TASKS);
});

test('task IDs and the canceled marker are not visible text', async () => {
  const {read, $getRoot} = await loaded(TASKS);
  const text = read(() => $getRoot().getTextContent());
  assert.doesNotMatch(text, /\^t|\[-\]/);
  assert.match(text, /canceled/);
});

test('a canceled item is unchecked and keeps its status after an edit to a sibling', async () => {
  const {editor, markdown, $getRoot} = await loaded(TASKS);
  const {$isListItemNode} = await import('@lexical/list');
  editor.update(() => {
    const items = $getRoot().getFirstChild().getChildren().filter($isListItemNode);
    assert.equal(items[2].getChecked(), false);
    items[3].getFirstChild().setTextContent('new line, edited');
  }, {discrete: true});
  assert.equal(markdown(), TASKS.replace('new line', 'new line, edited'));
});

test('Enter does not copy the ID or the canceled status to the new item', async () => {
  const {editor, markdown, $getRoot} = await loaded('- [-] canceled ^tqq11aa');
  const {$createRangeSelection} = await import('lexical');
  editor.update(() => {
    const item = $getRoot().getFirstChild().getFirstChild();
    item.insertNewAfter($createRangeSelection(), false).getWritable();
  }, {discrete: true});
  const lines = markdown().split('\n');
  assert.equal(lines[0], '- [-] canceled ^tqq11aa');
  assert.match(lines[1], /^- \[ \] ?$/);
});

test('tasks nested in a bullet list keep their IDs', async () => {
  const nested = '- group\n    - [ ] inner ^tab12cd';
  assert.equal((await loaded(nested)).markdown(), nested);
});
