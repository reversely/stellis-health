/**
 * @fileoverview A blood pressure estimate from the camera pulse waveform,
 * computed on the device.
 *
 * The network is the LSTM of Schrumpf et al., "Assessment of Non-Invasive
 * Blood Pressure Prediction from PPG and rPPG Signals Using Deep Learning",
 * Sensors 2021, 21(18):6022, with its PPG-trained weights (CC BY 4.0). Its
 * output layer was refitted on the paper's published camera (rPPG) recordings
 * with each 7 s window scaled to unit spread; vendor/bp-lstm/README.md has the
 * method and the measured error. The estimate averages the waveform and its
 * mirror image, since a camera waveform's sign depends on the method.
 */
(function(root) {
'use strict';

const BASE = '/static/vendor/bp-lstm/';
const RATE_HZ = 125;
const SAMPLES = 875;
/** A pause in the waveform longer than this, in seconds, breaks a window. */
const MAX_GAP_S = 0.5;

/**
 * The last unbroken 7 s of waveform, resampled to 125 Hz and scaled to zero
 * mean and unit spread.
 * @param {!Array<{t: number, v: number}>} recording t is in seconds.
 * @return {?Float32Array} Null when no unbroken 7 s exist.
 */
function window7s(recording) {
  const span = (SAMPLES - 1) / RATE_HZ;
  let start = recording.length - 1;
  while (start > 0 && recording[start].t - recording[start - 1].t <= MAX_GAP_S &&
         recording[recording.length - 1].t - recording[start].t < span) start--;
  const end = recording[recording.length - 1];
  if (!end || end.t - recording[start].t < span) return null;
  const x = new Float32Array(SAMPLES);
  let j = start;
  for (let i = 0; i < SAMPLES; i++) {
    const t = end.t - span + i / RATE_HZ;
    while (j < recording.length - 2 && recording[j + 1].t < t) j++;
    const a = recording[j], b = recording[j + 1];
    const share = b.t > a.t ? Math.min(1, Math.max(0, (t - a.t) / (b.t - a.t))) : 0;
    x[i] = a.v + (b.v - a.v) * share;
  }
  let mean = 0, spread = 0;
  for (const v of x) mean += v / SAMPLES;
  for (const v of x) spread += (v - mean) ** 2 / SAMPLES;
  spread = Math.sqrt(spread);
  if (!(spread > 0)) return null;
  for (let i = 0; i < SAMPLES; i++) x[i] = (x[i] - mean) / spread;
  return x;
}

/**
 * Splits the weight file into named tensors.
 * @param {!ArrayBuffer} buffer
 * @param {!Object} manifest
 * @return {!Object<string, !Float32Array>}
 */
function tensors(buffer, manifest) {
  const all = new Float32Array(buffer);
  const out = {};
  for (const [name, {offset, shape}] of Object.entries(manifest.tensors)) {
    out[name] = all.subarray(offset, offset + shape.reduce((a, b) => a * b, 1));
  }
  return out;
}

const sigmoid = z => 1 / (1 + Math.exp(-z));

/**
 * One LSTM direction (Keras gate order i, f, c, o).
 * @param {!Float32Array} x Steps × inputs.
 * @param {number} steps
 * @param {number} inputs
 * @param {!Float32Array} kernel Inputs × 4 units.
 * @param {!Float32Array} recurrent Units × 4 units.
 * @param {!Float32Array} bias 4 units.
 * @param {boolean} backward Reads the steps last to first.
 * @param {?Float32Array} out Steps × 2 units, written at column offset; null
 *     returns only the final state.
 * @param {number} column
 * @return {!Float32Array} The final hidden state.
 */
function lstm(x, steps, inputs, kernel, recurrent, bias, backward, out, column) {
  const units = bias.length / 4, width = 4 * units;
  const h = new Float32Array(units), c = new Float32Array(units), z = new Float32Array(width);
  for (let n = 0; n < steps; n++) {
    const s = backward ? steps - 1 - n : n;
    z.set(bias);
    for (let i = 0; i < inputs; i++) {
      const v = x[s * inputs + i];
      if (v === 0) continue;
      const row = i * width;
      for (let k = 0; k < width; k++) z[k] += v * kernel[row + k];
    }
    for (let i = 0; i < units; i++) {
      const v = h[i], row = i * width;
      for (let k = 0; k < width; k++) z[k] += v * recurrent[row + k];
    }
    for (let k = 0; k < units; k++) {
      c[k] = sigmoid(z[units + k]) * c[k] + sigmoid(z[k]) * Math.tanh(z[2 * units + k]);
      h[k] = sigmoid(z[3 * units + k]) * Math.tanh(c[k]);
    }
    if (out) out.set(h, s * 2 * units + column);
  }
  return h;
}

/**
 * The network's 64 features for one window.
 * @param {!Object<string, !Float32Array>} w
 * @param {!Float32Array} x 875 samples.
 * @return {!Float32Array}
 */
function features(w, x) {
  const filters = w['conv.bias'].length, kernel = w['conv.kernel'];
  let y = new Float32Array(SAMPLES * filters), inputs = filters;
  for (let t = 0; t < SAMPLES; t++) {
    for (let f = 0; f < filters; f++) {
      let v = w['conv.bias'][f];
      // Causal: tap j reads the sample 4 - j steps back.
      for (let j = 0; j < 5; j++) {
        const s = t - 4 + j;
        if (s >= 0) v += x[s] * kernel[j * filters + f];
      }
      y[t * filters + f] = Math.max(0, v);
    }
  }
  for (const layer of ['l0', 'l1']) {
    const units = w[`${layer}.forward.bias`].length / 4;
    const out = new Float32Array(SAMPLES * 2 * units);
    for (const [d, backward, column] of [['forward', false, 0], ['backward', true, units]]) {
      lstm(y, SAMPLES, inputs, w[`${layer}.${d}.kernel`], w[`${layer}.${d}.recurrent`],
           w[`${layer}.${d}.bias`], backward, out, column);
    }
    y = out;
    inputs = 2 * units;
  }
  const last = (d, backward) => lstm(y, SAMPLES, inputs, w[`l2.${d}.kernel`],
      w[`l2.${d}.recurrent`], w[`l2.${d}.bias`], backward, null, 0);
  const forward = last('forward', false), backward = last('backward', true);
  const result = new Float32Array(forward.length * 2);
  result.set(forward);
  result.set(backward, forward.length);
  return result;
}

/**
 * Systolic and diastolic pressure for one window, in mmHg.
 * @param {!Object<string, !Float32Array>} w
 * @param {!Float32Array} x 875 samples scaled to unit spread.
 * @return {{systolic: number, diastolic: number}}
 */
function predict(w, x) {
  const a = features(w, x), b = features(w, x.map(v => -v));
  const head = w.head, size = a.length;
  const value = column => {
    let v = head[size * 2 + column];
    for (let i = 0; i < size; i++) v += (a[i] + b[i]) / 2 * head[i * 2 + column];
    return v;
  };
  return {systolic: value(0), diastolic: value(1)};
}

let loading = null;

/**
 * The estimate for a finished recording, rounded to whole mmHg.
 * @param {!Array<{t: number, v: number}>} recording
 * @return {!Promise<?{systolic: number, diastolic: number}>} Null when the
 *     recording has no unbroken 7 s.
 */
async function estimate(recording) {
  const x = window7s(recording);
  if (!x) return null;
  loading ||= Promise.all([
    fetch(BASE + 'weights.bin').then(r => r.arrayBuffer()),
    fetch(BASE + 'manifest.json').then(r => r.json()),
  ]).then(([buffer, manifest]) => tensors(buffer, manifest));
  const {systolic, diastolic} = predict(await loading, x);
  return {systolic: Math.round(systolic), diastolic: Math.round(diastolic)};
}

/** Where the method and its error are published. */
const SOURCES = Object.freeze({
  paper: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC8472879/',
  weights: 'https://doi.org/10.5281/zenodo.5590603',
});

const exported = {SOURCES, estimate, predict, tensors, window7s};
root.StellisBpEstimate = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
