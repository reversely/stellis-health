/**
 * @fileoverview Records page: drop every health file in one place, then sort
 * the unsorted ones into notebooks from a reviewed proposal or one by one.
 */
(function(root) {
'use strict';

const ICONS = {
  document: 'file-text',
  recording: 'microphone'
};

/**
 * Returns the app link that opens a record.
 * @param {{kind: string, id: string}} item
 * @return {string}
 */
function recordLink(item) {
  const path = item.kind === 'recording' ? 'conversation' : 'document';
  return `/${path}/${encodeURIComponent(item.id)}`;
}

/**
 * Lists the records of a group the person kept checked.
 * @param {!Array<{kind: string, id: string}>} items
 * @param {!Set<string>} unchecked Keys of the form "kind:id".
 * @return {!Array<{kind: string, id: string}>}
 */
function keptItems(items, unchecked) {
  return items.filter((item) => !unchecked.has(`${item.kind}:${item.id}`))
      .map((item) => ({kind: item.kind, id: item.id}));
}

/** The Records page. */
class RecordsPage {
  /**
   * @param {!Element} host
   * @param {function(): !Promise<void>} onChanged Refreshes the rail.
   */
  constructor(host, onChanged) {
    const dom = root.StellisDom;
    this.dom = dom;
    this.onChanged = onChanged;
    /** @type {!Array<{id: string, name: string}>} */
    this.notebooks = [];
    this.items = [];
    /** Keys ("kind:id") of records shown in an open proposal. */
    this.proposed = new Set();

    const head = dom.el('div', null, 'records-head');
    head.append(dom.el('h2', 'Records'));
    this.summary = dom.el('p', null, 'records-summary');

    this.drop = dom.el('div', null, 'records-drop');
    this.drop.tabIndex = 0;
    this.drop.setAttribute('role', 'button');
    this.drop.append(
        dom.icon('upload'), dom.el('strong', 'Drop all your health files here'),
        dom.el('span', 'Choose files'));
    this.picker = /** @type {!HTMLInputElement} */ (dom.el('input'));
    this.picker.type = 'file';
    this.picker.multiple = true;
    this.picker.accept = root.StellisRecordUpload.ACCEPT;
    this.picker.hidden = true;
    this.progress = dom.el('ul', null, 'records-progress');
    this.wireDrop();

    this.sort = dom.button('Sort into notebooks', 'records-sort');
    this.sort.addEventListener('click', () => this.suggest());
    // One press files every proposed notebook, as each card's own File button would.
    this.fileAll = dom.button('File all', 'coach-button is-primary records-file-all');
    this.fileAll.hidden = true;
    this.fileAll.addEventListener('click', async () => {
      this.fileAll.disabled = true;
      const cards = [...this.proposal.querySelectorAll('.records-group')]
          .filter((card) => card.querySelector('.records-file:not(:disabled)'));
      for (const [index, card] of cards.entries()) {
        this.status.textContent = `Filing ${index + 1} of ${cards.length} notebooks`;
        await card.apply();
      }
      this.status.textContent = `Filed in ${dom.plural(cards.length, 'notebook')}`;
      this.fileAll.hidden = true;
      this.fileAll.disabled = false;
    });
    this.status = dom.el('p', null, 'records-status');
    this.status.setAttribute('role', 'status');
    this.proposal = dom.el('div', null, 'records-proposal');
    this.review = dom.el('section', null, 'records-review');
    this.review.hidden = true;

    const listHead = this.listHead = dom.el('h3', 'Not sorted');
    listHead.id = 'records-unsorted-title';
    this.list = dom.el('ul', null, 'records-list');
    this.list.setAttribute('aria-labelledby', listHead.id);
    const actions = dom.el('div', null, 'records-actions');
    actions.append(this.summary, this.sort, this.fileAll);
    const examples = root.StellisRecordUpload.exampleChoices(
        true, (files) => this.upload(files));
    host.replaceChildren(
        head, this.drop, examples, this.picker,
        this.progress, this.review, actions, this.status, this.proposal, listHead,
        this.list);
  }

  wireDrop() {
    const open = () => this.picker.click();
    this.drop.addEventListener('click', open);
    this.drop.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        open();
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
  }

  /**
   * Uploads files into no notebook, one at a time.
   * @param {!Array<!File>} files
   * @return {!Promise<void>}
   */
  async upload(files) {
    this.progress.replaceChildren();
    this.review.hidden = true;
    let added = 0;
    const documents = [];
    for (const [index, file] of files.entries()) {
      this.status.textContent = `Uploading ${index + 1} of ${files.length}`;
      const line = this.dom.el('li', `${file.name}: uploading`);
      this.progress.append(line);
      const result = await root.StellisRecordUpload.uploadFile(file, null);
      line.textContent = `${file.name}: ${result.message}`;
      added += result.outcome === 'added' ? 1 : 0;
      if (result.kind === 'document' && result.record) {
        documents.push(result.record.id);
      }
    }
    this.status.textContent =
        `${this.dom.plural(added, 'record')} added from ${files.length}`;
    // A batch that all went in collapses to its count; lines stay only for files that did not.
    for (const line of [...this.progress.children]) {
      if (/: (added|already uploaded)$/.test(line.textContent)) line.remove();
    }
    await this.load();
    if (documents.length) {
      await this.reviewRead(documents);
    }
  }

  /**
   * Shows what Stellis read from newly added documents: each one's date with the line it came from,
   * how its text was read, and its results with their rows. Approve all confirms those dates and
   * saves those results; nothing is saved before.
   * @param {!Array<string>} ids
   * @return {!Promise<void>}
   */
  async reviewRead(ids) {
    const dom = this.dom;
    this.status.textContent = `Reading ${dom.plural(ids.length, 'record')}`;
    const read = [];
    for (const [index, id] of ids.entries()) {
      this.status.textContent = `Reading ${index + 1} of ${ids.length}`;
      const path = `/documents/${encodeURIComponent(id)}`;
      const [record, staged] = await Promise.all([dom.api(path), dom.api(`${path}/facts`)]);
      const suggestion = record.date_suggestion || {};
      read.push({
        record,
        date: !record.record_date && suggestion.status === 'suggested' ? suggestion.candidates?.[0] : null,
        facts: staged.facts.filter((fact) => fact.status === 'suggested'),
      });
    }
    const results = read.reduce((sum, item) => sum + item.facts.length, 0);
    const dates = read.filter((item) => item.date).length;
    this.status.textContent = '';
    if (!results && !dates) {
      return;
    }
    const day = (value) => new Date(`${value}T12:00:00`).toLocaleDateString(
        [], {day: 'numeric', month: 'short', year: 'numeric'});
    const list = dom.el('ul', null, 'records-review-list');
    for (const item of read) {
      const row = dom.el('li');
      const name = dom.el('a', item.record.title);
      name.href = `/document/${encodeURIComponent(item.record.id)}`;
      const how = item.record.pages.some((page) => page.text_source === 'ocr') ?
          'text recognised on the server' : 'the PDF’s own text';
      const lines = [`${dom.plural(item.record.page_count, 'page')}, read from ${how}.`];
      if (item.date) {
        lines.push(`Dated ${day(item.date.date)} from “${item.date.quote}”, page ${item.date.page}.`);
      }
      if (item.facts.length) {
        lines.push(item.facts.map((fact) => `${fact.name} ${fact.value}${fact.unit ? ' ' + fact.unit : ''}` +
            (fact.range_status?.label && fact.range_status.label !== 'In range' ? ` (${fact.range_status.label.toLowerCase()})` : '')).join(', ') + '.');
      }
      row.append(name, ...lines.map((line) => dom.el('p', line)));
      list.append(row);
    }
    const approve = dom.button(`Approve all`, 'coach-button is-primary');
    const note = dom.el('p', null, 'records-review-note');
    note.setAttribute('role', 'status');
    approve.addEventListener('click', async () => {
      approve.disabled = true;
      try {
        for (const [index, item] of read.entries()) {
          note.textContent = `Saving ${index + 1} of ${read.length}`;
          const path = `/documents/${encodeURIComponent(item.record.id)}`;
          if (item.date) {
            await dom.api(path, {method: 'PATCH', json: {record_date: item.date.date}});
          }
          if (item.facts.length) {
            await dom.api(`${path}/facts/accept`, {method: 'POST'});
          }
        }
        const home = dom.el('a', 'Open My Health', 'coach-button is-quiet');
        home.href = '/';
        this.review.replaceChildren(dom.el('h3', 'Saved'),
            dom.el('p', `${dom.plural(results, 'result')} and ${dom.plural(dates, 'date')} saved. ` +
                'The results are on My Health and in Measurements, each linked to its row.'), home);
        await this.load();
        await this.onChanged();
      } catch (error) {
        note.textContent = error.message;
        approve.disabled = false;
      }
    });
    this.review.replaceChildren(
        dom.el('h3', 'What Stellis read'),
        dom.el('p', `${dom.plural(results, 'result')} and ${dom.plural(dates, 'date')} from ` +
            `${dom.plural(read.length, 'record')}. Nothing is saved until you approve.`),
        list, approve, note);
    this.review.hidden = false;
  }

  /** Asks for a sorting proposal and shows it for review. */
  async suggest() {
    this.sort.disabled = true;
    this.status.textContent =
        `Reading ${this.dom.plural(this.items.length, 'record')}`;
    try {
      const result =
          await this.dom.api('/records/sort/suggest', {method: 'POST'});
      this.proposed = new Set(result.groups.flatMap(
          (group) => group.items.map((item) => `${item.kind}:${item.id}`)));
      this.proposal.replaceChildren(
          ...result.groups.map((group) => this.groupCard(group)));
      this.paintList();
      this.status.textContent = result.groups.length ?
          `${this.dom.plural(result.groups.length, 'notebook')} proposed. Check them below, then file each or all.` :
          'No notebooks proposed';
      this.fileAll.hidden = result.groups.length < 2;
      // While a proposal is open its File buttons are the next step, not another sort.
      this.sort.hidden = result.groups.length > 0;
    } catch (error) {
      this.status.textContent = error.message;
    } finally {
      this.sort.disabled = false;
    }
  }

  /**
   * Renders one proposed notebook with its records for review.
   * @param {!Object} group
   * @return {!Element}
   */
  groupCard(group) {
    const dom = this.dom;
    const card = dom.el('section', null, 'records-group');
    const name = /** @type {!HTMLInputElement} */ (dom.el('input'));
    name.value = group.notebook_name;
    name.maxLength = 80;
    name.readOnly = true;
    name.setAttribute('aria-label', 'Notebook name');
    const where = group.collection_name ? ` in ${group.collection_name}` : '';
    const tag = dom.el(
        'span', (group.notebook_id ? 'Existing notebook' : 'New notebook') + where,
        'records-tag');
    const head = dom.el('div', null, 'records-group-head');
    head.append(name, tag);
    const unchecked = new Set();
    const file = dom.button(
        `File ${dom.plural(group.items.length, 'record')}`, 'records-file');
    const list = dom.el('ul', null, 'records-group-list');
    for (const item of group.items) {
      const box = /** @type {!HTMLInputElement} */ (dom.el('input'));
      box.type = 'checkbox';
      box.checked = true;
      box.addEventListener('change', () => {
        const key = `${item.kind}:${item.id}`;
        if (box.checked) {
          unchecked.delete(key);
        } else {
          unchecked.add(key);
        }
        file.textContent =
            `File ${dom.plural(group.items.length - unchecked.size, 'record')}`;
      });
      const label = dom.el('label', null, 'records-group-item');
      label.append(
          box, dom.icon(ICONS[item.kind]), dom.el('span', item.title),
          dom.el('time', item.date));
      const row = dom.el('li');
      row.append(label);
      list.append(row);
    }
    const skip = dom.button('Skip');
    skip.addEventListener('click', () => {
      for (const item of group.items) {
        this.proposed.delete(`${item.kind}:${item.id}`);
      }
      card.remove();
      this.paintList();
    });
    card.apply = () => {
      const items = keptItems(group.items, unchecked);
      const renamed = name.value.trim() !== group.notebook_name;
      return this.applyGroup(card, file, {
        notebook_name: name.value.trim(),
        notebook_id: renamed ? null : group.notebook_id,
        collection_id: group.collection_id || null,
        items,
      });
    };
    file.addEventListener('click', () => card.apply?.());
    const actions = dom.el('div', null, 'records-group-actions');
    actions.append(file, skip);
    card.append(head);
    if (group.reason) {
      card.append(dom.el('p', group.reason, 'records-reason'));
    }
    card.append(list, actions);
    return card;
  }

  /**
   * Files one reviewed group and reports the result in its card.
   * @param {!Element} card
   * @param {!HTMLButtonElement} control
   * @param {!Object} request
   * @return {!Promise<void>}
   */
  async applyGroup(card, control, request) {
    control.disabled = true;
    try {
      const result = await this.dom.api(
          '/records/sort/apply', {method: 'POST', json: request});
      const note = result.failed.length ?
          `${result.failed.length} could not move: ${result.failed[0].error}` :
          '';
      const done = `Filed ${this.dom.plural(result.moved, 'record')} in ${
          request.notebook_name}`;
      card.replaceChildren(
          this.dom.el('p', note ? `${done}. ${note}` : done, 'records-done'));
      await this.load();
      await this.onChanged();
    } catch (error) {
      this.status.textContent = error.message;
      control.disabled = false;
    }
  }

  /**
   * Renders one unsorted record with a menu to move it.
   * @param {!Object} item
   * @return {!Element}
   */
  row(item) {
    const dom = this.dom;
    const link = /** @type {!HTMLAnchorElement} */ (
        dom.el('a', item.title, 'records-title'));
    link.href = recordLink(item);
    const move = /** @type {!HTMLSelectElement} */ (dom.el('select'));
    move.setAttribute('aria-label', `Move ${item.title} to a notebook`);
    move.append(
        new Option('Move to', ''),
        ...this.notebooks.map((book) => new Option(book.name, book.id)));
    move.addEventListener('change', async () => {
      if (!move.value) {
        return;
      }
      move.disabled = true;
      try {
        await this.dom.api(
            `/records/${item.kind}/${encodeURIComponent(item.id)}/notebook`,
            {method: 'PUT', json: {notebook_id: move.value}});
        await this.load();
        await this.onChanged();
      } catch (error) {
        this.status.textContent = error.message;
        move.disabled = false;
        move.value = '';
      }
    });
    const row = dom.el('li', null, 'records-row');
    row.append(
        dom.icon(ICONS[item.kind]), link,
        dom.el('time', item.date, 'records-date'), move);
    return row;
  }

  /** Lists unsorted records that no open proposal shows. */
  paintList() {
    const left = this.items.filter(
        (item) => !this.proposed.has(`${item.kind}:${item.id}`));
    this.list.replaceChildren(...left.map((item) => this.row(item)));
    // Nothing left to sort: no heading over an empty list (owner, 2026-10-28).
    this.listHead.hidden = this.list.hidden = !left.length;
  }

  /** Reloads the unsorted records and the notebook list. */
  async load() {
    const [unsorted, books] = await Promise.all(
        [this.dom.api('/records/unsorted'), this.dom.api('/notebooks')]);
    this.items = unsorted.items.slice().reverse();
    this.notebooks = books.notebooks;
    this.paintList();
    this.summary.textContent = this.items.length ?
        `${this.dom.plural(this.items.length, 'record')} not sorted` : '';
    this.summary.hidden = !this.items.length;
    this.sort.hidden = !this.items.length;
  }
}

/**
 * Shows the Records page in host.
 * @param {!Element} host
 * @param {function(): !Promise<void>} onChanged
 * @return {!Promise<void>}
 */
function mount(host, onChanged) {
  return new RecordsPage(host, onChanged).load();
}

const exported = {
  mount,
  recordLink,
  keptItems
};
root.StellisRecords = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
