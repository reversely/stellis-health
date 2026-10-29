// Model text in a Summary item stays literal text when it becomes Markdown (#278).
const test = require('node:test');
const assert = require('node:assert/strict');
const {escapeItemText, summaryMarkdown} = require('../src/stellis/shared/summary-text.js');

const FIELDS = [['overview', 'Overview'], ['topics', 'Topics'], ['decisions', 'Decisions'], ['action_items', 'Action proposals']];

const HOSTILE = [
  '[ ] sneak a task ^tabc123', '[x] done', '<details><summary>x</summary>', '</details>', '<columns><column>', '<bookmark url="https://a.example">x</bookmark>',
  '# a heading', '> [!warning] a callout', '| a | b |', '```js', '---', '1. numbered', '+ plus', '- minus',
  '![shot](stellis-image:123e4567-e89b-12d3-a456-426614174000)', '[link](https://a.example)', '**bold** and _em_ and ~~strike~~ and `code`', 'back\\slash',
  'line one\n- [ ] line two\n# line three',
];

test('every character that Markdown reads as syntax gets a backslash', () => {
  assert.equal(escapeItemText('[ ] a *b* `c` <d>'), '\\[ \\] a \\*b\\* \\`c\\` \\<d\\>');
});
test('line breaks fold into one line and plain text is untouched', () => {
  assert.equal(escapeItemText('  Ship it\n\nnext week  '), 'Ship it next week');
  assert.equal(escapeItemText('Budget is 5 percent'), 'Budget is 5 percent');
  assert.equal(escapeItemText(undefined), '');
});
test('a leading list marker is neutralized', () => {
  assert.equal(escapeItemText('1. one'), '1\\. one');
  assert.equal(escapeItemText('2) two'), '2\\) two');
  assert.equal(escapeItemText('+ plus'), '\\+ plus');
});
test('the summary has a section per field, one bullet per item', () => {
  assert.equal(summaryMarkdown({overview: [{text: 'A'}], topics: [], decisions: [{text: 'B'}, {text: 'C'}]}, FIELDS),
    '## Overview\n\n- A\n\n## Decisions\n\n- B\n- C');
  assert.equal(summaryMarkdown(null, FIELDS), '');
});

async function imported(markdown) {
  const lexical = await import('lexical');
  const {$convertFromMarkdownString, $convertToMarkdownString} = await import('@lexical/markdown');
  const {NOTES_NODES, NOTES_THEME, notesTransformers} = await import('../src/stellis/static/notes-editor.js');
  const transformers = notesTransformers();
  const editor = lexical.createEditor({namespace: 'Summary', nodes: NOTES_NODES, theme: NOTES_THEME, onError(error) {throw error;}});
  editor.update(() => $convertFromMarkdownString(markdown, transformers), {discrete: true});
  let shape;
  editor.getEditorState().read(() => {
    const root = lexical.$getRoot();
    const list = root.getChildren().at(-1);
    shape = {types: root.getChildren().map(node => node.getType()),
      lists: list.getChildren().map(item => ({type: item.getType(), kids: item.getChildren().map(child => child.getType()),
        text: item.getTextContent(), checked: item.getChecked()}))};
  });
  return shape;
}

test('hostile item text loads as one plain bullet each, with no other block', async () => {
  for (const text of HOSTILE) {
    const shape = await imported(summaryMarkdown({decisions: [{text}]}, FIELDS));
    assert.deepEqual(shape.types, ['heading', 'list'], text);
    assert.equal(shape.lists.length, 1, text);
    assert.deepEqual(shape.lists[0].kids, ['text'], text);
    assert.equal(shape.lists[0].text, text.replace(/\s+/g, ' ').trim(), text);
  }
});
