/**
 * @fileoverview A side panel for one document beside a chat: its notebook,
 * title and date with evidence, the health facts to check, and the original
 * page with flagged lines (or the cited region) highlighted. On phones it is
 * a full-height sheet. Everything comes from the document's own records.
 */
(function(root) {
'use strict';
const dom = () => root.StellisDom;
const el = (tag, text, cls) => dom().el(tag, text, cls);
let current = null;
const words = text => String(text).toLowerCase().replace(/\s+/g, ' ').trim();
/** Whether a recognised page line is the line a fact quotes. */
const quotes = (region, fact) => words(region.text).includes(words(fact.quote)) ||
    (words(fact.quote).includes(words(region.text)) && words(region.text).startsWith(words(fact.name)));

function close() {
  if (!current) return;
  const {panel, opener, onClose} = current;
  current = null;
  panel.remove();
  document.body.classList.remove('has-chat-document');
  onClose?.();
  if (opener?.isConnected) opener.focus({preventScroll: true});
}

/** The page image with highlight boxes from its line map. */
async function pageView(documentId, page, regionId, flagged) {
  const figure = el('figure', null, 'chat-doc-page');
  const stage = el('div', null, 'chat-doc-stage');
  const image = el('img');
  image.alt = `Page ${page} of the document`;
  image.src = `/api/v1/documents/${encodeURIComponent(documentId)}/pages/${page}/original`;
  stage.append(image);
  const caption = el('figcaption', null, 'chat-doc-caption');
  figure.append(stage, caption);
  try {
    const map = await dom().api(`/documents/${encodeURIComponent(documentId)}/pages/${page}/map`);
    // The cited region wins; otherwise lines quoted by values outside their range.
    const marked = regionId ? map.regions.filter(region => String(regionId).split(',').includes(region.id)) :
        map.regions.filter(region => flagged.some(fact => quotes(region, fact)));
    for (const region of marked) {
      const [x, y, w, h] = region.box;
      const box = el('span', null, 'chat-doc-mark');
      Object.assign(box.style, {left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%`});
      stage.append(box);
    }
    if (marked.length) {
      const names = flagged.filter(fact => marked.some(region => quotes(region, fact))).map(fact => fact.name.toLowerCase());
      caption.textContent = regionId ? 'Highlighted: the cited line.' :
          `Highlighted: the ${[...new Set(names)].join(' and ')} line${marked.length > 1 ? 's' : ''}.`;
    }
  } catch { /* The page shows without highlights when it has no line map. */ }
  return figure;
}

/**
 * Opens the panel for a document, replacing any open one.
 * @param {{documentId: string, page: (number|undefined), region: (string|undefined),
 *     onClose: (function()|undefined)}} options
 */
async function open(options) {
  close();
  const opener = document.activeElement;
  const panel = el('aside', null, 'chat-doc-panel');
  panel.setAttribute('aria-label', 'Document');
  panel.tabIndex = -1;
  current = {panel, opener, onClose: options.onClose};
  document.body.classList.add('has-chat-document');
  document.body.append(panel);
  panel.append(el('p', 'Loading', 'chat-doc-status'));
  const id = encodeURIComponent(options.documentId);
  let record, books, facts;
  try {
    [record, books, facts] = await Promise.all([
      dom().api(`/documents/${id}`), dom().api('/notebooks'), dom().api(`/documents/${id}/facts`)]);
  } catch (error) {
    panel.replaceChildren(el('p', error.message, 'chat-doc-status'));
    return panel;
  }
  if (current?.panel !== panel) return panel;
  const head = el('div', null, 'chat-doc-head');
  const titles = el('div', null, 'chat-doc-titles');
  const book = books.notebooks.find(item => item.id === record.notebook_id);
  if (book) titles.append(el('p', book.name, 'chat-doc-notebook'));
  const title = el('h2', record.title);
  title.id = 'chat-doc-title';
  panel.setAttribute('aria-labelledby', title.id);
  const kind = record.media_type === 'application/pdf' ? 'PDF' : 'Image';
  titles.append(title, el('p', `${kind}, ${dom().plural(record.page_count, 'page')}`, 'chat-doc-meta'));
  const actions = el('div', null, 'chat-doc-actions');
  const file = el('a', 'Open PDF', 'coach-button');
  file.href = `/api/v1/documents/${id}/file`; file.target = '_blank'; file.rel = 'noopener';
  if (kind !== 'PDF') file.textContent = 'Open image';
  const shut = dom().button(null, 'coach-button tracker-icon-button');
  shut.setAttribute('aria-label', 'Close document'); shut.append(dom().icon('x'));
  shut.addEventListener('click', close);
  actions.append(file, shut);
  head.append(titles, actions);
  const date = el('dl', null, 'chat-doc-date');
  if (record.record_date) {
    const row = el('div');
    row.append(el('dt', 'Visit or test date'), el('dd', new Date(record.record_date + 'T12:00:00').toLocaleDateString([], {month: 'short', day: 'numeric', year: 'numeric'})));
    date.append(row);
    const evidence = record.date_suggestion?.candidates?.find(item => item.date === record.record_date);
    if (evidence) date.append(el('p', `From page ${evidence.page}: ${evidence.quote}`, 'chat-doc-evidence'));
  }
  const factsHost = el('div', null, 'chat-doc-facts');
  factsHost.addEventListener('report-focus-fact',event=>{
    const fact=event.detail;close();
    root.StellisNavigate(`/document/${encodeURIComponent(record.id)}?page=${fact.page}&fact=${encodeURIComponent(fact.id)}`);
  });
  const flagged = facts.facts.filter(fact => fact.range_status?.tone === 'alert');
  panel.replaceChildren(head, date, factsHost);
  const review=el('a','Review report','coach-button is-quiet');review.href=`/document/${id}`;actions.prepend(review);
  root.StellisHealthFacts.mount(factsHost, record, {compact: true}).catch(() => {});
  panel.append(await pageView(options.documentId, options.page || flagged[0]?.page || 1, options.region, flagged));
  panel.focus({preventScroll: true});
  return panel;
}

// The panel belongs to the chat page; leaving it closes the panel.
window.addEventListener('popstate', () => setTimeout(() => {
  if (!document.body.classList.contains('agent-chat-page')) close();
}));

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && current && current.panel.contains(document.activeElement)) close();
});

root.StellisChatDocument = {open, close};
})(window);
