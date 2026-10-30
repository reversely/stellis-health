/**
 * @fileoverview Notebook chat: the Sources panel with checkboxes and one chat
 * history that answers from the checked sources with numbered citations.
 */
(function(root) {
'use strict';

/** Source kinds as the viewer names them. */
const TYPE_LABELS = {recording: 'Recording', audio: 'Recording', note: 'Note', pdf: 'Document', image: 'Image'};

/** Icon names by source type. */
const ICONS = {
  recording: 'microphone',
  audio: 'file',
  note: 'note',
  pdf: 'file-text',
  image: 'file',
};

/** Labels for sources that cannot be checked yet. */
const STATUS_LABELS = {
  processing: 'Transcribing',
  failed: 'Failed',
  needs_text: 'Needs text',
};

/** How often to re-read sources while one is still transcribing. */
const REFRESH_MS = 5000;

/**
 * @typedef {{n: number, source_id: string, title: string, target: string,
 *     start_ms: ?number, page: ?number, excerpt: string, kind: (string|undefined),
 *     provider: (string|undefined)}}
 */
let Citation;

/** @typedef {{text: string}|{citation: !Citation}} */
let AnswerPart;

/**
 * Splits answer text into paragraphs of text and citations.
 * @param {string} content Answer text with [[n]] markers.
 * @param {!Array<!Citation>} citations
 * @return {!Array<!Array<!AnswerPart>>} One list of parts per paragraph.
 *     A marker without a matching citation stays as text.
 */
function answerParts(content, citations) {
  const byNumber = new Map(citations.map((item) => [item.n, item]));
  // Punctuation the model put after a marker goes before it, as footnotes sit, so no gap opens between
  // the chip and its full stop ("course 6 ." in a demo answer, owner 2026-10-30).
  content = content.replace(/[ \t]*((?:\[\[\d+\]\][ \t]*)+?)[ \t]*([.,;:!?])(?=\s|$)/g, (_, markers, mark) => `${mark} ${markers.trim()}`);
  // Each paragraph is its own message bubble. A reply without blank lines separates its points with
  // single line breaks instead.
  const blocks = /\n\s*\n/.test(content.trim()) ? content.split(/\n\s*\n/) : content.split(/\s*\n\s*/);
  return blocks.filter((block) => block.trim()).map(
      (block) => block.split(/(\[\[\d+\]\])/).filter(Boolean).map((part) => {
        const match = part.match(/^\[\[(\d+)\]\]$/);
        const citation = match && byNumber.get(Number(match[1]));
        return citation ? {citation} : {text: part};
      }));
}

/** Renders the shared answer bubbles with a caller-supplied source link. */
function answerBody(content, citations, citationLink) {
  const dom = root.StellisDom;
  const body = dom.el('div', null, 'nb-answer');
  for (const parts of answerParts(content, citations)) {
    const paragraph = dom.el('p');
    for (const part of parts) {
      paragraph.append(
          part.citation ? citationLink(part.citation) : part.text);
    }
    body.append(paragraph);
  }
  return body;
}

/**
 * Formats a saved answer as Markdown with its sources listed as links.
 * @param {string} content Answer text with [[n]] markers.
 * @param {!Array<!Citation>} citations
 * @return {string}
 */
function answerMarkdown(content, citations) {
  const text = content.replace(/\[\[(\d+)\]\]/g, '[$1]');
  if (!citations.length) {
    return text;
  }
  const links = citations.map((item) => {
    const where = item.page ? `${item.title}, page ${item.page}` : item.title;
    return `- ${item.n}. [${where.replace(/[[\]]/g, '')}](${item.target})`;
  });
  return `${text}\n\n## Sources\n\n${links.join('\n')}`;
}

/**
 * Returns the app link that opens a source.
 * @param {string} sourceId A typed source ID such as "document:abc".
 * @return {string}
 */
function sourceTarget(sourceId) {
  const [kind, id] = sourceId.split(':');
  return `/${kind}/${encodeURIComponent(id)}`;
}

/**
 * Questions worth asking first, built from this notebook's own Brief and sources.
 * @param {?{levels: !Array<!Object>, coming_up: !Array<!Object>}} brief
 * @param {!Array<{title: string, status: string}>} sources
 * @return {!Array<string>} At most four questions, the most pressing first.
 */
function starterQuestions(brief, sources) {
  const questions = [];
  const levels = (brief?.levels || []).filter((level) => level.latest?.status);
  for (const level of levels.filter((item) => item.latest.status.tone === 'alert').slice(0, 1)) {
    questions.push(`What does my ${level.name} of ${level.latest.value} ${level.unit} mean?`);
  }
  const changed = levels.find((level) => level.points.filter((point) => point.own).length > 1);
  if (changed) {
    const first = changed.points.find((point) => point.own);
    questions.push(`How has my ${changed.name} changed since ${first.date.slice(0, 4)}?`);
  }
  const next = brief?.coming_up?.find((item) => item.status === 'planned' || item.status === 'scheduled');
  if (next) questions.push(`What should I ask before ${next.title}?`);
  const ready = sources.filter((source) => source.status === 'ready');
  if (ready.length) questions.push(`Summarize ${ready[0].title}`);
  if (ready.length > 1 && questions.length < 4) questions.push('What changed across these records?');
  return [...new Set(questions)].slice(0, 4);
}

/** The chat, its Sources panel and its source viewer for one notebook. */
/**
 * Whether an answer is about change over time: the question asks how something changed or went, or the
 * answer itself walks through three or more dated results.
 * @param {string|undefined} question
 * @param {string} answer
 * @return {boolean}
 */
function asksTrend(question, answer) {
  if (/\b(trends?|chang\w*|over time|over the years|history|rise|rising|risen|fall\w*|drop\w*|improv\w*|progress\w*|went|gone|going)\b/i.test(question || '')) return true;
  const dates = String(answer).match(/\b(19|20)\d{2}[-\u2010\u2011]\d{2}[-\u2010\u2011]\d{2}\b|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4}\b/g) || [];
  return dates.length >= 3;
}

class NotebookChat {
  /**
   * @param {!Element} host
   * @param {{id: string}} book
   * @param {{onRecord: (function()|undefined),
   *     onNote: (function(!Element)|undefined)}} options
   */
  constructor(host, book, options) {
    const dom = root.StellisDom;
    this.dom = dom;
    this.book = book;
    this.options = options;
    this.sources = [];
    this.agents = [];
    this.limit = 50;
    this.lastQuestion = '';
    this.stopped = false;
    this.timer = 0;

    this.layout = dom.el('div', null, 'notebook-chat');
    const wide = window.matchMedia('(min-width: 761px)').matches;
    this.layout.dataset.sources = wide ? 'open' : 'closed';
    this.viewer = root.StellisNotebookViewer.create();
    this.viewer.element.addEventListener('vieweropen', () => {
      this.layout.dataset.viewer = 'open';
    });
    this.viewer.element.addEventListener('viewerclose', () => {
      delete this.layout.dataset.viewer;
      for (const open of this.layout.querySelectorAll('a.nb-cite[aria-current]')) {
        open.removeAttribute('aria-current');
      }
    });
    this.layout.append(
        this.buildSourcesPanel(), this.buildChat(), this.viewer.element);
    host.replaceChildren(this.layout);
  }

  /** @return {!Element} The Sources panel. */
  buildSourcesPanel() {
    const dom = this.dom;
    const panel = dom.el('aside', null, 'nb-sources');
    panel.setAttribute('aria-label', 'Sources');

    this.toggle = dom.button(null, 'nb-sources-toggle');
    this.toggleCount = dom.el('span', '', 'nb-sources-count');
    this.toggle.append(
        dom.icon('list'), dom.el('span', 'Sources', 'nb-sources-label'),
        this.toggleCount);
    this.toggle.addEventListener('click', () => this.toggleSources());

    const body = dom.el('div', null, 'nb-sources-body');
    body.id = `nb-sources-body-${this.book.id}`;
    this.toggle.setAttribute('aria-controls', body.id);

    this.addButton = dom.button(null, 'nb-source-add');
    this.addButton.append(dom.icon('plus'), dom.el('span', 'Add source'));
    this.addButton.addEventListener('click', () => this.openAddSource());
    // New note sits in the notebook header; the panel keeps only what is its own (#258).
    const actions = dom.el('div', null, 'nb-sources-actions');
    actions.append(this.addButton);

    this.selectAll = dom.el('label', null, 'nb-source-all');
    this.selectAllBox = /** @type {!HTMLInputElement} */ (dom.el('input'));
    this.selectAllBox.type = 'checkbox';
    this.selectAllBox.addEventListener('change', () => {
      for (const source of this.readySources()) {
        source.selected = this.selectAllBox.checked;
      }
      this.saveSelection();
    });
    this.selectAll.append(dom.el('span', 'Select all'), this.selectAllBox);

    this.list = dom.el('ul', null, 'nb-source-list');
    this.empty = dom.el(
        'p', 'Add a recording, note or document to ask about it', 'nb-empty');
    // Related sources the latest answer found in other notebooks.
    this.addFrom = dom.el('div', null, 'nb-add-from');
    body.append(actions, this.selectAll, this.list, this.empty, this.addFrom);
    panel.append(this.toggle, body);
    return panel;
  }

  /** @return {!Element} The chat log and composer. */
  buildChat() {
    const dom = this.dom;
    const chat = dom.el('section', null, 'nb-chat');
    chat.setAttribute('aria-label', 'Chat');

    this.log = dom.el('div', null, 'nb-messages');
    this.log.setAttribute('role', 'log');
    this.log.addEventListener('click', (event) => this.openCitation(event));
    // Before the first question: what this notebook holds and what to ask (see welcome()).
    this.intro = dom.el('div', null, 'nb-welcome');

    this.form = dom.el('form', null, 'nb-composer');
    this.answerWith = dom.el('label', null, 'nb-answer-with');
    this.picker = /** @type {!HTMLSelectElement} */ (dom.el('select'));
    this.answerWith.append(dom.el('span', 'Answer with'), this.picker);
    this.answerWith.hidden = true;

    this.researchBox = dom.el('label', null, 'nb-research');
    this.research = /** @type {!HTMLInputElement} */ (dom.el('input'));
    this.research.type = 'checkbox';
    try {
      // On until the person turns it off; every query passes the privacy check first.
      this.research.checked = localStorage.getItem('stellis-research') !== 'off';
    } catch (error) {
      this.research.checked = true;
    }
    this.research.addEventListener('change', () => {
      try {
        localStorage.setItem('stellis-research', this.research.checked ? 'on' : 'off');
      } catch (error) {}
    });
    this.researchBox.append(this.research, dom.el('span', 'Search public sources'));

    this.input = /** @type {!HTMLTextAreaElement} */ (dom.el('textarea'));
    this.input.rows = 2;
    this.input.maxLength = 2000;
    this.input.setAttribute('aria-label', 'Question');
    this.input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        this.form.requestSubmit();
      }
    });
    this.send = root.StellisChatUI.sendButton(dom.el('button', 'Send'));
    this.send.type = 'submit';
    this.input.placeholder = 'Ask about your sources';
    const box = dom.el('div', null, 'nb-input');
    box.append(this.input, this.send);
    root.StellisChatUI.composer(box, this.input);

    this.status = dom.el('p', null, 'nb-status');
    this.status.setAttribute('role', 'status');
    this.attachments=root.StellisChatAttachments.create({kind:'notebook',id:this.book.id,books:[this.book],notebookId:()=>this.book.id,onChange:()=>this.paintComposer(),onFiled:()=>this.refreshSources().catch(error=>this.status.textContent=error.message)});
    const options = dom.el('div', null, 'nb-composer-options');
    options.append(this.attachments.element, this.researchBox, this.answerWith);
    this.form.append(box, options, this.status);
    this.form.addEventListener('submit', (event) => {
      event.preventDefault();
      this.submit();
    });
    chat.append(this.log, this.form);
    return chat;
  }

  /** @return {!Array<!Object>} Sources that have text. */
  readySources() {
    return this.sources.filter((source) => source.status === 'ready');
  }

  /** @return {!Array<!Object>} Checked sources. */
  checkedSources() {
    return this.readySources().filter((source) => source.selected);
  }

  toggleSources() {
    const open = this.layout.dataset.sources === 'open';
    this.layout.dataset.sources = open ? 'closed' : 'open';
    this.paintToggle();
  }

  paintToggle() {
    const open = this.layout.dataset.sources === 'open';
    const checked = this.checkedSources().length;
    const total = this.sources.length;
    this.toggle.setAttribute('aria-expanded', String(open));
    this.toggle.setAttribute(
        'aria-label',
        `${open ? 'Hide' : 'Show'} sources, ${checked} of ${total} checked`);
    this.toggleCount.textContent = `${checked}/${total}`;
  }

  /**
   * Puts a suggested question in the box for the person to send.
   * @param {string} question
   */
  prefill(question) {
    this.input.value = question.slice(0, this.input.maxLength);
    if (!this.input.disabled) this.input.focus();
  }

  paintComposer() {
    const count = this.checkedSources().length + (this.attachments?.pending().filter(item=>item.status==='ready').length||0);
    this.input.placeholder = count ?
        `Ask about ${this.dom.plural(count, 'source')}` :
        'Check a source to ask about it';
    this.input.disabled = !count;
    this.send.disabled = !count || this.form.dataset.busy === 'true' || this.attachments?.busy();
  }

  paintSources() {
    this.list.replaceChildren(
        ...this.sources.map((source) => this.sourceRow(source)));
    const ready = this.readySources().length;
    const checked = this.checkedSources().length;
    this.empty.hidden = this.sources.length > 0;
    // One Add source per screen (#232): an empty notebook's welcome carries it; the panel's appears once sources exist.
    this.addButton.hidden = this.sources.length === 0;
    this.selectAll.hidden = ready < 2;
    this.selectAllBox.checked = ready > 0 && checked === ready;
    this.selectAllBox.indeterminate = checked > 0 && checked < ready;
    this.paintToggle();
    this.paintComposer();
  }

  /**
   * @param {!Object} source
   * @return {!Element}
   */
  sourceRow(source) {
    const dom = this.dom;
    const row = dom.el('li', null, 'nb-source');
    row.dataset.status = source.status;
    const open = dom.button(null, 'nb-source-open');
    open.append(
        dom.icon(ICONS[source.type] || 'file'),
        dom.el('span', source.title, 'nb-source-title'));
    open.addEventListener(
        'click', () => this.viewer.show(sourceTarget(source.id), open));
    row.append(open);
    if (source.status !== 'ready') {
      row.append(
          dom.el('span', STATUS_LABELS[source.status], 'nb-source-status'));
      return row;
    }
    const box = /** @type {!HTMLInputElement} */ (dom.el('input'));
    box.type = 'checkbox';
    box.checked = source.selected;
    box.setAttribute('aria-label', `Use ${source.title}`);
    box.addEventListener('change', () => {
      source.selected = box.checked;
      this.saveSelection();
    });
    const check = dom.el('label', null, 'nb-source-check');
    check.append(box);
    row.append(check);
    return row;
  }

  async saveSelection() {
    this.paintSources();
    const excluded = this.readySources()
                         .filter((source) => !source.selected)
                         .map((source) => source.id);
    try {
      await this.dom.api(
          `/notebooks/${this.book.id}/sources/selection`,
          {method: 'PUT', json: {excluded}});
    } catch (error) {
      this.status.textContent = error.message;
    }
  }

  openAddSource() {
    root.StellisAddSource.open({
      book: this.book,
      count: this.sources.length,
      sourceIds: this.sources.map(source => source.id),
      limit: this.limit,
      trigger: this.addButton,
      onRecord: this.options.onRecord,
      onChanged: () => this.refreshSources().catch(() => {}),
    });
  }

  /** @param {!MouseEvent} event */
  openCitation(event) {
    const link = event.target.closest('a.nb-cite');
    // Public sources open on their own site in a new tab.
    if (!link || link.dataset.public || !this.dom.isPlainClick(event)) {
      return;
    }
    event.preventDefault();
    for (const open of this.layout.querySelectorAll('a.nb-cite[aria-current]')) {
      open.removeAttribute('aria-current');
    }
    link.setAttribute('aria-current', 'true');
    const source = this.sources.find(item => item.id === link.dataset.source);
    const date = source?.created_at ? new Date(String(source.created_at).slice(0, 10) + 'T12:00:00')
        .toLocaleDateString([], {month: 'long', day: 'numeric', year: 'numeric'}) : '';
    const page = new URL(link.href, location.origin).searchParams.get('page');
    const meta = [source ? TYPE_LABELS[source.type] : '', date, page ? `page ${page}` : ''].filter(Boolean).join(', ');
    this.viewer.show(link.getAttribute('href'), link, {number: link.textContent, meta});
  }

  /**
   * @param {string} content
   * @param {!Array<!Citation>} citations
   * @return {!Element}
   */
  answerBody(content, citations) {
    return answerBody(content, citations, citation => this.citationLink(citation));
  }

  /**
   * Describes where one query went, in plain words.
   * @param {!Array<string>} providers
   * @return {string}
   */
  services(providers) {
    const named = providers.filter(name => name !== 'Tavily');
    const list = named.length > 1 ? named.slice(0, -1).join(', ') + ' and ' + named.at(-1) : named[0] || '';
    if (!providers.includes('Tavily')) return list;
    return list ? `${list}, then the web with Tavily` : 'the web with Tavily';
  }

  /**
   * An information button whose note says what a search may contain.
   * @return {!Element}
   */
  searchInfo() {
    const dom = this.dom;
    const wrap = dom.el('span', null, 'nb-search-info');
    const button = dom.el('button', null, 'nb-search-info-button');
    button.type = 'button';
    button.setAttribute('aria-label', 'About these searches');
    button.setAttribute('aria-expanded', 'false');
    button.append(dom.icon('info-circle'));
    const note = dom.el('p', 'When Stellis looks something up, it sends only the topic, never your name, dates, numbers or ' +
        'other details. Your records are never sent to search services.', 'nb-search-note');
    note.id = 'nb-search-note-' + Math.random().toString(36).slice(2);
    note.hidden = true;
    button.setAttribute('aria-controls', note.id);
    button.addEventListener('click', () => {
      note.hidden = !note.hidden;
      button.setAttribute('aria-expanded', String(!note.hidden));
    });
    wrap.append(button, note);
    return wrap;
  }

  /**
   * Shows each search while the answer is prepared, as it is sent.
   * @param {string} requestId
   * @return {{element: !Element, stop: function()}}
   */
  liveSearch(requestId) {
    const dom = this.dom;
    const box = dom.el('section', null, 'nb-live-search');
    box.setAttribute('aria-label', 'Searches in progress');
    const head = dom.el('div', null, 'nb-live-head');
    const status = dom.el('p', 'Choosing what to search', 'nb-live-status');
    status.setAttribute('role', 'status');
    head.append(status, this.searchInfo());
    const list = dom.el('ul', null, 'nb-live-list');
    box.append(head, list);
    let stopped = false;
    const shown = new Map();
    const poll = async () => {
      if (stopped) return;
      try {
        const state = await dom.api(`/notebooks/${this.book.id}/ask/${requestId}/progress`);
        for (const query of state.queries) {
          let row = shown.get(query.text);
          if (!row) {
            row = dom.el('li');
            row.append(dom.icon('search'), dom.el('span'));
            shown.set(query.text, row);
            list.append(row);
          }
          row.lastElementChild.textContent = `Searching ${this.services(query.providers)} for “${query.text}”`;
        }
        // Each line already says what it searches; the heading only marks the stages around them.
        status.textContent = state.stage === 'answering' || state.stage === 'done' ? 'Writing the answer' :
            state.queries.length ? '' : 'Choosing what to search';
      } catch (error) {}
      if (!stopped) setTimeout(poll, 700);
    };
    poll();
    return {element: box, stop: () => { stopped = true; }};
  }

  /**
   * Summarises the searches an answer sent, folded under one line.
   * @param {?{queries: !Array<{text: string, providers: !Array<string>, results: number}>, blocked: number}} research
   * @param {!Array<!Citation>} citations The answer's citations; public ones are listed here.
   * @return {?Element}
   */
  searched(research, citations) {
    if (!research || (!research.queries.length && !research.blocked)) {
      return null;
    }
    const dom = this.dom;
    const row = dom.el('div', null, 'nb-searched-row');
    const box = dom.el('details', null, 'nb-searched');
    const first = research.queries[0];
    const more = research.queries.length - 1;
    box.append(dom.el('summary', first ?
        `Searched for “${first.text}”${more ? ` and ${more} more` : ''}` : 'No search sent'));
    if (research.queries.length) {
      const list = dom.el('ul');
      for (const query of research.queries) {
        const item = dom.el('li');
        item.append(dom.el('q', query.text),
            dom.el('span', `${this.services(query.providers)}, ${dom.plural(query.results, 'result')}`));
        list.append(item);
      }
      box.append(list);
    }
    const cited = citations.filter(citation => citation.kind === 'public');
    if (cited.length) {
      box.append(dom.el('h3', 'Public sources cited'));
      const list = dom.el('ol', null, 'nb-public-list');
      for (const citation of cited) {
        const item = dom.el('li');
        const link = dom.el('a', citation.title);
        link.href = citation.target;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        item.append(dom.el('span', String(citation.n), 'nb-cite nb-cite-public'), link, dom.el('span', citation.provider));
        list.append(item);
      }
      box.append(list);
    }
    if (research.blocked) {
      const many = research.blocked > 1;
      box.append(dom.el('p', `${research.blocked} ${many ? 'searches' : 'search'} held personal details and ${many ? 'were' : 'was'} not sent.`));
    }
    row.append(box, this.searchInfo());
    return row;
  }

  /**
   * @param {!Citation} citation
   * @return {!Element}
   */
  citationLink(citation) {
    const link = this.dom.el('a', String(citation.n), 'nb-cite');
    link.href = citation.target;
    link.dataset.source = citation.source_id;
    const where = citation.page ? `${citation.title}, page ${citation.page}` :
                                  citation.title;
    link.setAttribute('aria-label', `Source ${citation.n}: ${where}`);
    link.title = `${where}: ${citation.excerpt}`;
    if (citation.kind === 'public') {
      link.classList.add('nb-cite-public');
      link.dataset.public = 'true';
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.setAttribute('aria-label', `Public source ${citation.n}: ${where}, from ${citation.provider}, opens in a new tab`);
      link.title = `${where} (${citation.provider}): ${citation.excerpt}`;
    }
    return link;
  }

  /**
   * @param {!Object} item A stored chat message.
   * @return {!Element}
   */
  message(item) {
    const dom = this.dom;
    if (item.role === 'user') {
      this.lastQuestion = item.content;
      const node = dom.el('div', null, 'nb-msg nb-msg-user');
      node.append(dom.el('p', item.content),this.attachments.messages(item.id));
      return node;
    }
    const node = dom.el('article', null, 'nb-msg');
    if (item.author) {
      node.append(dom.el('strong', item.author, 'nb-msg-author'));
    }
    const citations = item.citations || [];
    node.append(this.answerBody(item.content, citations));
    const searched = this.searched(item.research, citations);
    if (searched) node.append(searched);
    const unread = item.unread || [];
    if (unread.length) {
      const names = unread.map(other => other.name);
      const list = names.length > 1 ? names.slice(0, -1).join(', ') + ' and ' + names.at(-1) : names[0];
      node.append(dom.el('p', `Related records are in the ${list} notebook${names.length > 1 ? 's' : ''}, which this answer did not read.`, 'nb-unread'));
    }
    const checked = citations.filter(citation => 'found' in citation);
    if (checked.length) {
      const line = dom.el('p', null, 'nb-check');
      line.append(dom.icon('checks'), `${checked.filter(c => c.found).length} of ${checked.length} citations found on their page`);
      node.append(line);
    }
    const actions = dom.el('div', null, 'nb-msg-actions');
    const trace = root.StellisAnswerTrace?.link({traceId: item.trace_id});
    if (trace) actions.append(trace);
    actions.append(this.saveButton(item.content, citations));
    node.append(actions);
    // Follow-ups from data: a chart for a level the answer names, and other notebooks to include. A
    // question about change over time gets the chart itself under the answer, drawn from the notebook's
    // own results, each point opening its report (owner, 2026-10-30); otherwise a button opens it.
    const follow = dom.el('div', null, 'nb-follow');
    const health = root.StellisNotebookHealth;
    const question = this.lastQuestion;
    const embed = () => {
      const level = health?.levelNamed?.(item.content);
      const figure = level && asksTrend(question, item.content) ? health.trend?.(level) : null;
      if (figure) { node.querySelector('.nb-follow-chart')?.remove(); actions.before(figure); }
      return {level, figure};
    };
    const {level, figure} = embed();
    if (level && !figure) {
      const chart = dom.button(`Open the ${level.toLowerCase()} chart`, 'nb-follow-chip nb-follow-chart');
      chart.addEventListener('click', () => root.StellisNotebookHealth.showLevel(level));
      follow.append(chart);
    }
    // History drawn before the notebook's Brief loaded gets its charts once the Brief is there.
    if (!level) root.addEventListener('stellis:brief-loaded', () => { if (node.isConnected && !node.querySelector('.nb-trend')) embed(); }, {once: true});
    // One button for the notebooks the line above names, not one per notebook.
    if (unread.length) {
      const again = dom.button(unread.length === 1 ? `Include ${unread[0].name} and ask again` : `Include all ${unread.length} and ask again`, 'nb-follow-chip');
      again.addEventListener('click', () => this.askWith(unread));
      follow.append(again);
    }
    if (follow.children.length) node.append(follow);
    return node;
  }

  /**
   * Creates the button that files an answer as a notebook note.
   * @param {string} content
   * @param {!Array<!Citation>} citations
   * @return {!Element}
   */
  saveButton(content, citations) {
    const save = this.dom.button('Save as note', 'nb-msg-save');
    const title = (this.lastQuestion || 'Saved answer').slice(0, 120);
    save.addEventListener('click', async () => {
      save.disabled = true;
      try {
        await this.dom.api('/pages', {
          method: 'POST',
          json: {
            title,
            notebook_id: this.book.id,
            markdown: answerMarkdown(content, citations),
          },
        });
        save.textContent = 'Saved as a note';
        await this.refreshSources();
      } catch (error) {
        this.status.textContent = error.message;
        save.disabled = false;
      }
    });
    return save;
  }

  paintAgents() {
    this.picker.replaceChildren(
        new Option('Sources', ''),
        ...this.agents.map((agent) => new Option(agent.name, agent.id)));
    this.answerWith.hidden = !this.agents.length;
  }

  /**
   * Shows Add from <notebook> for each notebook the latest answer did not read.
   * @param {!Array<!Object>} unread
   */
  paintAddFrom(unread) {
    this.addFrom.replaceChildren();
    for (const other of unread) {
      const add = this.dom.button(`Add from ${other.name}`, 'nb-add-from-button');
      add.addEventListener('click', async () => {
        add.disabled = true;
        try {
          for (const sourceId of other.source_ids) {
            await this.dom.api(`/notebooks/${this.book.id}/links`, {method: 'POST', json: {source_id: sourceId}});
          }
          add.remove();
          await this.refreshSources();
          this.status.textContent = `Added ${this.dom.plural(other.source_ids.length, 'source')} from ${other.name}`;
        } catch (error) { this.status.textContent = error.message; add.disabled = false; }
      });
      this.addFrom.append(add);
    }
  }

  /**
   * Asks the last question again with another notebook's matching sources, for this answer only.
   * @param {!Object} other An unread notebook from the answer.
   */
  askWith(others) {
    this.include = others.map(other => ({notebook_id: other.notebook_id, source_ids: other.source_ids}));
    this.input.value = this.lastQuestion;
    this.submit();
  }

  async submit() {
    const question = this.input.value.trim();
    const sourceIds = this.checkedSources().map((source) => source.id);
    if (!question || this.send.disabled || this.form.dataset.busy === 'true') {
      return;
    }
    this.form.dataset.busy = 'true';
    this.attachments.setSending(true);
    this.paintComposer();
    this.intro.remove();
    const previousQuestion = this.lastQuestion;
    const pending = this.message({role: 'user', content: question});
    this.log.append(pending);
    const requestId = root.crypto?.randomUUID ? root.crypto.randomUUID() :
        Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
    const live = this.research.checked ? this.liveSearch(requestId) : null;
    if (live) this.log.append(live.element);
    // Three dots hold the place of the answer until it arrives.
    const writing = root.StellisChatUI.typing();
    this.log.append(writing);
    this.input.value = '';
    this.status.textContent = `Reading ${this.dom.plural(sourceIds.length, 'source')}` +
        (this.research.checked ? ' and searching public sources' : '');
    try {
      await this.dom.api(`/notebooks/${this.book.id}/ask`, {
        method: 'POST',
        json: {
          question,
          attachment_ids: this.attachments.ids(),
          source_ids: sourceIds,
          agent_id: this.picker.value || null,
          include: this.include || [],
          research: this.research.checked,
          request_id: this.research.checked ? requestId : null,
        },
      });
      await this.attachments.load();
      if (!this.stopped) {
        const history=await this.dom.api(`/notebooks/${this.book.id}/chat`);
        this.log.replaceChildren(...history.messages.map(item=>this.message(item)));
        this.paintAddFrom(history.messages.filter(item => item.role === 'assistant').at(-1)?.unread || []);
        this.status.textContent = '';
      }
    } catch (error) {
      pending.remove();
      live?.element.remove();
      writing.remove();
      this.lastQuestion = previousQuestion;
      if (!this.input.value) {
        this.input.value = question;
      }
      this.status.textContent = error.message;
      this.offerStored(error.message);
    } finally {
      live?.stop();
      this.include = null;
      this.form.dataset.busy = 'false';
      this.attachments.setSending(false);
      this.paintComposer();
    }
  }

  async refreshSources() {
    const data = await this.dom.api(`/notebooks/${this.book.id}/sources`);
    if (this.stopped) {
      return;
    }
    this.sources = data.sources;
    this.limit = data.limit;
    this.paintSources();
    clearTimeout(this.timer);
    if (this.sources.some((source) => source.status === 'processing')) {
      this.timer = setTimeout(() => {
        if (location.pathname === `/notebook/${this.book.id}`) {
          this.refreshSources().catch(() => {});
        }
      }, REFRESH_MS);
    }
  }

  /**
   * Whether an agent answers in this notebook.
   * @param {!Object} agent
   * @return {boolean}
   */
  answersHere(agent) {
    return agent.enabled &&
        agent.notebooks.some(
            (item) =>
                item.notebook_id === this.book.id && item.enabled !== false);
  }

  /** Loads sources, chat history and the notebook's agents. */
  async start() {
    try {
      const [, history, team] = await Promise.all([
        Promise.all([this.refreshSources(),this.attachments.load()]),
        this.dom.api(`/notebooks/${this.book.id}/chat`),
        this.dom.api('/agents').catch(() => []),
      ]);
      if (this.stopped) {
        return;
      }
      this.agents = team.filter((agent) => this.answersHere(agent));
      this.paintAgents();
      if (history.messages.length) {
        this.log.append(...history.messages.map((item) => this.message(item)));
        this.paintAddFrom(history.messages.filter(item => item.role === 'assistant').at(-1)?.unread || []);
        // Level names arrive with the Brief; repaint so answers can offer their chart.
        this.onBrief = () => {
          if (this.stopped || this.form.dataset.busy === 'true') return;
          this.log.replaceChildren(...history.messages.map((item) => this.message(item)));
        };
        root.addEventListener('stellis:brief-loaded', this.onBrief, {once: true});
      } else if (!this.log.childElementCount && this.form.dataset.busy !== 'true') {
        // A question sent while the history loaded has already started the thread.
        this.log.append(this.intro);
        await this.welcome();
      }
    } catch (error) {
      this.status.textContent = error.message;
    }
  }

  /**
   * Fills the empty chat: the notebook, what it holds, its latest results and next date, and
   * questions built from them. Every line comes from the notebook's records; nothing is written
   * by a model.
   */
  async welcome() {
    const dom = this.dom;
    const brief = await dom.api(`/notebooks/${this.book.id}/brief`).catch(() => null);
    if (this.stopped || !this.intro.isConnected) return;
    const ready = this.readySources();
    const head = dom.el('div', null, 'nb-welcome-head');
    // The notebook heading sits directly above, so the welcome starts with what it holds.
    const counts = [dom.plural(this.sources.length, 'source')];
    const levels = (brief?.levels || []).filter((level) => level.latest?.status);
    if (levels.length) counts.push(dom.plural(levels.length, 'result') + ' tracked');
    head.append(dom.el('p', counts.join(', '), 'nb-welcome-meta'));
    if (this.book.description) head.append(dom.el('p', this.book.description, 'nb-welcome-about'));
    this.intro.replaceChildren(head);

    if (levels.length) {
      const list = dom.el('ul', null, 'nb-welcome-levels');
      list.setAttribute('aria-label', 'Latest results');
      for (const level of levels.slice(0, 4)) {
        const row = dom.el('li', null, 'nb-welcome-level' + (level.latest.status.tone === 'alert' ? ' is-alert' : ''));
        const link = dom.el('a', null, 'nb-welcome-level-link');
        link.href = level.latest.target;
        const date = new Date(level.latest.date + 'T12:00:00').toLocaleDateString([], {day: 'numeric', month: 'short', year: 'numeric'});
        const meta = dom.el('span', null, 'nb-welcome-level-meta');
        meta.append(dom.el('span', level.latest.status.label, 'nb-welcome-level-status'), dom.el('span', date));
        link.append(dom.el('span', level.name, 'nb-welcome-level-name'),
            dom.el('strong', `${level.latest.value} ${level.unit}`), meta);
        row.append(link);
        list.append(row);
      }
      this.intro.append(list);
    }
    const next = brief?.coming_up?.[0];
    if (next) {
      const when = new Date(next.date + 'T12:00:00').toLocaleDateString([], next.precision === 'month' ?
          {month: 'long', year: 'numeric'} : {weekday: 'short', day: 'numeric', month: 'short'});
      const line = dom.el('p', null, 'nb-welcome-next');
      line.append(dom.icon('calendar'), dom.el('span', `Coming up: ${next.title}, ${when}`));
      this.intro.append(line);
    }

    const questions = starterQuestions(brief, this.sources);
    const items = questions.length ?
        questions.map((question) => ({icon: 'message-2', title: question, action: {label: 'Ask', command: 'ask'}})) :
        // The header already has New note, so the welcome offers only the first source.
        [{icon: 'plus', title: 'Add a record, a recording or a link', detail: 'Stellis answers from what you add here', action: {label: 'Add source', command: 'add'}}];
    // The list sits in a white widget so its rows and actions use the paper roles in both themes.
    const panel = dom.el('div', null, 'nb-welcome-ideas widget-paper');
    this.intro.append(dom.el('h3', questions.length ? 'Suggested questions' : 'Start this notebook', 'nb-welcome-title'), panel);
    panel.append(root.StellisChatUI.ideas(items, (item, action) => {
          if (item.action.command === 'ask') {
            this.prefill(item.title);
            this.form.requestSubmit();
          } else if (item.action.command === 'add') {
            this.openAddSource();
          } else if (item.action.command === 'note') {
            this.options.onNote?.(action);
          }
        }));
    if (!ready.length && this.sources.length) {
      this.intro.append(dom.el('p', 'Check a source to ask about it', 'nb-empty'));
    }
  }

  /**
   * Once the deployment's AI budget is spent, questions with stored answers in this notebook are offered:
   * asking one replays its stored reply, which costs nothing (owner, 2026-10-30).
   * @param {string} message The refusal shown in the status line.
   */
  async offerStored(message) {
    this.stored?.remove();
    if (!/used its AI credits|allowance is used up/.test(message)) return;
    const found = await this.dom.api('/demo/stored-answers?scope=' + encodeURIComponent('notebook:' + this.book.name)).catch(() => null);
    // Answers already in this conversation are not offered again.
    const answered = new Set([...this.log.querySelectorAll('.nb-msg-user > p')].map(node => node.textContent));
    const questions = (found?.questions || []).filter(question => !answered.has(question));
    if (!questions.length || this.stopped) return;
    this.stored = this.dom.el('div', null, 'nb-stored');
    this.stored.append(this.dom.el('h3', 'Answers you can still open', 'nb-welcome-title'));
    const items = questions.map(question => ({icon: 'message-2', title: question, action: {label: 'Open', command: 'ask'}}));
    this.stored.append(root.StellisChatUI.ideas(items, item => { this.stored.remove(); this.prefill(item.title); this.form.requestSubmit(); }));
    this.status.after(this.stored);
  }

  stop() {
    this.stopped = true;
    if (this.onBrief) root.removeEventListener('stellis:brief-loaded', this.onBrief);
    clearTimeout(this.timer);
  }
}

/** @type {?NotebookChat} */
let current = null;

/**
 * Shows a notebook's chat in host, replacing any chat shown before.
 * @param {!Element} host
 * @param {{id: string}} book
 * @param {{onRecord: (function()|undefined),
 *     onNote: (function(!Element)|undefined)}=} options
 * @return {!Promise<void>}
 */
function mount(host, book, options = {}) {
  current?.stop();
  const chat = new NotebookChat(host, book, options);
  current = chat;
  // A question chosen on Home arrives as ?ask= and waits in the box.
  const ask = new URLSearchParams(root.location?.search || '').get('ask');
  return chat.start().then(() => {
    if (ask && chat === current) chat.prefill(ask);
  });
}

const exported = {
  mount,
  stop: () => current?.stop(),
  prefill: (question) => current?.prefill(question),
  answerParts,
  answerBody,
  answerMarkdown,
  sourceTarget,
  starterQuestions,
};
root.StellisNotebookChat = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
