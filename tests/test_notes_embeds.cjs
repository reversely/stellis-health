// Link cards, video embeds and external images round-trip through the notes Markdown dialect (#281, #282).
const test = require('node:test');
const assert = require('node:assert/strict');

async function load(markdown) {
  const lexical = await import('lexical');
  const {$convertFromMarkdownString, $convertToMarkdownString} = await import('@lexical/markdown');
  const {NOTES_NODES, NOTES_THEME, notesTransformers} = await import('../src/stellis/static/notes-editor.js');
  const embeds = await import('../src/stellis/static/notes-embeds.js');
  const transformers = notesTransformers();
  const editor = lexical.createEditor({namespace:'Embeds', nodes:NOTES_NODES, theme:NOTES_THEME, onError(error) {throw error;}});
  editor.update(() => $convertFromMarkdownString(markdown, transformers), {discrete:true});
  const read = fn => {let value; editor.getEditorState().read(() => {value = fn();}); return value;};
  const types = () => read(() => lexical.$getRoot().getChildren().map(node => node.getType()));
  return {editor, embeds, read, types, markdown: () => read(() => $convertToMarkdownString(transformers))};
}
const same = async markdown => assert.equal((await load(markdown)).markdown(), markdown);

test('a link card with and without a caption', async () => {
  await same('<bookmark url="https://example.com/a?x=1&y=2">Example page</bookmark>');
  await same('<bookmark url="https://example.com/"></bookmark>');
  assert.deepEqual((await load('<bookmark url="https://example.com/a">x</bookmark>')).types(), ['bookmark']);
});
test('link card captions are escaped on export and read back', async () => {
  const {editor, embeds, markdown} = await load('before');
  const {$getRoot} = await import('lexical');
  editor.update(() => $getRoot().append(new embeds.BookmarkNode('https://example.com/a', 'A <b> & "c"')), {discrete:true});
  assert.equal(markdown(), 'before\n\n<bookmark url="https://example.com/a">A &lt;b&gt; &amp; "c"</bookmark>');
  await same('<bookmark url="https://example.com/a">A &lt;b&gt; &amp; "c"</bookmark>');
});
test('a link card needs an http or https address', async () => {
  for (const line of ['<bookmark url="javascript:alert(1)">x</bookmark>', '<bookmark url="ftp://example.com/">x</bookmark>',
    '<bookmark url="">x</bookmark>']) {
    const {types, markdown} = await load(line);
    assert.deepEqual(types(), ['paragraph'], line);
    assert.ok(markdown().includes('bookmark'), line);
  }
});
test('an address with quotes or angle brackets is exported percent-encoded', async () => {
  const {editor, embeds, markdown} = await load('x');
  const {$getRoot} = await import('lexical');
  editor.update(() => $getRoot().append(new embeds.BookmarkNode('https://example.com/a"b?c=<d>', '')), {discrete:true});
  assert.match(markdown(), /<bookmark url="https:\/\/example\.com\/a%22b\?c=%3Cd%3E"><\/bookmark>$/);
});

test('YouTube, Vimeo and Loom lines become embeds and round-trip', async () => {
  for (const line of ['![Demo](https://www.youtube.com/watch?v=dQw4w9WgXcQ)', '![](https://youtu.be/dQw4w9WgXcQ)',
    '![Talk](https://vimeo.com/76979871)', '![Walkthrough](https://www.loom.com/share/0123456789abcdef0123456789abcdef)',
    '![N](https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ)']) {
    const loaded = await load(line);
    assert.deepEqual(loaded.types(), ['video-embed'], line);
    assert.equal(loaded.markdown(), line);
  }
});
test('embed URLs use each host\'s embed address', async () => {
  const {embeds} = await load('');
  const cases = {
    'https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=90': 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?start=90',
    'https://youtu.be/dQw4w9WgXcQ?t=1m30s': 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?start=90',
    'https://www.youtube.com/shorts/dQw4w9WgXcQ': 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ',
    'https://vimeo.com/76979871': 'https://player.vimeo.com/video/76979871',
    'https://vimeo.com/76979871/abcdef1234': 'https://player.vimeo.com/video/76979871?h=abcdef1234',
    'https://player.vimeo.com/video/76979871': 'https://player.vimeo.com/video/76979871',
    'https://www.loom.com/share/0123456789abcdef0123456789abcdef': 'https://www.loom.com/embed/0123456789abcdef0123456789abcdef',
  };
  for (const [url, src] of Object.entries(cases)) assert.equal(embeds.videoEmbed(url)?.src, src, url);
});
test('other hosts and malformed video links are not embedded', async () => {
  const {embeds} = await load('');
  for (const url of ['https://evil.example/watch?v=dQw4w9WgXcQ', 'https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ',
    'https://www.youtube.com/channel/UC1', 'https://vimeo.com/user', 'https://www.loom.com/share/short', 'javascript:alert(1)',
    'https://example.com/clip.mp4']) assert.equal(embeds.videoEmbed(url), null, url);
});
test('a video host without a usable video stays a plain line', async () => {
  const loaded = await load('![x](https://www.youtube.com/channel/UC1)');
  assert.deepEqual(loaded.types(), ['paragraph']);
  assert.ok(loaded.markdown().includes('youtube.com/channel/UC1'));
});

test('external images render as image blocks and round-trip', async () => {
  for (const line of ['![A photo](https://example.com/photo.png)', '![](http://example.com/a.jpg?w=2)']) {
    const loaded = await load(line);
    assert.deepEqual(loaded.types(), ['external-image'], line);
    assert.equal(loaded.markdown(), line);
  }
});
test('stellis images, other schemes and mid-line images are untouched', async () => {
  const id = '123e4567-e89b-12d3-a456-426614174000';
  assert.deepEqual((await load(`![s](stellis-image:${id})`)).types(), ['meeting-image']);
  assert.deepEqual((await load('![x](ftp://example.com/a.png)')).types(), ['paragraph']);
  assert.deepEqual((await load('see ![x](https://example.com/a.png) here')).types(), ['paragraph']);
});

test('blocks sit between other content and survive JSON for copy and paste', async () => {
  const markdown = 'Intro\n\n<bookmark url="https://example.com/a">A</bookmark>\n\n![v](https://youtu.be/dQw4w9WgXcQ)\n\n![i](https://example.com/a.webp)\n\nOutro';
  const {editor, markdown: out} = await load(markdown);
  assert.equal(out(), markdown);
  editor.setEditorState(editor.parseEditorState(JSON.stringify(editor.getEditorState().toJSON())));
  assert.equal(out(), markdown);
});
test('a link card inside a callout keeps its place', () => same('> [!note] Read\n> <bookmark url="https://example.com/a">A</bookmark>'));

test('image and paste helpers', async () => {
  const {embeds} = await load('');
  assert.ok(embeds.isImageUrl('https://example.com/a/b.PNG?x=1'));
  assert.ok(!embeds.isImageUrl('https://example.com/page'));
  assert.equal(embeds.httpUrl('https://example.com/a b'), null);
  assert.equal(embeds.httpUrl('mailto:a@b.c'), null);
});
