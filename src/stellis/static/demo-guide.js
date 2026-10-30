/**
 * @fileoverview The demo walkthrough (#247): for a demo guest only, a tour docked beside the real app, a
 * sidebar on wide windows and a bottom panel otherwise, that the page makes room for. Nothing dims the
 * page and nothing floats over it (owner, 2026-10-28): the step's control is outlined in place and
 * scrolls with the page. Each step names where the visitor is (the pillar roadmap, a title and one
 * sentence) with Back and Next; some wait for the visitor to press the control itself.
 */
(() => {
  const STEP_KEY = 'stellis-demo-step';
  // A minimized tour stays a small pill across pages until the visitor opens it again (#258).
  const SMALL_KEY = 'stellis-demo-small';
  // The context slide opens the demo once, before any step names Maya (owner, 2026-10-28).
  const MET_KEY = 'stellis-demo-met';
  // One tour (owner, 2026-10-30: one click, not a short and a full one): Maya's five years already filed,
  // one question, one source, one new report that changes her history, one call, one follow-through.
  const ANEMIA_KEY = 'stellis-demo-anemia';

  // Maya's 16 by 16 portrait, the same pixels as the film's.
  const MAYA_ROWS = ['................', '.....HHHHHH.....', '...HHHHHHHHHH...', '..HHHhhHHHHHHH..', '..HHHHHFFFFHHH..',
    '.HHHHFFFFFFFHHH.', '.HHHFFFFFFFFFHH.', '.HHFFEFFFFEFFHH.', '.HHFFEFFFFEFFHH.', '.HHFBFFFFFFBFHH.', '.HHFFFFMMFFFFHH.',
    '.HHHFFFFFFFFHHH.', '.HHHHfFFFFfHHHH.', '.HHH..fNNf..HHH.', '..HSSSSSSSSSSH..', '..SSSSSSSSSSSS..'];
  const MAYA_TONES = {H: '#3A2A33', h: '#5A4350', F: '#EFC6A3', f: '#DDAA86', N: '#E6B892', E: '#2E2228', B: '#E9A99A', M: '#C27B72', S: '#547980'};
  function mayaPortrait() {
    const disc = document.createElement('span'); disc.className = 'demo-maya'; disc.setAttribute('aria-hidden', 'true');
    const paths = Object.entries(MAYA_TONES).map(([tone, fill]) => `<path fill="${fill}" d="${MAYA_ROWS.flatMap((row, y) =>
      [...row].map((c, x) => (c === tone ? `M${x} ${y}h1v1h-1z` : ''))).join('')}"/>`).join('');
    disc.innerHTML = `<svg viewBox="-1 -1 18 18" shape-rendering="crispEdges">${paths}</svg>`;
    return disc;
  }
  const PILLARS = [['understand', 'Understand'], ['log', 'Log'], ['chart', 'Chart'], ['monitor', 'Monitor']];

  /**
   * The walk, on the app's real routes, one point per beat (owner review, 2026-10-30): personal problem,
   * reasoning across history, verifiable execution, new evidence, controlled update, a call that acts,
   * useful follow-through. `path` may name {added}, the record the visitor uploaded, or {anemia}.
   * `target` is a selector, or [selector, text] for the first match containing that text. A `wait` step
   * moves on when the visitor presses the target; an `until` step moves on once that selector appears.
   * On those steps › presses the control for the visitor (owner, 2026-10-28); on a `say` step › types
   * the sentence into the target and sends it. `receipt` lists what the server did with the uploaded file.
   */
  const STEPS = [
    {path: '/', target: '.home-results', pillar: 'chart', title: 'Five years of results',
      text: "Five years of Maya's care, 70 synthetic records, each result in a different report. Stellis reads them together."},
    {path: '/notebook/{anemia}', target: '.chat-idea-action[data-idea*="changed since"]', pillar: 'understand', wait: true,
      title: "Reasoning across Maya's history", text: 'This question needs every dated ferritin result brought together and explained. Nemotron on Nebius Token Factory reads Maya\'s Anemia Management records and answers with a source for each value.'},
    {path: '/notebook/{anemia}', target: '.nb-msg-actions .trace-link', pillar: 'understand', wait: true, patience: 180000,
      title: 'How Stellis answered', text: 'Every answer keeps a log of how it was made. A first answer can take a minute or two while the model reads five years of reports.'},
    {target: 'dialog.answer-trace .trace-tiles', pillar: 'understand', title: 'What ran, on what',
      text: "The model that answered, where it ran and how many passages it read, from this request's own log. Maya's records stay encrypted in Maya's account; only the passages for this question went to Token Factory, with names replaced, and Token Factory keeps nothing."},
    {path: '/notebook/{anemia}', close: true, target: '.nb-cite:not(.nb-cite-public)', pillar: 'understand', wait: true,
      title: 'Check a claim', text: 'Every value in the answer links to the report it came from.'},
    {target: '.report-paper', pillar: 'understand', title: 'The original report',
      text: "The cited result on the lab's own page, so the answer can be checked line by line."},
    {path: '/', target: '#global-add', opens: 'upload', notebook: ANEMIA_KEY, pillar: 'understand', wait: true, title: 'New evidence',
      text: "A March 2032 blood test from outside the 70 records in Maya's account. The next steps show how it enters that history."},
    {target: ['.health-capture .upload-examples .coach-button', 'March 2032'], pillar: 'understand', until: '.report-paper', waits: 'Uploading and reading the report',
      title: 'Iron studies, March 2032', text: 'Choosing the file uploads it, and Stellis reads and summarizes the report.'},
    {path: '/document/{added}', target: ['.report-found .coach-button', 'Add to Measurements'], pillar: 'chart', wait: true,
      title: 'A proposed update', text: 'Stellis proposes each result with its date and the row it came from, including ferritin 52 ug/L on March 24, 2032. Nothing joins Maya\'s history until Maya approves it.'},
    {path: '/', target: '.home-results', pillar: 'chart', title: 'One history',
      text: "The approved result joins the same history: ferritin now ends at 52, with December's 48 and every earlier value kept."},
    // The question is pressed, then the step holds until its answer is written, so the next card
    // never describes an answer still loading.
    {path: '/document/{added}', target: ['.report-idea', 'next appointment'], pillar: 'understand', patience: 180000, waits: 'Stellis is writing the answer',
      until: ['.report-conversation article.report-exchange:not(.is-pending)', 'next appointment'],
      title: "Ready for Maya's appointment", text: "Ask what to raise at the next visit. Stellis answers from five years of results, each with its source."},
    // The answer itself is its own step, so the tour never ends on a question still being written.
    {path: '/document/{added}', target: ['.report-conversation article.report-exchange:not(.is-pending)', 'next appointment'], pillar: 'understand', patience: 180000,
      title: 'Questions for the visit', text: "Questions for Maya's clinician, each linked to the report it came from, so Maya does not have to assemble them."},
    // The call (owner, 2026-10-30): the visitor asks Stellis by voice or text, and confirms on screen.
    {path: '/', target: '#dashboard-call', pillar: 'log', wait: true, title: 'Call Stellis',
      text: 'Stellis takes calls. Talk or type, and Stellis acts on the page while you confirm each change.'},
    {target: '.coach-call-foot input', say: 'Add a goal to walk 30 minutes three times a week', pillar: 'log',
      until: ['#home-goal-draft button', 'Add goal'], patience: 60000, waits: 'Stellis is filling in the goal', title: 'Ask for a goal',
      text: 'Say or type: “Add a goal to walk 30 minutes three times a week.” Stellis fills in the goal form.'},
    {target: ['#home-goal-draft button', 'Add goal'], pillar: 'log', wait: true, title: 'Add the goal',
      text: 'Nothing is saved until you check the form and add it.'},
    {target: '.coach-call-foot input', say: 'I walked for 30 minutes this morning', pillar: 'log',
      until: ['button', 'Log selected'], patience: 60000, waits: 'Stellis is putting the entry on screen', title: 'Log a walk',
      text: 'Say or type: “I walked for 30 minutes this morning.” Stellis puts the entry on screen with its date.'},
    {target: ['button', 'Log selected'], pillar: 'log', wait: true, title: 'Confirm the entry',
      text: 'The walk counts toward the goal once you log it.'},
    {path: '/trends', target: ['button', 'Log vital signs'], pillar: 'monitor', wait: true, title: 'Vital signs',
      text: 'Pulse and breathing from 30 seconds of camera video that stays on your device'},
    {kind: 'end', title: 'Your records', pillar: null},
  ];

  // Maya's files, offered as example files in the real Add form and on the Records page.
  async function offerExamples() {
    const response = await fetch('/api/v1/demo/files').catch(() => null);
    if (!response || !response.ok || !window.StellisRecordUpload) return;
    const followups = (await response.json()).followups || [];
    window.StellisRecordUpload.examples.splice(0, Infinity, {label: 'Iron studies, March 2032', files: followups.filter(file => file.name.startsWith('D071_'))});
    window.dispatchEvent(new Event('stellis-upload-examples'));
  }
  const remember = (key, value) => { try { sessionStorage.setItem(key, value); } catch {} };
  const recall = key => { try { return sessionStorage.getItem(key) || ''; } catch { return ''; } };
  // An upload opens its own page; the report the visitor added is the one later steps return to.
  window.addEventListener('health-capture-saved', event => { if (event.detail?.kind === 'document') remember('stellis-demo-added', event.detail.id); });

  /** What the server did with the file the visitor just uploaded, from its own record and results. */
  async function receipt() {
    const id = recall('stellis-demo-added');
    if (!id) return null;
    const path = `/api/v1/documents/${encodeURIComponent(id)}`;
    const [record, staged] = await Promise.all([fetch(path).then(r => r.json()), fetch(`${path}/facts`).then(r => r.json())]);
    const date = record.date_suggestion?.candidates?.[0];
    const facts = staged.facts.filter(fact => fact.status === 'suggested');
    const how = record.pages.some(page => page.text_source === 'ocr') ? 'text recognised (OCR) on the server' : "the PDF's own text layer, so no OCR was needed";
    const list = el('ol', null, 'demo-receipt');
    const day = value => new Date(`${value}T12:00:00`).toLocaleDateString([], {day: 'numeric', month: 'long', year: 'numeric'});
    for (const line of [
      `Uploaded ${record.filename}, ${Math.max(1, Math.round(record.size / 1024))} KB, ${record.page_count} page${record.page_count === 1 ? '' : 's'}.`,
      `Read the page from ${how}.`,
      `Named it “${record.title}”.`,
      date ? `Found the date ${day(date.date)} in “${date.quote}”, page ${date.page}.` : 'Found no date on the page; you can add one.',
      `Found ${facts.length} result${facts.length === 1 ? '' : 's'}, each from a row on the page: ${facts.map(fact => `${fact.name} ${fact.value} ${fact.unit || ''}`.trim()).join(', ')}.`,
    ]) list.append(el('li', line));
    return list;
  }

  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text) n.textContent = text; if (cls) n.className = cls; return n; };
  const stepIndex = () => { try { return Number(sessionStorage.getItem(STEP_KEY) || 0); } catch { return 0; } };
  const setStep = i => { try { sessionStorage.setItem(STEP_KEY, String(i)); } catch {} };

  /** The route for a step, with the uploaded record filled in, or null when the step stays put. */
  const route = step => step.path ? step.path.replace('{added}', encodeURIComponent(recall('stellis-demo-added'))).replace('{anemia}', encodeURIComponent(recall(ANEMIA_KEY))) : null;
  const here = path => { const url = new URL(path, location.origin); return url.pathname === location.pathname && url.search === location.search; };

  function find(target) {
    // A page still routing is hidden (html[data-routing]); its controls exist but the visitor can't see them.
    if (!target || document.documentElement.hasAttribute('data-routing')) return null;
    const [selector, text] = Array.isArray(target) ? target : [target, null];
    const all = [...document.querySelectorAll(selector)].filter(n => n.getClientRects().length);
    return (text ? all.find(n => n.textContent.includes(text)) : all[0]) || null;
  }
  /** Whether a press on `node` is a press on the target, even if the page drew the target again since. */
  function hits(target, node) {
    const [selector, text] = Array.isArray(target) ? target : [target, null];
    const control = node instanceof Element ? node.closest(selector) : null;
    return !!control && (!text || control.textContent.includes(text));
  }
  /** The target once it has rendered: up to 8 seconds, or a step's own `patience` (an answer being written). */
  async function waitFor(target, ms = 8000) {
    for (let i = 0; i < ms / 100; i++) { const node = find(target); if (node) return node; await new Promise(r => setTimeout(r, 100)); }
    return null;
  }

  function roadmap(pillar) {
    const row = el('div', null, 'demo-roadmap');
    const now = PILLARS.findIndex(([id]) => id === pillar);
    PILLARS.forEach(([id, name], i) => {
      const chip = el('span', name, `demo-pillar demo-pillar-${id}`);
      if (i === now) chip.classList.add('is-current'); else if (now > -1 && i < now) chip.classList.add('is-done');
      row.append(chip);
    });
    return row;
  }

  const layer = el('aside', null, 'demo-guide'); layer.setAttribute('aria-label', 'Demo tour');
  const card = el('section', null, 'demo-card'); card.setAttribute('aria-live', 'polite');
  layer.append(card);
  const root = document.documentElement;
  // The page keeps room for the panel: its height, when it runs along the bottom, pads the page.
  const sheet = matchMedia('(max-width: 1179px)');
  const fit = () => root.style.setProperty('--demo-sheet', sheet.matches && layer.isConnected && !layer.hidden ? `${layer.offsetHeight}px` : '0px');
  new ResizeObserver(fit).observe(layer);
  sheet.addEventListener('change', fit);
  function dock(on) {
    root.classList.toggle('has-demo-panel', on);
    if (on && !layer.isConnected) document.body.append(layer);
    if (!on) layer.remove();
    fit();
  }
  // A modal dialog makes the rest of the page inert, the panel included, so while one is open the panel
  // lives inside it (still fixed in the same place) and its buttons keep working. A dialog that removes
  // itself on closing takes the panel with it; the next check puts the panel back on the page.
  setInterval(() => {
    if (!root.classList.contains('has-demo-panel')) return;
    const host = document.querySelector('dialog:modal') || document.body;
    if (layer.parentElement !== host) host.append(layer);
  }, 150);

  function avatar() {
    return window.StellisAgentAvatar ? window.StellisAgentAvatar.avatarElement(document, {seed: 'stellis', size: 36}) : el('span');
  }

  /**
   * Outlines the step's control in place, so the mark scrolls with it. A small control the step asks for
   * keeps its outline; a large region shows one briefly. The page is never scrolled for the visitor: a
   * page opens at its top (owner, 2026-10-28), and a control out of view gets a Show me in the panel.
   */
  let marked = null, fade = 0;
  function mark(node) {
    clearTimeout(fade);
    if (marked) marked.classList.remove('demo-target', 'is-brief', 'is-inset');
    document.querySelectorAll('.demo-quiet').forEach(other => other.classList.remove('demo-quiet'));
    marked = node;
    if (!node) return;
    const box = node.getBoundingClientRect();
    const small = node.matches('button, a') ? box.height < 120 : box.height < innerHeight * 0.35 && box.width < innerWidth * 0.6;
    if (small) quiet(node);
    node.classList.add('demo-target');
    if (ringCut(node, box)) node.classList.add('is-inset');
    if (!small) { node.classList.add('is-brief'); fade = setTimeout(() => mark(null), 2600); }
  }
  /** True when a clipping ancestor would cut a ring drawn 7px outside the node's edge. */
  function ringCut(node, box) {
    for (let a = node.parentElement; a && a !== document.body; a = a.parentElement) {
      const style = getComputedStyle(a);
      if (style.overflowX === 'visible' && style.overflowY === 'visible') continue;
      const c = a.getBoundingClientRect(), l = c.left + a.clientLeft, t = c.top + a.clientTop;
      if (box.left - 7 < l || box.top - 7 < t || box.right + 7 > l + a.clientWidth || box.bottom + 7 > t + a.clientHeight) return true;
    }
    return false;
  }
  /**
   * The step's control is the one filled button where it sits (owner, 2026-10-29: three filled buttons
   * left no clear next press); others filled the same way show as outlines until the step moves on.
   * Run again while the step is open, for buttons drawn after it began.
   */
  function quiet(node) {
    if (!node.matches('button, a')) return;
    // Read before the node is marked, whose own fill changes (and transitions) as it becomes the target.
    const fill = getComputedStyle(node).backgroundColor;
    // A transparent control is not filled, so nothing matches it (2026-10-29: in dark mode every
    // transparent link and button gained an outline).
    if (/^rgba\(.*,\s*0\)$|^transparent$/.test(fill)) return;
    const scope = node.closest('dialog') || document.querySelector('main') || document.body;
    for (const other of scope.querySelectorAll('button:not(.demo-quiet), a:not(.demo-quiet)')) {
      // A fill that shows where you are (the current page, a selected tab) is state, not a competing action.
      const state = other.matches('[aria-current], [aria-selected="true"], [aria-pressed="true"]');
      if (other === node || state || layer.contains(other) || !other.getClientRects().length || getComputedStyle(other).backgroundColor !== fill) continue;
      // A split button stays one control: both halves quiet together (2026-10-29: Record was half light, half dark).
      const split = other.closest('.split-button');
      for (const part of split && !split.contains(node) ? split.querySelectorAll('button, summary') : [other]) part.classList.add('demo-quiet');
    }
  }
  /** Whether the visitor can see the control now: its top inside the window and not under the tour panel. */
  function inView(node) {
    const box = node.getBoundingClientRect(), panel = layer.getBoundingClientRect();
    const head = {top: box.top, bottom: Math.min(box.bottom, box.top + 48)};
    const covered = box.left < panel.right && box.right > panel.left && head.top < panel.bottom && head.bottom > panel.top;
    return box.top >= 0 && head.bottom <= innerHeight && !covered;
  }
  /** A quiet Show me beside the step's buttons; it scrolls only when pressed, then goes away. */
  function showMe(node) {
    const row = card.querySelector('.demo-actions');
    if (!row || inView(node)) return;
    const show = el('button', 'Show me', 'coach-button is-quiet demo-show'); show.type = 'button';
    show.onclick = () => { node.scrollIntoView({block: 'center', behavior: 'smooth'}); show.remove(); mark(node); };
    row.prepend(show);
  }

  /** A button that is only a universal symbol, named for screen readers and on hover. */
  function symbol(icon, name, cls = 'demo-icon') {
    const button = el('button', null, cls); button.type = 'button';
    button.setAttribute('aria-label', name); button.title = name;
    button.append(window.StellisDom ? window.StellisDom.icon(icon) : el('span', name));
    return button;
  }

  /**
   * Back and Next as ‹ and ›: the arrows every carousel uses. On a step that asks for a press, › presses
   * the control itself and waits for the step to move on, so nothing is skipped.
   */
  function buttons(index, {next = true} = {}) {
    const row = el('div', null, 'demo-actions');
    if (index > 0) { const back = symbol('chevron-left', 'Back'); back.onclick = () => go(index - 1); row.append(back); }
    if (next) {
      const forward = symbol('chevron-right', 'Next', 'demo-icon is-primary');
      forward.onclick = async () => {
        const step = STEPS[index];
        forward.disabled = true; forward.setAttribute('aria-busy', 'true');
        // A control still on its way (an answer being written) is waited for, not skipped.
        const control = (step.wait || step.until) && (find(step.target) || await waitFor(step.target, step.patience));
        // A say step's own sentence stays usable if the answer never comes.
        if (step.say) setTimeout(() => { forward.disabled = false; forward.removeAttribute('aria-busy'); }, 60000);
        if (stepIndex() !== index) return;
        if (!control) { go(index + 1); return; }
        if (step.say) { control.value = step.say; control.form?.requestSubmit(); return; }
        control.click();
        // A press › made itself moves the tour on, even when the page redrew the control as it was pressed.
        if (step.wait && stepIndex() === index) { setStep(index + 1); setTimeout(() => { if (stepIndex() === index + 1) go(index + 1); }, 600); }
        // A press that changes nothing (a server that refuses, say) leaves › usable again.
        setTimeout(() => { forward.disabled = false; forward.removeAttribute('aria-busy'); }, 8000);
      };
      row.append(forward);
    }
    return row;
  }

  /** Minimize (–) and leave (×), top right of every card. */
  function windowControls(index) {
    const controls = el('div', null, 'demo-window');
    const fold = symbol('minus', 'Minimize tour'); fold.onclick = () => minimize(index);
    const leave = symbol('x', 'Leave demo'); leave.onclick = leaveDemo;
    controls.append(fold, leave);
    return controls;
  }

  const small = () => { try { return sessionStorage.getItem(SMALL_KEY) === '1'; } catch { return false; } };
  const setSmall = on => { try { on ? sessionStorage.setItem(SMALL_KEY, '1') : sessionStorage.removeItem(SMALL_KEY); } catch {} };

  /**
   * The tour folded where it was, at the bottom left of the page beside the sidebar: the step count,
   * expand (or ▶ when paused) and ×. Nothing moves across the screen.
   */
  function pill(index, paused) {
    mark(null);
    card.classList.add('is-pill');
    root.classList.add('demo-is-small');
    card.replaceChildren();
    const open = symbol(paused ? 'player-play' : 'arrows-diagonal', paused ? 'Resume tour' : 'Expand tour', 'demo-icon is-primary');
    open.onclick = () => { setSmall(false); card.classList.remove('is-pill'); go(index); };
    const leave = symbol('x', 'Leave demo'); leave.onclick = leaveDemo;
    card.append(avatar(), el('span', `${index + 1}/${STEPS.length - 1}`, 'demo-count'), open, leave);
  }
  function minimize(index) { setSmall(true); pill(index, false); }

  function stepCard(index, step) {
    card.replaceChildren();
    card.classList.remove('is-pill');
    root.classList.remove('demo-is-small');
    const head = el('div', null, 'demo-card-head');
    head.append(avatar(), el('span', `${index + 1}/${STEPS.length - 1}`, 'demo-count'), windowControls(index));
    card.append(roadmap(step.pillar), head, el('h2', step.title), el('p', step.text, 'demo-text'), buttons(index));
  }

  function endCard() {
    card.replaceChildren();
    card.classList.remove('is-pill');
    root.classList.remove('demo-is-small');
    const head = el('div', null, 'demo-card-head'); head.append(avatar());
    const own = el('button', 'Create your own account', 'coach-button is-primary'); own.type = 'button';
    own.onclick = () => leaveDemo('/auth?create=1');
    const stay = symbol('x', 'Close the tour'); stay.onclick = () => { setStep(-1); dock(false); };
    head.append(stay); stay.classList.add('demo-end-close');
    const row = el('div', null, 'demo-actions'); row.append(own);
    card.append(roadmap(null), head, el('h2', 'Your records'),
      el('p', 'Stellis reads your own reports and recordings the same way', 'demo-text'), el('p', 'Only you can read what you add', 'demo-text'), row);
  }

  /**
   * The context slide, before the first step: who Maya is, in the film's words and portrait, and what
   * the demo shows. Start the tour opens step 1; Explore on my own keeps the tour as a pill.
   */
  function contextSlide() {
    const slide = el('section', null, 'demo-context'); slide.setAttribute('role', 'dialog'); slide.setAttribute('aria-modal', 'true');
    slide.setAttribute('aria-labelledby', 'demo-context-title');
    const inner = el('div', null, 'demo-context-inner');
    const title = el('h1', 'Maya is 28, and about to face health issues and treatment for the first time.'); title.id = 'demo-context-title';
    // The narration's second sentence read as antagonistic (owner, 2026-10-28), so the slide says what
    // Maya has instead.
    const lines = el('p', "Maya has five years of lab reports, letters and visit recordings to keep track of.", 'demo-context-lede');
    // What Stellis is, in the narration's second line, with the four pillars the tour is organized by.
    const stellis = el('div', null, 'demo-context-stellis');
    const said = el('p', 'Stellis Health is for people like Maya: an AI agent that knows your history and helps you understand, log, monitor and chart your health.', 'demo-context-lede');
    const pillars = el('div', null, 'demo-roadmap demo-context-pillars');
    for (const [id, name] of [['understand', 'Understand'], ['log', 'Log'], ['monitor', 'Monitor'], ['chart', 'Chart']]) pillars.append(el('span', name, `demo-pillar demo-pillar-${id} is-current`));
    const words = el('div'); words.append(said, pillars);
    // One left edge for the whole slide: Maya's portrait is the slide's one image, so no avatar column.
    stellis.append(words);
    const what = el('p', "Maya is fictional. Her five years of records are already in Stellis. You'll ask about them, add one new report and call Stellis.", 'demo-context-what');
    const start = el('button', 'Start the tour', 'coach-button is-primary'); start.type = 'button';
    const explore = el('button', 'Explore on my own', 'coach-button is-quiet'); explore.type = 'button';
    const begin = async (button, tour) => {
      for (const other of [start, explore]) other.disabled = true;
      button.setAttribute('aria-busy', 'true');
      try { await startTour(tour, slide); } catch (error) { for (const other of [start, explore]) other.disabled = false; button.removeAttribute('aria-busy'); what.textContent = error.message; }
    };
    start.onclick = () => begin(start, true); explore.onclick = () => begin(explore, false);
    const row = el('div', null, 'demo-context-actions'); row.append(start, explore);
    inner.append(mayaPortrait(), title, lines, stellis, what, row);
    slide.append(inner);
    dock(false);
    document.body.append(slide);
    document.documentElement.classList.remove('demo-arriving');
    start.focus();
  }

  /**
   * Maya's records as they now stand in her account, oldest first, scrolling past under their years and
   * coming to rest on the newest. Read from the account, so it shows what was added, not a script.
   * Resolves when the list has come to rest.
   */
  function filing(slide, documents, books) {
    const names = new Map(books.map(book => [book.id, book.name]));
    const dated = documents.filter(doc => doc.record_date).sort((a, b) => a.record_date.localeCompare(b.record_date));
    if (!dated.length) return Promise.resolve();
    const years = [...new Set(dated.map(doc => doc.record_date.slice(0, 4)))];
    const panel = el('section', null, 'demo-filing'); panel.setAttribute('role', 'status');
    panel.append(el('h1', "Adding Maya's five years of records"),
      el('p', `${dated.length} records, ${years[0]} to ${years[years.length - 1]}, filed in ${books.length} notebooks`, 'demo-context-lede'));
    // Seventy rows are for the eye; the sentence above says what they are.
    const view = el('div', null, 'demo-filing-view'); view.setAttribute('aria-hidden', 'true');
    const list = el('ol', null, 'demo-filing-list');
    let year = '';
    for (const doc of dated) {
      if (doc.record_date.slice(0, 4) !== year) { year = doc.record_date.slice(0, 4); list.append(el('li', year, 'demo-filing-year')); }
      const row = el('li', null, 'demo-filing-row');
      const day = new Date(`${doc.record_date}T12:00:00`).toLocaleDateString([], {month: 'short', day: 'numeric'});
      const where = (doc.notebook_ids || []).map(id => names.get(id)).filter(Boolean).join(', ');
      row.append(el('time', day), el('span', doc.title, 'demo-filing-title'), el('span', where, 'demo-filing-book'));
      list.append(row);
    }
    view.append(list); panel.append(view);
    const inner = slide.querySelector('.demo-context-inner');
    inner.hidden = true; slide.append(panel);
    const travel = list.scrollHeight - view.clientHeight;
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    // A long scroll that eases in and settles on the newest record; held still under reduced motion.
    const done = still || travel <= 0
      ? new Promise(resolve => { list.style.transform = `translateY(${-Math.max(0, travel)}px)`; setTimeout(resolve, 1200); })
      : list.animate([{transform: 'translateY(0)'}, {transform: `translateY(${-travel}px)`}],
          {duration: 5200, easing: 'cubic-bezier(0.65, 0, 0.35, 1)', fill: 'forwards'}).finished.then(() => new Promise(r => setTimeout(r, 500)));
    return done;
  }

  /**
   * The tour starts from Maya's five years already filed (the snapshot), with her clock moved to April
   * 2032 so the March 2032 report it adds is part of her history, then reloads into its first step, or
   * folded to a pill for a visitor exploring on their own.
   */
  async function startTour(tour = true, slide = null) {
    const call = async (path, options) => { const response = await fetch(path, options); if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'The demo could not start. Try again.'); return response.json(); };
    await call('/api/v1/demo/seed', {method: 'POST'});
    await call('/api/v1/demo/story-date', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({date: '2032-04-01'})});
    const books = (await call('/api/v1/notebooks')).notebooks;
    const anemia = books.find(book => /anemia/i.test(book.name));
    // The records now in Maya's account pass by before the tour opens (owner, 2026-10-30).
    // Without the list the tour still opens.
    if (slide) await call('/api/v1/documents').then(documents => filing(slide, documents, books)).catch(() => {});
    try {
      sessionStorage.setItem(MET_KEY, '1'); sessionStorage.setItem(STEP_KEY, '0');
      if (anemia) sessionStorage.setItem(ANEMIA_KEY, anemia.id);
      if (tour) sessionStorage.removeItem(SMALL_KEY); else sessionStorage.setItem(SMALL_KEY, '1');
    } catch {}
    location.assign('/');
  }

  async function leaveDemo(next = '/auth') {
    try { sessionStorage.removeItem(STEP_KEY); } catch {}
    await fetch('/api/v1/account', {method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({confirm: 'DELETE'})}).catch(() => {});
    location.assign(typeof next === 'string' ? next : '/auth');
  }

  /** A control the visitor can press now: drawn, outside the tour, and not behind an open modal dialog. */
  function usable(node) {
    const modal = document.querySelector('dialog:modal');
    return !!node && node.getClientRects().length > 0 && !layer.contains(node) && (!modal || modal.contains(node));
  }
  /**
   * The visitor may go ahead by hand (owner, 2026-10-29: Record pressed directly left the tour on step 1
   * while the upload was done, so it named nothing on screen). When the step's control can't be pressed
   * and a later step's can, on the same page, the tour moves to that step, so it always names the press
   * that comes next. A step whose `until` has already appeared is done and passed over.
   */
  function catchUp() {
    const index = stepIndex(), step = STEPS[index];
    if (!step || step.kind === 'end' || card.classList.contains('is-pill') || !card.isConnected) return;
    if (usable(find(step.target)) && !(step.until && find(step.until))) return;
    // A control still loading is waited for: only one the visitor has seen, or one a dialog they opened
    // now covers, means they went ahead.
    if (reached !== index && !document.querySelector('dialog:modal') && !(step.until && find(step.until))) return;
    for (let j = index + 1; j < STEPS.length - 1 && j <= index + 8; j++) {
      const later = STEPS[j];
      if (later.path && !here(route(later))) return;
      if (later.until && find(later.until)) continue;
      if (usable(find(later.target))) { go(j); return; }
    }
  }

  /** While the visitor looks around on their own, the tour waits as a pill; Resume picks it up again. */
  function pause(index) { pill(index, true); }

  /**
   * A line on the card saying what the step waits for, with the seconds so far, until `done()` is true or
   * the tour moves on (owner, 2026-10-30: loading must be transparent). Returns a function that removes it.
   */
  function waitLine(index, label, done) {
    const started = Date.now();
    let line = null;
    const stop = () => { clearInterval(tick); line?.remove(); };
    const tick = setInterval(() => {
      if (stepIndex() !== index || done()) { stop(); return; }
      if (!line) { line = el('p', null, 'demo-wait'); line.setAttribute('role', 'status'); card.querySelector('.demo-text')?.after(line); }
      line.textContent = `${label} · ${Math.round((Date.now() - started) / 1000)}s`;
    }, 1000);
    return stop;
  }

  let shown = null, reached = -1;
  async function go(index) {
    setStep(index);
    const step = STEPS[index];
    const path = route(step);
    // The app changes pages in place, so the old page stays up until the new one is drawn; a full
    // load (where the app's router is absent) is the fallback.
    if (path && !here(path)) {
      // Whatever the last step opened (an answer's working, say) closes with the page it belongs to.
      document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
      if (!window.StellisNavigate) { location.assign(path); return; }
      shown = null;
      await window.StellisNavigate(path);
      if (stepIndex() !== index) return;
    }
    // A step that returns to the page from an open panel (the answer's log) closes it first.
    if (step.close) document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
    shown = path ? location.pathname + location.search : null;
    if (small()) { pill(index, false); return; }
    if (step.kind === 'end') { mark(null); endCard(); return; }
    stepCard(index, step);
    mark(null);
    // A step whose control is still on its way says what it is waiting for and for how long (owner,
    // 2026-10-30: loading must be transparent), from the first second, until the control appears.
    // A step drawn again after its press (its control gone while the result is made) keeps its own words.
    const stopLoading = waitLine(index, step.waits || (step.patience ? 'Stellis is writing the answer' : 'Loading the page'),
      () => find(step.target) || (step.until && find(step.until)));
    const node = await waitFor(step.target, step.patience);
    stopLoading();
    // The visitor may have moved on while this step waited; its card is not drawn again.
    if (stepIndex() !== index) return;
    // A step whose control never appears still offers › rather than stopping the tour.
    if (!node) { if (step.wait) stepCard(index, {...step, wait: false}); return; }
    reached = index;
    mark(node);
    // Inside a dialog the control is brought into the dialog's view (on a phone the tour's sheet leaves
    // the dialog little room); the page itself is never scrolled for the visitor.
    if (node.closest('dialog')) node.scrollIntoView({block: 'nearest'});
    showMe(node);
    if (step.receipt) receipt().then(list => { if (list && stepIndex() === index) card.querySelector('.demo-text').after(list); }).catch(() => {});
    // The next step is recorded at the press itself: a link may load its page before any timer runs.
    // The press is matched against the step's target as it is now: a page that re-renders (a report
    // drawing its rows) replaces the node found when the step began.
    // `opens` skips a menu: the press on the step's control opens that part of Add directly.
    if (step.opens) {
      const open = event => {
        if (stepIndex() !== index) { document.removeEventListener('click', open, true); return; }
        if (!hits(step.target, event.target)) return;
        event.preventDefault(); event.stopImmediatePropagation();
        document.removeEventListener('click', open, true);
        window.StellisCapture?.open(step.opens, step.notebook ? {notebookId: recall(step.notebook)} : {});
        setStep(index + 1);
        setTimeout(() => { if (stepIndex() === index + 1) go(index + 1); }, 600);
      };
      document.addEventListener('click', open, true);
    }
    if (step.wait && !step.opens) {
      const pressed = event => {
        if (stepIndex() !== index) { document.removeEventListener('click', pressed, true); return; }
        if (!hits(step.target, event.target)) return;
        document.removeEventListener('click', pressed, true);
        // Recorded at once, since a link may load another page; if this page stays, the timer moves on.
        setStep(index + 1);
        setTimeout(() => { if (stepIndex() === index + 1) go(index + 1); }, 600);
      };
      document.addEventListener('click', pressed, true);
    }
    // An `until` step moves on when what the press makes appears, never on a press that did nothing.
    // The outline follows a re-rendered target.
    const follow = setInterval(() => {
      if (stepIndex() !== index) { clearInterval(follow); return; }
      const now = find(step.target);
      if (now && now !== marked && marked !== null) mark(now);
      else if (marked && marked.classList.contains('demo-target') && !marked.classList.contains('is-brief')) {
        quiet(marked);
        // A dialog that shrank for the tour's sheet (a receipt makes it taller) keeps the control in view.
        if (marked.closest('dialog') && !inView(marked)) marked.scrollIntoView({block: 'nearest'});
        // Show me is only offered while the control is out of view.
        if (inView(marked)) card.querySelector('.demo-show')?.remove();
      }
    }, 500);
    if (step.until && step.waits) {
      const begin = event => {
        if (stepIndex() !== index) { document.removeEventListener('click', begin, true); document.removeEventListener('submit', begin, true); return; }
        if (event.type === 'click' ? !hits(step.target, event.target) : !event.target.matches?.('.coach-call-foot')) return;
        document.removeEventListener('click', begin, true); document.removeEventListener('submit', begin, true);
        waitLine(index, step.waits, () => !!find(step.until));
      };
      document.addEventListener('click', begin, true); document.addEventListener('submit', begin, true);
    }
    if (step.until) {
      const watch = setInterval(() => {
        if (stepIndex() !== index) { clearInterval(watch); return; }
        if (find(step.until)) { clearInterval(watch); go(index + 1); }
      }, 300);
    }
  }

  async function start() {
    const uncover = () => document.documentElement.classList.remove('demo-arriving');
    const status = await fetch('/api/v1/auth/status').then(r => r.json()).catch(() => null);
    // Not a demo guest after all (a stale tab): give back the room demo-early.js reserved.
    if (!status || !status.guest) { uncover(); root.classList.remove('has-demo-panel', 'demo-is-small'); }
    if (!status || !status.guest) return;
    // Demo guests add records through the real routes, with or without the tour.
    document.body.classList.add('is-demo-guest');
    // The logo takes a demo guest back to the main page, which offers a way back into the demo.
    document.querySelector('#app-header .brand')?.setAttribute('href', '/auth?home=1');
    offerExamples();
    const index = stepIndex();
    if (index < 0 || index >= STEPS.length) { uncover(); root.classList.remove('has-demo-panel', 'demo-is-small'); return; }
    dock(true);
    // A visitor who opened another page themselves finds the tour paused there, never sent back.
    const path = route(STEPS[index]);
    let met = false; try { met = sessionStorage.getItem(MET_KEY) === '1'; } catch {}
    if (!met && index === 0) contextSlide();
    else { uncover(); if (path && !here(path)) pause(index); else go(index); }
    // The app changes pages in place; leaving the step's page pauses the tour the same way.
    setInterval(() => { if (shown && location.pathname + location.search !== shown) { shown = null; pause(stepIndex()); } }, 300);
    setInterval(catchUp, 500);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
