/**
 * Meet Stellis: the three ways Stellis helps and what a notebook is.
 *
 * Previews use the isolated synthetic profile (/onboarding/preview), the native trend chart and
 * the dashboard notebook card.
 */
(() => {
  const slides = [
    {id: 'understand', title: 'Understand', icon: 'notebook', description: 'Recall your medical history and past appointments.'},
    {id: 'monitor', title: 'Monitor', icon: 'activity', description: 'Bring in your data, and read live vital signs from your camera on this device.'},
    {id: 'improve', title: 'Improve', icon: 'circle-check', description: 'Keep to your prescriptions and goals, and see how far you’ve come.'},
  ];
  let previewPromise;
  function sample(api) {return previewPromise ||= api('/onboarding/preview').catch(error => {previewPromise = null; throw error;});}
  // Named measurements only: comment sentences parsed as readings carry report_ keys.
  const named = data => data.measures.filter(measure => !measure.key.startsWith('report_') && measure.points.length);
  const day = value => new Date(String(value).slice(0, 10) + 'T12:00:00').toLocaleDateString([], {month: 'short', day: 'numeric', year: 'numeric'});
  const number = value => Number.isInteger(value) ? String(value) : String(Math.round(value * 10) / 10);
  const recordFor = (data, point) => data.records.find(record => record.id === point.document_id);
  /** Rows of label, value and source; the shared list shape for every preview. */
  function rows(node, items) {
    const list = node('ul', null, 'meet-rows');
    for (const [label, value, source] of items) {
      const row = node('li');row.append(node('strong', label), node('span', value, 'meet-row-value'), node('span', source, 'meet-row-source'));list.append(row);
    }
    return list;
  }
  /** The measure that changed most across the sample, for Improve. */
  function biggestChange(data) {
    const change = m => Math.abs(m.points.at(-1).value - m.points[0].value) / Math.abs(m.points[0].value || 1);
    return named(data).filter(m => m.points.length > 1).sort((a, b) => change(b) - change(a))[0];
  }
  function chart(host, measure, node, resources) {
    const plot = node('div', null, 'trend-plot');plot.setAttribute('aria-label', `${measure.label} over time in the sample records`);host.append(plot);
    const times = measure.points.map(point => StellisTrendChart.parseTime(point.time));const start = Math.min(...times), end = Math.max(...times);
    resources.charts.push(StellisTrendChart.render(plot, measure, {start, end: end > start ? end : start + 86400000, plotHeight: 80}));
  }

  async function preview(host, id, {node, icon}, resources) {
    try {
      const data = await sample(resources.api);if (!host.isConnected || resources.closed) return;
      host.replaceChildren(node('p', 'Sample records', 'meet-preview-label'));
      if (id === 'understand') {
        // The question is a static label; the answer is assembled from the sample records, not written by a model.
        const measure = named(data).find(m => m.key === 'ferritin') || named(data)[0];
        const last = measure.points.at(-1), source = recordFor(data, last);
        const ask = node('div', null, 'meet-ask');ask.append(node('span', `When was my ${measure.label.toLowerCase()} last checked?`), icon('microphone'));
        const answer = node('p', `${measure.label} was last checked on ${day(last.time)}: ${number(last.value)} ${measure.unit}.`, 'meet-answer');
        host.append(ask, answer);
        if (source) host.append(node('span', source.title, 'meet-cite'));
        const records = [...data.records].sort((a, b) => String(b.record_date).localeCompare(String(a.record_date))).slice(0, 2);
        host.append(rows(node, records.map(record => [record.title, day(record.record_date), ''])));
      } else if (id === 'monitor') {
        const camera = node('div', null, 'meet-camera');camera.setAttribute('role', 'img');camera.setAttribute('aria-label', 'Camera view with a face guide');
        camera.innerHTML = '<svg viewBox="0 0 200 64" width="100%" height="64" aria-hidden="true"><ellipse cx="100" cy="32" rx="19" ry="25"/></svg>';
        camera.append(node('span', 'Processed on this device'));
        host.append(camera);
        // One real camera test on 23 Oct 2026, shown as Trends showed it; the same capture also records breathing rate and HRV.
        host.append(rows(node, [
          ['Pulse and breathing rate', '70 bpm · 14 breaths/min', 'Camera reading, 23 Oct 2026'],
          ...named(data).slice(0, 1).map(measure => {const last = measure.points.at(-1);return [measure.label, `${number(last.value)}${last.value2 != null ? '/' + number(last.value2) : ''} ${measure.unit}`, recordFor(data, last)?.title || day(last.time)];}),
        ]));
      } else if (id === 'improve') {
        const measure = biggestChange(data);
        if (measure) {
          const first = measure.points[0], last = measure.points.at(-1);
          host.append(node('p', `${measure.label} went from ${number(first.value)} to ${number(last.value)} ${measure.unit} between ${day(first.time)} and ${day(last.time)}.`, 'meet-answer'));
          chart(host, measure, node, resources);
        }
      } else {
        const grid = node('div', null, 'dashboard-book-grid');
        // Two examples show the idea and keep the primer on one screen.
        for (const book of data.notebooks.slice(0, 2)) grid.append(StellisHealthDashboard.notebookCard(book, {collection: data.collections.find(c => c.id === book.collection_id), preview: true}));
        host.append(grid);
      }
    } catch (error) {
      if (host.isConnected) host.replaceChildren(node('p', 'The example could not load. Continue to set up yours.', 'dashboard-empty'));
    }
  }

  function progress(header, state, {node}) {
    header.querySelector('[data-setup-progress]')?.remove();
    const phase = state.step === 'welcome' ? 0 : 1;
    const list = node('ol', null, 'setup-phases');list.dataset.setupProgress = '';list.setAttribute('aria-label', 'Setup progress');
    // Setup ends on the notebook page, which opens My Health, so there is no third phase.
    ['How it works', 'Set up yours'].forEach((label, i) => {
      const item = node('li', null, 'setup-phase');if (i === phase) item.setAttribute('aria-current', 'step');if (i < phase) item.dataset.complete = 'true';
      item.append(node('span', String(i + 1), 'setup-phase-number'), node('span', label));list.append(item);
    });header.insertBefore(list, header.querySelector('#setup-theme'));
  }

  /** One rotating gallery with direct selection; one Next leaves it from any slide. */
  function render(scene, {node, icon, avatar, button, start, advance, api}, index) {
    scene.classList.add('setup-meet', 'widget-paper');
    sample(api).catch(() => {});
    const isPrimer = index === 3;
    const resources = {closed: false, charts: [], api};
    let cleanup = () => {};
    let galleryControls = null;
    const head = node('div', null, 'meet-head');
    const titles = node('div');
    if (isPrimer) {
      titles.append(node('h1', 'Everything goes into notebooks'), node('p', 'A notebook holds one part of your health, such as check-ups, an ongoing condition or a procedure you’re planning. Stellis files each record in the notebook it belongs to, and looks there first when you ask a question.'));
      // Stellis speaks beside the heading, so nothing below can cover it.
      const say = node('div', null, 'meet-say');
      say.append(avatar('stellis', true, 44), node('p', 'You won’t build these by hand. From a few quick answers, I’ll suggest notebooks, and you check them before I save anything.'));
      head.append(titles, say);
    } else {
      head.append(avatar('stellis', true, 48));
      titles.append(node('h1', 'Meet Stellis'), node('p', 'Stellis reads your records, appointments and medicines, and helps you understand, monitor and improve your health.'));
      // Where records live and what leaves the server, as the product film tells it (#226).
      const privacy = node('aside', null, 'meet-privacy');privacy.setAttribute('aria-label', 'Privacy');
      const facts = node('ul');
      for (const [glyph, text] of [
        ['key', 'Your records stay on our server on Nebius AI Cloud.'],
        ['sparkles', 'Only what a question needs goes to Nemotron on Nebius Token Factory, which keeps nothing.'],
        ['search', 'When Stellis looks something up, it sends only the topic, never your details.'],
      ]) {const item = node('li');item.append(icon(glyph), node('span', text));facts.append(item);}
      privacy.append(facts);
      head.append(titles, privacy);
    }
    head.classList.toggle('is-primer', isPrimer);
    scene.append(head);
    if (!isPrimer) {
      const gallery = node('section', null, 'meet-gallery');
      gallery.setAttribute('aria-label', 'Ways Stellis can help');gallery.setAttribute('aria-roledescription', 'carousel');
      const viewport = node('div', null, 'meet-gallery-viewport'), track = node('div', null, 'meet-gallery-track');viewport.append(track);
      const panels = slides.map((slide, i) => {
        const stage = node('div', null, 'meet-stage');stage.setAttribute('role', 'group');stage.setAttribute('aria-roledescription', 'slide');
        stage.setAttribute('aria-label', `${i + 1} of ${slides.length}: ${slide.title}`);
        const tile = node('span', null, 'meet-tile is-' + slide.id);tile.append(icon(slide.icon));
        const copy = node('div', null, 'meet-stage-copy');copy.append(tile, node('h2', slide.title), node('p', slide.description));
        const box = node('div', null, 'meet-preview');box.setAttribute('aria-label', slide.title + ' example');
        preview(box, slide.id, {node, icon}, resources);
        stage.append(copy, box);track.append(stage);return stage;
      });
      let current = Math.min(2, Math.max(0, index)), paused = matchMedia('(prefers-reduced-motion: reduce)').matches, hovered = false, focused = false, timer;
      const controls = node('div', null, 'meet-gallery-controls');
      const rotation = button('', () => {paused = !paused;paint();schedule();}, 'meet-rotation');
      const picks = node('div', null, 'meet-gallery-dots');picks.setAttribute('role', 'group');picks.setAttribute('aria-label', 'Choose a slide');
      const go = i => {paused = true;current = (i + slides.length) % slides.length;paint();schedule();};
      const dots = slides.map((slide, i) => {const dot = button('', () => go(i), 'meet-gallery-dot');dot.setAttribute('aria-label', 'Show ' + slide.title);picks.append(dot);return dot;});
      gallery.addEventListener('keydown', event => {
        if (event.target.closest('select,input,textarea')) return;
        if (event.key === 'ArrowRight') {event.preventDefault();go(current + 1);}
        else if (event.key === 'ArrowLeft') {event.preventDefault();go(current - 1);}
      });
      function paint() {
        track.style.setProperty('--gallery-index', current);
        panels.forEach((panel, i) => {panel.inert = i !== current;panel.setAttribute('aria-hidden', String(i !== current));panel.classList.toggle('is-active', i === current);});
        dots.forEach((dot, i) => dot.setAttribute('aria-pressed', String(i === current)));
        rotation.replaceChildren(icon(paused ? 'player-play' : 'player-pause'));rotation.setAttribute('aria-label', paused ? 'Play slides' : 'Pause slides');
        track.setAttribute('aria-live', paused ? 'polite' : 'off');
      }
      function schedule() {clearInterval(timer);if (!paused && !hovered && !focused && !document.hidden) timer = setInterval(() => {current = (current + 1) % slides.length;paint();}, 6000);}
      const visibility = () => schedule();document.addEventListener('visibilitychange', visibility);
      // The slides, arrows and footer controls all hold rotation while pointed at or focused.
      const holds = event => gallery.contains(event.relatedTarget) || controls.contains(event.relatedTarget);
      for (const area of [gallery, controls]) {
        area.addEventListener('mouseenter', () => {hovered = true;schedule();});area.addEventListener('mouseleave', event => {hovered = holds(event);schedule();});
        area.addEventListener('focusin', () => {focused = true;schedule();});area.addEventListener('focusout', event => {focused = holds(event);schedule();});
      }
      // Small arrows on either side step one slide; like the dots, they stop the rotation.
      const arrow = (label, glyph, step) => {const result = button('', () => go(current + step), 'meet-gallery-arrow');result.setAttribute('aria-label', label);result.append(icon(glyph));return result;};
      // The dots and pause sit in the footer row, so the slides and actions share one screen.
      controls.append(picks, rotation);galleryControls = controls;
      gallery.append(arrow('Previous slide', 'chevron-left', -1), viewport, arrow('Next slide', 'chevron-right', 1));scene.append(gallery);paint();schedule();
      cleanup = () => {clearInterval(timer);document.removeEventListener('visibilitychange', visibility);};
    } else {
      const layout = node('div', null, 'meet-primer');
      const parts = node('section', null, 'meet-primer-parts');parts.append(node('h2', 'What a notebook holds'));
      const list = node('ul');
      for (const [glyph, name, text] of [['file', 'Records', 'Lab results and letters, kept as the original file'], ['microphone', 'Visits', 'Recordings and transcripts of appointments'],
        ['pill', 'Medicines', 'Courses with doses, days taken and refill dates'], ['activity', 'Levels', 'Results charted over time']]) {
        const row = node('li');const words = node('div');words.append(node('strong', name), node('span', text));row.append(icon(glyph), words);list.append(row);
      }
      parts.append(list);
      const books = node('section', null, 'meet-preview meet-notebook-primer');books.setAttribute('aria-label', 'Sample notebooks');
      preview(books, 'notebooks', {node, icon}, resources);
      const side = node('div');side.append(books);
      layout.append(parts, side);scene.append(layout);
    }
    const footer = node('div', null, 'meet-footer');
    footer.append(button(isPrimer ? 'Set up my notebooks' : 'Next', () => isPrimer ? start() : advance(3), 'primary'));
    if (isPrimer) footer.append(button('Back', () => advance(0)));
    if (galleryControls) footer.append(galleryControls);
    if (!isPrimer) footer.append(node('p', 'Sample records from a fictional patient', 'meet-example-label'));
    scene.append(footer);
    return () => {resources.closed = true;resources.charts.forEach(item => item?.destroy());cleanup();};
  }

  window.StellisMeet = {progress, render};
})();
