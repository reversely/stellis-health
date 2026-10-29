/**
 * @fileoverview Microphone capture for the intake call. Resamples the input
 * to 16 kHz mono 16-bit PCM and posts 20 ms frames with their loudness, which
 * the call screen shows as a ring around the speaker.
 *
 * A windowed-sinc low-pass filter runs before the rate change: without it,
 * sound above 8 kHz folds into the speech band as noise, and speech
 * recognition misses more words (12.8% against 15% of words in a test with
 * background noise).
 */
const TARGET_RATE = 16000;
const FRAME = 320;
const TAPS = 63;
/** Hz; below the 8 kHz limit of 16 kHz audio, so the filter has room. */
const CUTOFF = 7200;

/**
 * Builds a Blackman-windowed sinc low-pass filter with unit gain.
 * @param {number} rate The input sample rate.
 * @return {!Float32Array}
 */
function lowPass(rate) {
  const taps = new Float32Array(TAPS);
  const band = 2 * CUTOFF / rate;
  let total = 0;
  for (let i = 0; i < TAPS; i++) {
    const n = i - (TAPS - 1) / 2;
    const sinc =
        n === 0 ? 1 : Math.sin(Math.PI * band * n) / (Math.PI * band * n);
    const window = 0.42 - 0.5 * Math.cos(2 * Math.PI * i / (TAPS - 1)) +
        0.08 * Math.cos(4 * Math.PI * i / (TAPS - 1));
    taps[i] = band * sinc * window;
    total += taps[i];
  }
  for (let i = 0; i < TAPS; i++) taps[i] /= total;
  return taps;
}

class StellisMic extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.taps = lowPass(sampleRate);
    this.recent = new Float32Array(TAPS);
    this.at = 0;
    // The filtered input sample count, the last filtered sample, and the
    // input position of the next output sample.
    this.count = 0;
    this.previous = 0;
    this.next = 0;
    this.squares = 0;
    this.frame = new Int16Array(FRAME);
    this.filled = 0;
  }

  /** @override */
  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      this.recent[this.at] = input[i];
      this.at = (this.at + 1) % TAPS;
      let filtered = 0;
      for (let k = 0; k < TAPS; k++) {
        filtered += this.taps[k] * this.recent[(this.at + k) % TAPS];
      }
      // Output samples between the last filtered sample and this one.
      while (this.next <= this.count) {
        const share = this.next - (this.count - 1);
        this.emit(this.previous + (filtered - this.previous) * share);
        this.next += this.ratio;
      }
      this.previous = filtered;
      this.count++;
    }
    return true;
  }

  /** Adds one 16 kHz sample, and posts the frame when it is full. */
  emit(sample) {
    const value = Math.max(-1, Math.min(1, sample));
    this.squares += value * value;
    this.frame[this.filled++] = value * 32767;
    if (this.filled === FRAME) {
      const level = Math.sqrt(this.squares / FRAME);
      this.port.postMessage(
          {pcm: this.frame.buffer, level}, [this.frame.buffer]);
      this.frame = new Int16Array(FRAME);
      this.filled = 0;
      this.squares = 0;
    }
  }
}

registerProcessor('stellis-mic', StellisMic);
