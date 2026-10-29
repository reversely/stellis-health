/**
 * @fileoverview Trends page: one card per vital sign with the latest value,
 * how it compares with the person's usual range, a chart over the chosen
 * time range, a form to log a reading and a table of every reading.
 */
(function(root) {
'use strict';

const DAY = 24 * 60 * 60 * 1000;
const RANGES = [
  {key: '1m', label: '1 month', days: 31},
  {key: '3m', label: '3 months', days: 92},
  {key: '1y', label: '1 year', days: 366},
  {key: 'all', label: 'All', days: null},
];
const STATUS_TEXT = {
  usual: 'Within usual range',
  above: 'Higher than usual',
  below: 'Lower than usual',
};
const STATUS_ICONS = {
  usual: 'check',
  above: 'chevron-up',
  below: 'chevron-down',
};
const SOURCE_NAMES = {
  record: 'Records',
  self: 'Manual entry',
  camera: 'Camera',
};
/** Below this many readings the card explains how the usual range forms. */
const USUAL_HINT_READINGS = 6;

/**
 * Works out the time span a range key covers.
 * @param {string} key A RANGES key.
 * @param {!Array<!Object>} measures
 * @param {number} now
 * @return {{start: number, end: number}}
 */
function rangeSpan(key, measures, now) {
  const range = RANGES.find((item) => item.key === key) || RANGES[3];
  if (range.days) {
    return {start: now - range.days * DAY, end: now};
  }
  const times = measures.flatMap(
      (measure) => measure.points.map(
          (point) => root.StellisTrendChart.parseTime(point.time)));
  const first = times.length ? Math.min(...times) : now - 366 * DAY;
  const pad = Math.max((now - first) * 0.03, DAY);
  return {start: first - pad, end: now};
}

/**
 * Sums up the readings in view in one line. Weight and HbA1c show their
 * change, other measures their average, and the All range the highest point.
 * @param {!Array<!Object>} points In view, oldest first.
 * @param {!Object} measure
 * @param {string} rangeKey
 * @return {string} Empty with fewer than two readings.
 */
function summaryText(points, measure, rangeKey) {
  if (points.length < 2) {
    return '';
  }
  const chart = root.StellisTrendChart;
  const unit = measure.unit;
  if (rangeKey === 'all') {
    const peak =
        points.reduce((best, point) => point.value > best.value ? point : best);
    const when = chart.longDate(chart.parseTime(peak.time)).split(' ');
    return `Highest ${chart.formatValue(peak, measure.decimals)} ${unit} in ` +
        `${when[1]} ${when[2]}`;
  }
  if (measure.key === 'weight' || measure.key === 'hba1c') {
    const change = points[points.length - 1].value - points[0].value;
    const amount = Math.abs(change).toFixed(measure.decimals);
    if (Number(amount) === 0) {
      return 'No change in this period';
    }
    return `${change > 0 ? 'Up' : 'Down'} ${amount} ${unit} in this period`;
  }
  const mean = (key) =>
      points.reduce((sum, point) => sum + point[key], 0) / points.length;
  const average = {
    value: mean('value'),
    value2: measure.paired ? mean('value2') : null,
  };
  return `Average ${chart.formatValue(average, measure.decimals)} ${unit}`;
}

/**
 * Formats a Date as the value of a datetime-local input.
 * @param {!Date} date
 * @return {string}
 */
function localInputValue(date) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

/**
 * Charts a counted measure, such as steps, as one total per local day; its
 * table keeps each entry in ``entries`` so entries can be deleted.
 * @param {!Object} measure
 * @return {!Object}
 */
function dailyTotals(measure) {
  if (measure.daily !== 'sum') return measure;
  const days = new Map();
  for (const point of measure.points) {
    const at =
        new Date(point.time.length <= 10 ? `${point.time}T12:00` : point.time);
    const key = at.toDateString();
    const total = days.get(key);
    if (total) {
      total.value += point.value;
    } else {
      at.setHours(12, 0, 0, 0);
      days.set(key, {...point, id: null, time: at.toISOString()});
    }
  }
  return {...measure, entries: measure.points, points: [...days.values()]};
}

/** The Trends page. */
class TrendsPage {
  /** @param {!Element} host */
  constructor(host) {
    const dom = root.StellisDom;
    this.dom = dom;
    this.rangeKey = 'all';
    this.measures = [];
    this.charts = [];
    this.tableMeasures = new Set();
    const head = dom.el('div', null, 'trends-head');
    head.append(dom.el('h2', 'Trends'));
    this.ranges = dom.el('div', null, 'trends-ranges');
    this.ranges.setAttribute('role', 'group');
    this.ranges.setAttribute('aria-label', 'Time range');
    for (const range of RANGES) {
      const button = dom.button(range.label);
      button.dataset.range = range.key;
      button.addEventListener('click', () => {
        this.rangeKey = range.key;
        this.paint();
      });
      this.ranges.append(button);
    }
    this.status = dom.el('p', null, 'trends-status');
    this.status.setAttribute('role', 'status');
    this.cards = dom.el('div', null, 'trends-cards');
    this.cards.addEventListener('trend-open', (event) => {
      root.location.assign(event.detail);
    });
    host.replaceChildren(head, this.ranges, this.status, this.cards);
  }

  async load() {
    try {
      this.measures =
          (await this.dom.api('/readings')).measures.map(dailyTotals);
      this.paint();
      if (!this.linkOpened) {
        this.linkOpened = true;
        const query = new URLSearchParams(root.location.search);
        const key = query.get('measure') || query.get('log');
        if (this.measures.some(measure => measure.key === key)) {
          const target = this.cards.querySelector('[data-measure="'+key+'"]');
          if (query.has('log')) target?.querySelector('[data-log]')?.click();
          target?.scrollIntoView({block:'start'});
        }
      }
    } catch (error) {
      this.status.textContent = error.message;
    }
  }

  paint() {
    for (const control of this.ranges.querySelectorAll('button')) {
      control.setAttribute(
          'aria-pressed', String(control.dataset.range === this.rangeKey));
    }
    for (const chart of this.charts) {
      chart.destroy();
    }
    this.charts = [];
    const span = rangeSpan(this.rangeKey, this.measures, Date.now());
    const camera = root.StellisCameraPulse;
    // Keep each measurement in a stable section, whether it has readings or not.
    const groups = [
      {key: 'vitals', label: 'Vital signs', keys: ['pulse', 'breathing_rate', 'hrv', 'blood_pressure', 'temperature']},
      {key: 'body', label: 'Body measurements', keys: ['weight']},
      {key: 'glucose', label: 'Blood sugar', keys: ['blood_glucose', 'hba1c']},
      {key: 'sleep', label: 'Sleep', keys: ['sleep']},
      {key: 'symptoms', label: 'Symptoms', keys: ['pain']},
    ];
    this.cards.replaceChildren();
    for (const definition of groups) {
      const measures = definition.keys.map(key => this.measures.find(m => m.key === key)).filter(Boolean);
      if (!measures.length) continue;
      const group = this.dom.el('section', null, definition.key === 'vitals' ? 'trend-group trend-vitals' : 'trend-group');
      group.dataset.group = definition.key;
      const head = this.dom.el('div', null, 'vital-signs-head');
      const title = this.dom.el('h3', definition.label);
      title.id = `trends-${definition.key}`;
      group.setAttribute('aria-labelledby', title.id);
      head.append(title);
      if (definition.key === 'vitals' && camera) {
        head.append(camera.logButton(() => this.load(), this.measures, 'trend-add trend-camera'));
      }
      const body = this.dom.el('div', null, 'trend-group-body');
      group.append(head, body);
      for (const measure of measures.filter(m => m.points.length)) {
        body.append(this.card(measure, span, 'h4'));
      }
      const empty = measures.filter(m => !m.points.length);
      if (empty.length) body.append(this.startCard(empty));
      this.cards.append(group);
    }
  }

  /**
   * Renders one tracked measure.
   * @param {!Object} measure
   * @param {{start: number, end: number}} span
   * @param {string=} heading Heading level within its group.
   * @return {!Element}
   */
  card(measure, span, heading = 'h3') {
    const dom = this.dom;
    const chartApi = root.StellisTrendChart;
    const card = dom.el('section', null, 'trend-card');
    card.dataset.measure = measure.key;
    const titleId = `trend-${measure.key}`;
    card.setAttribute('aria-labelledby', titleId);
    const latest = measure.points[measure.points.length - 1];
    const latestAt = chartApi.parseTime(latest.time);

    const title = dom.el(heading, measure.label);
    title.id = titleId;
    const figure = dom.el('p', null, 'trend-figure');
    figure.append(
        dom.el(
            'span', chartApi.formatValue(latest, measure.decimals),
            'trend-value'),
        dom.el('span', measure.unit, 'trend-unit'));
    const origin = latest.source === 'record' ?
        latest.title :
        chartApi.SOURCE_LABELS[latest.source];
    const head = dom.el('div', null, 'trend-card-head');
    const whenLine = dom.el('div', null, 'trend-when');
    const date = dom.el('time', chartApi.longDate(latestAt));
    date.dateTime = latest.time;
    whenLine.append(date);
    if (origin) whenLine.append(dom.el('span', origin, 'trend-source'));
    if (latest.source === 'camera') {
      whenLine.append(' ', dom.accuracyNote('camera'));
    }
    const top = dom.el('div', null, 'trend-card-top');
    top.append(title);
    head.append(top, figure, whenLine);
    if (measure.status in STATUS_TEXT) {
      const status = dom.el('p', null, `trend-status is-${measure.status}`);
      status.append(
          dom.icon(STATUS_ICONS[measure.status]),
          dom.el('span', STATUS_TEXT[measure.status]));
      head.append(status);
    } else if (measure.points.length < USUAL_HINT_READINGS) {
      head.append(dom.el(
          'p', 'Usual range appears after a few more readings',
          'trend-status'));
    }
    card.append(head);

    const inView = measure.points.filter((point) => {
      const at = chartApi.parseTime(point.time);
      return at >= span.start && at <= span.end;
    });
    const plot = dom.el('div', null, 'trend-plot');
    plot.setAttribute('role', 'group');
    if (inView.length) {
      plot.setAttribute(
          'aria-label',
          `${measure.label} chart, ${dom.plural(inView.length, 'reading')}. ` +
              'Use the arrow keys to move between readings.');
      card.append(plot);
      this.charts.push(chartApi.render(plot, measure, span));
      const change = summaryText(inView, measure, this.rangeKey);
      const sources = [...new Set(inView.map((point) => point.source))];
      const foot = dom.el('div', null, 'trend-foot');
      if (change) {
        foot.append(dom.el('span', change, 'trend-change'));
      }
      if (sources.length > 1 || measure.usual) {
        foot.append(this.legend(sources, measure));
      }
      card.append(foot);
    } else {
      card.append(dom.el('p', 'No readings in this period', 'trend-empty'));
    }
    this.actions(measure, card);
    return card;
  }

  /**
   * Names the marker shapes and the usual band.
   * @param {!Array<string>} sources
   * @param {!Object} measure
   * @return {!Element}
   */
  legend(sources, measure) {
    const dom = this.dom;
    const usual = measure.usual;
    const list = dom.el('ul', null, 'trend-legend');
    list.setAttribute('aria-label', 'Legend');
    for (const source of ['record', 'self', 'camera']) {
      if (sources.includes(source)) {
        const item = dom.el('li');
        item.append(
            dom.el('span', null, `trend-key is-${source}`),
            dom.el('span', SOURCE_NAMES[source]));
        list.append(item);
      }
    }
    if (usual) {
      const item = dom.el('li');
      item.append(
          dom.el('span', null, 'trend-key is-usual'),
          dom.el('span', `Usual range ${root.StellisTrendChart.formatValue({value: usual.low, value2: usual.low2 ?? null}, measure.decimals)}–${root.StellisTrendChart.formatValue({value: usual.high, value2: usual.high2 ?? null}, measure.decimals)} ${measure.unit}`));
      list.append(item);
    }
    return list;
  }

  /**
   * Adds the log form and the table toggle under a card.
   * @param {!Object} measure
   * @param {!Element} card
   */
  actions(measure, card) {
    const dom = this.dom;
    const row = dom.el('div', null, 'trend-actions');
    const cameraMeasure = root.StellisCameraPulse?.MEASURE_KEYS.includes(measure.key);
    const add = dom.button(
        cameraMeasure ? 'Log manually' : 'Add a reading',
        cameraMeasure ? 'trend-manual' : 'trend-add');
    add.dataset.log = measure.key;
    const toggle = dom.button(null, 'trend-table-control');
    toggle.setAttribute('aria-label', 'Table view');
    toggle.title = 'Table view';
    const glyph = dom.el('span', null, 'table-view-glyph');
    glyph.setAttribute('aria-hidden', 'true');
    toggle.append(glyph);
    const table = this.table(measure);
    table.id = `trend-table-${measure.key}`;
    toggle.setAttribute('aria-controls', table.id);
    const update = () => {
      const selected = this.tableMeasures.has(measure.key);
      toggle.setAttribute('aria-pressed', String(selected));
      table.hidden = !selected;
      for (const node of card.querySelectorAll('.trend-plot, .trend-foot, .trend-empty')) node.hidden = selected;
    };
    toggle.addEventListener('click', () => {
      if (this.tableMeasures.has(measure.key)) this.tableMeasures.delete(measure.key);
      else this.tableMeasures.add(measure.key);
      update();
    });
    card.querySelector('.trend-card-top').append(toggle);
    add.addEventListener('click', () => {
      add.hidden = true;
      const form = this.form(measure, () => {
        form.remove();
        add.hidden = false;
        add.focus();
      });
      row.after(form);
      form.querySelector('input').focus({preventScroll: true});
      form.scrollIntoView({block: 'nearest', behavior: 'instant'});
    });
    if (!measure.points.length) {
      toggle.hidden = true;
    }
    row.append(add);
    card.append(row, table);
    update();
  }

  /**
   * Builds the form for logging one reading.
   * @param {!Object} measure
   * @param {function()} close
   * @return {!Element}
   */
  form(measure, close) {
    const dom = this.dom;
    const form =
        /** @type {!HTMLFormElement} */ (dom.el('form', null, 'trend-form'));
    const field = (label, name, attributes) => {
      const wrap = dom.el('label', null, 'trend-field');
      const input = /** @type {!HTMLInputElement} */ (dom.el('input'));
      input.name = name;
      Object.assign(input, attributes);
      wrap.append(dom.el('span', label), input);
      return wrap;
    };
    const number = {
      type: 'number',
      inputMode: 'decimal',
      required: true,
      step: measure.decimals ? '0.1' : '1'
    };
    if (measure.paired) {
      form.append(
          field('Top number', 'value', number),
          field('Bottom number', 'value2', number));
    } else {
      form.append(field(`${measure.label} (${measure.unit})`, 'value', number));
    }
    form.append(
        field('When', 'taken_at', {
          type: 'datetime-local',
          required: true,
          value: localInputValue(new Date())
        }),
        field('Note (optional)', 'note', {type: 'text', maxLength: 200}));
    const error = dom.el('p', null, 'trend-form-error');
    error.setAttribute('role', 'alert');
    const save = dom.button('Save', 'trend-save');
    save.type = 'submit';
    const cancel = dom.button('Cancel', 'trend-cancel');
    cancel.addEventListener('click', close);
    const buttons = dom.el('div', null, 'trend-form-buttons');
    buttons.append(save, cancel);
    form.append(error, buttons);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      save.disabled = true;
      try {
        await dom.api('/readings', {
          method: 'POST',
          json: {
            measure: measure.key,
            value: Number(data.get('value')),
            value2: measure.paired ? Number(data.get('value2')) : null,
            taken_at: new Date(String(data.get('taken_at'))).toISOString(),
            note: String(data.get('note') || ''),
            notebook_id: this.notebookId || null,
            capture_id: this.captureId || null,
          },
        });
        this.status.textContent = `${measure.label} saved`;
        await this.load();
      } catch (failure) {
        error.textContent = failure.message;
        save.disabled = false;
      }
    });
    return form;
  }

  /**
   * Lists every reading of a measure, newest first.
   * @param {!Object} measure
   * @return {!Element}
   */
  table(measure) {
    const dom = this.dom;
    const chartApi = root.StellisTrendChart;
    const wrap = dom.el('div', null, 'trend-table-wrap');
    const table = dom.el('table', null, 'trend-table');
    const caption = dom.el('caption', `${measure.label} readings`);
    const head = dom.el('tr');
    for (const name of ['Date', measure.unit, 'Source', '']) {
      const cell = dom.el('th', name);
      cell.scope = 'col';
      head.append(cell);
    }
    const thead = dom.el('thead');
    thead.append(head);
    const body = dom.el('tbody');
    for (const point of [...(measure.entries || measure.points)].reverse()) {
      const row = dom.el('tr');
      const source = dom.el('td');
      if (point.target) {
        const link =
            /** @type {!HTMLAnchorElement} */ (dom.el('a', point.title));
        link.href = point.target;
        source.append(link);
      } else {
        source.textContent = chartApi.SOURCE_LABELS[point.source];
      }
      const action = dom.el('td');
      if (point.source !== 'record') {
        const remove = dom.button(null, 'trend-delete');
        remove.append(dom.icon('trash'));
        remove.title = 'Delete';
        remove.setAttribute(
            'aria-label',
            `Delete ${chartApi.formatValue(point, measure.decimals)} from ${
                chartApi.longDate(chartApi.parseTime(point.time))}`);
        remove.addEventListener('click', async () => {
          remove.disabled = true;
          try {
            await dom.api(
                `/readings/${encodeURIComponent(point.id)}`,
                {method: 'DELETE'});
            await this.load();
          } catch (error) {
            this.status.textContent = error.message;
            remove.disabled = false;
          }
        });
        action.append(remove);
      }
      row.append(
          dom.el('td', chartApi.longDate(chartApi.parseTime(point.time))),
          dom.el(
              'td', chartApi.formatValue(point, measure.decimals),
              'trend-number'),
          source, action);
      body.append(row);
    }
    table.append(caption, thead, body);
    wrap.append(table);
    return wrap;
  }

  /**
   * Offers logging for measures with no readings yet.
   * @param {!Array<!Object>} measures
   * @return {!Element}
   */
  startCard(measures) {
    const dom = this.dom;
    const card = dom.el('div', null, 'trend-untracked');
    for (const measure of measures) {
      const row = dom.el('div', null, 'trend-start-row');
      row.dataset.measure = measure.key;
      row.append(dom.el('span', measure.label, 'trend-start-name'));
      const cameraMeasure = root.StellisCameraPulse?.MEASURE_KEYS.includes(measure.key);
      const add = dom.button(
          cameraMeasure ? 'Log manually' : 'Add a reading',
          'trend-manual');
      add.dataset.log = measure.key;
      add.setAttribute(
          'aria-label', cameraMeasure ?
              `Log ${measure.label.toLowerCase()} manually` :
              `Add a ${measure.label.toLowerCase()} reading`);
      add.addEventListener('click', () => {
        add.hidden = true;
        const form = this.form(measure, () => {
          form.remove();
          add.hidden = false;
          add.focus();
        });
        row.after(form);
        form.querySelector('input').focus({preventScroll: true});
        form.scrollIntoView({block: 'nearest', behavior: 'instant'});
      });
      const buttons = dom.el('div', null, 'trend-start-buttons');
      buttons.append(add);
      row.append(buttons);
      card.append(row);
    }
    return card;
  }
}

/**
 * Shows the Trends page in host.
 * @param {!Element} host
 * @return {!Promise<void>}
 */
function mount(host) {
  return root.StellisDashTrends.mount(host);
}

const exported = {
  async entryForm(host, notebookId, saved, initialMeasure) {
    const measures = (await root.StellisDom.api('/readings')).measures.filter(m=>!m.record_only);
    const select = document.createElement('select'); select.setAttribute('aria-label','Measurement');
    for (const m of measures) select.add(new Option(m.label,m.key));
    if(initialMeasure)select.value=initialMeasure;
    const body = document.createElement('div'); host.append(select,body);
    const controller = Object.create(TrendsPage.prototype);
    Object.assign(controller,{dom:root.StellisDom, notebookId, status:document.createElement('p'), load:saved});
    const paint = () => {
      controller.captureId = root.crypto.randomUUID();
      const form = controller.form(measures.find(m=>m.key===select.value),()=>host.closest('dialog').close());
      form.querySelector('.trend-save').classList.add('coach-button','is-primary');
      form.querySelector('button:not(.trend-save)').classList.add('coach-button','is-quiet');
      body.replaceChildren(form);
    };
    select.addEventListener('change',paint); paint();
  },
  RANGES,
  dailyTotals,
  summaryText,
  localInputValue,
  mount,
  rangeSpan,
};
root.StellisTrends = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
