(() => {
  const base = '/api/v1';
  let leavingPage = false;
  window.addEventListener('beforeunload', () => { leavingPage = true; });
  window.addEventListener('pageshow', () => { leavingPage = false; });
  let agents = [];
  let notebooks = [];
  let collections = [];
  let accountScope = '';
  let workspaceTab = 'thread';
  let threadTimer = null;
  const pendingTurns = new Map();
  function draftKey(id) { return `stellis-compose:${accountScope}:${id}`; }
  function readDraft(id) { try { return JSON.parse(sessionStorage.getItem(draftKey(id))) || {}; } catch { return {}; } }
  function writeDraft(id, value) { try { sessionStorage.setItem(draftKey(id), JSON.stringify(value)); } catch {} }
  let selectedId = null;
  let runtimeConfigured = false;
  let detailRevision = 0;
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };
  async function request(path, options) {
    const response = await fetch(`${base}${path}`, options);
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || 'Could not save. Try again.');
    return result;
  }
  function status(message) { document.querySelector('#agent-chat-status').textContent = message; }
  function agentAvatar(agent, size) {
    return window.StellisAgentAvatar.avatarElement(document, {seed: agent.id, coordinator: window.StellisAgentAvatar.isCoordinator(agent), size});
  }
  function messageBody(content) {
    const body = el('p');
    const links = /\[([^\]\n]{1,120})\]\((\/(?!\/)[^\s)]+|https:\/\/[^\s)]+)\)/g;
    let offset = 0;
    for (const match of content.matchAll(links)) {
      body.append(document.createTextNode(content.slice(offset, match.index)));
      // A record link becomes a named citation chip that opens the document beside the chat.
      const record = /^\/document\/([^/?#]+)(?:\?([^#]*))?/.exec(match[2]);
      const page = record && new URLSearchParams(record[2] || '').get('page');
      const named = page && !/\b(p\.|page)\s*\d/i.test(match[1]) ? `${match[1]}, p. ${page}` : match[1];
      const link = el('a', record ? 'chat-cite' : '', record ? named : match[1]);
      link.href = match[2];
      if (record) link.dataset.document = decodeURIComponent(record[1]);
      if (match[2].startsWith('https://')) { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
      body.append(link);
      offset = match.index + match[0].length;
    }
    body.append(document.createTextNode(content.slice(offset)));
    return body;
  }
  function field(labelText, value, name, multiline = false) {
    const label = el('label', '', labelText);
    const input = el(multiline ? 'textarea' : 'input');
    input.name = name; input.value = value || '';
    input.maxLength = name === 'instructions' ? 4000 : name === 'role' ? 160 : 80;
    input.required = name !== 'instructions';
    label.append(input);
    return label;
  }
  // Settings sections pass their agent; thread actions reload the selected one.
  async function reloadAgent(id = selectedId) {
    const agent = await request(`/agents/${encodeURIComponent(id)}`);
    agents = agents.map(item => item.id === agent.id ? agent : item);
    await renderDetail(agent);
  }
  async function memorySection(agent) {
    const section = el('section', 'agent-detail-section');
    section.append(el('h3', '', 'Memory'));
    const memories = await request(`/agents/${encodeURIComponent(agent.id)}/memories`);
    const list = el('div', 'agent-memory-list');
    if (!memories.length) list.append(el('p', 'agent-empty', 'No saved memory'));
    for (const memory of memories) {
      const form = el('form', 'agent-memory-row');
      const label = el('label', '', 'Memory');
      const input = el('textarea'); input.value = memory.fact; input.maxLength = 2000; input.required = true;
      label.append(input);
      const buttons = el('div', 'agent-inline-actions');
      const save = el('button', '', 'Save'); save.type = 'submit';
      const remove = el('button', 'agent-quiet', 'Delete'); remove.type = 'button';
      form.addEventListener('submit', async event => {
        event.preventDefault(); save.disabled = true;
        try {
          await request(`/agents/${encodeURIComponent(agent.id)}/memories/${encodeURIComponent(memory.id)}`,
            {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({fact:input.value, kind:memory.kind, notebook_id:memory.notebook_id, source_id:memory.source_id})});
          status('Memory saved');
        } catch (error) { status(error.message); }
        finally { save.disabled = false; }
      });
      remove.addEventListener('click', async () => {
        try {
          await request(`/agents/${encodeURIComponent(agent.id)}/memories/${encodeURIComponent(memory.id)}`, {method:'DELETE'});
          status('Memory deleted'); await reloadAgent(agent.id);
        } catch (error) { status(error.message); }
      });
      buttons.append(save, remove); form.append(label, buttons); list.append(form);
    }
    section.append(list);
    const add = el('form', 'agent-memory-add');
    const label = el('label', '', 'Add memory');
    const input = el('input'); input.maxLength = 2000; input.required = true; label.append(input);
    const button = el('button', '', 'Add'); button.type = 'submit';
    add.append(label, button);
    add.addEventListener('submit', async event => {
      event.preventDefault(); button.disabled = true;
      try {
        await request(`/agents/${encodeURIComponent(agent.id)}/memories`,
          {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({fact:input.value, kind:'general'})});
        status('Memory added'); await reloadAgent(agent.id);
      } catch (error) { status(error.message); button.disabled = false; }
    });
    section.append(add);
    return section;
  }
  function contextChip(id) {
    const book = notebooks.find(item => item.id === id);
    if (!book) return null;
    const chip = el('a', 'agent-context-chip', book.name); chip.href = `/notebook/${encodeURIComponent(id)}`;
    return chip;
  }
  const artifactEdits=new Map();
  const followupEdits = new Map();
  function suggestionCard(suggestion,onChange) {
    const card=el('section','agent-followup');card.dataset.suggestionId=suggestion.id;
    card.append(el('h4','','Suggested task'),el('p','',suggestion.values.text));
    for(const evidence of suggestion.evidence||[])card.append(el('blockquote','',evidence.quote));
    const target=suggestion.source_target || (suggestion.source_id ? (suggestion.source_kind==='page'?'/page/':'/conversation/')+suggestion.source_id : null);
    if(target){const source=el('a','',suggestion.source_title||'Open source');source.href=target;card.append(source);}
    const notice=el('p','agent-status');notice.role='status';
    if(suggestion.state!=='pending'){
      card.append(el('p','agent-job-state',suggestion.state==='accepted'?'Task added':suggestion.state==='rejected'?'Dismissed':'No longer pending'));
      return card;
    }
    const actions=el('div','agent-followup-actions artifact-actions');
    const send=async(path,body,method='POST')=>request(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const button=(text,run,refresh=true)=>{const b=el('button','',text);b.type='button';b.onclick=async()=>{b.disabled=true;try{await run();if(refresh)await onChange();}catch(error){notice.textContent=error.message;}finally{b.disabled=false;}};actions.append(b);};
    const editor=el('form','agent-followup-editor artifact-editor'),input=el('input');input.setAttribute('aria-label','Task');input.required=true;input.maxLength=500;
    input.value=followupEdits.get(suggestion.id)??suggestion.values.text;
    editor.hidden=!followupEdits.has(suggestion.id);input.oninput=()=>followupEdits.set(suggestion.id,input.value);
    const save=el('button','','Save task');save.type='submit';editor.append(input,save);
    editor.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{await send('/suggestions/'+suggestion.id,{values:{...suggestion.values,text:input.value}},'PATCH');followupEdits.delete(suggestion.id);await onChange();}catch(error){notice.textContent=error.message;}finally{save.disabled=false;}};
    button('Add task',()=>send('/suggestions/accept',{ids:[suggestion.id]}));
    button('Edit task',async()=>{editor.hidden=false;followupEdits.set(suggestion.id,input.value);input.focus();},false);
    button('Dismiss',()=>send('/suggestions/reject',{ids:[suggestion.id]}));
    card.append(actions,editor,notice);return card;
  }
  function artifactCard(draft,onChange,showFollowup=true) {
    const card=el('section','agent-artifact');card.dataset.artifactId=draft.id;
    const state={draft:'Draft',filed:draft.notebook_id?'Saved to notebook':'Saved as note',discarded:'Discarded'}[draft.state]||draft.state;
    card.append(el('h4','',draft.title),el('p','artifact-meta',state));
    if(draft.state==='discarded')return card;
    card.append(messageBody(draft.body));
    if(draft.sources.length){const evidence=el('details','artifact-sources');evidence.append(el('summary','','Draft sources'));for(const source of draft.sources){const link=el('a','',source.title||'Source');link.href=source.target;if(source.target.startsWith('https://')){link.target='_blank';link.rel='noopener noreferrer';}evidence.append(link);}card.append(evidence);}
    const actions=el('div','artifact-actions'),notice=el('p','artifact-notice');notice.setAttribute('role','status');notice.textContent=draft.error||'';
    const button=(label,action)=>{const control=el('button','agent-quiet',label);control.type='button';control.addEventListener('click',async()=>{actions.querySelectorAll('button').forEach(b=>b.disabled=true);try{const updated=await action();if(updated){artifactEdits.delete(draft.id);await onChange(updated);}}catch(error){notice.textContent=error.message;}finally{actions.querySelectorAll('button').forEach(b=>b.disabled=false);}});actions.append(control);return control;};
    const send=(suffix,data)=>request(`/artifacts/${draft.id}${suffix}`,{method:suffix?'POST':'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if(draft.state==='draft'){
      button(draft.notebook_id?'Save to notebook':'Save as note',()=>{if(artifactEdits.has(draft.id))throw Error('Save your edits first.');return send('/note',{revision:draft.revision});});button('Copy draft',async()=>{await navigator.clipboard.writeText(draft.body);notice.textContent='Copied';});
      const editor=el('form','artifact-editor');editor.hidden=!artifactEdits.has(draft.id);
      const values=artifactEdits.get(draft.id)||{title:draft.title,body:draft.body};
      function input(label,name,multiline=false){const wrap=el('label','',label),control=el(multiline?'textarea':'input');control.name=name;control.value=values[name];control.required=true;control.maxLength=name==='body'?20000:200;wrap.append(control);editor.append(wrap);control.addEventListener('input',()=>artifactEdits.set(draft.id,Object.fromEntries(new FormData(editor))));return control;}
      input('Title','title');input('Draft','body',true);
      const save=el('button','','Save changes');save.type='submit';const cancel=el('button','agent-quiet','Cancel');cancel.type='button';cancel.addEventListener('click',()=>{artifactEdits.delete(draft.id);for(const control of editor.querySelectorAll('input,textarea'))control.value=draft[control.name];editor.hidden=true;});editor.append(save,cancel);
      editor.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;try{const values=Object.fromEntries(new FormData(editor));const updated=await send('',{revision:draft.revision,title:values.title,body:values.body});artifactEdits.delete(draft.id);await onChange(updated);}catch(error){notice.textContent=error.message;}finally{save.disabled=false;}});
      button('Edit',async()=>{editor.hidden=false;artifactEdits.set(draft.id,Object.fromEntries(new FormData(editor)));editor.querySelector('input').focus();});
      button('Discard',()=>send('/discard',{revision:draft.revision}));card.append(editor);
    }
    if(draft.state==='filed'){const link=el('a','agent-link-button','Open note');link.href='/page/'+encodeURIComponent(draft.id);actions.append(link);}
    card.append(actions,notice);if(showFollowup && draft.follow_up)card.append(suggestionCard(draft.follow_up,onChange));return card;
  }
  async function refreshThread(section, agent) {
    const [messages, jobs, artifacts, attachments] = await Promise.all([
      request(`/agents/${encodeURIComponent(agent.id)}/messages`),
      request(`/agents/${encodeURIComponent(agent.id)}/jobs`),
      request(`/agents/${encodeURIComponent(agent.id)}/artifacts`),
      window.StellisDom.api(window.StellisChatAttachments.path('agent',agent.id)),
    ]);
    if (selectedId !== agent.id) return;
    const feed = section.querySelector('.agent-thread-feed');
    const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
    const openSteps = new Set([...feed.querySelectorAll('.agent-step-result[open]')].map(item=>item.dataset.stepId));
    const byJob = new Map(jobs.map(job => [job.id, job]));
    const nodes = [];
    for (const message of messages) {
      const item = el('article', `agent-message agent-message-${message.role}`); item.id = 'message-' + message.id;
      const author = el('strong', 'agent-message-author', message.role === 'user' ? 'You' : message.role === 'system' ? 'Task update' : message.addressed_agent_name || agent.name);
      if (message.role === 'agent') author.prepend(agentAvatar(agents.find(a=>a.id===message.addressed_agent_id)||agent, 24));
      item.append(author);
      if(message.role==='user' && message.addressed_agent_id && message.addressed_agent_id!==agent.id)item.append(el('small','agent-job-state','To '+message.addressed_agent_name));
      const draft=message.routed_artifact || artifacts.find(item=>item.message_id===message.id);
      if(draft)item.append(artifactCard(draft,()=>refreshThread(section,agent)));else item.append(messageBody(message.content));
      item.append(window.StellisChatAttachments.cards(attachments.filter(file=>file.message_id===message.id),{kind:'agent',id:agent.id,books:notebooks,notebookId:()=>message.notebook_id||''}));
      const chip = contextChip(message.notebook_id); if (chip) item.append(chip);
      const trace = message.role === 'agent' ? window.StellisAnswerTrace?.link({jobId: message.job_id}) : null;
      if (trace) item.append(trace);
      const job = byJob.get(message.job_id);
      if (message.role === 'user' && job && job.state !== 'succeeded')
        item.append(job.state === 'running' ? window.StellisChatUI.typing(message.addressed_agent_name ? 'Asking ' + message.addressed_agent_name : 'Working on your request') : el('small', 'agent-job-state', job.error || 'Reply interrupted. Try again.'));
      if(message.role==='user' && job?.steps?.length){
        const work=el('ol','agent-work-steps');work.setAttribute('aria-label','Work steps');
        for(const step of job.steps){
          const row=el('li','agent-work-step');row.dataset.state=step.state;
          row.append(el('strong','',step.agent_name),el('span','',step.task));
          const states={queued:'Waiting',running:'Working…',succeeded:'Done',failed:'Failed',interrupted:'Stopped',skipped:'Not run'};
          row.append(el('small','agent-job-state',states[step.state]||step.state));
          if(step.sources?.length)row.append(el('small','agent-job-state',`${step.sources.length} source${step.sources.length===1?'':'s'}`));
          if(step.result){const detail=el('details','agent-step-result');detail.dataset.stepId=step.id;detail.open=openSteps.has(step.id);detail.append(el('summary','','View result'),messageBody(step.result));row.append(detail);}
          if(step.error)row.append(el('p','agent-status',step.error));
          work.append(row);
        }
        item.append(work);
      }
      if(message.role==='user' && job?.state==='running'){
        const stop=el('button','agent-job-stop','Stop reply');stop.type='button';
        stop.addEventListener('click',async()=>{stop.disabled=true;try{
          await request(`/agents/${agent.id}/jobs/${job.id}/stop`,{method:'POST'});
          await refreshThread(section,agent);
        }catch(error){status(error.message);stop.disabled=false;}});
        item.append(stop);
      }
      nodes.push(item);
    }
    const pending = pendingTurns.get(agent.id);
    section.running = Boolean(pending) || jobs.some(job => job.state === 'running');
    const send = section.querySelector('button[type=submit]');
    if (send) send.disabled = !runtimeConfigured || !agent.enabled || section.running || section.attachments?.busy();
    if (pending && !messages.some(message => message.role === 'user' && message.content === pending.question.replace(/\s+/g, ' ').trim() && Date.parse(message.created_at) >= Date.parse(pending.started) - 1000)) {
      const item = el('article', 'agent-message agent-message-user');
      item.append(el('strong', 'agent-message-author', 'You'), messageBody(pending.question), el('small', 'agent-job-state', 'Submitting…'));
      const chip = contextChip(pending.notebook); if (chip) item.append(chip); nodes.push(item);
    }
    // A new conversation says what to ask instead of showing a blank feed (owner: never empty).
    if (!nodes.length) nodes.push(el('p', 'agent-thread-start', 'Ask about a result, a record or a visit. Each answer links to the records it comes from.'));
    const signature = JSON.stringify([messages,jobs,artifacts,attachments,pending?.question]);
    if (feed.dataset.signature !== signature) {feed.replaceChildren(...nodes);feed.dataset.signature = signature;}
    if (nearBottom || pending) window.StellisChatUI?.showNewest ? window.StellisChatUI.showNewest(feed, feed.lastElementChild) : feed.scrollTop = feed.scrollHeight;
    clearTimeout(threadTimer);
    if (pending || jobs.some(job => job.state === 'running')) threadTimer = setTimeout(() => {
      if (selectedId === agent.id && section.isConnected) refreshThread(section, agent).catch(error => status(error.message));
    }, 1800);
  }
  async function threadSection(agent) {
    const section = el('section', 'agent-detail-section agent-thread');
    const feed = el('div', 'agent-thread-feed'); feed.setAttribute('role', 'log'); feed.setAttribute('aria-label', `Conversation with ${agent.name}`); feed.setAttribute('aria-live', 'polite');
    section.append(feed);
    await refreshThread(section, agent);
    const form = el('form', 'agent-ask-form agent-composer');
    const draft = readDraft(agent.id);
    const context = el('div', 'agent-composer-context');
    const select = el('select'); select.name = 'notebook_id'; select.setAttribute('aria-label', 'Notebook context');
    const any = el('option', '', 'No notebook'); any.value = ''; select.append(any);
    for (const book of (agent.is_default?notebooks.map(n=>({notebook_id:n.id,notebook_name:n.name,enabled:true})):agent.notebooks)) {
      const option = el('option', '', book.notebook_name + (book.enabled===false?' (Off)':'')); option.value = book.notebook_id; option.disabled=book.enabled===false;select.append(option);
    }
    select.value = draft.notebook || ''; context.append(glyph('notebook'), select); form.append(context);
    const handoffId=new URLSearchParams(location.search).get('source_context');
    let sourceHandoff=null;
    if(handoffId){try{sourceHandoff=await window.StellisDom.api('/source-handoffs/'+encodeURIComponent(handoffId));const proof=el('section','agent-source-handoff');proof.append(el('strong','','Selected report context'),el('p','',sourceHandoff.quote));const source=el('a','chat-source-link',sourceHandoff.title);source.href=sourceHandoff.target;const remove=el('button','coach-button is-quiet','Remove context');remove.type='button';remove.onclick=()=>{sourceHandoff=null;proof.remove();const url=new URL(location.href);url.searchParams.delete('source_context');history.replaceState({},'',url);};proof.append(source,remove);form.append(proof);if(sourceHandoff.notebook_id)select.value=sourceHandoff.notebook_id;}catch(error){form.append(el('p','agent-error',error.message));}}
    const input = el('textarea'); input.name = 'question'; input.maxLength = 2000; input.required = true;
    // A question started on Home arrives as ?ask= and waits in the box.
    input.value = (new URLSearchParams(location.search).get('ask') || draft.question || '').slice(0, 2000);
    input.placeholder = `Message ${agent.name}`; input.setAttribute('aria-label', `Message ${agent.name}`); form.append(input);
    const remember = () => writeDraft(agent.id, {question:input.value, notebook:select.value});
    input.addEventListener('input', remember); select.addEventListener('change', remember);
    const actions = el('div', 'agent-composer-actions');
    const state = el('span', 'agent-composer-state'); state.role = 'status';
    const send = window.StellisChatUI.sendButton(el('button', '', 'Send')); send.type = 'submit';
    const box = el('div', 'agent-input'); input.replaceWith(box); box.append(input, send); window.StellisChatUI.composer(box, input);
    send.disabled = !runtimeConfigured || !agent.enabled || section.running;
    if (!agent.enabled) state.textContent = 'Turn on this agent in Setup.';
    else if (!runtimeConfigured) state.textContent = 'Replies need AI set up in Settings';
    const add = el('button', 'agent-capture-action', 'Add a record'); add.type = 'button'; add.setAttribute('aria-haspopup','dialog');
    add.addEventListener('click',()=>window.StellisCapture.open(null,{notebookId:select.value || null}));
    const attachments=window.StellisChatAttachments.create({kind:'agent',id:agent.id,books:notebooks,notebookId:()=>select.value,
      onChange:()=>{send.disabled=!runtimeConfigured||!agent.enabled||section.running||attachments.busy();}});
    section.attachments=attachments;await attachments.load();
    actions.append(attachments.element, add, state); form.append(actions);
    input.addEventListener('keydown', event => { if (!event.defaultPrevented && !event.isComposing && !event.shiftKey && event.key === 'Enter' && !send.disabled) {event.preventDefault(); form.requestSubmit();} });
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (send.disabled || !input.value.trim()) return;
      const submitted = {question:input.value.trim(), notebook:select.value, started:new Date().toISOString()};
      attachments.setSending(true);pendingTurns.set(agent.id, submitted); send.disabled = true; input.value = ''; remember(); state.textContent = 'Working…';
      const optimistic = el('article', 'agent-message agent-message-user'); optimistic.append(el('strong', 'agent-message-author', 'You'), messageBody(submitted.question), el('small', 'agent-job-state', 'Submitting…')); feed.querySelector('.agent-empty')?.remove(); feed.append(optimistic); feed.scrollTop = feed.scrollHeight;
      clearTimeout(threadTimer); threadTimer = setTimeout(() => refreshThread(section, agent).catch(error => status(error.message)), 700);
      try {
        await request(`/agents/${encodeURIComponent(agent.id)}/ask`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:submitted.question, notebook_id:submitted.notebook || null,attachment_ids:attachments.ids(),source_handoff_id:sourceHandoff?.id||null})});
        await attachments.load();state.textContent = '';
      } catch (error) {
        state.textContent = error.message;
        if (!leavingPage && !readDraft(agent.id).question) {
          writeDraft(agent.id, {question:submitted.question, notebook:submitted.notebook});
          const current = selectedId === agent.id ? document.querySelector('.agent-composer textarea') : null;
          if (current && !current.value) current.value = submitted.question;
        }
      } finally {
        attachments.setSending(false);pendingTurns.delete(agent.id); send.disabled = !runtimeConfigured || !agent.enabled;
        if (selectedId === agent.id && section.isConnected) await refreshThread(section, agent).catch(error => status(error.message));
      }
    });
    section.append(form); return section;
  }
  function identityForm(agent) {
    const identity = el('form', 'agent-identity-form');
    identity.append(field('Name', agent.name, 'name'), field('Role', agent.role, 'role'),
      field('Instructions', agent.instructions, 'instructions', true));
    const save = el('button', '', 'Save agent'); save.type = 'submit'; identity.append(save);
    identity.addEventListener('submit', async event => {
      event.preventDefault(); save.disabled = true;
      try {
        const data = Object.fromEntries(new FormData(identity));
        await request(`/agents/${encodeURIComponent(agent.id)}`,
          {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
        status('Agent saved'); await reloadAgent(agent.id);
      } catch (error) { status(error.message); save.disabled = false; }
    });
    return identity;
  }
  async function contextPanel(agent) {
    const panel = el('aside', 'agent-context-panel'); panel.setAttribute('aria-label', 'Agent context');
    const group = (title, text) => { const section = el('section'); section.append(el('h4', '', title)); if (text) section.append(el('p', '', text)); panel.append(section); return section; };
    // Groups with nothing in them are left out (owner, 2026-10-28: no empty items on any page), and a
    // panel with no groups is left out with them, so the conversation takes the width.
    if (agent.notebooks.length) {const scope = group('Works in'); for (const book of agent.notebooks) {const chip = contextChip(book.notebook_id); if (chip) scope.append(chip);}}
    const memories = await request(`/agents/${encodeURIComponent(agent.id)}/memories`);
    if (memories.length) {const memory = group('Memory'); for (const item of memories.slice(-4)) memory.append(el('p', '', item.fact));}
    return panel.children.length ? panel : null;
  }
  async function renderDetail(agent) {
    const revision = ++detailRevision;
    const target = document.querySelector('#agent-detail');
    clearTimeout(threadTimer);
    const head = el('div', 'agent-detail-head'); const identity=el('div','agent-workspace-identity'),text=el('div');
    text.append(el('h3', '', agent.name), el('p', '', agent.behavior?.does || agent.role));identity.append(agentAvatar(agent,44),text);head.append(identity);
    const tabs=el('div','agent-workspace-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Agent workspace');
    const thread=await threadSection(agent);
    if (selectedId !== agent.id || revision !== detailRevision) return;
    const shell=el('div','agent-workspace'),center=el('div','agent-workspace-center');
    const threadPanel=el('div','agent-workspace-panel'),setupPanel=el('div','agent-workspace-panel');
    const panels={thread:threadPanel,setup:setupPanel}; let setupLoaded=false;
    threadPanel.append(thread);
    async function activate(name) {
      workspaceTab=name;
      for (const [key,panel] of Object.entries(panels)) {panel.hidden=key!==name;tabs.querySelector(`[data-tab="${key}"]`).setAttribute('aria-selected',String(key===name));}
      try {
        if(name==='setup'&&!setupLoaded){setupLoaded=true;setupPanel.append(behaviorCard(agent),identityForm(agent),await memorySection(agent));}
      } catch(error) {setupLoaded=false;status(error.message);}
    }
    for(const [name,label] of [['thread','Thread'],['setup','Setup']]) {
      const button=el('button','',label);button.type='button';button.dataset.tab=name;button.id=`agent-tab-${name}`;button.setAttribute('role','tab');button.setAttribute('aria-controls',`agent-panel-${name}`);
      const panel=panels[name];panel.id=`agent-panel-${name}`;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);
      button.addEventListener('click',()=>activate(name));button.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const buttons=[...tabs.children],index=buttons.indexOf(button),next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[next].focus();buttons[next].click();});tabs.append(button);
    }
    center.append(tabs,threadPanel,setupPanel);const context=await contextPanel(agent);shell.append(center);if(context)shell.append(context);
    if (selectedId !== agent.id || revision !== detailRevision) return;
    target.replaceChildren(head,shell);await activate(workspaceTab);
    if(window.StellisChatWindow){
      const bar=el('div','agent-chat-window-head'),who=el('div','agent-chat-window-identity');
      who.append(agentAvatar(agent,36),el('strong','',agent.name));bar.append(who);thread.prepend(bar);thread.classList.add('widget-paper');
      window.StellisChatWindow.attach(thread,{head:bar,title:agent.name,initialMode:'docked'});
    }
    if (location.hash.startsWith('#message-')) document.getElementById(location.hash.slice(1))?.scrollIntoView();
    else { const feed = thread.querySelector('.agent-thread-feed'); window.StellisChatUI?.showNewest ? window.StellisChatUI.showNewest(feed, feed.lastElementChild) : feed.scrollTop = feed.scrollHeight; }
  }
  function glyph(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'icon'); svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use'); use.setAttribute('href', `#i-${name}`);
    svg.append(use); return svg;
  }
  async function select(id, navigate) {
    clearTimeout(threadTimer);
    if (selectedId !== id) workspaceTab = 'thread';
    selectedId = id;
    if (navigate) history.pushState({}, '', `/chat/${encodeURIComponent(id)}`);
    const agent = agents.find(item => item.id === id);
    if (agent) await renderDetail(agent);
  }
  async function show(requestedId) {
    status('');
    const [items, books, groups, runtime] = await Promise.all([request('/agents'), request('/notebooks'), request('/collections'), request('/agents/runtime')]);
    const account = (await request('/auth/status')).username || 'local';
    const demo = await request('/demo/status');
    accountScope = demo.active ? account + ':demo-' + demo.generation : account;
    agents = items; notebooks = books.notebooks || []; collections = groups.collections || []; runtimeConfigured = runtime.configured;
    const id = agents.some(agent => agent.id === requestedId) ? requestedId : agents[0]?.id;
    if (id) await select(id, false);
  }
  function behaviorCard(agent) {
    const card = el('dl', 'agent-behavior');
    const behavior = agent.behavior || {};
    for (const [key, label] of [['does', 'Does'], ['runs', 'Runs'], ['returns', 'Returns'], ['asks_first', 'Asks first']]) {
      card.append(el('dt', '', label), el('dd', '', behavior[key] || 'Not specified'));
    }
    return card;
  }
  window.StellisAgentChat = {show, artifactCard, suggestionCard, messageBody};

  // Citation chips and attachment links open the document panel on a plain click.
  document.addEventListener('click', event => {
    const link = event.target.closest('.agent-message a[data-document]');
    if (!link || !window.StellisChatDocument || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    for (const open of document.querySelectorAll('.agent-message a[aria-current]')) open.removeAttribute('aria-current');
    link.setAttribute('aria-current', 'true');
    const params = new URL(link.href, location.origin).searchParams;
    window.StellisChatDocument.open({documentId: link.dataset.document, page: Number(params.get('page')) || undefined,
      region: params.get('region') || undefined, onClose: () => link.removeAttribute('aria-current')});
  });
})();
