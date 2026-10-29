// Transcript filler cleanup
const test = require('node:test');
const assert = require('node:assert/strict');
const {fillerCleanup, cleanedText} = require('../src/stellis/shared/transcript-fillers.js');

test('removes fillers and repairs the seams they leave', () => {
  assert.equal(cleanedText('So we, uh, decided to ship it.'), 'So we, decided to ship it.');
  assert.equal(cleanedText('That is right? Uh, for example the valve.'), 'That is right? For example the valve.');
  assert.equal(cleanedText('Um, the RFI is still open.'), 'The RFI is still open.');
  assert.equal(cleanedText('I was just, uh, um, fixing it.'), 'I was just, fixing it.');
  assert.equal(cleanedText('Hmm I think so'), 'I think so');
  assert.equal(cleanedText('We should, ah, wait'), 'We should, wait');
});

test('a filler before a dash goes, hyphenated words stay', () => {
  assert.equal(cleanedText("if you don't have, um-- the drawings"), "if you don't have, -- the drawings");
  assert.equal(cleanedText('Uh-huh.'), 'Uh-huh.');
  assert.equal(cleanedText('Mm-hmm.'), 'Mm-hmm.');
});

test('a word said three or more times is shown once, twice is kept', () => {
  assert.equal(cleanedText('I I I I think so'), 'I think so');
  assert.equal(cleanedText('it is very very late'), 'it is very very late');
});

test('words that only look like fillers stay', () => {
  for (const text of ['The screw is 5 mm long.', 'We flew to Ha Long Bay.', 'Ahmed and Umma joined.',
    "Uhura's line", 'The hum of the pump.', 'See e.g. the um-brella term.']) {
    assert.equal(cleanedText(text), text);
  }
});

test('text without fillers is untouched', () => {
  const text = 'Can you send me the report by Friday? e.g. tomorrow.';
  assert.deepEqual(fillerCleanup(text), {hidden:[], upper:[]});
  assert.equal(cleanedText(text), text);
});

test('a passage that is only filler stays as spoken', () => {
  assert.equal(cleanedText('Um.'), 'Um.');
  assert.equal(cleanedText('uh, hmm'), 'uh, hmm');
});

test('ranges point into the original text, so offsets stay valid', () => {
  const text = 'Right? Uh, the deadline moved.';
  const {hidden, upper} = fillerCleanup(text);
  assert.deepEqual(hidden, [[7, 11]]);
  assert.equal(text.slice(7, 11), 'Uh, ');
  assert.deepEqual(upper, [11]);
  assert.equal(text[11], 't');
});
