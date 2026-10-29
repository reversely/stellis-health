/**
 * @fileoverview Vital signs computed on the device from a camera pulse
 * waveform and the face box. Every function is pure, so tests drive them
 * with synthetic signals.
 *
 * - Pulse: the strongest rhythm between 40 and 180 per minute.
 * - Heart rate variability: RMSSD and SDNN of the intervals between beats.
 * - Breathing rate: the strongest rhythm between 6 and 30 per minute in three
 *   signals that breathing modulates: each beat's height, the interval
 *   between beats (respiratory sinus arrhythmia) and the face's up and down
 *   movement. Estimates that agree within 2 breaths a minute are combined.
 */
(function(root) {
'use strict';

const PULSE_RANGE = [40, 180];
const BREATHING_RANGE = [6, 30];
/** A beat interval this far from the median is treated as a missed beat. */
const MAX_INTERVAL_DEVIATION = 0.25;
const MIN_INTERVALS = 15;
/** Breathing estimates closer than this, per minute, agree. */
const AGREEMENT = 2;
/** The smallest share of power near the peak for a sure breathing rate. */
const MIN_BREATHING_SHARE = 0.35;
/** The smallest share of accepted intervals for a sure HRV. */
const MIN_ACCEPTED_SHARE = 0.8;
const RESAMPLE_HZ = 100;
/** Raw samples within this share of a beat of a peak shape its fitted top. */
const PEAK_WINDOW = 0.25;

/**
 * Finds the strongest rhythm in an unevenly sampled series. Removes the
 * series' mean and linear trend, applies a Hann window by time and scans a
 * Fourier transform that uses each sample's own time.
 * @param {!Array<{t: number, v: number}>} points t is in seconds.
 * @param {!Array<number>} range The lowest and highest rate, per minute.
 * @param {number} step The scan step, per minute.
 * @param {number} near Rates within this of the peak count toward its share.
 * @param {number} minSeconds The shortest series that gives a result.
 * @return {?{rate: number, share: number}} share is the part of the power in
 *     the range that lies near the peak; null for a short series.
 */
function rhythm(points, range, step, near, minSeconds) {
  const n = points.length;
  if (n < 4 || points[n - 1].t - points[0].t < minSeconds) {
    return null;
  }
  const start = points[0].t;
  const span = points[n - 1].t - start;
  const detrended = detrend(points);
  const windowed = detrended.map(
      (value, i) => value *
          (0.5 - 0.5 * Math.cos((2 * Math.PI * (points[i].t - start)) / span)));
  const powers = [];
  for (let rate = range[0]; rate <= range[1] + 1e-9; rate += step) {
    const omega = (2 * Math.PI * rate) / 60;
    let re = 0;
    let im = 0;
    for (let i = 0; i < n; i++) {
      const phase = omega * (points[i].t - start);
      re += windowed[i] * Math.cos(phase);
      im -= windowed[i] * Math.sin(phase);
    }
    powers.push({rate, power: re * re + im * im});
  }
  const peak =
      powers.reduce((best, item) => item.power > best.power ? item : best);
  const total = powers.reduce((sum, item) => sum + item.power, 0);
  const close = powers.filter((item) => Math.abs(item.rate - peak.rate) <= near)
                    .reduce((sum, item) => sum + item.power, 0);
  return {rate: peak.rate, share: total ? close / total : 0};
}

/**
 * Removes a series' least-squares line.
 * @param {!Array<{t: number, v: number}>} points
 * @return {!Array<number>} The residual for each point.
 */
function detrend(points) {
  const n = points.length;
  const meanT = points.reduce((sum, p) => sum + p.t, 0) / n;
  const meanV = points.reduce((sum, p) => sum + p.v, 0) / n;
  let covariance = 0;
  let variance = 0;
  for (const p of points) {
    covariance += (p.t - meanT) * (p.v - meanV);
    variance += (p.t - meanT) ** 2;
  }
  const slope = variance ? covariance / variance : 0;
  return points.map((p) => p.v - meanV - slope * (p.t - meanT));
}

/**
 * The pulse as the waveform's strongest rhythm.
 * @param {!Array<{t: number, v: number}>} points
 * @return {?{bpm: number, share: number}} null when shorter than 10 s.
 */
function pulse(points) {
  const found = rhythm(points, PULSE_RANGE, 0.5, 6, 10);
  return found && {bpm: Math.round(found.rate), share: found.share};
}

/**
 * Resamples a series evenly by linear interpolation.
 * @param {!Array<{t: number, v: number}>} points Ordered by time.
 * @param {number} hz
 * @return {!Array<{t: number, v: number}>}
 */
function resample(points, hz) {
  const result = [];
  let j = 0;
  for (let t = points[0].t; t <= points[points.length - 1].t; t += 1 / hz) {
    while (j < points.length - 2 && points[j + 1].t < t) {
      j++;
    }
    const a = points[j];
    const b = points[j + 1];
    const f = b.t > a.t ? (t - a.t) / (b.t - a.t) : 0;
    result.push({t, v: a.v + f * (b.v - a.v)});
  }
  return result;
}

/**
 * Finds the beats in a pulse waveform: the highest point in each window of
 * about one beat, refined between samples with a parabola.
 * @param {!Array<{t: number, v: number}>} points
 * @param {number} bpm The pulse, which sets the shortest gap between beats.
 * @return {!Array<{t: number, height: number}>} height is the peak's rise
 *     above the lowest point since the previous beat.
 */
function findBeats(points, bpm) {
  if (points.length < 4 || !(bpm > 0)) {
    return [];
  }
  const even = resample(points, RESAMPLE_HZ);
  const gap = Math.round((0.6 * 60 / bpm) * RESAMPLE_HZ);
  const half = Math.max(1, Math.floor(gap / 2));
  const beats = [];
  let last = -Infinity;
  let trough = Infinity;
  for (let i = 1; i < even.length - 1; i++) {
    trough = Math.min(trough, even[i].v);
    let highest = true;
    for (let k = Math.max(0, i - half);
         k <= Math.min(even.length - 1, i + half); k++) {
      if (even[k].v > even[i].v) {
        highest = false;
        break;
      }
    }
    if (!highest || i - last < gap) {
      continue;
    }
    beats.push({
      t: refinePeak(points, even[i].t, (PEAK_WINDOW * 60) / bpm),
      height: even[i].v - trough,
    });
    last = i;
    trough = Infinity;
  }
  return beats.slice(1);
}

/**
 * Places a peak between samples: fits a parabola by least squares to the
 * raw samples near a rough peak time and returns its top. Interpolated
 * samples alone place a peak only to the nearest sample, about 40 ms at a
 * webcam's frame rate, and the fit also averages out frame noise.
 * @param {!Array<{t: number, v: number}>} points The raw samples.
 * @param {number} rough The rough peak time, in seconds.
 * @param {number} halfWidth Seconds either side of the rough time to fit.
 * @return {number} The refined time, or the rough one when no fit peaks.
 */
function refinePeak(points, rough, halfWidth) {
  const near = points.filter((p) => Math.abs(p.t - rough) <= halfWidth);
  if (near.length < 3) {
    return rough;
  }
  // Fits v = a x^2 + b x + c with x = t - rough by the normal equations.
  const sums = [0, 0, 0, 0, 0];
  const right = [0, 0, 0];
  for (const p of near) {
    const x = p.t - rough;
    for (let k = 0; k < 5; k++) sums[k] += x ** k;
    for (let k = 0; k < 3; k++) right[k] += p.v * x ** k;
  }
  const matrix = [
    [sums[4], sums[3], sums[2], right[2]],
    [sums[3], sums[2], sums[1], right[1]],
    [sums[2], sums[1], sums[0], right[0]],
  ];
  const [a, b] = solve(matrix);
  const top = a < 0 ? -b / (2 * a) : 0;
  return Math.abs(top) <= halfWidth ? rough + top : rough;
}

/**
 * Solves a 3 by 3 linear system by Gaussian elimination.
 * @param {!Array<!Array<number>>} m Rows of [coefficients..., constant].
 * @return {!Array<number>} The unknowns, or zeros for a singular system.
 */
function solve(m) {
  for (let col = 0; col < 3; col++) {
    let pivot = col;
    for (let row = col + 1; row < 3; row++) {
      if (Math.abs(m[row][col]) > Math.abs(m[pivot][col])) pivot = row;
    }
    if (Math.abs(m[pivot][col]) < 1e-12) return [0, 0, 0];
    [m[col], m[pivot]] = [m[pivot], m[col]];
    for (let row = 0; row < 3; row++) {
      if (row === col) continue;
      const factor = m[row][col] / m[col][col];
      for (let k = col; k < 4; k++) m[row][k] -= factor * m[col][k];
    }
  }
  return [m[0][3] / m[0][0], m[1][3] / m[1][1], m[2][3] / m[2][2]];
}

/**
 * Heart rate variability from beat times.
 * @param {!Array<{t: number}>} beats
 * @return {?{rmssd: number, sdnn: number, intervals: number, sure: boolean}}
 *     In milliseconds; null with fewer than MIN_INTERVALS clean intervals.
 */
function heartRateVariability(beats) {
  const intervals = [];
  for (let i = 1; i < beats.length; i++) {
    intervals.push((beats[i].t - beats[i - 1].t) * 1000);
  }
  if (!intervals.length) {
    return null;
  }
  const median = [...intervals].sort((a, b) => a - b)[intervals.length >> 1];
  const clean = intervals.map(
      (ms) =>
          Math.abs(ms - median) <= MAX_INTERVAL_DEVIATION * median ? ms : null);
  const kept = clean.filter((ms) => ms != null);
  if (kept.length < MIN_INTERVALS) {
    return null;
  }
  const differences = [];
  for (let i = 1; i < clean.length; i++) {
    if (clean[i] != null && clean[i - 1] != null) {
      differences.push(clean[i] - clean[i - 1]);
    }
  }
  const mean = kept.reduce((sum, ms) => sum + ms, 0) / kept.length;
  const sdnn = Math.sqrt(
      kept.reduce((sum, ms) => sum + (ms - mean) ** 2, 0) / (kept.length - 1));
  const rmssd = Math.sqrt(
      differences.reduce((sum, d) => sum + d * d, 0) /
      Math.max(1, differences.length));
  return {
    rmssd: Math.round(rmssd),
    sdnn: Math.round(sdnn),
    intervals: kept.length,
    sure: kept.length / intervals.length >= MIN_ACCEPTED_SHARE,
  };
}

/**
 * Breathing rate from the three signals breathing modulates.
 * @param {!Array<{t: number, height: number}>} beats
 * @param {!Array<{t: number, v: number}>} motion The face centre's height
 *     in the frame; t is in seconds.
 * @return {?{rate: number, sure: boolean, sources: !Array<string>}} null
 *     when no signal is long enough.
 */
function breathingRate(beats, motion) {
  const series = {
    height: beats.map((beat) => ({t: beat.t, v: beat.height})),
    interval:
        beats.slice(1).map((beat, i) => ({t: beat.t, v: beat.t - beats[i].t})),
    motion,
  };
  const estimates = [];
  for (const [source, points] of Object.entries(series)) {
    const found = rhythm(points, BREATHING_RANGE, 0.25, 1.5, 20);
    if (found) {
      estimates.push({source, ...found});
    }
  }
  if (!estimates.length) {
    return null;
  }
  // The largest group of estimates within AGREEMENT of each other wins; a
  // tie goes to the group with the stronger peaks.
  let best = null;
  for (const anchor of estimates) {
    const group = estimates.filter(
        (other) => Math.abs(other.rate - anchor.rate) <= AGREEMENT);
    const strength = group.reduce((sum, item) => sum + item.share, 0);
    if (!best || group.length > best.group.length ||
        (group.length === best.group.length && strength > best.strength)) {
      best = {group, strength};
    }
  }
  const rate =
      best.group.reduce((sum, item) => sum + item.rate * item.share, 0) /
      best.strength;
  return {
    rate: Math.round(rate),
    sure: best.group.length >= 2 &&
        best.strength / best.group.length >= MIN_BREATHING_SHARE,
    sources: best.group.map((item) => item.source),
  };
}

const exported = {
  breathingRate,
  findBeats,
  heartRateVariability,
  pulse,
  rhythm,
};
root.StellisVitalsMath = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
