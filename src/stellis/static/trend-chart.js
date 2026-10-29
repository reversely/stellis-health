/**
 * @fileoverview Draws one measure's readings over time as an SVG chart: a
 * line (or a bar per blood pressure reading), the person's usual range as a
 * band, source-shaped markers, and a crosshair tooltip that follows the
 * pointer or the arrow keys.
 */
(function(root) {
'use strict';

const SVG = 'http://www.w3.org/2000/svg';
const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov',
  'Dec'
];
const DAY = 24 * 60 * 60 * 1000;
const MARGIN = {
  top: 12,
  right: 14,
  bottom: 28,
  left: 40
};
const PLOT_HEIGHT = 160;
/** Markers closer than this many pixels are skipped, except the latest. */
const MARKER_GAP = 12;
/**
 * The smallest y span per measure, so normal day-to-day variation is not
 * drawn as a large swing.
 */
const MIN_SPAN = {
  weight: 6,
  blood_pressure: 50,
  pulse: 30,
  breathing_rate: 8,
  hrv: 20,
  hba1c: 2,
};
const SOURCE_LABELS = {
  record: 'From your records',
  self: 'Manual entry',
  camera: 'Camera reading',
};

/**
 * Parses a reading time. A bare date is local midnight, so it never shifts
 * to the day before in timezones west of UTC.
 * @param {string} text
 * @return {number} Milliseconds since the epoch.
 */
function parseTime(text) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    const [year, month, day] = text.split('-').map(Number);
    return new Date(year, month - 1, day).getTime();
  }
  return Date.parse(text);
}

/**
 * Formats a time as "14 Mar 2024".
 * @param {number} time
 * @return {string}
 */
function longDate(time) {
  const date = new Date(time);
  return `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
}

/**
 * Formats a reading's value, "88/64" for a pair.
 * @param {{value: number, value2: ?number}} point
 * @param {number} decimals
 * @return {string}
 */
function formatValue(point, decimals) {
  const one = (value) => Number(value).toFixed(decimals);
  return point.value2 == null ? one(point.value) :
                                `${one(point.value)}/${one(point.value2)}`;
}

/**
 * Picks round tick values covering a range.
 * @param {number} min
 * @param {number} max
 * @param {number} count Roughly how many ticks to return.
 * @return {!Array<number>}
 */
function niceTicks(min, max, count) {
  if (min === max) {
    return [min];
  }
  const rough = (max - min) / Math.max(count - 1, 1);
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10]
                   .map((factor) => factor * power)
                   .find((candidate) => candidate >= rough);
  const ticks = [];
  for (let tick = Math.ceil(min / step) * step; tick <= max + step * 1e-9;
       tick += step) {
    ticks.push(Number(tick.toFixed(10)));
  }
  return ticks;
}

/**
 * Picks the y range: every value and the usual band, padded and widened to
 * round ticks so a flat series is not drawn as a jagged one.
 * @param {!Array<!Object>} points
 * @param {?Object} usual
 * @param {number=} minSpan The smallest range to show.
 * @return {{min: number, max: number, ticks: !Array<number>}}
 */
function yScale(points, usual, minSpan = 0) {
  const values = points.flatMap(
      (point) =>
          point.value2 == null ? [point.value] : [point.value, point.value2]);
  if (usual) {
    values.push(...['low', 'high', 'low2', 'high2']
                    .filter((key) => key in usual)
                    .map((key) => usual[key]));
  }
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (max - min < minSpan) {
    const middle = (max + min) / 2;
    min = middle - minSpan / 2;
    max = middle + minSpan / 2;
  }
  const pad = Math.max((max - min) * 0.15, Math.abs(max) * 0.02, 1);
  min -= pad;
  max += pad;
  const ticks = niceTicks(min, max, 5);
  const step = ticks.length > 1 ? ticks[1] - ticks[0] : 1;
  return {
    min: Math.min(min, ticks[0]),
    max: Math.max(max, ticks[ticks.length - 1]),
    ticks: ticks.filter((tick) => tick >= min - step && tick <= max + step),
  };
}

/**
 * Picks date ticks for a time range, as many as fit.
 * @param {number} start
 * @param {number} end
 * @param {number} maxCount
 * @return {!Array<{time: number, label: string}>}
 */
function timeTicks(start, end, maxCount) {
  const span = end - start;
  const steps = [
    {days: 1}, {days: 7}, {months: 1}, {months: 3}, {months: 6}, {years: 1},
    {years: 2}, {years: 5}, {years: 10}
  ];
  const approx = (step) => (step.days || 0) * DAY +
      (step.months || 0) * 30.4 * DAY + (step.years || 0) * 365.25 * DAY;
  const step =
      steps.find((candidate) => span / approx(candidate) <= maxCount) ||
      steps[steps.length - 1];
  const first = new Date(start);
  let cursor;
  if (step.years) {
    const year = Math.ceil(first.getFullYear() / step.years) * step.years;
    cursor = new Date(year, 0, 1);
    if (cursor.getTime() < start) {
      cursor = new Date(year + step.years, 0, 1);
    }
  } else if (step.months) {
    const month = Math.ceil(first.getMonth() / step.months) * step.months;
    cursor = new Date(first.getFullYear(), month, 1);
    if (cursor.getTime() < start) {
      cursor = new Date(first.getFullYear(), month + step.months, 1);
    }
  } else {
    cursor =
        new Date(first.getFullYear(), first.getMonth(), first.getDate() + 1);
  }
  const ticks = [];
  while (cursor.getTime() <= end) {
    const time = cursor.getTime();
    let label;
    if (step.years) {
      label = String(cursor.getFullYear());
    } else if (step.months) {
      label = cursor.getMonth() === 0 || !ticks.length ?
          `${MONTHS[cursor.getMonth()]} ${cursor.getFullYear()}` :
          MONTHS[cursor.getMonth()];
    } else {
      label = `${cursor.getDate()} ${MONTHS[cursor.getMonth()]}`;
    }
    ticks.push({time, label});
    if (step.years) {
      cursor = new Date(cursor.getFullYear() + step.years, 0, 1);
    } else if (step.months) {
      cursor =
          new Date(cursor.getFullYear(), cursor.getMonth() + step.months, 1);
    } else {
      cursor = new Date(
          cursor.getFullYear(), cursor.getMonth(),
          cursor.getDate() + step.days);
    }
  }
  return ticks;
}

/**
 * Finds the reading closest in time.
 * @param {!Array<number>} times Sorted ascending.
 * @param {number} time
 * @return {number} Index into times, or -1 when empty.
 */
function nearestIndex(times, time) {
  if (!times.length) {
    return -1;
  }
  let low = 0;
  let high = times.length - 1;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (times[middle] < time) {
      low = middle + 1;
    } else {
      high = middle;
    }
  }
  if (low > 0 && time - times[low - 1] <= times[low] - time) {
    return low - 1;
  }
  return low;
}

/**
 * Creates an SVG element with attributes.
 * @param {string} tag
 * @param {!Object<string, (string|number)>=} attributes
 * @return {!Element}
 */
function svgEl(tag, attributes = {}) {
  const element = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) {
    element.setAttribute(name, String(value));
  }
  return element;
}

/**
 * Draws a marker whose shape shows the reading's source.
 * @param {string} source
 * @param {number} x
 * @param {number} y
 * @param {number} radius
 * @return {!Element}
 */
function marker(source, x, y, radius) {
  if (source === 'record') {
    const side = radius * 2;
    return svgEl('rect', {
      x: x - radius,
      y: y - radius,
      width: side,
      height: side,
      rx: 1.5,
      class: 'trend-marker is-record',
    });
  }
  return svgEl('circle', {
    cx: x,
    cy: y,
    r: radius,
    class: `trend-marker is-${source}`,
  });
}

/** One measure's chart, with its tooltip and keyboard focus. */
class TrendChart {
  /**
   * @param {!Element} host The chart's container.
   * @param {!Object} measure As GET /readings lists it.
   * @param {{start: number, end: number}} range
   */
  constructor(host, measure, range) {
    this.host = host;
    this.measure = measure;
    this.range = range;
    // A compact preview may ask for a shorter plot; text keeps its size either way.
    this.plotHeight = range.plotHeight || PLOT_HEIGHT;
    this.points =
        measure.points.map((point) => ({...point, at: parseTime(point.time)}))
            .filter(
                (point) => point.at >= range.start && point.at <= range.end);
    this.times = this.points.map((point) => point.at);
    this.active = -1;
    this.tooltip = document.createElement('div');
    this.tooltip.className = 'trend-tooltip';
    this.tooltip.hidden = true;
    this.draw();
    this.resize = new ResizeObserver(() => {
      if (this.host.clientWidth !== this.width) {
        this.draw();
      }
    });
    this.resize.observe(host);
  }

  /** Stops following the container's size. */
  destroy() {
    this.resize.disconnect();
  }

  draw() {
    this.width = Math.max(this.host.clientWidth, 260);
    const width = this.width;
    const height = this.plotHeight + MARGIN.top + MARGIN.bottom;
    const plotWidth = width - MARGIN.left - MARGIN.right;
    const {min, max, ticks} = yScale(
        this.points, this.measure.usual, MIN_SPAN[this.measure.key] || 0);
    this.x = (time) => MARGIN.left +
        (time - this.range.start) / (this.range.end - this.range.start) *
            plotWidth;
    this.y = (value) =>
        MARGIN.top + (1 - (value - min) / (max - min)) * this.plotHeight;

    const svg = svgEl('svg', {
      viewBox: `0 0 ${width} ${height}`,
      width,
      height,
      class: 'trend-svg',
      role: 'img',
      'aria-hidden': 'true',
    });
    for (const tick of ticks) {
      const y = this.y(tick);
      svg.append(svgEl('line', {
        x1: MARGIN.left,
        x2: width - MARGIN.right,
        y1: y,
        y2: y,
        class: 'trend-grid',
      }));
      const label = svgEl('text', {
        x: MARGIN.left - 8,
        y: y + 4,
        class: 'trend-axis',
        'text-anchor': 'end',
      });
      label.textContent = String(tick);
      svg.append(label);
    }
    let dates = timeTicks(this.range.start, this.range.end,
        Math.max(2, Math.floor(plotWidth / 64)));
    if (dates.length < 2) {
      dates = [this.range.start, this.range.end].map(time => ({
        time, label: longDate(time).split(' ').slice(0, 2).join(' '),
      }));
    }
    for (const tick of dates) {
      const label = svgEl('text', {
        x: this.x(tick.time),
        y: height - 8,
        class: 'trend-axis',
        'text-anchor': tick.time === this.range.start ? 'start' : tick.time === this.range.end ? 'end' : 'middle',
      });
      label.textContent = tick.label;
      svg.append(label);
    }
    this.drawUsual(svg);
    if (this.measure.paired) {
      this.drawPairs(svg);
    } else {
      this.drawLine(svg);
    }
    this.cursor = svgEl('line', {
      y1: MARGIN.top,
      y2: MARGIN.top + this.plotHeight,
      class: 'trend-cursor',
      visibility: 'hidden',
    });
    this.focusMark = svgEl('circle', {r: 6, class: 'trend-focus'});
    this.focusMark.setAttribute('visibility', 'hidden');
    svg.append(this.cursor, this.focusMark);
    this.host.replaceChildren(svg, this.tooltip);
    this.svg = svg;
    this.wire();
    if (this.active >= 0) {
      this.show(this.active);
    }
  }

  /** @param {!Element} svg */
  drawUsual(svg) {
    const usual = this.measure.usual;
    if (!usual || !this.points.length) {
      return;
    }
    const bands = [[usual.low, usual.high]];
    if ('low2' in usual) {
      bands.push([usual.low2, usual.high2]);
    }
    // The band covers the days it was computed from, up to the latest reading.
    const left = this.x(Math.max(parseTime(usual.since), this.range.start));
    const right = this.x(this.points[this.points.length - 1].at);
    for (const [low, high] of bands) {
      const top = this.y(high);
      svg.append(svgEl('rect', {
        x: left,
        y: top,
        width: Math.max(right - left, 4),
        height: Math.max(this.y(low) - top, 2),
        rx: 4,
        class: 'trend-usual',
      }));
    }
  }

  /** @param {!Element} svg */
  drawLine(svg) {
    if (!this.points.length) {
      return;
    }
    const path =
        this.points
            .map(
                (point, index) =>
                    `${index ? 'L' : 'M'}${this.x(point.at).toFixed(1)},${
                        this.y(point.value).toFixed(1)}`)
            .join('');
    svg.append(svgEl('path', {d: path, class: 'trend-line'}));
    this.drawMarkers(svg, (point) => this.y(point.value));
  }

  /** @param {!Element} svg */
  drawPairs(svg) {
    const barWidth = Math.min(Math.max(this.medianGap() * 0.55, 2), 6);
    for (const point of this.points) {
      const top = this.y(point.value);
      svg.append(svgEl('rect', {
        x: this.x(point.at) - barWidth / 2,
        y: top,
        width: barWidth,
        height: Math.max(this.y(point.value2) - top, barWidth),
        rx: barWidth / 2,
        class: 'trend-bar',
      }));
    }
    this.drawMarkers(svg, (point) => this.y(point.value));
  }

  /**
   * @param {!Element} svg
   * @param {function(!Object): number} yOf
   */
  drawMarkers(svg, yOf) {
    const last = this.points.length - 1;
    const dense = this.medianGap() < MARKER_GAP;
    let drawnX = -Infinity;
    this.points.forEach((point, index) => {
      const x = this.x(point.at);
      const roomy = x - drawnX >= MARKER_GAP &&
          (index === last || this.x(this.points[last].at) - x >= MARKER_GAP);
      if (index !== last && (dense || !roomy)) {
        return;
      }
      drawnX = x;
      svg.append(marker(point.source, x, yOf(point), index === last ? 5 : 4));
    });
  }

  /**
   * The median pixel distance between neighbouring readings.
   * @return {number} Infinity with fewer than two readings.
   */
  medianGap() {
    const gaps = this.points.slice(1)
                     .map(
                         (point, index) =>
                             this.x(point.at) - this.x(this.points[index].at))
                     .sort((a, b) => a - b);
    return gaps.length ? gaps[gaps.length >> 1] : Infinity;
  }

  wire() {
    const pick = (event) => {
      const box = this.svg.getBoundingClientRect();
      const x = (event.clientX - box.left) * (this.width / box.width);
      const time = this.range.start +
          (x - MARGIN.left) / (this.width - MARGIN.left - MARGIN.right) *
              (this.range.end - this.range.start);
      this.show(nearestIndex(this.times, time));
    };
    this.svg.addEventListener('pointermove', pick);
    this.svg.addEventListener('pointerdown', pick);
    this.svg.addEventListener('pointerleave', () => {
      if (document.activeElement !== this.host) {
        this.hide();
      }
    });
    this.host.tabIndex = this.points.length ? 0 : -1;
    this.host.onkeydown = (event) => this.key(event);
    this.host.onfocus = () =>
        this.show(this.active >= 0 ? this.active : this.points.length - 1);
    this.host.onblur = () => this.hide();
  }

  /** @param {!KeyboardEvent} event */
  key(event) {
    const last = this.points.length - 1;
    const moves = {
      ArrowLeft: Math.max(this.active - 1, 0),
      ArrowRight: Math.min(this.active + 1, last),
      Home: 0,
      End: last,
    };
    if (event.key in moves) {
      event.preventDefault();
      this.show(moves[event.key]);
    } else if (event.key === 'Escape') {
      this.hide();
    } else if (event.key === 'Enter' && this.points[this.active]?.target) {
      this.host.dispatchEvent(new CustomEvent('trend-open', {
        bubbles: true,
        detail: this.points[this.active].target,
      }));
    }
  }

  /** @param {number} index */
  show(index) {
    const point = this.points[index];
    if (!point) {
      return;
    }
    this.active = index;
    const x = this.x(point.at);
    const y = this.y(point.value);
    this.cursor.setAttribute('x1', String(x));
    this.cursor.setAttribute('x2', String(x));
    this.cursor.setAttribute('visibility', 'visible');
    this.focusMark.setAttribute('cx', String(x));
    this.focusMark.setAttribute('cy', String(y));
    this.focusMark.setAttribute('visibility', 'visible');
    this.fillTooltip(point);
    this.tooltip.hidden = false;
    const scale = this.svg.getBoundingClientRect().width / this.width || 1;
    const tipWidth = this.tooltip.offsetWidth;
    const left = Math.min(
        Math.max(x * scale - tipWidth / 2, 0),
        this.host.clientWidth - tipWidth);
    this.tooltip.style.transform =
        `translate(${left}px, ${Math.max(y * scale - 76, 0)}px)`;
  }

  hide() {
    this.active = -1;
    this.tooltip.hidden = true;
    this.cursor?.setAttribute('visibility', 'hidden');
    this.focusMark?.setAttribute('visibility', 'hidden');
  }

  /** @param {!Object} point */
  fillTooltip(point) {
    const value = document.createElement('strong');
    value.textContent =
        `${formatValue(point, this.measure.decimals)} ${this.measure.unit}`;
    const when = document.createElement('span');
    when.textContent = longDate(point.at);
    const source = document.createElement('span');
    source.className = 'trend-tooltip-source';
    source.textContent =
        point.source === 'record' ? point.title : SOURCE_LABELS[point.source];
    this.tooltip.replaceChildren(value, when, source);
  }
}

/**
 * Draws a chart into host and returns it.
 * @param {!Element} host
 * @param {!Object} measure
 * @param {{start: number, end: number, plotHeight: (number|undefined)}} range
 * @return {!TrendChart}
 */
function render(host, measure, range) {
  return new TrendChart(host, measure, range);
}

const exported = {
  SOURCE_LABELS,
  formatValue,
  longDate,
  nearestIndex,
  niceTicks,
  parseTime,
  render,
  timeTicks,
  yScale,
  MIN_SPAN,
};
root.StellisTrendChart = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
