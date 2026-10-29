// The Markdown that copy and cut write to text/plain (#277). The HTML half needs a DOM and is checked in a browser.
const test = require('node:test');
const assert = require('node:assert/strict');

async function copied(markdown, select = lexical => lexical.$getRoot().select(0, lexical.$getRoot().getChildrenSize())) {
  const lexical = await import('lexical');
  const {$convertFromMarkdownString} = await import('@lexical/markdown');
  const {NOTES_NODES, NOTES_THEME, notesTransformers} = await import('../src/stellis/static/notes-editor.js');
  const {selectionMarkdown} = await import('../src/stellis/static/notes-clipboard.js');
  const transformers = notesTransformers();
  const editor = lexical.createEditor({namespace:'Clip', nodes:NOTES_NODES, theme:NOTES_THEME, onError(error) {throw error;}});
  editor.update(() => {$convertFromMarkdownString(markdown, transformers); select(lexical);}, {discrete:true});
  let out = '';
  editor.read(() => {out = selectionMarkdown(editor, lexical.$getSelection(), {nodes:NOTES_NODES, theme:NOTES_THEME, transformers});});
  return out;
}

test('copied Markdown has no task IDs', async () => {
  assert.equal(await copied('# Plan\n\n- [ ] call Avery tomorrow ^tabc123\n- [x] sent ^tzzz999\n\n> quoted'),
    '# Plan\n\n- [ ] call Avery tomorrow\n- [x] sent\n\n> quoted');
});
test('canceled to-dos keep their marker', async () => {
  assert.equal(await copied('- [-] dropped ^tabc123\n- [ ] kept ^tdef456'), '- [-] dropped\n- [ ] kept');
});
test('a partial selection copies only the selected text', async () => {
  assert.equal(await copied('hello world', lexical => {
    const text = lexical.$getRoot().getFirstChild().getFirstChild(); text.select(0, 5);
  }), 'hello');
});
test('a selection that spans a table copies the table as Markdown', async () => {
  const table = 'Before\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\nAfter';
  assert.equal(await copied(table), table);
});
