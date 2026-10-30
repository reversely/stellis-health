const $ = selector => document.querySelector(selector);
let current;
let busy = false;
const draftRoutes = new Map();
const kindLabels = {report_page: 'Report page', document_ocr: 'Text recognition', agent_request: 'Agent request', onboarding_plan: 'Notebook proposals', agent_chat: 'Agent chat', agent_plan: 'Work planning', agent_review: 'Agent review',
  answer: 'Transcript question', summary: 'Summary', clean: 'Transcript cleanup', agent: 'To-do extraction', categorization: 'Category scoring', safety_reply: 'Safety reply'};
const stateLabels = {running: 'Running', succeeded: 'Completed', failed: 'Failed', interrupted: 'Interrupted'};
const eventLabels = {page_started: 'Page started', page_completed: 'Page completed', request_started: 'Request started', sources_selected: 'Sources selected',
  model_requested: 'Model requested', model_replied: 'Model replied', answer_retry: 'Answer format retry',
  sources_validated: 'Source citations validated', answer_validated: 'Answer validated',
  structured_reply_received: 'Structured reply received', category_scored: 'Categories scored',
  request_completed: 'Request completed', request_failed: 'Request failed', request_interrupted: 'Request interrupted'};
const checkLabels = {available: 'Model available', model_missing: 'Selected model not found',
  unavailable: 'Unavailable', not_configured: 'Not configured', reported: 'Route reported by runtime',
  mismatch: 'Managed runtime uses a different model', remote_unverified: 'Agent route not yet verified'};
Object.assign(checkLabels,{voice_unverified:'Speech endpoint available; requested voice not yet verified',voice_available:'Requested voice listed by server',voice_missing:'Requested voice not found',local_unverified:'Local speech not probed',invalid_response:'Voice listing could not be read'});
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
async function request(path = '', options = {}) {
  const response = await fetch('/api/v1/settings/inference' + path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Request failed. Try again.');
  return body;
}
function time(value) { return new Date(value).toLocaleString(); }
function route(title, execution, model, state) {
  const row = element('div', undefined, 'inference-route');
  row.append(element('strong', title));
  const details = element('div');
  details.append(element('div', execution), element('code', model || 'Model not reported'),
    element('p', state, 'settings-note'));
  row.append(details);
  return row;
}
function editableRoute(entry, data) {
  const model = draftRoutes.has(entry.role) ? draftRoutes.get(entry.role) : entry.model;
  const observed = entry.observed;
  const state = observed ? (checkLabels[observed.state] || observed.state) +
    (observed.model ? `: ${observed.model}` : '') : entry.model ? 'Configured but not checked' : 'Not configured';
  const row = route(entry.label, entry.role === 'agent' ? data.agent_execution : data.inference_provider, entry.model, state);
  row.dataset.role = entry.role;
  const form = element('form', undefined, 'inference-route-editor');
  const input = element(data.local_inference ? 'select' : 'input'); input.setAttribute('aria-label', entry.label + ' model');
  if (data.local_inference) {
    const values = [...new Set([entry.model, ...(data.observed?.models || [])].filter(Boolean))];
    for (const value of values) { const option = element('option', value); option.value = value; input.append(option); }
  } else { input.type = 'text'; input.maxLength = 200; input.placeholder = 'Token Factory model ID'; input.setAttribute('list', 'inference-recommended'); }
  input.value = model || ''; input.required = true;
  input.disabled = !entry.editable || data.local_inference && !data.observed?.models.length;
  const save = element('button', 'Save'); save.type = 'submit';
  const update = () => {draftRoutes.set(entry.role,input.value);save.disabled=busy || input.disabled || !input.value || input.value === entry.model;};
  input.addEventListener('input', update); input.addEventListener('change', update);
  save.disabled = busy || input.disabled || !model || model === entry.model;
  form.append(input,save);
  form.addEventListener('submit', async event => {
    event.preventDefault(); const selected=input.value;
    await operate(save, $('#inference-check-status'), `Saving ${entry.label}…`, async () => {
      const result=await request('/routes',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({routes:{[entry.role]:selected}})});
      if(draftRoutes.get(entry.role)===selected)draftRoutes.delete(entry.role);
      return result;
    });
  });
  row.lastElementChild.append(form);
  if (!entry.editable) row.lastElementChild.append(element('p','The deployment owner manages this route','settings-note'));
  else if (input.disabled) row.lastElementChild.append(element('p','Check connections to load installed models.','settings-note'));
  return row;
}
// The deployment's shared AI budget: what is spent, and for the owner a switch for its limit (owner, 2026-10-30).
function budgetRow(data) {
  const budget = data.budget;
  if (!budget) return [];
  const money = value => '$' + value.toFixed(2);
  const spent = budget.enabled && budget.spent_usd >= budget.limit_usd;
  const row = element('div', undefined, 'inference-route');
  row.append(element('strong', 'AI budget'));
  const details = element('div');
  details.append(element('div', budget.enabled ? `${money(budget.spent_usd)} of ${money(budget.limit_usd)} spent` : `${money(budget.spent_usd)} spent, no limit`),
    element('p', spent ? 'Spent. New answers are paused; stored answers can still be opened.'
      : 'Shared by every account and demo visitor. When it is spent, new answers pause and stored answers stay open.', 'settings-note'));
  if (data.operator) {
    const toggle = element('button', budget.enabled ? 'Turn off the limit' : 'Turn on the limit');
    toggle.type = 'button';
    toggle.addEventListener('click', () => operate(toggle, $('#inference-check-status'), 'Saving the budget…',
      async () => { await request('/budget', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: !budget.enabled})}); return request(); }));
    const actions = element('div', undefined, 'settings-actions'); actions.append(toggle);
    details.append(actions);
  }
  row.append(details);
  return [row];
}
function render(data) {
  current = data;
  const observed = data.observed;
  const agent = observed?.agent;
  // Nemotron suggestions for the Token Factory route fields; any listed model ID still works.
  const suggested = element('datalist'); suggested.id = 'inference-recommended';
  for (const item of data.recommended || []) {
    const option = element('option'); option.value = item.model; option.label = item.label; suggested.append(option);
  }
  const recommended = (data.recommended || []).length ? [suggested, element('p',
    'Super 120B gives the most grounded answers',
    'settings-note')] : [];
  $('#inference-routes').replaceChildren(
    ...budgetRow(data),
    ...recommended,
    ...data.routes.map(entry => editableRoute(entry, data)),
    route('Live transcription', data.speech_execution, 'Local Parakeet speech worker',
      data.speech?.live_installed ? 'Worker and model files installed' : 'Not configured'),
    route('Saved transcription', data.speech_execution, 'Whisper + local speaker models',
      data.speech?.transcribe && data.speech?.diarize ? 'Model files installed' : 'Not configured'),
    route('Call voice',data.tts_execution,data.tts?.voice,
      observed?.tts ? (checkLabels[observed.tts.state]||observed.tts.state) : data.tts?.configured ? 'Configured but not checked' : 'Not configured'));
  $('#inference-checked-at').textContent = observed ? 'Last checked ' + time(observed.checked_at) : 'No connection check yet';
  $('#local-model-controls').hidden = true;
  const select = $('#local-model-select');
  const wanted = select.value || data.inference_model;
  select.replaceChildren(element('option', observed?.models.length ? 'Choose a model' : 'Check connections to load models'));
  select.firstElementChild.value = '';
  for (const model of data.local_inference ? observed?.models || [] : []) {
    const option = element('option', model);
    option.value = model; select.append(option);
  }
  if ([...select.options].some(option => option.value === wanted)) select.value = wanted;
  select.disabled = busy || !data.can_switch || !observed?.models.length;
  updateApply();
  const open = new Set([...document.querySelectorAll('#inference-traces details[open]')].map(node => node.dataset.traceId));
  const rows = data.traces.map(trace => {
    const details = element('details', undefined, 'inference-trace'); details.dataset.traceId = trace.id;
    details.open = open.has(trace.id);
    const summary = element('summary');
    const title = (kindLabels[trace.kind] || trace.kind) + (trace.agent_name ? ', ' + trace.agent_name : '');
    summary.append(element('strong', title), element('span', stateLabels[trace.state] || trace.state, 'trace-state'),
      element('span', trace.duration_ms == null ? 'Duration unavailable' : (trace.duration_ms / 1000).toFixed(1) + ' s', 'trace-duration'));
    details.append(summary);
    details.append(element('p', time(trace.created_at) + ', ' + trace.execution, 'settings-note'));
    details.append(element('p', (trace.model || 'Model not reported') +
      (trace.model ? (trace.model_source === 'reported' ? ' (reported)' : ' (configured)') : '') +
      (trace.provider ? ', ' + trace.provider : ''), 'trace-model'));
    const tokens = Object.entries(trace.usage).map(([key, count]) => key.replaceAll('_', ' ') + ': ' + count).join(', ');
    details.append(element('p', tokens || 'Token usage not reported', 'settings-note'));
    const list = element('ol', undefined, 'trace-events');
    for (const event of trace.events) {
      let label = eventLabels[event.name] || event.name;
      if (event.attempt != null) label += ', attempt ' + event.attempt;
      if (event.source_count != null) label += ': ' + event.source_count;
      if (event.citation_count != null) label += ': ' + event.citation_count;
      if (event.reads != null) label += ': ' + event.reads + ' reads';
      const failures = {timeout: 'Request timed out', network: 'Provider unreachable', authentication: 'Credential rejected',
        rate_limited: 'Rate limit reached', model_unavailable: 'Model unavailable', invalid_json: 'Invalid structured response',
        output_limit: 'Output limit reached', not_configured: 'Inference not configured', provider: 'Provider error'};
      if (event.error_code) label += ': ' + (failures[event.error_code] || 'Request failed');
      else if (event.error_type) label += ' (' + event.error_type + ')';
      const item = element('li');
      const elapsed = Math.max(0, (new Date(event.at) - new Date(trace.created_at)) / 1000);
      item.append(element('span', label), element('span', elapsed.toFixed(1) + ' s', 'trace-duration'));
      list.append(item);
    }
    details.append(list);
    const id = element('small', 'Request ' + trace.id); details.append(id);
    return details;
  });
  $('#inference-traces').replaceChildren(...(rows.length ? rows : [element('p', 'No requests recorded yet', 'settings-note')]));
  $('#inference-clear').disabled = busy || !data.traces.some(trace => trace.state !== 'running');
}
function updateApply() {
  const value = $('#local-model-select').value;
  $('#local-model-apply').disabled = busy || !value || current?.traces.some(trace => trace.state === 'running') ||
    (value === current?.inference_model && value === current?.observed?.agent?.model);
}
export async function load() {
  try { render(await request()); $('#inference-trace-status').textContent = ''; }
  catch (error) { $('#inference-trace-status').textContent = error.message; }
}
async function operate(button, status, message, action) {
  if (busy) return;
  busy = true; button.disabled = true; status.textContent = message; updateApply();
  document.querySelectorAll('.inference-route-editor button').forEach(item => item.disabled = true);
  try { render(await action()); status.textContent = 'Done'; }
  catch (error) { status.textContent = error.message; }
  finally { busy = false; button.disabled = false; if (current) render(current); }
}
$('#local-model-select').addEventListener('change', updateApply);
$('#inference-refresh').addEventListener('click', () => load());
$('#inference-check').addEventListener('click', event => operate(event.currentTarget,
  $('#inference-check-status'), 'Checking connections…', () => request('/check', {method: 'POST'})));
$('#local-model-apply').addEventListener('click', event => {
  const model = $('#local-model-select').value;
  operate(event.currentTarget, $('#local-model-status'), 'Loading and verifying model…', () =>
    request('/model', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({model})}));
});
$('#inference-clear').addEventListener('click', event => operate(event.currentTarget,
  $('#inference-trace-status'), 'Clearing completed history…', () => request('/traces', {method: 'DELETE'})));
