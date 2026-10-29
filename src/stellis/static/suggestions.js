// Agent suggestions beside a note (ADR 0059, #270). Suggestions never sit inside the editor's document: a column of
// cards stands beside it, pending to-dos appear as editable green lines below it, and a status change to an existing
// line is a highlight on that line. The server writes the accepted change; the host reloads the notes afterwards.
(function (root) {
  const AGENTS = {'transcript-agent': 'Transcript Agent'};
  const STATUS_VERB = {open: 'Reopen', done: 'Mark done', canceled: 'Cancel'};
  const squash = value => String(value || '').replace(/\s+/g, ' ').trim();

  function agentName(author) {
    if (AGENTS[author]) return AGENTS[author];
    return squash(String(author || 'Agent').replace(/[-_]+/g, ' ')).replace(/^./, letter => letter.toUpperCase());
  }

  // The text of the task line that carries `todoId` in a Markdown document, or null.
  function todoTextFor(markdown, todoId) {
    if (!todoId) return null;
    for (const line of String(markdown || '').split('\n')) {
      const match = /^\s*[-*+]\s+\[[ xX-]\]\s+(.*?)\s+\^([0-9a-z]{6,7})\s*$/.exec(line);
      if (match && match[2] === todoId) return squash(match[1]);
    }
    return null;
  }

  // The change in words: {verb, text}. `lineText` is the existing to-do's text when the suggestion names one.
  function describe(suggestion, lineText) {
    if (suggestion.operation === 'add_todo') return {verb: 'Add to-do', text: squash(suggestion.values?.text)};
    const verb = STATUS_VERB[suggestion.values?.status] || 'Change';
    return {verb, text: lineText || 'a to-do in this note'};
  }

  // Index of the line whose text is `target`: an exact match, else the single line that starts with or contains it.
  function matchLine(texts, target) {
    const wanted = squash(target).toLowerCase();
    if (!wanted) return -1;
    const lines = texts.map(text => squash(text).toLowerCase());
    const exact = lines.indexOf(wanted);
    if (exact >= 0) return exact;
    for (const test of [line => line.startsWith(wanted), line => line.includes(wanted)]) {
      const hits = lines.map((line, index) => test(line) ? index : -1).filter(index => index >= 0);
      if (hits.length === 1) return hits[0];
    }
    return -1;
  }

  function clockLabel(ms) {
    const total = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
  }

  // The evidence link's text: the segment's start time, or a plain "Open transcript" when the segment does not resolve.
  function evidenceLabel(segment) {
    return Number.isFinite(segment?.start_ms) ? clockLabel(segment.start_ms) : 'Open transcript';
  }

  function mount(options) {
    const {api = '/api/v1', icon, kind, panel, toolbar, saveLine, editorRoot, savedMarkdown, flush, reload,
      resolveSegment = () => null, openEvidence = () => {}, setStatus = () => {}} = options;
    const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; };
    const authorAvatar = author => root.StellisAgentAvatar.avatarElement(document, {seed: author || 'agent'});
    const iconButton = (name, label, className) => {
      const button = el('button', className); button.type = 'button'; button.setAttribute('aria-label', label); button.title = label;
      button.append(icon(name)); return button;
    };
    const request = async (path, init) => {
      const response = await fetch(`${api}${path}`, init);
      const data = await response.json().catch(() => ({}));
      if (response.status === 401) { location.replace('/auth'); throw new Error('Login required'); }
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The request failed.');
      return data;
    };
    const send = (method, path, body) => request(path, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

    // Wrap the editor, its toolbar and save line in one column; the cards stand beside it.
    const layout = el('div', 'notes-suggest-layout');
    const main = el('div', 'notes-main');
    const aside = el('aside', 'suggestions-column'); aside.hidden = true; aside.setAttribute('aria-label', 'Suggestions');
    toolbar.before(layout);
    for (let node = toolbar; node;) { const next = node === saveLine ? null : node.nextElementSibling; main.append(node); node = next; }
    const agentStatus = el('p', 'agent-status'); agentStatus.setAttribute('role', 'status'); agentStatus.hidden = true;
    agentStatus.append(icon('sparkles'), el('span', ''));
    const lines = el('section', 'suggested-todos'); lines.hidden = true; lines.setAttribute('aria-label', 'Suggested to-dos');
    main.prepend(agentStatus); main.append(lines);
    layout.append(main, aside);

    const head = el('div', 'suggestions-head');
    const count = el('h3', 'suggestions-count'); count.setAttribute('aria-live', 'polite');
    const previous = iconButton('chevron-up', 'Previous suggestion', 'suggestion-nav');
    const next = iconButton('chevron-down', 'Next suggestion', 'suggestion-nav');
    const acceptAll = iconButton('checks', 'Accept all suggestions', 'suggestion-bulk suggestion-accept');
    const rejectAll = iconButton('x', 'Reject all suggestions', 'suggestion-bulk suggestion-reject');
    head.append(count, previous, next, acceptAll, rejectAll);
    const note = el('p', 'suggestions-note'); note.setAttribute('role', 'status');
    const cards = el('div', 'suggestion-cards');
    aside.append(head, note, cards);

    let items = [];
    let token = 0;
    let cursor = -1;
    let pollTimer = null;
    let busy = false;
    const views = new Map(); // suggestion ID -> {card, words, input, pending}
    let pollToken = 0;

    const say = message => { note.textContent = message; };
    const lineTextOf = suggestion => todoTextFor(savedMarkdown(), suggestion.todo_id);

    function paintWords(suggestion, words) {
      const {verb, text} = describe(suggestion, lineTextOf(suggestion));
      words.replaceChildren(el('strong', '', `${verb}: `), document.createTextNode(text));
    }

    function evidenceNode(suggestion, evidence) {
      const quote = el('blockquote', 'suggestion-quote');
      quote.append(el('p', '', evidence.quote));
      const segment = resolveSegment(evidence.segment_id, suggestion);
      const link = el('button', 'suggestion-time'); link.type = 'button';
      const label = evidenceLabel(segment);
      link.append(icon('clock'), el('span', '', label));
      link.setAttribute('aria-label', label === 'Open transcript' ? 'Open the transcript' : `Open the transcript at ${label}`);
      link.addEventListener('click', () => openEvidence(suggestion, evidence, segment));
      quote.append(link);
      return quote;
    }

    function cardFor(suggestion) {
      const card = el('article', 'suggestion-card'); card.tabIndex = -1; card.dataset.suggestionId = suggestion.id;
      const who = el('div', 'suggestion-author');
      who.append(authorAvatar(suggestion.author), el('span', 'agent-name', agentName(suggestion.author)));
      const words = el('p', 'suggestion-words'); paintWords(suggestion, words);
      const actions = el('div', 'suggestion-actions');
      const accept = iconButton('check', 'Accept suggestion', 'suggestion-accept');
      const reject = iconButton('x', 'Reject suggestion', 'suggestion-reject');
      accept.addEventListener('click', () => decide([suggestion.id], 'accept'));
      reject.addEventListener('click', () => decide([suggestion.id], 'reject'));
      actions.append(accept, reject);
      card.append(who, words, ...suggestion.evidence.map(item => evidenceNode(suggestion, item)), actions);
      return {card, words};
    }

    // One green line per pending add_todo. Typing amends the suggestion (PATCH) on a pause or on blur.
    function lineFor(suggestion, view) {
      const row = el('li', 'suggested-line');
      const input = el('input', 'suggested-line-input'); input.type = 'text'; input.maxLength = 500; input.autocomplete = 'off';
      input.value = suggestion.values.text; input.setAttribute('aria-label', 'Suggested to-do, editable before you accept it');
      let timer = null;
      const save = () => {
        clearTimeout(timer);
        const text = squash(input.value);
        if (text === suggestion.values.text) return view.pending;
        if (!text) { input.value = suggestion.values.text; return view.pending; }
        view.pending = (view.pending || Promise.resolve()).then(async () => {
          try {
            const saved = await send('PATCH', `/suggestions/${encodeURIComponent(suggestion.id)}`, {values: {...suggestion.values, text}});
            suggestion.values = saved.values; suggestion.original_values = saved.original_values; suggestion.amended_at = saved.amended_at;
            paintWords(suggestion, view.words); say('');
          } catch (error) { say(error.message); setStatus(error.message); }
        });
        return view.pending;
      };
      input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(save, 700); });
      input.addEventListener('blur', save);
      input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); input.blur(); } });
      row.append(icon('circle'), input);
      return row;
    }

    function highlight() {
      const lineElements = [...editorRoot.querySelectorAll('li.note-check-open, li.note-check-done, li.note-check-canceled')];
      for (const line of editorRoot.querySelectorAll('.suggested-change')) line.classList.remove('suggested-change');
      const texts = lineElements.map(line => line.textContent);
      for (const suggestion of items) {
        if (suggestion.operation !== 'set_todo_status') continue;
        const index = matchLine(texts, lineTextOf(suggestion));
        if (index < 0) continue;
        lineElements[index].classList.add('suggested-change');
        lineElements[index].title = `${agentName(suggestion.author)} suggests: ${describe(suggestion, '').verb.toLowerCase()}`;
      }
    }

    function render() {
      views.clear(); cards.replaceChildren(); lines.replaceChildren();
      const total = items.length;
      aside.hidden = total === 0;
      panel.classList.toggle('has-suggestions', total > 0);
      count.textContent = total === 1 ? '1 suggestion' : `${total} suggestions`;
      const adds = items.filter(item => item.operation === 'add_todo');
      lines.hidden = adds.length === 0;
      let list = null;
      if (adds.length) {
        const label = el('h3', 'suggested-todos-label'); label.append(icon('sparkles'), el('span', '', 'Suggested to-dos'));
        list = el('ul', 'suggested-lines'); lines.append(label, list);
      }
      for (const suggestion of items) {
        const view = {}; views.set(suggestion.id, view);
        const {card, words} = cardFor(suggestion); view.card = card; view.words = words; cards.append(card);
        if (suggestion.operation === 'add_todo') list.append(lineFor(suggestion, view));
      }
      cursor = -1;
      highlight();
    }

    async function refresh() {
      const mine = ++token;
      // The page clears its ID when the visitor leaves; a load still in flight then has nothing to ask about.
      if (!options.id()) return;
      const data = await request(`/suggestions?target_kind=${kind}&target_id=${encodeURIComponent(options.id())}`);
      if (mine !== token) return;
      items = data.suggestions.filter(item => item.state === 'pending');
      render();
    }

    async function decide(ids, decision) {
      if (busy || !ids.length) return;
      busy = true; aside.setAttribute('aria-busy', 'true');
      try {
        await Promise.all(ids.map(id => views.get(id)?.pending));
        if (decision === 'accept') await flush();
        const result = await send('POST', `/suggestions/${decision}`, {ids});
        if (decision === 'accept') {
          await reload();
          const skipped = result.superseded?.length || 0;
          say(skipped ? `${skipped} ${skipped === 1 ? 'suggestion' : 'suggestions'} no longer applied because the line changed.` : '');
        } else say('');
        await refresh();
      } catch (error) { say(error.message); setStatus(error.message); }
      finally { busy = false; aside.removeAttribute('aria-busy'); }
    }

    function step(delta) {
      if (!items.length) return;
      cursor = (cursor + delta + items.length) % items.length;
      const card = views.get(items[cursor].id)?.card;
      card?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
      card?.focus({preventScroll: true});
    }
    previous.addEventListener('click', () => step(-1));
    next.addEventListener('click', () => step(1));
    acceptAll.addEventListener('click', () => decide(items.map(item => item.id), 'accept'));
    rejectAll.addEventListener('click', () => decide(items.map(item => item.id), 'reject'));

    // A Transcript Agent run that is queued or running shows one quiet status line; every other state shows nothing.
    async function pollRuns(mine) {
      clearTimeout(pollTimer);
      if (kind !== 'conversation' || !options.id()) return;
      let runs = [];
      try { runs = await request(`/conversations/${encodeURIComponent(options.id())}/agent-runs`); } catch { runs = []; }
      if (mine !== pollToken) return;
      const active = Array.isArray(runs) && runs.some(run => run.state === 'queued' || run.state === 'running');
      agentStatus.hidden = !active;
      agentStatus.lastChild.textContent = active ? 'Transcript Agent is reading this meeting' : '';
      if (active) pollTimer = setTimeout(async () => {
        await pollRuns(pollToken);
        if (agentStatus.hidden) refresh().catch(() => {});
      }, 5000);
    }
    // Keep the highlight on the right line while the editor re-renders.
    const observer = new MutationObserver(() => requestAnimationFrame(highlight));
    observer.observe(editorRoot, {childList: true, subtree: true, characterData: true});

    return {
      async load() {
        const mine = ++pollToken;
        say(''); agentStatus.hidden = true;
        try { await refresh(); } catch (error) { items = []; render(); }
        pollRuns(mine);
      },
      // Re-read after the notes were reloaded, so lines and highlights follow the saved text.
      repaint() { highlight(); },
      clear() { pollToken += 1; token += 1; clearTimeout(pollTimer); items = []; render(); agentStatus.hidden = true; say(''); },
      get count() { return items.length; },
    };
  }

  const api = {agentName, todoTextFor, describe, matchLine, evidenceLabel, clockLabel, mount};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.StellisSuggestions = api;
})(typeof window !== 'undefined' ? window : globalThis);
