/**
 * @fileoverview Health timeline page: documents, recordings and accepted
 * facts grouped by year, newest first, with kind and notebook filters.
 */
(function(root) {
'use strict';

const FILTERS = [
  {id: '', label: 'All'},
  {id: 'document', label: 'Documents'},
  {id: 'recording', label: 'Recordings'},
  {id: 'fact', label: 'Facts'},
  {id: 'reading', label: 'Readings'},
  {id: 'event', label: 'Appointments'},
];
const ICONS = {
  document: 'file-text',
  recording: 'microphone',
  fact: 'check', reading: 'activity', event: 'calendar'
};
const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov',
  'Dec'
];

/**
 * Groups entries by year, keeping their order.
 * @param {!Array<{date: string}>} entries Entries sorted newest first.
 * @return {!Array<{year: string, entries: !Array<!Object>}>}
 */
/** Entries shown at first, and added by each Show more. */
const PAGE = 80;

function groupByYear(entries) {
  const groups = [];
  for (const entry of entries) {
    const year = entry.date.slice(0, 4);
    if (!groups.length || groups[groups.length - 1].year !== year) {
      groups.push({year, entries: []});
    }
    groups[groups.length - 1].entries.push(entry);
  }
  return groups;
}

/**
 * Formats YYYY-MM-DD as a short day, such as "Apr 2".
 * @param {string} date
 * @return {string}
 */
function shortDate(date) {
  const [, month, day] = date.split('-').map(Number);
  return `${MONTHS[month - 1]} ${day}`;
}

/** The timeline page. */
class Timeline {
  /** @param {!Element} host */
  constructor(host) {
    const dom = root.StellisDom;
    this.dom = dom;
    this.kind = '';
    this.request = 0;
    this.entries = [];
    this.shown = PAGE;
    const head = dom.el('div', null, 'timeline-head');
    head.append(dom.el('h2', 'Timeline'));
    this.filters = dom.el('div', null, 'timeline-filters');
    this.filters.setAttribute('role', 'group');
    this.filters.setAttribute('aria-label', 'Show');
    for (const filter of FILTERS) {
      const control = dom.button(filter.label, 'timeline-filter');
      control.dataset.kind = filter.id;
      control.addEventListener('click', () => {
        this.kind = filter.id;
        this.load();
      });
      this.filters.append(control);
    }
    this.notebook = /** @type {!HTMLSelectElement} */ (dom.el('select'));
    this.notebook.setAttribute('aria-label', 'Notebook');
    this.notebook.addEventListener('change', () => this.load());
    this.controls = dom.el('div', null, 'timeline-controls');
    this.controls.append(this.filters, this.notebook);
    this.status = dom.el('p', null, 'timeline-status');
    this.status.setAttribute('role', 'status');
    this.list = dom.el('div', null, 'timeline-years');
    host.replaceChildren(head, this.controls, this.status, this.list);
  }

  /** @param {!Array<{id: string, name: string}>} notebooks */
  setNotebooks(notebooks) {
    this.notebook.replaceChildren(
        new Option('All notebooks', ''),
        ...notebooks.map((book) => new Option(book.name, book.id)));
  }

  paintFilters() {
    for (const control of this.filters.querySelectorAll('button')) {
      control.setAttribute(
          'aria-pressed', String(control.dataset.kind === this.kind));
    }
  }

  /**
   * @param {!Object} entry
   * @return {!Element}
   */
  row(entry) {
    const dom = this.dom;
    const item = dom.el('li', null, 'timeline-entry');
    item.dataset.kind = entry.kind;
    const time = dom.el('time', shortDate(entry.date), 'timeline-date');
    time.setAttribute('datetime', entry.date);
    const link = /** @type {!HTMLAnchorElement} */ (
        dom.el('a', entry.title, 'timeline-title'));
    link.href = entry.target;
    const text = dom.el('div', null, 'timeline-text');
    text.append(link);
    const detail =
        entry.source ? `${entry.detail}, ${entry.source}` : entry.detail;
    if (detail) {
      text.append(dom.el('span', detail, 'timeline-detail'));
    }
    item.append(time, dom.icon(ICONS[entry.kind]), text);
    return item;
  }

  /** @param {!Array<!Object>} entries */
  paint(entries) {
    const dom = this.dom;
    this.entries = entries;
    this.paintFilters();
    // Years of daily readings run to thousands of rows; the newest come first, the rest on request.
    const shown = entries.slice(0, this.shown);
    this.list.replaceChildren(...groupByYear(shown).map((group) => {
      const section = dom.el('section', null, 'timeline-year');
      const heading = dom.el('h3', group.year);
      heading.id = `timeline-${group.year}`;
      section.setAttribute('aria-labelledby', heading.id);
      const list = dom.el('ol', null, 'timeline-list');
      list.append(...group.entries.map((entry) => this.row(entry)));
      section.append(heading, list);
      return section;
    }));
    // Never an empty page: with nothing yet it says what will appear and offers the first step.
    if (!entries.length) {
      const filtered = this.kind || this.notebook.value;
      const start = dom.el('div', null, 'timeline-start');
      start.append(dom.el('p', filtered ? 'Nothing in the timeline matches this filter.' :
          'Your records, visits and readings appear here by date as you add them.'));
      if (!filtered && window.StellisHealthCapture) {
        const add = dom.button('Add a record', 'coach-button is-primary');
        add.addEventListener('click', () => window.StellisHealthCapture.open('upload', {notebookId: null}));
        start.append(add);
      }
      this.list.append(start);
    }
    if (entries.length > shown.length) {
      const more = dom.button(`Show ${Math.min(PAGE, entries.length - shown.length)} more`, 'coach-button timeline-more');
      more.addEventListener('click', () => {
        this.shown += PAGE;
        this.paint(this.entries);
      });
      this.list.append(more);
    }
    this.status.textContent = '';
    // Filters over nothing are empty items; they appear once there is something to filter.
    this.controls.hidden =
        !entries.length && !this.kind && !this.notebook.value;
  }

  async load() {
    const request = ++this.request;
    this.shown = PAGE;
    const query = new URLSearchParams();
    if (this.kind) {
      query.set('kinds', this.kind);
    }
    if (this.notebook.value) {
      query.set('notebook_id', this.notebook.value);
    }
    try {
      const data = await this.dom.api(`/timeline?${query}`);
      if (request === this.request) {
        this.paint(data.entries);
      }
    } catch (error) {
      this.status.textContent = error.message;
    }
  }
}

/**
 * Shows the timeline in host.
 * @param {!Element} host
 * @param {!Array<{id: string, name: string}>} notebooks
 * @return {!Promise<void>}
 */
function mount(host, notebooks) {
  const timeline = new Timeline(host);
  timeline.setNotebooks(notebooks);
  return timeline.load();
}

const exported = {
  mount,
  groupByYear,
  shortDate
};
root.StellisTimeline = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
