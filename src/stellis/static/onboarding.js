/**
 * @fileoverview First-run setup as four steps in order: a welcome, a short
 * form about the person's health (onboarding-form.js), adding records, and
 * reviewing the notebooks proposed from the answers and the records, each with
 * its goal. Nothing is filed until the person finishes the review.
 */
(() => {
  const main = document.querySelector('#setup-main');
  const error = document.querySelector('#setup-error');
  /** Proposed notebooks from records; more would overwhelm a first run. */
  const MAX_NOTEBOOKS = 100;
  let state;
  let taskChoices = {};
  let saves = Promise.resolve();
  let reviewLayout = null;
  let reviewSaveTimer = null;
  function scheduleReviewSave() {
    clearTimeout(reviewSaveTimer);
    reviewSaveTimer = setTimeout(() => save('review').catch(e => {error.textContent=e.message;}),300);
  }
  /** The About you step, while it is on screen. */
  let formStep = null;
  let leaveIntro = null;
  /** Records added during this setup, newest last. */
  const added = [];

  const node = (tag, text, cls) => {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (cls) element.className = cls;
    return element;
  };
  const icon = (name) => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(svg.namespaceURI, 'use');
    use.setAttribute('href', '#i-' + name);
    svg.append(use);
    return svg;
  };
  const avatar = (name, coordinator = false, size = 40) =>
      window.StellisAgentAvatar.avatarElement(
          document, {seed: name, coordinator, size});
  const plural = (count, noun) => `${count} ${noun}${count === 1 ? '' : 's'}`;

  async function api(path, method = 'GET', body) {
    const response = await fetch('/api/v1' + path, {
      method,
      headers: body ? {'Content-Type': 'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    // An expired session goes back to sign-in, as on every other page.
    if (response.status === 401) {
      location.replace('/auth?next=/onboarding');
      throw Error('Sign in to continue');
    }
    const data = await response.json();
    if (!response.ok) {
      throw Error(
          typeof data.detail === 'string' ?
              data.detail :
              'Setup could not finish. Try again.');
    }
    return data;
  }

  function button(text, action, cls = 'quiet') {
    const result = node('button', text, cls);
    result.type = 'button';
    result.addEventListener('click', async () => {
      result.disabled = true;
      error.textContent = '';
      try {
        await action();
      } catch (e) {
        error.textContent = e.message;
        error.scrollIntoView({block: 'nearest'});
      } finally {
        if (result.isConnected) result.disabled = false;
      }
    });
    return result;
  }

  function save(step) {
    clearTimeout(reviewSaveTimer);
    const payload = {
      uses: [],
      step,
      task_choices: structuredClone(taskChoices),
      notebook_choices: reviewLayout ? structuredClone(reviewLayout.groups) : undefined,
    };
    saves = saves.catch(() => {}).then(async () => {
      state = await api('/onboarding', 'PUT', payload);
    });
    return saves;
  }

  /** Saves the step and shows it. */
  async function go(step) {
    await save(step);
    history.replaceState({}, '', '/onboarding');
    render();
  }

  function render(focus = true) {
    leaveIntro?.();
    leaveIntro = null;
    formStep?.leave();
    formStep = null;
    error.textContent = '';
    main.replaceChildren();
    window.StellisMeet.progress(document.querySelector('.setup-header'),state,{node});
    const review = state.step === 'review';
    document.body.classList.toggle('onboarding-review', review);
    document.body.classList.toggle('onboarding-welcome', state.step === 'welcome');
    document.querySelector('#setup-rail').hidden = !review;
    const scene = node('section', null, 'setup-scene');
    main.append(scene);
    if(['call','health','records','review'].includes(state.step)){const steps=node('ol',null,'setup-substeps');steps.setAttribute('aria-label','Set up your workspace');const active={call:0,health:0,records:1,review:2}[state.step];['About you','Add records','Check notebooks'].forEach((label,i)=>{const item=node('li',`${i+1} ${label}`);if(i===active)item.setAttribute('aria-current','step');steps.append(item);});scene.append(steps);}
    if (state.step === 'welcome') {
      welcome(scene);
    } else if ((state.step === 'call' || state.step === 'health')) {
      formStep = window.StellisSetupForm.render(scene, {
        api,
        node,
        icon,
        button,
        error: (text) => {
          error.textContent = text;
        },
        next: () => go('records'),
        back: () => go('welcome'),
      });
    } else if (review) {
      reviewScreen(scene);
    } else {
      recordsScreen(scene);
    }
    if (focus) {
      main.focus();
      window.scrollTo(0, 0);
    }
  }

  /** Step 1: what the product does and who works in it. */
  function welcome(scene) {
    scene.classList.add('setup-welcome');
    const index=Math.max(0,Math.min(3,Math.trunc(Number(new URL(location.href).searchParams.get('intro'))||0)));
    leaveIntro = window.StellisMeet.render(scene,{node,icon,avatar,button,api,start:()=>go('call'),advance:index=>{history.replaceState({},'', '/onboarding?intro='+index);render();}},index);
  }

  /** Step 2: the person drops their files; each upload is listed. */
  function recordsScreen(scene) {
    scene.append(
        node('h1', 'Add your records'),
        node(
            'p',
            'Letters, lab results, scans and recordings all work, and ' +
                'Stellis files them with the notebooks from your answers.',
            'setup-hint'));
    const drop = node('div', null, 'setup-drop');
    drop.tabIndex = 0;
    drop.setAttribute('role', 'button');
    drop.append(
        icon('upload'), node('strong', 'Drop your health files here'),
        node('span', 'Choose files: PDF, PNG, JPEG or MP3'));
    const picker = node('input');
    picker.type = 'file';
    picker.multiple = true;
    picker.accept = window.StellisRecordUpload.ACCEPT;
    picker.hidden = true;
    const list = node('ul', null, 'setup-uploads');
    list.setAttribute('aria-live', 'polite');
    list.setAttribute('aria-label','Records available for review');
    const paintList = () => list.replaceChildren(
        ...added.map((item) => node('li', `${item.name}: ${item.status}`)));
    paintList();
    // One way on: Add later until a record is added, then Continue. A greyed-out Continue read as broken.
    const next = button('Add later', () => go('review'), 'primary');
    const paintNext = () => {next.textContent = added.some((item) => item.ok) ? 'Continue' : 'Add later';};
    async function upload(files) {
      for (const file of files) {
        const item = {name: file.name, status: 'uploading', ok: false};
        added.push(item);
        paintList();
        const result = await window.StellisRecordUpload.uploadFile(file, null);
        item.id = result.record?.id;
        item.ok = result.outcome === 'added' || result.outcome === 'duplicate';
        item.status = result.message;
        paintList();
      }
      paintNext();
    }
    Promise.all([api('/documents'),api('/conversations')]).then(([documents,conversations])=>{
      if(!list.isConnected)return;
      for(const record of [...documents,...conversations].filter(r=>!r.notebook_id)){
        if(added.some(item=>item.id===record.id))continue;
        added.push({id:record.id,name:record.filename||record.title,status:record.status==='needs_text'?'needs text':'available for review',ok:true});
      }
      paintList();paintNext();
    }).catch(()=>{if(list.isConnected)error.textContent='Saved records could not load. Reload to try again.';});
    drop.addEventListener('click', () => picker.click());
    drop.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        picker.click();
      }
    });
    picker.addEventListener('change', () => upload([...picker.files]));
    drop.addEventListener('dragover', (event) => {
      event.preventDefault();
      drop.classList.add('is-over');
    });
    drop.addEventListener('dragleave', () => drop.classList.remove('is-over'));
    drop.addEventListener('drop', (event) => {
      event.preventDefault();
      drop.classList.remove('is-over');
      upload([...event.dataTransfer.files]);
    });
    const content=node('div',null,'setup-record-layout');const files=node('section');files.append(drop,picker,list);const help=node('aside',null,'setup-record-help widget-paper');help.append(node('h2','What happens next'),node('p','The original file is kept. You check the notebook suggestions before anything is filed.'));content.append(files,help);scene.append(content);
    if (!state.managed) {const advanced=node('details',null,'setup-advanced');advanced.append(node('summary','Connections and runtime'),connections(),runtimePanel());scene.append(advanced);}
    const actions = node('div', null, 'setup-actions');
    actions.append(next, button('Back', () => go('call')));
    scene.append(actions);
  }

  function runtimePanel() {
    const panel = node('section', null, 'setup-runtime');
    panel.append(
        node('h2', state.runtime.local_inference ? 'Local AI' : 'Cloud AI'),
        node(
            'p',
            state.runtime.local_inference ?
                'AI responses run on your connected computer without a ' +
                    'cloud AI service' :
                state.runtime.profile === 'cloud' ?
                    'Connect your NemoClaw gateway and Token Factory key' :
                    'Connect your Token Factory key'));
    const result = node(
        'p', state.ready ? 'AI is ready' : 'Test that Stellis can respond',
        'setup-hint');
    result.role = 'status';
    panel.append(result, button('Check setup', async () => {
                   result.textContent = 'Checking AI responses';
                   try {
                     const checked = await api('/onboarding/check', 'POST');
                     state.ready = checked.ready;
                     result.textContent = 'AI is ready';
                   } catch (e) {
                     result.textContent = e.message;
                     throw e;
                   }
                 }));
    return panel;
  }

  function keyRow(label, kind, description) {
    const row = node('div', null, 'setup-connection');
    const heading = node('div');
    heading.append(node('h3', label), node('p', description));
    const form = node('form');
    const input = node('input');
    input.type = 'password';
    input.autocomplete = 'off';
    input.maxLength = 512;
    input.required = true;
    input.placeholder = kind === 'tavily' ? 'tvly-…' : 'API key';
    input.setAttribute('aria-label', label + ' API key');
    const submit = node('button', 'Save');
    submit.type = 'submit';
    form.append(input, submit);
    const status = node('p', '', 'connection-status');
    status.role = 'status';
    row.append(heading, form, status);
    api('/settings/' + kind + '-key')
        .then((data) => {
          status.textContent = data.configured ? 'Connected' : '';
        })
        .catch((e) => {
          status.textContent = e.message;
        });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      submit.disabled = true;
      status.textContent = 'Saving';
      try {
        const data =
            await api('/settings/' + kind + '-key', 'PUT', {key: input.value});
        input.value = '';
        status.textContent = data.message || 'Connected';
      } catch (e) {
        status.textContent = e.message;
      } finally {
        submit.disabled = false;
      }
    });
    return row;
  }

  /** Keys a self-hosted deployment needs before Stellis can respond. */
  function connections() {
    const panel = node('section', null, 'setup-connections');
    panel.setAttribute('aria-label', 'Connections');
    panel.append(keyRow('Tavily', 'tavily', 'For deep research'));
    if (!state.runtime.local_inference) {
      panel.append(
          keyRow('Nebius Token Factory', 'nebius', 'For cloud inference'));
    }
    return panel;
  }

  /** Labels for the kind of subject each notebook covers. */
  const KIND_LABELS = {
    condition: 'Condition',
    treatment: 'Treatment',
    objective: 'Goal',
    symptom: 'Symptom',
    recovery: 'Recovery',
  };
  /** The select value for records left out of every notebook. */
  const NOT_FILED = 'not-filed';

  /**
   * Turns a reply from POST /intake/notebooks into the layout the review
   * screen edits, keeping task choices for notebooks that keep their name.
   * @param {!Object} result
   * @param {?Object} before The layout the reply revises, or null.
   * @return {!Object} {groups, unsorted}
   */
  function layoutFrom(result, before) {
    const tasks = new Map(
        (before?.groups || [])
            .map(
                (group) =>
                    [group.notebook_name.toLowerCase(), group.suggest_tasks]));
    const groups =
        result.groups.slice(0, MAX_NOTEBOOKS)
            .map(
                (group, index) => ({
                  ...group,
                  proposed_name: group.notebook_name,
                  include: true,
                  suggest_tasks: tasks.get(group.notebook_name.toLowerCase()) ??
                      taskChoices['records-' + index] ?? true,
                }));
    return {groups, unsorted: result.unsorted || []};
  }

  /**
   * One record in the layout, with a select that moves it to another
   * notebook or out of every notebook.
   */
  function recordRow(item, here, layout, move) {
    const row = node('li', null, 'setup-layout-record');
    const text = node('span', null, 'setup-layout-record-title');
    text.append(icon('file-text'), node('span', item.title));
    if (item.date) text.append(node('small', item.date));
    const where = node('select', null, 'setup-layout-move');
    where.setAttribute('aria-label', `Notebook for ${item.title}`);
    layout.groups.forEach((group, index) => {
      if (!group.include) return;
      const option = node('option', group.notebook_name || 'New notebook');
      option.value = String(index);
      where.append(option);
    });
    const loose = node('option', 'Leave in Records');
    loose.value = NOT_FILED;
    where.append(loose);
    where.value = here;
    where.addEventListener('change', () => move(item, here, where.value));
    row.append(text, where);
    return row;
  }

  /**
   * One proposed notebook: whether to include it, its name, kind, goal,
   * records and whether it suggests check-ins.
   */
  function notebookCard(group, index, layout, {move, changed}) {
    const card = node('article', null, 'setup-book setup-record-book widget-paper');
    card.classList.toggle('is-left-out', !group.include);
    const include = node('input');
    include.type = 'checkbox';
    include.checked = group.include;
    include.setAttribute('aria-label', 'Include this notebook');
    const name = node('input', null, 'setup-book-input');
    name.value = group.notebook_name;
    name.maxLength = 80;
    name.placeholder = 'Name this notebook';
    name.setAttribute('aria-label', 'Notebook name');
    const choose = node('div', null, 'setup-group-choice');
    const rename = node('button', null, 'setup-book-rename');rename.type = 'button';rename.setAttribute('aria-label', 'Rename notebook');rename.append(icon('pencil'));
    rename.addEventListener('click', () => {name.focus();name.select();});
    choose.append(include, name, rename);
    const head = node('div', null, 'setup-book-name');
    head.append(choose);
    const problem = node('p', group.problem || '', 'setup-problem');
    problem.hidden = !group.problem;
    const collectionLabel = node('label', 'Collection');
    const collectionSelect = node('select');collectionSelect.setAttribute('aria-label','Collection');
    for (const entry of state.health_catalogue?.collections || []) {
      collectionSelect.add(new Option(entry.display_name, entry.collection_id));
    }
    if (group.collection_id && ![...collectionSelect.options].some(o=>o.value===group.collection_id)) collectionSelect.add(new Option(group.collection_name || 'Current collection',group.collection_id));
    collectionSelect.value = group.collection_id || 'fitness';
    group.collection_id = collectionSelect.value;
    collectionLabel.append(collectionSelect);
    collectionSelect.addEventListener('change', () => {
      group.collection_id = collectionSelect.value;
      group.collection_changed = true;
      group.template_id = null;
      group.named_by_person = true;
      changed(true);
    });
    head.append(problem);
    const sameText=(a,b)=>String(a||'').toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim()===String(b||'').toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim();
    const summary=node('p','','setup-notebook-summary');head.append(summary);
    // Under the name: what the notebook covers. The pencil line below says what it is set to.
    summary.textContent=group.description||'';summary.hidden=!group.description;
    function metaText(){
      const groupName=(state.health_catalogue?.collections||[]).find(entry=>entry.collection_id===group.collection_id)?.display_name;
      if(group.goal&&!sameText(group.goal,group.notebook_name))return 'Goal · '+group.goal;
      if(group.reason&&!sameText(group.reason,group.notebook_name))return 'From your answers · '+group.reason;
      return groupName&&!sameText(groupName,group.notebook_name)?`In the ${groupName} group`:'Add a goal';
    }
    function updateSummary(){detailsSummary.firstChild.textContent=metaText();}
    // The group line carries the pencil: opening it edits the collection and goal in place.
    const details=node('details',null,'setup-notebook-details');const detailsSummary=node('summary');detailsSummary.setAttribute('aria-label','Edit collection and goal');details.append(detailsSummary);
    details.append(collectionLabel);
    const goalLabel = node('label', null, 'setup-goal');
    goalLabel.append(icon('target'), node('span', 'Goal'));
    const goal = node('input', null, 'setup-goal-input');
    goal.value = group.goal || '';
    goal.maxLength = 200;
    goal.placeholder = 'No goal yet';
    goalLabel.append(goal);
    details.append(goalLabel);
    if(group.reason&&!sameText(group.reason,group.goal)&&!sameText(group.reason,group.notebook_name))details.append(node('p',group.reason,'setup-hint'));
    const records = node('ul', null, 'setup-layout-records');
    for (const item of group.items) {
      records.append(recordRow(item, String(index), layout, move));
    }
    if (!group.items.length) {
      records.append(node('li', 'No records yet', 'setup-layout-empty'));
    }
    const tasks = node('label', null, 'setup-task-choice');
    const tasksInput = node('input');
    tasksInput.type = 'checkbox';
    tasksInput.checked = group.suggest_tasks;
    tasksInput.setAttribute('aria-label', 'Suggest tasks');
    const tasksText = node('span', 'Suggest to-dos from visits');
    tasks.append(tasksInput, tasksText);
    // What it covers, what it is set to, what is in it and whether Stellis suggests to-dos.
    detailsSummary.append(node('span', ''), icon('pencil'));updateSummary();
    const section = (title, ...body) => {const block = node('section', null, 'setup-book-section');block.append(node('h3', title), ...body);return block;};
    const contents = node('div');
    contents.append(records);
    const does = node('div', null, 'setup-book-does');
    // Only real choices appear here; approved results reach Measurements in every notebook.
    does.append(tasks);
    card.append(head, details);
    // An empty notebook has nothing to list yet, so the section waits for its first record.
    if (group.items.length) card.append(section('What’s in it', contents));
    // One choice needs no heading of its own (owner, 2026-10-27): it sits as a plain row.
    card.append(does);
    include.addEventListener('change', () => {
      group.include = include.checked;
      changed(true);
    });
    name.addEventListener('input', () => {
      group.notebook_name = name.value.trim();updateSummary();
      group.named_by_person = group.notebook_name !== group.proposed_name;
      if (group.named_by_person) group.template_id = null;
      // A name the person chose replaces Stellis's check of the old name.
      problem.hidden = true;
      changed(false);
    });
    goal.addEventListener('input', () => {
      group.goal = goal.value.trim();updateSummary();
      changed(false);
    });
    tasksInput.addEventListener('change', () => {
      group.suggest_tasks = tasksInput.checked;
      changed(false);
    });
    return {card, name};
  }

  /** Records in no notebook, each with a select to file it. */
  function notFiledCard(layout, move) {
    const card = node('article', null, 'setup-book setup-record-book is-loose');
    card.append(node('h2', 'Not filed', 'setup-layout-title'),
        node('p', 'Stellis wasn’t sure where these go. Choose a notebook, or leave them in Records.', 'setup-hint'));
    const records = node('ul', null, 'setup-layout-records');
    for (const item of layout.unsorted) {
      records.append(recordRow(item, NOT_FILED, layout, move));
    }
    card.append(records);
    return card;
  }

  /** Shows catalogue recommendations for the person to review and edit. */
  function reviewScreen(scene) {
    // The only notebook page: saving it finishes setup and opens My Health.
    const title = node('h1', 'Stellis made your notebooks');
    const subtitle = node('p', 'Reading your notes and records', 'setup-hint');
    subtitle.role = 'status';
    const head = node('div', null, 'setup-review-head');
    head.append(title, subtitle);
    const grid = node('div', null, 'setup-grid setup-plan setup-notebook-review');
    const tree = document.querySelector('#setup-tree');
    let layout = {groups: [], unsorted: []};
    const reviewed = () =>
        layout.groups.filter((group) => group.include && group.notebook_name);
    const finish = button('Finish setup', async () => {
      await saves;
      await save('review');
      const completed=await api('/onboarding/complete', 'POST', {
        record_groups: reviewed().map(
            (group) => ({
              notebook_name: group.notebook_name,
              template_id: group.template_id || null,
              collection_id: group.collection_id,
              collection_changed: Boolean(group.collection_changed),
              description: group.description || "",
              notebook_id: group.notebook_name === group.proposed_name ?
                  group.notebook_id :
                  null,
              items:
                  group.items.map((item) => ({kind: item.kind, id: item.id})),
              suggest_tasks: group.suggest_tasks,
              goal: group.goal || '',
            })),
      });
      // One notebook page (owner, 2026-10-27): saving opens My Health, which offers the tour.
      if(completed)location.replace('/?tour=1');
    }, 'primary');
    finish.disabled = true;
    const add = button('Add Notebook', async () => {
      await window.StellisHealthCatalogue.open({
        selected:layout.groups.map(g => g.template_id),
        onSelect(selections) {
          for (const selection of selections) layout.groups.push({
            ...selection, notebook_name:selection.name, proposed_name:selection.name,
            kind:'', reason:'', goal:selection.goal || '', items:selection.items || [], include:true,
            named_by_person:!selection.template_id, suggest_tasks:false
          });
          paint();
        }
      });
    });
    add.disabled = true;
    add.prepend(icon('folder-plus'));
    add.classList.add('setup-add-book');
    /** Lists the notebooks and their record counts in the side rail. */
    function paintTree() {
      const groups = reviewed();
      title.textContent = groups.length ?
          `Stellis made ${plural(groups.length, 'notebook')} for you` : 'Notebooks start from your records';
      // With nothing proposed the side rail's "Proposed" label would head an empty list.
      const railNote = document.querySelector('#setup-rail .setup-rail-content > p');
      if (railNote) railNote.hidden = !groups.length;
      finish.textContent = groups.length ?
          `Save ${plural(groups.length, 'notebook')}` :
          'Finish setup';
      tree.replaceChildren();
      if (!groups.length) return;
      const branch = node('div', null, 'setup-tree-group');
      branch.append(node('strong', 'Notebooks'));
      for (const group of groups) {
        const leaf = node('div', null, 'setup-tree-leaf');
        leaf.append(
            icon('folder'), node('span', group.notebook_name),
            node('small', String(group.items.length)));
        branch.append(leaf);
      }
      tree.append(branch);
    }

    /** Moves a record from one notebook, or Not filed, to another. */
    function move(item, from, to) {
      const take = (list) => list.splice(
          list.findIndex(
              (other) => other.kind === item.kind && other.id === item.id),
          1);
      take(
          from === NOT_FILED ? layout.unsorted :
                               layout.groups[Number(from)].items);
      (to === NOT_FILED ? layout.unsorted : layout.groups[Number(to)].items)
          .push(item);
      paint();
    }

    function paint() {
      const cards = layout.groups.map(
          (group, index) => notebookCard(group, index, layout, {
            move,
            changed(structural) {
              if (structural) {
                paint();
                return;
              }
              // Record selects name each notebook, so they follow a rename.
              for (const option of grid.querySelectorAll(
                       `option[value="${index}"]`)) {
                option.textContent = group.notebook_name || 'New notebook';
              }
              paintTree();
              reviewLayout = layout; scheduleReviewSave();
            },
          }));
      // One grid of notebook cards, in catalogue order; each card names its own group.
      const order = (state.health_catalogue?.collections || []).map(entry => entry.collection_id);
      const rank = group => {const at = order.indexOf(group.collection_id);return at < 0 ? order.length : at;};
      grid.replaceChildren(...layout.groups.map((group, index) => [group, index]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([, index]) => cards[index].card));
      const loose = layout.unsorted.length ? notFiledCard(layout, move) : null;
      looseHost.replaceChildren(...(loose ? [loose] : []));
      add.hidden = layout.groups.length >= MAX_NOTEBOOKS;
      paintTree();
      reviewLayout = layout;
      scheduleReviewSave();
    }

    const actions = node('div', null, 'setup-actions');
    actions.append(finish, button('Back', () => go('records')));
    actions.append(node('p', 'You can rename, add or remove notebooks later from the sidebar.', 'setup-hint setup-actions-note'));
    const looseHost = node('div', null, 'setup-loose-host');
    scene.append(head, grid, looseHost, add, actions);
    api('/intake/notebooks', 'POST')
        .then((result) => {
          layout = layoutFrom(result, null);
          if (state.notebook_choices) layout.groups = state.notebook_choices;
          reviewLayout = layout;
          subtitle.textContent = layout.groups.length ?
              'Here is how Stellis would organise your health. Rename a notebook, untick one you don’t want, or move a record before anything is saved.' :
              'Add a record later and Stellis suggests the notebook it belongs in. You can also add a notebook now.';
        })
        .catch((e) => {
          subtitle.textContent = e.message;
        })
        .finally(() => {
          finish.disabled = false;
          add.disabled = false;
          paint();
        });
  }

  const themeButton = document.querySelector('#setup-theme');
  function syncThemeButton() {
    const light = document.documentElement.dataset.theme === 'light';
    const label = `Switch to ${light ? 'dark' : 'light'} mode`;
    themeButton.setAttribute('aria-label', label);
    themeButton.title = label;
  }
  syncThemeButton();
  themeButton.addEventListener('click', () => {
    const root = document.documentElement;
    const theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    // Colours change at once; transitions return two frames later.
    root.classList.add('theme-swapping');
    root.dataset.theme = theme;
    syncThemeButton();
    requestAnimationFrame(
        () => requestAnimationFrame(
            () => root.classList.remove('theme-swapping')));
    try {
      localStorage.setItem('stellis-theme', theme);
    } catch {
      // Without storage the theme lasts for this page only.
    }
  });

  (async () => {
    try {
      state = await api('/onboarding');
      taskChoices = state.task_choices || {};
      if (!state.required) {
        location.replace('/');
        return;
      }
      // Setups saved by the earlier flow resume at the call.
      if (state.step === 'uses') state.step = 'call';
      render(false);
    } catch (e) {
      error.textContent = e.message;
      main.replaceChildren(
          node('h1', 'Setup could not load'),
          button('Try again', async () => location.reload(), 'primary'));
    }
  })();
})();
