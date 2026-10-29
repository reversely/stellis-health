// Callouts round-trip through the notes Markdown dialect (#279).
const test = require('node:test');
const assert = require('node:assert/strict');

async function load(markdown) {
  const lexical = await import('lexical');
  const {$convertFromMarkdownString, $convertToMarkdownString} = await import('@lexical/markdown');
  const {NOTES_NODES, NOTES_THEME, notesTransformers} = await import('../src/stellis/static/notes-editor.js');
  const callouts = await import('../src/stellis/static/notes-callouts.js');
  const transformers = notesTransformers();
  const editor = lexical.createEditor({namespace:'Callouts', nodes:NOTES_NODES, theme:NOTES_THEME, onError(error) {throw error;}});
  callouts.registerCallouts(editor);
  editor.update(() => $convertFromMarkdownString(markdown, transformers), {discrete:true});
  const read = fn => {let value; editor.getEditorState().read(() => {value = fn();}); return value;};
  return {editor, lexical, callouts, transformers, read, markdown: () => read(() => $convertToMarkdownString(transformers))};
}
const same = async markdown => assert.equal((await load(markdown)).markdown(), markdown);

test('a titled callout', () => same('> [!note] Heads up\n> Body text.'));
test('GitHub types without a title', () => same('> [!warning]\n> Be careful.'));
test('an uppercase type word is written back in lowercase', async () => {
  assert.equal((await load('> [!WARNING]\n> Be careful.')).markdown(), '> [!warning]\n> Be careful.');
});
test('any type word', () => same('> [!my-custom-type] Mine\n> Body.'));
test('a multi-block body', () => same('> [!tip] Packing\n> First paragraph.\n>\n> - one\n> - two\n>\n> ```\n> code\n> ```\n>\n> Last paragraph.'));
test('a to-do inside a callout keeps its ID', () => same('> [!note] Plan\n> - [ ] call Avery ^tabc123'));
test('inline formatting in the title and body', () => same('> [!note] **Bold** title\n> Some `code` and *emphasis*.'));
test('callouts sit between other blocks', () => same('Before\n\n> [!info] One\n> a\n\n> [!question] Two\n> b\n\nAfter'));
test('a plain quote stays a quote', () => same('> just a quote'));
test('a callout with no body still exports', async () => {
  assert.equal((await load('> [!note] Only a title')).markdown(), '> [!note] Only a title');
});

test('the structure is a title paragraph then the body', async () => {
  const {read, lexical} = await load('> [!tip] Hello\n> one\n>\n> two');
  const shape = read(() => {
    const callout = lexical.$getRoot().getFirstChild();
    return [callout.getType(), callout.getKind(), callout.getChildren().map(child => child.getTextContent())];
  });
  assert.deepEqual(shape, ['callout', 'tip', ['Hello', 'one', 'two']]);
});

test('the shape is repaired when the title paragraph is removed', async () => {
  const {editor, lexical, markdown} = await load('> [!note] T\n> body');
  editor.update(() => lexical.$getRoot().getFirstChild().getFirstChild().remove(), {discrete:true});
  assert.equal(markdown(), '> [!note] body');
});

test('insertion wraps the current block and a second pick changes the type', async () => {
  const {editor, lexical, callouts, markdown} = await load('keep me');
  editor.update(() => {lexical.$getRoot().getFirstChild().selectEnd(); callouts.$insertCallout('warning');}, {discrete:true});
  assert.equal(markdown(), '> [!warning]\n> keep me');
  editor.update(() => callouts.$insertCallout('tip'), {discrete:true});
  assert.equal(markdown(), '> [!tip]\n> keep me');
});

test('callout JSON round-trips for copy and paste', async () => {
  const {editor, lexical, markdown} = await load('> [!caution] Hot\n> body');
  const json = editor.getEditorState().toJSON();
  editor.setEditorState(editor.parseEditorState(JSON.stringify(json)));
  assert.equal(markdown(), '> [!caution] Hot\n> body');
});
