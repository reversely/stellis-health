/**
 * @fileoverview Add source dialog: upload or drop files, record, paste text,
 * or bring in recordings, notes and documents from other notebooks.
 */
(function(root) {
'use strict';

const MAX_CANDIDATES = 30;
const RECORD_ICONS = {
  recording: 'microphone',
  note: 'note',
  document: 'file-text',
};

/**
 * @typedef {{kind: string, id: string, title: string,
 *     notebook_id: ?string, at: string}}
 */
let Candidate;

/**
 * Lists records outside a notebook that match a title filter.
 * @param {!Array<!Candidate>} items
 * @param {string} notebookId
 * @param {string} filter
 * @return {!Array<!Candidate>} At most MAX_CANDIDATES, newest first.
 */
function candidates(items, notebookId, filter) {
  const needle = filter.trim().toLocaleLowerCase();
  return items
      .filter(
          (item) => item.notebook_id !== notebookId &&
              (!needle || item.title.toLocaleLowerCase().includes(needle)))
      .sort((a, b) => String(b.at).localeCompare(String(a.at)))
      .slice(0, MAX_CANDIDATES);
}

/**
 * Builds dialog candidates from the conversation, note and document lists.
 * @param {!Array<!Object>} conversations
 * @param {!Array<!Object>} notes
 * @param {!Array<!Object>} documents
 * @return {!Array<!Candidate>}
 */
function toCandidates(conversations, notes, documents) {
  const pick = (kind, item, at) => ({
    kind,
    id: item.id,
    title: item.title,
    notebook_id: item.notebook_id,
    at,
  });
  return [
    ...conversations.map(
        (item) => pick('recording', item, item.recorded_at || item.created_at)),
    ...notes.map((item) => pick('note', item, item.updated_at)),
    ...documents.map(
        (item) => pick('document', item, item.record_date || item.created_at)),
  ];
}

/** The dialog for one notebook. It removes itself when closed. */
class AddSourceDialog {
  /**
   * @param {{book: {id: string}, count: number, limit: number,
   *     trigger: ?Element, onRecord: (function()|undefined),
   *     onChanged: (function()|undefined)}} options
   */
  constructor(options) {
    this.dom = root.StellisDom;
    this.options = options;
    this.book = options.book;
    this.total = options.count;
    /** @type {!Array<!Candidate>} */
    this.items = [];
    /** @type {!Map<string, string>} */
    this.notebookNames = new Map();
    this.loaded = Promise.resolve();
    this.element = /** @type {!HTMLDialogElement} */ (
        this.dom.el('dialog', null, 'nb-add'));
    this.element.setAttribute('aria-labelledby', 'nb-add-title');
    this.element.append(
        this.buildHead(), ...this.buildUpload(), this.buildActions(),
        this.buildPaste(), this.buildRecords(), this.buildStatus());
    this.element.addEventListener('close', () => {
      this.element.remove();
      if (options.trigger?.isConnected) {
        options.trigger.focus();
      }
    });
  }

  /** @return {!Element} */
  buildHead() {
    const dom = this.dom;
    const title = dom.el('h2', 'Add source');
    title.id = 'nb-add-title';
    this.counter = dom.el('span', null, 'nb-add-count');
    const close = dom.button('×', 'nb-add-close');
    close.setAttribute('aria-label', 'Close');
    close.addEventListener('click', () => this.element.close());
    const head = dom.el('div', null, 'nb-add-head');
    head.append(title, this.counter, close);
    return head;
  }

  /** @return {!Array<!Element>} The drop zone, file input and progress. */
  buildUpload() {
    const dom = this.dom;
    this.drop = dom.el('div', null, 'nb-add-drop');
    this.drop.tabIndex = 0;
    this.drop.setAttribute('role', 'button');
    this.drop.append(
        dom.icon('upload'),
        dom.el('strong', 'Drop audio, PDF or image files here'),
        dom.el('span', 'Choose files'));
    this.picker = /** @type {!HTMLInputElement} */ (dom.el('input'));
    this.picker.type = 'file';
    this.picker.multiple = true;
    this.picker.accept = root.StellisRecordUpload.ACCEPT;
    this.picker.hidden = true;
    this.progress = dom.el('ul', null, 'nb-add-progress');

    this.drop.addEventListener('click', () => this.choose());
    this.drop.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        this.choose();
      }
    });
    this.picker.addEventListener('change', () => {
      this.upload([...this.picker.files]);
      this.picker.value = '';
    });
    for (const name of ['dragenter', 'dragover']) {
      this.drop.addEventListener(name, (event) => {
        event.preventDefault();
        this.drop.classList.add('is-over');
      });
    }
    for (const name of ['dragleave', 'drop']) {
      this.drop.addEventListener(name, (event) => {
        event.preventDefault();
        this.drop.classList.remove('is-over');
      });
    }
    this.drop.addEventListener(
        'drop', (event) => this.upload([...event.dataTransfer.files]));
    return [this.drop, this.picker, this.progress];
  }

  /** @return {!Element} The Record and Paste text buttons. */
  buildActions() {
    const dom = this.dom;
    this.record = dom.button();
    this.record.append(dom.icon('microphone'), dom.el('span', 'Record'));
    this.record.addEventListener('click', () => {
      this.element.close();
      this.options.onRecord?.();
    });
    this.pasteToggle = dom.button();
    this.pasteToggle.append(
        dom.icon('file-text'), dom.el('span', 'Paste text'));
    this.pasteToggle.setAttribute('aria-expanded', 'false');
    this.pasteToggle.addEventListener('click', () => this.showPaste(true));
    const actions = dom.el('div', null, 'nb-add-actions');
    actions.append(this.record, this.pasteToggle);
    return actions;
  }

  /** @return {!Element} The Paste text form. */
  buildPaste() {
    const dom = this.dom;
    this.paste = dom.el('form', null, 'nb-add-paste');
    this.paste.hidden = true;
    this.pasteTitle = /** @type {!HTMLInputElement} */ (dom.el('input'));
    this.pasteTitle.maxLength = 180;
    this.pasteTitle.required = true;
    this.pasteText = /** @type {!HTMLTextAreaElement} */ (dom.el('textarea'));
    this.pasteText.required = true;
    this.pasteText.rows = 6;
    this.pasteSave = dom.el('button', 'Add text');
    this.pasteSave.type = 'submit';
    const titleLabel = dom.el('label');
    titleLabel.append(dom.el('span', 'Title'), this.pasteTitle);
    const textLabel = dom.el('label');
    textLabel.append(dom.el('span', 'Text'), this.pasteText);
    this.paste.append(titleLabel, textLabel, this.pasteSave);
    this.paste.addEventListener('submit', (event) => {
      event.preventDefault();
      this.savePaste();
    });
    return this.paste;
  }

  /** @return {!Element} The From your records section. */
  buildRecords() {
    const dom = this.dom;
    const section = dom.el('section', null, 'nb-add-records');
    const heading = dom.el('h3', 'From your records');
    heading.id = 'nb-add-records-title';
    section.setAttribute('aria-labelledby', heading.id);
    this.filter = /** @type {!HTMLInputElement} */ (dom.el('input'));
    this.filter.type = 'search';
    this.filter.placeholder = 'Filter by title';
    this.filter.setAttribute('aria-label', 'Filter records by title');
    this.filter.addEventListener('input', () => this.paintRecords());
    this.list = dom.el('ul', null, 'nb-add-list');
    section.append(heading, this.filter, this.list);
    return section;
  }

  /** @return {!Element} */
  buildStatus() {
    this.status = this.dom.el('p', null, 'nb-status');
    this.status.setAttribute('role', 'status');
    return this.status;
  }

  /** @return {boolean} */
  isFull() {
    return this.total >= this.options.limit;
  }

  paintCount() {
    const full = this.isFull();
    this.counter.textContent = `${this.total} of ${this.options.limit} sources`;
    const controls = [
      this.drop, this.record, this.pasteSave,
      ...this.list.querySelectorAll('button')
    ];
    for (const control of controls) {
      control.toggleAttribute('disabled', full);
    }
    this.drop.setAttribute('aria-disabled', String(full));
    if (full) {
      this.status.textContent =
          `This notebook is full at ${this.options.limit} sources`;
    }
  }

  async added() {
    this.total += 1;
    this.paintCount();
    await this.options.onChanged?.();
  }

  choose() {
    if (!this.isFull()) {
      this.picker.click();
    }
  }

  /** @param {boolean} visible */
  showPaste(visible) {
    this.paste.hidden = !visible;
    this.pasteToggle.setAttribute('aria-expanded', String(visible));
    if (visible) {
      this.pasteTitle.focus();
    }
  }

  async savePaste() {
    const title = this.pasteTitle.value.trim();
    this.pasteSave.disabled = true;
    try {
      await this.dom.api('/pages', {
        method: 'POST',
        json: {
          title,
          notebook_id: this.book.id,
          markdown: this.pasteText.value,
        },
      });
      this.status.textContent = `Added ${title}`;
      this.paste.reset();
      this.showPaste(false);
      await this.added();
    } catch (error) {
      this.status.textContent = error.message;
    } finally {
      this.pasteSave.disabled = this.isFull();
    }
  }

  /**
   * Files a record the account already has into this notebook.
   * @param {string} kind "recording", "note" or "document".
   * @param {string} id
   * @return {!Promise<void>}
   */
  async move(kind, id) {
    const api = this.dom.api;
    const prefix = {recording:'conversation',note:'page',document:'document'}[kind];
    await api(`/notebooks/${encodeURIComponent(this.book.id)}/links`, {
      method:'POST', json:{source_id:prefix+':'+id}
    });
  }

  /**
   * Uploads files one at a time with a status line for each.
   * @param {!Array<!File>} files
   * @return {!Promise<void>}
   */
  async upload(files) {
    await this.loaded;
    for (const file of files) {
      const line = this.dom.el('li', `${file.name}: uploading`);
      this.progress.append(line);
      line.textContent = `${file.name}: ${await this.uploadOne(file)}`;
    }
  }

  /**
   * Uploads one file and files it in this notebook.
   * @param {!File} file
   * @return {!Promise<string>} The outcome to show beside the file name.
   */
  async uploadOne(file) {
    if (this.isFull()) {
      return 'this notebook is full';
    }
    const upload = root.StellisRecordUpload;
    const result = await upload.uploadFile(file, this.book.id);
    if (result.outcome === 'added') {
      await this.added();
    }
    if (result.outcome !== 'duplicate') {
      return result.message;
    }
    const notebookId = result.record.notebook_id;
    if (notebookId === this.book.id) {
      return 'already in this notebook';
    }
    if (notebookId) {
      const name = this.notebookNames.get(notebookId);
      return `already in ${name || 'another notebook'}`;
    }
    // A file without a notebook moves here, since nothing else holds it.
    try {
      await this.move(
          result.kind === 'audio' ? 'recording' : 'document', result.record.id);
      await this.added();
      return 'added';
    } catch (error) {
      return error.message;
    }
  }


  paintRecords() {
    const dom = this.dom;
    const rows = candidates(this.items, this.book.id, this.filter.value);
    this.list.replaceChildren(...rows.map((item) => this.recordRow(item)));
    if (!rows.length) {
      const empty = this.filter.value ? 'No records match this filter' :
                                        'Every record is already here';
      this.list.append(dom.el('li', empty, 'nb-empty'));
    }
  }

  /**
   * @param {!Candidate} item
   * @return {!Element}
   */
  recordRow(item) {
    const dom = this.dom;
    const where = item.notebook_id ?
        `In ${this.notebookNames.get(item.notebook_id) || 'another notebook'}` :
        'No notebook';
    const text = dom.el('span', null, 'nb-add-row-text');
    text.append(
        dom.el('span', item.title, 'nb-add-row-title'), dom.el('small', where));
    const add = dom.button('Add');
    add.setAttribute('aria-label', `Add ${item.title}`);
    add.disabled = this.isFull();
    const row = dom.el('li', null, 'nb-add-row');
    row.append(dom.icon(RECORD_ICONS[item.kind]), text, add);
    add.addEventListener('click', async () => {
      add.disabled = true;
      try {
        await this.move(item.kind, item.id);
        item.notebook_id = this.book.id;
        row.remove();
        this.status.textContent = `Added ${item.title}`;
        await this.added();
      } catch (error) {
        this.status.textContent = error.message;
        add.disabled = this.isFull();
      }
    });
    return row;
  }

  /** Shows the dialog and loads the records it can offer. */
  open() {
    document.body.append(this.element);
    this.paintCount();
    this.element.showModal();
    const api = this.dom.api;
    this.loaded =
        Promise
            .all([
              api('/conversations'), api('/pages'), api('/documents'),
              api('/notebooks')
            ])
            .then(([conversations, notes, documents, books]) => {
              this.notebookNames =
                  new Map(books.notebooks.map((item) => [item.id, item.name]));
              const linked = new Set(this.options.sourceIds || []);
              const prefixes={recording:'conversation',note:'page',document:'document'};
              this.items = toCandidates(conversations, notes, documents).filter(item=>!linked.has(prefixes[item.kind]+':'+item.id));
              this.paintRecords();
              this.paintCount();
            })
            .catch((error) => {
              this.status.textContent = error.message;
            });
  }
}

/** @type {?AddSourceDialog} */
let current = null;

/**
 * Opens the dialog for a notebook, closing any dialog already open.
 * @param {{book: {id: string}, count: number, limit: number,
 *     trigger: ?Element, onRecord: (function()|undefined),
 *     onChanged: (function()|undefined)}} options
 */
function open(options) {
  current?.element.remove();
  current = new AddSourceDialog(options);
  current.open();
}

const exported = {
  open,
  candidates,
  toCandidates
};
root.StellisAddSource = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
