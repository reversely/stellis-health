/**
 * @fileoverview The sign-in page's introduction (#258): the pillar word that cycles in the opening line,
 * Stellis filing records in the opening, the ferritin chart, and each illustration shown once as it comes into view. Without motion every
 * part shows in its final state; the account card is auth.js's own.
 */
(() => {
  const page = document.querySelector('.landing');
  if (!page) return;
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!still) page.classList.add('is-moving');
  // Boxes on the report image, in percent of it (the page's policy allows no inline style attributes).
  page.querySelectorAll('[data-box]').forEach(node => {
    const [x, y, w, h] = node.dataset.box.split(' ');
    node.style.setProperty('--x', `${x}%`); node.style.setProperty('--y', `${y}%`);
    if (w) { node.style.setProperty('--w', `${w}%`); node.style.setProperty('--h', `${h}%`); }
    if (node.dataset.i) node.style.setProperty('--i', node.dataset.i);
  });

  // "helps you ___ your health": understand, log, monitor, chart, each in its own colour, while the
  // opening line is on screen.
  const PILLARS = ['understand', 'log', 'monitor', 'chart'];
  const slot = page.querySelector('.landing-slot');
  let current = 0, timer = 0;
  function next() {
    current = (current + 1) % PILLARS.length;
    // Only the word showing stays: a tab left in the background (no animation frames) never stacks words.
    const words = [...slot.querySelectorAll('.landing-slot-word')];
    words.slice(0, -1).forEach(word => word.remove());
    const id = PILLARS[current], old = words[words.length - 1];
    const word = document.createElement('span');
    word.className = 'landing-slot-word' + (still ? '' : ' is-coming');
    word.dataset.pillar = id; word.textContent = id;
    slot.append(word);
    slot.style.setProperty('--slot', `var(--pillar-${id}-colour)`);
    if (still) { old?.remove(); return; }
    requestAnimationFrame(() => requestAnimationFrame(() => {
      word.classList.remove('is-coming');
      old?.classList.add('is-leaving');
      setTimeout(() => old?.remove(), 320);
    }));
  }
  if (slot) {
    let visible = false;
    const run = () => { clearInterval(timer); if (visible && !document.hidden) timer = setInterval(next, 1800); };
    new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; run(); }).observe(slot);
    document.addEventListener('visibilitychange', run);
  }

  // Stellis as the film draws it: the app's sprite in its
  // colour, hopping and squashing as it lands, arms up in the air, a wave now and then and a blink. One
  // works in the opening's scene; a small one stands on the server in the diagram.
  const AVATAR = window.StellisAgentAvatar;
  const LIMBS = new Set(['M0 4', 'M6 4', 'M1 6', 'M5 6']);
  function hop(t, height) {
    if (t < 0 || t > 0.62) return {lift: 0, squash: 0};
    if (t < 0.08) return {lift: 0, squash: 0.12 * Math.sin((t / 0.08) * Math.PI / 2)};
    if (t < 0.44) { const p = (t - 0.08) / 0.36; return {lift: height * 4 * p * (1 - p), squash: -0.1 * Math.cos(p * Math.PI) ** 2}; }
    const p = (t - 0.44) / 0.18;
    return {lift: 0, squash: 0.16 * Math.sin(p * Math.PI) * (1 - p * 0.5)};
  }
  function character(host, size) {
    if (!AVATAR || !host) return null;
    const NS = 'http://www.w3.org/2000/svg', color = AVATAR.colorFor('stellis');
    const body = AVATAR.spritePath('stellis').split('z').filter(c => c && !LIMBS.has(c.split('h')[0])).map(c => `${c}z`).join('');
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '-0.5 -0.5 8 8'); svg.setAttribute('shape-rendering', 'crispEdges'); svg.setAttribute('aria-hidden', 'true');
    const path = (d) => { const node = document.createElementNS(NS, 'path'); node.setAttribute('d', d); node.setAttribute('fill', color); svg.append(node); return node; };
    path(body);
    const arms = [path(''), path('')], legs = [path(''), path('')], eyes = path('');
    const pose = (airborne, leftUp, rightUp, blink) => {
      arms[0].setAttribute('d', `M0 ${leftUp ? 3 : 4}h1v1h-1z`); arms[1].setAttribute('d', `M6 ${rightUp ? 3 : 4}h1v1h-1z`);
      legs[0].setAttribute('d', `M${airborne ? 0 : 1} 6h1v1h-1z`); legs[1].setAttribute('d', `M${airborne ? 6 : 5} 6h1v1h-1z`);
      eyes.setAttribute('d', blink ? 'M2 3h1v1h-1zM4 3h1v1h-1z' : '');
    };
    pose(false, false, false, false);
    svg.style.transformOrigin = '50% 100%';
    host.append(svg);
    return {svg, size, pose, jumpAt: -1};
  }
  // Ferritin from the demo's eleven reports, January 2027 to December 2031, for the chart section.
  const FERRITIN = [['2027-01-21', 11], ['2027-05-20', 34], ['2028-11-30', 38], ['2029-05-17', 36], ['2030-01-08', 32],
    ['2030-05-02', 9], ['2030-06-13', 22], ['2030-10-10', 42], ['2031-06-15', 13], ['2031-09-18', 35], ['2031-12-11', 48]];

  // The opening's scene (owner, 2026-10-28/29): what Stellis does with records, in motion, with no patient
  // and no single test named. A loose cloud of mixed records drifts at the top; Stellis takes one at a time
  // and files it into its area of care, which counts it and shows it. It runs only while the opening is in
  // view; under reduced motion the areas are simply shown filled.
  const AREAS = ['Bloodwork', 'Pregnancy', 'Nutrition'];
  const DOCS = [['Blood test', 'file-text', 0], ['Ultrasound scan', 'file-text', 1], ['Dietitian letter', 'file-text', 2],
    ['Thyroid panel', 'file-text', 0], ['Visit recording', 'microphone', 1], ['Food diary', 'file-text', 2],
    ['Iron panel', 'file-text', 0], ['Prenatal letter', 'file-text', 1], ['Meal plan', 'file-text', 2]];
  const scene = page.querySelector('.landing-scene');
  const icon = name => `<svg class="icon"><use href="/static/landing/icons.svg#${name}"/></svg>`;
  let mover = null, board = null, areas = [];
  if (scene) {
    board = document.createElement('div'); board.className = 'landing-scene-shelf';
    areas = AREAS.map(name => {
      const area = document.createElement('section'); area.className = 'landing-scene-area';
      area.innerHTML = `<p class="landing-scene-name">${icon('notebook')}<span>${name}</span><b>0</b></p><ul></ul>`;
      board.append(area);
      return area;
    });
    mover = document.createElement('div'); mover.className = 'landing-scene-stellis';
    scene.append(board, mover);
  }
  // Filing shows the record at the top of its area's list (two at most) and counts it.
  function file(i, pop = true) {
    const [title, kind, k] = DOCS[i], area = areas[k];
    const count = area.querySelector('b'), list = area.querySelector('ul');
    count.textContent = String(Number(count.textContent) + 1);
    const item = document.createElement('li'); item.innerHTML = `${icon(kind)}<span>${title}</span>`;
    list.prepend(item);
    while (list.children.length > 2) list.lastElementChild.remove();
    if (pop) item.animate([{opacity: 0, transform: 'translateY(-6px)'}, {opacity: 1, transform: 'none'}], {duration: 260, easing: 'cubic-bezier(0.22, 1, 0.36, 1)'});
  }
  function empty() { for (const area of areas) { area.querySelector('b').textContent = '0'; area.querySelector('ul').replaceChildren(); } }
  const worker = character(mover, 64);
  const small = character(page.querySelector('.landing-node-stellis'), 44);
  if (worker) worker.working = true;
  const actors = [worker, small].filter(Boolean);
  let onScreen = true;
  if (page.querySelector('.landing-hero')) new IntersectionObserver(([entry]) => { onScreen = entry.isIntersecting; }).observe(page.querySelector('.landing-hero'));
  if (board && still) DOCS.forEach((_, i) => file(i, false));
  if (mover && (still || !worker)) mover.style.transform = `translate(${scene.clientWidth / 2 - 32}px, ${board.offsetTop - 70}px)`;
  if (actors.length && !still) {
    const start = performance.now();
    const tick = now => {
      const t = (now - start) / 1000;
      for (const actor of actors) {
        if (actor === worker && !onScreen) continue;
        const pressed = actor.jumpAt >= 0 ? (now - actor.jumpAt) / 1000 : -1;
        // The worker hops only when it moves or is pressed; the one in the diagram hops every 2.2 s.
        const motion = pressed >= 0 && pressed <= 0.62 ? hop(pressed, actor.size * (actor.working ? 0.3 : 0.6))
          : actor.working ? {lift: 0, squash: 0} : t < 0.62 ? hop(t, actor.size * 0.35) : hop((t - 0.62) % 2.2 - (2.2 - 0.62), actor.size * 0.22);
        actor.lift = motion.lift;
        const airborne = motion.lift > actor.size * 0.04;
        const wave = !actor.holding && t > 2 && (t - 2) % 5.2 < 0.8 && Math.floor((t - 2) / 0.2) % 2 === 0;
        actor.pose(airborne, airborne || actor.holding, airborne || actor.holding || wave, t > 1.2 && (t - 1.2) % 3.4 < 0.12);
        actor.svg.style.transform = `translateY(${-motion.lift}px) scale(${1 + motion.squash}, ${1 - motion.squash})`;
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    mover?.addEventListener('pointerdown', () => { worker.jumpAt = performance.now(); });
  }

  // The filing loop. Positions come from the scene's size each time, so it fits any width.
  if (worker && !still) {
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const ready = async () => { while (!onScreen || document.hidden) await wait(300); };
    const inOut = p => p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2;
    const out = p => 1 - (1 - p) ** 3;
    const tween = (ms, step, ease = inOut) => new Promise(done => {
      const s0 = performance.now();
      const run = now => { const p = Math.min(1, (now - s0) / ms); step(ease(p)); p < 1 ? requestAnimationFrame(run) : done(); };
      requestAnimationFrame(run);
    });
    const place = (node, x, y, scale = 1, opacity = 1) => { node.style.transform = `translate(${x}px, ${y}px) scale(${scale})`; node.style.opacity = String(opacity); };
    // The pile keeps as many rows as fit above Stellis: five on a wide screen, fewer in the shorter
    // scene of a narrow one, where five reached down over Stellis (owner, 2026-10-30).
    // A phone shows two records in the sky, so the opening fits one screen (owner, 2026-10-31).
    const S = 64, ROW = 30, PHONE = scene.clientWidth < 560;
    const CLOUD = PHONE ? 2 : Math.max(2, Math.min(5, Math.floor((board.offsetTop - S - 6 - 36) / ROW) + 1));
    let x = 0;
    // The cloud: the next few reports in a loose fanned pile across the top, one row each, each drifting
    // slightly. A report taken from the pile frees its row for the next one.
    const cloud = [];
    // On a phone the two share the top row, one at each side, slightly staggered, clear of Stellis's hops;
    // the narrowest phones put the second a card lower.
    const slot = (k, cw) => { const W = scene.clientWidth, jitter = [-0.18, 0.12, -0.05, 0.2, -0.12][k % CLOUD];
      if (PHONE) return k % 2 ? [W - cw, W < 280 ? 40 : 6] : [0, 0];
      return [Math.max(0, Math.min(W - cw, (W - cw) / 2 + jitter * W)), (k % CLOUD) * ROW]; };
    function spawn(i, k) {
      const [title, kind] = DOCS[i];
      const card = document.createElement('div'); card.className = 'landing-scene-card is-floating';
      card.innerHTML = `${icon(kind)}<span>${title}</span>`;
      card.style.setProperty('--drift', `${(k % 4) * 0.7}s`);
      scene.insertBefore(card, mover);
      const [cx, cy] = slot(k, card.offsetWidth);
      card.dataset.x = cx; card.dataset.y = cy;
      card.animate([{opacity: 0, transform: `translate(${cx}px, ${cy - 10}px) scale(.96)`}, {opacity: 1, transform: `translate(${cx}px, ${cy}px)`}], {duration: 320, easing: 'cubic-bezier(0.22, 1, 0.36, 1)'});
      place(card, cx, cy);
      cloud.push({card, i});
    }
    (async () => {
      for (let k = 0; k < CLOUD; k++) spawn(k % DOCS.length, k);
      let made = CLOUD;
      await wait(700);
      for (let n = 0; ; n++) {
        await ready();
        const {card, i} = cloud.shift();
        if (i === 0 && n) { await wait(1200); empty(); }
        const W = scene.clientWidth, ground = board.offsetTop - S - 6, area = areas[DOCS[i][2]];
        const dropX = board.offsetLeft + area.offsetLeft + area.offsetWidth / 2, dropY = board.offsetTop + area.offsetTop + 24;
        if (n === 0) { x = W / 2 - S / 2; place(mover, x, ground); }
        const cw = card.offsetWidth, ch = card.offsetHeight;
        const clamp = v => Math.max(0, Math.min(W - cw, v));
        let cx = Number(card.dataset.x), cy = Number(card.dataset.y);
        card.classList.remove('is-floating'); card.style.zIndex = '2';
        // Stellis hops under the report it picked; the report comes down onto its raised hands.
        const under = Math.max(0, Math.min(W - S, cx + cw / 2 - S / 2));
        worker.jumpAt = performance.now();
        const from = x;
        await tween(Math.min(700, 250 + Math.abs(under - from) * 1.3), p => { x = from + (under - from) * p; place(mover, x, ground); });
        const head = ground - ch + 8;
        await tween(320, p => place(card, cx + (clamp(x + S / 2 - cw / 2) - cx) * p, cy + (head - cy) * p * p), p => p);
        worker.holding = true;
        // It carries the record to its area, hopping.
        const target = Math.max(0, Math.min(W - S, dropX - S / 2));
        const start = x;
        worker.jumpAt = performance.now();
        await tween(Math.min(800, 280 + Math.abs(target - start) * 1.3), p => { x = start + (target - start) * p; place(mover, x, ground); place(card, clamp(x + S / 2 - cw / 2), head - (worker.lift || 0)); });
        // A hop drops it in: the record folds into its area, which lists and counts it.
        worker.jumpAt = performance.now();
        worker.holding = false;
        const lx = clamp(x + S / 2 - cw / 2);
        await tween(340, p => place(card, lx + (dropX - cw / 2 - lx) * p, head + (dropY - ch / 2 - head) * p, 1 - 0.75 * p, 1 - p), p => p * p);
        card.remove();
        file(i);
        // The cloud fills back up with the next record.
        spawn(made % DOCS.length, made); made++;
        await wait(380);
      }
    })();
  }

  const chart = page.querySelector('.landing-chart svg');
  if (chart) {
    // Drawn at the width it has, so its labels keep their size on a phone.
    const NS = 'http://www.w3.org/2000/svg', W = Math.round(chart.getBoundingClientRect().width) || 640, H = 220, left = 16, right = 16, top = 24, bottom = 32;
    chart.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const time = d => new Date(d + 'T12:00:00').getTime();
    const t0 = time(FERRITIN[0][0]), t1 = time(FERRITIN[FERRITIN.length - 1][0]);
    const x = d => left + (time(d) - t0) / (t1 - t0) * (W - left - right);
    const y = v => top + (1 - v / 50) * (H - top - bottom);
    const add = (tag, attrs, text) => {
      const node = document.createElementNS(NS, tag);
      for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
      if (text) node.textContent = text;
      return chart.appendChild(node);
    };
    add('line', {class: 'landing-range', x1: left, x2: W - right, y1: y(15), y2: y(15)});
    // Labelled at the left, between the first two points, where the line is far above it.
    add('text', {x: x('2027-09-01'), y: y(15) - 6, 'text-anchor': 'start'}, "Lab's range starts at 15");
    for (const year of [2027, 2028, 2029, 2030, 2031]) add('text', {x: x(`${year}-01-01`), y: H - 8, 'text-anchor': 'start'}, W < 480 ? `'${String(year).slice(2)}` : String(year));
    add('polyline', {class: 'landing-line', pathLength: 1, points: FERRITIN.map(([d, v]) => `${x(d)},${y(v)}`).join(' ')});
    FERRITIN.forEach(([d, v], i) => {
      const point = add('circle', {class: 'landing-point', cx: x(d), cy: y(v), r: 5});
      point.style.setProperty('--i', i);
    });
    // The first, lowest and last results are labelled.
    for (const i of [0, 5, FERRITIN.length - 1]) {
      const [d, v] = FERRITIN[i];
      add('text', {class: 'landing-value', x: x(d) + (i ? -8 : 8), y: v > 20 ? y(v) - 12 : y(v) + 22, 'text-anchor': i ? 'end' : 'start'}, String(v));
    }
  }

  // Each illustration shows once, the first time it is in view.
  const shown = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) { entry.target.classList.add('is-shown'); shown.unobserve(entry.target); }
  }, {threshold: 0.35});
  page.querySelectorAll('[data-reveal]').forEach(node => shown.observe(node));

  // The example doses tick as the app's do.
  page.querySelectorAll('.landing-tick').forEach(tick => tick.addEventListener('click', () =>
    tick.setAttribute('aria-pressed', String(tick.getAttribute('aria-pressed') !== 'true'))));

  // Signing in: the corner button opens the account card; ×, Escape or a press outside closes it. It
  // opens by itself for a new account (?create=1, or a server with none yet).
  const panel = document.querySelector('#account-panel'), opener = document.querySelector('#landing-signin');
  const setOpen = open => {
    if (!panel || !opener) return;
    panel.hidden = !open; opener.setAttribute('aria-expanded', String(open));
    if (open) setTimeout(() => panel.querySelector('input:not([hidden])')?.focus(), 50); else opener.focus({preventScroll: true});
  };
  opener?.addEventListener('click', () => setOpen(panel.hidden));
  panel?.querySelector('.landing-account-close')?.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && panel && !panel.hidden) setOpen(false); });
  document.addEventListener('pointerdown', event => {
    if (panel && !panel.hidden && !panel.contains(event.target) && !opener.contains(event.target)) setOpen(false);
  });
  page.querySelectorAll('[data-signin]').forEach(button => button.addEventListener('click', () => { window.scrollTo({top: 0, behavior: still ? 'auto' : 'smooth'}); setOpen(true); }));
  const card = document.querySelector('.auth-card');
  if (card) new MutationObserver(() => {
    if (card.classList.contains('is-ready') && document.querySelector('#sign-up-tab')?.getAttribute('aria-current') === 'page' && !card.dataset.opened) { card.dataset.opened = '1'; setOpen(true); }
  }).observe(card, {attributes: true, attributeFilter: ['class']});

  // The down arrow goes to the first section.
  page.querySelectorAll('[data-scroll]').forEach(button => button.addEventListener('click', () => {
    const target = document.querySelector(button.dataset.scroll);
    target?.scrollIntoView({behavior: still ? 'auto' : 'smooth', block: 'start'});
  }));
})();
