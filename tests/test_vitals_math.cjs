/**
 * @fileoverview Tests for the on-device vital sign formulas, driven by
 * synthetic camera signals with known pulse, breathing and beat timing.
 */
const assert = require('node:assert/strict');
const test = require('node:test');

const vitals = require('../src/stellis/static/vitals-math.js');

/**
 * Makes a camera-like recording: a pulse waveform whose rate and height
 * follow breathing, sampled at an uneven 22 to 30 per second, and the face's
 * up and down movement.
 * @param {{bpm: number, breaths: number, seconds: number, noise: number,
 *     sway: number}} options sway is the pulse's swing with each breath, in
 *     beats a minute.
 * @return {{wave: !Array, motion: !Array, beats: !Array<number>}} beats are
 *     the true peak times.
 */
function recording({bpm, breaths, seconds, noise, sway}) {
  let seed = 11;
  const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const breath = (t) => Math.sin((2 * Math.PI * breaths * t) / 60);
  const wave = [];
  const motion = [];
  const beats = [];
  let phase = 0;
  let t = 0;
  const step = 1 / 1000;
  // Integrates the instantaneous rate finely so beat times are exact.
  for (let next = 0; t < seconds; t += step) {
    const before = phase;
    phase += ((bpm + sway * breath(t)) / 60) * step;
    if (Math.floor(before - 0.25) !== Math.floor(phase - 0.25)) {
      beats.push(t);
    }
    if (t >= next) {
      const height = 1 + 0.25 * breath(t);
      wave.push({
        t,
        v: height * Math.sin(2 * Math.PI * phase) + noise * (random() - 0.5),
      });
      motion.push({t, v: 0.46 + 0.004 * breath(t) + 0.001 * (random() - 0.5)});
      next = t + 1 / 30 + random() * (1 / 22 - 1 / 30);
    }
  }
  return {wave, motion, beats};
}

/** RMSSD in milliseconds from true beat times. */
function trueRmssd(times) {
  const intervals = times.slice(1).map((t, i) => (t - times[i]) * 1000);
  const differences = intervals.slice(1).map((ms, i) => ms - intervals[i]);
  return Math.sqrt(
      differences.reduce((sum, d) => sum + d * d, 0) / differences.length);
}

test('beats are found at their true times', () => {
  const {wave, beats} =
      recording({bpm: 66, breaths: 12, seconds: 40, noise: 0.1, sway: 4});
  const found = vitals.findBeats(wave, 66);
  assert.ok(Math.abs(found.length - (beats.length - 1)) <= 1);
  const errors =
      found.map((beat) => Math.min(...beats.map((t) => Math.abs(t - beat.t))));
  assert.ok(Math.max(...errors) < 0.02, `largest error ${Math.max(...errors)}`);
});

test('HRV matches the true beat intervals', () => {
  const {wave, beats} =
      recording({bpm: 66, breaths: 12, seconds: 60, noise: 0.1, sway: 6});
  const result = vitals.heartRateVariability(vitals.findBeats(wave, 66));
  const truth = trueRmssd(beats);
  assert.ok(result.sure);
  assert.ok(
      Math.abs(result.rmssd - truth) < 0.25 * truth,
      `rmssd ${result.rmssd} against ${truth.toFixed(1)}`);
});

test('a missed beat is left out of HRV', () => {
  const beats = [];
  for (let i = 0; i < 40; i++) beats.push({t: i * 0.9 + (i % 2) * 0.02});
  const clean = vitals.heartRateVariability(beats);
  const missed = vitals.heartRateVariability(beats.filter((_, i) => i !== 20));
  assert.ok(Math.abs(missed.rmssd - clean.rmssd) <= 2);
  assert.equal(vitals.heartRateVariability(beats.slice(0, 10)), null);
});

test('breathing rate agrees across beat height, timing and movement', () => {
  for (const breaths of [9, 15, 22]) {
    const {wave, motion} =
        recording({bpm: 72, breaths, seconds: 45, noise: 0.15, sway: 5});
    const result = vitals.breathingRate(vitals.findBeats(wave, 72), motion);
    assert.ok(
        Math.abs(result.rate - breaths) <= 1,
        `${result.rate} against ${breaths}`);
    assert.ok(result.sure);
    assert.ok(result.sources.length >= 2);
  }
});

test('breathing rate needs 20 seconds of signal', () => {
  const {wave, motion} =
      recording({bpm: 72, breaths: 15, seconds: 15, noise: 0.1, sway: 5});
  assert.equal(vitals.breathingRate(vitals.findBeats(wave, 72), motion), null);
});

test('the pulse is the strongest rhythm', () => {
  const {wave} =
      recording({bpm: 58, breaths: 12, seconds: 20, noise: 0.5, sway: 2});
  assert.ok(Math.abs(vitals.pulse(wave).bpm - 58) <= 1);
  assert.equal(vitals.pulse(wave.slice(0, 100)), null);
});


test('isolated accepted intervals do not manufacture zero RMSSD', () => {
  const beats = [{t: 0}];
  for (let i = 0; i < 41; i++) beats.push({t: beats.at(-1).t + (i % 2 ? 1.6 : .8)});
  assert.equal(vitals.heartRateVariability(beats), null);
});


test('a slow respiratory peak needs enough cycles before it is accepted', () => {
  const {wave, motion} = recording({bpm:72, breaths:6, seconds:45, noise:0.1, sway:5});
  const result = vitals.breathingRate(vitals.findBeats(wave,72),motion);
  assert.ok(result && !result.sure);
  const longer = recording({bpm:72, breaths:6, seconds:65, noise:0.1, sway:5});
  assert.ok(vitals.breathingRate(vitals.findBeats(longer.wave,72),longer.motion).sure);
});


test('sampling artifacts in an unmodulated pulse do not become breathing', () => {
  for (const step of [.033,1/30,1/25]) {
    const wave=Array.from({length:Math.ceil(45/step)},(_,i)=>({t:i*step,v:Math.sin(2*Math.PI*1.2*i*step)}));
    const result=vitals.breathingRate(vitals.findBeats(wave,72),[]);
    assert.ok(!result || !result.sure, `false respiratory rhythm at ${1/step} fps`);
  }
});
