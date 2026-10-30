/** Local camera check-in with FacePhys and POS comparison. Frames and signals
 * remain on the device. Only approved derived observations use host persistence.
 */
(function(root) {
'use strict';

const LIBRARY = '/static/vendor/vitallens/vitallens.browser.js';
const math = root.StellisVitalsMath || require('./vitals-math.js');
/** The pulse waveform quality above which HRV counts as sure. */
const HRV_MIN_QUALITY = 0.6;
/** Milliseconds of good signal a reading needs. */
const GOOD_MS = 45000;
/** The longest attempt before the capture gives up. */
const MAX_MS = 90000;
/** The reading comes from this final stretch of good signal. */
const WINDOW_MS = 20000;
/** Good signal that still gives a reading when the attempt runs out. */
const MIN_GOOD_MS = 15000;
/** Extra measuring time when the first 30 seconds give an unsure reading. */
const EXTRA_MS = 15000;
const MIN_ESTIMATES = 10;
/** The largest spread (10th to 90th percentile) of sure estimates. */
const MAX_SPREAD_BPM = 8;
/**
 * The smallest share of the waveform's power within 6 bpm of its peak for a
 * sure reading. Lower shares still give a reading, marked as approximate.
 */
const MIN_PEAK_SHARE = 0.3;
const BPM_BOUNDS = [40, 180];
/** The oval guide's centre, as fractions of the frame. */
const OVAL_CENTRE = [0.5, 0.46];
const CENTRE_TOLERANCE = 0.15;
/** Face box height as a fraction of the frame height. */
const FACE_HEIGHT = [0.2, 0.85];
const LUMA = {
  dark: 70,
  bright: 215,
  backlit: 45
};
/** Largest movement of the face centre over a second, as a frame fraction. */
const MAX_MOVEMENT = 0.06;
/** Waveform samples must have arrived within this time for a signal. */
const SIGNAL_MS = 3000;
/** How long an issue lasts before its advice shows. */
const HINT_MS = 3000;
const SAMPLE_MS = 400;
/** The longest wait for the pulse reader to load. */
const LOAD_MS = 60000;
/** The longest wait for the pulse reader's first result after it starts. */
const FIRST_RESULT_MS = 20000;
const START_FAILED = 'Try again in Chrome or Safari';
/** Gaps between events longer than this count as this long. */
const MAX_STEP_MS = 250;
const SPEAK_GAP_MS = 3000;
/** The dialog's closing transition, from motion.css. */
const CLOSE_MS = 200;
const SPEAK_KEY = 'stellis-pulse-speak';
const WAVE_SAMPLES = 150;

/**
 * Advice for each issue that lowers the reading's quality. Measuring goes
 * on during these; the advice shows once an issue lasts HINT_MS.
 */
const HINTS = {
  off: 'Try centring your face in the oval',
  far: 'Try moving a little closer',
  close: 'Try moving a little farther back',
  dark: 'A little more light may help',
  backlit: 'Try turning toward the light',
  bright: 'Softer light may help',
  moving: 'Resting your phone may help steady the signal',
};
/** The status line for each stage of a capture. */
const STAGE_TEXT = {
  camera: 'Turning on the camera',
  loading: 'Getting ready',
  face: 'Looking for your face',
  signal: 'Finding your pulse',
  measuring: 'Measuring your pulse',
};
/** The progress steps beside the camera. */
const STEPS = [
  ['camera', 'Camera on'],
  ['face', 'Face found'],
  ['measuring', 'Measuring'],
];
/** Advice when an attempt ends without enough signal. */
const FAILURE_TEXT = {
  face: 'Sit facing the camera with your face in the oval and try again',
  signal: 'More light on your face helps the camera find your pulse',
};

/**
 * Checks where the face is in the frame.
 * @param {?Array<number>} box [x0, y0, x1, y1] in video pixels, or null.
 * @param {number} width The video's width in pixels.
 * @param {number} height The video's height in pixels.
 * @return {string} '' when it fits, else 'none', 'off', 'far' or 'close'.
 */
function faceCheck(box, width, height) {
  if (!box || !width || !height) {
    return 'none';
  }
  const [x0, y0, x1, y1] = box;
  const centreX = (x0 + x1) / 2 / width;
  const centreY = (y0 + y1) / 2 / height;
  if (Math.abs(centreX - OVAL_CENTRE[0]) > CENTRE_TOLERANCE ||
      Math.abs(centreY - OVAL_CENTRE[1]) > CENTRE_TOLERANCE) {
    return 'off';
  }
  const size = (y1 - y0) / height;
  if (size < FACE_HEIGHT[0]) {
    return 'far';
  }
  if (size > FACE_HEIGHT[1]) {
    return 'close';
  }
  return '';
}

/**
 * Checks the light on the face.
 * @param {?number} face Mean luma of the face, 0 to 255.
 * @param {?number} frame Mean luma of the whole frame, 0 to 255.
 * @return {string} '' when good or not yet sampled, else 'dark', 'backlit'
 *     or 'bright'.
 */
function lightCheck(face, frame) {
  if (face == null) {
    return '';
  }
  if (face > LUMA.bright) {
    return 'bright';
  }
  if (frame != null && frame - face > LUMA.backlit) {
    return 'backlit';
  }
  if (face < LUMA.dark) {
    return 'dark';
  }
  return '';
}

/**
 * Checks that the face stayed still over the last second.
 * @param {!Array<{t: number, x: number, y: number}>} centres Face centres as
 *     frame fractions, oldest first.
 * @return {string} '' when still, else 'moving'.
 */
function stillCheck(centres) {
  if (centres.length < 2) {
    return '';
  }
  const end = centres[centres.length - 1].t;
  const recent = centres.filter((centre) => centre.t >= end - 1000);
  const xs = recent.map((centre) => centre.x);
  const ys = recent.map((centre) => centre.y);
  const spread = Math.max(
      Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  return spread > MAX_MOVEMENT ? 'moving' : '';
}

/**
 * Sums up a capture into one reading. The waveform's strongest rhythm gives
 * the number; the median of vitallens' own estimates stands in when the
 * waveform is too short.
 * @param {!Array<{t: number, bpm: ?number}>} samples Estimates taken while
 *     every check passed; t is milliseconds of good signal.
 * @param {!Array<{t: number, v: number}>=} wave Waveform samples from the
 *     last stretch of good signal; t is in seconds.
 * @return {{bpm: ?number, quality: number, sure: boolean}} bpm is null when
 *     the capture has too little signal.
 */
function summarize(samples, wave = []) {
  const rhythm = math.pulse(wave);
  if (rhythm) {
    return {
      bpm: rhythm.bpm,
      quality: Math.round(rhythm.share * 100) / 100,
      sure: rhythm.share >= MIN_PEAK_SHARE,
    };
  }
  const end = samples.length ? samples[samples.length - 1].t : 0;
  const recent =
      samples
          .filter((sample) => sample.t >= end - WINDOW_MS && sample.bpm != null)
          .map((sample) => sample.bpm)
          .sort((a, b) => a - b);
  if (recent.length < MIN_ESTIMATES) {
    return {bpm: null, quality: 0, sure: false};
  }
  const at = (fraction) => recent[Math.round((recent.length - 1) * fraction)];
  const bpm = Math.round(at(0.5));
  const spread = at(0.9) - at(0.1);
  const quality = Math.max(0, Math.min(1, 1 - spread / (MAX_SPREAD_BPM * 2.5)));
  return {
    bpm: bpm >= BPM_BOUNDS[0] && bpm <= BPM_BOUNDS[1] ? bpm : null,
    quality: Math.round(quality * 100) / 100,
    sure: spread <= MAX_SPREAD_BPM,
  };
}

/**
 * Builds a finished reading: the pulse, then the HRV and breathing rate
 * computed from the beats in the whole recording and the face's movement.
 * @param {{bpm: number, quality: number, sure: boolean}} summary
 * @param {!Array<{t: number, v: number}>=} recording All good waveform.
 * @param {!Array<{t: number, v: number}>=} motion The face centre's height.
 * @return {{ok: boolean, bpm: number, quality: number, approximate: boolean,
 *     hrv: ?{rmssd: number, approximate: boolean},
 *     breathing: ?{rate: number, approximate: boolean},
 *     recording: !Array<{t: number, v: number}>}} recording feeds the blood
 *     pressure estimate.
 */
function reading(summary, recording = [], motion = []) {
  const beats = math.findBeats(recording, summary.bpm);
  const duration = recording.length > 1 ? recording.at(-1).t - recording[0].t : 0;
  const hrv = duration >= 30 && recording.length / duration >= 20 ? math.heartRateVariability(beats) : null;
  const breathing = math.breathingRate(beats, motion);
  return {
    ok: true,
    bpm: summary.bpm,
    quality: summary.quality,
    approximate: !summary.sure,
    duration, capturedAt: new Date().toISOString(),
    hrv: hrv && hrv.sure && hrv.rmssd >= 5 && hrv.rmssd <= 300 && summary.quality >= HRV_MIN_QUALITY ? {
      rmssd: hrv.rmssd, sdnn: hrv.sdnn, intervals: hrv.intervals,
      approximate: !(hrv.sure && summary.quality >= HRV_MIN_QUALITY),
    } : null,
    breathing: breathing && breathing.sure ? {
      rate: breathing.rate, sources: breathing.sources, duration:breathing.duration, quality:breathing.quality,
      approximate: !breathing.sure,
    } : null,
    recording,
  };
}

/**
 * The rows under the pulse on the result screen.
 * @param {!Object} summary A finished reading.
 * @return {!Array<{label: string, value: string, approximate: boolean}>}
 */
function extraRows(summary) {
  const rows = [];
  if (summary.breathing) {
    rows.push({
      label: 'Breathing rate',
      value: `${summary.breathing.rate} breaths a minute`,
      approximate: summary.breathing.approximate,
    });
  }
  if (summary.hrv) {
    rows.push({
      label: 'Heart rate variability',
      value: `${summary.hrv.rmssd} ms`,
      approximate: summary.hrv.approximate,
    });
  }
  return rows;
}

/**
 * The readings a finished capture saves: the pulse, and the breathing rate
 * and HRV when the recording gave them. Approximate values say so in their
 * note.
 * @param {!Object} summary
 * @return {!Array<!Object>} Bodies for POST /readings.
 */
function readingsToSave(summary) {
  const body = (measure, value, approximate) => ({
    measure,
    value,
    source: 'camera',
    quality: summary.quality,
    note: approximate ? 'Approximate' : '',
  });
  const result = [body('pulse', summary.bpm, summary.approximate)];
  if (summary.breathing) {
    result.push(body(
        'breathing_rate', summary.breathing.rate,
        summary.breathing.approximate));
  }
  if (summary.hrv) {
    result.push(body('hrv', summary.hrv.rmssd, summary.hrv.approximate));
  }
  return result;
}

/**
 * Follows one capture attempt: the checks, the paused countdown and the
 * estimates taken while every check passed. Holds no DOM, so tests drive it
 * directly.
 */
class CaptureState {
  /** @param {number} start When the attempt began, in milliseconds. */
  constructor(start, targetMs = GOOD_MS) {
    this.targetMs = targetMs;
    this.start = start;
    this.last = start;
    this.goodMs = 0;
    this.retained = null;
    this.centres = [];
    this.waveAt = -Infinity;
    this.samples = [];
    /** Waveform samples from good signal, newest last. */
    this.wave = [];
    /** Every good waveform sample, for beats, HRV and breathing. */
    this.recording = [];
    /** The face centre's height in the frame while measuring. */
    this.motion = [];
    /** Milliseconds without a face, and with a face but no pulse signal. */
    this.missing = {face: 0, signal: 0};
    /** The current quality issue and when it began. */
    this.issue = '';
    this.issueSince = 0;
  }

  /**
   * Takes one vitals event.
   * @param {{now: number, box: ?Array<number>, width: number,
   *     height: number, wave: number, bpm: ?number, faceLuma: ?number,
   *     frameLuma: ?number, waveData: (!Array<number>|undefined),
   *     waveTimes: (!Array<number>|undefined)}} event wave is how many
   *     waveform samples arrived with the event; waveData has the samples
   *     and waveTimes their times in seconds.
   * @return {{stage: string, hint: string, goodMs: number, done: boolean,
   *     result: ?Object}} stage is 'face', 'signal' or 'measuring'; hint is
   *     advice for an issue that has lasted HINT_MS; result is set once done.
   */
  update(event) {
    const gap = event.now - this.last;
    const step = Math.max(0, Math.min(gap, MAX_STEP_MS));
    this.last = event.now;
    if (event.wave > 0) {
      this.waveAt = event.now;
    }
    const face = faceCheck(event.box, event.width, event.height);
    let stage = 'measuring';
    let issue = '';
    if (face === 'none') {
      stage = 'face';
      this.centres = [];
    } else {
      const [x0, y0, x1, y1] = event.box;
      this.centres.push({
        t: event.now,
        x: (x0 + x1) / 2 / event.width,
        y: (y0 + y1) / 2 / event.height,
      });
      this.centres =
          this.centres.filter((centre) => centre.t >= event.now - 1000);
      if (event.now - this.waveAt > SIGNAL_MS) {
        stage = 'signal';
      }
      issue = face || lightCheck(event.faceLuma, event.frameLuma) ||
          stillCheck(this.centres);
    }
    // Advice is not a gate: only absent tracking, stale frames or extreme exposure break a segment.
    const unusable = gap > 1000 || event.faceLuma != null && (event.faceLuma < 20 || event.faceLuma > 248);
    if (unusable) stage = 'signal';
    if (stage !== 'measuring') {
      this.retainSegment();
      this.wave = []; this.recording = []; this.samples = []; this.motion = []; this.goodMs = 0;
    }
    if (issue) this.advisedFrames = (this.advisedFrames || 0) + 1;
    this.totalFrames = (this.totalFrames || 0) + 1;
    if (stage === 'measuring') {

      this.samples.push({t: this.goodMs, bpm: event.bpm});
      this.addWave(event.waveData, event.waveTimes);
      this.goodMs = this.recording.length > 1 ? (this.recording.at(-1).t-this.recording[0].t)*1000 : 0;
      this.motion.push({t: this.recording.at(-1)?.t ?? event.now / 1000, v: this.centres.at(-1).y});
    } else {
      this.missing[stage] += step;
    }
    if (issue !== this.issue) {
      this.issue = issue;
      this.issueSince = event.now;
    }
    const hint =
        issue && event.now - this.issueSince >= HINT_MS ? HINTS[issue] : '';
    if (this.goodMs >= this.targetMs) {
      const summary = this.summary();
      if (summary.bpm != null &&
          summary.sure) {
        return this.finish(reading(summary, this.recording, this.motion));
      }
    }
    if (event.now - this.start >= MAX_MS) {
      return this.finish(this.lastResult());
    }
    return {stage, hint, goodMs: this.goodMs, done: false, result: null};
  }

  /**
   * Keeps the last WINDOW_MS of good waveform.
   * @param {!Array<number>|undefined} values
   * @param {!Array<number>|undefined} times Seconds, one per value.
   */
  addWave(values, times) {
    if (!values || !times || values.length !== times.length) {
      return;
    }
    values.forEach((v, i) => {
      if (!Number.isFinite(v) || !Number.isFinite(times[i]) || times[i] <= (this.recording.at(-1)?.t ?? -Infinity)) return;
      if (this.recording.length && times[i]-this.recording.at(-1).t > .5) { this.retainSegment(); this.recording=[]; this.wave=[]; this.motion=[]; }
      this.wave.push({t: times[i], v});
      this.recording.push({t: times[i], v});
    });
    if (!this.wave.length) return;
    const from = this.wave[this.wave.length - 1].t - WINDOW_MS / 1000;
    const first = this.wave.findIndex((point) => point.t >= from);
    this.wave.splice(0, Math.max(0, first));
  }

  /** Keeps a useful segment across reacquisition without joining beat intervals. */
  retainSegment() {
    if (this.goodMs < MIN_GOOD_MS || this.goodMs <= (this.retained?.duration || 0) * 1000) return;
    const summary = this.summary();
    if (summary.bpm != null && summary.sure) {
      this.retained = reading(summary, this.recording.slice(), this.motion.slice());
    }
  }

  /** @return {{bpm: ?number, quality: number, sure: boolean}} */
  summary() {
    const result = summarize([], this.wave);
    // Moderate acquisition issues lower confidence without blocking capture.
    result.quality *= 1 - .2 * ((this.advisedFrames || 0) / (this.totalFrames || 1));
    return result;
  }

  /**
   * The result when time runs out: a reading from enough good signal,
   * otherwise the check that failed longest.
   * @return {!Object}
   */
  lastResult() {
    this.retainSegment();
    if (this.retained) return this.retained;
    return {
      ok: false,
      reason: this.missing.face > this.missing.signal ? 'face' : 'signal',
    };
  }

  /**
   * @param {!Object} result
   * @return {{stage: string, hint: string, goodMs: number, done: boolean,
   *     result: !Object}}
   */
  finish(result) {
    return {
      stage: 'measuring',
      hint: '',
      goodMs: this.goodMs,
      done: true,
      result
    };
  }
}

/**
 * Marks each progress step for a capture stage.
 * @param {string} stage A key of STAGE_TEXT.
 * @return {!Object<string, string>} 'done', 'active' or 'wait' by step.
 */
function stepStates(stage) {
  const order = ['camera', 'face', 'measuring'];
  const reached = {camera: 0, loading: 0, face: 1, signal: 2, measuring: 2};
  const at = reached[stage] ?? 0;
  const states = {};
  order.forEach((step, index) => {
    states[step] = index < at ? 'done' : index === at ? 'active' : 'wait';
  });
  return states;
}

/**
 * Describes a pulse against the person's usual range.
 * @param {number} bpm
 * @param {?Object} usual {low, high} from GET /readings, or null.
 * @return {string}
 */
function usualText(bpm, usual) {
  if (!usual) {
    return 'Usual range shows after a few more readings';
  }
  const range = `${Math.round(usual.low)} to ${Math.round(usual.high)}`;
  if (bpm > usual.high) {
    return `Higher than usual: ${range}`;
  }
  if (bpm < usual.low) {
    return `Lower than usual: ${range}`;
  }
  return `Within usual range of ${range}`;
}

/**
 * Explains why the camera could not start.
 * @param {*} error
 * @return {string}
 */
function cameraError(error) {
  const name = error && error.name;
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Allow the camera in your browser settings and try again';
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return 'Connect a camera to log vital signs';
  }
  return 'Try again in a moment';
}

/**
 * Sets an element's text when it changes, so screen readers hear changes
 * only.
 * @param {?Element} element
 * @param {string} text
 */
function setText(element, text) {
  if (element && element.textContent !== text) {
    element.textContent = text;
  }
}

/**
 * @param {*} error
 * @return {string} The error's name and message, for the details line.
 */
function describe(error) {
  if (!error) {
    return 'unknown error';
  }
  return [error.name, error.message].filter(Boolean).join(': ') ||
      String(error);
}

/**
 * Rejects with a TimeoutError when a promise takes too long.
 * @param {!Promise<T>} promise
 * @param {number} ms
 * @param {string} what
 * @return {!Promise<T>}
 * @template T
 */
function withTimeout(promise, ms, what) {
  let timer;
  const timeout = new Promise((resolve, reject) => {
    timer = setTimeout(() => {
      const error = new Error(`${what} took longer than ${ms} ms`);
      error.name = 'TimeoutError';
      reject(error);
    }, ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/** @return {?SpeechSynthesisVoice} An on-device English voice, if any. */
function localVoice() {
  const speech = root.speechSynthesis;
  if (!speech) {
    return null;
  }
  const voices = speech.getVoices().filter((voice) => voice.localService);
  return voices.find((voice) => voice.lang.startsWith('en')) || voices[0] ||
      null;
}

/**
 * Spoken guidance is opt-in and stays on this device.
 * @return {boolean}
 */
function speakChosen() {
  try {
    return root.localStorage.getItem(SPEAK_KEY) === 'on';
  } catch (error) {
    return false;
  }
}

const SVG = 'http://www.w3.org/2000/svg';
/** The oval's centre and radii as fractions of the stage. */
const OVAL = {
  cx: 0.5,
  cy: 0.46,
  rx: 0.32,
  ry: 0.36
};

/**
 * Draws the oval from its top point, so the progress ring starts there.
 * @param {number} width The stage's width in pixels.
 * @param {number} height The stage's height in pixels.
 * @return {string} An SVG path.
 */
function ovalPath(width, height) {
  const round = (value) => Math.round(value * 100) / 100;
  const cx = round(OVAL.cx * width);
  const top = round((OVAL.cy - OVAL.ry) * height);
  const rx = round(OVAL.rx * width);
  const ry = round(OVAL.ry * height);
  return `M${cx} ${top}A${rx} ${ry} 0 1 1 ${round(cx - 0.01)} ${top}Z`;
}

/**
 * Builds the camera overlay: a dimmed surround, the oval and its progress
 * ring, drawn in the stage's pixel space so the ring's stroke stays even.
 * @param {!Element} stage
 * @return {{svg: !Element, ring: !Element, observer: !ResizeObserver}}
 */
function overlay(stage) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('class', 'pulse-overlay');
  svg.setAttribute('aria-hidden', 'true');
  const path = (className) => {
    const element = document.createElementNS(SVG, 'path');
    element.setAttribute('class', className);
    return element;
  };
  const shade = path('pulse-shade');
  shade.setAttribute('fill-rule', 'evenodd');
  shade.setAttribute('fill', 'rgb(0 0 0 / 0.55)');
  const oval = path('pulse-oval');
  const ring = path('pulse-ring');
  ring.setAttribute('pathLength', '100');
  ring.setAttribute('stroke-dasharray', '0 100');
  svg.append(shade, oval, ring);
  const fit = () => {
    const width = stage.clientWidth || 300;
    const height = stage.clientHeight || 400;
    const d = ovalPath(width, height);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    shade.setAttribute('d', `M0 0H${width}V${height}H0Z ${d}`);
    oval.setAttribute('d', d);
    ring.setAttribute('d', d);
  };
  const observer = new ResizeObserver(fit);
  observer.observe(stage);
  fit();
  return {svg, ring, observer};
}

/** The measuring dialog. */
class PulseDialog {
  /**
   * @param {function(): !Promise<void>} onSaved Runs after a reading is saved.
   * @param {?Object} measure The pulse measure from GET /readings.
   * @param {{saveReading: Function, showComparison: boolean}=} options Optional isolated-host persistence and comparison policy.
   */
  constructor(onSaved, measure, options = {}) {
    const dom = root.StellisDom;
    this.dom = dom;
    this.onSaved = onSaved;
    this.saveReading = options.saveReading || ((json) => dom.api('/readings', {method: 'POST', json}));
    this.showComparison = options.showComparison !== false;
    this.measureInfo = measure;
    this.isolated = options.showComparison === false;
    this.notebookId = options.notebookId || null;
    this.engineChoice = 'facephys';
    this.captureMs = GOOD_MS;
    this.usual = measure ? measure.usual : null;
    this.speak = speakChosen();
    this.spokenAt = -Infinity;
    this.spoken = '';
    this.dialog = /** @type {!HTMLDialogElement} */ (
        dom.el('dialog', null, 'pulse-dialog'));
    this.dialog.setAttribute('aria-labelledby', 'pulse-title');
    const title = dom.el('h2', 'Health scan');
    title.id = 'pulse-title';
    const close = dom.button(null, 'pulse-close');
    close.append(dom.icon('x'));
    close.setAttribute('aria-label', 'Close');
    close.addEventListener('click', () => this.dialog.close());
    const head = dom.el('div', null, 'pulse-head');
    head.append(title, close);
    this.body = dom.el('div', null, 'pulse-body');
    this.dialog.append(head, this.body);
    this.dialog.addEventListener('close', () => {
      this.stop();
      root.speechSynthesis?.cancel();
      root.speechSynthesis?.removeEventListener(
          'voiceschanged', this.paintVoice);
      // Removal waits for the closing transition in motion.css.
      setTimeout(() => this.dialog.remove(), CLOSE_MS);
    });
    document.body.append(this.dialog);
  }

  open() {
    this.dialog.showModal();
    this.intro();
  }

  intro() {
    const dom = this.dom;
    const lead = dom.el('p', 'Check in with your body', 'pulse-intro-title');
    const description = dom.el('p', 'A 45-second camera scan for pulse and breathing', 'pulse-note');
    const preview = dom.el('div', null, 'pulse-preview');
    for (const label of ['Pulse', 'Breathing', 'Pulse variability']) {
      const item = dom.el('div');
      item.append(dom.el('span', label));
      preview.append(item);
    }
    const tips = dom.el('p', 'Get comfortable with your face in view', 'pulse-note');
    const privacy = dom.el('p', 'Your video and signal stay on this device', 'pulse-note');
    const settings = dom.el('details', null, 'pulse-settings');
    settings.append(dom.el('summary', 'How it works'));
    settings.append(dom.el('p', 'The camera follows small changes in skin colour', 'pulse-note'),
        dom.el('p', 'Breathing and pulse variability appear when the signal supports them', 'pulse-note'),
        dom.accuracyNote('camera', 'About accuracy'));
    const engineLabel = dom.el('label', 'Pulse method', 'pulse-field');
    const engine = dom.el('select');
    for (const [value, text] of [['facephys', 'FacePhys'], ['pos', 'POS comparison']]) {
      const option = dom.el('option', text); option.value = value; engine.append(option);
    }
    engine.value = this.engineChoice;
    engine.onchange = () => { this.engineChoice = engine.value; };
    engineLabel.append(engine); settings.append(engineLabel);
    const consent = dom.el('label', null, 'pulse-consent');
    const agree = dom.el('input'); agree.type = 'checkbox';
    consent.append(agree, dom.el('span', 'I agree to measure my pulse signals on this device'));
    const start = dom.button('Start scan', 'pulse-primary');
    start.disabled = true;
    agree.onchange = () => { start.disabled = !agree.checked; };
    start.addEventListener('click', () => this.measure());
    this.setStage('intro');
    this.body.replaceChildren(lead, description, preview, tips, privacy, settings, consent, start);
    const accuracy = settings.querySelector('.accuracy-note'); accuracy.target = '_blank'; accuracy.rel = 'noopener';
    agree.focus();
  }

  /**
   * Marks which screen shows, which sets the dialog's layout.
   * @param {string} stage 'intro', 'live' or 'done'.
   */
  setStage(stage) {
    this.dialog.querySelector('.pulse-save, .pulse-capture-actions')?.remove();
    this.dialog.dataset.stage = stage;
    this.dialog.querySelector('#pulse-title').textContent = stage === 'done' ? 'Your scan' : 'Health scan';
    this.body.scrollTop = 0;
    if (stage !== 'live') {
      this.dialog.classList.remove('is-measuring');
    }
  }

  /** Builds the live screen: the camera stage beside a checks panel. */
  live() {
    const dom = this.dom;
    this.setStage('live');
    this.video =
        /** @type {!HTMLVideoElement} */ (dom.el('video', null, 'pulse-video'));
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.setAttribute('aria-hidden', 'true');
    const stage = dom.el('div', null, 'pulse-stage');
    const {svg, ring, observer} = overlay(stage);
    this.ring = ring;
    this.observer = observer;
    // The panel's status is the one written cue; a caption on the video lagged
    // behind it with a different sentence and was clipped by the frame.
    stage.append(this.video, svg);

    this.status = dom.el('p', null, 'pulse-status');
    this.status.setAttribute('role', 'status');
    this.hint = dom.el('p', null, 'pulse-hint');
    this.wave = /** @type {!HTMLCanvasElement} */ (dom.el('canvas', null, 'pulse-wave'));
    this.wave.setAttribute('aria-label', 'Live pulse signal');
    this.wave.setAttribute('role', 'img');
    this.waveData = [];
    const waveform = dom.el('div', null, 'pulse-live-signal');
    this.waveCaption = dom.el('p', 'Waiting for your signal', 'pulse-note');
    this.waveCaption.classList.add('pulse-wave-caption');
    waveform.append(dom.el('h3', 'Pulse signal'), this.wave, this.waveCaption);
    const progress = dom.el('div', null, 'pulse-progress');
    this.progressText = dom.el('span', `0 of ${this.captureMs / 1000} seconds`);
    const progressLabel = dom.el('div', null, 'pulse-progress-label');
    progressLabel.append(dom.el('span', 'Usable signal'), this.progressText);
    this.progress = dom.el('progress');
    this.progress.max = this.captureMs; this.progress.value = 0;
    this.progress.setAttribute('aria-label', 'Usable signal collected');
    progress.append(progressLabel, this.progress);
    const buttons = dom.el('div', null, 'pulse-buttons pulse-capture-actions');
    buttons.append(this.voiceButton());
    const cancel = dom.button('Cancel');
    cancel.addEventListener('click', () => this.dialog.close());
    buttons.append(cancel);
    const panel = dom.el('div', null, 'pulse-panel');
    const guidance = dom.el('div', null, 'pulse-guidance');
    guidance.append(this.status, this.hint);
    panel.append(guidance, progress, waveform);
    const layout = dom.el('div', null, 'pulse-live');
    layout.append(stage, panel);
    this.body.replaceChildren(layout);
    this.dialog.append(buttons);
  }

  /** @return {!Element} The spoken-guidance toggle. */
  voiceButton() {
    const dom = this.dom;
    const button = dom.button(null, 'pulse-voice');
    const paint = () => {
      const available = Boolean(localVoice());
      const on = this.speak && available;
      button.replaceChildren(
          dom.icon(on ? 'volume' : 'volume-off'), dom.el('span', 'Voice'));
      button.setAttribute('aria-pressed', String(on));
      // A device without an on-device voice has nothing to switch on.
      button.hidden = !available;
    };
    button.addEventListener('click', () => {
      this.speak = !this.speak;
      try {
        root.localStorage.setItem(SPEAK_KEY, this.speak ? 'on' : 'off');
      } catch (error) {
        // Without storage the choice lasts for this capture only.
      }
      if (!this.speak) {
        root.speechSynthesis?.cancel();
      }
      paint();
    });
    // Chrome lists its voices only after the page asks for them once.
    root.speechSynthesis?.addEventListener('voiceschanged', paint);
    if(this.paintVoice) root.speechSynthesis?.removeEventListener('voiceschanged',this.paintVoice);
    this.paintVoice = paint;
    paint();
    return button;
  }

  async measure() {
    this.stop();
    this.live();
    // Safari speaks only after a tap, so the first cue runs inside the Start
    // tap, before anything waits.
    this.cue('Take a moment to settle in. We’ll check your pulse together.', true);
    this.state = null;
    this.gotResult = false;
    this.runtimeMs=null;
    this.box = null;
    this.luma = {face: null, frame: null};
    this.lensStage = 'camera';
    this.measuringSaid = false;
    try {
      this.showStage('camera', 'Choose Allow if your browser asks');
      this.stream = await navigator.mediaDevices.getUserMedia(
          {video: {facingMode: 'user', width: {ideal: 640}}, audio: false});
      if (!this.dialog.open) {
        this.stop();
        return;
      }
      // Show the camera at once, so the person sees it working while the
      // pulse reader loads.
      this.video.srcObject = this.stream;
      this.video.play().catch(() => {});
      this.showStage('loading');
      this.lensStage = 'loading';
      const {VitalLens} = await withTimeout(import(LIBRARY), LOAD_MS, 'load');
      if (!this.dialog.open) {
        this.stop();
        return;
      }
      // POS runs on the device; the API method would upload video.
      if (this.engineChoice === 'facephys') {
        const {FacePhysEngine} = await import('/static/facephys-engine.js');
        this.engine = new FacePhysEngine();
        await withTimeout(this.engine.init(payload => this.onFacePhys(payload), () => {
          if (this.dialog.open && this.state) { this.stop(); this.failed('The scan paused — try again when you’re ready'); }
        }), LOAD_MS, 'FacePhys');
        if (!this.dialog.open) { this.stop(); return; }
      }
      this.lens = new VitalLens({method: 'pos'});
      this.lens.addEventListener('vitals', (result) => this.onVitals(result));
      this.lens.addEventListener('status', (event) => this.onLensStatus(event));
      await this.lens.setVideoStream(this.stream, this.video);
      this.lens.startVideoStream();
      this.lensStage = 'starting';
      this.showStage('face');
      this.state = new CaptureState(performance.now(), this.captureMs);
      const tick = () => {
        if (!this.lens) return;
        if (this.engine && performance.now() - (this.faceAt || 0) < 700) this.engine.frame(this.video, this.box, performance.now());
        this.frameLoop = requestAnimationFrame(tick);
      };
      tick();
      this.sampler = setInterval(() => this.sampleLight(), SAMPLE_MS);
      this.watchdog = setTimeout(() => this.startTimedOut(), FIRST_RESULT_MS);
      // The capture must settle even when the reader stops emitting events.
      this.deadline = setTimeout(() => this.captureTimedOut(), MAX_MS);
    } catch (error) {
      this.stop();
      if (error && error.name === 'TimeoutError') {
        this.failed(
            'Check your internet connection and try again',
            `load timed out after ${LOAD_MS / 1000} s`);
      } else if (this.lensStage === 'camera') {
        this.failed(cameraError(error), describe(error));
      } else {
        this.failed(START_FAILED, `${this.lensStage}: ${describe(error)}`);
      }
    }
  }

  /**
   * Shows a capture stage in the status line and the progress steps.
   * @param {string} stage A key of STAGE_TEXT.
   * @param {string=} hint Advice under the status line.
   * @param {string=} text Status text in place of the stage's own.
   */
  showStage(stage, hint = '', text = STAGE_TEXT[stage]) {
    setText(this.status, text);
    setText(this.hint, hint || ({face:'Bring your face into the oval', signal:'Rest your phone while the signal settles', measuring:'Breathe naturally', loading:'Your camera stays on while the reader loads'}[stage] || ''));
    this.dialog.dataset.capture = stage;
  }

  /**
   * Follows the pulse reader's start-up and fails on its errors.
   * @param {*} event A vitallens status event.
   */
  onLensStatus(event) {
    const stage = event && event.stage;
    if (!stage || !this.lens) {
      return;
    }
    this.lensStage = stage;
    if (stage === 'detectorFailed' || stage === 'startFailed') {
      this.stop();
      this.failed(START_FAILED, `${stage}: ${event.reason || ''}`);
    }
  }

  /** Ends a capture whose pulse reader never sent a result. */
  startTimedOut() {
    if (!this.lens || this.gotResult) {
      return;
    }
    const stage = this.lensStage;
    this.stop();
    this.failed(
        START_FAILED,
        `no result after ${FIRST_RESULT_MS / 1000} s (last stage: ${stage})`);
  }

  /** Completes an active capture when its independent deadline expires. */
  captureTimedOut() {
    if (this.lens && this.state) {
      this.finishCapture(this.state.lastResult());
    }
  }

  /** Samples brightness from a small canvas; nothing leaves the device. */
  sampleLight() {
    const video = this.video;
    if (!video || !video.videoWidth) {
      return;
    }
    const size = 48;
    this.canvas = this.canvas || document.createElement('canvas');
    this.canvas.width = size;
    this.canvas.height = size;
    const context = this.canvas.getContext('2d', {willReadFrequently: true});
    context.drawImage(video, 0, 0, size, size);
    const pixels = context.getImageData(0, 0, size, size).data;
    const luma = (x0, y0, x1, y1) => {
      let total = 0;
      let count = 0;
      for (let y = Math.max(0, y0); y < Math.min(size, y1); y++) {
        for (let x = Math.max(0, x0); x < Math.min(size, x1); x++) {
          const i = (y * size + x) * 4;
          total +=
              0.299 * pixels[i] + 0.587 * pixels[i + 1] + 0.114 * pixels[i + 2];
          count++;
        }
      }
      return count ? total / count : null;
    };
    this.luma.frame = luma(0, 0, size, size);
    if (this.box) {
      const scaleX = size / video.videoWidth;
      const scaleY = size / video.videoHeight;
      this.luma.face = luma(
          Math.floor(this.box[0] * scaleX), Math.floor(this.box[1] * scaleY),
          Math.ceil(this.box[2] * scaleX), Math.ceil(this.box[3] * scaleY));
    }
  }

  /** @param {*} result A vitallens vitals event. */
  onVitals(result) {
    if (!this.lens || !this.state) {
      return;
    }
    this.gotResult = true;
    const coordinates = result?.face?.coordinates;
    this.box = coordinates && coordinates.length ?
        coordinates[coordinates.length - 1] :
        null;
    this.faceAt = performance.now();
    if (this.engine) {
      if (!this.box) {
        const update = this.state.update({now:performance.now(), box:null, width:this.video.videoWidth, height:this.video.videoHeight, wave:0});
        if (update.done) this.finishCapture(update.result); else this.paintLive(update, []);
      }
      return;
    }
    const waveData = result?.waveforms?.ppg_waveform?.data;
    const samples = Array.isArray(waveData) ? waveData : [];
    const heart = result?.vitals?.heart_rate;
    const update = this.state.update({
      now: performance.now(),
      box: this.box,
      width: this.video.videoWidth,
      height: this.video.videoHeight,
      wave: samples.length,
      waveData: samples,
      waveTimes: Array.isArray(result?.time) ? result.time : undefined,
      bpm: heart && Number.isFinite(heart.value) ? heart.value : null,
      faceLuma: this.luma.face,
      frameLuma: this.luma.frame,
    });
    if (!update.done) {
      this.paintLive(update, samples);
      return;
    }
    this.finishCapture(update.result);
  }

  onFacePhys(payload) {
    if (!this.state || !this.lens) return;
    const update = this.state.update({now:performance.now(), box:performance.now()-this.faceAt<700 ? this.box : null,
      width:this.video.videoWidth, height:this.video.videoHeight, wave:1, waveData:[payload.value], waveTimes:[payload.timestamp/1000],
      bpm:null, faceLuma:this.luma.face, frameLuma:this.luma.frame});
    this.runtimeMs=payload.time;
    if (update.done) this.finishCapture(update.result); else this.paintLive(update,[payload.value]);
  }

  /** @param {!Object} result The completed reading or failure. */
  finishCapture(result) {
    result.method = this.engineChoice;
    result.runtimeMs = this.runtimeMs;
    result.conditions = {advisedFrames:this.state?.advisedFrames||0,totalFrames:this.state?.totalFrames||0};
    this.stop();
    if (result.ok) {
      this.result(result);
    } else {
      this.failed(FAILURE_TEXT[result.reason]);
    }
  }

  /**
   * @param {{issue: string, goodMs: number}} update
   * @param {!Array<number>} samples New waveform samples.
   */
  paintLive(update, samples) {
    const measuring = update.stage === 'measuring';
    let text = STAGE_TEXT[update.stage];
    if (measuring) {
      const left = Math.ceil((this.captureMs - update.goodMs) / 1000);
      text = left > 0 ? 'Taking your scan' : 'Finishing your scan';
      if (!this.measuringSaid) {
        this.measuringSaid = true;
        // One welcome and one completion cue are enough.
      }
    }
    this.showStage(update.stage, update.hint || (!measuring && this.state?.retained ? 'Your earlier signal is kept while we reconnect' : ''), text);
    this.dialog.classList.toggle('is-measuring', measuring);
    const usableMs = Math.max(update.goodMs, (this.state?.retained?.duration || 0) * 1000);
    this.progress.value = usableMs;
    setText(this.progressText, `${Math.min(this.captureMs / 1000, Math.floor(usableMs / 1000))} of ${this.captureMs / 1000} seconds`);
    setText(this.waveCaption, measuring && samples.length ? 'Live from your camera' : 'Waiting for your signal');
    if (!measuring) this.waveData = [];
    this.wave.closest('.pulse-live-signal')?.classList.toggle('has-signal', measuring && this.waveData.length + samples.length > 1);
    this.drawWave(measuring ? samples : []);
  }

  /** @param {!Array<number>} samples New waveform samples. */
  drawWave(samples) {
    this.waveData.push(...samples.filter(Number.isFinite));
    this.waveData = this.waveData.slice(-WAVE_SAMPLES);
    const canvas = this.wave;
    const ratio = root.devicePixelRatio || 1;
    const width = Math.round(canvas.clientWidth * ratio);
    const height = Math.round(canvas.clientHeight * ratio);
    if (!width || !height) {
      return;
    }
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    const context = canvas.getContext('2d');
    context.clearRect(0, 0, width, height);
    const values = this.waveData;
    if (values.length < 2) {
      return;
    }
    const min = Math.min(...values);
    const span = Math.max(...values) - min || 1;
    const pad = 3 * ratio;
    context.strokeStyle =
        getComputedStyle(this.dialog).getPropertyValue('--chart-series');
    context.lineWidth = 2 * ratio;
    context.lineJoin = 'round';
    context.lineCap = 'round';
    context.beginPath();
    values.forEach((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - pad - ((value - min) / span) * (height - 2 * pad);
      if (index) {
        context.lineTo(x, y);
      } else {
        context.moveTo(x, y);
      }
    });
    context.stroke();
  }

  /**
   * Captions a short cue and speaks it with an on-device voice, unless the
   * person turned the voice off.
   * @param {string} text
   * @param {boolean=} force Ignore the quiet gap between cues.
   */
  cue(text, force = false) {
    const now = performance.now();
    if (text === this.spoken ||
        (!force && now - this.spokenAt < SPEAK_GAP_MS)) {
      return;
    }
    this.spoken = text;
    this.spokenAt = now;
    const voice = this.speak ? localVoice() : null;
    if (!voice) {
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = voice;
    utterance.rate = 0.95;
    root.speechSynthesis.cancel();
    root.speechSynthesis.speak(utterance);
  }

  /** @param {{bpm: number, quality: number}} summary */
  result(summary) {
    const dom=this.dom;
    this.setStage('done');
    summary.method ||= this.engineChoice;
    summary.methodVersion = summary.method === 'facephys' ? '0007a97565872f5845824802eccec0258305c98d' : 'vitallens-0.4.8';
    summary.capturedAt ||= new Date().toISOString();
    const captureId=crypto.randomUUID();
    this.cue('Your scan is ready. Take a look when you’re ready.',true);
    const heading=dom.el('div',null,'pulse-result-heading');
    heading.append(dom.el('p',new Intl.DateTimeFormat(undefined, {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}).format(new Date(summary.capturedAt)),'pulse-note'));
    const metrics=dom.el('div',null,'pulse-metrics');
    for(const [label,value,unit,detail] of [
      ['Pulse',summary.bpm,'bpm',''],
      ['Breathing',summary.breathing?.rate,'breaths/min',summary.breathing ? '' : this.captureMs < 60000 ? 'A longer scan may help' : 'Try another scan while resting'],
      ['Pulse variability',summary.hrv?.rmssd,'ms',summary.hrv ? 'Camera-derived RMSSD' : 'Try resting your phone for steadier beat timing'],
    ]) {
      const card=dom.el('section',null,'pulse-metric');
      const figure=dom.el('p',null,'pulse-figure');
      figure.append(dom.el('span',value == null ? '—' : String(value),'pulse-value'),dom.el('span',unit,'pulse-unit'));
      card.append(dom.el('h4',label),figure);
      if(detail) card.append(dom.el('p',detail,'pulse-note'));
      metrics.append(card);
    }
    const context=dom.el('section',null,'pulse-context');
    const noteLabel=dom.el('label','Add a note','pulse-field');
    const note=dom.el('textarea'); note.rows=2; note.maxLength=200; note.value=this.draftNote || ''; note.placeholder='How you feel or what you were doing'; noteLabel.append(note);
    const bookLabel=dom.el('label','Notebook','pulse-field');
    const book=dom.el('select'); const general=dom.el('option','General history');general.value='';book.append(general);bookLabel.append(book);
    context.append(noteLabel,bookLabel);
    const comparison=dom.el('section',null,'pulse-comparison');
    const compare=()=>{
      const previous=this.showComparison && (this.measureInfo?.points || []).filter(p=>p.source==='camera' && p.notebook_id===(book.value||null)).reverse().find(p=>{
        try { const evidence=p.scan_evidence || {}; return evidence.method===summary.method && evidence.methodVersion===summary.methodVersion && evidence.duration>=30 && summary.duration>=30 && Math.abs(evidence.duration-summary.duration)/summary.duration < .25 && Math.abs(p.quality-summary.quality)<.2 && evidence.context===note.value; } catch { return false; }
      });
      this.comparison=previous ? {previous:{value:previous.value,time:previous.time},current:{value:summary.bpm,time:summary.capturedAt},difference:summary.bpm-previous.value} : null;
      comparison.replaceChildren(dom.el('h3', previous ? 'Since your last scan' : 'Comparison'));
      comparison.hidden = !this.showComparison;
      if (previous) {
        const date = new Intl.DateTimeFormat(undefined, {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}).format(new Date(previous.time));
        comparison.append(dom.el('p', `Previous scan ${date}`, 'pulse-note'));
        const table = dom.el('table', null, 'pulse-comparison-table');
        const head = dom.el('tr');
        for (const label of ['Measurement', 'Before', 'Now', 'Change']) {
          const th = dom.el('th', label); th.scope = 'col'; head.append(th);
        }
        const thead = dom.el('thead'); thead.append(head); table.append(thead);
        const tbody = dom.el('tbody');
        const prior = previous.scan_evidence;
        for (const [label, old, current, unit] of [
          ['Pulse', previous.value, summary.bpm, 'bpm'],
          ['Breathing', prior.breathing?.rate, summary.breathing?.rate, 'breaths/min'],
          ['RMSSD', prior.variability?.rmssd, summary.hrv?.rmssd, 'ms'],
          ['SDNN', prior.variability?.sdnn, summary.hrv?.sdnn, 'ms'],
        ]) {
          if (!Number.isFinite(old) || !Number.isFinite(current)) continue;
          const difference = current-old;
          const row = dom.el('tr');
          const labelCell = dom.el('th', label); labelCell.scope = 'row';
          labelCell.append(dom.el('span', unit, 'pulse-note'));
          row.append(labelCell, dom.el('td', String(old)), dom.el('td', String(current)), dom.el('td', `${difference > 0 ? '+' : ''}${difference}`));
          tbody.append(row);
          this.comparison[label] = {previous:old, current, difference};
        }
        table.append(tbody); comparison.append(table);
        comparison.append(dom.el('p', 'Same method and note with similar scan length and quality', 'pulse-note'));
      } else {
        comparison.append(dom.el('p', (this.measureInfo?.points || []).length ? 'No earlier scan matches this method and context' : 'This scan starts your comparison history', 'pulse-note'));
      }
    };
    compare();
    const evidence = dom.el('details', null, 'pulse-evidence');
    evidence.append(dom.el('summary', 'Signals and accuracy'));
    const charts = dom.el('div', null, 'pulse-charts');
    const chart = (title, description, labels) => {
      const figure = dom.el('figure', null, 'pulse-chart');
      const caption = dom.el('figcaption', title);
      const canvas = dom.el('canvas', null, 'pulse-wave');
      canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', description);
      const axis = dom.el('div', null, 'pulse-axis');
      for (const label of labels) axis.append(dom.el('span', label));
      figure.append(caption, canvas, axis);
      charts.append(figure);
      return canvas;
    };
    const points = summary.recording || [];
    const last = points.slice(-150);
    const waveDuration = last.length > 1 ? (last.at(-1).t-last[0].t).toFixed(1) : '0';
    const signal = chart('Pulse waveform', `Last ${waveDuration} seconds of the accepted signal with relative amplitude`, [`−${waveDuration} s`, 'Time', '0 s']);
    const spectrum = chart('Frequency spectrum', `Relative signal power from 40 to 180 beats per minute with an accepted pulse of ${summary.bpm} beats per minute`, ['40', 'Pulse · bpm', '180']);
    const facts = dom.el('dl', null, 'pulse-facts');
    for (const [label, value] of [
      ['Usable signal', `${Math.round(summary.duration || 0)} seconds`],
      ['Signal quality', `${Math.round(summary.quality * 100)}%`],
      ['Breathing evidence', summary.breathing ? summary.breathing.sources?.map(source => ({height:'Pulse amplitude', interval:'Beat timing', motion:'Face movement'}[source] || source)).join(' and ') || 'Matching respiratory rhythms' : 'Not enough matching respiratory rhythms'],
      ['Beat intervals', summary.hrv ? `${summary.hrv.intervals} accepted` : 'Too few reliable intervals'],
      ['RMSSD', summary.hrv ? `${summary.hrv.rmssd} ms` : 'Unavailable'],
      ['SDNN', summary.hrv ? `${summary.hrv.sdnn} ms` : 'Unavailable'],
      ['Method', summary.method === 'facephys' ? 'FacePhys · LiteRT WASM' : 'POS comparison'],
      ['Processing', 'On this device'],
      ...(summary.runtimeMs ? [['Model frame', `${summary.runtimeMs.toFixed(1)} ms`]] : []),
      ['Frames with advice', `${summary.conditions?.advisedFrames || 0} of ${summary.conditions?.totalFrames || 0}`],
    ]) facts.append(dom.el('dt', label), dom.el('dd', value));
    const signalInfo = dom.el('div', null, 'pulse-signal-info');
    signalInfo.append(facts, dom.el('p', 'RMSSD and SDNN describe variation between camera-detected beats', 'pulse-note'));
    charts.append(dom.el('p', 'Wave height and spectrum power use relative scales', 'pulse-note'));
    const signalLayout = dom.el('div', null, 'pulse-signal-layout');
    signalLayout.append(charts, signalInfo);
    evidence.append(signalLayout, dom.accuracyNote('camera', 'About accuracy'));
    const powers = [];
    if (points.length > 1) {
      const mean = points.reduce((a, p) => a + p.v, 0) / points.length;
      for (let bpm = 40; bpm <= 180; bpm++) {
        let re = 0, im = 0;
        points.forEach((p, i) => {
          const v = (p.v-mean) * (.5-.5*Math.cos(2*Math.PI*i/(points.length-1)));
          const phase = 2*Math.PI*bpm/60*(p.t-points[0].t);
          re += v*Math.cos(phase); im += v*Math.sin(phase);
        });
        powers.push(re*re+im*im);
      }
    }
    if (powers.length) {
      const peak = powers.indexOf(Math.max(...powers)) + 40;
      spectrum.parentElement.append(dom.el('p', `Strongest rhythm ${peak} bpm`, 'pulse-note'));
    }
    const paint = () => {
      this.wave = signal; this.waveData = []; this.drawWave(last.map(p => p.v));
      this.wave = spectrum; this.waveData = []; this.drawWave(powers);
    };
    evidence.addEventListener('toggle', () => { if (evidence.open) paint(); });
    this.resultObserver?.disconnect();
    this.resultObserver = new ResizeObserver(() => { if (evidence.open) paint(); });
    this.resultObserver.observe(charts);
    const research = dom.el('details', null, 'pulse-research');
    research.hidden = true;
    research.append(dom.el('summary', 'Research estimate'));
    const researchValues = dom.el('dl', null, 'pulse-more'); researchValues.hidden = true;
    research.append(researchValues); evidence.append(research);
    this.bloodPressure(summary, researchValues);
    const check=dom.el('details',null,'pulse-checkin'); check.append(dom.el('summary','Ask Stellis about this scan'));
    const sources=dom.el('div',null,'pulse-sources');
    const checkStatus=dom.el('p','Choose a notebook above to use its records','pulse-note');
    const consent=dom.el('label',null,'pulse-consent'); const agree=dom.el('input');agree.type='checkbox';
    consent.append(agree,dom.el('span','I agree to send this scan and selected records with notebook chat history to Nemotron on Nebius'));
    const privacy=dom.el('p','Your check-in stays in notebook chat until you delete it','pulse-note');

    const policy=dom.el('a','Provider retention and withdrawal'); policy.href='/privacy#retention';policy.target='_blank';policy.rel='noopener';
    const ask=dom.button('Start check-in');ask.disabled=true;
    const reply=dom.el('div',null,'pulse-reply');reply.setAttribute('aria-live','polite');
    check.append(checkStatus,sources,consent,privacy,policy,ask,reply);
    let inferenceReady = false;
    const refreshAsk = () => { ask.disabled = asking || saving || !inferenceReady || !agree.checked || !book.value || !sourceIds.length; };
    dom.api('/capabilities').then(data => {
      if (!check.isConnected) return;
      inferenceReady = Boolean(data.ai?.configured);
      if (!inferenceReady) checkStatus.textContent = 'Set up AI in Settings to use this check-in';
      refreshAsk();
    }).catch(() => { checkStatus.textContent = 'Stellis could not check the connection'; });
    if(this.isolated) {check.hidden=true; bookLabel.hidden=true;}
    let sourceIds=[],checkin=null,revision=0,sourcesRevision=0,asking=false,saving=false;
    const invalidate=()=>{revision++;checkin=null;reply.replaceChildren();agree.checked=false;ask.disabled=true;compare();};
    note.addEventListener('input',()=>{this.draftNote=note.value;invalidate();});
    book.addEventListener('change',async()=>{
      this.notebookId=book.value;invalidate();sources.replaceChildren();sourceIds=[];const rev=++sourcesRevision;
      if(!book.value){checkStatus.textContent='Choose a notebook above to use its records';return;}
      // Editing the note invalidates an answer, not this notebook's pending records.
      try {const data=await dom.api(`/notebooks/${encodeURIComponent(book.value)}/sources`);if(rev!==sourcesRevision||!sources.isConnected)return;
        checkStatus.textContent = data.sources.some(s=>s.status==='ready') ? (inferenceReady ? 'Choose the records you want Stellis to use' : 'Set up AI in Settings to use this check-in') : 'Add a record to this notebook to use it in a check-in';
        for(const source of data.sources.filter(s=>s.status==='ready')) {
          const label=dom.el('label',null,'pulse-consent'),box=dom.el('input');box.type='checkbox';
          box.onchange=()=>{invalidate();sourceIds=box.checked?[...sourceIds,source.id]:sourceIds.filter(id=>id!==source.id);};
          label.append(box,dom.el('span',source.title||source.name||source.id));sources.append(label);
        }
      }catch {if(rev===sourcesRevision&&sources.isConnected)reply.textContent='Choose the notebook again to reload its records';}
    });
    agree.onchange=()=>{if(!agree.checked){revision++;checkin=null;}refreshAsk();};
    ask.onclick=async()=>{
      if(asking||saving||!inferenceReady||!agree.checked||!book.value||!sourceIds.length)return;asking=true;ask.disabled=true;save.disabled=true;const rev=revision;const began=performance.now();
      reply.textContent='Stellis is reviewing your check-in';
      try {
        const evidence={pulse:summary.bpm,breathing:summary.breathing?.rate,variability:summary.hrv,duration:summary.duration,quality:summary.quality,method:summary.method,capturedAt:summary.capturedAt,comparison:this.comparison,reportedContext:note.value};
        const response=await dom.api(`/notebooks/${encodeURIComponent(book.value)}/ask`,{method:'POST',json:{source_ids:sourceIds,request_id:crypto.randomUUID(),question:'Camera check-in. Measurements and reported context are distinct. Use only these observations and selected records. Use prior check-in context only if present in this thread. Use the supplied deterministic differences without calculating new comparisons. Treatment dates are context and do not establish causation. Do not diagnose or recommend treatment. Give a brief source-linked explanation and one focused follow-up plus two questions for a future visit. State when evidence is insufficient. Observation: '+JSON.stringify(evidence)}});
        if(rev!==revision||!reply.isConnected)return;
        checkin={id:response.id,notebook_id:book.value,trace_id:response.trace_id};
        const citations = (response.citations || []).filter(c => c.target?.startsWith('/') && !c.target.startsWith('//'));
        const answer = root.StellisNotebookChat?.answerBody(response.content, citations, citation => {
          const link = dom.el('a', String(citation.n), 'nb-cite');
          link.href=citation.target; link.target='_blank'; link.rel='noopener';
          link.setAttribute('aria-label', `Source ${citation.n}: ${citation.title}`);
          return link;
        });
        reply.replaceChildren(answer || dom.el('p',response.content));
        for(const citation of response.citations||[]) {if(!citation.target?.startsWith('/')||citation.target.startsWith('//'))continue;const link=dom.el('a',citation.title);link.href=citation.target;link.target='_blank';link.rel='noopener';reply.append(link);}
        const runtime=dom.el('details',null,'pulse-checkin-runtime');
        runtime.append(dom.el('summary','Check-in details'), dom.el('p',`Reply received in ${Math.round((performance.now()-began)/1000)} seconds`,'pulse-note'));
        const trace=root.StellisAnswerTrace?.link({traceId:response.trace_id});
        if(trace) runtime.append(trace);
        else runtime.append(dom.el('p',`Reference ${response.trace_id||response.id}`,'pulse-note'));
        reply.append(runtime);
      }catch {if(rev===revision)reply.textContent='Stellis could not reply this time — you can still save your scan';}
      finally {asking=false;if(reply.isConnected&&!saving){refreshAsk();save.disabled=!approve.checked;}}
    };
    if(!this.isolated) dom.api('/notebooks').then(data=>{if(!book.isConnected)return;for(const b of data.notebooks){const option=dom.el('option',b.name);option.value=b.id;book.append(option);}if(this.notebookId){book.value=this.notebookId;book.dispatchEvent(new Event('change'));}}).catch(()=>{bookLabel.append(dom.el('p','Notebooks unavailable','pulse-note'));});
    const consentSave=dom.el('label',null,'pulse-consent'), approve=dom.el('input');approve.type='checkbox';
    consentSave.append(approve,dom.el('span',this.isolated?'Keep this scan for this demo session':'Save this scan to my Stellis history until I delete it'));
    const error=dom.el('p',null,'pulse-error');error.setAttribute('role','alert');
    const save=dom.button('Save scan','pulse-primary');save.disabled=true;approve.onchange=()=>{save.disabled=asking||!approve.checked;};
    const offerLonger = !summary.breathing && this.captureMs < 60000;
    const again=dom.button(offerLonger ? 'Scan for 60 seconds' : 'Scan again');
    again.onclick=()=>{revision++;if(offerLonger)this.captureMs=60000;this.measure();};

    const buttons=dom.el('div',null,'pulse-buttons pulse-save-actions');buttons.append(again,save);
    let pending=null,index=0,saved=false;
    save.onclick=async()=>{
      if(saved||save.disabled)return;saving=true;save.disabled=true;again.disabled=true;
      note.disabled=true;book.disabled=true;approve.disabled=true;ask.disabled=true;
      for (const input of check.querySelectorAll('input')) input.disabled=true;
      pending ||= readingsToSave(summary).map((json,i)=>({...json,taken_at:summary.capturedAt,capture_id:`${captureId}-${i}`,notebook_id:book.value||null,note:note.value,scan_evidence:{version:1,observation:captureId,method:summary.method,methodVersion:summary.methodVersion,duration:summary.duration,quality:summary.quality,conditions:summary.conditions,variability:summary.hrv,breathing:summary.breathing,context:note.value,checkin}}));
      try {while(index<pending.length){await this.saveReading(pending[index]);index++;}saved=true;save.textContent='Saved to history';await this.onSaved?.();this.dialog.close();}
      catch {error.textContent=saved?'Your scan is saved — close to return to history':'Your scan is here — retry to finish saving';save.textContent='Retry save';save.disabled=saved;}
    };
    const observations = dom.el('div', null, 'pulse-observations');
    observations.append(heading, metrics, comparison);
    const journal = dom.el('div', null, 'pulse-journal');
    journal.append(context, check);
    const review = dom.el('div', null, 'pulse-review');
    review.append(observations, journal);
    const footer = dom.el('div', null, 'pulse-save');
    footer.append(consentSave);
    footer.append(error, buttons);
    this.body.replaceChildren(review, evidence);
    this.dialog.append(footer);
    for (const link of this.body.querySelectorAll('.accuracy-note')) { link.target = '_blank'; link.rel = 'noopener'; }
    heading.tabIndex=-1;heading.focus({preventScroll:true});
    if(!matchMedia('(prefers-reduced-motion: reduce)').matches) metrics.animate([{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:260,easing:'cubic-bezier(.22,1,.36,1)'});
  }

  /**
   * Adds the blood pressure estimate under the other readings once the
   * network has run. It is shown, never saved: it is not a cuff reading.
   * @param {!Object} summary A finished reading.
   * @param {!Element} more The result's list of readings.
   */
  async bloodPressure(summary, more) {
    const estimator = root.StellisBpEstimate;
    if (!estimator || !summary.recording) {
      return;
    }
    let pressure = null;
    try {
      pressure = await estimator.estimate(summary.recording);
    } catch (failure) {
      console.warn('Blood pressure estimate:', failure);
    }
    if (!pressure || !more.isConnected) {
      return;
    }
    const dom = this.dom;
    const value = dom.el('dd', `${pressure.systolic}/${pressure.diastolic} mmHg`);
    const accuracy = dom.accuracyNote('blood-pressure', 'About this estimate');
    accuracy.target = '_blank'; accuracy.rel = 'noopener';
    value.append(accuracy);
    more.append(dom.el('dt', 'Blood pressure'), value);
    more.hidden = false;
    more.parentElement.hidden = false;
    more.after(dom.el('p', 'Blood pressure is a research estimate and not for diagnosis', 'pulse-note'));
  }

  /**
   * @param {string} message What happened and what to do.
   * @param {string=} details A technical reason, shown small for bug reports.
   */
  failed(message, details = '') {
    const dom = this.dom;
    this.setStage('done');
    this.gotResult = false;
    const retry = dom.button('Try again', 'pulse-primary');
    retry.addEventListener('click', () => this.measure());
    this.body.replaceChildren(
        dom.el('p', 'No reading yet', 'pulse-label'),
        dom.el('p', message, 'pulse-message'), retry);
    if (details) {
      console.warn('Camera pulse:', details);
      this.body.append(dom.el('p', `Details: ${details}`, 'pulse-details'));
    }
    retry.focus();
  }

  /** Stops the camera, the light sampler and vitallens. */
  stop() {
    cancelAnimationFrame(this.frameLoop);
    this.engine?.close(); this.engine=null;
    clearInterval(this.sampler);
    clearTimeout(this.watchdog);
    clearTimeout(this.deadline);
    this.observer?.disconnect();
    this.resultObserver?.disconnect();
    if (this.lens) {
      const lens = this.lens;
      this.lens = null;
      lens.stopVideoStream();
      lens.close().catch(() => {});
    }
    if (this.stream) {
      for (const track of this.stream.getTracks()) {
        track.stop();
      }
      this.stream = null;
    }
  }
}

/**
 * Opens the measuring dialog.
 * @param {function(): !Promise<void>} onSaved
 * @param {?Object=} measure The pulse measure, for its usual range.
 * @param {{saveReading: Function, showComparison: boolean}=} options Optional host adapter; native saving remains the default.
 */
function open(onSaved, measure = null, options = {}) {
  new PulseDialog(onSaved, measure, options).open();
}

/** @return {boolean} Whether this browser can open a camera. */
function supported() {
  return Boolean(
      root.navigator && navigator.mediaDevices &&
      navigator.mediaDevices.getUserMedia);
}

/** The readings computed from one camera recording. */
const MEASURE_KEYS = Object.freeze(['pulse', 'breathing_rate', 'hrv']);

/**
 * Builds the shared action for one camera capture.
 * @param {function(): !Promise<void>} onSaved
 * @param {!Array<!Object>} measures
 * @param {string} className
 * @return {!HTMLButtonElement}
 */
function logButton(onSaved, measures, className) {
  const dom = root.StellisDom;
  const button = dom.button(null, className + ' invert-button');
  button.append(dom.icon('camera'), dom.el('span', 'Log vital signs'));
  button.disabled = !supported();
  if (button.disabled) button.title = 'Camera access is unavailable in this browser';
  button.addEventListener(
      'click', () => open(onSaved, measures.find((m) => m.key === 'pulse')));
  return button;
}

const exported = {
  MEASURE_KEYS,
  logButton,
  CaptureState,
  GOOD_MS,
  MAX_MS,
  stepStates,
  faceCheck,
  lightCheck,
  open,
  ovalPath,
  readingsToSave,
  speakChosen,
  stillCheck,
  summarize,
  supported,
  usualText,
};
root.StellisCameraPulse = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
