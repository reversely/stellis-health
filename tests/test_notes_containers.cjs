// Toggles and columns round-trip through the notes Markdown dialect (#280).
const test = require('node:test');
const assert = require('node:assert/strict');

async function load(markdown) {
  const lexical = await import('lexical');
  const {$convertFromMarkdownString, $convertToMarkdownString} = await import('@lexical/markdown');
  const {NOTES_NODES, NOTES_THEME, notesTransformers} = await import('../src/stellis/static/notes-editor.js');
  const callouts = await import('../src/stellis/static/notes-callouts.js');
  const containers = await import('../src/stellis/static/notes-containers.js');
  const transformers = notesTransformers();
  const editor = lexical.createEditor({namespace:'Containers', nodes:NOTES_NODES, theme:NOTES_THEME, onError(error) {throw error;}});
  callouts.registerCallouts(editor);
  containers.registerContainers(editor);
  editor.update(() => $convertFromMarkdownString(markdown, transformers), {discrete:true});
  const read = fn => {let value; editor.getEditorState().read(() => {value = fn();}); return value;};
  return {editor, lexical, containers, transformers, read, markdown: () => read(() => $convertToMarkdownString(transformers))};
}
const same = async markdown => assert.equal((await load(markdown)).markdown(), markdown);
const toggle = (title, body) => `<details>\n<summary>${title}</summary>\n\n${body}\n\n</details>`;
const columns = (...bodies) => `<columns>\n${bodies.map(body => `<column>\n${body}\n</column>`).join('\n')}\n</columns>`;

test('a toggle with a paragraph', () => same(toggle('Why these goals', 'The brief asks for a remote.')));
test('a toggle with an empty body', () => same('<details>\n<summary>Only a title</summary>\n</details>'));
test('a toggle with several kinds of blocks', () => same(toggle('Plan',
  'Intro with **bold**.\n\n- one\n- two\n\n1. first\n2. second\n\n```js\nconst a = 1;\n\nconst b = 2;\n```\n\n> quoted\n\n| A | B |\n| --- | --- |\n| 1 | 2 |')));
test('to-do lines keep their IDs inside a toggle', () => same(toggle('Tasks', '- [ ] call Avery ^tabc123\n- [x] send notes ^tdef456\n- [-] drop it ^tghi789')));
test('toggles nest', () => same(toggle('Outer', 'Before\n\n' + toggle('Inner', '- [ ] deep task ^tabc123') + '\n\nAfter')));
test('a toggle sits between other blocks', () => same('Before\n\n' + toggle('One', 'a') + '\n\n' + toggle('Two', 'b') + '\n\nAfter'));
test('the single-line summary form and the open attribute are read', async () => {
  const input = '<details open><summary>Title</summary>\n\nBody\n\n</details>';
  assert.equal((await load(input)).markdown(), toggle('Title', 'Body'));
});
test('a closing tag inside a code fence does not end the toggle', () => same(toggle('Code', '```html\n</details>\n```\n\nStill inside')));
test('an unclosed toggle stays as text', async () => {
  const {markdown} = await load('<details>\n<summary>Open</summary>\n\nBody');
  assert.match(markdown(), /<details>/);
  assert.match(markdown(), /Body/);
});
test('markup in a title is escaped and read back', () => same(toggle('a &lt;/summary&gt; b', 'x').replace('&gt;', '>')));
test('a plain angle bracket in a title stays safe', async () => {
  const {editor, lexical, markdown} = await load(toggle('Title', 'Body'));
  editor.update(() => lexical.$getRoot().getFirstChild().getFirstChild().append(lexical.$createTextNode(' </summary> x')), {discrete:true});
  const out = markdown();
  assert.equal(out.split('\n')[1], '<summary>Title &lt;/summary> x</summary>');
  assert.equal((await load(out)).markdown(), out);
});

test('two columns', () => same(columns('left', 'right')));
test('columns hold any blocks, to-do lines included', () => same(columns('- [ ] left task ^tabc123\n\nSecond paragraph\n\n> quote', '1. one\n2. two\n\n```\ncode\n```')));
test('an empty column', () => same('<columns>\n<column>\n</column>\n<column>\nright\n</column>\n</columns>'));
test('three columns', () => same(columns('a', 'b', 'c')));
test('a toggle inside a column and columns inside a toggle', () => same(columns(toggle('In a column', '- [ ] task ^tabc123'), 'plain')
  + '\n\n' + toggle('Holds columns', columns('x', 'y'))));
test('columns inside a callout', () => same('> [!note] Look\n> ' + columns('a', 'b').split('\n').join('\n> ')));
test('text outside a column stays as text', async () => {
  const output = (await load('<columns>\nstray\n<column>\nleft\n</column>\n</columns>')).markdown();
  assert.match(output, /stray/);
  assert.match(output, /left/);
});

test('the structure of a toggle and of columns', async () => {
  const {read, lexical} = await load(toggle('T', 'one\n\ntwo') + '\n\n' + columns('a', 'b'));
  const shape = read(() => lexical.$getRoot().getChildren().map(node =>
    [node.getType(), node.getChildren().map(child => child.getType() === 'column' ? child.getTextContent() : child.getTextContent())]));
  assert.deepEqual(shape, [['toggle', ['T', 'one', 'two']], ['columns', ['a', 'b']]]);
});

test('the shape of a toggle is repaired', async () => {
  const {editor, lexical, markdown} = await load(toggle('T', 'body'));
  editor.update(() => lexical.$getRoot().getFirstChild().getFirstChild().remove(), {discrete:true});
  assert.equal(markdown(), '<details>\n<summary>body</summary>\n</details>');
});

test('the toolbar inserts a toggle after a block and columns in place of an empty paragraph', async () => {
  const {editor, lexical, containers, markdown} = await load('keep me');
  editor.update(() => {lexical.$getRoot().getFirstChild().selectEnd(); containers.$insertToggle();}, {discrete:true});
  assert.equal(markdown(), 'keep me\n\n<details>\n<summary></summary>\n</details>');
  const more = await load('');
  more.editor.update(() => {more.lexical.$getRoot().getFirstChild().select(); containers.$insertColumns(2);}, {discrete:true});
  assert.equal(more.markdown(), '<columns>\n<column>\n</column>\n<column>\n</column>\n</columns>');
  assert.equal(more.read(() => more.lexical.$getRoot().getChildrenSize()), 1);
});

const rootBlock = lexical => {let node = lexical.$getSelection().anchor.getNode(); while (node.getParent() && node.getParent().getType() !== 'root') node = node.getParent(); return node.getType();};
function press(editor, lexical, command, init = {}) {
  let prevented = false; let handled = false;
  editor.update(() => {handled = editor.dispatchCommand(command, {preventDefault() {prevented = true;}, ...init});}, {discrete:true});
  return {handled, prevented};
}

test('Enter in the title moves into the body; Enter on an empty last line leaves the toggle', async () => {
  const {editor, lexical, markdown, read} = await load(toggle('T', 'body'));
  editor.update(() => lexical.$getRoot().getFirstChild().getFirstChild().selectEnd(), {discrete:true});
  assert.ok(press(editor, lexical, lexical.KEY_ENTER_COMMAND).handled);
  assert.equal(read(() => rootBlock(lexical)), 'toggle');
  assert.equal(read(() => lexical.$getSelection().anchor.getNode().getTextContent()), 'body');
  editor.update(() => {
    const body = lexical.$getRoot().getFirstChild().getLastChild(); body.selectEnd();
    body.insertAfter(lexical.$createParagraphNode()); body.getNextSibling().select();
  }, {discrete:true});
  assert.ok(press(editor, lexical, lexical.KEY_ENTER_COMMAND).handled);
  assert.equal(read(() => lexical.$getRoot().getChildrenSize()), 2);
  assert.equal(read(() => rootBlock(lexical)), 'paragraph');
  assert.equal(markdown().trimEnd(), toggle('T', 'body'));
});

test('ArrowDown at the end of the last line leaves a toggle or a column group', async () => {
  const {editor, lexical, read} = await load(toggle('T', 'body') + '\n\n' + columns('a', 'b'));
  editor.update(() => lexical.$getRoot().getFirstChild().getLastChild().selectEnd(), {discrete:true});
  assert.ok(press(editor, lexical, lexical.KEY_ARROW_DOWN_COMMAND).prevented);
  assert.equal(read(() => rootBlock(lexical)), 'columns');
  editor.update(() => lexical.$getRoot().getLastChild().getFirstChild().selectEnd(), {discrete:true});
  press(editor, lexical, lexical.KEY_ARROW_DOWN_COMMAND);
  assert.equal(read(() => lexical.$getRoot().getLastChild().getType()), 'paragraph');
  assert.equal(read(() => rootBlock(lexical)), 'paragraph');
});

test('ArrowDown in the middle of a line is left to the browser', async () => {
  const {editor, lexical} = await load(toggle('T', 'body'));
  editor.update(() => lexical.$getRoot().getFirstChild().getLastChild().getFirstChild().select(1, 1), {discrete:true});
  assert.equal(press(editor, lexical, lexical.KEY_ARROW_DOWN_COMMAND).handled, false);
});

test('Backspace in an empty toggle or column removes it', async () => {
  const empty = await load('<details>\n<summary></summary>\n</details>');
  empty.editor.update(() => empty.lexical.$getRoot().getFirstChild().getFirstChild().select(), {discrete:true});
  assert.ok(press(empty.editor, empty.lexical, empty.lexical.KEY_BACKSPACE_COMMAND).handled);
  assert.equal(empty.read(() => empty.lexical.$getRoot().getFirstChild().getType()), 'paragraph');
  const cols = await load('<columns>\n<column>\n</column>\n<column>\nright\n</column>\n</columns>');
  cols.editor.update(() => cols.lexical.$getRoot().getFirstChild().getFirstChild().getFirstChild().select(), {discrete:true});
  press(cols.editor, cols.lexical, cols.lexical.KEY_BACKSPACE_COMMAND);
  assert.equal(cols.markdown(), '<columns>\n<column>\nright\n</column>\n</columns>');
});
