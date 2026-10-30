/**
 * @fileoverview "How Stellis answered": a side panel for one answer's request.
 * It joins the stored trace (model, runtime, timing, tokens, steps) with the
 * answer's own sources and checks from GET /traces/{id}. Values the runtime
 * did not report say so; nothing is filled in.
 */
(function(root) {
'use strict';
const dom = () => root.StellisDom;
const el = (tag, text, cls) => dom().el(tag, text, cls);
let open = null;

const seconds = ms => `${(ms / 1000).toFixed(1)} s`;
const count = value => Number(value).toLocaleString();

/** Token counts in words, from whichever names the runtime used. */
function tokens(usage) {
  const input = usage.prompt_tokens ?? usage.input;
  const output = usage.completion_tokens ?? usage.output;
  if (input == null && output == null) return 'Token counts not reported';
  return [input != null ? `${count(input)} tokens in` : '', output != null ? `${count(output)} out` : ''].filter(Boolean).join(', ');
}

/**
 * A model ID as people read it: "nvidia/nemotron-3-super-120b-a12b" becomes "Nemotron 3 Super 120B".
 * @param {?string} id
 * @return {?string}
 */
function modelName(id) {
  if (!id) return id;
  const base = id.split('/').pop().replace(/[-_]a\d+b$/i, '');
  return base.split(/[-_]/).map(part => /^\d+(\.\d+)?b$/i.test(part) ? part.toUpperCase() : /^nvidia$/i.test(part) ? 'NVIDIA'
    : part.charAt(0).toUpperCase() + part.slice(1)).join(' ').replace(/(\d) (\d)/g, '$1.$2');
}

function tile(value, caption, title) {
  const node = el('div', null, 'trace-tile');
  const strong = el('strong', value);
  if (title && title !== value) strong.title = title;
  node.append(strong, el('span', caption));
  return node;
}

function statusBlock(status) {
  const changed = status === 'changed';
  const label = status === 'unchanged' ? 'Unchanged' : changed ? 'Changed' : status;
  return el('span', label, `status-block ${changed ? 'is-alert' : 'is-ok'}`);
}

/** One step row: name and detail, a bar from the previous step, and its time. */
function step(name, detail, start, at, total, extra) {
  const row = el('li', null, 'trace-step');
  const text = el('div', null, 'trace-step-text');
  text.append(el('strong', name));
  if (detail) text.append(el('span', detail));
  const bar = el('div', null, 'trace-step-bar');
  bar.setAttribute('aria-hidden', 'true');
  const fill = el('span');
  const scale = total || 1;
  fill.style.left = `${Math.min(100, start / scale * 100)}%`;
  fill.style.width = `max(2px, ${Math.min(100, (at - start) / scale * 100)}%)`;
  bar.append(fill);
  row.append(el('span', null, 'trace-step-dot'), text, bar, el('span', seconds(at), 'trace-step-at'));
  if (extra) row.append(extra);
  return row;
}

function sourceList(sources) {
  const list = el('ul', null, 'trace-sublist');
  for (const source of sources) {
    const item = el('li');
    const where = `${source.title}${source.page ? ', page ' + source.page : ''}`;
    const text = el(source.target ? 'a' : 'span', `${where}: "${source.quote}"`);
    if (source.target) text.href = source.target;
    item.append(text, el('span', `Rank ${source.rank}`, 'trace-sublist-side'));
    list.append(item);
  }
  return list;
}

function checkList(checks) {
  const list = el('ul', null, 'trace-sublist');
  for (const check of checks) {
    const item = el('li');
    item.append(el('span', check.label), statusBlock(check.status));
    list.append(item);
  }
  return list;
}

/** Turns stored events into described steps. */
function steps(trace, answer) {
  const begin = Date.parse(trace.created_at);
  const total = trace.duration_ms ?? Math.max(0, ...trace.events.map(e => Date.parse(e.at) - begin));
  const selected = answer?.sources.find(source => source.role === 'selected');
  const related = answer?.sources.filter(source => source.role !== 'selected') || [];
  const list = el('ol', null, 'trace-steps');
  let previous = 0;
  // Model calls before the research step planned the searches; later ones wrote the answer.
  let planning = trace.events.some(event => event.name === 'research_done');
  for (const event of trace.events) {
    const at = Math.max(0, Date.parse(event.at) - begin);
    let name, detail = '', extra = null;
    switch (event.name) {
      case 'request_started': case 'request_completed': previous = at; continue;
      case 'selection_resolved':
        name = 'Line selected';
        detail = selected ? `${selected.title}, page ${event.page_number}` : `Page ${event.page_number}`;
        break;
      case 'sources_selected':
        name = answer?.kind === 'report' ? 'Related pages selected' : 'Sources selected';
        detail = answer?.kind === 'report' ?
            `${dom().plural(event.source_count, 'excerpt')} from other reports in this account` :
            `${dom().plural(event.source_count, 'passage')} from the chosen sources`;
        if (answer?.kind === 'report' && related.length) extra = sourceList(related);
        break;
      case 'model_requested':
        name = planning ? 'Search planned' : 'Model requested';
        detail = `${trace.model || 'Model not reported'} on ${trace.execution}${event.attempt ? ', attempt ' + event.attempt : ''}`;
        break;
      case 'model_replied':
        if (planning) { name = 'Searches written'; detail = 'General terms only, checked before sending'; break; }
        name = 'Model replied';
        detail = answer?.citations ? `Reply with ${answer.citations.total} source markers` : '';
        break;
      case 'citations_checked':
        name = 'Citations checked';
        detail = `Cited ${event.cited_count} of ${dom().plural(event.supplied_count, 'supplied source')}`;
        if (answer?.kind === 'notebook' && related.length) extra = sourceList(related);
        break;
      case 'sources_checked': case 'sources_validated':
        name = 'Sources checked';
        detail = answer?.kind === 'report' ? 'Page revision and related text, compared with now' :
            event.citation_count != null ? `${event.citation_count} citations validated` : '';
        if (answer?.checks.length) extra = checkList(answer.checks);
        break;
      case 'research_provider_fallback':
        name = 'Public source fallback';
        detail = 'No Tavily key is available';
        break;
      case 'research_done': {
        planning = false;
        name = 'Public sources searched';
        detail = `${event.query_count} ${event.query_count === 1 ? 'search' : 'searches'} sent, ${dom().plural(event.result_count, 'result')}` +
            (event.blocked_count ? `, ${event.blocked_count} held back for personal details` : '');
        const queries = answer?.research?.queries || [];
        if (queries.length) {
          extra = el('ul', null, 'trace-sublist');
          for (const query of queries) extra.append(el('li', `“${query.text}” to ${query.providers.join(', ')}`));
        }
        break;
      }
      case 'answer_saved':
        name = answer?.kind === 'report' ? 'Saved to this line' : 'Saved';
        detail = answer?.kind === 'report' ? 'Follow-up questions reuse the selection' : '';
        break;
      case 'safety_gate':
        name = 'Safety rule answered';
        detail = ({emergency: 'Possible emergency', crisis: 'Crisis', worse_after_change: 'Feeling unwell after a medicine change',
          medicine_change: 'Changing a medicine', keep_from_doctor: 'Keeping something from a doctor'}[event.gate] || 'Safety rule') +
          '. A fixed reply; no model was called.';
        break;
      case 'request_failed': name = 'Request failed'; detail = event.error_code || event.error_type || ''; break;
      case 'request_interrupted': name = 'Request stopped'; break;
      default: name = event.name.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
    }
    list.append(step(name, detail, previous, at, total, extra));
    previous = at;
  }
  return {list, total};
}

function render(panel, data) {
  const {trace, answer} = data;
  const when = new Date(answer?.created_at || trace.created_at).toLocaleString([], {month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit'});
  panel.subtitle.textContent = answer ? `${answer.title}, ${when}` : when;
  const tiles = el('div', null, 'trace-tiles');
  // A safety rule's fixed reply calls no model, so it has no model, tokens or citations to show.
  if (trace.kind === 'safety_reply') tiles.append(tile('No model', 'A safety rule answered'), tile(seconds(trace.duration_ms || 0), 'Time taken'));
  else tiles.append(
      tile(modelName(trace.model) || 'Not reported', trace.model_source === 'reported' ? 'Reported by the runtime' : 'Configured model', trace.model),
      tile(trace.execution + (trace.provider ? `, ${trace.provider}` : ''), 'Where the request ran'),
      tile(trace.duration_ms == null ? (trace.state === 'running' ? 'Running' : 'Not recorded') : seconds(trace.duration_ms), tokens(trace.usage)));
  if (answer?.citations && trace.kind !== 'safety_reply') tiles.append(tile(`${answer.citations.found} of ${answer.citations.total}`, 'Citations point to supplied sources'));
  const {list, total} = steps(trace, answer);
  const head = el('div', null, 'trace-steps-head');
  head.setAttribute('aria-hidden', 'true');
  head.append(el('span', 'Step'), el('span', `0 to ${seconds(total)}`), el('span', 'At'));
  const footer = el('div', null, 'trace-footer');
  const copy = dom().button('Copy request ID', 'coach-button');
  copy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(trace.id); copy.textContent = 'Copied'; } catch { copy.textContent = 'Copy failed'; }
  });
  const all = el('a', 'All requests in Settings', 'coach-button trace-settings-link');
  all.href = '/settings#inference-traces';
  footer.append(copy, all, el('code', trace.id.slice(0, 8), 'trace-id'));
  panel.body.replaceChildren(tiles, head, list, footer);
}

/**
 * Opens the panel for one answer.
 * @param {{traceId: (string|undefined), jobId: (string|undefined)}} ref
 */
async function show(ref) {
  open?.close();
  const opener = document.activeElement;
  const dialog = el('dialog', null, 'answer-trace');
  open = dialog;
  const head = el('div', null, 'trace-head');
  const titles = el('div');
  const heading = el('h2', 'How Stellis answered'); heading.id = 'answer-trace-title';
  const subtitle = el('p', null, 'trace-subtitle');
  titles.append(heading, subtitle);
  const close = dom().button(null, 'coach-button tracker-icon-button');
  close.setAttribute('aria-label', 'Close'); close.append(dom().icon('x'));
  close.addEventListener('click', () => dialog.close());
  head.append(titles, close);
  const body = el('div', null, 'trace-body');
  const status = el('p', 'Loading', 'trace-status'); status.setAttribute('role', 'status');
  body.append(status);
  dialog.setAttribute('aria-labelledby', heading.id);
  dialog.append(head, body);
  document.body.append(dialog);
  dialog.addEventListener('close', () => { dialog.remove(); if (open === dialog) open = null; if (opener?.isConnected) opener.focus(); }, {once: true});
  dialog.showModal();
  try {
    const data = await dom().api(ref.traceId ? `/traces/${encodeURIComponent(ref.traceId)}` : `/traces/job/${encodeURIComponent(ref.jobId)}`);
    if (dialog.open) render({body, subtitle}, data);
  } catch (error) { status.textContent = error.message; }
  return dialog;
}

/** A text link that opens the panel; null without a trace or job. */
function link(ref) {
  if (!ref.traceId && !ref.jobId) return null;
  const node = dom().button('How Stellis answered', 'trace-link');
  node.addEventListener('click', () => show(ref));
  return node;
}

root.StellisAnswerTrace = {open: show, link};
})(window);
