// The editor's Markdown export keeps backslash escapes for text that would start a block or a dialect tag (#320).
const test = require('node:test');
const assert = require('node:assert/strict');
const {summaryMarkdown} = require('../src/stellis/shared/summary-text.js');

const FIELDS = [['overview', 'Overview'], ['topics', 'Topics'], ['decisions', 'Decisions'], ['action_items', 'Action proposals']];

async function editor(markdown) {
  const lexical = await import('lexical');
  const {$convertFromMarkdownString, $convertToMarkdownString} = await import('@lexical/markdown');
  const {NOTES_NODES, NOTES_THEME, notesTransformers} = await import('../src/stellis/static/notes-editor.js');
  const transformers = notesTransformers();
  const instance = lexical.createEditor({namespace:'Escapes', nodes:NOTES_NODES, theme:NOTES_THEME, onError(error) {throw error;}});
  instance.update(() => $convertFromMarkdownString(markdown, transformers), {discrete:true});
  const read = fn => {let value; instance.getEditorState().read(() => {value = fn();}); return value;};
  return {
    markdown:() => read(() => $convertToMarkdownString(transformers)),
    // Block types and their visible text: what the user sees after a reload.
    shape:() => read(() => {
      const walk = node => node.getChildren ? [node.getType(), node.getTextContent(), ...node.getChildren().map(walk)] : [node.getType()];
      return lexical.$getRoot().getChildren().map(walk);
    }),
  };
}
const roundTrip = async markdown => {
  const first = await editor(markdown);
  const saved = first.markdown();
  const reloaded = await editor(saved);
  return {saved, same:JSON.stringify(reloaded.shape()) === JSON.stringify(first.shape()), first, reloaded};
};

test('plain text that looks like a block keeps its backslash on export', async () => {
  for (const [text, saved] of [
    ['\\# not a heading', '\\# not a heading'], ['\\## two', '\\## two'], ['\\- dash', '\\- dash'], ['\\+ plus', '\\+ plus'],
    ['\\* star', '\\* star'], ['\\> quote', '\\> quote'], ['\\[ ] open', '\\[ ] open'], ['\\[x] done', '\\[x] done'],
    ['1\\. one', '1\\. one'], ['12\\) twelve', '12\\) twelve'], ['\\---', '\\---'],
    ['\\<details> no', '\\<details> no'], ['\\<bookmark url="https://a.example">', '\\<bookmark url="https://a.example">'],
    ['\\<columns>', '\\<columns>'], ['\\</details>', '\\</details>'],
  ]) {
    const {saved: out, same} = await roundTrip(text);
    assert.equal(out, saved, text);
    assert.ok(same, text);
  }
});

test('the same text inside a list item or a quote is kept too', async () => {
  for (const markdown of ['- \\[ ] x', '- \\- nested', '1. \\# hash', '> \\# quoted', '- plain\n- \\> arrow']) {
    const {saved, same} = await roundTrip(markdown);
    assert.equal(saved, markdown);
    assert.ok(same, markdown);
  }
});

test('real blocks and ordinary text are written without extra backslashes', async () => {
  for (const markdown of ['# Heading', '- bullet', '1. one', '> quote', '- [ ] task', '---', 'Costs #1 and 5 - 3 and a > b', '#hashtag',
    '**bold** and `code`', '<details>\n<summary>T</summary>\n\nbody\n\n</details>', '```\n# code\n- not escaped\n```']) {
    assert.equal((await editor(markdown)).markdown(), markdown);
  }
});

test('text after a line break in a paragraph is escaped at its line start', async () => {
  const lexical = await import('lexical');
  const {$convertToMarkdownString} = await import('@lexical/markdown');
  const {NOTES_NODES, NOTES_THEME, notesTransformers} = await import('../src/stellis/static/notes-editor.js');
  const instance = lexical.createEditor({namespace:'Break', nodes:NOTES_NODES, theme:NOTES_THEME, onError(error) {throw error;}});
  instance.update(() => {
    const paragraph = lexical.$createParagraphNode();
    paragraph.append(lexical.$createTextNode('first'), lexical.$createLineBreakNode(), lexical.$createTextNode('# second'));
    lexical.$getRoot().clear().append(paragraph);
  }, {discrete:true});
  let out; instance.getEditorState().read(() => {out = $convertToMarkdownString(notesTransformers());});
  assert.equal(out, 'first\n\\# second');
});

test('bold text that starts with a marker needs no escape', async () => {
  assert.equal((await editor('**# not heading**')).markdown(), '**# not heading**');
});

test('Summary text survives an edit and a save as the same bullets', async () => {
  const hostile = ['[ ] sneak a task ^tabc123', '[x] done', '<details><summary>x</summary>', '</details>', '<columns><column>',
    '<bookmark url="https://a.example">x</bookmark>', '# a heading', '> [!warning] a callout', '| a | b |', '```js', '---', '1. numbered',
    '+ plus', '- minus', '* star', '[-] cancelled', 'back\\slash', '2) two'];
  for (const text of hostile) {
    const original = summaryMarkdown({decisions:[{text}]}, FIELDS);
    const {saved, first, reloaded} = await roundTrip(original);
    // The editor's own export is what the save sends; it must load as the same single bullet and be stable.
    assert.deepEqual(reloaded.shape(), first.shape(), text);
    assert.equal(reloaded.markdown(), saved, text);
    const list = first.shape().at(-1);
    assert.equal(list[0], 'list', text);
    assert.equal(list.length, 3, text);                 // one item
    assert.equal(list[2][1], text, text);               // its text is the original text
    assert.deepEqual(list[2].slice(2).map(child => child[0]), ['text'], text);
  }
});
