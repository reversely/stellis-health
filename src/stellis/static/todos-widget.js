// The To-dos widget: one component for Home, the To-dos page, and (later) a notebook page.
// `scope.notebookId` is 'all' (grouped by notebook), 'none' (uncategorized), or one notebook ID (flat).
// Writing goes to a note titled "To-dos" in the scope's notebook; to-dos are task lines in notes (ADR 0059).
(function (root) {
  const NOTE_TITLE = 'To-dos';
  const STATUSES = [
    {id: 'open', label: 'Open', icon: 'circle', empty: ''},
    {id: 'done', label: 'Completed', icon: 'circle-check', empty: ''},
    {id: 'canceled', label: 'Canceled', icon: 'circle-x', empty: ''},
  ];

  // Group the API's per-source groups by notebook. A specific notebook scope stays flat (one group, no header).
  function groupTodos(sourceGroups, notebooks, scope = 'all') {
    const byId = new Map(notebooks.map(book => [book.id, book]));
    const flat = scope !== 'all';
    const buckets = new Map();
    for (const {source, items} of sourceGroups) {
      const book = byId.get(source.notebook_id) || null;
      const key = flat ? '' : (book ? book.id : '');
      if (!buckets.has(key)) buckets.set(key, {notebook: flat ? null : book, items: []});
      buckets.get(key).items.push(...items.map(item => ({...item, source})));
    }
    return [...buckets.values()];
  }

  // Append one open task line to a note's Markdown. A task list after other text needs a blank line first.
  function appendTodoMarkdown(markdown, text) {
    const line = `- [ ] ${String(text).replace(/\s+/g, ' ').trim()}`;
    const body = String(markdown || '').replace(/\s+$/, '');
    if (!body) return `${line}\n`;
    const last = body.split('\n').pop();
    const joined = /^\s*[-*+]\s+\[[ xX-]\]\s/.test(last) ? '\n' : '\n\n';
    return `${body}${joined}${line}\n`;
  }

  // The date shown at the right of a row, and what it means (the row's title attribute).
  function rowDate(item) {
    if (item.status === 'done' && item.completed_at) return {value: item.completed_at, label: 'Completed'};
    if (item.status === 'canceled' && item.canceled_at) return {value: item.canceled_at, label: 'Canceled'};
    if (item.due_date) return {value: item.due_date, label: 'Due'};
    return null;
  }

  function mount(host, options) {
    const {api = '/api/v1', icon, setStatus = () => {}, formatListDate, notebooks, title = 'Check-ins',
      linkTitle = false, onChange = () => {}} = options;
    const scope = options.scope?.notebookId || 'all';
    let filter = 'open';
    let token = 0;
    const request = async (path, init) => {
      const response = await fetch(`${api}${path}`, init);
      const data = await response.json().catch(() => ({}));
      if (response.status === 401) { location.replace('/auth'); throw new Error('Login required'); }
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The request failed.');
      return data;
    };
    const send = (method, path, body) => request(path, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; };

    host.replaceChildren();
    host.classList.add('list-widget', 'todos-widget');
    const head = el('div', 'list-widget-head');
    const heading = el('h2'); heading.append(icon('circle-check'));
    if (linkTitle) { const link = el('a', '', title); link.href = '/todos'; heading.append(link); } else heading.append(el('span', '', title));
    const filters = el('div', 'todo-filters'); filters.setAttribute('role', 'group'); filters.setAttribute('aria-label', 'Show check-ins');
    const filterButtons = STATUSES.map(item => {
      const button = el('button', 'todo-filter'); button.type = 'button'; button.dataset.status = item.id;
      button.append(icon(item.icon), el('span', '', item.label));
      button.addEventListener('click', () => { filter = item.id; paintFilters(); load(); });
      return button;
    });
    filters.append(...filterButtons);
    head.append(heading, filters);

    const form = el('form', 'todo-add');
    const label = el('label', 'visually-hidden', 'New check-in'); const input = el('input'); input.id = `todo-input-${Math.random().toString(36).slice(2, 8)}`;
    label.htmlFor = input.id; input.type = 'text'; input.maxLength = 500; input.autocomplete = 'off'; input.placeholder = 'Add a check-in'; input.required = true;
    const add = el('button', 'invert-button todo-add-button'); add.type = 'submit'; add.append(icon('plus'), el('span', '', 'Add'));
    form.append(label, input, add);

    const note = el('p', 'todo-note'); note.setAttribute('role', 'status'); note.setAttribute('aria-live', 'polite');
    const body = el('div', 'todo-body');
    host.append(head, form, note, body);

    function paintFilters() {
      for (const button of filterButtons) button.setAttribute('aria-pressed', String(button.dataset.status === filter));
    }
    paintFilters();

    function say(message) { note.textContent = message; }

    function rowFor(item, grouped) {
      const row = el('li', 'list-widget-row todo-row'); row.dataset.status = item.status;
      if (item.pending_suggestions?.length) row.classList.add('has-suggestion');
      const check = el('label', 'todo-check'); const box = el('input'); box.type = 'checkbox';
      box.checked = item.status === 'done'; box.indeterminate = item.status === 'canceled';
      box.setAttribute('aria-label', `${item.status === 'open' ? 'Complete' : 'Reopen'} ${item.text}`);
      check.append(box);
      box.addEventListener('change', async () => {
        const next = item.status === 'open' ? 'done' : 'open';
        // The box shows the tick at once and keeps its pointer while it saves; a second press waits (#258).
        if (box.getAttribute('aria-busy') === 'true') { box.checked = next === 'done'; return; }
        box.setAttribute('aria-busy', 'true');
        try {
          await send('PATCH', `/todos/${encodeURIComponent(item.id)}`, {status: next});
          say(next === 'done' ? `Completed: ${item.text}` : `Reopened: ${item.text}`);
          row.classList.add('leaving');
          setTimeout(() => { removeRow(row); }, 160);
          onChange();
        } catch (error) {
          box.checked = item.status === 'done'; box.indeterminate = item.status === 'canceled'; box.removeAttribute('aria-busy');
          say(error.message); setStatus(error.message);
        }
      });
      const text = el('span', 'list-widget-text');
      text.append(el('span', 'todo-text', item.text));
      const source = item.source;
      const sourceLabel = source.kind === 'conversation' ? el('a', 'todo-source', source.title) : el('span', 'todo-source', source.title);
      if (source.kind === 'conversation') sourceLabel.href = `/conversation/${encodeURIComponent(source.id)}`;
      text.append(sourceLabel);
      if (item.pending_suggestions?.length) {
        const count = item.pending_suggestions.length;
        // The label opens the note or meeting that holds the suggestion's cards.
        const mark = el('a', 'todo-suggestion'); mark.title = 'An agent suggested a change to this check-in';
        mark.href = `/${source.kind === 'conversation' ? 'conversation' : 'page'}/${encodeURIComponent(source.id)}`;
        mark.append(icon('sparkles'), el('span', '', count === 1 ? 'Suggested change' : `${count} suggested changes`));
        text.append(mark);
      }
      row.append(check, text);
      const date = rowDate(item);
      if (date) {
        const time = el('time', 'list-widget-date', formatListDate(date.value)); time.title = `${date.label} ${formatListDate(date.value, {format: 'iso'})}`;
        time.dateTime = String(date.value).slice(0, 10); row.append(time);
      }
      return row;
    }
    function removeRow(row) {
      const list = row.parentElement; row.remove();
      if (list && !list.children.length) {
        const section = list.closest('.todo-group'); (section || list).remove();
        if (!body.querySelector('.todo-row')) paintEmpty();
      }
    }
    function paintEmpty() {
      // The add field above already invites the first one; an empty list shows no hint line.
      const empty = STATUSES.find(item => item.id === filter).empty;
      body.replaceChildren(...(empty ? [el('p', 'todo-empty', empty)] : []));
      if (filter === 'open') hideFiltersIfNone();
    }
    // With no check-in in any status the Open/Completed/Canceled filters are empty items (owner, 2026-10-28).
    async function hideFiltersIfNone() {
      const counts = await Promise.all(['done', 'canceled'].map(status =>
        request(`/todos?${new URLSearchParams({status, notebook_id: scope})}`).then(({groups}) => groups.some(group => group.items.length), () => true)));
      filters.hidden = filter === 'open' && !body.querySelector('.todo-row') && counts.every(count => !count);
    }

    async function load() {
      const mine = ++token;
      const query = new URLSearchParams({status: filter, notebook_id: scope});
      try {
        const [{groups}, books] = await Promise.all([request(`/todos?${query}`), Promise.resolve(notebooks ? notebooks() : [])]);
        if (mine !== token) return;
        const sections = groupTodos(groups, books, scope);
        if (!sections.length) { paintEmpty(); return; }
        filters.hidden = false;
        body.replaceChildren(...sections.map(({notebook, items}) => {
          const section = el('section', 'todo-group');
          if (scope === 'all') {
            const h = el('h3', 'todo-group-head');
            if (notebook) {
              const dot = el('span', 'recent-dot'); dot.setAttribute('aria-hidden', 'true'); dot.style.setProperty('--dot', notebook.color);
              const link = el('a', '', notebook.name); link.href = `/notebook/${encodeURIComponent(notebook.id)}`; h.append(dot, link);
            } else h.append(icon('inbox'), el('span', '', 'No notebook'));
            section.append(h);
          }
          const rows = el('ul', 'list-widget-rows'); rows.append(...items.map(item => rowFor(item)));
          section.append(rows); return section;
        }));
      } catch (error) {
        if (mine === token) body.replaceChildren(Object.assign(el('p', 'todo-empty', error.message), {role: 'alert'}));
      }
    }

    // The scope's "To-dos" note: found by title in the notebook (or uncategorized), created with the first line.
    async function addTodo(text) {
      const notebookId = scope === 'all' || scope === 'none' ? null : scope;
      const pages = await request(`/pages?notebook_id=${encodeURIComponent(notebookId || 'none')}`);
      const existing = pages.find(page => page.title.trim().toLowerCase() === NOTE_TITLE.toLowerCase());
      if (!existing) {
        await send('POST', '/pages', {title: NOTE_TITLE, ...(notebookId ? {notebook_id: notebookId} : {}), markdown: appendTodoMarkdown('', text)});
        return;
      }
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const page = await request(`/pages/${encodeURIComponent(existing.id)}`);
        try {
          await send('PUT', `/pages/${encodeURIComponent(existing.id)}/markdown`, {markdown: appendTodoMarkdown(page.markdown, text), expected_revision: page.revision});
          return;
        } catch (error) { if (attempt) throw error; }
      }
    }
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const text = input.value.replace(/\s+/g, ' ').trim();
      if (!text) return;
      input.disabled = add.disabled = true;
      try {
        await addTodo(text);
        input.value = ''; filter = 'open'; paintFilters(); say(`Added: ${text}`);
        await load(); onChange();
      } catch (error) { say(error.message); setStatus(error.message); }
      finally { input.disabled = add.disabled = false; input.focus(); }
    });

    load();
    return {refresh: load, destroy() { token += 1; host.replaceChildren(); host.classList.remove('list-widget', 'todos-widget'); }};
  }

  const api = {mount, groupTodos, appendTodoMarkdown, rowDate, NOTE_TITLE};
  root.StellisTodos = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
