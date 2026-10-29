/**
 * @fileoverview Renders a tracked item's form from its registry schema
 * (GET /trackers/kinds), so kinds, labels, units and choices come from the
 * server. Supports conditional fields with `when` and notebook/source pickers.
 */
(function(root) {
'use strict';
const dom = () => root.StellisDom;
let nextId = 0;

/** Values of the pickers, loaded once per form. */
async function pickerOptions(fields) {
  const types = new Set(fields.map(field => field.type));
  const [books, records] = await Promise.all([
    types.has('notebook') ? dom().api('/notebooks') : null,
    types.has('document') ? dom().api('/documents') : null,
  ]);
  return {
    notebook: (books?.notebooks || []).filter(book => !book.archived).map(book => [book.id, book.name]),
    document: (records || []).map(record => [record.id, record.title]),
  };
}

/**
 * Builds one control for a field.
 * @return {{wrapper: !Element, read: function(): *, input: !Element}}
 */
function control(field, value, options) {
  const id = `tracker-field-${++nextId}`;
  const wrapper = dom().el(field.type === 'weekdays' ? 'fieldset' : 'label', null,
      'tracker-field' + (['textarea', 'weekdays', 'boolean', 'document'].includes(field.type) ? ' is-wide' : ''));
  wrapper.dataset.field = field.key;
  if (field.type === 'weekdays') {
    wrapper.classList.add('tracker-choices');
    wrapper.append(dom().el('legend', field.label));
    const chosen = new Set(value || []);
    const boxes = field.choices.map((label, index) => {
      const item = dom().el('label', null, 'tracker-weekday');
      const box = dom().el('input'); box.type = 'checkbox'; box.checked = chosen.has(index);
      item.append(box, dom().el('span', label)); wrapper.append(item);
      return box;
    });
    return {wrapper, input: boxes[0], read: () => boxes.flatMap((box, index) => box.checked ? [index] : [])};
  }
  // Name the control by its label text alone; a wrapping label would add the selected option.
  const labelText = dom().el('span', field.label, 'tracker-field-label');
  labelText.id = id + '-label';
  wrapper.append(labelText);
  wrapper.htmlFor = id;
  let input;
  if (['choice', 'notebook', 'document'].includes(field.type)) {
    input = dom().el('select');
    const choices = field.type === 'choice' ?
        field.choices.map(choice => [choice, field.labels?.[choice] || choice]) : options[field.type];
    if (!field.required) input.append(new Option(field.type === 'notebook' ? 'No notebook' : 'None', ''));
    for (const [key, label] of choices) input.append(new Option(label, key));
    input.value = value ?? (field.required ? choices[0]?.[0] ?? '' : '');
  } else if (field.type === 'textarea') {
    input = dom().el('textarea'); input.rows = 3; input.value = value ?? '';
  } else {
    input = dom().el('input');
    input.type = {number: 'number', date: 'date', time: 'time', boolean: 'checkbox'}[field.type] || 'text';
    if (field.type === 'boolean') {
      wrapper.classList.add('is-checkbox'); input.checked = Boolean(value);
    } else input.value = value ?? field.default ?? '';
    if (field.min !== undefined) input.min = String(field.min);
    if (field.max !== undefined && field.type === 'number') input.max = String(field.max);
    if (field.type === 'number') input.step = field.step || '1';
    if (field.type === 'text' && field.max) input.maxLength = field.max;
  }
  if (field.type === 'textarea' && field.max) input.maxLength = field.max;
  input.id = id; input.name = field.key; input.setAttribute('aria-labelledby', labelText.id);
  input.required = Boolean(field.required) && field.type !== 'boolean';
  wrapper.append(input);
  if (field.hint) {
    const hint = dom().el('span', field.hint, 'tracker-field-hint');
    hint.id = id + '-hint'; input.setAttribute('aria-describedby', hint.id); wrapper.append(hint);
  }
  const read = () => field.type === 'boolean' ? input.checked :
      field.type === 'number' ? (input.value === '' ? null : Number(input.value)) : input.value;
  return {wrapper, input, read};
}

/**
 * Opens a modal form for one kind.
 * @param {!Object} kind A schema from GET /trackers/kinds.
 * @param {?Object} existing The stored record when editing.
 * @param {{host: !Element, onSaved: function(), onDelete: (function(): !Promise)=}} options
 */
async function open(kind, existing, options) {
  const trigger = document.activeElement;
  const dialog = dom().el('dialog', null, 'tracker-dialog');
  const title = dom().el('h2', existing ? `Edit ${kind.label.toLowerCase()}` : kind.add);
  title.id = `tracker-dialog-${++nextId}`;
  dialog.setAttribute('aria-labelledby', title.id);
  const close = dom().button(null, 'coach-button tracker-icon-button');
  close.setAttribute('aria-label', 'Close'); close.append(dom().icon('x'));
  close.addEventListener('click', () => dialog.close());
  const header = dom().el('div', null, 'tracker-dialog-head'); header.append(title, close);
  const form = dom().el('form', null, 'tracker-editor');
  const fields = dom().el('div', null, 'tracker-fields');
  const pickers = await pickerOptions(kind.fields);
  const controls = new Map();
  for (const field of kind.fields) {
    const built = control(field, existing?.[field.key], pickers);
    controls.set(field.key, {field, ...built});
    fields.append(built.wrapper);
  }
  // Show a field only while the field it depends on has the listed value.
  const refresh = () => {
    for (const {field, wrapper} of controls.values()) {
      if (!field.when) continue;
      const shown = controls.get(field.when.field)?.read() === field.when.is;
      wrapper.hidden = !shown;
      for (const input of wrapper.querySelectorAll('input,select,textarea')) input.disabled = !shown;
    }
  };
  form.addEventListener('change', refresh); refresh();
  const error = dom().el('p', null, 'tracker-form-error'); error.setAttribute('role', 'alert');
  const footer = dom().el('div', null, 'tracker-dialog-actions');
  const save = dom().el('button', 'Save', 'coach-button is-primary'); save.type = 'submit';
  const cancel = dom().button('Cancel', 'coach-button'); cancel.addEventListener('click', () => dialog.close());
  if (existing && options.onDelete) {
    const remove = dom().button('Delete', 'coach-button is-danger');
    remove.addEventListener('click', async () => {
      if (!root.confirm(`Delete this ${kind.label.toLowerCase()}?`)) return;
      remove.disabled = true;
      try { await options.onDelete(); dialog.close(); } catch (err) { error.textContent = err.message; remove.disabled = false; }
    });
    footer.append(remove);
  }
  footer.append(cancel, save);
  form.append(fields, error, footer);
  dialog.append(header, form);
  // Keep the modal alive when its dashboard widget refreshes asynchronously.
  document.body.append(dialog);
  dialog.addEventListener('close', () => { dialog.remove(); if (trigger?.isConnected) trigger.focus(); }, {once: true});
  form.addEventListener('submit', async event => {
    event.preventDefault(); save.disabled = true; error.textContent = '';
    const data = {};
    for (const [key, {wrapper, read}] of controls) if (!wrapper.hidden) data[key] = read();
    try {
      if (options.onSubmit) await options.onSubmit(data);
      else await dom().api(existing ? `/trackers/records/${encodeURIComponent(existing.id)}` : `/trackers/${kind.key}`,
          {method: existing ? 'PUT' : 'POST', json: data});
      await options.onSaved();
      dialog.close();
    } catch (err) { error.textContent = err.message; save.disabled = false; }
  });
  dialog.showModal();
  controls.values().next().value?.input.focus();
}

root.StellisTrackerForm = {open};
})(window);
