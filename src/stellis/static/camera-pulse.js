/**
 * @fileoverview Measures the pulse from the front camera with vitallens'
 * local POS method. The video never leaves the browser: vitallens runs face
 * detection and the pulse method on the device, brightness is sampled from a
 * small canvas, and only the final number and a quality score are saved.
 *
 * The countdown runs while a face and a pulse signal are found and pauses,
 * without resetting, when either is lost. Distance, light and movement only
 * lower the quality and show advice. The reading is the waveform's strongest
 * rhythm.
 */
(function(root) {
'use strict';

const LIBRARY = '/static/vendor/vitallens/vitallens.browser.js';
const math = root.StellisVitalsMath || require('./vitals-math.js');
/** The pulse waveform quality above which HRV counts as sure. */
const HRV_MIN_QUALITY = 0.6;
/** Milliseconds of good signal a reading needs. */
const GOOD_MS = 30000;
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
  off: 'Keeping your face in the oval gives a better reading',
  far: 'Sitting a little closer gives a better reading',
  close: 'Sitting a little farther back gives a better reading',
  dark: 'More light on your face gives a better reading',
  backlit: 'Facing the light gives a better reading',
  bright: 'Softer light gives a better reading',
  moving: 'Staying still gives a better reading',
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
  const hrv = math.heartRateVariability(beats);
  const breathing = math.breathingRate(beats, motion);
  return {
    ok: true,
    bpm: summary.bpm,
    quality: summary.quality,
    approximate: !summary.sure,
    hrv: hrv && {
      rmssd: hrv.rmssd,
      approximate: !(hrv.sure && summary.quality >= HRV_MIN_QUALITY),
    },
    breathing: breathing && {
      rate: breathing.rate,
      approximate: !breathing.sure,
    },
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
  constructor(start) {
    this.start = start;
    this.last = start;
    this.goodMs = 0;
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
    const step = Math.min(event.now - this.last, MAX_STEP_MS);
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
    if (stage === 'measuring') {
      this.goodMs += step;
      this.samples.push({t: this.goodMs, bpm: event.bpm});
      this.addWave(event.waveData, event.waveTimes);
      this.motion.push({t: event.now / 1000, v: this.centres.at(-1).y});
    } else {
      this.missing[stage] += step;
    }
    if (issue !== this.issue) {
      this.issue = issue;
      this.issueSince = event.now;
    }
    const hint =
        issue && event.now - this.issueSince >= HINT_MS ? HINTS[issue] : '';
    if (this.goodMs >= GOOD_MS) {
      const summary = this.summary();
      if (summary.bpm != null &&
          (summary.sure || this.goodMs >= GOOD_MS + EXTRA_MS)) {
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
      this.wave.push({t: times[i], v});
      this.recording.push({t: times[i], v});
    });
    const from = this.wave[this.wave.length - 1].t - WINDOW_MS / 1000;
    const first = this.wave.findIndex((point) => point.t >= from);
    this.wave.splice(0, Math.max(0, first));
  }

  /** @return {{bpm: ?number, quality: number, sure: boolean}} */
  summary() {
    return summarize(this.samples, this.wave);
  }

  /**
   * The result when time runs out: a reading from enough good signal,
   * otherwise the check that failed longest.
   * @return {!Object}
   */
  lastResult() {
    const summary = this.summary();
    if (this.goodMs >= MIN_GOOD_MS && summary.bpm != null) {
      return reading(summary, this.recording, this.motion);
    }
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
 * Spoken guidance is on unless the person turned it off on this device.
 * @return {boolean}
 */
function speakChosen() {
  try {
    return root.localStorage.getItem(SPEAK_KEY) !== 'off';
  } catch (error) {
    return true;
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
    this.usual = measure ? measure.usual : null;
    this.speak = speakChosen();
    this.spokenAt = -Infinity;
    this.spoken = '';
    this.dialog = /** @type {!HTMLDialogElement} */ (
        dom.el('dialog', null, 'pulse-dialog'));
    this.dialog.setAttribute('aria-labelledby', 'pulse-title');
    const title = dom.el('h2', 'Log vital signs');
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
    const readings = dom.el(
        'p', 'Pulse, breathing rate, heart rate variability and a blood pressure estimate',
        'pulse-note');
    const tips = dom.el('ul', null, 'pulse-tips');
    for (const text
             of ['Rest for a minute before you start',
                 'Sit facing a window or lamp',
                 'Hold still for about 30 seconds',
                 'The video stays on this device',
    ]) {
      tips.append(dom.el('li', text));
    }
    const start = dom.button('Start', 'pulse-primary');
    start.addEventListener('click', () => this.measure());
    this.setStage('intro');
    const note =
        dom.el('p', 'A home estimate for following your trends ', 'pulse-note');
    note.append(dom.accuracyNote('camera', 'About accuracy'));
    this.body.replaceChildren(readings, tips, start, note);
    start.focus();
  }

  /**
   * Marks which screen shows, which sets the dialog's layout.
   * @param {string} stage 'intro', 'live' or 'done'.
   */
  setStage(stage) {
    this.dialog.dataset.stage = stage;
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
    const steps = dom.el('ol', null, 'pulse-steps');
    steps.setAttribute('aria-label', 'Progress');
    this.steps = {};
    for (const [key, label] of STEPS) {
      const step = dom.el('li', null, 'pulse-step');
      step.append(
          dom.el('span', null, 'pulse-step-dot'), dom.el('span', label));
      this.steps[key] = step;
      steps.append(step);
    }
    this.wave =
        /** @type {!HTMLCanvasElement} */ (
            dom.el('canvas', null, 'pulse-wave'));
    this.wave.setAttribute('aria-hidden', 'true');
    this.waveData = [];

    const buttons = dom.el('div', null, 'pulse-buttons');
    buttons.append(this.voiceButton());
    const cancel = dom.button('Cancel');
    cancel.addEventListener('click', () => this.dialog.close());
    buttons.append(cancel);
    const panel = dom.el('div', null, 'pulse-panel');
    panel.append(steps, this.status, this.hint, this.wave, buttons);
    const layout = dom.el('div', null, 'pulse-live');
    layout.append(stage, panel);
    this.body.replaceChildren(layout);
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
    this.paintVoice = paint;
    paint();
    return button;
  }

  async measure() {
    this.live();
    // Safari speaks only after a tap, so the first cue runs inside the Start
    // tap, before anything waits.
    this.cue(STAGE_TEXT.camera, true);
    this.state = null;
    this.gotResult = false;
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
      this.lens = new VitalLens({method: 'pos'});
      this.lens.addEventListener('vitals', (result) => this.onVitals(result));
      this.lens.addEventListener('status', (event) => this.onLensStatus(event));
      await this.lens.setVideoStream(this.stream, this.video);
      this.lens.startVideoStream();
      this.lensStage = 'starting';
      this.showStage('face');
      this.state = new CaptureState(performance.now());
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
    setText(this.hint, hint);
    const states = stepStates(stage);
    for (const [key] of STEPS) {
      this.steps[key].dataset.state = states[key];
    }
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

  /** @param {!Object} result The completed reading or failure. */
  finishCapture(result) {
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
      const left = Math.ceil((GOOD_MS - update.goodMs) / 1000);
      text = left > 0 ? `About ${left} seconds left` : 'Almost done';
      if (!this.measuringSaid) {
        this.measuringSaid = true;
        this.cue(STAGE_TEXT.measuring, true);
      }
    }
    this.showStage(update.stage, update.hint, text);
    this.dialog.classList.toggle('is-measuring', measuring);
    const progress = Math.min(update.goodMs / GOOD_MS, 1) * 100;
    this.ring.setAttribute('stroke-dasharray', `${progress.toFixed(1)} 100`);
    this.drawWave(samples);
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
      const x = (index / (WAVE_SAMPLES - 1)) * width;
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
    const dom = this.dom;
    this.setStage('done');
    this.cue(`Your pulse is ${summary.bpm} beats per minute`, true);
    const figure = dom.el('p', null, 'pulse-figure');
    figure.append(
        dom.el('span', String(summary.bpm), 'pulse-value'),
        dom.el('span', 'bpm', 'pulse-unit'));
    const error = dom.el('p', null, 'pulse-error');
    error.setAttribute('role', 'alert');
    const save = dom.button('Save', 'pulse-primary');
    save.addEventListener('click', async () => {
      save.disabled = true;
      try {
        for (const json of readingsToSave(summary)) {
          await this.saveReading(json);
        }
        this.dialog.close();
        await this.onSaved();
      } catch (failure) {
        error.textContent = failure.message;
        save.disabled = false;
      }
    });
    const again = dom.button('Measure again');
    again.addEventListener('click', () => this.measure());
    const buttons = dom.el('div', null, 'pulse-buttons');
    buttons.append(save, again);
    this.body.replaceChildren(
        dom.el('p', 'Your pulse', 'pulse-label'), figure,
        dom.el('p', this.showComparison ? usualText(summary.bpm, this.usual) : '', 'pulse-usual'));
    const more = dom.el('dl', null, 'pulse-more');
    for (const row of extraRows(summary)) {
      const value = dom.el('dd', row.value);
      if (row.approximate) {
        value.append(dom.el('span', 'approximate', 'pulse-tag'));
      }
      more.append(dom.el('dt', row.label), value);
    }
    more.hidden = !more.childElementCount;
    this.body.append(more);
    this.bloodPressure(summary, more);
    if (summary.approximate) {
      this.body.append(dom.el(
          'p', 'Measure again sitting still for a closer reading',
          'pulse-note'));
    }
    this.body.append(
        dom.accuracyNote('camera', 'About accuracy'), error, buttons);
    save.focus();
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
    value.append(dom.accuracyNote('blood-pressure', 'Estimate'));
    more.append(dom.el('dt', 'Blood pressure'), value);
    more.hidden = false;
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
    clearInterval(this.sampler);
    clearTimeout(this.watchdog);
    clearTimeout(this.deadline);
    this.observer?.disconnect();
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
