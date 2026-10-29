const test = require('node:test');
const assert = require('node:assert/strict');
const fixture = require('./fixtures/markdown_plain.json');
const {plainMarkdown} = require('../src/stellis/shared/plain-markdown.js');

for (const item of fixture.plain_markdown) test(`plain markdown: ${item.name}`, () => assert.equal(plainMarkdown(item.input), item.expected));
