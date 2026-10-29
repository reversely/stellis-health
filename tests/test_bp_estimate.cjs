/**
 * @fileoverview Tests for the camera blood pressure estimate: the JavaScript
 * network matches the numpy reference, and the window needs 7 unbroken seconds.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const bp = require('../src/stellis/static/bp-estimate.js');

const vendor = path.join(__dirname, '../src/stellis/static/vendor/bp-lstm');
const weights = bp.tensors(
    fs.readFileSync(path.join(vendor, 'weights.bin')).buffer.slice(0),
    JSON.parse(fs.readFileSync(path.join(vendor, 'manifest.json'))));
const reference = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/bp_reference.json')));

test('matches the numpy reference on published camera windows', () => {
  reference.inputs.forEach((row, i) => {
    const x = Float32Array.from(row);
    const mean = x.reduce((a, b) => a + b) / x.length;
    const spread = Math.sqrt(x.reduce((a, b) => a + (b - mean) ** 2, 0) / x.length);
    const {systolic, diastolic} = bp.predict(weights, x.map(v => (v - mean) / spread));
    assert.ok(Math.abs(systolic - reference.expected[i][0]) < 0.05, `systolic ${systolic}`);
    assert.ok(Math.abs(diastolic - reference.expected[i][1]) < 0.05, `diastolic ${diastolic}`);
  });
});

test('takes the last unbroken 7 seconds at 125 Hz with unit spread', () => {
  const wave = t => ({t, v: Math.sin(2 * Math.PI * 1.2 * t)});
  const camera = Array.from({length: 300}, (_, i) => wave(i / 30));
  const x = bp.window7s(camera);
  assert.equal(x.length, 875);
  const mean = x.reduce((a, b) => a + b) / x.length;
  assert.ok(Math.abs(mean) < 1e-4);
  assert.ok(Math.abs(Math.sqrt(x.reduce((a, b) => a + (b - mean) ** 2, 0) / x.length) - 1) < 1e-3);
});

test('a pause breaks the window', () => {
  const before = Array.from({length: 150}, (_, i) => ({t: i / 30, v: Math.sin(i)}));
  const after = Array.from({length: 150}, (_, i) => ({t: 10 + i / 30, v: Math.sin(i)}));
  assert.equal(bp.window7s([...before, ...after]), null);
  assert.equal(bp.window7s([]), null);
});
