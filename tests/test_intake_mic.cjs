const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
    require.resolve('../src/stellis/static/intake-mic.worklet.js'), 'utf8');

/** Runs a tone through the worklet at an input rate; returns its 16 kHz output. */
function capture(rate, hertz, seconds = 0.5) {
  let Mic;
  const posted = [];
  const context = {
    sampleRate: rate,
    AudioWorkletProcessor: class {
      constructor() {
        this.port = {postMessage: (data) => posted.push(data)};
      }
    },
    registerProcessor: (name, type) => {
      Mic = type;
    },
    Math,
    Float32Array,
    Int16Array,
  };
  vm.runInNewContext(SOURCE, context);
  const mic = new Mic();
  const total = Math.round(rate * seconds);
  for (let start = 0; start < total; start += 128) {
    const block = new Float32Array(Math.min(128, total - start));
    for (let i = 0; i < block.length; i++) {
      block[i] = 0.5 * Math.sin(2 * Math.PI * hertz * (start + i) / rate);
    }
    mic.process([[block]]);
  }
  return posted.flatMap((data) => [...new Int16Array(data.pcm)].map((v) => v / 32767));
}

const level = (samples) =>
    Math.sqrt(samples.slice(400).reduce((sum, v) => sum + v * v, 0) / (samples.length - 400));

test('speech-band sound passes at 16 kHz from 48 kHz and 44.1 kHz input', () => {
  for (const rate of [48000, 44100]) {
    const out = capture(rate, 1000);
    assert.ok(Math.abs(out.length - 8000) <= 320, `${rate}: ${out.length} samples`);
    assert.ok(Math.abs(level(out) - 0.5 / Math.SQRT2) < 0.02, `${rate}: ${level(out)}`);
  }
});

test('sound above 8 kHz is filtered out rather than folded into speech', () => {
  for (const rate of [48000, 44100]) {
    assert.ok(level(capture(rate, 12000)) < 0.005, `${rate}: ${level(capture(rate, 12000))}`);
  }
});
