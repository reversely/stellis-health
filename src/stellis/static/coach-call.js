/**
 * @fileoverview Coach calls on Home. The morning check-in arrives as an
 * incoming call card at the top of Home; any time, Call Stellis opens a
 * call. A call runs in a panel docked over the page, with its chat log, so
 * the person sees Stellis fill in the page as they talk. A call by text
 * ("Chat instead") uses the same panel without the microphone or speech.
 * The guided tour runs in the same panel: each step opens a page and
 * highlights one control while Stellis describes it.
 */
(function(root) {
'use strict';

const STATUS = {...root.StellisIntakeCall.STATUS, typing:'Chat'};
const TITLES = {
  morning: 'Daily check-in',
  call: 'Call with Stellis',
  tour: 'Tour with Stellis',
  report: 'Call about this report',
};
const TOUR_WAIT_MS = 8000;

function localParams(referenceDay=null) {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const actual = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  // A past review date is over: its evening has happened, so "tonight" means that date, not the day
  // before it, whatever the actual clock says.
  const past = referenceDay && referenceDay < actual;
  return {
    today: referenceDay || actual,
    now: past ? '23:59' : `${pad(now.getHours())}:${pad(now.getMinutes())}`,
    utc_offset: String(-new Date((referenceDay||`${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}`)+'T12:00:00').getTimezoneOffset()),
  };
}

function icon(glyph) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '#i-' + glyph);
  svg.append(use);
  return svg;
}

function setGlyph(button, glyph) {
  button.querySelector('use').setAttribute('href', '#i-' + glyph);
}

function avatar(size) {
  return root.StellisAgentAvatar.avatarElement(
      document, {seed: 'stellis', coordinator: true, size});
}

/**
 * Builds a button with an icon and a visible or hidden label.
 * @param {string} glyph
 * @param {string} label
 * @param {string} cls
 * @param {boolean=} iconOnly
 * @return {!HTMLButtonElement}
 */
function button(glyph, label, cls, iconOnly = false) {
  const element = root.StellisDom.el('button', null, cls);
  element.type = 'button';
  element.append(icon(glyph));
  if (iconOnly) {
    element.setAttribute('aria-label', label);
    element.title = label;
  } else {
    element.append(root.StellisDom.el('span', label));
  }
  return /** @type {!HTMLButtonElement} */ (element);
}

/**
 * Builds the morning check-in's incoming call card.
 * @param {{answer: function(), chat: function(), decline: function(),
 *     snooze: (function()|undefined)}} on
 * @param {{time: (string|undefined), agenda: (!Array<!Object>|undefined),
 *     snoozeMinutes: (number|undefined)}=} details What the call covers,
 *     from GET /coach, and when it rang.
 * @return {!Element}
 */
function incoming(on, details = {}) {
  const dom = root.StellisDom;
  const card = dom.el('section', null, 'coach-incoming');
  card.setAttribute('aria-labelledby', 'coach-incoming-title');
  const face = dom.el('div', null, 'coach-incoming-face');
  face.append(dom.el('span', null, 'coach-incoming-ring'), avatar(72));
  const text = dom.el('div', null, 'coach-incoming-text');
  const title = dom.el('h2', 'Stellis is calling');
  title.id = 'coach-incoming-title';
  text.append(
      dom.el('p', details.time ? `Morning check-in, ${details.time}` : 'Morning check-in', 'coach-incoming-kind'), title);
  // Without an agenda, a line says what a check-in covers.
  if (!details.agenda?.length) {
    text.append(dom.el('p', 'Today’s appointments and reminders, and how you’re doing', 'coach-incoming-hint'));
  }
  const actions = dom.el('div', null, 'coach-incoming-actions');
  // One answer control, the same swipe as onboarding; a tap or Enter also answers.
  const answer = root.StellisSwipeAnswer ? root.StellisSwipeAnswer.create({onAnswer: on.answer}) : button('phone', 'Answer', 'coach-button is-primary');
  if (!root.StellisSwipeAnswer) answer.addEventListener('click', on.answer);
  const chat = button('message-2', 'Chat instead', 'coach-button');
  const later = dom.button('Not now', 'coach-button is-quiet');
  chat.addEventListener('click', on.chat);
  later.addEventListener('click', on.decline);
  actions.append(answer, chat);
  if (on.snooze && details.snoozeMinutes) {
    const snooze = dom.button(`In ${details.snoozeMinutes} minutes`, 'coach-button is-quiet');
    snooze.addEventListener('click', on.snooze);
    actions.append(snooze);
  }
  actions.append(later);
  card.append(face, text, actions);
  if (details.agenda?.length) {const agenda=dom.el('details',null,'coach-incoming-details');agenda.append(dom.el('summary',`${details.agenda.length} ${details.agenda.length === 1 ? 'topic' : 'topics'} on this call`),agendaList(details.agenda,'coach-incoming-agenda'));card.append(agenda);}
  return card;
}

let nextAgenda = 0;

/**
 * Lists what a check-in covers, each with its icon and where it comes from.
 * @param {!Array<!Object>} agenda
 * @param {string} cls
 * @return {!Element}
 */
function agendaList(agenda, cls) {
  const dom = root.StellisDom;
  const section = dom.el('section', null, cls);
  const heading = dom.el('h3', 'On this call');
  heading.id = `${cls}-${++nextAgenda}`;
  section.setAttribute('aria-labelledby', heading.id);
  const list = dom.el('ul');
  for (const item of agenda) {
    const row = dom.el('li');
    row.dataset.key = item.key;
    const mark = dom.el('span', null, 'coach-agenda-icon');
    mark.append(icon(item.icon));
    const source=dom.el(item.target?'a':'span',item.label,'coach-agenda-source');
    if(item.target)source.href=item.target;
    row.append(mark, dom.el('span', item.title, 'coach-agenda-title'), source);
    list.append(row);
  }
  section.append(heading, list);
  return section;
}

let current = null;
let target = null;
let stepGeneration = 0;

/** Removes the tour's highlight. */
function clearTarget() {
  stepGeneration++;
  target?.classList.remove('tour-target');
  target = null;
}

/**
 * Opens a page in the app, then highlights a control once it shows.
 * @param {string} path
 * @param {string} selector
 */
function showStep(path, selector) {
  clearTarget();
  const generation = stepGeneration;
  if (!path.startsWith('/') || path.startsWith('//')) return Promise.resolve(false);
  if (location.pathname !== path) {
    history.pushState({}, '', path);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }
  const started = Date.now();
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  return new Promise(resolve => {
    (function find() {
      if (generation !== stepGeneration) {resolve(false); return;}
      // The first visible match: phones hide the sidebar, so a step can name a fallback on the page.
      const found = [...document.querySelectorAll(selector)].find(node => node.checkVisibility());
      if (found?.checkVisibility()) {
        target = found;
        target.classList.add('tour-target');
        target.scrollIntoView({block:'start', behavior:still ? 'instant' : 'smooth'});
        resolve(true);
      } else if (Date.now() - started < TOUR_WAIT_MS) setTimeout(find, 150);
      else resolve(false);
    })();
  });
}

/** Offers the tour in the docked panel, after setup. */
function offerTour() {
  const dom = root.StellisDom;
  const panel = dom.el('section', null, 'coach-call coach-offer');
  panel.setAttribute('aria-labelledby', 'coach-offer-title');
  const head = dom.el('div', null, 'coach-call-head');
  const who = dom.el('div', null, 'coach-call-who');
  const title = dom.el('strong', 'Stellis is ready to show you around');
  title.id = 'coach-offer-title';
  who.append(
      title, dom.el('span', 'Explore your records and health tools', 'coach-call-line'));
  head.append(avatar(36), who);
  const actions = dom.el('div', null, 'coach-call-ended');
  const start = button('phone', 'Start tour', 'coach-button is-primary');
  const later = dom.button('Not now', 'coach-button is-quiet');
  start.addEventListener('click', () => {
    panel.remove();
    open({kind:'tour'});
  });
  later.addEventListener('click', () => panel.remove());
  actions.append(start, later);
  panel.append(head, actions);
  document.body.append(panel);
}

/**
 * Opens a call in the docked panel; an open call is ended first.
 * @param {{kind: string, chat: boolean, onAction: function(!Object),
 *     agenda: (!Array<!Object>|undefined)}} options
 *     ``kind`` is "morning" or "call"; ``chat`` runs the call by text;
 *     ``onAction`` receives each action Stellis takes on the page;
 *     ``agenda`` is what a check-in covers, shown as a checklist.
 * @return {{end: function()}}
 */
async function defaultAction(message){
 if(message.action==='fill_goal'){
  let target=location.pathname==='/'?document.querySelector('#home-goal-draft'):null;
  if(!target){target=document.createElement('section');target.className='coach-goal-draft';document.querySelector('.coach-call .coach-call-body')?.append(target);}
  try{await root.StellisGoals?.draft?.(target,[message.values]);target.hidden=false;target.scrollIntoView({block:'nearest'});}catch(e){target.textContent=e.message;}
 }
 if(message.action==='logged')root.StellisGoals?.reload?.();
 if(['fill_goal','logged'].includes(message.action))root.dispatchEvent(new Event('health-capture-saved'));
}

function open({kind, chat, onAction=defaultAction, agenda = [], source=null, host=null, onClose=()=>{}, onRestart=null, handoff=null,referenceDay=null}) {
  current?.close();
  referenceDay=referenceDay||(location.pathname==='/'?root.StellisHomeCare?.date:null)||null;
  // Calls on Home sit in its call workspace; the tour stays docked over the app, because it moves between targets.
  const homeHost=host?.id==='home-call-workspace'?host:!host&&!['report','tour'].includes(kind)&&location.pathname==='/'?document.querySelector('#home-call-workspace'):null;
  if(homeHost){host=homeHost;host.hidden=false;host.parentElement.classList.add('has-active-call');}
  const dom = root.StellisDom;
  const panel = dom.el('section', null, 'coach-call widget-paper');
  panel.setAttribute('aria-label', TITLES[kind]);
  panel.dataset.state = 'connecting';
  panel.dataset.kind = kind;

  const head = dom.el('div', null, 'coach-call-head');
  const face = dom.el('span', null, 'coach-call-face');
  face.append(avatar(36));
  const who = dom.el('div', null, 'coach-call-who');
  const status = dom.el('span', STATUS.connecting, 'coach-call-status');
  status.setAttribute('role', 'status');
  const timer = dom.el('span', '', 'coach-call-timer');
  const line = dom.el('span', null, 'coach-call-line');
  line.append(status, timer);
  who.append(dom.el('strong', TITLES[kind]), line);
  const shrink = button(
      'arrows-diagonal-minimize-2', 'Minimise call', 'coach-call-icon', true);
  const hangUp =
      button('phone-off', 'End call', 'coach-call-icon is-end', true);
  head.append(face, who, shrink, hangUp);

  const stepCard = dom.el('section', null, 'coach-tour-step');
  const stepTitle = dom.el('h3');
  const stepNarration = dom.el('p');
  stepCard.append(stepTitle, stepNarration);
  stepCard.hidden = true;
  const log = dom.el('ol', null, 'coach-call-log');
  log.setAttribute('aria-live', 'polite');
  const callBody = dom.el('div', null, 'coach-call-body');
  callBody.tabIndex = 0;
  callBody.setAttribute('role', 'region');
  callBody.setAttribute('aria-label', 'Conversation');
  // A check-in's agenda becomes a checklist that fills in as topics are covered.
  let topics = agenda.length ? agendaList(agenda, 'coach-agenda') : null;
  const progress = dom.el('span', null, 'coach-agenda-progress');
  progress.setAttribute('aria-live', 'polite');
  if (topics) topics.querySelector('h3').after(progress);
  function cover(key) {
    const row = topics?.querySelector(`li[data-key="${CSS.escape(key)}"]`);
    if (row) row.dataset.covered = 'true';
    paintProgress();
  }
  function paintProgress() {
    if (topics) progress.textContent = `${topics.querySelectorAll('li[data-covered]').length} of ${agenda.length}`;
  }
  paintProgress();
  if (topics) callBody.append(topics);
  if(referenceDay&&referenceDay!==localParams().today)callBody.append(dom.el('p','Review date: '+new Date(referenceDay+'T12:00:00').toLocaleDateString([],{year:'numeric',month:'long',day:'numeric'}),'coach-call-date'));
  if(handoff){const proof=dom.el('details',null,'coach-call-source');proof.append(dom.el('summary','Selected report context'),dom.el('blockquote',handoff.quote));const sourceLink=dom.el('a',handoff.title,'chat-source-link');sourceLink.href=handoff.target;proof.append(sourceLink,dom.el('p','Care changes still need review and approval.'));callBody.append(proof);}
  callBody.append(stepCard, log);

  const foot = dom.el('form', null, 'coach-call-foot');
  const input = /** @type {!HTMLInputElement} */ (dom.el('input'));
  input.maxLength = 2000;
  input.placeholder = 'Type a message';
  input.setAttribute('aria-label', 'Message');
  const send = root.StellisChatUI.sendButton(dom.button('Send', 'coach-button'));
  send.type = 'submit';
  const mute = button('microphone', 'Mute', 'coach-call-icon', true);
  const sound = button('volume', 'Speaker off', 'coach-call-icon', true);
  mute.hidden = sound.hidden = chat;
  const box = dom.el('div', null, 'coach-call-input');box.append(input, send);root.StellisChatUI.composer(box, input);
  foot.append(mute, sound, box);
  let next = null;
  let stepReady = false;
  if (kind === 'tour') {
    next = dom.button('Next', 'coach-button is-primary');
    next.disabled = true;
    next.addEventListener('click', () => {next.disabled = true; stepReady = false; call.sendText('Next');});
    foot.append(next);
    input.placeholder = 'Ask a question';
  }

  const ended = dom.el('div', null, 'coach-call-ended');
  const close = dom.button('Close', 'coach-button');
  const again = button('phone', 'Call again', 'coach-button');
  const destination = dom.button('Go to My Health', 'coach-button is-primary');
  destination.hidden = !['tour','morning'].includes(kind);
  destination.addEventListener('click', () => {remove(); history.pushState({}, '', '/'); window.dispatchEvent(new PopStateEvent('popstate'));});
  if(kind === 'tour') again.lastChild.textContent = 'Restart tour';
  ended.append(destination, again, close);
  ended.hidden = true;

  const heard = dom.el('p', null, 'coach-call-heard');
  heard.append(
      chat ? 'Stellis can make mistakes. ' :
             'Speech recognition can mishear words. ',
      dom.accuracyNote(chat ? 'summaries' : 'speech', 'About accuracy'));
  const recovery = dom.el('div', null, 'coach-call-recovery');
  const failure = dom.el('p'); failure.role = 'alert';
  const retry = dom.button('Retry reply', 'coach-button'); retry.hidden = true;
  const doneSpeaking = dom.button('Done speaking', 'coach-button'); doneSpeaking.hidden = true;
  retry.addEventListener('click', () => call.retryReply());
  doneSpeaking.addEventListener('click', () => call.finishAnswer());
  recovery.append(failure, retry, doneSpeaking); recovery.hidden = true;
  panel.append(head, callBody, recovery, foot, ended, heard);
  (host || document.body).append(panel);
  if(host)panel.classList.add('is-embedded');
  // On narrow Home the call workspace sits below For you now; bring the call into view as it starts.
  if(homeHost&&matchMedia('(max-width: 1200px)').matches)requestAnimationFrame(()=>panel.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'}));
  if(homeHost)requestAnimationFrame(()=>homeHost.scrollIntoView({block:'start'}));

  let sessionId=null;const coverageBatches=new Map();
  let started = 0;
  let clock = 0;
  let muted = false;
  let speaker = !chat;
  let finished = false;

  function setStatus(key) {
    if (finished && key !== 'ended' && key !== 'lost') return;
    if (panel.dataset.state === 'ending' && !['ended','lost'].includes(key)) return;
    panel.dataset.state = key;
    status.textContent = STATUS[key];
    if (next) next.disabled = !stepReady || !['listening','typing','hearing'].includes(key);
    doneSpeaking.hidden = chat || mute.hidden || key !== 'hearing';
    recovery.hidden = !failure.textContent && retry.hidden && doneSpeaking.hidden;
  }

  /** Adds a line to the chat log; pieces of one reply join one bubble. */
  function addLine(role, text, turn) {
    const selector = role === 'agent' ? 'agentTurn' : 'turn';
    const existing = turn != null ?
        [...log.children].find(
            (item) => item.dataset[selector] === String(turn)) :
        null;
    if (existing && role === 'agent') {
      existing.lastChild.textContent += ' ' + text;
    } else {
      const item = existing || dom.el('li', null, 'is-' + role);
      item.replaceChildren(
          dom.el(
              'span', role === 'agent' ? 'Stellis' : 'You', 'coach-call-said'),
          dom.el('p', text));
      if (turn != null) item.dataset[selector] = String(turn);
      // The check-in's first line is written from records, not by the model.
      if (role === 'agent' && kind === 'morning' && agenda.length && !log.querySelector('.is-agent')) {
        item.append(dom.el('span', 'Written from your goals and records', 'coach-call-source'));
      }
      if (!existing) log.append(item);
    }
    callBody.scrollTop = callBody.scrollHeight;
  }

  function addAction(text) {
    log.append(dom.el('li', text, 'is-action'));
    callBody.scrollTop = callBody.scrollHeight;
  }

  /** Shows what Stellis proposed and why, for the person to confirm before anything is saved. */
  function propose(message) {
    panel.classList.add('is-reviewing');

    const why = dom.el('li', null, 'is-action');
    why.append(dom.el('strong','Ready to log'),dom.el('p','Review the entries below. Nothing has been saved.'),dom.el('blockquote',message.evidence));
    const review = dom.el('li', null, 'is-review');
    log.append(why, review);
    root.StellisTrackerLog.review(review, {
      proposals: message.proposals, sharedEvidence:message.evidence, day: message.day, title: `Confirm for ${message.day_text}`,
      onSaved:async receipt=>{review.remove();if(!log.querySelector('.is-review'))panel.classList.remove('is-reviewing');addAction(`Logged ${receipt.saved} ${receipt.saved === 1 ? 'entry' : 'entries'} for ${message.day_text}`);if(sessionId){try{await dom.api(`/coach/calls/${sessionId}/receipts`,{method:'POST',json:{batch_id:receipt.batch_id}});coverageBatches.set(receipt.batch_id,message.proposals);for(const p of message.proposals)cover(p.item.startsWith('tracker:')?'dose:'+p.item.slice(8):p.item);}catch(e){addAction('Entries saved. Call receipt: '+e.message);}}onAction({action:'logged',receipt});},
      onCancel: () => { review.remove(); if(!log.querySelector('.is-review'))panel.classList.remove('is-reviewing');addAction('Not logged'); },
    }).then(() => { callBody.scrollTop = callBody.scrollHeight; });
  }

  function onEvent(message) {
    switch (message.type) {
      case 'ready':
        sessionId=message.session_id||null;
        started = Date.now();
        // A tour shows its step in place of the call's length.
        if (kind !== 'tour')
          clock = setInterval(() => {
            const seconds = Math.floor((Date.now() - started) / 1000);
            timer.textContent = `${Math.floor(seconds / 60)}:${
                String(seconds % 60).padStart(2, '0')}`;
          }, 1000);
        setStatus(message.voice && !chat ? 'listening' : 'typing');
        if (!message.voice) mute.hidden = sound.hidden = true;
        input.focus({preventScroll: true});
        break;
      case 'mic':
        if (!message.ok) {
          mute.hidden = true;
          setStatus('typing');
        }
        break;
      case 'state':
        setStatus(
            message.state === 'listening' && (chat || mute.hidden) ?
                'typing' :
                message.state);
        break;
      case 'caption':
        if (message.text) {
          if (message.audio) setStatus('speaking');
          if (kind !== 'tour' || !stepNarration.textContent || !message.text.endsWith(stepNarration.textContent)) addLine('agent', message.text, message.turn);
        }
        break;
      case 'heard':
        failure.textContent = ''; retry.hidden = true;
        // The Next button's own word needs no bubble.
        if (kind === 'tour' && message.text === 'Next') break;
        [...log.children]
            .find((item) => item.dataset.agentTurn === String(message.turn))
            ?.remove();
        addLine('person', message.text, message.turn);
        break;
      case 'action':
        if (message.action === 'show_step') {
          timer.textContent = `Step ${message.position} of ${message.total}`;
          stepCard.hidden = false;
          stepTitle.textContent = message.title || 'Tour';
          stepNarration.textContent = message.message || '';
          callBody.scrollTop = 0;
          const step = message.step;
          panel.dataset.step = step;
          stepReady = false;
          if (next) {next.disabled = true; next.textContent = message.position === message.total ? 'Finish tour' : 'Next';}
          showStep(message.path, message.selector).then(found => {
            if (!panel.isConnected || panel.dataset.step !== step) return;
            stepReady = true;
            if (!found) failure.textContent = 'This page is unavailable';
            setStatus(panel.dataset.state);
          });
        } else if (message.action === 'agenda') {
          topics?.remove();agenda=message.items||[];
          topics=agenda.length?agendaList(agenda,'coach-agenda'):null;
          if(topics){topics.querySelector('h3').after(progress);callBody.prepend(topics);paintProgress();}
        } else if (message.action === 'report_answer') {
          const item=[...log.children].find(row=>row.dataset.agentTurn===String(message.turn));
          if(item){item.querySelector('p')?.replaceWith(root.StellisReportContext.answer(message.exchange));
            const trace=root.StellisAnswerTrace?.link({traceId:message.exchange.trace_id});if(trace)item.append(trace);}
          callBody.scrollTop=callBody.scrollHeight;
        } else if (message.action === 'fill_goal') {
          addAction(`Filled in: ${message.values.title}`);
        } else if (message.action === 'propose_log') {
          propose(message);
        } else if (message.action === 'covered') {
          cover(message.key);
        }
        onAction(message);
        break;
      case 'retrying':
        failure.textContent = ''; retry.hidden = true;
        [...log.children].find(item => item.dataset.agentTurn === String(message.turn))?.remove();
        break;
      case 'ending':
        setStatus('ending');
        foot.hidden = true;
        recovery.hidden = true;
        break;
      case 'error':
        failure.textContent = message.message;
        retry.hidden = !message.retryable;
        recovery.hidden = false;
        break;
      case 'ended':
        finish('ended', true);
        break;
      case 'closed':
        finish('lost');
        break;
    }
  }

  const call = root.StellisIntakeCall.start(onEvent, {
    path: kind === 'tour' ?
        '/api/v1/tour/call' :
        `/api/v1/coach/call?${new URLSearchParams({kind, ...localParams(referenceDay), ...(handoff?{context:handoff.id}:{}), ...(source?{document:source.documentId,map:source.mapId,region:source.regionId}:{})})}`,
    listen: !chat,
  });
  call.setSpeaker(speaker);

  function finish(key, completed = false) {
    if (finished) return;
    finished = true;
    call.end();
    clearInterval(clock);
    clearTarget();
    setStatus(key);
    foot.hidden = true;
    recovery.hidden = true;
    ended.hidden = false;
    if (completed && kind === 'tour') status.textContent = 'Tour complete';
    if (completed && kind === 'morning') status.textContent = 'Check-in complete';
    panel.classList.remove('is-minimised');
    shrink.setAttribute('aria-label', 'Minimise call');
    shrink.title = 'Minimise call';
    setGlyph(shrink, 'arrows-diagonal-minimize-2');
    (destination.hidden ? close : destination).focus({preventScroll: true});
  }

  let removed=false;
  function remove() {
    if(removed)return;removed=true;
    finish('ended');
    panel.remove();
    root.removeEventListener('health-capture-saved',refreshCoverage);
    if(homeHost){homeHost.hidden=true;homeHost.parentElement.classList.remove('has-active-call');}
    if (current === controls) current = null;
    detach?.disconnect();
    onClose();
  }

  foot.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    call.sendText(text);
    input.value = '';
  });
  mute.addEventListener('click', () => {
    muted = !muted;
    call.setMuted(muted);
    mute.setAttribute('aria-pressed', String(muted));
    setGlyph(mute, muted ? 'microphone-off' : 'microphone');
  });
  sound.addEventListener('click', () => {
    speaker = !speaker;
    call.setSpeaker(speaker);
    sound.setAttribute('aria-pressed', String(!speaker));
    setGlyph(sound, speaker ? 'volume' : 'volume-off');
  });
  hangUp.addEventListener('click', () => finish('ended'));
  close.addEventListener('click', remove);
  again.addEventListener('click', () => onRestart ? onRestart() : open({kind,chat,onAction,agenda,source,host,onClose,handoff,referenceDay}));
  shrink.addEventListener('click', () => {
    const small = panel.classList.toggle('is-minimised');
    shrink.setAttribute('aria-label', small ? 'Expand call' : 'Minimise call');
    shrink.title = shrink.getAttribute('aria-label');
    setGlyph(shrink, small ? 'arrows-diagonal' : 'arrows-diagonal-minimize-2');
  });

  async function refreshCoverage(){if(!sessionId||!coverageBatches.size)return;try{const saved=await dom.api('/coach/calls/'+sessionId);for(const action of saved.saved_actions||[]){if(!action.undo_applied||!coverageBatches.has(action.batch_id))continue;for(const p of coverageBatches.get(action.batch_id)){const key=p.item.startsWith('tracker:')?'dose:'+p.item.slice(8):p.item;const row=topics?.querySelector(`[data-key="${CSS.escape(key)}"]`);row?.removeAttribute('data-covered');}coverageBatches.delete(action.batch_id);}paintProgress();}catch{}}
  root.addEventListener('health-capture-saved',refreshCoverage);
  let detach;
  const controls = {end: () => finish('ended'), close: remove};
  if(host){detach=new MutationObserver(()=>{if(!panel.isConnected)remove();});detach.observe(document.body,{childList:true,subtree:true});}
  current = controls;
  return controls;
}

root.StellisCoachCall = {
  incoming,
  open,
  offerTour,
  localParams
};
})(window);
