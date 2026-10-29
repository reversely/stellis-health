// The slash menu (#317): matching, filtering, keyboard stepping, placement, and what each row inserts.
const test = require('node:test');
const assert = require('node:assert/strict');

async function load(markdown, offset) {
  const lexical = await import('lexical');
  const {$convertFromMarkdownString, $convertToMarkdownString} = await import('@lexical/markdown');
  const {NOTES_NODES, NOTES_THEME, notesTransformers} = await import('../src/stellis/static/notes-editor.js');
  const callouts = await import('../src/stellis/static/notes-callouts.js');
  const containers = await import('../src/stellis/static/notes-containers.js');
  const slash = await import('../src/stellis/static/notes-slash.js');
  const {registerRichText} = await import('@lexical/rich-text');
  const {registerList, registerCheckList} = await import('@lexical/list');
  const transformers = notesTransformers();
  const editor = lexical.createEditor({namespace:'Slash', nodes:NOTES_NODES, theme:NOTES_THEME, onError(error) {throw error;}});
  registerRichText(editor); registerList(editor); registerCheckList(editor);
  callouts.registerCallouts(editor); containers.registerContainers(editor);
  editor.update(() => {
    $convertFromMarkdownString(markdown, transformers);
    // Put the caret at the end of the last text (or `offset` characters into it).
    const text = lexical.$getRoot().getLastDescendant();
    if (lexical.$isTextNode(text)) text.select(offset ?? text.getTextContentSize(), offset ?? text.getTextContentSize());
    else text.selectEnd();
  }, {discrete:true});
  const out = () => {let value; editor.getEditorState().read(() => {value = $convertToMarkdownString(transformers);}); return value;};
  const shape = () => {let value; editor.getEditorState().read(() => {
    value = lexical.$getRoot().getChildren().map(node => node.getType() + (node.getListType ? `:${node.getListType()}` : ''));}); return value;};
  return {editor, slash, out, shape, lexical};
}
const choose = async (markdown, id, extra) => {
  const ctx = await load(markdown);
  const item = ctx.slash.SLASH_ITEMS.find(entry => entry.id === id);
  ctx.editor.update(() => ctx.slash.$applySlashItem(ctx.editor, item, extra), {discrete:true});
  return ctx;
};

test('slashMatch opens at the start of a line or after a space only', async () => {
  const {slash} = await load('');
  assert.deepEqual(slash.slashMatch('/'), {query:'', start:0});
  assert.deepEqual(slash.slashMatch('/cal'), {query:'cal', start:0});
  assert.deepEqual(slash.slashMatch('see /cal'), {query:'cal', start:4});
  assert.deepEqual(slash.slashMatch('/heading 1'), {query:'heading 1', start:0});
  assert.equal(slash.slashMatch('and/or'), null);
  assert.equal(slash.slashMatch('https://example.com'), null);
  assert.equal(slash.slashMatch('/ spaced'), null);
  assert.equal(slash.slashMatch('plain'), null);
});

test('the menu lists every block in the order of the ticket', async () => {
  const {slash} = await load('');
  assert.deepEqual(slash.SLASH_ITEMS.map(item => item.label), ['Text', 'Heading 1', 'Heading 2', 'Heading 3',
    'Bulleted list', 'Numbered list', 'To-do', 'Quote', 'Callout', 'Toggle', 'Code', 'Divider', 'Table', '2 columns',
    'Image', 'Video', 'Link card']);
  assert.ok(slash.SLASH_ITEMS.every(item => item.icon && item.description));
  const shortcuts = Object.fromEntries(slash.SLASH_ITEMS.map(item => [item.id, item.shortcut]));
  for (const [id, shortcut] of Object.entries({h1:'#', bullets:'-', numbers:'1.', todo:'[]', quote:'>', code:'```', divider:'---'})) {
    assert.equal(shortcuts[id], shortcut);
  }
});

test('typing filters the menu', async () => {
  const {slash} = await load('');
  const labels = query => slash.filterItems(query).map(item => item.label);
  assert.equal(slash.filterItems('').length, slash.SLASH_ITEMS.length);
  assert.deepEqual(labels('cal'), ['Callout']);
  assert.deepEqual(labels('HEAD'), ['Heading 1', 'Heading 2', 'Heading 3']);
  assert.deepEqual(labels('heading 2'), ['Heading 2']);
  assert.deepEqual(labels('todo'), ['To-do']);
  assert.deepEqual(labels('youtube'), ['Video']);
  assert.deepEqual(labels('zzz'), []);
  const noImage = slash.SLASH_ITEMS.filter(item => item.id !== 'image');
  assert.deepEqual(slash.filterItems('img', noImage), []);
});

test('arrow keys step through the rows and wrap', async () => {
  const {slash} = await load('');
  assert.equal(slash.stepIndex(0, 1, 3), 1);
  assert.equal(slash.stepIndex(2, 1, 3), 0);
  assert.equal(slash.stepIndex(0, -1, 3), 2);
  assert.equal(slash.stepIndex(0, 1, 0), -1);
});

test('the menu sits below the caret and flips above near the bottom edge', async () => {
  const {slash} = await load('');
  const viewport = {width:1440, height:900};
  const size = {width:320, height:400};
  const below = slash.placeMenu({left:100, top:100, bottom:120}, size, viewport);
  assert.equal(below.flipped, false); assert.equal(below.top, 126);
  const flipped = slash.placeMenu({left:100, top:760, bottom:780}, size, viewport);
  assert.equal(flipped.flipped, true); assert.equal(flipped.top, 760 - 6 - 400);
  const phone = slash.placeMenu({left:300, top:200, bottom:220}, size, {width:390, height:700});
  assert.equal(phone.width, 320); assert.equal(phone.left, 390 - 320 - 8);
  const narrow = slash.placeMenu({left:10, top:200, bottom:220}, size, {width:300, height:700});
  assert.equal(narrow.width, 284);
  const short = slash.placeMenu({left:10, top:300, bottom:320}, size, {width:390, height:700});
  assert.ok(short.maxHeight >= 120);
});

const cases = [
  ['text', '/te', 'plain?', null],
  ['h1', '/h1', '# ', null], ['h2', '/h2', '## ', null], ['h3', '/h3', '### ', null],
  ['bullets', '/bul', '- ', null], ['numbers', '/num', '1. ', null],
  ['quote', '/quo', '> ', null],
];
const blockTypes = {h1:'heading', h2:'heading', h3:'heading', bullets:'list:bullet', numbers:'list:number', quote:'quote'};
for (const [id, typed] of cases.filter(entry => entry[0] !== 'text')) {
  test(`${id} turns the line into its block`, async () => {
    const ctx = await choose(typed, id);
    assert.deepEqual(ctx.shape(), [blockTypes[id]]);
    assert.ok(!ctx.out().includes('/'), ctx.out());
  });
}
test('text turns a heading back into a paragraph', async () => {
  assert.deepEqual((await choose('# /te', 'text')).shape(), ['paragraph']);
});
test('to-do makes a check item and drops the slash', async () => {
  assert.deepEqual((await choose('/to', 'todo')).shape(), ['list:check']);
});
test('code makes a code block', async () => {
  assert.deepEqual((await choose('/co', 'code')).shape(), ['code']);
});
test('divider replaces an empty line and leaves a line to type on', async () => {
  const ctx = await choose('before\n\n/di', 'divider');
  assert.deepEqual(ctx.shape(), ['paragraph', 'horizontalrule', 'paragraph']);
  assert.equal(ctx.out(), 'before\n\n---\n');
});
test('callout wraps the line as a note', async () => {
  const markdown = (await choose('/cal', 'callout')).out();
  assert.match(markdown, /^> \[!note\]/);
  assert.ok(!markdown.includes('/cal'));
});
test('toggle inserts a details block', async () => {
  const markdown = (await choose('/tog', 'toggle')).out();
  assert.match(markdown, /^<details>\n<summary>/);
});
test('table inserts a two by two table', async () => {
  const ctx = await choose('/tab', 'table');
  assert.deepEqual(ctx.shape(), ['table', 'paragraph']);
  assert.match(ctx.out(), /^\| {3}\| {3}\|\n\| --- \| --- \|\n\| {3}\| {3}\|\n$/);
});
test('2 columns inserts two columns', async () => {
  const markdown = (await choose('/2', 'columns')).out();
  assert.match(markdown, /^<columns>\n<column>/);
  assert.equal((markdown.match(/<column>/g) || []).length, 2);
});
test('link card and video use the address they were given', async () => {
  assert.match((await choose('/link', 'bookmark', {url:'https://example.com/a'})).out(), /<bookmark url="https:\/\/example.com\/a">/);
  assert.match((await choose('/vid', 'video', {url:'https://youtu.be/dQw4w9WgXcQ'})).out(), /^!\[\]\(https:\/\/youtu\.be\/dQw4w9WgXcQ\)/);
});
test('image removes the slash text and leaves the caret for the picker', async () => {
  const ctx = await choose('/ima', 'image');
  assert.deepEqual(ctx.shape(), ['paragraph']);
  assert.equal(ctx.out(), '');
});
test('text before the slash stays, and a slash after a space is removed alone', async () => {
  const markdown = (await choose('buy milk /cal', 'callout')).out();
  assert.ok(markdown.includes('buy milk'), markdown);
  assert.ok(!markdown.includes('/cal'), markdown);
});
