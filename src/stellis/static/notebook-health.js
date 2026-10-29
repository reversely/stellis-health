/**
 * @fileoverview A notebook's Brief, from GET /notebooks/{id}/brief. Above the
 * tabs: a two-week timeline of doses, one band per medicine course and the
 * pending items coming up. In the Brief tab: Levels, with a card per measure
 * and a chart of the selected one, plus facts that are not levels.
 */
(function(root) {
'use strict';
const dom = () => root.StellisDom;
const el = (tag, text, cls) => dom().el(tag, text, cls);
const SVG = 'http://www.w3.org/2000/svg';
const DAY = 86400000;
const parse = date => new Date(date + 'T12:00:00');
const short = date => parse(date).toLocaleDateString([], {month: 'short', day: 'numeric'});
const long = date => parse(date).toLocaleDateString([], {month: 'short', day: 'numeric', year: 'numeric'});
const amount = value => Number(value).toLocaleString([], {maximumFractionDigits: 2});
const lowerFirst = text => text ? text[0].toLowerCase() + text.slice(1) : '';
const STATUS_CLASS = {calculated: 'is-calculated', planned: 'is-planned', scheduled: '', due: 'is-alert'};
let serial = 0;

function svg(tag, attributes = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}

function sectionHead(title, aside) {
  const head = el('div', null, 'brief-head');
  const heading = el('h2', title);
  heading.id = `brief-${++serial}`;
  head.append(heading);
  if (aside) head.append(aside);
  return {head, id: heading.id};
}

/** Two weeks of capsules: logged doses solid, expected ones pale, today outlined. */
function timeline(data) {
  const section = el('section', null, 'brief-timeline');
  const {head, id} = sectionHead('Timeline', el('time', 'Today, ' + parse(data.today).toLocaleDateString([], {weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'}), 'brief-today'));
  head.querySelector('time').dateTime = data.today;
  section.setAttribute('aria-labelledby', id);
  const days = el('ol', null, 'brief-strip');
  for (const day of data.strip) {
    const date = parse(day.date);
    const item = el('li', null, 'brief-day');
    if (day.today) item.setAttribute('aria-current', 'date');
    if (day.date > data.today) item.classList.add('is-future');
    const words = {taken: 'taken', skipped: 'skipped', missed: 'not logged', expected: 'dose due'};
    const said = day.marks.map(mark => mark.event || words[mark.state]);
    item.setAttribute('aria-label', `${date.toLocaleDateString([], {weekday: 'long', month: 'long', day: 'numeric'})}${said.length ? ': ' + said.join(', ') : ''}`);
    const capsule = el('span', null, 'brief-capsule');
    for (const mark of day.marks) {
      const dot = el('span', null, `brief-dot is-${mark.state}${mark.status ? ' is-' + mark.status : ''}`);
      if (mark.event) dot.title = mark.event;
      capsule.append(dot);
    }
    item.append(el('span', date.toLocaleDateString([], {weekday: 'narrow'}), 'brief-weekday'), capsule,
        el('span', String(date.getDate()), 'brief-date'));
    days.append(item);
  }
  section.append(head, days);
  const pregnancy = data.pregnancy?.summary.pregnancy;
  if (pregnancy) section.append(el('p', `${pregnancy.weeks} weeks, ${pregnancy.days} days`, 'brief-gestation'));
  return section;
}

/** Weeks and days, the due date, and a track from the start to the due or booked date. */
function pregnancyBand(record, today) {
  const p = record.summary.pregnancy;
  const band = el('article', null, 'brief-course brief-pregnancy');
  const weeks = el('div', null, 'brief-course-count brief-pregnancy-weeks');
  const figure = el('p');
  figure.append(el('strong', String(p.weeks)), ' weeks, ', el('strong', String(p.days)), ` day${p.days === 1 ? '' : 's'}`);
  weeks.append(figure, el('span', p.trimester));
  const due = el('div', null, 'brief-course-count');
  const dueFigure = el('p'); dueFigure.append(el('strong', short(p.due)));
  due.append(dueFigure, el('span', 'Due date'));
  const end = p.booked_birth && p.booked_birth < p.due ? p.booked_birth : p.due;
  const span = Math.max(1, (parse(end) - parse(p.start)) / DAY);
  const at = date => `${Math.min(100, Math.max(0, (parse(date) - parse(p.start)) / DAY / span * 100))}%`;
  const track = el('div', null, 'brief-course-track');
  const bar = el('div', null, 'brief-course-bar');
  bar.setAttribute('aria-hidden', 'true');
  const done = el('span', null, 'brief-course-done'); done.style.width = at(today);
  const goal = el('span', null, 'brief-course-marker'); goal.style.left = at(end);
  const now = el('span', null, 'brief-course-now'); now.style.left = at(today);
  bar.append(done, goal, now);
  const labels = el('div', null, 'brief-course-labels');
  labels.append(el('span', `Start: ${long(p.start)}`), el('span', p.booked_birth ? `Booked: ${short(p.booked_birth)}` : `Due: ${short(p.due)}`));
  track.append(bar, labels);
  band.append(weeks, due, track);
  return band;
}

/** Lets the person add a booked induction or caesarean date, which ends the timeline there. */
function birthCard(record, onSaved) {
  const card = el('section', null, 'brief-birth');
  const heading = el('h3', 'Birth date'); heading.id = `brief-birth-${++serial}`;
  card.setAttribute('aria-labelledby', heading.id);
  const field = el('label', null, 'brief-birth-field');
  const input = el('input'); input.type = 'date'; input.value = record.booked_birth || '';
  input.min = record.summary.pregnancy.start;
  field.append(el('span', 'Booked date'), input);
  const status = el('p', null, 'brief-birth-status'); status.setAttribute('role', 'status');
  input.addEventListener('change', async () => {
    const data = {};
    for (const field of ['dating_basis', 'basis_date', 'embryo_day', 'scan_weeks', 'scan_days', 'note']) {
      if (record[field] != null) data[field] = record[field];
    }
    data.booked_birth = input.value;
    status.textContent = 'Saving';
    try {
      await dom().api(`/trackers/records/${encodeURIComponent(record.id)}`, {method: 'PUT', json: data});
      status.textContent = input.value ? 'Saved' : 'Removed';
      onSaved();
    } catch (error) { status.textContent = error.message; }
  });
  card.append(heading, el('p', 'If the clinic books an induction or a caesarean, add the date. The timeline then ends there, and the due date stays for reference.'), field, status);
  const wrap = el('div', null, 'brief-birth-wrap');
  wrap.append(card, el('p', 'Shows only what is still ahead. Past visits and results stay in Records.', 'brief-birth-note'));
  return wrap;
}

/** One course: name and schedule, doses logged and weeks, and a start-to-end track. */
function course(record, today) {
  const summary = record.summary;
  const band = el('article', null, 'brief-course');
  const who = el('div', null, 'brief-course-name');
  who.append(el('strong', `${record.name} ${amount(record.dose_amount)} ${record.dose_unit}`),
      el('span', `${summary.schedule_text} since ${parse(record.start).toLocaleDateString([], {month: 'long', day: 'numeric'})}`));
  const elapsed = Math.max(0, Math.round((parse(record.end && record.end < today ? record.end : today) - parse(record.start)) / DAY) + 1);
  const count = el('div', null, 'brief-course-count');
  const figure = el('p');
  figure.append(el('strong', String(summary.taken_count)), ` dose${summary.taken_count === 1 ? '' : 's'} logged`);
  count.append(figure, el('span', elapsed < 14 ? `${elapsed} day${elapsed === 1 ? '' : 's'}` : `${Math.floor(elapsed / 7)} weeks`));
  const dates = summary.derived.map(item => item.date);
  const last = [record.end, today, ...dates].filter(Boolean).sort().at(-1);
  const span = Math.max(1, (parse(last) - parse(record.start)) / DAY);
  const at = date => `${Math.min(100, Math.max(0, (parse(date) - parse(record.start)) / DAY / span * 100))}%`;
  const track = el('div', null, 'brief-course-track');
  const bar = el('div', null, 'brief-course-bar');
  bar.setAttribute('aria-hidden', 'true');
  const done = el('span', null, 'brief-course-done'); done.style.width = at(today);
  bar.append(done);
  for (const item of summary.derived) {
    const marker = el('span', null, `brief-course-marker is-${item.status}`);
    marker.style.left = at(item.date); marker.title = `${item.title}, ${long(item.date)}`;
    bar.append(marker);
  }
  const now = el('span', null, 'brief-course-now'); now.style.left = at(today);
  bar.append(now);
  const ends = record.end ? `Ends ${short(record.end)}` : 'Ongoing';
  const labels = el('div', null, 'brief-course-labels');
  labels.append(el('span', `Started ${short(record.start)}`), el('span', ends));
  track.append(bar, labels);
  band.append(who, count, track);
  return band;
}

/** Pending items only, with their status as a colour block and their source. */
function comingUp(items) {
  const section = el('section', null, 'brief-coming');
  const {head, id} = sectionHead('Coming up');
  section.setAttribute('aria-labelledby', id);
  const list = el('ul', null, 'brief-coming-list');
  for (const item of items) {
    const row = el('li', null, 'brief-coming-row');
    const when = item.precision === 'month' ?
        parse(item.date).toLocaleDateString([], {month: 'long'}) :
        parse(item.date).toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric'});
    const text = el('div', null, 'brief-coming-text');
    text.append(el('strong', item.title));
    if (item.detail) text.append(el('p', item.detail));
    if (item.source) {
      const link = el('a', null, 'brief-coming-source');
      link.append(dom().icon(item.source.target.startsWith('/?event=') ? 'calendar' : 'file-text'),
          el('span', item.source.title));
      link.href = item.source.target;
      text.append(link);
    }
    const label = item.status[0].toUpperCase() + item.status.slice(1);
    row.append(el('time', when, 'brief-coming-date'), el('span', null, `brief-coming-dot is-${item.status}`), text,
        el('span', label, `status-block ${STATUS_CLASS[item.status] ?? ''}`.trim()));
    row.querySelector('time').dateTime = item.date;
    row.querySelector('.brief-coming-dot').setAttribute('aria-hidden', 'true');
    list.append(row);
  }
  section.append(head, list);
  return section;
}

function spark(points) {
  const values = points.map(point => point.value);
  const low = Math.min(...values), high = Math.max(...values);
  const graphic = svg('svg', {viewBox: '0 0 100 30', preserveAspectRatio: 'none', class: 'brief-spark', 'aria-hidden': 'true'});
  if (points.length > 1) {
    const path = points.map((point, i) => `${i ? 'L' : 'M'}${(i / (points.length - 1) * 100).toFixed(2)} ${(28 - (point.value - low) / ((high - low) || 1) * 26).toFixed(2)}`).join(' ');
    graphic.append(svg('path', {d: path, 'vector-effect': 'non-scaling-stroke'}));
  }
  return graphic;
}

function niceTicks(min, max, count = 4) {
  const raw = (max - min) / count || 1;
  const step = [1, 2, 2.5, 5, 10].map(f => f * 10 ** Math.floor(Math.log10(raw))).find(s => s >= raw);
  const ticks = [];
  // The last tick is at or above the highest value, so no point sits above the plot.
  const last = Math.ceil(max / step - 1e-9) * step;
  for (let value = Math.floor(min / step) * step; value <= last + step * 1e-9; value += step) ticks.push(+value.toFixed(10));
  return ticks;
}

/** The selected measure over time, with its reference band and treatment periods. */
function chart(level, points, treatments, onSelect, selected) {
  const box = el('div', null, 'brief-chart');
  const width = Math.max(320, box.clientWidth || 720);
  const draw = (pixels) => {
    box.replaceChildren();
    const W = pixels, H = 280, left = 40, right = 16, top = 12, bottom = 64;
    const times = points.map(p => parse(p.date).getTime());
    const start = Math.min(...times) - DAY * 15, end = Math.max(...times) + DAY * 15;
    const ref = level.reference;
    const values = points.flatMap(p => [p.value, p.value2]).filter(v => v != null);
    const yMax = Math.max(...values, ref.low ?? -Infinity) * 1.1;
    const ticks = niceTicks(Math.min(0, ...values), yMax);
    const yTop = ticks.at(-1), yBottom = ticks[0];
    const x = t => left + (t - start) / (end - start) * (W - left - right);
    const y = v => top + (yTop - v) / (yTop - yBottom) * (H - top - bottom);
    const graphic = svg('svg', {viewBox: `0 0 ${W} ${H}`, class: 'brief-chart-svg', role: 'img',
      'aria-label': `${level.name} over time, ${points.length} results`});
    if (ref.low != null && ref.low > yBottom) {
      graphic.append(svg('rect', {x: left, y: y(Math.min(ref.low, yTop)), width: W - left - right, height: y(yBottom) - y(Math.min(ref.low, yTop)), class: 'brief-chart-out'}));
    }
    if (ref.high != null && ref.high < yTop) {
      graphic.append(svg('rect', {x: left, y: y(yTop), width: W - left - right, height: y(ref.high) - y(yTop), class: 'brief-chart-out'}));
    }
    for (const tick of ticks) {
      graphic.append(svg('line', {x1: left, x2: W - right, y1: y(tick), y2: y(tick), class: 'brief-chart-grid'}));
      const label = svg('text', {x: left - 8, y: y(tick) + 4, class: 'brief-chart-label', 'text-anchor': 'end'});
      label.textContent = amount(tick); graphic.append(label);
    }
    for (const treatment of treatments) {
      const from = Math.max(start, parse(treatment.start).getTime());
      const to = Math.min(end, parse(treatment.end || new Date().toISOString().slice(0, 10)).getTime());
      if (to <= from) continue;
      const clip = `brief-clip-${++serial}`;
      const defs = svg('clipPath', {id: clip});
      defs.append(svg('rect', {x: x(from), y: H - bottom + 10, width: Math.max(2, x(to) - x(from)), height: 20}));
      graphic.append(defs, svg('rect', {x: x(from), y: H - bottom + 10, width: Math.max(2, x(to) - x(from)), height: 20, rx: 4, class: 'brief-chart-treatment'}));
      const name = svg('text', {x: x(from) + 6, y: H - bottom + 24, class: 'brief-chart-treatment-label', 'clip-path': `url(#${clip})`});
      name.textContent = treatment.name; graphic.append(name);
    }
    const spanYears = (end - start) / (365 * DAY);
    for (let year = new Date(start).getFullYear(); year <= new Date(end).getFullYear(); year++) {
      for (let month = 0; month < 12; month += spanYears > 1.5 ? 12 : 3) {
        const t = new Date(year, month, 1).getTime();
        if (t < start || t > end) continue;
        const label = svg('text', {x: x(t), y: H - 12, class: 'brief-chart-label', 'text-anchor': 'middle'});
        label.textContent = spanYears > 1.5 ? String(year) : new Date(t).toLocaleDateString([], {month: 'short', year: '2-digit'});
        graphic.append(label);
      }
    }
    // Lines join results within a year of each other; longer gaps stay open.
    let path = '';
    points.forEach((point, i) => {
      const gap = i && times[i] - times[i - 1] > 365 * DAY;
      path += `${!i || gap ? 'M' : 'L'}${x(times[i]).toFixed(1)} ${y(point.value).toFixed(1)} `;
    });
    graphic.append(svg('path', {d: path, class: 'brief-chart-line'}));
    points.forEach((point, i) => {
      const dot = svg('circle', {cx: x(times[i]), cy: y(point.value), r: i === selected ? 7 : 4.5,
        class: `brief-chart-point${point.own ? '' : ' is-other'}${i === selected ? ' is-selected' : ''}`});
      dot.addEventListener('click', () => onSelect(i));
      graphic.append(dot);
    });
    const chosen = points[selected];
    if (chosen) {
      const text = `${amount(chosen.value)}${chosen.value2 != null ? '/' + amount(chosen.value2) : ''} ${level.unit}, ${long(chosen.date)}`;
      const tipWidth = text.length * 7.2 + 20;
      const tx = Math.min(W - right - tipWidth, Math.max(left, x(times[selected]) - tipWidth / 2));
      const ty = Math.max(top, y(chosen.value) - 44);
      graphic.append(svg('rect', {x: tx, y: ty, width: tipWidth, height: 28, rx: 6, class: 'brief-chart-tip'}));
      const label = svg('text', {x: tx + tipWidth / 2, y: ty + 19, class: 'brief-chart-tip-text', 'text-anchor': 'middle'});
      label.textContent = text; graphic.append(label);
    }
    box.append(graphic);
  };
  draw(width);
  new ResizeObserver(entries => {
    const next = Math.round(entries[0].contentRect.width);
    if (next && Math.abs(next - (box.firstChild?.viewBox.baseVal.width || 0)) > 4) draw(next);
  }).observe(box);
  return box;
}

/** Levels: a card per measure, a range switch, and the selected measure's chart and facts. */
function levels(host, data) {
  if (!data.levels.length) return;
  const all = data.levels.flatMap(level => level.points.map(point => point.date)).sort();
  const firstYear = all[0].slice(0, 4), lastYear = all.at(-1).slice(0, 4);
  const yearAgo = new Date(parse(data.today).getTime() - 365 * DAY).toISOString().slice(0, 10);
  let range = 'all', chosen = 0, pointIndex = null;
  const ranges = el('div', null, 'brief-ranges');
  ranges.setAttribute('role', 'group'); ranges.setAttribute('aria-label', 'Time range');
  const rangeButtons = [['all', firstYear === lastYear ? firstYear : `${firstYear} to ${lastYear}`], ['year', 'Past year']].map(([key, label]) => {
    const button = dom().button(label, 'brief-range');
    button.dataset.range = key;
    button.addEventListener('click', () => { range = key; pointIndex = null; paint(); });
    return button;
  });
  ranges.append(...rangeButtons);
  // Results come from this notebook; the same tests in other notebooks join only on request (#231).
  let scope = 'own';
  const scopes = el('div', null, 'brief-ranges');
  scopes.setAttribute('role', 'group'); scopes.setAttribute('aria-label', 'Results from');
  const scopeButtons = [['own', 'This notebook'], ['all', 'All notebooks']].map(([key, label]) => {
    const button = dom().button(label, 'brief-range');
    button.dataset.scope = key;
    button.addEventListener('click', () => { scope = key; pointIndex = null; paint(); });
    return button;
  });
  scopes.append(...scopeButtons);
  const controls = el('div', null, 'brief-level-controls');
  controls.append(scopes, ranges);
  const {head, id} = sectionHead('Levels', controls);
  const section = el('section', null, 'brief-levels');
  section.setAttribute('aria-labelledby', id);
  const cards = el('div', null, 'brief-level-cards');
  const detail = el('div', null, 'brief-level-detail');
  const live = el('p', null, 'visually-hidden'); live.setAttribute('aria-live', 'polite');
  section.append(head, cards, detail, live);
  host.append(section);
  const visible = level => level.points.filter(point => (scope === 'all' || point.own) && (range === 'all' || point.date >= yearAgo));

  function paint() {
    for (const button of rangeButtons) button.setAttribute('aria-pressed', String(button.dataset.range === range));
    for (const button of scopeButtons) button.setAttribute('aria-pressed', String(button.dataset.scope === scope));
    cards.replaceChildren();
    data.levels.forEach((level, index) => {
      const latest = level.latest;
      const card = dom().button(null, 'brief-level-card');
      card.setAttribute('aria-pressed', String(index === chosen));
      const top = el('span', null, 'brief-level-top');
      top.append(el('span', level.name, 'brief-level-name'), el('time', short(latest.date)));
      const value = el('span', null, 'brief-level-value');
      value.append(el('strong', `${amount(latest.value)}${latest.value2 != null ? '/' + amount(latest.value2) : ''}`), el('span', level.unit, 'brief-level-unit'));
      if (latest.status) value.append(el('span', latest.status.label, `status-block ${latest.status.tone === 'ok' ? 'is-ok' : 'is-alert'}`));
      card.append(top, value, spark(visible(level)));
      card.addEventListener('click', () => { chosen = index; pointIndex = null; paint(); });
      cards.append(card);
    });
    const level = data.levels[chosen];
    const points = visible(level);
    detail.replaceChildren();
    if (!points.length) { detail.append(el('p', 'No results in this range', 'brief-empty')); return; }
    if (pointIndex == null || pointIndex >= points.length) pointIndex = points.findLastIndex(point => point.own);
    const panel = el('div', null, 'brief-level-panel');
    const title = el('div', null, 'brief-level-title');
    const name = el('h3'); name.append(level.name + ' ', el('span', level.unit));
    const notes = [];
    if (level.reference.low != null) notes.push(`Shaded: below ${amount(level.reference.low)}${level.reference.high != null && level.reference.high < Math.max(...points.map(p => p.value)) * 1.15 ? ` and above ${amount(level.reference.high)}` : ''}, ${level.reference.shared ? 'outside the range on these reports' : 'the lower limit on most reports'}.`);
    if (scope === 'all' && level.other_notebooks.length) notes.push(`Hollow: from the ${level.other_notebooks.join(' and ')} notebook${level.other_notebooks.length > 1 ? 's' : ''}.`);
    title.append(name, el('p', notes.join(' '), 'brief-level-legend'));
    const graph = chart(level, points, data.treatments, index => { pointIndex = index; paint(); }, pointIndex);
    graph.tabIndex = 0;
    graph.setAttribute('aria-label', `${level.name} chart. Use the arrow keys to move between results.`);
    graph.addEventListener('keydown', event => {
      const next = {ArrowLeft: pointIndex - 1, ArrowRight: pointIndex + 1, Home: 0, End: points.length - 1}[event.key];
      if (next == null) return;
      event.preventDefault();
      pointIndex = Math.max(0, Math.min(points.length - 1, next));
      paint();
      detail.querySelector('.brief-chart')?.focus();
    });
    const facts = el('dl', null, 'brief-level-facts');
    const fact = (label, value, link) => {
      const row = el('div'); row.append(el('dt', label));
      const dd = el('dd', value);
      if (link) { const a = el('a', link.title, 'brief-level-source'); a.href = link.target; dd.append(a); }
      row.append(dd); facts.append(row);
    };
    const words = point => `${amount(point.value)}${point.value2 != null ? '/' + amount(point.value2) : ''} ${level.unit}, ${long(point.date)}`;
    fact('Latest', words(level.latest));
    if (level.previous) fact('Previous', words(level.previous));
    if (level.flagged.low) fact('Flagged low by the lab', `${level.flagged.low} of ${level.flagged.total} results`);
    if (level.flagged.high) fact('Flagged high by the lab', `${level.flagged.high} of ${level.flagged.total} results`);
    const picked = points[pointIndex];
    fact('Selected', words(picked), picked.target ? {title: `${picked.title}${picked.page ? ', page ' + picked.page : ''}`, target: picked.target} : null);
    live.textContent = `Selected ${words(picked)}`;
    panel.append(title, graph, facts);
    detail.append(panel);
  }
  paint();
}

/** Facts that are not levels, such as conditions, medicines and allergies. */
function otherFacts(host, facts) {
  const list = facts.filter(fact => !['lab_result', 'vital_sign'].includes(fact.kind) || isNaN(parseFloat(fact.value)));
  if (!list.length) return;
  const section = el('section', null, 'brief-facts');
  const {head, id} = sectionHead('Health facts');
  section.setAttribute('aria-labelledby', id);
  const rows = el('ul', null, 'brief-facts-list');
  for (const fact of list) {
    const row = el('li');
    const link = el('a', [fact.name, fact.value, fact.unit].filter(Boolean).join(' '));
    link.href = fact.target;
    row.append(link, el('span', `${fact.kind.replaceAll('_', ' ')}, ${short(fact.date)}`, 'brief-facts-meta'));
    rows.append(row);
  }
  section.append(head, rows);
  host.append(section);
}

let latestMount = 0;
// Level names of the notebook on screen, for chat follow-ups.
let levelNames = [];

/** The longest level name a text mentions as a word, or null. */
function levelNamed(text) {
  const lower = String(text).toLowerCase();
  return [...levelNames].sort((a, b) => b.length - a.length).find(name =>
      new RegExp(`\\b${name.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).test(lower)) || null;
}

/** Opens the Brief tab with one level's chart selected. */
function showLevel(name) {
  document.querySelector('#notebook-tab-overview')?.click();
  const card = [...document.querySelectorAll('#notebook-levels .brief-level-card')]
      .find(item => item.querySelector('.brief-level-name')?.textContent === name);
  card?.click();
  card?.scrollIntoView({block: 'start'});
  card?.focus({preventScroll: true});
}

async function mount(host, notebookId) {
  // Only the latest call renders; earlier ones that finish later are dropped.
  const call = ++latestMount;
  const levelHost = document.querySelector('#notebook-levels');
  const today = root.StellisDemoDate || root.StellisCalendarDates.formatDate(new Date());
  const [brief, context] = await Promise.all([
    dom().api(`/notebooks/${encodeURIComponent(notebookId)}/brief?today=${today}`),
    dom().api(`/notebooks/${encodeURIComponent(notebookId)}/health`),
  ]);
  if (call !== latestMount || location.pathname !== '/notebook/' + notebookId) return;
  host.replaceChildren();
  levelHost?.replaceChildren();
  if (brief.courses.length || brief.pregnancy) {
    host.append(timeline(brief));
    const bands = el('div', null, 'brief-courses');
    if (brief.pregnancy) bands.append(pregnancyBand(brief.pregnancy, brief.today));
    for (const record of brief.courses) bands.append(course(record, brief.today));
    host.append(bands);
  }
  if (brief.pregnancy) {
    const row = el('div', null, 'brief-coming-wrap');
    row.append(brief.coming_up.length ? comingUp(brief.coming_up) : el('p', 'Nothing is coming up', 'brief-empty'),
        birthCard(brief.pregnancy, () => mount(host, notebookId)));
    host.append(row);
  } else if (brief.coming_up.length) host.append(comingUp(brief.coming_up));
  if (levelHost) {
    levels(levelHost, brief);
    otherFacts(levelHost, context.facts);
  }
  levelNames = brief.levels.map(level => level.name);
  root.dispatchEvent(new Event('stellis:brief-loaded'));
}

root.StellisNotebookHealth = {mount, levelNamed, showLevel};
})(window);
