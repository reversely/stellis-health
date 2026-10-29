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
  // The short tour (owner, 2026-10-29): Maya's five years already filed, one question, one source, one
  // new report that changes her history, one follow-through.
  const TRACK_KEY = 'stellis-demo-track', ANEMIA_KEY = 'stellis-demo-anemia';

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
   * The walk, on the app's real routes (owner, 2026-10-28): the visitor presses the real Add, Upload
   * Record, example file, Approve, Sort and File controls, with Maya's files offered as example files. `path` may name
   * {added}, the record the visitor uploaded. `target` is a selector, or [selector, text] for the first
   * match containing that text. A `wait` step moves on when the visitor presses the target; an `until`
   * step moves on once that selector appears. On those steps › presses the control for the visitor
   * (owner, 2026-10-28), so the demo never loses its place; elsewhere it moves on.
   * `receipt` lists what the server did with the uploaded file.
   */
  const LONG = [
    {path: '/', target: '#global-add', pillar: 'understand', wait: true, title: 'From a lab report to answers',
      text: "Add a lab report and Stellis reads every result on it, so you don't have to decode it yourself."},
    {target: ['.health-capture .coach-button', 'Upload Record'], pillar: 'understand', wait: true, title: 'Upload a record',
      text: 'Lab reports, letters, photos and visit recordings all work.'},
    // The upload opens the report itself, so the step ends when the report appears, not at the press.
    {target: ['.health-capture .upload-examples .coach-button', 'Blood test'], pillar: 'understand', until: '.report-paper',
      title: "Maya's blood test",
      text: 'A blood test from January 2027. Choosing it uploads it, and the report opens.'},
    {path: '/document/{added}', target: '.report-found', pillar: 'understand', receipt: true, title: 'What Stellis read',
      text: 'Stellis summarizes the report as it opens. The date and results it found are added with one press each, whenever you like.'},
    {path: '/document/{added}', target: ['.report-region.has-fact', 'Ferritin'], pillar: 'understand', wait: true,
      title: 'Choose a result',
      text: "Ferritin is 11, below this lab's range of 15 to 150. Select it to ask about it."},
    {path: '/document/{added}', target: ['.report-idea', 'mean'], pillar: 'understand', wait: true, title: 'Ask what it means',
      text: "A plain answer about this result, drawn from Maya's records."},
    {path: '/document/{added}', target: '.report-explanation .trace-link', pillar: 'understand', wait: true, patience: 180000,
      title: 'Check the answer',
      text: 'Every fact links to the page it came from. How Stellis answered shows the rest of its working.'},
    {target: 'dialog.answer-trace .trace-body', pillar: 'understand', title: 'How Stellis answered',
      text: 'The records and pages Stellis read for this answer, and any public sources it searched, so you can see where each part came from.'},
    {path: '/records', target: ['.upload-examples .coach-button', 'All of'], pillar: 'chart',
      until: '.records-review .coach-button.is-primary', title: 'Five years at once',
      text: "Add all of Maya's records together. Each is read, dated and checked the same way."},
    {target: '.records-review .coach-button.is-primary', pillar: 'chart', until: ['.records-review h3', 'Saved'],
      title: 'Check, then approve',
      text: 'The dates and results Stellis found in each record, for you to check before anything is saved.'},
    {target: '.records-sort', pillar: 'chart', until: '.records-file-all:not([hidden])', hideBefore: 'demo-before-sort', title: 'Sort into notebooks',
      text: 'Stellis groups the records by area of care, such as Bloodwork, IVF or Pregnancy.'},
    {target: '.records-file-all', pillar: 'chart', until: ['.records-status', 'Filed in'], title: 'File them',
      text: 'Each record goes into its notebook. You can move any of them later.'},
    {path: '/', target: '.home-results', pillar: 'chart', title: "Maya's latest results",
      text: 'Her newest result for each test, and how it changed since the one before.'},
    {path: '/', target: '#global-add', pillar: 'log', wait: true, title: 'A visit, summarized',
      text: 'Record an appointment, or add a recording, and Stellis writes up what was said.'},
    {target: ['.health-capture .coach-button', 'Upload Record'], pillar: 'log', wait: true, title: 'Upload a recording',
      text: 'Recordings go in the same way as reports.'},
    {target: ['.health-capture .upload-examples .coach-button', 'Visit recording'], pillar: 'log', wait: true,
      title: "Dr Vale's call",
      text: "A short call about Maya's iron tablets. Choosing it uploads it, and Stellis transcribes it on its own server."},
    {target: '#enhanced-notes-panel', pillar: 'understand', title: 'Transcript and summary',
      text: 'What was decided and what happens next, with the full transcript beside it. The line under Summary shows progress until it is ready.'},
    {path: '/trends', target: 'iframe[title="Measurements"]', pillar: 'chart', title: 'Every result over time',
      text: 'One chart per test across five years of reports. The table icon shows which report each value came from.'},
    {path: '/trends', target: ['button', 'Log vital signs'], pillar: 'monitor', wait: true, title: 'Your own vital signs',
      text: 'Check your pulse and breathing with your camera in about 30 seconds. The video stays on your device.'},
    {kind: 'end', title: 'Now with your own records', pillar: null},
  ];
  const SHORT = [
    {path: '/', target: '.home-results', pillar: 'chart', title: "Maya's five years",
      text: 'A fictional five-year case: 70 synthetic records, already read and filed. Ferritin, her iron store, is the thread to follow.'},
    {path: '/notebook/{anemia}', target: '.chat-idea-action[data-idea*="changed since"]', pillar: 'understand', wait: true,
      title: 'Ask how it changed', text: 'A question Stellis offers here. The answer reads every dated ferritin result in her records.'},
    {path: '/notebook/{anemia}', target: '.nb-cite:not(.nb-cite-public)', pillar: 'understand', wait: true, patience: 180000,
      title: 'Open a source', text: 'Each value links to the report it came from.'},
    {target: '.report-paper', pillar: 'understand', title: 'The original report',
      text: "The lab's own page, where that value is printed."},
    {path: '/', target: '#global-add', opens: 'upload', notebook: ANEMIA_KEY, pillar: 'understand', wait: true, title: 'Add a new report',
      text: 'A blood test from March 2032, after her five years. Adding it should change what Stellis shows.'},
    {target: ['.health-capture .upload-examples .coach-button', 'March 2032'], pillar: 'understand', until: '.report-paper',
      title: 'Iron studies, March 2032', text: 'Choosing it uploads it, and the report opens.'},
    {path: '/document/{added}', target: ['.report-region.has-fact', 'Ferritin'], pillar: 'understand',
      title: 'Ferritin 52', text: 'The report prints ferritin 52 ug/L. Stellis read it from that line.'},
    {path: '/document/{added}', target: ['.report-found .coach-button', 'Add to Measurements'], pillar: 'chart', wait: true,
      title: 'Add it to her history', text: 'Nothing is added until you approve it. One press adds every result Stellis found.'},
    {path: '/', target: '.home-results', pillar: 'chart', title: 'Her history, updated',
      text: 'Ferritin now ends at 52 ug/L, March 24, 2032, after 48 in December.'},
    {path: '/document/{added}', target: ['.report-idea', 'next appointment'], pillar: 'understand', wait: true, patience: 180000,
      title: 'For her next appointment', text: 'Stellis drafts what to ask from her records, each point linked to its source.'},
    {kind: 'end', title: 'Now with your own records', pillar: null},
  ];
  const STEPS = (() => { try { return sessionStorage.getItem(TRACK_KEY) === 'short' ? SHORT : LONG; } catch { return LONG; } })();

  // Maya's files, offered as example files in the real Add form and on the Records page.
  async function offerExamples() {
    const response = await fetch('/api/v1/demo/files').catch(() => null);
    if (!response || !response.ok || !window.StellisRecordUpload) return;
    const listing = await response.json();
    const all = Object.values(listing.years).flat();
    const one = name => all.filter(file => file.name.startsWith(name));
    const reports = all.filter(file => file.type === 'application/pdf');
    const followups = listing.followups || [];
    if (STEPS === SHORT) window.StellisRecordUpload.examples.splice(0, Infinity, {label: 'Iron studies, March 2032', files: followups.filter(file => file.name.startsWith('D071_'))});
    else window.StellisRecordUpload.examples.splice(0, Infinity,
      {label: 'Blood test, January 2027', files: one('D002_')},
      {label: 'Visit recording, February 2027', files: all.filter(file => file.type === 'audio/mpeg')},
      {label: `All of Maya's records, 2027 to 2031 (${reports.length})`, files: reports});
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
  // A control a later step introduces stays out of sight until that step (owner, 2026-10-28: Sort into
  // notebooks showed before the tour reached it).
  // The page's policy allows no inline <style>, so each such step names a class demo-guide.css defines.
  function hideLater(index) {
    for (const step of STEPS) if (step.hideBefore) root.classList.toggle(step.hideBefore, STEPS.indexOf(step) > index);
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
        if (stepIndex() !== index) return;
        if (!control) { go(index + 1); return; }
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
    card.append(roadmap(null), head, el('h2', 'Now with your own records'),
      el('p', 'Add your reports and recordings, and Stellis explains every result, keeps track of your care and shows how things change. Only you can read what you add.', 'demo-text'), row);
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
    const lines = el('p', 'She has five years of lab reports, letters and visit recordings to keep track of.', 'demo-context-lede');
    // What Stellis is, in the narration's second line, with the four pillars the tour is organized by.
    const stellis = el('div', null, 'demo-context-stellis');
    const said = el('p', 'Stellis Health is for people like Maya: an AI agent that knows your history and helps you understand, log, monitor and chart your health.', 'demo-context-lede');
    const pillars = el('div', null, 'demo-roadmap demo-context-pillars');
    for (const [id, name] of [['understand', 'Understand'], ['log', 'Log'], ['monitor', 'Monitor'], ['chart', 'Chart']]) pillars.append(el('span', name, `demo-pillar demo-pillar-${id} is-current`));
    const words = el('div'); words.append(said, pillars);
    // One left edge for the whole slide: Maya's portrait is the slide's one image, so no avatar column.
    stellis.append(words);
    const what = el('p', "Maya is fictional. You'll add her records the way you would add yours, and see what Stellis does with them.", 'demo-context-what');
    const start = el('button', 'Start the tour', 'coach-button is-primary'); start.type = 'button';
    const quick = el('button', 'Short tour', 'coach-button is-quiet'); quick.type = 'button';
    const explore = el('button', 'Explore on my own', 'coach-button is-quiet'); explore.type = 'button';
    const done = tour => { try { sessionStorage.setItem(MET_KEY, '1'); } catch {} slide.remove(); dock(true); if (!tour) setSmall(true); go(stepIndex()); };
    start.onclick = () => done(true); explore.onclick = () => done(false);
    quick.onclick = async () => {
      quick.disabled = true; quick.setAttribute('aria-busy', 'true');
      try { await startShort(); } catch (error) { quick.disabled = false; quick.removeAttribute('aria-busy'); what.textContent = error.message; }
    };
    const row = el('div', null, 'demo-context-actions'); row.append(start, quick, explore);
    inner.append(mayaPortrait(), title, lines, stellis, what, row);
    slide.append(inner);
    dock(false);
    document.body.append(slide);
    document.documentElement.classList.remove('demo-arriving');
    start.focus();
  }

  /**
   * The short tour starts from Maya's five years already filed (the snapshot), with her clock moved to
   * April 2032 so the March 2032 report it adds is part of her history, then reloads into its steps.
   */
  async function startShort() {
    const call = async (path, options) => { const response = await fetch(path, options); if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'The short tour could not start. Try again.'); return response.json(); };
    await call('/api/v1/demo/seed', {method: 'POST'});
    await call('/api/v1/demo/story-date', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({date: '2032-04-01'})});
    const anemia = (await call('/api/v1/notebooks')).notebooks.find(book => /anemia/i.test(book.name));
    try {
      sessionStorage.setItem(TRACK_KEY, 'short'); sessionStorage.setItem(MET_KEY, '1'); sessionStorage.setItem(STEP_KEY, '0');
      if (anemia) sessionStorage.setItem(ANEMIA_KEY, anemia.id);
      sessionStorage.removeItem(SMALL_KEY);
    } catch {}
    location.assign('/');
  }

  async function leaveDemo(next = '/auth') {
    try { sessionStorage.removeItem(STEP_KEY); sessionStorage.removeItem(TRACK_KEY); } catch {}
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
    shown = path ? location.pathname + location.search : null;
    if (small()) { pill(index, false); return; }
    if (step.kind === 'end') { mark(null); endCard(); return; }
    stepCard(index, step);
    mark(null);
    hideLater(index);
    const node = await waitFor(step.target, step.patience);
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
