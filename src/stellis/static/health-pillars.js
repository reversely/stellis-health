/**
 * @fileoverview The pillars of Stellis Health as cards. Home shows records
 * and appointments, and questions; goals have their own section there
 * (goals.js). Each notebook also shows its check-ins. Each card draws the
 * person's own data: a dated strip of records, questions from the intake
 * call's notes, and a ring of finished check-ins. The drawings animate when
 * their data arrives.
 */
(function(root) {
'use strict';

const SVG = 'http://www.w3.org/2000/svg';
const API = '/api/v1';
const MAX_QUESTIONS = 3;
const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov',
  'Dec'
];

const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
const svg = (tag, attrs = {}) => {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs))
    node.setAttribute(key, value);
  return node;
};
const icon = (name) => {
  const node = svg('svg', {class: 'icon', 'aria-hidden': 'true'});
  node.append(svg('use', {href: '#i-' + name}));
  return node;
};
const get = (path) =>
    fetch(API + path).then((r) => (r.ok ? r.json() : null)).catch(() => null);
const plural = (count, noun) => `${count} ${noun}${count === 1 ? '' : 's'}`;
/** Formats 2026-04-02 as "Apr 2, 2026". */
const shortDate = (value) => {
  const [y, m, d] = String(value).slice(0, 10).split('-').map(Number);
  return y && m && d ? `${MONTHS[m - 1]} ${d}, ${y}` : '';
};
/**
 * A record's date: its record date, a date its title starts with, or its
 * upload.
 */
const recordDate = (item) => item.record_date ||
    (String(item.title).match(/^\d{4}-\d{2}-\d{2}/) || [])[0] ||
    item.created_at;
/**
 * Runs a change on the next frame, so a transition starts from the first
 * state.
 */
const settle = (fn) => requestAnimationFrame(() => requestAnimationFrame(fn));

/**
 * A dated strip: one dot per record along the years they span.
 * @param {!Array<string>} dates ISO dates.
 * @return {!SVGElement}
 */
function strip(dates) {
  const times = dates.map((d) => Date.parse(String(d).slice(0, 10)))
                    .filter(Number.isFinite)
                    .sort((a, b) => a - b);
  const graphic = svg(
      'svg',
      {class: 'pillar-strip', viewBox: '0 0 240 40', 'aria-hidden': 'true'});
  graphic.append(svg(
      'line', {x1: 8, y1: 24, x2: 232, y2: 24, class: 'pillar-strip-axis'}));
  if (!times.length) return graphic;
  const first = times[0];
  const span = Math.max(1, times[times.length - 1] - first);
  times.forEach((time, index) => {
    const x = times.length === 1 ? 120 : 8 + (224 * (time - first)) / span;
    const dot = svg('circle', {
      cx: x.toFixed(1),
      cy: 24,
      r: index === times.length - 1 ? 5 : 3.5,
      class: index === times.length - 1 ? 'is-latest' : ''
    });
    dot.style.setProperty('--i', index);
    graphic.append(dot);
  });
  for (const [time, anchor] of [
           [first, 'start'], [times[times.length - 1], 'end']]) {
    if (times.length < 2 && anchor === 'start') continue;
    const label =
        svg('text',
            {x: anchor === 'start' ? 8 : 232, y: 10, 'text-anchor': anchor});
    label.textContent = String(new Date(time).getUTCFullYear());
    graphic.append(label);
  }
  return graphic;
}

/**
 * A ring that fills to the share of check-ins done.
 * @param {number} done
 * @param {number} total
 * @return {!SVGElement}
 */
function ring(done, total) {
  const graphic =
      svg('svg',
          {class: 'pillar-ring', viewBox: '0 0 64 64', 'aria-hidden': 'true'});
  graphic.append(
      svg('circle', {cx: 32, cy: 32, r: 26, class: 'pillar-ring-track'}));
  const arc = svg('circle', {
    cx: 32,
    cy: 32,
    r: 26,
    class: 'pillar-ring-arc',
    pathLength: 100,
    transform: 'rotate(-90 32 32)'
  });
  arc.style.strokeDasharray = '0 100';
  graphic.append(arc);
  const label = svg('text', {x: 32, y: 37, 'text-anchor': 'middle'});
  label.textContent = total ? `${done}/${total}` : '0';
  graphic.append(label);
  const share = total ? Math.round((100 * done) / total) : 0;
  settle(() => {
    arc.style.strokeDasharray = `${share} 100`;
  });
  return graphic;
}

/**
 * Lowercases a note line's first letter unless it starts a name like "HbA1c".
 */
function topic(line) {
  const text =
      String(line).replace(/[.,;:]+$/, '').split(/,| since | in \d/)[0].trim();
  return /^[A-Z][a-z]/.test(text) ? text[0].toLowerCase() + text.slice(1) :
                                    text;
}

/**
 * Questions to ask about the person's records, from the call's notes.
 * @param {!Array<{heading: string, items: !Array<string>}>} notes
 * @param {?string} within Only topics that share a word with this name.
 * @return {!Array<string>}
 */
function questions(notes, within = null) {
  const words = within ?
      new Set(within.toLowerCase().split(/\W+/).filter((w) => w.length > 3)) :
      null;
  const asks = [];
  for (const heading of ['Goals', 'Conditions', 'Medicines']) {
    const section = notes.find((s) => s.heading === heading);
    for (const item of section?.items || []) {
      const subject = topic(item);
      if (!subject || subject.length > 48) continue;
      if (words &&
          !subject.toLowerCase().split(/\W+/).some((w) => words.has(w)))
        continue;
      asks.push(
          heading === 'Medicines' ? `What do my records say about ${subject}?` :
              heading === 'Goals' ? `What has helped me ${subject}?` :
                                    `What do my records say about ${subject}?`);
    }
  }
  return [...new Set(asks)].slice(0, MAX_QUESTIONS);
}

/** One card: icon, heading, a graphic, a bare figure, and a detail line. */
function card(cls, glyph, heading, href) {
  const node = el('a', null, 'pillar ' + cls);
  node.href = href;
  const head = el('div', null, 'pillar-head');
  head.append(icon(glyph), el('h2', heading));
  const figure = el('div', null, 'pillar-figure');
  const body = el('div', null, 'pillar-body');
  node.append(head, figure, body);
  return {node, figure, body};
}

function askLinks(asks, notebookId) {
  const list = el('div', null, 'pillar-asks');
  for (const ask of asks) {
    const link = el('a', ask, 'pillar-ask');
    link.href = notebookId ? `/notebook/${encodeURIComponent(notebookId)}?ask=${
                                 encodeURIComponent(ask)}` :
                             '/chat';
    link.addEventListener('click', (event) => event.stopPropagation());
    list.append(link);
  }
  return list;
}

function openItems(groups) {
  return (groups || [])
      .flatMap(
          (group) =>
              group.items.map((item) => ({...item, source: group.source})));
}

/**
 * Fills Home's records and questions cards.
 * @param {!Element} host
 */
async function home(host) {
  const [documents, conversations, intake, books] = await Promise.all([
    get('/documents'),
    get('/conversations'),
    get('/intake'),
    get('/notebooks'),
  ]);
  const records = [
    ...(documents || []).map((d) => ({title: d.title, date: recordDate(d)})),
    ...(conversations || [])
        .map((c) => ({title: c.title, date: c.recorded_at || c.created_at})),
  ].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const notebooks = books?.notebooks || [];

  const recordCard = card(
      'pillar-records', 'file-text', 'Records and appointments', '/records');
  recordCard.figure.append(strip(records.map((r) => r.date)));
  if (records.length) {
    const latest = records[records.length - 1];
    recordCard.body.append(
        el('strong', plural(records.length, 'record'), 'pillar-stat'),
        el('span', latest.title, 'pillar-detail'),
        el('time', shortDate(latest.date), 'pillar-date'));
  } else {
    recordCard.body.append(
        el('span', 'Add your letters and results', 'pillar-detail'));
  }

  const notes = intake?.notes || [];
  const asks = questions(notes);
  const questionCard = card(
      'pillar-questions', 'message-2', 'Questions',
      notebooks[0] ? `/notebook/${encodeURIComponent(notebooks[0].id)}` :
                     '/chat');
  if (asks.length) {
    questionCard.figure.append(askLinks(asks, notebooks[0]?.id));
  } else {
    questionCard.body.append(
        el('span', 'Ask about your records in any notebook', 'pillar-detail'));
  }

  // Goals have their own section, Today (goals.js).
  host.replaceChildren(recordCard.node, questionCard.node);
  host.classList.add('is-ready');
}

/**
 * Fills a notebook's three tiles, which switch its tabs.
 * @param {!Element} host
 * @param {!Object} book The notebook, with conversations and pages.
 * @param {function(string)} showTab Opens the "list", "chat" or "tasks" tab.
 */
async function notebook(host, book, showTab) {
  const id = encodeURIComponent(book.id);
  const [documents, open, done, intake] = await Promise.all([
    get('/documents'),
    get(`/todos?notebook_id=${id}&status=open`),
    get(`/todos?notebook_id=${id}&status=done`),
    get('/intake'),
  ]);
  const dates = [
    ...(documents || [])
        .filter((d) => (d.notebook_ids || [d.notebook_id]).includes(book.id))
        .map(recordDate),
    ...(book.conversations || [])
        .map((c) => c.recorded_at || c.created_at || c.date),
  ].filter(Boolean);
  const tile = (cls, glyph, heading, tab) => {
    const node = el('button', null, 'pillar pillar-tile ' + cls);
    node.type = 'button';
    const head = el('div', null, 'pillar-head');
    head.append(icon(glyph), el('h3', heading));
    const figure = el('div', null, 'pillar-figure');
    node.append(head, figure);
    node.addEventListener('click', () => showTab(tab));
    return {node, figure};
  };
  const records = tile('pillar-records', 'file-text', 'Records', 'list');
  records.figure.append(
      strip(dates),
      el('strong', plural(dates.length, 'record'), 'pillar-stat'));
  const asks = questions(intake?.notes || [], book.name);
  const ask = tile('pillar-questions', 'message-2', 'Ask', 'chat');
  ask.figure.append(
      el('span', asks[0] || 'Ask about these records', 'pillar-detail'));
  if (asks[0]) {
    ask.node.addEventListener(
        'click', () => root.StellisNotebookChat?.prefill?.(asks[0]));
  }
  const openList = openItems(open?.groups);
  const doneCount = openItems(done?.groups).length;
  const goals = tile('pillar-goals', 'target', 'Check-ins', 'tasks');
  const goal =
      openList.find((item) => item.source?.title === 'Goals') || openList[0];
  goals.figure.append(
      ring(doneCount, doneCount + openList.length),
      el('span',
         goal          ? goal.text.replace(/\s\^\w+$/, '') :
             doneCount ? 'Every check-in is done' :
                         'No check-ins yet',
         'pillar-detail'));
  host.replaceChildren(records.node, ask.node, goals.node);
  host.classList.add('is-ready');
}

root.StellisHealthPillars = {
  home,
  notebook,
  questions,
  strip,
  ring
};
if (typeof module !== 'undefined') module.exports = root.StellisHealthPillars;
})(typeof window !== 'undefined' ? window : globalThis);
