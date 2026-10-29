/**
 * @fileoverview Setup's About you step: one short form of tap-first answers in place of the setup
 * call (owner, 2026-10-28). Each answer saves as it changes; conditions and medicines use the shared
 * answer picker with common answers as taps; every goal is measurable, with steppers for its amount
 * and days a week.
 */
(function(root) {
'use strict';

const MAX_AREAS = 5;

function render(scene, kit) {
  const {node, api, button} = kit;
  scene.classList.add('setup-form');
  const card = node('section', null, 'setup-form-card widget-paper');
  scene.append(node('h1', 'About you'), card);
  const timers = {};
  let pickers = [];

  // Saves one answer; typed answers wait until the person pauses.
  function save(field, value, wait = 0) {
    clearTimeout(timers[field]);
    timers[field] = setTimeout(() => {
      delete timers[field];
      api('/onboarding/profile', 'PUT', {field, value}).catch(e => kit.error(e.message));
    }, wait);
  }
  async function flush() {
    for (const picker of pickers) picker.commit();
    const pending = Object.keys(timers);
    for (const field of pending) clearTimeout(timers[field]);
    await Promise.all(pending.map(field => api('/onboarding/profile', 'PUT', {field, value: latest[field]})));
  }
  const latest = {};
  const change = (field, value, wait) => { latest[field] = value; save(field, value, wait); };

  function section(title, hint) {
    const box = node('fieldset', null, 'setup-form-section');
    box.append(node('legend', title));
    if (hint) box.append(node('p', hint, 'setup-form-hint'));
    card.append(box);
    return box;
  }
  function pill(text, pressed, onPress) {
    const item = node('button', text, 'setup-pill');
    item.type = 'button';
    item.setAttribute('aria-pressed', String(pressed));
    item.addEventListener('click', onPress);
    return item;
  }
  function pills(label) {
    const row = node('div', null, 'setup-pills');
    row.setAttribute('role', 'group');
    row.setAttribute('aria-label', label);
    return row;
  }
  // One answer from a short list; pressing the chosen one again clears it.
  function single(box, field, answer) {
    const row = pills(box.querySelector('legend').textContent);
    const paint = () => row.querySelectorAll('.setup-pill').forEach(b => b.setAttribute('aria-pressed', String(b.textContent === latest[field])));
    latest[field] = answer[field] ?? null;
    for (const choice of answer.choices) row.append(pill(choice, false, () => {
      change(field, latest[field] === choice ? null : choice);
      paint();
    }));
    paint();
    box.append(row);
  }

  function nameAndDate(answers) {
    const box = section('You');
    const row = node('div', null, 'setup-form-row');
    const field = (labelText, type, key) => {
      const label = node('label', null, 'setup-form-field');
      const input = node('input');
      input.type = type;
      input.value = answers[key] || '';
      if (type === 'date') input.max = new Date().toISOString().slice(0, 10);
      else { input.autocomplete = 'given-name'; input.maxLength = 100; }
      latest[key] = input.value;
      input.addEventListener('input', () => {
        // A half-typed date is not a date yet.
        if (type === 'date' && input.value && !input.checkValidity()) return;
        change(key, input.value || null, 500);
      });
      label.append(node('span', labelText), input);
      return label;
    };
    row.append(field('What should Stellis call you?', 'text', 'name'), field('Birth date', 'date', 'birth_date'));
    box.append(row);
  }

  function goals(field, answers) {
    const box = section('Goals to start with', field.hint);
    const byId = Object.fromEntries(field.choices.map(choice => [choice.id, choice]));
    const chosen = new Map((answers.goals || []).filter(g => g && byId[g.id]).map(g => [g.id, {...g}]));
    const row = pills('Goals');
    const editors = node('div', null, 'setup-goal-editors');
    const days = (choice, n) => n === 7 ? 'every day'
      : `${n} ${choice.activity === 'sleep' ? 'night' : 'day'}${n === 1 ? '' : 's'} a week`;
    const unit = (choice, amount) => `${amount} ${choice.unit}`;
    function stepper(text, label, onStep, atMin, atMax) {
      const group = node('span', null, 'setup-stepper');
      group.setAttribute('role', 'group');
      group.setAttribute('aria-label', label);
      const less = node('button', '−', 'setup-step');
      less.type = 'button'; less.disabled = atMin; less.setAttribute('aria-label', 'Less');
      less.addEventListener('click', () => onStep(-1));
      const more = node('button', '+', 'setup-step');
      more.type = 'button'; more.disabled = atMax; more.setAttribute('aria-label', 'More');
      more.addEventListener('click', () => onStep(1));
      const value = node('output', text);
      value.setAttribute('aria-live', 'polite');
      group.append(less, value, more);
      return group;
    }
    function commit() {
      change('goals', [...chosen.values()].map(g => ({id: g.id, amount: g.amount, times_per_week: g.times_per_week})));
      paint();
    }
    // A chosen goal leaves the pills for its own row, so its target shows once, as set.
    function paint() {
      editors.replaceChildren(...[...chosen.values()].map(goal => {
        const choice = byId[goal.id];
        const line = node('div', null, 'setup-goal-editor');
        line.setAttribute('role', 'group');
        line.setAttribute('aria-label', choice.name);
        line.append(node('strong', choice.name));
        if (choice.amounts.length) {
          const at = choice.amounts.indexOf(goal.amount);
          line.append(stepper(unit(choice, goal.amount), 'Amount', step => {
            goal.amount = choice.amounts[at + step]; commit();
          }, at <= 0, at >= choice.amounts.length - 1));
        }
        line.append(stepper(days(choice, goal.times_per_week), 'Days a week', step => {
          goal.times_per_week += step; commit();
        }, goal.times_per_week <= 1, goal.times_per_week >= 7));
        const remove = node('button', null, 'setup-step setup-goal-remove');
        remove.type = 'button';
        remove.setAttribute('aria-label', 'Remove ' + choice.name);
        remove.append(kit.icon('x'));
        remove.addEventListener('click', () => { chosen.delete(goal.id); commit(); });
        line.append(remove);
        return line;
      }));
      row.replaceChildren(...field.choices.filter(choice => !chosen.has(choice.id)).map(choice => pill(choice.label, false, () => {
        chosen.set(choice.id, {id: choice.id, amount: choice.amount, times_per_week: choice.days});
        commit();
      })));
    }
    latest.goals = [...chosen.values()];
    paint();
    box.append(editors, row);
  }

  // Everyday and life-stage areas show first; the clinical ones wait behind More areas.
  const FIRST_AREAS = new Set(['Everyday Health', 'Reproductive and Life-Stage Health']);
  function areas(field, answers) {
    const box = section('Other areas to keep track of', `Up to ${MAX_AREAS}`);
    const chosen = new Set(answers.focus || []);
    let all = field.choices.some(choice => chosen.has(choice.id) && !FIRST_AREAS.has(choice.group));
    const row = pills('Other areas');
    const paint = () => {
      const shown = field.choices.filter(choice => all || FIRST_AREAS.has(choice.group) || chosen.has(choice.id));
      row.replaceChildren(...shown.map(choice => {
        const item = pill(choice.label, chosen.has(choice.id), () => {
          chosen.has(choice.id) ? chosen.delete(choice.id) : chosen.add(choice.id);
          change('focus', [...chosen]);
          paint();
        });
        item.disabled = !chosen.has(choice.id) && chosen.size >= MAX_AREAS;
        return item;
      }));
      if (!all) {
        const more = node('button', 'More areas', 'setup-pill setup-pill-more');
        more.type = 'button';
        more.addEventListener('click', () => { all = true; paint(); row.children[shown.length]?.focus(); });
        row.append(more);
      }
    };
    latest.focus = [...chosen];
    paint();
    box.append(row);
  }

  async function build() {
    const {answers, fields} = await api('/onboarding/profile');
    const byId = Object.fromEntries(fields.map(field => [field.id, field]));
    nameAndDate(answers);
    single(section('Gender'), 'gender', {...byId.gender, gender: answers.gender});
    single(section('Track your cycle?', 'If you have a period'), 'cycle_tracking', {...byId.cycle_tracking, cycle_tracking: answers.cycle_tracking});
    for (const key of ['conditions', 'medicines']) {
      const field = byId[key];
      const box = section(key === 'conditions' ? field.question : 'Medicines and supplements', field.hint);
      const picker = root.StellisIntakeLibrary.answerPicker({
        node, api, category: field.library, noneLabel: field.choices[0], value: answers[key] || '',
        label: key === 'conditions' ? 'Search conditions' : 'Search medicines and supplements',
        suggestions: field.suggestions || [], onChange: value => change(key, value || null, 400)});
      picker.element.querySelector('label').classList.add('visually-hidden');
      latest[key] = picker.value();
      pickers.push(picker);
      box.append(picker.element);
    }
    goals(byId.goals, answers);
    areas(byId.focus, answers);
    const actions = node('div', null, 'setup-actions');
    actions.append(button('Continue', async () => { await flush(); kit.next(); }, 'primary'),
                   button('Back', async () => { await flush(); kit.back(); }));
    scene.append(actions);
  }

  build().catch(e => kit.error(e.message));
  return {leave() { pickers.forEach(picker => picker.destroy()); pickers = []; }};
}

root.StellisSetupForm = {render};
})(window);
