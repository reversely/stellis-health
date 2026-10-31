const fileInput = document.querySelector('#audio-file');
const status = document.querySelector('#status');
const list = document.querySelector('#conversation-list');
const dropZone = document.querySelector('#drop-zone');
const importDialog = document.querySelector('#import-dialog');
const importStatus = document.querySelector('#import-status');
const listView = document.querySelector('#list-view');
const detailView = document.querySelector('#detail-view');
const player = document.querySelector('#audio-player');
const seekSlider = document.querySelector('#seek-slider');
const playPauseButton = document.querySelector('#play-pause');
const skipSilenceCheckbox = document.querySelector('#skip-silence');
const skipSilenceStatus = document.querySelector('#skip-silence-status');
let silenceSpans = [];
let silenceScanTimer = null;
let manualSeekUntil = 0;
// Deferred UI modules must finish before an initial route uses their exports.
const modulesReady = new Promise(resolve => {
  if (document.readyState === 'complete') resolve();
  else document.addEventListener('DOMContentLoaded', resolve, {once:true});
});
const API = '/api/v1';
const conversationMore = document.querySelector('#conversation-more');
for (const menu of [conversationMore, document.querySelector('#library-more'), document.querySelector('#record-more'), document.querySelector('#notebook-more')]) {
  menu.addEventListener('click', event => { if (event.target.closest('button, a')) menu.open = false; });
  document.addEventListener('pointerdown', event => { if (!menu.contains(event.target)) menu.open = false; });
  menu.addEventListener('keydown', event => {
    if (event.key === 'Escape') { menu.open = false; menu.querySelector('summary').focus(); }
  });
}
const themeButton = document.querySelector('#theme-toggle');
function syncThemeButton() {
  const light = document.documentElement.dataset.theme === 'light';
  const label = `Switch to ${light ? 'dark' : 'light'} mode`;
  themeButton.setAttribute('aria-label', label);
  themeButton.title = label;
  document.querySelector('meta[name="theme-color"]').content = light ? '#27222B' : '#2A252F';
}
syncThemeButton();
// Colours change at once on a theme switch; transitions return two frames later (motion.css).
function swapTheme(theme) {
  const root = document.documentElement;
  root.classList.add('theme-swapping');
  root.dataset.theme = theme;
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove('theme-swapping')));
}
themeButton.addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  swapTheme(theme);
  try { localStorage.setItem('stellis-theme', theme); } catch {}
  syncThemeButton();
});
const processButton = document.querySelector('#process-button');
const rematchButton = document.querySelector('#rematch-button');
const alignWordsButton = document.querySelector('#align-words-button');
const editTranscriptButton = document.querySelector('#edit-transcript-button');
const discardEditsButton = document.querySelector('#discard-edits-button');
const restorePassageButton = document.querySelector('#restore-passage-button');
const editStatus = document.querySelector('#edit-status');
const processingStatus = document.querySelector('#processing-status');
const transcriptView = document.querySelector('#transcript');
// What a running speech job is doing and how long is left, from how fast its progress has moved so far.
const jobStarts = new Map();
let transcriptWait = '';
function jobProgressText(job) {
  if (job.state === 'queued') return 'Queued to transcribe';
  const stage = job.stage.charAt(0).toUpperCase() + job.stage.slice(1);
  const done = Math.max(0, Math.min(1, Number(job.progress) || 0));
  if (!jobStarts.has(job.id)) jobStarts.set(job.id, {at: Date.now(), done});
  const start = jobStarts.get(job.id), rate = (done - start.done) / ((Date.now() - start.at) / 1000);
  const left = rate > 0 ? Math.ceil((1 - done) / rate) : null;
  const time = left === null ? '' : left < 60 ? `, about ${Math.max(5, Math.ceil(left / 5) * 5)} s left` : `, about ${Math.ceil(left / 60)} min left`;
  return `${stage}: ${Math.round(done * 100)}%${time}`;
}
/** The Summary's status line, with the spinner while something is still on its way. */
function setSummaryStatus(text, loading = false) {
  const status = document.querySelector('#enhanced-status');
  status.textContent = text;
  status.classList.toggle('is-loading', loading);
}
function setTranscriptLoading(loading) {
  processingStatus.classList.toggle('is-loading', loading);
  document.querySelector('#transcript-tab').classList.toggle('is-loading', loading);
  transcriptView.setAttribute('aria-busy', String(loading));
}
let activeConversationId = null;
let jobTimer = null;
let aiRunTimer = null;
let cleanRunTimer = null;
let cleanRuns = [];
let currentCleanRun = null;
let awaitingCleanRun = false;
let cleanEditMode = false;
let cleanEditError = null;
const pendingCleanEdits = new Map();
let transcriptSegments = [];
let currentTranscript = null;
let highlightsOnly = false;
let rematchChangesOnly = false;
let transcriptEditMode = false;
let focusedEditSegmentId = null;
let savingTranscriptEdits = false;
let transcriptEditError = null;
const pendingTranscriptEdits = new Map();
let selectedExcerpt = null;
let commentsOpen = false;
let activeWordElement = null;
let lastIntentionalScroll = 0;
let lastAutoFollow = 0;
for (const name of ['wheel', 'touchstart', 'keydown']) window.addEventListener(name, () => { lastIntentionalScroll = Date.now(); }, {passive:true});
const recordingPanel = document.querySelector('#recording-panel');
const recordingState = document.querySelector('#recording-state');
const recordingDetail = document.querySelector('#recording-detail');
const recordingTimer = document.querySelector('#recording-timer');
let capture = null;
let captureRequesting = false;
let captureFinalizing = false;
let captureHeartbeatId = null;
let captureHeartbeatAt = 0;
// A fresh local heartbeat keeps another tab from offering recovery during capture.
const recordingHeartbeatKey = id => `stellis-recording-live-${id}`;
function clearCaptureHeartbeat(id) {
  if (captureHeartbeatId !== id) return;
  try { localStorage.removeItem(recordingHeartbeatKey(id)); } catch {}
  captureHeartbeatId = null;
}
let recordingChatTimer = null;
let recordingPrimaryView = 'notes';
let librarySearchResults = [];
let librarySearchTotal = 0;
let librarySearchLimited = false;
let libraryCategories = [];
let libraryConversations = [];
let libraryJobTimer;
let railTags = new Set();
let libraryCollections = [];
let libraryAgents = [];
let libraryPages = [];
/** Documents filed in a notebook; unsorted ones live on the Records page. */
let libraryDocuments = [];
let activePersonId = null;
let transcriptSearchMatches = [];
let transcriptSearchIndex = -1;

function time(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}
function setStatus(message, success = false) {
  status.textContent = message;
  status.classList.toggle('success', success);
}
async function json(response) {
  const data = await response.json();
  if (response.status === 401) { location.replace('/auth'); throw new Error('Login required'); }
  if (!response.ok) throw new Error(data.detail || 'The request failed.');
  return data;
}
const notesStates = {recording:null, conversation:null, page:null};
const notesEditors = {recording:null, conversation:null, page:null};
let notesModulePromise = null;
function notesEndpoint(scope, id) {
  if (scope === 'page') return `${API}/pages/${encodeURIComponent(id)}/markdown`;
  return `${API}/${scope === 'recording' ? 'recordings' : 'conversations'}/${encodeURIComponent(id)}/notes`;
}
// A note's own GET returns the whole page; the other scopes read from the same URL they write to.
const notesReadUrl = (scope, id) => scope === 'page' ? `${API}/pages/${encodeURIComponent(id)}` : notesEndpoint(scope, id);
let conversationImages = [];
let pageImages = [];
let recordingImages = [];
let imageDialogOwner = null;
let pendingImageFiles = [];
let imagePreviewUrl = null;
let selectedImageSegmentId = null;
const imageDialog = document.querySelector('#image-dialog');
const imageStatus = document.querySelector('#image-status');

function imageEndpoint(scope, id) {
  return `${API}/${scope === 'recording' ? 'recordings' : scope === 'page' ? 'pages' : 'conversations'}/${encodeURIComponent(id)}/images`;
}
function imageFileUrl(scope, id, imageId, download = false) {
  return `${imageEndpoint(scope, id)}/${encodeURIComponent(imageId)}/file${download ? '?download=true' : ''}`;
}
async function removeImageFromNotes(scope, ownerId, imageId) {
  const state = notesStates[scope];
  if (state?.id !== ownerId) return true;
  const previous = state.pendingMarkdown;
  const next = previous.replace(new RegExp(`!\\[[^\\]\\n]*\\]\\(stellis-image:${imageId}\\)`, 'g'), '')
    .replace(/\n{3,}/g, '\n\n');
  if (next === previous) return true;
  state.pendingMarkdown = next;
  if (!await flushNotes(state)) {state.pendingMarkdown = previous; return false;}
  notesEditors[scope]?.setMarkdown(next);
  return true;
}
function imageSegmentFromSelection() {
  const selection = window.getSelection();
  if (!selection?.toString().trim()) return null;
  const node = selection.anchorNode;
  const row = (node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement)?.closest?.('.segment');
  return row && transcriptView.contains(row) ? row.dataset.segmentId : null;
}
function makeImageCard(item, scope, ownerId) {
  const card = document.createElement('figure'); card.className = 'image-card'; card.dataset.imageId = item.id;
  const thumb = document.createElement('button'); thumb.type = 'button'; thumb.className = 'image-thumb';
  thumb.setAttribute('aria-label', `Expand image at ${time(item.media_position_ms / 1000)}`);
  const img = document.createElement('img'); img.src = imageFileUrl(scope, ownerId, item.id);
  img.alt = item.caption || `Meeting image at ${time(item.media_position_ms / 1000)}`; img.loading = 'lazy';
  thumb.append(img);
  thumb.addEventListener('click', () => {
    const viewer = document.querySelector('#image-viewer');
    const expanded = document.querySelector('#expanded-image');
    expanded.src = img.src; expanded.alt = img.alt; viewer.showModal();
  });
  const info = document.createElement('figcaption'); info.className = 'image-card-info';
  const position = document.createElement('button'); position.type = 'button';
  position.textContent = time(item.media_position_ms / 1000);
  position.setAttribute('aria-label', `Go to image time ${time(item.media_position_ms / 1000)}`);
  position.addEventListener('click', () => {
    if (scope === 'conversation') { player.pause(); player.currentTime = item.media_position_ms / 1000; showConversationTab('transcript'); }
  });
  const caption = document.createElement('p'); caption.textContent = item.caption || 'Meeting image';
  if (item.quote) {
    const anchor = document.createElement('p');
    const source = transcriptSegments.find(segment => segment.id === item.segment_id);
    anchor.textContent = `${source ? source.text.slice(item.start_offset, item.end_offset) === item.quote ? '' : 'Source changed: ' : 'Earlier transcript: '}“${item.quote}”`;
    info.append(anchor);
  }
  const actions = document.createElement('div'); actions.className = 'image-card-actions';
  const edit = document.createElement('button'); edit.type = 'button'; edit.textContent = 'Edit caption';
  edit.addEventListener('click', async () => {
    const next = window.prompt('Image caption', item.caption || '');
    if (next === null || next.trim() === item.caption) return;
    try {
      await json(await fetch(`${imageEndpoint(scope, ownerId)}/${encodeURIComponent(item.id)}`, {
        method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({caption:next.trim()})}));
      await loadImages(scope, ownerId);
    } catch (error) {setStatus(error.message);}
  });
  const download = document.createElement('a'); download.href = imageFileUrl(scope, ownerId, item.id, true);
  download.className = 'button-with-icon';
  download.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/></svg><span>Download</span>';
  download.setAttribute('download', `meeting-image-${item.id}.webp`);
  const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Delete';
  remove.addEventListener('click', async () => {
    if (!window.confirm('Delete this image permanently?')) return;
    try {
      if (!await removeImageFromNotes(scope, ownerId, item.id)) return;
      await json(await fetch(`${imageEndpoint(scope, ownerId)}/${encodeURIComponent(item.id)}`, {method:'DELETE'}));
      await loadImages(scope, ownerId);
    } catch (error) {setStatus(error.message);}
  });
  actions.append(edit, download, remove);
  info.append(position, caption, actions); card.append(thumb, info);
  return card;
}
function renderConversationImages() {
  transcriptView.querySelectorAll('.image-card').forEach(card => card.remove());
  const unplaced = document.querySelector('#conversation-image-list'); unplaced.replaceChildren();
  if (!activeConversationId) return;
  const lastByRow = new Map();
  for (const item of conversationImages) {
    let segment = transcriptSegments.find(value => value.id === item.segment_id);
    if (!segment && transcriptSegments.length) {
      segment = transcriptSegments[0];
      for (const candidate of transcriptSegments) {
        if (candidate.start_ms > item.media_position_ms) break;
        segment = candidate;
      }
    }
    const row = segment && transcriptView.querySelector(`[data-segment-id="${CSS.escape(segment.id)}"]`);
    const card = makeImageCard(item, 'conversation', activeConversationId);
    if (!row) {unplaced.append(card); continue;}
    const predecessor = lastByRow.get(row) || row;
    predecessor.after(card); lastByRow.set(row, card);
  }
}
function renderRecordingImages() {
  const list = document.querySelector('#recording-image-list'); list.replaceChildren();
  const recordingId = capture?.session.id || notesStates.recording?.id;
  const markdown = notesStates.recording?.pendingMarkdown || '';
  if (recordingId) for (const item of recordingImages) {
    if (!markdown.includes(`stellis-image:${item.id}`)) list.append(makeImageCard(item, 'recording', recordingId));
  }
}
async function loadImages(scope, id) {
  const items = await json(await fetch(imageEndpoint(scope, id)));
  if (scope === 'page') {
    if (notesStates.page?.id === id) pageImages = items;
  } else if (scope === 'recording') {
    if (capture?.session.id !== id && notesStates.recording?.id !== id) return;
    recordingImages = items; renderRecordingImages();
  } else if (activeConversationId === id) {
    conversationImages = items; renderConversationImages();
  }
}
function chooseImageFile(file) {
  const files = Array.isArray(file) ? file : file ? [file] : [];
  if (!files.length) return;
  pendingImageFiles = [];
  document.querySelector('#save-image').disabled = true;
  if (files.length > 10) { imageStatus.textContent = 'Choose up to 10 images.'; return; }
  if (files.some(item => !['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(item.type))) {
    imageStatus.textContent = 'Use a PNG, JPEG, WebP, or GIF image.'; return;
  }
  if (files.some(item => item.size > 12_000_000)) {imageStatus.textContent = 'Image must be smaller than 12 MB.'; return;}
  if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
  file = files[0]; pendingImageFiles = files; imagePreviewUrl = URL.createObjectURL(file);
  const preview = document.querySelector('#image-preview'); preview.src = imagePreviewUrl; preview.hidden = false;
  imageStatus.textContent = files.length > 1 ? `${files.length} images selected` : '';
  const save = document.querySelector('#save-image'); save.querySelector('span').textContent = files.length > 1 ? `Save ${files.length} images` : 'Save image';
  save.disabled = false;
}
function openImageDialog(scope, id, file = null, segmentId = null, excerpt = null) {
  if (!id) return;
  const segment = scope === 'conversation' ? transcriptSegments.find(item => item.id === segmentId) : null;
  const position = segment ? segment.start_ms : scope === 'recording' ? Math.round(captureElapsedMs()) : Math.round(player.currentTime * 1000);
  imageDialogOwner = {scope, id, position, segmentId:segment?.id || null, excerpt};
  pendingImageFiles = []; imageStatus.textContent = '';
  document.querySelector('#image-caption').value = '';
  document.querySelector('#image-anchor-label').textContent = excerpt ?
    `At ${time(position / 1000)}, “${segment.text.slice(excerpt.start_offset, excerpt.end_offset)}”` : `At ${time(position / 1000)}`;
  document.querySelector('#image-preview').hidden = true;
  document.querySelector('#save-image').disabled = true;
  document.querySelector('#save-image span').textContent = 'Save image';
  document.querySelector('#image-file').value = '';
  imageDialog.showModal();
  if (file) chooseImageFile(file);
  document.querySelector('#image-paste-target').focus();
}
function clipboardImage(event) {
  return [...(event.clipboardData?.items || [])].find(item => item.type.startsWith('image/'))?.getAsFile() || null;
}
let notesImageChoice = null;
function chooseNotesImage(scope, selection) {
  const state = notesStates[scope];
  if (!state) return;
  notesImageChoice = {scope, id:state.id, selection};
  const input = document.querySelector('#notes-image-file');
  input.value = ''; input.click();
}
async function saveNotesImage(scope, id, file, selection) {
  if (!file || notesStates[scope]?.id !== id) return;
  const status = notesElements(scope).status;
  const form = new FormData();
  form.append('file', file, file.name || 'screenshot.png');
  if (scope !== 'page') form.append('media_position_ms', String(scope === 'recording' ? Math.round(captureElapsedMs()) :
    Math.round(player.currentTime * 1000)));
  status.textContent = 'Adding image…';
  try {
    const image = await json(await fetch(imageEndpoint(scope, id), {method:'POST', body:form}));
    if (notesStates[scope]?.id !== id) return;
    const editor = await ensureNotesEditor(scope);
    if (scope === 'page') pageImages = [...pageImages, image];
    editor.insertImage(image.id, image.caption, selection);
    await loadImages(scope, id);
  } catch (error) {status.textContent = error.message;}
}
function pasteNotesImage(event, scope) {
  const file = clipboardImage(event);
  const state = notesStates[scope];
  if (!file || !state) return;
  event.preventDefault(); event.stopPropagation();
  saveNotesImage(scope, state.id, file, notesEditors[scope]?.captureSelection());
}
function pasteImage(event, scope) {
  const file = clipboardImage(event);
  if (!file) return;
  event.preventDefault(); event.stopPropagation();
  if (!imageDialog.open) {
    const id = scope === 'recording' ? capture?.session.id : activeConversationId;
    openImageDialog(scope, id, file, scope === 'conversation' ? imageSegmentFromSelection() : null);
  } else chooseImageFile(file);
}
document.querySelector('#recording-notes-editor').addEventListener('paste', event => {
  pasteNotesImage(event, 'recording');
}, true);
document.querySelector('#conversation-notes-editor').addEventListener('paste', event => {
  pasteNotesImage(event, 'conversation');
}, true);
document.querySelector('#page-notes-editor').addEventListener('paste', event => {
  pasteNotesImage(event, 'page');
}, true);
document.querySelector('#notes-image-file').addEventListener('change', event => {
  if (!notesImageChoice) return;
  const {scope, id, selection} = notesImageChoice;
  notesImageChoice = null;
  saveNotesImage(scope, id, event.target.files?.[0], selection);
});
transcriptView.addEventListener('paste', event => {
  if (activeConversationId) pasteImage(event, 'conversation');
}, true);
document.querySelector('#image-paste-target').addEventListener('paste', event => pasteImage(event, imageDialogOwner?.scope));
document.querySelector('#image-paste-target').addEventListener('click', () => document.querySelector('#image-file').click());
document.querySelector('#image-paste-target').addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); document.querySelector('#image-file').click();}
});
document.querySelector('#image-paste-target').addEventListener('dragover', event => event.preventDefault());
document.querySelector('#image-paste-target').addEventListener('drop', event => {
  event.preventDefault(); chooseImageFile(event.dataTransfer?.files?.[0]);
});
document.querySelector('#choose-image').addEventListener('click', () => document.querySelector('#image-file').click());
document.querySelector('#image-file').addEventListener('change', event => chooseImageFile(event.target.files?.[0]));
document.querySelector('#add-recording-image').addEventListener('click', () =>
  chooseNotesImage('recording', notesEditors.recording?.captureSelection()));
document.querySelector('#add-conversation-image').addEventListener('pointerdown', () => {
  selectedImageSegmentId = imageSegmentFromSelection();
});
document.querySelector('#add-conversation-image').addEventListener('click', () => {
  openImageDialog('conversation', activeConversationId, null, selectedImageSegmentId || imageSegmentFromSelection());
  selectedImageSegmentId = null;
});
document.querySelector('#cancel-image').addEventListener('click', () => imageDialog.close());
imageDialog.addEventListener('close', () => {
  if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
  imagePreviewUrl = null; pendingImageFiles = []; imageDialogOwner = null;
});
document.querySelector('#image-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!pendingImageFiles.length || !imageDialogOwner) return;
  const {scope, id, position, segmentId, excerpt} = imageDialogOwner;
  const save = document.querySelector('#save-image'); save.disabled = true; imageStatus.textContent = 'Saving image…';
  const cancel = document.querySelector('#cancel-image');
  const choose = document.querySelector('#choose-image');
  cancel.disabled = true; choose.disabled = true;
  let saved = 0;
  try {
    for (const file of [...pendingImageFiles]) {
      const form = new FormData();
      form.append('file', file, file.name || 'screenshot.png');
      form.append('media_position_ms', String(position));
      form.append('caption', document.querySelector('#image-caption').value.trim());
      if (segmentId) form.append('segment_id', segmentId);
      if (excerpt) for (const field of ['start_offset', 'end_offset', 'expected_revision']) form.append(field, String(excerpt[field]));
      await json(await fetch(imageEndpoint(scope, id), {method:'POST', body:form}));
      pendingImageFiles.shift(); saved += 1;
    }
    imageDialog.close(); await loadImages(scope, id); setStatus(saved === 1 ? 'Image saved.' : `${saved} images saved.`, true);
  } catch (error) {
    if (saved) await loadImages(scope, id);
    imageStatus.textContent = `${saved ? `${saved} saved. ` : ''}${error.message}`;
    save.disabled = false;
  } finally {
    cancel.disabled = false; choose.disabled = false;
  }
});
document.querySelector('#close-image-viewer').addEventListener('click', () => document.querySelector('#image-viewer').close());
function notesDraftKey(scope, id) { return `stellis-notes-${scope}-${id}`; }
function notesElements(scope) {
  return {root:document.querySelector(`#${scope}-notes-editor`),
    toolbar:document.querySelector(`#${scope}-notes-toolbar`),
    status:document.querySelector(`#${scope}-notes-status`),
    retry:document.querySelector(`#${scope}-notes-retry`)};
}
// Two routes can ask for the same editor at once (the app routes twice at start); one editor per root, built once.
const notesEditorBuilds = {};
function ensureNotesEditor(scope) {
  return notesEditorBuilds[scope] ||= buildNotesEditor(scope);
}
async function buildNotesEditor(scope) {
  notesModulePromise ||= import('/static/notes-editor.bundle.js');
  const {createNotesEditor} = await notesModulePromise;
  const elements = notesElements(scope);
  const editor = createNotesEditor(elements.root, elements.toolbar, (markdown, dates) => {
    const state = notesStates[scope];
    if (!state) return;
    state.pendingMarkdown = markdown; state.pendingDates = dates;
    if (scope === 'recording') renderRecordingImages();
    if (scope === 'page') adoptForeignImages(state);
    try { localStorage.setItem(notesDraftKey(scope, state.id), JSON.stringify({markdown, revision:state.revision})); } catch {}
    elements.status.textContent = 'Unsaved'; elements.retry.hidden = true;
    clearTimeout(state.timer);
    state.timer = setTimeout(() => flushNotes(state), 600);
  }, error => {elements.status.textContent = error.message;}, {
    imageUrlForId:imageId => imageFileUrl(scope, notesStates[scope]?.id, imageId),
    onOpenImage:(src, alt) => {
      const viewer = document.querySelector('#image-viewer');
      const expanded = document.querySelector('#expanded-image');
      expanded.src = src; expanded.alt = alt; viewer.showModal();
    },
    onPickImage:selection => chooseNotesImage(scope, selection),
    todoDates:scope === 'conversation' || scope === 'page',
  });
  notesEditors[scope] = editor;
  elements.retry.addEventListener('click', () => retryNotes(scope));
  return editor;
}
// A note keeps its own copy of every image. An image pasted in from another note, meeting, or recording still names
// its first owner, so each such reference is copied to this note and the Markdown is rewritten to the copy.
const copyingImages = new Set();
async function adoptForeignImages(state) {
  const owned = new Set(pageImages.map(item => item.id));
  const foreign = [...new Set([...state.pendingMarkdown.matchAll(/stellis-image:([0-9a-f-]{36})/gi)].map(match => match[1]))]
    .filter(id => !owned.has(id) && !copyingImages.has(id));
  if (!foreign.length) return;
  for (const id of foreign) copyingImages.add(id);
  try {
    for (const id of foreign) {
      const copy = await json(await fetch(`${API}/images/${encodeURIComponent(id)}/copy`, {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({scope:'page', owner_id:state.id})}));
      if (notesStates.page !== state) return;
      pageImages = [...pageImages, {id:copy.id}];
      state.pendingMarkdown = state.pendingMarkdown.split(id).join(copy.id);
      notesEditors.page.setMarkdown(state.pendingMarkdown, {keepSelection:true, dates:state.savedDates,
        onLoaded:() => {state.pendingDates = notesEditors.page.getTodoDates();}});
    }
    clearTimeout(state.timer); state.timer = setTimeout(() => flushNotes(state), 600);
  } catch (error) { notesElements('page').status.textContent = error.message; }
  finally { for (const id of foreign) copyingImages.delete(id); }
}
async function loadNotes(scope, id) {
  const editor = await ensureNotesEditor(scope);
  const remote = await json(await fetch(notesReadUrl(scope, id)));
  let markdown = remote.markdown;
  let recovered = false;
  try {
    const draft = JSON.parse(localStorage.getItem(notesDraftKey(scope, id)) || 'null');
    if (draft?.markdown !== undefined && draft.markdown !== remote.markdown) {
      const merged = StellisNotesMerge.mergeWithRemote(remote.markdown, draft.markdown, draft.revision === remote.revision);
      markdown = merged.markdown;
      recovered = merged.action !== 'adopt';
      if (!recovered) localStorage.removeItem(notesDraftKey(scope, id));
    }
  } catch {}
  const state = {scope,id,revision:remote.revision,savedMarkdown:remote.markdown,
    pendingMarkdown:markdown,timer:null,savePromise:null,
    savedDates:remote.todo_dates || {}, pendingDates:{}};
  notesStates[scope] = state;
  editor.setMarkdown(markdown, {dates:state.savedDates, onLoaded:() => {state.pendingDates = editor.getTodoDates();}});
  if (scope === 'recording') renderRecordingImages();
  const elements = notesElements(scope);
  elements.status.textContent = recovered ? 'Recovered draft' : '';
  elements.retry.hidden = true;
  if (recovered) state.timer = setTimeout(() => flushNotes(state), 500);
  return state;
}
// Agent suggestions stand beside the editor, never inside its document (ADR 0059, #270).
function mountSuggestions(scope, kind, panel, toolbar, currentId) {
  return StellisSuggestions.mount({
    api:API, icon, kind, panel, toolbar, saveLine:panel.querySelector('.notes-save-line'),
    editorRoot:notesElements(scope).root, id:currentId, setStatus,
    savedMarkdown:() => notesStates[scope]?.savedMarkdown || '',
    flush:() => flushNotes(notesStates[scope]),
    // The server wrote the accepted lines; read the notes again so they and their new IDs appear.
    reload:async () => {
      const old = notesStates[scope];
      if (old) clearTimeout(old.timer);
      await loadNotes(scope, currentId());
    },
    resolveSegment:(segmentId, suggestion) => suggestion.source_kind === 'conversation' && suggestion.source_id === activeConversationId
      ? transcriptSegments.find(segment => segment.id === segmentId) || null : null,
    openEvidence:openSuggestionEvidence,
  });
}
// The evidence link seeks the transcript. A segment id that does not resolve (sample meetings are not transcribed)
// opens the transcript at its top and leaves the quote on the card.
async function openSuggestionEvidence(suggestion, evidence) {
  if (suggestion.source_kind !== 'conversation') return;
  if (suggestion.source_id !== activeConversationId) {
    history.pushState({}, '', `/conversation/${encodeURIComponent(suggestion.source_id)}?at=${encodeURIComponent(evidence.segment_id)}`);
    await route().catch(error => setStatus(error.message));
    return;
  }
  try {
    highlightsOnly = false; showConversationTab('transcript');
    await showTranscript(activeConversationId);
  } catch (error) { setStatus(error.message); }
  const segment = transcriptSegments.find(item => item.id === evidence.segment_id);
  if (!segment) { document.querySelector('#transcript-card').scrollIntoView({block:'start'}); return; }
  const seek = () => { player.currentTime = segment.start_ms / 1000; };
  if (player.readyState >= 1) seek(); else player.addEventListener('loadedmetadata', seek, {once:true});
  transcriptView.querySelector(`[data-segment-id="${CSS.escape(segment.id)}"]`)?.scrollIntoView({block:'center'});
}
const suggestionsUi = {
  conversation:mountSuggestions('conversation', 'conversation', document.querySelector('#my-notes-panel'),
    document.querySelector('#conversation-notes-toolbar'), () => activeConversationId),
  page:mountSuggestions('page', 'page', document.querySelector('#page-view'),
    document.querySelector('#page-notes-toolbar'), () => activeNoteId),
};
// Due dates the editor holds that the server does not yet, by task ID; null clears a date. Lines without an ID wait for one.
function changedDates(state) {
  const changes = {};
  for (const [id, due] of Object.entries(state.pendingDates || {})) {
    const saved = state.savedDates?.[id] || null;
    if (JSON.stringify(due) !== JSON.stringify(saved && {due_date:saved.due_date, due_time:saved.due_time ?? null})) changes[id] = due;
  }
  return changes;
}
async function flushNotes(state) {
  if (!state || state.savePromise) return state?.savePromise || true;
  clearTimeout(state.timer);
  if (state.pendingMarkdown === state.savedMarkdown && !Object.keys(changedDates(state)).length) return true;
  const elements = notesElements(state.scope);
  state.savePromise = (async () => {
    while (state.pendingMarkdown !== state.savedMarkdown || Object.keys(changedDates(state)).length) {
      const markdown = state.pendingMarkdown;
      const todoDates = changedDates(state);
      elements.status.textContent = 'Saving…';
      try {
        const saved = await json(await fetch(notesEndpoint(state.scope, state.id), {
          method:'PUT',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({markdown,expected_revision:state.revision,
            ...(Object.keys(todoDates).length ? {todo_dates:todoDates} : {})}),
        }));
        state.revision = saved.revision; state.savedMarkdown = saved.markdown; state.savedDates = saved.todo_dates || {};
        // The server adds task IDs. When that is the only difference, adopt its text so the loop ends.
        if (state.pendingMarkdown !== saved.markdown && StellisNotesMerge.sameIgnoringIds(state.pendingMarkdown, saved.markdown)) {
          state.pendingMarkdown = saved.markdown;
          // Wait for the reload so dates carried onto the new IDs are counted before the loop decides to stop.
          const editor = notesStates[state.scope] === state ? notesEditors[state.scope] : null;
          if (editor) await new Promise(resolve => editor.setMarkdown(saved.markdown, {
            keepSelection:true, dates:state.savedDates,
            onLoaded:() => {state.pendingDates = editor.getTodoDates(); resolve();}}));
        }
        if (state.pendingMarkdown === state.savedMarkdown && !Object.keys(changedDates(state)).length) {
          try {localStorage.removeItem(notesDraftKey(state.scope, state.id));} catch {}
          elements.status.textContent = 'Saved'; elements.retry.hidden = true;
        }
      } catch (error) {
        elements.status.textContent = error.message; elements.retry.hidden = false;
        return false;
      }
    }
    return true;
  })().finally(() => {state.savePromise = null;});
  return state.savePromise;
}
async function retryNotes(scope) {
  const state = notesStates[scope];
  if (!state) return;
  try {
    const remote = await json(await fetch(notesReadUrl(scope, state.id)));
    if (remote.revision !== state.revision) {
      const merged = StellisNotesMerge.mergeWithRemote(remote.markdown, state.pendingMarkdown);
      state.pendingMarkdown = merged.markdown;
      state.revision = remote.revision; state.savedMarkdown = remote.markdown; state.savedDates = remote.todo_dates || {};
      notesEditors[scope].setMarkdown(state.pendingMarkdown, {keepSelection:true, dates:state.savedDates,
        onLoaded:() => {state.pendingDates = notesEditors[scope].getTodoDates();}});
      if (merged.action === 'adopt') {
        try {localStorage.removeItem(notesDraftKey(scope, state.id));} catch {}
        const elements = notesElements(scope);
        elements.status.textContent = 'Saved'; elements.retry.hidden = true;
      }
    }
    await flushNotes(state);
  } catch (error) {notesElements(scope).status.textContent = error.message;}
}
async function recoverNotesDraft(recordingId) {
  const key = notesDraftKey('recording', recordingId);
  let draft;
  try {draft = JSON.parse(localStorage.getItem(key) || 'null');} catch {return;}
  if (typeof draft?.markdown !== 'string') return;
  const remote = await json(await fetch(notesEndpoint('recording', recordingId)));
  const merged = StellisNotesMerge.mergeWithRemote(remote.markdown, draft.markdown);
  if (merged.action === 'adopt') {localStorage.removeItem(key); return;}
  await json(await fetch(notesEndpoint('recording', recordingId), {method:'PUT',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({markdown:merged.markdown,expected_revision:remote.revision})}));
  localStorage.removeItem(key);
}
const enhancedFields = [['overview','Overview'],['topics','Topics'],['decisions','Decisions'],['action_items','Action proposals']];
let aiConfigured = true;
async function refreshAI() {
  try { aiConfigured = (await json(await fetch(`${API}/capabilities`))).ai.configured; } catch {}
  document.body.classList.toggle('no-ai', !aiConfigured);
}
function setCategoryStatus(unavailable, message = '') {
  const status = document.querySelector('#category-status');
  if (!unavailable) { status.textContent = message; return; }
  const link = document.createElement('a'); link.href = '/settings'; link.dataset.addKey = '';
  link.textContent = 'Add a key in Account settings';
  status.replaceChildren('Automatic tags need a Nebius API key. ', link);
}
let enhancedRuns = [];
let enhancedSelectedId = null;
let enhancedEditor = null;
let enhancedState = null;
let enhancedSaveTimer = null;
let enhancedPollTimer = null;
function enhancedMarkdown(result) {
  return window.StellisSummaryText.summaryMarkdown(result, enhancedFields); // escapes model text so it cannot add blocks
}
async function ensureEnhancedEditor() {
  if (enhancedEditor) return enhancedEditor;
  notesModulePromise ||= import('/static/notes-editor.bundle.js');
  document.querySelector('#enhanced-notes-toolbar').innerHTML = document.querySelector('#conversation-notes-toolbar').innerHTML;
  document.querySelector('#enhanced-notes-toolbar [data-note-action="image"]').remove();
  const {createNotesEditor} = await notesModulePromise;
  enhancedEditor = createNotesEditor(document.querySelector('#enhanced-notes-editor'),
    document.querySelector('#enhanced-notes-toolbar'), markdown => {
      if (!enhancedState) return;
      enhancedState.pending = markdown;
      try {localStorage.setItem(`stellis-enhanced-${enhancedState.id}`,
        JSON.stringify({markdown,revision:enhancedState.revision}));} catch {}
      document.querySelector('#enhanced-save-status').textContent = 'Unsaved';
      clearTimeout(enhancedSaveTimer); enhancedSaveTimer = setTimeout(() => flushEnhanced(), 600);
    }, error => {document.querySelector('#enhanced-save-status').textContent = error.message;});
  document.querySelector('#enhanced-retry').addEventListener('click', () => retryEnhanced());
  return enhancedEditor;
}
async function flushEnhanced() {
  if (!enhancedState || enhancedState.saving) return enhancedState?.saving || true;
  clearTimeout(enhancedSaveTimer);
  if (enhancedState.pending === enhancedState.saved) return true;
  const state = enhancedState;
  state.saving = (async () => {
    while (state.pending !== state.saved) {
      const markdown = state.pending;
      document.querySelector('#enhanced-save-status').textContent = 'Saving…';
      try {
        const saved = await json(await fetch(`${API}/enhanced-notes/${encodeURIComponent(state.id)}`, {
          method:'PUT',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({markdown,expected_revision:state.revision})}));
        state.revision = saved.edit_revision; state.saved = saved.edited_markdown;
        if (state.pending === state.saved) {
          try {localStorage.removeItem(`stellis-enhanced-${state.id}`);} catch {}
          document.querySelector('#enhanced-save-status').textContent = 'Saved';
          document.querySelector('#enhanced-retry').hidden = true;
        }
      } catch (error) {
        document.querySelector('#enhanced-save-status').textContent = error.message;
        document.querySelector('#enhanced-retry').hidden = false;
        return false;
      }
    }
    return true;
  })().finally(() => {state.saving = null;});
  return state.saving;
}
async function retryEnhanced() {
  if (!enhancedState) return;
  try {
    const remote = await json(await fetch(`${API}/enhanced-notes/${encodeURIComponent(enhancedState.id)}`));
    if (remote.edit_revision !== enhancedState.revision) {
      const original = remote.edited_markdown ?? enhancedMarkdown(remote.result);
      enhancedState.pending = enhancedState.pending.includes(original) ? enhancedState.pending :
        `${original}\n\n---\n\n${enhancedState.pending}`;
      enhancedState.saved = original; enhancedState.revision = remote.edit_revision;
      enhancedEditor.setMarkdown(enhancedState.pending);
    }
    await flushEnhanced();
  } catch (error) {document.querySelector('#enhanced-save-status').textContent = error.message;}
}
async function selectEnhanced(run) {
  if (enhancedState?.id !== run.id && !(await flushEnhanced())) return;
  enhancedSelectedId = run.id;
  const writing = run.state === 'running' || run.state === 'queued';
  setSummaryStatus(run.state === 'failed' ? 'Summary failed to generate' :
    writing ? `Writing the summary (${run.stage}), about 20 s` :
    run.stale ? 'Notes or transcript changed since this summary' : '', writing);
  const sources = document.querySelector('#enhanced-sources'); sources.replaceChildren();
  if (run.state !== 'succeeded') {
    enhancedState = null;
    if (enhancedEditor) enhancedEditor.setMarkdown('');
    document.querySelector('#enhanced-notes-editor').contentEditable = 'false';
    document.querySelector('#enhanced-notes-toolbar').hidden = true;
    return;
  }
  await ensureEnhancedEditor();
  document.querySelector('#enhanced-notes-editor').contentEditable = 'true';
  document.querySelector('#enhanced-notes-toolbar').hidden = false;
  const saved = run.edited_markdown ?? enhancedMarkdown(run.result);
  let pending = saved;
  try {
    const draft = JSON.parse(localStorage.getItem(`stellis-enhanced-${run.id}`) || 'null');
    if (typeof draft?.markdown === 'string' && draft.markdown !== saved)
      pending = draft.revision === run.edit_revision || draft.markdown.includes(saved) ? draft.markdown :
        `${saved}\n\n---\n\n${draft.markdown}`;
  } catch {}
  enhancedState = {id:run.id,revision:run.edit_revision,saved,pending,saving:null};
  enhancedEditor.setMarkdown(pending);
  document.querySelector('#enhanced-save-status').textContent = pending !== saved ? 'Recovered draft' : '';
  document.querySelector('#enhanced-retry').hidden = true;
  if (pending !== saved) {clearTimeout(enhancedSaveTimer); enhancedSaveTimer = setTimeout(() => flushEnhanced(), 600);}
  for (const [field,title] of enhancedFields) renderAIItems(title, run.result[field], sources, run.source_starts);
}
async function loadEnhancedRuns(id, autoStart = false) {
  const runs = await json(await fetch(`${API}/conversations/${encodeURIComponent(id)}/enhanced-notes`));
  if (id !== activeConversationId) return;
  enhancedRuns = runs;
  document.querySelector('#enhanced-notes-panel').classList.toggle('no-key-empty',
    !aiConfigured && !runs.some(run => run.state === 'succeeded'));
  if (!runs.length && autoStart && aiConfigured && currentTranscript?.segments?.length) {
    await startEnhanced(false);
    return;
  }
  const picker = document.querySelector('#enhanced-version'); picker.replaceChildren();
  for (const run of runs) {
    const option = document.createElement('option'); option.value = run.id;
    option.textContent = new Date(run.created_at).toLocaleString(); picker.append(option);
  }
  const chosen = runs.find(run => run.id === enhancedSelectedId) || runs[0];
  picker.hidden = runs.length < 2;
  if (chosen) {
    picker.value = chosen.id;
    if (enhancedState?.id !== chosen.id || chosen.state !== 'succeeded') await selectEnhanced(chosen);
    else setSummaryStatus(chosen.stale ? 'Notes or transcript changed since this summary' : '');
  }
  else if (currentTranscript?.segments?.length) setSummaryStatus('Summary is not ready');
  else setSummaryStatus(transcriptWait ? `Waiting for the transcript (${transcriptWait}). The summary follows in about 20 s.` : 'The summary is written once the transcript is ready.', Boolean(transcriptWait));
  clearTimeout(enhancedPollTimer);
  if (runs.some(run => run.state === 'queued' || run.state === 'running'))
    enhancedPollTimer = setTimeout(() => loadEnhancedRuns(id).catch(error => {
      document.querySelector('#enhanced-status').textContent = error.message;}), 2000);
}
async function startEnhanced(force) {
  const id = activeConversationId;
  const status = document.querySelector('#enhanced-status');
  if (!currentTranscript?.segments?.length) { setSummaryStatus(transcriptWait ? `Waiting for the transcript (${transcriptWait}). The summary follows in about 20 s.` : 'The summary is written once the transcript is ready.', Boolean(transcriptWait)); return; }
  if (!(await flushNotes(notesStates.conversation))) { status.textContent = 'Save My Notes before regenerating'; return; }
  setSummaryStatus('Writing the summary, about 20 s', true);
  try {
    const run = await json(await fetch(`${API}/conversations/${encodeURIComponent(id)}/enhanced-notes`, {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force})}));
    enhancedSelectedId = run.id;
    await loadEnhancedRuns(id);
  } catch (error) {status.textContent = error.message;}
}
function showNotesChoice(choice) {
  const enhanced = choice === 'enhanced';
  document.querySelector('#my-notes-panel').hidden = enhanced;
  document.querySelector('#enhanced-notes-panel').hidden = !enhanced;
  document.querySelector('#my-notes-tab').setAttribute('aria-selected', String(!enhanced));
  document.querySelector('#enhanced-notes-tab').setAttribute('aria-selected', String(enhanced));
}
document.querySelector('#my-notes-tab').addEventListener('click', () => showNotesChoice('my'));
document.querySelector('#enhanced-notes-tab').addEventListener('click', async () => {
  showNotesChoice('enhanced');
  await flushNotes(notesStates.conversation);
  if (activeConversationId) loadEnhancedRuns(activeConversationId, true).catch(error => {
    document.querySelector('#enhanced-status').textContent = error.message;
  });
});
document.querySelector('#refresh-enhanced').addEventListener('click', () => startEnhanced(true));
document.querySelector('#enhanced-version').addEventListener('change', async event => {
  const run = enhancedRuns.find(item => item.id === event.target.value);
  if (run) await selectEnhanced(run);
});
// A demo guest can't act on the server's speech-model installer, so its card never shows to them.
const sessionStatus = fetch(`${API}/auth/status`).then(json);
sessionStatus.then(session => {
  document.querySelector('#logout-button').hidden = !session.required;
}).catch(error => setStatus(error.message));
document.querySelector('#logout-button').addEventListener('click', async () => {
  try { sessionStorage.removeItem(RAIL_CACHE); } catch {}
  try { await json(await fetch(`${API}/auth/logout`, {method:'POST'})); location.replace('/auth'); }
  catch (error) { setStatus(error.message); }
});
function appendSearchExcerpt(container, value, query) {
  const position = value.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
  if (position < 0) { container.textContent = value; return; }
  container.append(document.createTextNode(value.slice(0, position)));
  const mark = document.createElement('mark'); mark.textContent = value.slice(position, position + query.length);
  container.append(mark, document.createTextNode(value.slice(position + query.length)));
}
function renderLibrarySearch() {
  const kind = document.querySelector('#library-search-kind').value;
  const container = document.querySelector('#library-search-results');
  const query = document.querySelector('#library-search-input').value.trim();
  container.replaceChildren();
  const visible = librarySearchResults.filter(item => kind === 'all' || item.kind === kind);
  document.querySelector('#library-search-status').textContent = visible.length ?
    `${kind === 'all' ? librarySearchTotal : visible.length} results${kind === 'all' && librarySearchLimited ? ', first 150 shown' : ''}` :
    (kind === 'all' ? 'No results.' : 'No results in this category.');
  for (const item of visible) {
    const row = document.createElement('a'); row.className = 'search-result';
    row.href = item.kind === 'person' ? `/person/${encodeURIComponent(item.person_id)}` :
      item.kind === 'page' ? `/page/${encodeURIComponent(item.page_id)}` :
      item.kind === 'document' ? `/document/${encodeURIComponent(item.document_id)}?page=${item.page}` :
      `/conversation/${encodeURIComponent(item.conversation_id)}${item.segment_id ? `?at=${encodeURIComponent(item.segment_id)}` : ''}`;
    const heading = document.createElement('strong'); heading.textContent = item.conversation_title ?? item.title;
    const detail = document.createElement('span');
    detail.textContent = item.kind === 'conversation' ? 'Conversation' :
      item.kind === 'person' ? 'Confirmed person profile' :
      item.kind === 'page' ? 'Note' :
      item.kind === 'document' ? `Document, page ${item.page}` :
      item.kind === 'note' ? 'My Notes' :
      item.kind === 'summary' ? 'Summary' :
      `${item.kind === 'speaker' ? 'Speaker' : 'Passage'}, ${item.speaker}, ${time(item.start_ms / 1000)}${item.speaker_uncertain ? ', uncertain speaker match' : ''}`;
    const excerpt = document.createElement('span'); excerpt.className = 'search-excerpt';
    appendSearchExcerpt(excerpt, item.excerpt, query);
    row.append(heading, detail, excerpt); container.append(row);
    if (item.person_id && item.kind === 'speaker') {
      const profile = document.createElement('a'); profile.className = 'search-profile-link';
      profile.href = `/person/${encodeURIComponent(item.person_id)}`;
      profile.textContent = `Open ${item.speaker}'s profile`;
      container.append(profile);
    }
  }
}
async function submitLibrarySearch(event) {
  event.preventDefault();
  const query = document.querySelector('#library-search-input').value.trim();
  const status = document.querySelector('#library-search-status');
  if (query.length < 2) { status.textContent = 'Enter at least two characters.'; return; }
  status.textContent = 'Searching local transcripts…';
  document.querySelector('#library-search-results').replaceChildren();
  try {
    const data = await json(await fetch(`${API}/search?q=${encodeURIComponent(query)}`));
    if (document.querySelector('#library-search-input').value.trim() !== query) return;
    librarySearchResults = data.results;
    librarySearchTotal = data.total;
    librarySearchLimited = data.limited;
    document.querySelector('#library-search-controls').hidden = false;
    document.querySelector('#library-search-kind').value = 'all';
    renderLibrarySearch();
  } catch (error) { status.textContent = error.message; }
}
function refreshTranscriptSearch() {
  const query = document.querySelector('#transcript-search-input').value.trim().toLocaleLowerCase();
  const picker = document.querySelector('#transcript-search-speaker');
  const selectedSpeaker = picker.value;
  const speakers = [...new Set(transcriptSegments.map(segment => segment.speaker_display_name))];
  picker.replaceChildren(new Option('All speakers', ''), ...speakers.map(name => new Option(name, name)));
  picker.value = speakers.includes(selectedSpeaker) ? selectedSpeaker : '';
  transcriptSearchMatches = transcriptSegments.filter(segment =>
    (!query || segment.text.toLocaleLowerCase().includes(query)) &&
    (!picker.value || segment.speaker_display_name === picker.value));
  if (!query && !picker.value) transcriptSearchMatches = [];
  for (const row of transcriptView.querySelectorAll('.segment')) row.classList.remove('search-match', 'search-current');
  for (const segment of transcriptSearchMatches) transcriptView.querySelector(`[data-segment-id="${CSS.escape(segment.id)}"]`)?.classList.add('search-match');
  if (transcriptSearchIndex >= transcriptSearchMatches.length) transcriptSearchIndex = -1;
  const current = transcriptSearchMatches[transcriptSearchIndex];
  if (current) transcriptView.querySelector(`[data-segment-id="${CSS.escape(current.id)}"]`)?.classList.add('search-current');
  document.querySelector('#transcript-search-status').textContent =
    query || picker.value ? `${transcriptSearchMatches.length} matches${current ? `, ${transcriptSearchIndex + 1} of ${transcriptSearchMatches.length}` : ''}` : '';
}
function moveTranscriptSearch(direction) {
  if (!transcriptSearchMatches.length) return;
  transcriptSearchIndex = transcriptSearchIndex < 0 ? (direction > 0 ? 0 : transcriptSearchMatches.length - 1) :
    (transcriptSearchIndex + direction + transcriptSearchMatches.length) % transcriptSearchMatches.length;
  refreshTranscriptSearch();
  const segment = transcriptSearchMatches[transcriptSearchIndex];
  transcriptView.querySelector(`[data-segment-id="${CSS.escape(segment.id)}"]`)?.scrollIntoView({block: 'center'});
  player.pause(); player.currentTime = segment.start_ms / 1000;
}
function updateTranscriptSearch() {
  transcriptSearchIndex = -1;
  if (highlightsOnly || rematchChangesOnly) {
    highlightsOnly = false; rematchChangesOnly = false;
    showTranscript(activeConversationId).catch(error => document.querySelector('#transcript-search-status').textContent = error.message);
  } else refreshTranscriptSearch();
}
async function showList() {
  // The AI settings and the library load side by side; on a slow server each round trip adds seconds.
  const aiReady = refreshAI();
  document.body.classList.remove('profile-page', 'conversation-page', 'recording-page');
  recordingPanel.hidden = true;
  document.querySelector('#vocabulary-panel').hidden = true;
  if (conversationCategoriesDialog.open) conversationCategoriesDialog.close();
  conversationMore.open = false; conversationMore.hidden = true;
  clearTimeout(jobTimer);
  clearTimeout(aiRunTimer);
  clearTimeout(enhancedPollTimer);
  clearTimeout(recordingChatTimer);
  clearTimeout(cleanRunTimer);
  clearTimeout(silenceScanTimer); silenceSpans = [];
  activeConversationId = null;
  activePersonId = null;
  listView.hidden = false;
  detailView.hidden = true;
  document.querySelector('#trash-view').hidden = true;
  document.querySelector('#person-view').hidden = true;
  hideAppPages();
  document.querySelector('#page-title').textContent = 'Recent conversations';
  const [categories] = await Promise.all([fetch(`${API}/categories`).then(json), loadLibrary(), aiReady]);
  libraryCategories = categories;
  renderNotebooks();
  renderCategoryGroups();
  // Tag cards wait for the first conversation; an empty account sees none.
  document.querySelector('#list-view [data-widget="tags"]')?.toggleAttribute('hidden', !libraryConversations.length);
  document.querySelector('#home-pillars').hidden = true;
  await window.StellisHealthDashboard?.mount(document.querySelector('#health-dashboard'), {books:libraryNotebooks, collections:libraryCollections});
  window.StellisGoals?.home(document.querySelector('#home-goals'), document.querySelector('#dashboard-notifications'));
  watchSetup();
  if (calendarOutcome) { setStatus(calendarOutcomeText(), calendarOutcome === 'connected'); calendarOutcome = null; }
  // The automatic-tags setting fills in when it arrives; the page doesn't wait for it to show.
  fetch(`${API}/categories/automation`).then(json).then(automation => {
    document.querySelector('#auto-categories').checked = automation.enabled;
    setCategoryStatus(automation.enabled && !automation.configured);
  }).catch(() => {});
  renderCategoryControls();
  renderConversationList();
  if (homeTodos) homeTodos.refresh(); else homeTodos = mountTodos(document.querySelector('#home-todos'), {linkTitle: true});
  captureControls();
  if (document.querySelector('#library-search-input').value.trim().length >= 2)
    await submitLibrarySearch(new Event('submit'));
  window.StellisCalendar?.refresh();
}
// The Home sidebar is the file structure: collections hold notebooks, notebooks hold meetings and notes.
const RAIL_STATE_KEY = 'stellis-rail-open';
// Collections and groups start open and notebooks start closed; the viewer's own choices are kept per browser.
const railChoices = new Map((() => { try { return Object.entries(JSON.parse(localStorage.getItem(RAIL_STATE_KEY)) || {}); } catch { return []; } })());
const railOpen = key => railChoices.has(key) ? railChoices.get(key) : !key.startsWith('notebook:');
/** Whether a record is filed in a notebook: its own notebook or one that sorting linked it to. */
const inNotebook = (record, notebookId) => (record.notebook_ids || [record.notebook_id]).includes(notebookId);
async function loadLibrary() {
  // One load feeds the sidebar and the Home cards; pages come with their notebook, loose ones separately.
  const [conversations, books, collections, loose, agents, documents] = await Promise.all([
    fetch(`${API}/conversations`).then(json), fetch(`${API}/notebooks`).then(json),
    fetch(`${API}/collections`).then(json), fetch(`${API}/pages?notebook_id=none`).then(json),
    fetch(`${API}/agents`).then(json).catch(() => []), fetch(`${API}/documents`).then(json)]);
  libraryDocuments = documents.filter((item) => (item.notebook_ids || [item.notebook_id]).some(Boolean));
  libraryAgents = agents;
  libraryConversations = conversations;
  libraryNotebooks = books.notebooks; notebookPalette = books.palette; notebookPresets = books.presets;
  libraryCollections = collections.collections;
  libraryPages = [...books.notebooks.flatMap(book => book.pages.map(page => ({...page, notebook_id: book.id}))),
    ...loose.map(page => ({...page, notebook_id: null}))];
  libraryLoaded = true;
}
async function reloadLibrary() {
  await loadLibrary();
  renderNotebooks(); renderCategoryGroups(); renderRailTags(); renderConversationList();
  homeTodos?.refresh();
}
// A page load keeps the sidebar this tab last showed until the library arrives, so moving between
// pages never empties it. Its links are plain hrefs, so the copy works until the live tree replaces it.
let libraryLoaded = false;
const RAIL_CACHE = 'stellis-rail-tree';
// The page's policy blocks style attributes in parsed HTML, so the copy keeps each node's styles (its
// notebook colour) in data-rail-style and they are applied through the style object, which it allows.
try {
  const cached = sessionStorage.getItem(RAIL_CACHE);
  if (cached) {
    list.innerHTML = cached;
    for (const node of list.querySelectorAll('[data-rail-style]')) { node.style.cssText = node.dataset.railStyle; delete node.dataset.railStyle; }
  }
} catch {}
function renderConversationList() {
  if (!libraryLoaded) return;
  const notebookRow = book => {
    const row = document.createElement('div'); row.className = 'rail-node rail-notebook';
    row.append(railDot(book.color), railLink(book));
    row.draggable = true;
    row.addEventListener('dragstart', event => {event.dataTransfer.setData(NOTEBOOK_DRAG,book.id);event.dataTransfer.effectAllowed='move';});
    acceptDrops(row, {[CONVERSATION_DRAG]:id=>moveConversation(conversationById(id),book), [PAGE_DRAG]:id=>movePage(pageById(id),book)});
    return row;
  };
  const byId = new Map(libraryNotebooks.map(book => [book.id, book]));
  const placed = new Set();
  const branches = [];
  for (const collection of libraryCollections) {
    const books = collection.notebook_ids.map(id => byId.get(id)).filter(Boolean);
    if (!books.length) continue;
    books.forEach(book => placed.add(book.id));
    // A family holding only its namesake notebook (Fitness in Fitness) shows that notebook once.
    if (books.length === 1 && books[0].name.trim().toLowerCase() === collection.name.trim().toLowerCase()) { branches.push(notebookRow(books[0])); continue; }
    branches.push(railBranch({key:'collection:'+collection.id, name:railLabel(collection.name),
      lead:railIcon('folder',collection.color), count:books.length, children:books.map(notebookRow),
      accepts:{[NOTEBOOK_DRAG]: id => moveNotebook(byId.get(id),collection)}}));
  }
  branches.push(...libraryNotebooks.filter(book => !placed.has(book.id)).map(notebookRow));
  list.replaceChildren(...branches);
  // No notebooks: no heading over an empty list (owner, 2026-10-28). Add Notebook sits below.
  const railHead = document.querySelector('.home-rail-head'); if (railHead) railHead.hidden = !branches.length;
  try {
    const copy = list.cloneNode(true);
    for (const node of copy.querySelectorAll('[style]')) { node.dataset.railStyle = node.getAttribute('style'); node.removeAttribute('style'); }
    sessionStorage.setItem(RAIL_CACHE, copy.innerHTML);
  } catch {}
  renderRecent();
  window.StellisNavigation?.refresh();
  clearTimeout(libraryJobTimer);
  if (libraryConversations.some(item => ['queued','running'].includes(item.processing_job?.state)))
    libraryJobTimer = setTimeout(refreshLibraryJobs, 3000);
}
const conversationById = id => libraryConversations.find(item => item.id === id);
const pageById = id => libraryPages.find(page => page.id === id);
function railDot(color) {
  const dot = document.createElement('span'); dot.className = 'recent-dot'; dot.setAttribute('aria-hidden', 'true');
  dot.style.setProperty('--dot', color); return dot;
}
function railIcon(name, color) {
  const svg = icon(name); svg.classList.add('rail-icon');
  if (color) svg.style.setProperty('--tint', color);
  return svg;
}
function railLabel(text) { const name = document.createElement('span'); name.className = 'rail-name'; name.textContent = text; return name; }
function railLink(book) {
  const link = document.createElement('a'); link.className = 'rail-name'; link.textContent = book.name;
  link.href = `/notebook/${encodeURIComponent(book.id)}`; return link;
}
function railBranch({key, name, lead, count, children = [], accepts}) {
  const collapsed = !railOpen(key);
  const branch = document.createElement('div'); branch.className = 'rail-branch';
  const head = document.createElement('div'); head.className = 'rail-node';
  const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'rail-toggle';
  toggle.setAttribute('aria-expanded', String(!collapsed)); toggle.append(icon('chevron-right'));
  toggle.setAttribute('aria-label', `${collapsed ? 'Expand' : 'Collapse'} ${name.textContent}`);
  const total = document.createElement('small'); total.textContent = count;
  head.append(toggle, lead, name, total);
  const body = document.createElement('div'); body.className = 'rail-children'; body.hidden = collapsed;
  body.append(...children);
  const flip = () => {
    railChoices.set(key, !railOpen(key));
    try { localStorage.setItem(RAIL_STATE_KEY, JSON.stringify(Object.fromEntries(railChoices))); } catch {}
    toggle.setAttribute('aria-expanded', String(railOpen(key)));
    toggle.setAttribute('aria-label', `${railOpen(key) ? 'Collapse' : 'Expand'} ${name.textContent}`);
    body.hidden = !railOpen(key);
  };
  toggle.addEventListener('click', flip);
  if (name.tagName !== 'A') name.addEventListener('click', flip);
  branch.append(head, body);
  acceptDrops(branch, accepts);
  return branch;
}
async function refreshLibraryJobs() {
  if (listView.hidden) return;
  try { libraryConversations = await json(await fetch(`${API}/conversations`)); renderConversationList(); }
  catch { libraryJobTimer = setTimeout(refreshLibraryJobs, 10000); }
}
function recentRow(item) {
  const row = document.createElement('a');
  row.className = 'recent-row';
  row.href = `/conversation/${encodeURIComponent(item.id)}`;
  const title = document.createElement('strong'); title.textContent = item.title;
  const date = document.createElement('time'); date.dateTime = capturedAt(item); date.textContent = shortDate(capturedAt(item));
  const job = item.processing_job;
  const progress = job && (job.state === 'queued' ? 'Queued' : job.state === 'running' ?
    `${job.stage.charAt(0).toUpperCase() + job.stage.slice(1)}: ${Math.round(job.progress * 100)}%` :
    job.state === 'failed' ? 'Processing stopped' : '');
  // The date leads the preview line so the title keeps the full rail width.
  const line = document.createElement('small'); line.append(date);
  if (progress) {
    const state = document.createElement('span'); state.className = 'recent-processing'; state.textContent = progress;
    line.append(' ', state);
  } else if (item.topic_line) line.append(` ${item.topic_line}`);
  row.append(title, line);
  row.draggable = true;
  row.addEventListener('dragstart', event => {
    event.dataTransfer.setData(CONVERSATION_DRAG, item.id);
    event.dataTransfer.effectAllowed = 'copyMove';
    row.classList.add('dragging');
  });
  row.addEventListener('dragend', () => row.classList.remove('dragging'));
  return row;
}
function renderCategoryControls() {
  for (const id of railTags) if (id !== 'untagged' && !libraryCategories.some(item => item.id === id)) railTags.delete(id);
  renderRailTags();
  const container = document.querySelector('#category-list'); container.replaceChildren();
  for (const category of libraryCategories) {
    const row = document.createElement('div'); row.className = 'category-row';
    const name = document.createElement('span'); name.textContent = `${category.name} (${category.conversation_count})`;
    const rename = document.createElement('button'); rename.type = 'button'; rename.textContent = 'Rename';
    rename.addEventListener('click', async () => {
      const next = window.prompt('Tag name', category.name);
      if (next === null || !next.trim()) return;
      try { await json(await fetch(`${API}/categories/${encodeURIComponent(category.id)}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:next})})); await showList(); }
      catch (error) { document.querySelector('#category-status').textContent = error.message; }
    });
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Delete';
    remove.addEventListener('click', async () => {
      if (!window.confirm(`Delete the tag “${category.name}”? Its conversations stay in the library.`)) return;
      try { await json(await fetch(`${API}/categories/${encodeURIComponent(category.id)}`, {method:'DELETE'})); await showList(); }
      catch (error) { document.querySelector('#category-status').textContent = error.message; }
    });
    row.append(name, rename, remove); container.append(row);
  }
}
async function renderConversationCategories(ids) {
  const categories = await json(await fetch(`${API}/categories`));
  const container = document.querySelector('#conversation-category-list'); container.replaceChildren();
  if (!categories.length) { const empty = document.createElement('p'); empty.textContent = 'Create tags from the ⋯ menu on Home.'; container.append(empty); }
  for (const category of categories) {
    const label = document.createElement('label'); label.className = 'category-checkbox';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = ids.includes(category.id);
    checkbox.addEventListener('change', async () => {
      checkbox.disabled = true;
      try {
        const result = await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/categories/${encodeURIComponent(category.id)}`, {method:checkbox.checked ? 'PUT' : 'DELETE'}));
        ids = result.category_ids;
      } catch (error) { checkbox.checked = !checkbox.checked; setStatus(error.message); }
      checkbox.disabled = false;
    });
    label.append(checkbox, document.createTextNode(category.name)); container.append(label);
  }
  await refreshCategorizationStatus();
}

async function refreshCategorizationStatus() {
  if (!activeConversationId) return;
  const [setting, result] = await Promise.all([
    json(await fetch(`${API}/categories/automation`)),
    json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/categorization`)),
  ]);
  const status = document.querySelector('#categorization-status');
  const retry = document.querySelector('#retry-categorization');
  status.textContent = result.state === 'failed' ? result.error :
    result.state === 'running' || result.state === 'queued' ? 'Tagging…' :
    result.state === 'unclear' ? 'Tag unclear. Choose one above.' : '';
  retry.hidden = !setting.enabled || !result.ready || result.manual_override || !['failed', 'unclear', 'idle'].includes(result.state);
  if (result.state === 'running' || result.state === 'queued') {
    const conversationId = activeConversationId;
    setTimeout(async () => {
      if (activeConversationId !== conversationId || detailView.hidden) return;
      const item = await json(await fetch(`${API}/conversations/${encodeURIComponent(conversationId)}`));
      await renderConversationCategories(item.category_ids);
    }, 2000);
  }
}
async function showTrash() {
  const items = await json(await fetch(`${API}/trash`));
  document.querySelector('#trash-status').textContent = '';
  document.querySelector('#vocabulary-panel').hidden = true; hideAppPages();
  listView.hidden = true; detailView.hidden = true; document.querySelector('#person-view').hidden = true;
  document.querySelector('#trash-view').hidden = false;
  const container = document.querySelector('#trash-list'); container.replaceChildren();
  if (!items.length) { const empty = document.createElement('p'); empty.textContent = 'Recently Deleted is empty.'; container.append(empty); }
  for (const item of items) {
    const isPage = item.kind === 'page';
    const row = document.createElement('div'); row.className = 'trash-row';
    const title = document.createElement('strong'); title.append(icon(isPage ? 'file-text' : 'microphone'), item.title);
    const restore = document.createElement('button'); restore.type = 'button'; restore.textContent = 'Restore';
    restore.addEventListener('click', async () => {
      try { await json(await fetch(`${API}/trash/${encodeURIComponent(item.id)}/restore`, {method:'POST'})); await showTrash(); }
      catch (error) { document.querySelector('#trash-status').textContent = error.message; }
    });
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Delete permanently';
    remove.addEventListener('click', async () => {
      const gone = isPage ? 'and its text' : 'and its audio and transcript';
      if (window.prompt(`Permanently delete “${item.title}” ${gone}? Type DELETE to confirm.`) !== 'DELETE') return;
      try { await json(await fetch(`${API}/trash/${encodeURIComponent(item.id)}`, {method:'DELETE'})); await showTrash(); }
      catch (error) { document.querySelector('#trash-status').textContent = error.message; }
    });
    const expiry = document.createElement('span'); expiry.className = 'trash-expiry';
    expiry.textContent = `Deletes ${new Date(item.expires_at).toLocaleString()}`;
    row.append(title, expiry, restore, remove); container.append(row);
  }
}
async function loadPeoplePanel() {
  const people = await json(await fetch(`${API}/persons`));
  const container = document.querySelector('#people-list'); container.replaceChildren();
  if (!people.length) { const empty = document.createElement('p'); empty.textContent = 'No people yet.'; container.append(empty); }
  for (const person of people) {
    const link = document.createElement('a'); link.className = 'person-list-row';
    link.href = `/person/${encodeURIComponent(person.id)}`;
    link.textContent = `${person.name} (${person.conversation_count} conversations)`;
    container.append(link);
  }
}
async function showPerson(id) {
  document.body.classList.remove('conversation-page', 'recording-page');
  recordingPanel.hidden = true;
  document.querySelector('#vocabulary-panel').hidden = true; hideAppPages();
  document.body.classList.add('profile-page');
  conversationMore.open = false; conversationMore.hidden = true;
  clearTimeout(jobTimer); clearTimeout(aiRunTimer); clearTimeout(enhancedPollTimer); clearTimeout(recordingChatTimer); clearTimeout(cleanRunTimer);
  clearTimeout(silenceScanTimer); silenceSpans = [];
  player.pause(); player.removeAttribute('src'); player.load();
  activeConversationId = null; activePersonId = id;
  const [person, all] = await Promise.all([
    json(await fetch(`${API}/persons/${encodeURIComponent(id)}`)), json(await fetch(`${API}/persons`)),
  ]);
  listView.hidden = true; detailView.hidden = true; document.querySelector('#trash-view').hidden = true;
  document.querySelector('#person-view').hidden = false;
  document.querySelector('#page-title').textContent = person.name;
  document.querySelector('#person-rename-input').value = person.name;
  document.querySelector('#person-status').textContent = '';
  const container = document.querySelector('#person-conversations'); container.replaceChildren();
  if (!person.conversations.length) { const empty = document.createElement('p'); empty.textContent = 'No active conversations confirmed for this person.'; container.append(empty); }
  for (const item of person.conversations) {
    const row = document.createElement('div'); row.className = 'person-conversation-row';
    const link = document.createElement('a'); link.href = `/conversation/${encodeURIComponent(item.conversation_id)}?at=${encodeURIComponent(item.segment_id)}&t=${item.start_ms}`;
    link.textContent = `${item.title}, ${new Date(item.created_at).toLocaleDateString()} at ${time(item.start_ms / 1000)}`;
    const note = document.createElement('small');
    note.textContent = `${item.confirmed_passages} confirmed passages${item.current_generation ? '' : ', prior transcript generation'}`;
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove association';
    remove.addEventListener('click', async () => {
      if (!window.confirm(`Remove ${person.name}'s confirmation from “${item.title}”?`)) return;
      try { await json(await fetch(`${API}/persons/${encodeURIComponent(id)}/conversations/${encodeURIComponent(item.conversation_id)}`, {method:'DELETE'})); await showPerson(id); }
      catch (error) { document.querySelector('#person-status').textContent = error.message; }
    });
    const separate = document.createElement('button'); separate.type = 'button'; separate.textContent = 'Move to new person';
    separate.addEventListener('click', async () => {
      const name = window.prompt('Name for the separate person'); if (!name?.trim()) return;
      try {
        const created = await json(await fetch(`${API}/persons`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})}));
        await json(await fetch(`${API}/persons/${encodeURIComponent(id)}/move-conversation`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({conversation_id:item.conversation_id,target_id:created.id})}));
        history.pushState({}, '', `/person/${encodeURIComponent(created.id)}`);
        await showPerson(created.id);
      } catch (error) { document.querySelector('#person-status').textContent = error.message; }
    });
    row.append(link, note, remove, separate); container.append(row);
  }
  const picker = document.querySelector('#person-merge-source');
  picker.replaceChildren(new Option('Choose a person', ''),
    ...all.filter(item => item.id !== id).map(item => new Option(`${item.name} (${item.conversation_count})`, item.id)));
  document.querySelector('#person-delete').hidden = person.total_link_count > 0;
}
async function showDetail(id) {
  const query = new URLSearchParams(location.search);
  const sourceNavigation = query.has('at') || query.has('t');
  if (sourceNavigation) highlightsOnly = false;
  // The page shows at once with what is still on its way, never blank while the requests run (#258).
  if (activeConversationId !== id) {
    hideAppPages(); listView.hidden = true; detailView.hidden = false;
    // The page class places the header controls, Add among them, so it is set with the layout.
    document.body.classList.remove('profile-page', 'recording-page'); document.body.classList.add('conversation-page');
    document.querySelector('#page-title').textContent = 'Loading the recording…';
    processingStatus.textContent = 'Loading transcript…'; setTranscriptLoading(true);
  }
  await flushNotes(notesStates.conversation);
  await leaveNote();
  await flushEnhanced();
  await refreshAI();
  document.body.classList.remove('profile-page', 'recording-page');
  recordingPanel.hidden = true;
  document.querySelector('#vocabulary-panel').hidden = true; hideAppPages();
  if (conversationCategoriesDialog.open) conversationCategoriesDialog.close();
  document.body.classList.add('conversation-page');
  conversationMore.open = false; conversationMore.hidden = false;
  clearTimeout(jobTimer);
  clearTimeout(aiRunTimer);
  clearTimeout(enhancedPollTimer);
  clearTimeout(recordingChatTimer);
  enhancedRuns = []; enhancedSelectedId = null; enhancedState = null;
  clearTimeout(cleanRunTimer);
  clearTimeout(silenceScanTimer); silenceSpans = [];
  cleanRuns = [];
  currentCleanRun = null;
  awaitingCleanRun = false;
  cleanEditMode = false;
  cleanEditError = null;
  pendingCleanEdits.clear();
  document.querySelector('#edited-tab').hidden = true;
  document.querySelector('#edited-transcript').replaceChildren();
  activeConversationId = id;
  conversationTabChosen = false;
  activePersonId = null;
  rematchChangesOnly = false;
  transcriptEditMode = false;
  pendingTranscriptEdits.clear();
  focusedEditSegmentId = null;
  transcriptEditError = null;
  transcriptSegments = [];
  conversationImages = [];
  currentTranscript = null;
  document.querySelector('#copy-transcript').hidden = true;
  document.querySelector('#transcript-search-input').value = '';
  document.querySelector('#transcript-search-speaker').value = '';
  transcriptSearchMatches = [];
  transcriptSearchIndex = -1;
  syncTranscriptEditControls();
  transcriptView.replaceChildren();
  processingStatus.textContent = 'Loading transcript…';
  setTranscriptLoading(true);
  const importReference = document.querySelector('#import-reference');
  importReference.hidden = true;
  importReference.open = false;
  document.querySelector('#import-reference-text').textContent = '';
  processButton.hidden = true;
  rematchButton.hidden = true;
  const item = await json(await fetch(`${API}/conversations/${encodeURIComponent(id)}`));
  await renderConversationCategories(item.category_ids);
  listView.hidden = true;
  detailView.hidden = false;
  document.querySelector('#person-view').hidden = true;
  document.querySelector('#page-title').textContent = item.title;
  document.querySelector('#audio-duration').textContent = time(item.duration_ms / 1000);
  seekSlider.max = String(item.duration_ms / 1000);
  seekSlider.value = '0';
  document.querySelector('#seek-segments').replaceChildren();
  player.src = `${API}/conversations/${encodeURIComponent(id)}/audio`;
  player.load();
  syncPlayerControls();
  if (skipSilenceCheckbox.checked) startSilenceScan(id).catch(error => { skipSilenceStatus.textContent = error.message; });
  await loadImages('conversation', id);
  await loadSpeech(id);
  const referenceResponse = await fetch(`${API}/conversations/${encodeURIComponent(id)}/import-reference`);
  const reference = referenceResponse.ok ? await referenceResponse.json() : null;
  if (reference && id === activeConversationId) {
    document.querySelector('#import-reference-text').textContent = reference.transcript_text;
    importReference.hidden = false;
  }
  const notes = await loadNotes('conversation', id);
  await suggestionsUi.conversation.load().catch(() => {});
  document.querySelector('#conversation-care-review')?.remove();
  const careReview=document.createElement('div');careReview.id='conversation-care-review';
  document.querySelector('#conversation-notes-editor')?.parentElement.append(careReview);
  window.StellisRoutineReview?.mount(careReview,'conversation',id);
  // Notes is the default view, unless the person already picked a tab while the conversation loaded.
  if (!conversationTabChosen) showConversationTab('summary');
  showNotesChoice(notes.pendingMarkdown.trim() || suggestionsUi.conversation.count ? 'my' : 'enhanced');
  await loadAIRuns(id);
  await loadSavedRecordingChat(id);
  await loadCleanRuns(id);
  const targetSegmentId = query.get('at');
  if (targetSegmentId) {
    showConversationTab('transcript');
    const target = transcriptSegments.find(segment => segment.id === targetSegmentId);
    if (target) {
      const requestedTime = Number(query.get('t'));
      const seekMs = query.has('t') && Number.isFinite(requestedTime) &&
        requestedTime >= target.start_ms && requestedTime <= target.end_ms ? requestedTime : target.start_ms;
      const seek = () => { player.pause(); player.currentTime = seekMs / 1000; };
      if (player.readyState >= 1) seek(); else player.addEventListener('loadedmetadata', seek, {once: true});
      transcriptView.querySelector(`[data-segment-id="${CSS.escape(target.id)}"]`)?.scrollIntoView({block: 'center'});
    }
  }
  if ((!targetSegmentId || !transcriptSegments.some(segment => segment.id === targetSegmentId)) && query.has('t')) {
    const timestamp = Number(query.get('t'));
    if (Number.isFinite(timestamp) && timestamp >= 0 && timestamp <= item.duration_ms) {
      showConversationTab('transcript');
      const seek = () => { player.pause(); player.currentTime = timestamp / 1000; };
      if (player.readyState >= 1) seek(); else player.addEventListener('loadedmetadata', seek, {once: true});
    }
  }
  await loadEnhancedRuns(id, !sourceNavigation);
}
/** True once the person picks a conversation tab, so loading never switches it back. */
let conversationTabChosen = false;
function showConversationTab(tab) {
  const summary = tab === 'summary';
  const edited = tab === 'edited';
  document.querySelector('#ai-card').hidden = !summary;
  document.querySelector('#transcript-card').hidden = summary || edited;
  document.querySelector('#edited-card').hidden = !edited;
  document.querySelector('#summary-tab').setAttribute('aria-selected', String(summary));
  document.querySelector('#transcript-tab').setAttribute('aria-selected', String(!summary && !edited));
  document.querySelector('#edited-tab').setAttribute('aria-selected', String(edited));
}
function sourceButtons(ids, sourceStarts = {}) {
  const holder = document.createElement('span'); holder.className = 'ai-sources';
  for (const id of ids) {
    const segment = transcriptSegments.find(item => item.id === id);
    const startMs = segment?.start_ms ?? sourceStarts[id];
    const button = document.createElement('button'); button.type = 'button';
    button.textContent = Number.isFinite(startMs) ? `${time(startMs / 1000)}, ${segment?.speaker_display_name || 'Earlier source'}` : 'Source unavailable';
    button.disabled = !Number.isFinite(startMs);
    button.addEventListener('click', async () => {
      if (!Number.isFinite(startMs)) return;
      if (segment) {
        highlightsOnly = false; showConversationTab('transcript');
        await showTranscript(activeConversationId);
        transcriptView.querySelector(`[data-segment-id="${id}"]`)?.scrollIntoView({block:'center'});
      }
      player.currentTime = startMs / 1000;
      player.play().catch(() => {});
    });
    holder.append(button);
  }
  return holder;
}
function renderAIItems(title, items, container, sourceStarts) {
  if (!items?.length) return;
  const section = document.createElement('section'); section.className = 'ai-result-section';
  const heading = document.createElement('h3'); heading.textContent = title; section.append(heading);
  const list = document.createElement('ul');
  for (const item of items) {
    const row = document.createElement('li');
    const text = document.createElement('p'); text.textContent = item.text;
    row.append(text, sourceButtons(item.source_segment_ids, sourceStarts)); list.append(row);
  }
  section.append(list); container.append(section);
}
function renderAIRuns(runs) {
  const answers = runs.filter(run => run.kind === 'answer');
  const status = document.querySelector('#ai-status');
  status.textContent = answers.some(run => run.state === 'queued' || run.state === 'running') ? 'Answering…' : '';
  const container = document.querySelector('#ai-answers'); container.replaceChildren();
  for (const run of answers) {
    const card = document.createElement('article'); card.className = 'ai-answer';
    const q = document.createElement('h4'); q.textContent = run.question; card.append(q);
    const body = document.createElement('p');
    body.textContent = run.state === 'succeeded' ? run.result.answer :
      run.state === 'failed' ? `Could not answer: ${run.error_message}` : `Answering: ${run.stage}`;
    card.append(body);
    if (run.stale) {const badge = document.createElement('small'); badge.textContent = 'Source changed since this answer'; card.append(badge);}
    if (run.state === 'succeeded') card.append(sourceButtons(run.result.source_segment_ids, run.source_starts));
    if (run.state === 'failed') {
      const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Retry answer';
      retry.addEventListener('click', () => startAIRun('answer', run.question)); card.append(retry);
    }
    container.append(card);
  }
}
async function loadAIRuns(id) {
  clearTimeout(aiRunTimer);
  const runs = await json(await fetch(`${API}/conversations/${encodeURIComponent(id)}/ai-runs`));
  if (id !== activeConversationId) return;
  renderAIRuns(runs);
  if (runs.some(run => run.kind === 'answer' && (run.state === 'queued' || run.state === 'running')))
    aiRunTimer = setTimeout(() => loadAIRuns(id).catch(error => document.querySelector('#ai-status').textContent = error.message), 2000);
}
async function startAIRun(kind, question = null) {
  const id = activeConversationId;
  const status = document.querySelector('#ai-status'); status.textContent = 'Answering…';
  try {
    await json(await fetch(`${API}/conversations/${encodeURIComponent(id)}/ai-runs`, {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({kind,question})}));
    await loadAIRuns(id);
  } catch (error) { status.textContent = error.message; }
}
document.querySelector('#summary-tab').addEventListener('click', () => {
  conversationTabChosen = true;
  showConversationTab('summary');
  if (activeConversationId) loadAIRuns(activeConversationId).catch(error => document.querySelector('#ai-status').textContent = error.message);
  if (activeConversationId) loadEnhancedRuns(activeConversationId, true).catch(error => document.querySelector('#enhanced-status').textContent = error.message);
});
document.querySelector('#transcript-tab').addEventListener('click', () => { conversationTabChosen = true; showConversationTab('transcript'); });
document.querySelector('#edited-tab').addEventListener('click', () => { conversationTabChosen = true; showConversationTab('edited'); });
document.querySelector('#regenerate-summary').addEventListener('click', () => {
  showConversationTab('summary'); showNotesChoice('enhanced'); startEnhanced(true);
});
document.querySelector('#clean-button').addEventListener('click', async () => {
  const id = activeConversationId;
  const status = document.querySelector('#clean-status');
  status.textContent = 'Starting transcript cleanup…';
  awaitingCleanRun = true;
  try {
    await json(await fetch(`${API}/conversations/${encodeURIComponent(id)}/clean-runs`, {method:'POST'}));
    await loadCleanRuns(id);
  } catch (error) { awaitingCleanRun = false; status.textContent = error.message; }
});
document.querySelector('#clean-version').addEventListener('change', event => {
  if (pendingCleanEdits.size && !window.confirm('Discard unsaved edited transcript changes and switch versions?')) {
    event.target.value = currentCleanRun.id; return;
  }
  pendingCleanEdits.clear(); cleanEditMode = false; cleanEditError = null;
  currentCleanRun = cleanRuns.find(run => run.id === event.target.value) || null;
  renderCleanRun();
});
async function loadCleanRuns(id) {
  clearTimeout(cleanRunTimer);
  const runs = await json(await fetch(`${API}/conversations/${encodeURIComponent(id)}/clean-runs`));
  if (id !== activeConversationId) return;
  cleanRuns = runs;
  if (cleanEditMode && pendingCleanEdits.size) return;
  const previous = currentCleanRun?.id;
  currentCleanRun = runs.find(run => run.id === previous) || runs.find(run => run.state === 'succeeded') || runs[0] || null;
  const button = document.querySelector('#clean-button');
  const latest = runs[0];
  button.disabled = latest?.state === 'queued' || latest?.state === 'running' || !currentTranscript;
  button.textContent = latest?.state === 'failed' ? 'Retry cleanup' : latest?.state === 'succeeded' && !latest.stale ? 'Transcript cleaned' : 'Clean transcript';
  document.querySelector('#edited-tab').hidden = !runs.some(run => run.state === 'succeeded');
  const version = document.querySelector('#clean-version');
  version.replaceChildren();
  for (const run of runs.filter(item => item.state === 'succeeded')) {
    const option = document.createElement('option');
    option.value = run.id;
    option.textContent = `${new Date(run.created_at).toLocaleString()}${run.stale ? ' (source changed)' : ''}`;
    version.append(option);
  }
  if (currentCleanRun?.state === 'succeeded') version.value = currentCleanRun.id;
  renderCleanRun();
  if (awaitingCleanRun && latest?.state === 'succeeded') {
    awaitingCleanRun = false; currentCleanRun = latest; version.value = latest.id; renderCleanRun(); showConversationTab('edited');
  }
  if (awaitingCleanRun && latest?.state === 'failed') awaitingCleanRun = false;
  if ((latest?.state === 'running' || latest?.state === 'queued') && currentCleanRun?.id !== latest.id)
    document.querySelector('#clean-status').textContent = `Cleaning new version: ${latest.stage}. An earlier version remains available.`;
  if (runs.some(run => run.state === 'queued' || run.state === 'running'))
    cleanRunTimer = setTimeout(() => loadCleanRuns(id).catch(error => document.querySelector('#clean-status').textContent = error.message), 2000);
}
function syncCleanEditControls() {
  document.querySelector('#clean-edit-button').textContent = cleanEditMode ? 'Done' : 'Edit transcript';
  document.querySelector('#clean-discard-button').hidden = !cleanEditMode;
  if (cleanEditMode) document.querySelector('#clean-status').textContent = cleanEditError ||
    (pendingCleanEdits.size ? `${pendingCleanEdits.size} passage${pendingCleanEdits.size === 1 ? '' : 's'} pending. Press Done to save.` : 'Edit wording in place. Press Done to finish.');
}
function setCleanEditMode(enabled) {
  cleanEditMode = enabled; cleanEditError = null;
  const segments = new Map((currentCleanRun?.result?.segments || []).map(item => [item.id, item]));
  for (const turn of document.querySelectorAll('#edited-transcript .clean-turn')) turn.classList.toggle('editing', enabled);
  for (const row of document.querySelectorAll('#edited-transcript .clean-segment')) {
    const words = row.querySelector('.segment-text');
    if (enabled) {
      words.contentEditable = 'true'; words.spellcheck = true;
      words.setAttribute('role', 'textbox'); words.setAttribute('aria-label', `Edit cleaned passage at ${time(segments.get(row.dataset.segmentId).start_ms / 1000)}`);
    } else { words.contentEditable = 'false'; words.removeAttribute('role'); }
  }
  document.querySelector('#clean-compare').hidden = true;
  syncCleanEditControls();
}
async function finishCleanEdit() {
  if (!pendingCleanEdits.size) { setCleanEditMode(false); renderCleanRun(); return; }
  const edits = [...pendingCleanEdits].map(([segment_id,text]) => ({segment_id,text:text.trim()}));
  if (edits.some(item => !item.text)) { cleanEditError = 'A passage is empty. Restore or enter words before saving.'; syncCleanEditControls(); return; }
  const button = document.querySelector('#clean-edit-button'); button.disabled = true;
  document.querySelector('#clean-status').textContent = `Saving ${edits.length} passages…`;
  try {
    await json(await fetch(`${API}/clean-runs/${encodeURIComponent(currentCleanRun.id)}/segments`, {
      method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({edits,expected_revision:currentCleanRun.edit_revision}),
    }));
    pendingCleanEdits.clear(); cleanEditMode = false;
    await loadCleanRuns(activeConversationId);
    document.querySelector('#clean-status').textContent = `${edits.length} edited passage${edits.length === 1 ? '' : 's'} saved.`;
  } catch (error) { cleanEditError = `${error.message}. Your unsaved wording remains here.`; syncCleanEditControls(); }
  finally { button.disabled = false; }
}
document.querySelector('#clean-edit-button').addEventListener('click', () => {
  if (cleanEditMode) finishCleanEdit(); else setCleanEditMode(true);
});
document.querySelector('#clean-discard-button').addEventListener('click', async () => {
  if (pendingCleanEdits.size && !window.confirm('Discard unsaved edited transcript changes?')) return;
  pendingCleanEdits.clear(); cleanEditMode = false; cleanEditError = null; renderCleanRun();
});
function renderCleanRun() {
  const run = currentCleanRun;
  const status = document.querySelector('#clean-status');
  const view = document.querySelector('#edited-transcript');
  view.replaceChildren();
  if (!run) { status.textContent = 'Use Clean transcript in Summary & AI to create an edited version.'; return; }
  if (run.state !== 'succeeded') { status.textContent = run.state === 'failed' ? `Cleanup stopped: ${run.error_message}` : `Cleaning transcript: ${run.stage}`; return; }
  const segments = run.result.segments;
  const review = segments.filter(segment => segment.needs_review && !segment.reviewed).length;
  status.textContent = `${segments.length} passages, ${review} awaiting audio review${run.stale ? '. Source transcript has changed.' : ''}`;
  for (const group of groupTranscriptSegments(segments)) {
    const turn = document.createElement('article'); turn.className = 'speaker-turn clean-turn';
    const heading = document.createElement('div'); heading.className = 'turn-heading';
    const speaker = document.createElement('strong'); speaker.textContent = group[0].speaker_display_name;
    const range = document.createElement('span');
    range.textContent = `${time(group[0].start_ms / 1000)}${group.length > 1 ? ` to ${time(group.at(-1).end_ms / 1000)}` : ''}`;
    heading.append(speaker, range); turn.append(heading);
    const body = document.createElement('div'); body.className = 'turn-body';
    for (const segment of group) {
      const row = document.createElement('span'); row.className = 'segment clean-segment'; row.dataset.segmentId = segment.id;
      row.title = `Play from ${time(segment.start_ms / 1000)}${segment.needs_review ? '. Check wording against audio.' : ''}`;
      const words = document.createElement('span'); words.className = 'segment-text'; words.textContent = segment.text;
      row.append(words);
      if (segment.needs_review) { const reviewBadge = document.createElement('span'); reviewBadge.className = 'clean-review'; reviewBadge.textContent = segment.reviewed ? 'Reviewed' : 'Review'; row.append(reviewBadge); }
      const compare = document.createElement('button'); compare.type = 'button'; compare.className = 'clean-compare-button'; compare.textContent = 'Compare';
      compare.setAttribute('aria-label', `Compare edited and original passage at ${time(segment.start_ms / 1000)}`);
      compare.addEventListener('click', () => showCleanCompare(segment)); row.append(compare);
      words.addEventListener('input', () => {
        if (!cleanEditMode) return;
        const value = words.innerText.replace(/\u00a0/g, ' ');
        if (value === segment.text) pendingCleanEdits.delete(segment.id);
        else pendingCleanEdits.set(segment.id, value);
        cleanEditError = null; syncCleanEditControls();
      });
      row.addEventListener('click', event => {
        if (cleanEditMode || event.target.closest('button,textarea') || window.getSelection()?.toString().trim()) return;
        player.currentTime = segment.start_ms / 1000;
        player.play().catch(() => {});
      });
      body.append(row);
    }
    turn.append(body); view.append(turn);
  }
  if (cleanEditMode) setCleanEditMode(true);
  else syncCleanEditControls();
}
function showCleanCompare(segment) {
  const panel = document.querySelector('#clean-compare'); panel.replaceChildren(); panel.hidden = false;
  const title = document.createElement('h3'); title.textContent = `Passage at ${time(segment.start_ms / 1000)}, ${segment.speaker_display_name}`;
  const source = document.createElement('p'); source.textContent = `Original: ${segment.source_text}`;
  const edited = document.createElement('p'); edited.textContent = `Edited: ${segment.text}`;
  const actions = document.createElement('div'); actions.className = 'editor-actions';
  const play = document.createElement('button'); play.type = 'button'; play.textContent = 'Play source';
  play.addEventListener('click', () => { player.currentTime = segment.start_ms / 1000; player.play().catch(() => {}); });
  const copy = document.createElement('button'); copy.type = 'button'; copy.textContent = 'Copy edited';
  copy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(segment.text); document.querySelector('#clean-status').textContent = `Copied passage at ${time(segment.start_ms / 1000)}. Check it against audio before quoting.`; }
    catch { document.querySelector('#clean-status').textContent = 'Clipboard is unavailable in this browser.'; }
  });
  const restore = document.createElement('button'); restore.type = 'button'; restore.textContent = 'Use exact source';
  restore.addEventListener('click', () => {
    setCleanEditMode(true);
    const words = document.querySelector(`#edited-transcript [data-segment-id="${segment.id}"] .segment-text`);
    words.textContent = segment.source_text; words.dispatchEvent(new Event('input', {bubbles:true}));
    words.scrollIntoView({block:'center'}); words.focus();
  });
  actions.append(play, copy, restore);
  if (segment.user_edited) {
    const generated = document.createElement('button'); generated.type = 'button'; generated.textContent = 'Use generated wording';
    generated.addEventListener('click', () => {
      setCleanEditMode(true);
      const words = document.querySelector(`#edited-transcript [data-segment-id="${segment.id}"] .segment-text`);
      words.textContent = segment.model_text; words.dispatchEvent(new Event('input', {bubbles:true}));
      words.scrollIntoView({block:'center'}); words.focus();
    }); actions.append(generated);
  }
  if (segment.needs_review && !segment.reviewed) {
    const checked = document.createElement('button'); checked.type = 'button'; checked.textContent = 'Mark checked';
    checked.addEventListener('click', async () => {
      checked.disabled = true;
      try {
        await json(await fetch(`${API}/clean-runs/${encodeURIComponent(currentCleanRun.id)}/segments/${encodeURIComponent(segment.id)}/review`, {
          method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:currentCleanRun.edit_revision})}));
        panel.hidden = true; await loadCleanRuns(activeConversationId);
      } catch (error) { checked.disabled = false; document.querySelector('#clean-status').textContent = error.message; }
    }); actions.append(checked);
  }
  const close = document.createElement('button'); close.type = 'button'; close.textContent = 'Close'; close.addEventListener('click', () => { panel.hidden = true; }); actions.append(close);
  panel.append(title, source, edited, actions); panel.scrollIntoView({block:'start'});
}
document.querySelector('#ai-question-form').addEventListener('submit', event => {
  event.preventDefault(); const input = document.querySelector('#ai-question-input');
  const question = input.value.trim(); if (!question) return;
  input.value = ''; startAIRun('answer', question);
});
async function loadSpeech(id) {
  const [capabilities, existing] = await Promise.all([
    json(await fetch(`${API}/capabilities`)),
    json(await fetch(`${API}/conversations/${encodeURIComponent(id)}/processing-job`)),
  ]);
  if (id !== activeConversationId) return;
  if (existing?.state === 'succeeded') { await showTranscript(id);
    const activeVersion = currentTranscript?.generation_version || existing.pipeline_version;
    processButton.hidden = activeVersion === capabilities.pipeline_version ||
      activeVersion.startsWith(`${capabilities.pipeline_version}-`);
    if (!processButton.hidden) processButton.textContent = 'Reprocess transcript';
    return; }
  if (existing?.state === 'queued' || existing?.state === 'running') {
    if (existing.kind === 'word_alignment' || existing.kind === 'voice_index') await showTranscript(id);
    watchJob(existing); return;
  }
  if (existing?.state === 'failed') {
    setTranscriptLoading(false);
    if (existing.kind === 'rematch') { await showTranscript(id); rematchButton.hidden = false; rematchButton.textContent = 'Retry speaker rematch'; }
    else if (existing.kind === 'word_alignment') { await showTranscript(id); alignWordsButton.hidden = false; alignWordsButton.textContent = 'Retry word timing'; }
    else if (existing.kind === 'voice_index') await showTranscript(id);
    else { processButton.hidden = false; processButton.textContent = 'Retry processing'; }
    processingStatus.textContent = `${existing.kind === 'rematch' ? 'Speaker rematch' : existing.kind === 'word_alignment' ? 'Word timing' : existing.kind === 'voice_index' ? 'Voice matching' : 'Processing'} stopped: ${existing.error || 'Unknown error'}`;
    return;
  }
  const savedTranscript = await fetch(`${API}/conversations/${encodeURIComponent(id)}/transcript`);
  if (savedTranscript.ok) {
    await showTranscript(id);
    processButton.hidden = !capabilities.features.transcribe || !capabilities.features.diarize;
    if (!processButton.hidden) processButton.textContent = 'Reprocess transcript';
    return;
  }
  if (savedTranscript.status !== 404) await json(savedTranscript);
  if (!capabilities.features.transcribe || !capabilities.features.diarize) {
    setTranscriptLoading(false);
    processingStatus.textContent = 'The speech models are still downloading. This recording transcribes as soon as they are ready.';
    return;
  }
  await beginProcessing(id);
}
async function beginProcessing(id) {
  processButton.hidden = true;
  setTranscriptLoading(true);
  processingStatus.textContent = 'Transcribing…';
  try {
    const job = await json(await fetch(`${API}/conversations/${encodeURIComponent(id)}/processing-jobs`, {method:'POST'}));
    watchJob(job);
  } catch (error) {
    setTranscriptLoading(false);
    processingStatus.textContent = error.message;
    processButton.hidden = false;
    processButton.textContent = 'Retry processing';
  }
}
function watchJob(job) {
  if (job.conversation_id !== activeConversationId) return;
  clearTimeout(jobTimer);
  if (job.kind === 'speech') transcriptWait = job.state === 'queued' || job.state === 'running' ? jobProgressText(job) : '';
  if (job.state === 'succeeded') { showTranscript(job.conversation_id).then(() =>
    loadEnhancedRuns(job.conversation_id, true)).catch(error => {
    setTranscriptLoading(false); processingStatus.textContent = error.message;
  }); return; }
  if (job.state === 'failed') {
    setTranscriptLoading(false);
    processingStatus.textContent = `${job.kind === 'rematch' ? 'Speaker rematch' : job.kind === 'word_alignment' ? 'Word timing' : 'Processing'} stopped: ${job.error || 'Unknown error'}`;
    if (job.kind === 'rematch') { rematchButton.hidden = false; rematchButton.disabled = false; rematchButton.textContent = 'Retry speaker rematch'; }
    else if (job.kind === 'word_alignment') { alignWordsButton.hidden = false; alignWordsButton.disabled = false; alignWordsButton.textContent = 'Retry word timing'; }
    else { processButton.hidden = false; processButton.textContent = 'Retry processing'; }
    return;
  }
  setTranscriptLoading(true);
  processingStatus.textContent = jobProgressText(job);
  if (job.kind === 'speech' && !currentTranscript?.segments?.length) setSummaryStatus(`Waiting for the transcript (${transcriptWait}). The summary follows in about 20 s.`, true);
  jobTimer = setTimeout(async () => {
    try { watchJob(await json(await fetch(`${API}/jobs/${encodeURIComponent(job.id)}`))); }
    catch (error) { setTranscriptLoading(false); processingStatus.textContent = error.message; }
  }, 1500);
}
const PROVISIONAL_SPEAKER_SOURCES = new Set(['embedding_fallback', 'nearest_turn', 'nearest_passage', 'single_speaker']);
async function confirmSpeakerSuggestion(button, segment, suggestion) {
  button.disabled = true;
  try {
    await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/person-links`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({person_id: suggestion.person_id, segment_id: segment.id, scope: 'cluster',
        expected_revision: currentTranscript.revision}),
    }));
    await showTranscript(activeConversationId);
  } catch (error) { button.disabled = false; processingStatus.textContent = error.message; }
}
function groupTranscriptSegments(segments) {
  const turns = [];
  for (const segment of segments) {
    const current = turns.at(-1);
    const previous = current?.at(-1);
    // Back-to-back passages under the same name read as one turn. Whether a name is
    // automatic or the user's shows in its colour (#210), not in extra headings.
    const sameTurn = previous &&
      previous.speaker_display_name === segment.speaker_display_name &&
      (previous.speaker_suggestion?.person_id || null) === (segment.speaker_suggestion?.person_id || null) &&
      (previous.person_id || previous.speaker_group_person_id || null) === (segment.person_id || segment.speaker_group_person_id || null) &&
      segment.start_ms - previous.end_ms <= 1500;
    if (sameTurn) current.push(segment);
    else turns.push([segment]);
  }
  return turns;
}
function syncTranscriptEditControls() {
  editTranscriptButton.hidden = !currentTranscript;
  editTranscriptButton.disabled = savingTranscriptEdits;
  editTranscriptButton.textContent = transcriptEditMode ? 'Done' : 'Edit transcript';
  discardEditsButton.hidden = !transcriptEditMode;
  discardEditsButton.disabled = savingTranscriptEdits;
  restorePassageButton.hidden = !transcriptEditMode || !focusedEditSegmentId;
  editStatus.hidden = !transcriptEditMode;
  if (transcriptEditMode && !savingTranscriptEdits)
    editStatus.textContent = transcriptEditError || (pendingTranscriptEdits.size ? `${pendingTranscriptEdits.size} passage${pendingTranscriptEdits.size === 1 ? '' : 's'} pending. Press Done to save.` : 'Edit words in place. Press Done to finish.');
}
function setTranscriptEditMode(enabled) {
  document.querySelector('#comment-composer')?.remove();
  if (enabled) { hideSelectionToolbar(); window.getSelection()?.removeAllRanges(); }
  transcriptEditMode = enabled;
  transcriptEditError = null;
  focusedEditSegmentId = null;
  for (const turn of transcriptView.querySelectorAll('.speaker-turn')) turn.classList.toggle('editing', enabled);
  for (const row of transcriptView.querySelectorAll('.segment')) {
    const words = row.querySelector('.segment-text');
    const segment = transcriptSegments.find(item => item.id === row.dataset.segmentId);
    if (!words || !segment) continue;
    if (enabled) {
      words.replaceChildren(document.createTextNode(segment.text));
      words.contentEditable = 'true'; words.spellcheck = true;
      words.setAttribute('role', 'textbox'); words.setAttribute('aria-multiline', 'true');
      words.setAttribute('aria-label', `Edit transcript at ${time(segment.start_ms / 1000)}`);
    } else {
      words.contentEditable = 'false'; words.removeAttribute('role'); words.removeAttribute('aria-multiline');
    }
  }
  syncTranscriptEditControls();
}
async function finishTranscriptEdit() {
  if (savingTranscriptEdits) return;
  if (!pendingTranscriptEdits.size) { setTranscriptEditMode(false); await showTranscript(activeConversationId); return; }
  const edits = [...pendingTranscriptEdits].map(([segment_id,text]) => ({segment_id,text:text.trim()}));
  if (edits.some(item => !item.text)) { editStatus.textContent = 'A passage is empty. Restore or enter words before saving.'; return; }
  savingTranscriptEdits = true; syncTranscriptEditControls(); editStatus.textContent = `Saving ${edits.length} passages…`;
  try {
    await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/transcript/segments`, {
      method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({edits,expected_revision:currentTranscript.revision}),
    }));
    pendingTranscriptEdits.clear();
    transcriptEditMode = false;
    await showTranscript(activeConversationId);
    processingStatus.textContent = `${edits.length} transcript passage${edits.length === 1 ? '' : 's'} saved.`;
  } catch (error) { transcriptEditError = `${error.message} Your unsaved wording remains here.`; }
  finally { savingTranscriptEdits = false; syncTranscriptEditControls(); }
}
editTranscriptButton.addEventListener('click', () => {
  if (transcriptEditMode) finishTranscriptEdit();
  else setTranscriptEditMode(true);
});
discardEditsButton.addEventListener('click', async () => {
  if (pendingTranscriptEdits.size && !window.confirm('Discard unsaved transcript changes?')) return;
  pendingTranscriptEdits.clear(); setTranscriptEditMode(false); await showTranscript(activeConversationId);
});
restorePassageButton.addEventListener('click', () => {
  const segment = transcriptSegments.find(item => item.id === focusedEditSegmentId);
  const words = transcriptView.querySelector(`[data-segment-id="${focusedEditSegmentId}"] .segment-text`);
  if (!segment || !words) return;
  words.textContent = segment.original_text || segment.text;
  words.dispatchEvent(new Event('input', {bubbles:true})); words.focus();
});
window.addEventListener('beforeunload', event => {
  if (!pendingTranscriptEdits.size && !pendingCleanEdits.size) return;
  event.preventDefault(); event.returnValue = '';
});
async function showTranscript(id) {
  if (transcriptEditMode && pendingTranscriptEdits.size) {
    editStatus.textContent = 'Unsaved wording remains here. Press Done before refreshing the transcript.';
    return;
  }
  const data = await json(await fetch(`${API}/conversations/${encodeURIComponent(id)}/transcript`));
  if (id !== activeConversationId) return;
  const previousScroll = window.scrollY;
  currentTranscript = data;
  renderSeekSegments(data.segments);
  document.querySelector('#copy-transcript').hidden = !data.segments.length;
  document.querySelector('#regenerate-summary').hidden = !data.segments.length;
  syncTranscriptEditControls();
  document.querySelector('#clean-button').disabled = cleanRuns[0]?.state === 'queued' || cleanRuns[0]?.state === 'running';
  rematchButton.hidden = false;
  alignWordsButton.hidden = data.segments.some(segment => segment.words?.length);
  alignWordsButton.disabled = false;
  rematchButton.disabled = data.generation_version.includes('-speaker-embedding-v1');
  rematchButton.textContent = rematchButton.disabled ? 'Speakers rematched' : 'Rematch speakers';
  transcriptSegments = data.segments;
  transcriptView.replaceChildren();
  hideSelectionToolbar();
  document.querySelector('#highlight-count').textContent = `${data.highlights.length} highlights`;
  document.querySelector('#show-highlights').setAttribute('aria-pressed', String(highlightsOnly));
  renderTranscriptComments(data);
  const changed = data.segments.filter(segment => segment.raw_speaker_cluster_id !== undefined && segment.raw_speaker_cluster_id !== segment.speaker_cluster_id).length;
  const changesButton = document.querySelector('#show-rematch-changes');
  changesButton.hidden = !data.generation_version.includes('-speaker-embedding-v1');
  if (changesButton.hidden) rematchChangesOnly = false;
  changesButton.setAttribute('aria-pressed', String(rematchChangesOnly));
  document.querySelector('#rematch-change-count').textContent = changesButton.hidden ? '' : `${changed} changed`;
  // Use the same persisted effective labels as the turns. Raw acoustic clusters
  // omit passage and confirmed-Person labels, so they cannot drive this index.
  const speakerDurations = new Map();
  for (const segment of data.segments) {
    const name = segment.speaker_display_name;
    if (!name) continue;
    const duration = Math.max(0, segment.end_ms - segment.start_ms);
    speakerDurations.set(name, (speakerDurations.get(name) || 0) + duration);
  }
  const totalSpeakerDuration = [...speakerDurations.values()].reduce((sum, duration) => sum + duration, 0);
  const summary = document.createElement('div');
  summary.className = 'speaker-summary';
  summary.setAttribute('role', 'list');
  summary.setAttribute('aria-label', 'Speakers');
  for (const [name, duration] of [...speakerDurations].sort((a, b) => b[1] - a[1])) {
    const pill = document.createElement('span');
    pill.className = 'speaker-pill';
    pill.setAttribute('role', 'listitem');
    const share = totalSpeakerDuration ? duration * 100 / totalSpeakerDuration : 0;
    pill.textContent = `${name} (${share > 0 && share < 0.5 ? '<1' : Math.round(share)}%)`;
    summary.append(pill);
  }
  transcriptView.append(summary);
  const turns = groupTranscriptSegments(data.segments);
  for (const group of turns) {
    const visible = group.filter(segment =>
      (!highlightsOnly || data.highlights.some(item => item.segment_id === segment.id)) &&
      (!rematchChangesOnly || segment.raw_speaker_cluster_id !== segment.speaker_cluster_id));
    if (!visible.length) continue;
    const turn = document.createElement('article');
    turn.className = 'speaker-turn';
    turn.classList.toggle('editing', transcriptEditMode);
    const heading = document.createElement('div'); heading.className = 'turn-heading';
    const firstSegment = visible[0];
    const turnSpeaker = document.createElement('button');
    turnSpeaker.type = 'button';
    const suggestion = firstSegment.speaker_suggestion;
    if (suggestion) {
      // Grey: a voice match the user has not confirmed. One click confirms it for this speaker.
      turnSpeaker.textContent = suggestion.name;
      turnSpeaker.className = 'speaker-name speaker-auto';
      turnSpeaker.title = `Confirm ${suggestion.name} for this speaker`;
      turnSpeaker.setAttribute('aria-label', `Confirm ${suggestion.name} for this speaker`);
      turnSpeaker.addEventListener('click', () => confirmSpeakerSuggestion(turnSpeaker, firstSegment, suggestion));
    } else {
      turnSpeaker.textContent = firstSegment.speaker_display_name;
      const manual = visible.some(segment => segment.speaker_label_origin === 'manual');
      turnSpeaker.className = `speaker-name ${firstSegment.speaker_label_origin === 'automatic' ? 'speaker-auto' : 'speaker-set'}`;
      if (manual) {
        const check = document.createElement('span'); check.className = 'speaker-check'; check.setAttribute('aria-hidden', 'true');
        check.innerHTML = '<svg viewBox="0 0 12 12" width="12" height="12"><path d="M3 6.2 5.1 8.3 9 4.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        turnSpeaker.append(check);
      }
      turnSpeaker.setAttribute('aria-label', `Tag speaker for turn at ${time(visible[0].start_ms / 1000)}${manual ? ', labelled by you' : ''}`);
      turnSpeaker.addEventListener('click', () => showSpeakerPicker(turnSpeaker, turn.querySelector('.segment'), firstSegment));
    }
    const range = document.createElement('span'); range.textContent = `${time(visible[0].start_ms / 1000)}${visible.length > 1 ? ` to ${time(visible.at(-1).end_ms / 1000)}` : ''}`;
    heading.append(turnSpeaker, range);
    turn.append(heading);
    const body = document.createElement('div'); body.className = 'turn-body';
    for (const segment of visible) {
    const highlights = data.highlights.filter(item => item.segment_id === segment.id);
    const comments = data.comments.filter(item => item.segment_id === segment.id);
    const row = document.createElement('div');
    row.className = 'segment';
    row.dataset.segmentId = segment.id;
    const stamp = document.createElement('button');
    stamp.type = 'button';
    stamp.className = 'segment-time';
    stamp.textContent = time(segment.start_ms / 1000);
    stamp.setAttribute('aria-label', `Play from ${time(segment.start_ms / 1000)}`);
    const seek = () => { player.currentTime = segment.start_ms / 1000; player.play(); };
    stamp.addEventListener('click', seek);
    const speaker = document.createElement('button');
    speaker.type = 'button';
    speaker.className = 'segment-speaker';
    speaker.append(icon('dots'));
    speaker.setAttribute('aria-label', `Edit speaker label for ${segment.speaker_display_name} at ${time(segment.start_ms / 1000)}`);
    if (segment.speaker_match_source === 'embedding_suggestion') speaker.title = 'Suggested from this passage’s voice; click to confirm or edit';
    if (segment.speaker_match_source === 'unresolved') speaker.title = 'Speaker unresolved; click to assign';
    if (PROVISIONAL_SPEAKER_SOURCES.has(segment.speaker_match_source) && !segment.person_id &&
      segment.speaker_label_source !== 'manual_segment') speaker.title = 'Suggested speaker; click to confirm or edit';
    speaker.addEventListener('click', () => showSpeakerPicker(speaker, row, segment));
    const words = document.createElement('div');
    words.className = 'segment-text';
    words.setAttribute('aria-label', `Transcript at ${time(segment.start_ms / 1000)}: ${segment.text}. Select text to highlight, comment, or click to play.`);
    renderHighlightedText(words, segment.text, highlights, segment.words || [], comments);
    words.addEventListener('input', () => {
      if (!transcriptEditMode) return;
      const value = words.innerText.replace(/\u00a0/g, ' ');
      if (value === segment.text) pendingTranscriptEdits.delete(segment.id);
      else pendingTranscriptEdits.set(segment.id, value);
      transcriptEditError = null;
      syncTranscriptEditControls();
    });
    words.addEventListener('focus', () => { focusedEditSegmentId = segment.id; syncTranscriptEditControls(); });
    row.addEventListener('click', event => {
      if (transcriptEditMode) return;
      if (event.target.closest('button,input,textarea,select') || window.getSelection()?.toString().trim()) return;
      const timed = event.target.closest('.timed-word');
      if (timed && row.contains(timed)) { player.currentTime = Number(timed.dataset.startMs) / 1000; player.play().catch(() => {}); }
      else seek();
    });
    // A real space after each passage, so copied text and screen readers keep sentences apart
    // ("day. We", not "day.We"); a grid row ignores a whitespace-only text node (#258).
    row.append(stamp, speaker, words, document.createTextNode(' '));
    body.append(row);
    }
    turn.append(body);
    transcriptView.append(turn);
  }
  const unresolved = data.segments.filter(segment => !segment.speaker_cluster_id).length;
  setTranscriptLoading(false);
  processingStatus.textContent = !data.segments.length ? 'No speech detected' : `${data.segments.length} passages, ${turns.length} turns, ${speakerDurations.size} speakers${unresolved ? `, ${unresolved} unresolved` : ''}`;
  if (transcriptEditMode) setTranscriptEditMode(true);
  if (cleanRuns.length) loadCleanRuns(id).catch(error => document.querySelector('#clean-status').textContent = error.message);
  refreshTranscriptSearch();
  renderConversationImages();
  window.scrollTo(0, previousScroll);
}

function renderHighlightedText(container, value, highlights, timedWords = [], comments = []) {
  const valid = highlights.filter(item => value.slice(item.start_offset, item.end_offset) === item.quote);
  const anchoredComments = comments.filter(item => value.slice(item.start_offset, item.end_offset) === item.quote);
  const cleanup = window.StellisTranscriptFillers.fillerCleanup(value);
  const measured = timedWords.map((word, index) => ({...word, index})).filter(word => value.slice(word.start_offset, word.end_offset) === word.text && word.end_ms > word.start_ms);
  const edges = [...new Set([0, value.length, ...valid.flatMap(item => [item.start_offset, item.end_offset]),
    ...anchoredComments.flatMap(item => [item.start_offset, item.end_offset]),
    ...measured.flatMap(word => [word.start_offset, word.end_offset]),
    ...cleanup.hidden.flat(), ...cleanup.upper.flatMap(index => [index, index + 1])])].sort((a, b) => a - b);
  let currentWord = null;
  let currentWordIndex = -1;
  for (let i = 0; i < edges.length - 1; i++) {
    const start = edges[i], end = edges[i + 1];
    const content = value.slice(start, end);
    const marked = valid.some(item => item.start_offset <= start && item.end_offset >= end);
    const commented = anchoredComments.some(item => item.start_offset <= start && item.end_offset >= end);
    const wordIndex = measured.findIndex(word => word.start_offset <= start && word.end_offset >= end);
    if (wordIndex !== currentWordIndex) {
      currentWordIndex = wordIndex; currentWord = null;
      if (wordIndex >= 0) {
        const word = measured[wordIndex]; currentWord = document.createElement('span');
        currentWord.className = 'timed-word'; currentWord.dataset.wordIndex = String(word.index);
        currentWord.dataset.startMs = String(word.start_ms); currentWord.dataset.endMs = String(word.end_ms);
        currentWord.title = `Play from ${time(word.start_ms / 1000)}, measured on this device`;
        container.append(currentWord);
      }
    }
    let node = marked ? document.createElement('mark') : document.createTextNode(content);
    if (marked) { node.textContent = content; node.title = 'Highlighted excerpt'; }
    if (commented) {
      const anchor = document.createElement('span'); anchor.className = 'comment-range'; anchor.title = 'Open comments';
      anchor.addEventListener('click', event => {
        if (window.getSelection()?.toString().trim()) return;
        event.stopPropagation(); commentsOpen = true; renderTranscriptComments(currentTranscript);
        document.querySelector('#transcript-comments').scrollIntoView({block:'nearest'});
      });
      anchor.append(node); node = anchor;
    }
    // hidden, not removed: selection offsets
    const cleanupClass = cleanup.hidden.some(([from, to]) => from <= start && to >= end) ? 'transcript-filler'
      : cleanup.upper.includes(start) ? 'transcript-cap' : null;
    if (cleanupClass) { const wrap = document.createElement('span'); wrap.className = cleanupClass; wrap.append(node); node = wrap; }
    (currentWord || container).append(node);
  }
  if (highlights.length) {
    const list = document.createElement('div'); list.className = 'segment-highlights';
    for (const item of highlights) {
      const chip = document.createElement('button'); chip.type = 'button';
      chip.textContent = `${value.slice(item.start_offset, item.end_offset) === item.quote ? 'Remove highlight' : 'Remove outdated highlight'}: “${item.quote.slice(0, 45)}”`;
      chip.addEventListener('click', event => { event.stopPropagation(); removeHighlight(item.id); });
      list.append(chip);
    }
    container.append(list);
  }
}
document.querySelector('#comments-toggle').addEventListener('click', () => {
  commentsOpen = !commentsOpen;
  renderTranscriptComments(currentTranscript);
});
function renderTranscriptComments(data) {
  const list = document.querySelector('#comments-list');
  const comments = data?.comments || [];
  document.querySelector('#comment-count').textContent = String(comments.length);
  document.querySelector('#comments-toggle').setAttribute('aria-expanded', String(commentsOpen));
  list.hidden = !commentsOpen;
  list.replaceChildren();
  if (!commentsOpen) return;
  for (const comment of comments) {
    const segment = data.segments.find(item => item.id === comment.segment_id);
    const exact = segment && segment.text.slice(comment.start_offset, comment.end_offset) === comment.quote;
    const item = document.createElement('article'); item.className = 'transcript-comment'; item.dataset.commentId = comment.id;
    const source = document.createElement('button'); source.type = 'button'; source.textContent = time(comment.start_ms / 1000);
    source.setAttribute('aria-label', `Play comment source at ${time(comment.start_ms / 1000)}`);
    source.addEventListener('click', () => {
      player.currentTime = comment.start_ms / 1000; player.play().catch(() => {});
      if (segment) transcriptView.querySelectorAll('.segment').forEach(row => {
        if (row.dataset.segmentId === comment.segment_id) row.scrollIntoView({block:'center'});
      });
    });
    const quote = document.createElement('blockquote'); quote.textContent = comment.quote;
    const body = document.createElement('p'); body.textContent = comment.body;
    const actions = document.createElement('div'); actions.className = 'comment-actions';
    const edit = document.createElement('button'); edit.type = 'button'; edit.textContent = 'Edit';
    edit.addEventListener('click', () => {
      const form = document.createElement('form'); form.className = 'comment-edit';
      const input = document.createElement('textarea'); input.value = comment.body; input.maxLength = 2000;
      input.setAttribute('aria-label', 'Edit comment');
      const save = document.createElement('button'); save.type = 'submit'; save.textContent = 'Save';
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel';
      cancel.addEventListener('click', () => renderTranscriptComments(currentTranscript));
      const message = document.createElement('p'); message.setAttribute('role', 'status');
      form.append(input, save, cancel, message); body.replaceWith(form); input.focus();
      form.addEventListener('submit', async event => {
        event.preventDefault(); save.disabled = true;
        try {
          await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/comments/${encodeURIComponent(comment.id)}`, {
            method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({body:input.value})}));
          await showTranscript(activeConversationId);
        } catch (error) { message.textContent = error.message; save.disabled = false; }
      });
    });
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Delete';
    remove.addEventListener('click', async () => {
      if (!window.confirm('Delete comment?')) return;
      remove.disabled = true;
      try { await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/comments/${encodeURIComponent(comment.id)}`, {method:'DELETE'}));
        await showTranscript(activeConversationId);
      } catch (error) { processingStatus.textContent = error.message; remove.disabled = false; }
    });
    actions.append(edit, remove); item.append(source, quote, body);
    if (!exact) { const status = document.createElement('small'); status.textContent = segment ? 'Source changed' : 'Earlier transcript'; item.append(status); }
    item.append(actions); list.append(item);
  }
}
function openCommentComposer(excerpt) {
  commentsOpen = true; renderTranscriptComments(currentTranscript);
  const list = document.querySelector('#comments-list');
  const form = document.createElement('form'); form.id = 'comment-composer'; form.className = 'comment-composer';
  const quote = document.createElement('blockquote');
  const segment = currentTranscript.segments.find(item => item.id === excerpt.segment_id);
  quote.textContent = segment?.text.slice(excerpt.start_offset, excerpt.end_offset) || '';
  const input = document.createElement('textarea'); input.rows = 3; input.maxLength = 2000;
  input.placeholder = 'Add a comment'; input.setAttribute('aria-label', 'Add a comment');
  const actions = document.createElement('div'); actions.className = 'comment-actions';
  const save = document.createElement('button'); save.type = 'submit'; save.textContent = 'Save'; save.disabled = true;
  const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel';
  cancel.addEventListener('click', () => form.remove());
  input.addEventListener('input', () => { save.disabled = !input.value.trim(); });
  const message = document.createElement('p'); message.setAttribute('role', 'status');
  actions.append(save, cancel); form.append(quote, input, actions, message); list.prepend(form);
  form.addEventListener('submit', async event => {
    event.preventDefault(); save.disabled = true;
    try { await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/comments`, {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...excerpt,body:input.value})}));
      await showTranscript(activeConversationId); processingStatus.textContent = 'Comment saved.';
    } catch (error) { message.textContent = error.message; save.disabled = false; }
  });
  document.querySelector('#transcript-comments').scrollIntoView({block:'nearest'}); input.focus();
}
function hideSelectionToolbar() {
  document.querySelector('#selection-toolbar')?.remove();
  selectedExcerpt = null;
}
function selectedSnippet(excerpt) {
  const segment = transcriptSegments.find(item => item.id === excerpt.segment_id);
  if (!segment || !activeConversationId || currentTranscript?.revision !== excerpt.expected_revision) return null;
  const quote = segment.text.slice(excerpt.start_offset, excerpt.end_offset);
  if (!quote) return null;
  const firstWord = segment.words?.find(word => word.end_offset > excerpt.start_offset &&
    word.start_offset < excerpt.end_offset && segment.text.slice(word.start_offset, word.end_offset) === word.text);
  const sourceTime = firstWord?.start_ms ?? segment.start_ms;
  const source = new URL(`/conversation/${encodeURIComponent(activeConversationId)}`, location.origin);
  source.searchParams.set('at', segment.id);
  source.searchParams.set('t', String(sourceTime));
  return {text:`“${quote}”\n${segment.speaker_display_name}, ${time(sourceTime / 1000)}\nSource on this device: ${source.href}`,
    sourceTime};
}
function openSelectionShare(toolbar, excerpt) {
  toolbar.querySelector('#selection-share-menu')?.remove();
  const menu = document.createElement('div'); menu.id = 'selection-share-menu'; menu.setAttribute('role', 'menu');
  const copy = document.createElement('button'); copy.type = 'button'; copy.setAttribute('role', 'menuitem');
  copy.textContent = 'Copy snippet';
  const download = document.createElement('button'); download.type = 'button'; download.setAttribute('role', 'menuitem');
  download.className = 'button-with-icon';
  download.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/></svg><span>Download text</span>';
  const message = document.createElement('p'); message.setAttribute('role', 'status');
  copy.addEventListener('click', async () => {
    const snippet = selectedSnippet(excerpt);
    if (!snippet) { message.textContent = 'Transcript changed. Select the words again.'; return; }
    try { await navigator.clipboard.writeText(snippet.text);
      window.getSelection()?.removeAllRanges(); hideSelectionToolbar(); processingStatus.textContent = 'Snippet copied.';
    } catch { message.textContent = 'Could not copy. Use Download text.'; }
  });
  download.addEventListener('click', () => {
    const snippet = selectedSnippet(excerpt);
    if (!snippet) { message.textContent = 'Transcript changed. Select the words again.'; return; }
    const url = URL.createObjectURL(new Blob([snippet.text + '\n'], {type:'text/plain;charset=utf-8'}));
    const link = document.createElement('a'); link.href = url;
    link.download = `stellis-snippet-${Math.floor(snippet.sourceTime / 1000)}.txt`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    window.getSelection()?.removeAllRanges(); hideSelectionToolbar(); processingStatus.textContent = 'Snippet downloaded.';
  });
  menu.append(copy, download, message); toolbar.append(menu);
  if (toolbar.getBoundingClientRect().bottom + menu.offsetHeight + 8 > innerHeight) menu.classList.add('share-above');
  menu.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); menu.remove();
      toolbar.querySelector('[aria-haspopup="menu"]')?.setAttribute('aria-expanded', 'false');
      toolbar.querySelector('[aria-haspopup="menu"]')?.focus(); }
  });
  copy.focus();
}
document.addEventListener('pointerdown', event => {
  if (document.querySelector('#selection-share-menu') && !event.target.closest('#selection-toolbar')) hideSelectionToolbar();
});
function inspectSelection() {
  hideSelectionToolbar();
  if (transcriptEditMode) return;
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.toString().trim()) return;
  const range = selection.getRangeAt(0);
  const start = range.startContainer.parentElement?.closest('.segment-text');
  const end = range.endContainer.parentElement?.closest('.segment-text');
  if (!start || start !== end || !transcriptView.contains(start) || start.closest('.segment-highlights')) return;
  const row = start.closest('.segment');
  const segment = transcriptSegments.find(item => item.id === row?.dataset.segmentId);
  if (!segment) return;
  const pre = range.cloneRange(); pre.selectNodeContents(start); pre.setEnd(range.startContainer, range.startOffset);
  const startOffset = pre.toString().length;
  const endOffset = startOffset + range.toString().length;
  if (segment.text.slice(startOffset, endOffset) !== range.toString()) return;
  selectedExcerpt = {segment_id:segment.id, start_offset:startOffset, end_offset:endOffset,
    expected_revision:currentTranscript.revision};
  const toolbar = document.createElement('div'); toolbar.id = 'selection-toolbar'; toolbar.setAttribute('role','toolbar');
  const highlight = document.createElement('button'); highlight.type = 'button'; highlight.textContent = 'Highlight';
  highlight.addEventListener('mousedown', event => event.preventDefault());
  highlight.addEventListener('click', async () => {
    const excerpt = selectedExcerpt;
    if (!excerpt) return;
    try {
      await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/highlights`, {
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(excerpt)}));
      selection.removeAllRanges();
      await showTranscript(activeConversationId);
      processingStatus.textContent = 'Highlight saved.';
    } catch (error) { processingStatus.textContent = error.message; }
  });
  const copy = document.createElement('button'); copy.type = 'button'; copy.textContent = 'Copy';
  copy.addEventListener('mousedown', event => event.preventDefault());
  copy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(selection.toString()); hideSelectionToolbar(); }
    catch { processingStatus.textContent = 'Could not copy.'; }
  });
  const comment = document.createElement('button'); comment.type = 'button'; comment.textContent = 'Comment';
  comment.addEventListener('mousedown', event => event.preventDefault());
  comment.addEventListener('click', () => { const excerpt = selectedExcerpt; hideSelectionToolbar();
    selection.removeAllRanges(); if (excerpt) openCommentComposer(excerpt); });
  const photo = document.createElement('button'); photo.type = 'button'; photo.textContent = 'Add Image';
  photo.addEventListener('mousedown', event => event.preventDefault());
  photo.addEventListener('click', () => {
    const excerpt = selectedExcerpt;
    if (!excerpt) return;
    hideSelectionToolbar(); selection.removeAllRanges();
    const input = document.createElement('input'); input.id = 'selected-image-file';
    input.type = 'file'; input.accept = 'image/png,image/jpeg,image/webp,image/gif'; input.multiple = true;
    input.hidden = true; document.body.append(input);
    input.addEventListener('change', () => {
      const files = [...(input.files || [])]; input.remove();
      if (files.length) openImageDialog('conversation', activeConversationId, files, excerpt.segment_id, excerpt);
    }, {once:true});
    window.addEventListener('focus', () => setTimeout(() => input.remove(), 1000), {once:true});
    input.click();
  });
  const share = document.createElement('button'); share.type = 'button'; share.textContent = 'Share';
  share.setAttribute('aria-haspopup', 'menu'); share.setAttribute('aria-expanded', 'false');
  share.addEventListener('mousedown', event => event.preventDefault());
  share.addEventListener('click', () => {
    const menu = toolbar.querySelector('#selection-share-menu');
    if (menu) { menu.remove(); share.setAttribute('aria-expanded', 'false'); }
    else if (selectedExcerpt) { openSelectionShare(toolbar, selectedExcerpt); share.setAttribute('aria-expanded', 'true'); }
  });
  toolbar.append(highlight, comment, photo, copy, share); document.body.append(toolbar);
  const rect = range.getBoundingClientRect();
  toolbar.style.left = `${Math.max(8, Math.min(window.innerWidth - toolbar.offsetWidth - 8, rect.left + rect.width / 2 - toolbar.offsetWidth / 2))}px`;
  toolbar.style.top = `${Math.max(8, rect.top - toolbar.offsetHeight - 8)}px`;
}
async function removeHighlight(id) {
  try {
    await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/highlights/${encodeURIComponent(id)}`, {method:'DELETE'}));
    await showTranscript(activeConversationId);
  } catch (error) { processingStatus.textContent = error.message; }
}
function closeRowEditor(row) { row.querySelector('.segment-editor')?.remove(); }
function showWordEditor(row, segment) {
  closeRowEditor(row);
  const form = document.createElement('form');
  form.className = 'segment-editor';
  const label = document.createElement('label');
  label.textContent = 'Edit transcript words';
  const input = document.createElement('textarea');
  input.value = segment.text;
  input.rows = 3;
  input.maxLength = 4000;
  input.required = true;
  label.append(input);
  const actions = document.createElement('div');
  actions.className = 'editor-actions';
  const save = document.createElement('button'); save.type = 'submit'; save.textContent = 'Save words';
  const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel'; cancel.addEventListener('click', () => form.remove());
  actions.append(save, cancel);
  if (segment.original_text !== undefined) {
    const revert = document.createElement('button');
    revert.type = 'button'; revert.textContent = 'Restore model text';
    revert.addEventListener('click', () => { input.value = segment.original_text; input.focus(); });
    actions.append(revert);
  }
  const message = document.createElement('p'); message.setAttribute('role', 'status');
  form.append(label, actions, message);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    save.disabled = true;
    try {
      await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/transcript/segments/${encodeURIComponent(segment.id)}`, {
        method: 'PATCH', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({text:input.value, expected_revision:currentTranscript.revision}),
      }));
      await showTranscript(activeConversationId);
      processingStatus.textContent = 'Transcript saved.';
    } catch (error) { message.textContent = error.message; save.disabled = false; }
  });
  row.append(form); form.scrollIntoView({block:'start'}); input.focus(); input.select();
}
function showSpeakerPicker(anchor, row, segment) {
  document.querySelectorAll('.speaker-picker').forEach(picker => picker.remove());
  const picker = document.createElement('div');
  picker.className = 'speaker-picker';
  picker.setAttribute('role', 'dialog');
  picker.setAttribute('aria-label', 'Tag speaker');
  const search = document.createElement('input');
  search.type = 'search'; search.placeholder = 'Search speakers'; search.maxLength = 100;
  search.setAttribute('aria-label', 'Search speakers');
  const tag = document.createElement('button'); tag.type = 'button'; tag.textContent = 'Tag'; tag.disabled = true;
  const top = document.createElement('div'); top.className = 'speaker-picker-search'; top.append(search, tag);
  const list = document.createElement('div'); list.className = 'speaker-picker-list';
  const manage = document.createElement('button'); manage.type = 'button'; manage.className = 'speaker-picker-manage'; manage.textContent = 'Manage Speakers';
  const message = document.createElement('p'); message.setAttribute('role', 'status');
  picker.append(top, list, message, manage);
  const host = anchor.closest('.turn-heading') || anchor.parentElement;
  host.append(picker);
  const close = event => { if (!picker.contains(event.target) && event.target !== anchor) { picker.remove(); document.removeEventListener('pointerdown', close); } };
  document.addEventListener('pointerdown', close);
  picker.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); picker.remove(); anchor.focus(); } });
  const manualNames = [...new Map((currentTranscript?.segments || [])
    .filter(item => !item.person_id && item.speaker_label_source?.startsWith('manual_'))
    .map(item => item.speaker_display_name)
    .filter(Boolean)
    .map(name => [name.trim().replace(/\s+/g, ' ').toLocaleLowerCase(), name])).values()];
  let people = [];
  async function apply(name, personId) {
    tag.disabled = true;
    message.textContent = '';
    try {
      const person = (personId ? people.find(item => item.id === personId) : null) ||
        people.find(item => item.name.toLocaleLowerCase() === name.toLocaleLowerCase());
      const result = await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/person-links`, {
        method:'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({person_id:person?.id || null,name:person ? null : name,segment_id:segment.id,
          scope:segment.speaker_label_source === 'manual_segment' ? 'segment' : segment.speaker_cluster_id ? 'cluster' : 'segment',
          expected_revision:currentTranscript.revision}),
      }));
      picker.remove(); document.removeEventListener('pointerdown', close);
      await showTranscript(activeConversationId);
      processingStatus.textContent = result.preserved_other_people ?
        `Speaker tagged. ${result.preserved_other_people} previously tagged passage${result.preserved_other_people === 1 ? '' : 's'} kept.` : 'Speaker tagged.';
    } catch (error) { message.textContent = error.message; tag.disabled = false; }
  }
  function render() {
    const query = search.value.trim().toLocaleLowerCase();
    tag.disabled = !query;
    list.replaceChildren();
    const title = document.createElement('strong'); title.textContent = 'Select speaker'; list.append(title);
    const names = [...people.filter(person => !person.hidden_at || person.id === segment.person_id).map(person => ({name:person.name,id:person.id})),
      ...manualNames.filter(name => !people.some(person => person.name.toLocaleLowerCase() === name.toLocaleLowerCase())).map(name => ({name,id:null}))];
    for (const item of names.filter(item => !query || item.name.toLocaleLowerCase().includes(query))) {
      const line = document.createElement('div'); line.className = 'speaker-picker-option';
      const option = document.createElement('button'); option.type = 'button'; option.textContent = item.name;
      option.addEventListener('click', () => apply(item.name, item.id)); line.append(option);
      const current = (segment.person_id && segment.person_id === item.id) ||
        (!segment.person_id && segment.speaker_label_source?.startsWith('manual_') && segment.speaker_display_name === item.name);
      if (current) {
        const untag = document.createElement('button'); untag.type = 'button'; untag.textContent = 'Untag';
        untag.addEventListener('click', async () => {
          untag.disabled = true;
          try {
            if (segment.person_id) await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/person-links/remove`, {
              method:'POST',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({segment_id:segment.id,scope:segment.speaker_cluster_id ? 'cluster' : 'segment',expected_revision:currentTranscript.revision}),
            }));
            else await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/speakers/label`, {
              method:'PUT',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({scope:segment.speaker_label_source === 'manual_segment' ? 'segment' : 'cluster',target_id:segment.speaker_label_source === 'manual_segment' ? segment.id : segment.speaker_cluster_id,name:null,expected_revision:currentTranscript.revision}),
            }));
            picker.remove(); document.removeEventListener('pointerdown', close);
            await showTranscript(activeConversationId); processingStatus.textContent = 'Speaker untagged.';
          } catch (error) { message.textContent = error.message; untag.disabled = false; }
        });
        line.append(untag);
      }
      list.append(line);
    }
  }
  search.addEventListener('input', render);
  search.addEventListener('keydown', event => { if (event.key === 'Enter' && !tag.disabled) { event.preventDefault(); tag.click(); } });
  tag.addEventListener('click', () => { const name = search.value.trim(); if (name) apply(name, null); });
  manage.addEventListener('click', () => {
    picker.remove(); document.removeEventListener('pointerdown', close);
    showManageSpeakers(row, segment);
  });
  fetch(`${API}/persons`).then(json).then(items => { if (picker.isConnected) { people = items; render(); } })
    .catch(error => { message.textContent = error.message; });
  render(); search.focus();
}
async function showManageSpeakers(row, segment) {
  document.querySelector('#manage-speakers-dialog')?.remove();
  const dialog = document.createElement('dialog'); dialog.id = 'manage-speakers-dialog';
  const heading = document.createElement('div'); heading.className = 'manage-speakers-heading';
  const title = document.createElement('h2'); title.textContent = 'Manage Speakers';
  const close = document.createElement('button'); close.type = 'button'; close.append(icon('x')); close.setAttribute('aria-label', 'Close');
  close.addEventListener('click', () => dialog.close()); heading.append(title, close);
  const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Search'; search.setAttribute('aria-label', 'Search speakers');
  const list = document.createElement('div'); list.className = 'manage-speakers-list';
  const detail = document.createElement('div'); detail.className = 'manage-speakers-detail';
  const message = document.createElement('p'); message.setAttribute('role', 'status');
  const merge = document.createElement('button'); merge.type = 'button'; merge.textContent = 'Merge'; merge.disabled = true;
  const editPassage = document.createElement('button'); editPassage.type = 'button'; editPassage.textContent = 'Edit this passage';
  const footer = document.createElement('div'); footer.className = 'manage-speakers-footer'; footer.append(editPassage, merge);
  const body = document.createElement('div'); body.className = 'manage-speakers-body'; body.append(list, detail);
  dialog.append(heading, search, body, message, footer); document.body.append(dialog);
  dialog.addEventListener('close', () => dialog.remove());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.showModal();
  let people = [];
  const selected = new Set();
  function render() {
    list.replaceChildren(); detail.replaceChildren();
    for (const person of people.filter(item => item.name.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase()))) {
      const item = document.createElement('div'); item.className = 'manage-speaker-row';
      const check = document.createElement('input'); check.type = 'checkbox'; check.checked = selected.has(person.id);
      check.setAttribute('aria-label', `Select ${person.name}`);
      check.addEventListener('change', () => { if (check.checked) selected.add(person.id); else selected.delete(person.id); render(); });
      const identity = document.createElement('span'); identity.textContent = person.name;
      if (person.hidden_at) { const hidden = document.createElement('small'); hidden.textContent = 'Hidden'; identity.append(hidden); }
      const rename = document.createElement('button'); rename.type = 'button'; rename.textContent = 'Rename';
      rename.setAttribute('aria-label', `Rename ${person.name}`);
      rename.addEventListener('click', () => {
        const form = document.createElement('form'); form.className = 'manage-speaker-rename';
        const input = document.createElement('input'); input.value = person.name; input.maxLength = 100; input.setAttribute('aria-label', 'Speaker name');
        const save = document.createElement('button'); save.textContent = 'Save';
        const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel'; cancel.addEventListener('click', render);
        form.append(input, save, cancel); item.replaceChildren(form); input.focus(); input.select();
        form.addEventListener('submit', async event => {
          event.preventDefault(); save.disabled = true;
          try { await json(await fetch(`${API}/persons/${encodeURIComponent(person.id)}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:input.value})}));
            people = await json(await fetch(`${API}/persons`)); message.textContent = 'Speaker renamed.'; render(); await showTranscript(activeConversationId);
          } catch (error) { message.textContent = error.message; save.disabled = false; }
        });
      });
      const visibility = document.createElement('button'); visibility.type = 'button'; visibility.textContent = person.hidden_at ? 'Show' : 'Hide';
      visibility.setAttribute('aria-label', `${person.hidden_at ? 'Show' : 'Hide'} ${person.name}`);
      visibility.addEventListener('click', async () => {
        visibility.disabled = true;
        try { await json(await fetch(`${API}/persons/${encodeURIComponent(person.id)}/visibility`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({hidden:!person.hidden_at})}));
          people = await json(await fetch(`${API}/persons`)); message.textContent = person.hidden_at ? 'Speaker shown.' : 'Speaker hidden.'; render();
        } catch (error) { message.textContent = error.message; visibility.disabled = false; }
      });
      item.append(check, identity, rename, visibility); list.append(item);
    }
    const manual = (currentTranscript?.speaker_labels || []).filter(label => {
      const matches = currentTranscript.segments.filter(item =>
        label.scope === 'cluster' ? item.speaker_cluster_id === label.target_id : item.id === label.target_id);
      return matches.some(item => !item.person_id) && label.name.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase());
    });
    const distinctManual = [...new Map(manual.map(label => [label.name.trim().replace(/\s+/g, ' ').toLocaleLowerCase(), label])).values()]
      .filter(label => !people.some(person => person.name.trim().replace(/\s+/g, ' ').toLocaleLowerCase() ===
        label.name.trim().replace(/\s+/g, ' ').toLocaleLowerCase()));
    for (const label of distinctManual) {
      const item = document.createElement('div'); item.className = 'manage-speaker-row';
      const identity = document.createElement('span'); identity.textContent = label.name;
      const local = document.createElement('small'); local.textContent = 'This recording'; identity.append(local);
      const tag = document.createElement('button'); tag.type = 'button'; tag.textContent = 'Tag';
      tag.addEventListener('click', async () => {
        const first = currentTranscript.segments.find(item => !item.person_id &&
          item.speaker_display_name.trim().replace(/\s+/g, ' ').toLocaleLowerCase() ===
            label.name.trim().replace(/\s+/g, ' ').toLocaleLowerCase() &&
          (label.scope === 'cluster' ? item.speaker_cluster_id === label.target_id : item.id === label.target_id));
        if (!first) return;
        tag.disabled = true;
        try { await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/person-links`, {
          method:'PUT',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({name:label.name,segment_id:first.id,
            scope:first.speaker_label_source === 'manual_segment' ? 'segment' : label.scope,
            expected_revision:currentTranscript.revision}),
        })); await showTranscript(activeConversationId); people = await json(await fetch(`${API}/persons`)); message.textContent = 'Speaker tagged.'; render();
        } catch (error) { message.textContent = error.message; tag.disabled = false; }
      });
      item.append(identity, tag); list.append(item);
    }
    merge.disabled = selected.size !== 2;
    if (selected.size === 2) {
      const label = document.createElement('label'); label.textContent = 'Keep speaker';
      const target = document.createElement('select'); target.id = 'manage-speakers-target';
      for (const person of people.filter(item => selected.has(item.id))) target.append(new Option(person.name, person.id));
      label.append(target); detail.append(label);
    } else detail.textContent = 'Select two speakers to merge.';
  }
  search.addEventListener('input', render);
  editPassage.addEventListener('click', () => {
    dialog.close();
    if (pendingTranscriptEdits.size) { editStatus.textContent = 'Save your word changes with Done first.'; return; }
    showSpeakerEditor(row, segment);
  });
  merge.addEventListener('click', async () => {
    const targetId = document.querySelector('#manage-speakers-target')?.value;
    const sourceId = [...selected].find(id => id !== targetId);
    if (!targetId || !sourceId) return;
    const targetName = people.find(person => person.id === targetId)?.name;
    const sourceName = people.find(person => person.id === sourceId)?.name;
    if (!window.confirm(`Merge ${sourceName} into ${targetName}?`)) return;
    merge.disabled = true;
    try { await json(await fetch(`${API}/persons/${encodeURIComponent(targetId)}/merge`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_id:sourceId})}));
      selected.clear(); people = await json(await fetch(`${API}/persons`)); message.textContent = 'Speakers merged.'; render(); await showTranscript(activeConversationId);
    } catch (error) { message.textContent = error.message; merge.disabled = false; }
  });
  try { people = await json(await fetch(`${API}/persons`)); render(); search.focus(); }
  catch (error) { message.textContent = error.message; }
}
function showSpeakerEditor(row, segment) {
  closeRowEditor(row);
  const form = document.createElement('div'); form.className = 'segment-editor';
  const scopeLabel = document.createElement('label'); scopeLabel.textContent = 'Apply to';
  const scope = document.createElement('select');
  for (const [value, caption] of [['cluster','All matching speaker turns'],['segment','This passage only']]) {
    if (value === 'cluster' && !segment.speaker_cluster_id) continue;
    const option = document.createElement('option'); option.value = value; option.textContent = caption; scope.append(option);
  }
  scope.value = segment.speaker_label_source === 'manual_segment' ? 'segment' : (segment.speaker_cluster_id ? 'cluster' : 'segment');
  scopeLabel.append(scope);
  const actions = document.createElement('div'); actions.className = 'editor-actions';
  const clear = document.createElement('button'); clear.type = 'button'; clear.textContent = 'Clear manual label';
  const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel'; cancel.addEventListener('click', () => form.remove());
  actions.append(clear, cancel);
  clear.hidden = !segment.speaker_label_source?.startsWith('manual_') || !!segment.person_id;
  const message = document.createElement('p'); message.setAttribute('role', 'status');
  const personSection = document.createElement('div'); personSection.className = 'person-confirmation';
  const personTitle = document.createElement('strong'); personTitle.textContent = 'Tag speaker';
  const personSelectLabel = document.createElement('label'); personSelectLabel.textContent = 'Existing speaker';
  const personSelect = document.createElement('select'); personSelect.append(new Option('Choose a person', ''));
  personSelectLabel.append(personSelect);
  const newPersonLabel = document.createElement('label'); newPersonLabel.textContent = 'New speaker name';
  const newPerson = document.createElement('input'); newPerson.type = 'text'; newPerson.maxLength = 100;
  newPerson.placeholder = 'Name';
  if (segment.speaker_label_source?.startsWith('manual_') && !segment.person_id) newPerson.value = segment.speaker_display_name;
  newPersonLabel.append(newPerson);
  const personActions = document.createElement('div'); personActions.className = 'editor-actions';
  const confirm = document.createElement('button'); confirm.type = 'button'; confirm.textContent = 'Tag';
  const unlink = document.createElement('button'); unlink.type = 'button'; unlink.textContent = 'Untag'; unlink.hidden = !segment.person_id;
  personActions.append(confirm, unlink);
  personSection.append(personTitle, personSelectLabel, newPersonLabel, personActions);
  fetch(`${API}/persons`).then(json).then(people => {
    personSelect.replaceChildren(new Option('Choose a person', ''), ...people.map(person => new Option(person.name, person.id)));
    if (segment.person_id) personSelect.value = segment.person_id;
  }).catch(error => { message.textContent = error.message; });
  personSelect.addEventListener('change', () => { if (personSelect.value) newPerson.value = ''; });
  newPerson.addEventListener('input', () => { if (newPerson.value.trim()) personSelect.value = ''; });
  confirm.addEventListener('click', async () => {
    confirm.disabled = true;
    try {
      const speakerName = newPerson.value.trim();
      if (!speakerName && !personSelect.value) throw new Error('Choose or name a speaker.');
      await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/person-links`, {
        method:'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({person_id:speakerName ? null : personSelect.value,name:speakerName || null,
          segment_id:segment.id,scope:scope.value,expected_revision:currentTranscript.revision}),
      }));
      await showTranscript(activeConversationId);
      processingStatus.textContent = 'Speaker tagged.';
    } catch (error) { message.textContent = error.message; confirm.disabled = false; }
  });
  newPerson.addEventListener('keydown', event => { if (event.key === 'Enter') {event.preventDefault();confirm.click();} });
  unlink.addEventListener('click', async () => {
    unlink.disabled = true;
    try {
      await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/person-links/remove`, {
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({segment_id:segment.id,scope:scope.value,expected_revision:currentTranscript.revision}),
      }));
      await showTranscript(activeConversationId);
      processingStatus.textContent = 'Speaker untagged.';
    } catch (error) { message.textContent = error.message; unlink.disabled = false; }
  });
  clear.addEventListener('click', async () => {
    const targetId = scope.value === 'cluster' ? segment.speaker_cluster_id : segment.id;
    clear.disabled = true;
    try {
      await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/speakers/label`, {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({scope:scope.value,target_id:targetId,name:null,expected_revision:currentTranscript.revision}),
      }));
      await showTranscript(activeConversationId);
      processingStatus.textContent = 'Speaker label cleared.';
    } catch (error) { message.textContent = error.message; clear.disabled = false; }
  });
  form.append(scopeLabel, personSection, actions, message);
  row.append(form); form.scrollIntoView({block:'start'}); newPerson.focus();
}
let vocabularyKind = 'other';
let vocabularyTerms = [];
const vocabularySelected = new Set();
const vocabularyStatus = document.querySelector('#vocabulary-status');
function vocabularyLetter(term) {
  const initial = term.normalize('NFD').replace(/[\u0300-\u036f]/g, '').charAt(0).toUpperCase();
  return /^[A-Z]$/.test(initial) ? initial : '#';
}
function syncVocabularySelection() {
  const count = vocabularySelected.size;
  document.querySelector('#vocabulary-delete-selected').disabled = !count;
  document.querySelector('#vocabulary-move-selected').disabled = !count;
  const selectAll = document.querySelector('#vocabulary-select-all');
  selectAll.checked = !!vocabularyTerms.length && count === vocabularyTerms.length;
  selectAll.indeterminate = count > 0 && count < vocabularyTerms.length;
}
function renderVocabularyTerms() {
  const container = document.querySelector('#vocabulary-list');
  const letters = document.querySelector('#vocabulary-letters');
  container.replaceChildren(); letters.replaceChildren(); vocabularySelected.clear();
  document.querySelector('#vocabulary-empty').hidden = !!vocabularyTerms.length;
  document.querySelector('#vocabulary-bulk').hidden = !vocabularyTerms.length;
  const groups = new Map();
  for (const term of vocabularyTerms) {
    const letter = vocabularyLetter(term.term);
    if (!groups.has(letter)) groups.set(letter, []);
    groups.get(letter).push(term);
  }
  for (const [letter, terms] of groups) {
    const jump = document.createElement('a'); jump.href = `#vocabulary-letter-${letter === '#' ? 'other' : letter}`;
    jump.textContent = letter; letters.append(jump);
    const group = document.createElement('section'); group.className = 'vocab-group';
    const heading = document.createElement('h3'); heading.id = `vocabulary-letter-${letter === '#' ? 'other' : letter}`;
    heading.textContent = letter; group.append(heading);
    for (const term of terms) {
      const row = document.createElement('div'); row.className = 'vocab-row';
      const label = document.createElement('label');
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = term.id;
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) vocabularySelected.add(term.id); else vocabularySelected.delete(term.id);
        syncVocabularySelection();
      });
      const name = document.createElement('span'); name.textContent = term.term;
      label.append(checkbox, name);
      const edit = document.createElement('button'); edit.type = 'button'; edit.textContent = 'Edit';
      edit.addEventListener('click', () => {
        row.replaceChildren();
        const form = document.createElement('form'); form.className = 'vocab-edit';
        const input = document.createElement('input'); input.value = term.term; input.maxLength = 100; input.required = true;
        input.setAttribute('aria-label', `Edit ${term.term}`);
        const save = document.createElement('button'); save.type = 'submit'; save.className = 'button-with-icon';
        save.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 3h14l3 3v15H3V3h1Zm3 0v6h10V3M7 21v-8h10v8"/></svg><span>Save</span>';
        const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel';
        cancel.addEventListener('click', () => renderVocabularyTerms());
        form.append(input, save, cancel); row.append(form); input.focus();
        form.addEventListener('submit', async event => {
          event.preventDefault(); save.disabled = true;
          try {
            await json(await fetch(`${API}/vocabulary/${encodeURIComponent(term.id)}`, {method:'PATCH',
              headers:{'Content-Type':'application/json'},body:JSON.stringify({term:input.value})}));
            vocabularyStatus.textContent = 'Saved.'; await loadVocabulary();
          } catch (error) { vocabularyStatus.textContent = error.message; save.disabled = false; }
        });
      });
      row.append(label, edit); group.append(row);
    }
    container.append(group);
  }
  syncVocabularySelection();
}
async function loadVocabulary() {
  const terms = await json(await fetch(`${API}/vocabulary`));
  const nameCount = terms.filter(term => term.kind === 'name').length;
  const otherCount = terms.filter(term => term.kind === 'other').length;
  document.querySelector('#vocabulary-name-count').textContent = `${nameCount} ${nameCount === 1 ? 'term' : 'terms'}`;
  document.querySelector('#vocabulary-other-count').textContent = `${otherCount} ${otherCount === 1 ? 'term' : 'terms'}`;
  vocabularyTerms = terms.filter(term => term.kind === vocabularyKind);
  renderVocabularyTerms();
}
async function showVocabulary(section = null) {
  await flushNotes(notesStates.conversation);
  await leaveNote();
  await flushEnhanced();
  suggestionsUi.conversation.clear();
  document.body.classList.remove('profile-page', 'conversation-page', 'recording-page');
  recordingPanel.hidden = true;
  if (conversationCategoriesDialog.open) conversationCategoriesDialog.close();
  conversationMore.open = false; conversationMore.hidden = true;
  clearTimeout(jobTimer); clearTimeout(aiRunTimer); clearTimeout(enhancedPollTimer);
  clearTimeout(recordingChatTimer); clearTimeout(cleanRunTimer); clearTimeout(silenceScanTimer);
  player.pause(); player.removeAttribute('src'); player.load();
  activeConversationId = null; activePersonId = null;
  listView.hidden = true; detailView.hidden = true;
  document.querySelector('#trash-view').hidden = true;
  document.querySelector('#person-view').hidden = true; hideAppPages();
  document.querySelector('#vocabulary-panel').hidden = false;
  document.querySelector('#vocabulary-overview').hidden = !!section;
  document.querySelector('#vocabulary-terms-page').hidden = !section;
  for (const link of document.querySelectorAll('.vocab-subnav a')) {
    if (link.getAttribute('href') === location.pathname) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  vocabularyKind = section === 'names' ? 'name' : 'other';
  document.querySelector('#page-title').textContent = 'Terms';
  document.querySelector('#vocabulary-kind-title').textContent = section === 'names' ? 'Names of people' : 'Other vocabulary';
  document.querySelector('#vocabulary-form label').textContent = section === 'names' ? 'Add names of people' : 'Add words or phrases';
  document.querySelector('#vocabulary-move-selected').textContent = section === 'names' ? 'Move to Other vocabulary' : 'Move to Names of people';
  vocabularyStatus.textContent = '';
  await loadVocabulary();
  captureControls();
}
/**
 * Shows the page for the current address. The old page stays on screen while the new one is prepared,
 * then the view cuts over in one frame (no cross-fade), so a half-built page or a blank one never
 * flashes between them (owner, 2026-10-28). A page that takes longer than 400 ms is shown as it builds.
 */
async function route() {
  // A page that takes a moment shows the page skeleton (html[data-routing]) instead of the last page
  // frozen behind it (owner, 2026-10-30: "a flickering ghost of a different page, and NO loaders").
  // A quick page never shows it.
  const root = document.documentElement;
  const status = document.querySelector('#route-label');
  if (status) status.textContent = routeLabel();
  if ('routing' in root.dataset) return renderRoute();
  const slow = setTimeout(() => { root.dataset.routing = ''; }, 100);
  try {
    return await renderRoute();
  } finally {
    clearTimeout(slow);
    delete root.dataset.routing;
  }
}
/** What the page being opened is called: the link that leads there (not the brand), or the kind of page. */
function routeLabel() {
  const path = location.pathname;
  const link = [...document.querySelectorAll('a[href]:not(.brand)')].find(a => a.pathname === path && a.textContent.trim());
  const kinds = [[/^\/notebook\//, 'the notebook'], [/^\/document\//, 'the report'], [/^\/conversation\//, 'the recording'],
    [/^\/page\//, 'the note'], [/^\/trends$/, 'Measurements'], [/^\/records$/, 'Records'], [/^\/chat/, 'Ask Stellis'],
    [/^\/timeline$/, 'Timeline'], [/^\/settings$/, 'Settings'], [/^\/todos$/, 'To-dos'], [/^\/summary$/, 'Summary'], [/^\/$/, 'My Health']];
  const name = link?.textContent.replace(/\s+/g, ' ').trim().slice(0, 60) || kinds.find(([pattern]) => pattern.test(path))?.[1] || 'the page';
  return `Opening ${name}`;
}
async function renderRoute() {
  await modulesReady;
  const match = location.pathname.match(/^\/conversation\/([^/]+)$/);
  if ((pendingTranscriptEdits.size || pendingCleanEdits.size) && (!match || decodeURIComponent(match[1]) !== activeConversationId)) {
    if (!window.confirm('Discard unsaved transcript changes and leave this conversation?')) {
      history.pushState({}, '', `/conversation/${encodeURIComponent(activeConversationId)}`);
      return;
    }
  }
  if (match) await showDetail(decodeURIComponent(match[1]));
  else if (location.pathname === '/recording') showRecordingWorkspace();
  else if (location.pathname === '/vocabulary') await showVocabulary();
  else if (location.pathname === '/vocabulary/names') await showVocabulary('names');
  else if (location.pathname === '/vocabulary/other') await showVocabulary('other');
  else if (location.pathname.match(/^\/person\/([^/]+)$/))
    await showPerson(decodeURIComponent(location.pathname.split('/')[2]));
  else if (location.pathname === '/settings') await showSettings();
  else if (location.pathname === '/chat' || location.pathname.startsWith('/chat/')) await showAgentChat();
  else if (location.pathname === '/todos') await showTodos();
  else if (location.pathname === '/timeline') await showTimeline();
  else if (location.pathname === '/records') await showRecords();
  else if (location.pathname === '/summary') await showSummary();
  else if (location.pathname === '/trends') await showTrends();
  else if (location.pathname.match(/^\/page\/([^/]+)$/)) await showNote(decodeURIComponent(location.pathname.split('/')[2]));
  else if (location.pathname.match(/^\/document\/([^/]+)$/)) await showDocument(decodeURIComponent(location.pathname.split('/')[2]));
  else if (location.pathname.match(/^\/notebook\/([^/]+)$/))
    await showNotebook(decodeURIComponent(location.pathname.split('/')[2]));
  else await showList();
  window.StellisNavigation?.refresh();
}
/**
 * Uploads a PDF or image from the Import dialog and opens it.
 * @param {!File} file
 */
async function importDocument(file) {
  importStatus.textContent = `Uploading ${file.name}`;
  fileInput.disabled = true;
  const body = new FormData();
  body.append('file', file);
  if (pendingNotebookId) {
    body.append('notebook_id', pendingNotebookId);
  }
  try {
    const result =
        await json(await fetch(`${API}/documents`, {method: 'POST', body}));
    importDialog.close();
    importStatus.textContent = '';
    const id = encodeURIComponent(result.document.id);
    history.pushState({}, '', `/document/${id}`);
    await showDocument(result.document.id);
    setStatus(result.duplicate ? 'Already uploaded' : 'Document uploaded', true);
  } catch (error) {
    importStatus.textContent = error.message;
  } finally {
    fileInput.disabled = false;
    fileInput.value = '';
  }
}
async function importFile(file) {
  if (!file) return;
  if (/\.(pdf|png|jpe?g)$/i.test(file.name)) return importDocument(file);
  if (!file.name.toLowerCase().endsWith('.mp3')) {
    importStatus.textContent = 'Choose an MP3, PDF, PNG or JPEG file';
    return;
  }
  if ((pendingTranscriptEdits.size || pendingCleanEdits.size) && !window.confirm('Discard unsaved transcript changes and import another recording?')) return;
  importStatus.textContent = `Importing ${file.name}…`;
  setStatus(`Importing ${file.name}…`);
  fileInput.disabled = true;
  document.querySelector('#import-close').disabled = true;
  const body = new FormData();
  body.append('file', file);
  // The file's modified time is the best capture time a browser exposes.
  if (file.lastModified) body.append('recorded_at', new Date(file.lastModified).toISOString());
  if (pendingNotebookId) body.append('notebook_id', pendingNotebookId);
  try {
    const result = await json(await fetch(`${API}/conversations/import`, {method: 'POST', body}));
    history.pushState({}, '', `/conversation/${encodeURIComponent(result.conversation.id)}`);
    await showDetail(result.conversation.id);
    showConversationTab('transcript');
    importDialog.close();
    importStatus.textContent = '';
    setStatus(result.duplicate ? 'Already imported. Opening your recording.' : 'Recording imported.', true);
  } catch (error) {
    importStatus.textContent = error.message;
    setStatus(error.message);
  } finally {
    fileInput.disabled = false;
    document.querySelector('#import-close').disabled = false;
    fileInput.value = '';
  }
}
function captureElapsedMs() {
  if (!capture) return 0;
  const paused = capture.pausedAt ? Date.now() - capture.pausedAt : 0;
  return Math.max(0, Date.now() - capture.startedAt - capture.totalPausedMs - paused);
}
function drawRecordingWaveform(current, audioContext) {
  const canvas = document.querySelector('#recording-waveform');
  const context = canvas.getContext('2d');
  if (!context || !audioContext) {audioContext?.close().catch(() => {}); return () => {};}
  const source = audioContext.createMediaStreamSource(current.stream);
  const analyser = audioContext.createAnalyser(); analyser.fftSize = 1024;
  source.connect(analyser);
  const samples = new Uint8Array(analyser.fftSize);
  const color = getComputedStyle(document.documentElement).getPropertyValue('--waveform').trim() || '#335C63';
  let frame;
  const draw = () => {
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (capture === current && current.recorder.state === 'recording') analyser.getByteTimeDomainData(samples);
    const count = 48, barWidth = 3, gap = 3;
    context.fillStyle = color;
    for (let index = 0; index < count; index++) {
      let peak = 0;
      if (capture === current && current.recorder.state === 'recording') {
        const start = Math.floor(index * samples.length / count);
        const end = Math.floor((index + 1) * samples.length / count);
        for (let sample = start; sample < end; sample++) peak = Math.max(peak, Math.abs(samples[sample] - 128));
      }
      const height = Math.max(2, Math.min(38, peak * 1.2 + 2));
      context.fillRect(index * (barWidth + gap) + 12, (canvas.height - height) / 2, barWidth, height);
    }
    frame = requestAnimationFrame(draw);
  };
  draw();
  return () => {cancelAnimationFrame(frame); source.disconnect(); audioContext.close().catch(() => {});
    context.clearRect(0, 0, canvas.width, canvas.height);};
}
// Agents by ID for the answerer line on recording chat cards; refreshed when a recording chat loads.
let recordingAgents = new Map();
async function loadRecordingAgents() {
  try { recordingAgents = new Map((await json(await fetch(`${API}/agents`))).map(agent => [agent.id, agent])); } catch {}
}
function recordingAnswerer(agentId) {
  const agent = recordingAgents.get(agentId);
  if (!agent) return null;
  const line = document.createElement('span'); line.className = 'recording-chat-agent';
  const avatar = window.StellisAgentAvatar.avatarElement(document, {seed: agent.id, coordinator: window.StellisAgentAvatar.isCoordinator(agent), size: 20});
  const name = document.createElement('strong'); name.textContent = agent.name;
  line.append(avatar, name);
  return line;
}
function showLiveSource(startMs) {
  if (!Number.isFinite(startMs) || startMs < 0) return;
  showRecordingView('transcript');
  const row = [...document.querySelectorAll('#recording-transcript-preview [data-start-ms]')]
    .find(item => Number(item.dataset.startMs) === startMs);
  if (row) { row.tabIndex = -1; row.focus({preventScroll:true}); row.scrollIntoView({block:'center'}); }
}
function renderRecordingChat(runs, container, saved = false) {
  container.replaceChildren();
  for (const run of runs) {
    const card = document.createElement('article');
    const heading = document.createElement(saved ? 'h4' : 'h3'); heading.textContent = run.question;
    const answer = document.createElement('p');
    const answerText = run.state === 'succeeded' ? run.result.answer :
      run.state === 'failed' ? run.error_message : `Answering: ${run.stage}`;
    const links = /\[([^\]\n]{1,120})\]\((\/(?!\/)[^\s)]+)\)/g;
    let offset = 0;
    for (const match of answerText.matchAll(links)) {
      answer.append(document.createTextNode(answerText.slice(offset, match.index)));
      const link = document.createElement('a'); link.href = match[2]; link.textContent = match[1];
      if (!saved && match[2].startsWith('/recording?t=')) link.addEventListener('click', event => {
        event.preventDefault(); showLiveSource(Number(new URL(link.href).searchParams.get('t')));
      });
      answer.append(link); offset = match.index + match[0].length;
    }
    answer.append(document.createTextNode(answerText.slice(offset)));
    const answerer = recordingAnswerer(run.agent_id);
    card.append(heading, ...(answerer ? [answerer] : []), answer);
    if (run.state === 'succeeded' && run.result.source_segment_ids.length) {
      if (saved) card.append(sourceButtons(run.result.source_segment_ids, run.source_starts));
      else {const times = document.createElement('small');
        for (const id of run.result.source_segment_ids) {
          const startMs = run.source_starts[id];
          if (!Number.isFinite(startMs)) continue;
          const button = document.createElement('button'); button.type = 'button';
          button.textContent = time(startMs / 1000);
          button.setAttribute('aria-label', `Show live transcript at ${time(startMs / 1000)}`);
          button.addEventListener('click', () => showLiveSource(startMs));
          times.append(button);
        }
        card.append(times);}
    }
    if (!saved && run.state === 'succeeded' && run.result.source_segment_ids.length) {
      const note = document.createElement('small'); note.textContent = 'Based on live transcript'; card.append(note);
    }
    container.append(card);
  }
}
async function loadRecordingChat(recordingId) {
  clearTimeout(recordingChatTimer);
  const [runs] = await Promise.all([json(await fetch(`${API}/recordings/${encodeURIComponent(recordingId)}/chat`)), loadRecordingAgents()]);
  if (capture?.session.id !== recordingId && notesStates.recording?.id !== recordingId) return;
  renderRecordingChat(runs, document.querySelector('#recording-chat-messages'));
  if (runs.some(run => run.state === 'queued' || run.state === 'running'))
    recordingChatTimer = setTimeout(() => loadRecordingChat(recordingId).catch(error => {
      document.querySelector('#recording-chat-status').textContent = error.message;}), 2000);
}
async function askRecordingChat(question) {
  const recordingId = capture?.session.id || notesStates.recording?.id;
  const status = document.querySelector('#recording-chat-status');
  if (!recordingId) {status.textContent = 'Start recording first.'; return;}
  status.textContent = 'Answering…';
  try {
    await json(await fetch(`${API}/recordings/${encodeURIComponent(recordingId)}/chat`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question})}));
    document.querySelector('#recording-chat-input').value = '';
    status.textContent = '';
    await loadRecordingChat(recordingId);
  } catch (error) {status.textContent = error.message;}
}
document.querySelector('#recording-chat-form').addEventListener('submit', event => {
  event.preventDefault(); askRecordingChat(document.querySelector('#recording-chat-input').value);
});
document.querySelectorAll('[data-live-question]').forEach(button => button.addEventListener('click', () =>
  askRecordingChat(button.dataset.liveQuestion)));
async function loadSavedRecordingChat(conversationId) {
  const [runs] = await Promise.all([json(await fetch(`${API}/conversations/${encodeURIComponent(conversationId)}/recording-chat`)), loadRecordingAgents()]);
  if (conversationId !== activeConversationId) return;
  const container = document.querySelector('#saved-recording-chat'); container.replaceChildren();
  if (!runs.length) return;
  const heading = document.createElement('h3'); heading.textContent = 'During recording'; container.append(heading);
  const messages = document.createElement('div'); renderRecordingChat(runs, messages, true); container.append(messages);
  if (runs.some(run => run.state === 'queued' || run.state === 'running'))
    setTimeout(() => loadSavedRecordingChat(conversationId).catch(() => {}), 2000);
}
function captureControls() {
  document.querySelector('#recording-notebook-change').disabled = captureRequesting || captureFinalizing || recordingDestinationSaving;
  document.querySelector('#recording-microphone').disabled = captureRequesting || captureFinalizing || microphoneSwitching;
  const active = Boolean(capture?.recorder && capture.recorder.state !== 'inactive');
  if (active && (captureHeartbeatId !== capture.session.id || Date.now() - captureHeartbeatAt >= 1000)) {
    try { localStorage.setItem(recordingHeartbeatKey(capture.session.id), JSON.stringify({at:Date.now(), state:capture.recorder.state})); } catch {}
    captureHeartbeatId = capture.session.id;
    captureHeartbeatAt = Date.now();
  }
  document.querySelector('#start-recording').hidden = active;
  document.querySelector('#start-recording').disabled = captureRequesting || captureFinalizing;
  document.querySelector('#stop-recording').hidden = !active;
  document.querySelector('#pause-recording').hidden = !active || !capture.recorder.pause;
  document.querySelector('#pause-recording').textContent = capture?.recorder?.state === 'paused' ? 'Resume' : 'Pause';
  document.querySelector('#back-from-recording').disabled = captureRequesting || captureFinalizing;
  document.querySelector('#record-button .button-label').textContent = active ? 'Open recording' : 'Record';
  const elapsed = time(captureElapsedMs() / 1000);
  recordingTimer.textContent = elapsed;
  document.querySelector('#capture-header-controls').hidden = !active || location.pathname === '/recording';
  document.querySelector('#capture-header-timer').textContent = elapsed;
  const headerPause = document.querySelector('#capture-header-pause');
  const paused = capture?.recorder?.state === 'paused';
  headerPause.textContent = paused ? '▶' : 'Ⅱ';
  headerPause.setAttribute('aria-label', paused ? 'Resume recording' : 'Pause recording');
  headerPause.title = paused ? 'Resume recording' : 'Pause recording';
  const liveCard = document.querySelector('#live-recording-card');
  liveCard.hidden = !active;
  document.querySelector('#recording-live-peek').hidden = !active || document.querySelector('#recording-notes-panel').hidden;
  if (active) {
    document.querySelector('#live-recording-state').textContent = capture.recorder.state === 'paused' ? 'Paused' : 'Live';
    document.querySelector('#live-recording-title').textContent = document.querySelector('#recording-title').value.trim() || capture.session.title;
    document.querySelector('#live-recording-time').textContent = elapsed;
  }
  document.querySelector('#add-recording-image').disabled = !(capture?.session.id || notesStates.recording?.id);
}
function showRecordingView(view) {
  if (view !== 'chat') recordingPrimaryView = view;
  const notes = view === 'notes';
  const transcript = view === 'transcript';
  document.querySelector('#recording-notes-panel').hidden = !notes;
  document.querySelector('#recording-transcript-panel').hidden = !transcript;
  document.querySelector('#recording-notes-tab').setAttribute('aria-selected', String(notes));
  document.querySelector('#recording-transcript-tab').setAttribute('aria-selected', String(transcript));
  document.querySelector('#recording-chat-toggle').setAttribute('aria-expanded', String(view === 'chat'));
  document.querySelector('.recording-workspace-grid').classList.toggle('recording-chat-open', view === 'chat');
  document.querySelector('#recording-live-peek').hidden = !notes || !capture;
}
document.querySelector('#recording-notes-tab').addEventListener('click', () => showRecordingView('notes'));
document.querySelector('#recording-transcript-tab').addEventListener('click', () => showRecordingView('transcript'));
document.querySelector('#recording-live-peek').addEventListener('click', () => showRecordingView('transcript'));
document.querySelector('#recording-chat-toggle').addEventListener('click', () =>
  showRecordingView(document.querySelector('#recording-chat-toggle').getAttribute('aria-expanded') === 'true' ? recordingPrimaryView : 'chat'));
function showRecordingWorkspace() {
  refreshAI();
  document.body.classList.remove('profile-page', 'conversation-page');
  document.body.classList.add('recording-page');
  conversationMore.open = false; conversationMore.hidden = true;
  clearTimeout(jobTimer); clearTimeout(aiRunTimer); clearTimeout(enhancedPollTimer); clearTimeout(cleanRunTimer);
  clearTimeout(recordingChatTimer);
  clearTimeout(silenceScanTimer); silenceSpans = [];
  player.pause(); player.removeAttribute('src'); player.load();
  activeConversationId = null;
  listView.hidden = true; detailView.hidden = true;
  document.querySelector('#trash-view').hidden = true;
  document.querySelector('#person-view').hidden = true; hideAppPages();
  document.querySelector('#vocabulary-panel').hidden = true;
  recordingPanel.hidden = false;
  refreshRecordingDestination();
  document.querySelector('#page-title').textContent = 'New recording';
  showRecordingView('notes');
  if (!capture) {
    document.querySelector('#recording-transcript-state').textContent = 'Listening…';
    document.querySelector('#recording-transcript-preview').replaceChildren();
    document.querySelector('#recording-chat-messages').replaceChildren();
    document.querySelector('#recording-chat-status').textContent = '';
  }
  captureControls();
}
function renderRecordingPreview(result) {
  const container = document.querySelector('#recording-transcript-preview'); container.replaceChildren();
  const segments = result.segments || [];
  document.querySelector('#recording-transcript-state').textContent = segments.length ? 'Provisional transcript' : 'No speech yet';
  for (const segment of segments) {
    const row = document.createElement('p'); row.dataset.startMs = String(segment.start_ms);
    const stamp = document.createElement('time'); stamp.textContent = time(segment.start_ms / 1000);
    const words = document.createElement('span'); words.textContent = segment.text;
    row.append(stamp, words); container.append(row);
  }
  const latest = segments.at(-1)?.text || 'No speech yet';
  document.querySelector('#recording-live-peek-text').textContent = latest;
  const homeLines = document.querySelector('#live-recording-preview-lines'); homeLines.replaceChildren();
  const homeStatus = document.querySelector('#live-recording-preview-status');
  homeStatus.hidden = segments.length > 0;
  homeStatus.textContent = 'No speech yet';
  for (const segment of segments.slice(-2)) {
    const line = document.createElement('p'); line.textContent = segment.text;
    homeLines.append(line);
  }
}
async function updateRecordingPreview(current) {
  if (current.previewPending || Date.now() < (current.previewRetryAfter || 0) || capture !== current || current.sequence < 3) return;
  if (current.sequence - current.lastPreviewSequence < 2 && current.lastPreviewSequence >= 0) return;
  current.previewPending = true;
  document.querySelector('#recording-transcript-state').textContent = 'Transcribing on this device…';
  if (!document.querySelector('#live-recording-preview-lines').childElementCount)
    document.querySelector('#live-recording-preview-status').textContent = 'Transcribing on this device…';
  try {
    const result = await json(await fetch(`${API}/recordings/${encodeURIComponent(current.session.id)}/preview`, {method:'POST'}));
    if (capture === current) {current.lastPreviewSequence = result.through_sequence; renderRecordingPreview(result);}
  } catch (error) {
    if (capture === current) {
      current.previewRetryAfter = Date.now() + 8000;
      const message = error.message === 'Local live transcription model unavailable' ||
        error.message === 'Local live transcription worker unavailable' ?
        'Install the local live speech models to use Live transcript.' : 'Live transcript unavailable. Retrying.';
      document.querySelector('#recording-transcript-state').textContent = message;
      document.querySelector('#recording-live-peek-text').textContent = message;
      if (!document.querySelector('#live-recording-preview-lines').childElementCount)
        document.querySelector('#live-recording-preview-status').textContent = message;
    }
  } finally {
    current.previewPending = false;
    if (capture === current && Date.now() >= (current.previewRetryAfter || 0) && current.sequence - current.lastPreviewSequence >= 2)
      updateRecordingPreview(current);
  }
}
async function loadRecoveries() {
  const sessions = await json(await fetch(`${API}/recordings`));
  const container = document.querySelector('#recover-recordings'); container.replaceChildren();
  for (const session of sessions) {
    if (capture?.session.id === session.id) continue;
    const row = document.createElement('div'); row.className = 'recovery-row';
    const title = document.createElement('strong'); title.textContent = session.title;
    const message = document.createElement('span');
    let heartbeat;
    try { heartbeat = JSON.parse(localStorage.getItem(recordingHeartbeatKey(session.id))); } catch {}
    const liveElsewhere = heartbeat && Date.now() - heartbeat.at < 5000;
    message.textContent = liveElsewhere ? (heartbeat.state === 'paused' ? 'Paused in another tab' : 'Live in another tab') : 'Unfinished recording';
    if (liveElsewhere) row.classList.add('remote-live-recording');
    const review = document.createElement('button'); review.type = 'button'; review.textContent = 'Review notes';
    review.addEventListener('click', async () => {
      try {
        history.pushState({}, '', '/recording'); showRecordingWorkspace();
        document.querySelector('#recording-title').value = session.title;
        await refreshRecordingDestination(session);
        await loadNotes('recording', session.id);
        await loadImages('recording', session.id);
        await loadRecordingChat(session.id);
        notesEditors.recording.focus();
      } catch (error) {message.textContent = error.message;}
    });
    const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Save recording';
    button.disabled = session.chunk_count === 0;
    button.addEventListener('click', async () => {
      button.disabled = true; message.textContent = 'Checking and importing saved audio…';
      try {
        if (notesStates.recording?.id === session.id && !(await flushNotes(notesStates.recording)))
          throw new Error('Save your notes before recovery.');
        await recoverNotesDraft(session.id);
        const result = await json(await fetch(`${API}/recordings/${encodeURIComponent(session.id)}/finalize`, {method:'POST'}));
        await loadRecoveries(); history.pushState({}, '', `/conversation/${encodeURIComponent(result.conversation.id)}`);
        await showDetail(result.conversation.id); setStatus('Recovered recording saved.', true);
      } catch (error) {message.textContent = `Recovery failed: ${error.message}`; button.disabled = false;}
    });
    const discard = document.createElement('button'); discard.type = 'button'; discard.textContent = 'Discard';
    discard.setAttribute('aria-label', `Discard unfinished recording ${session.title}`);
    discard.addEventListener('click', async () => {
      if (!window.confirm(`Discard unfinished recording “${session.title}”? Saved audio chunks will be deleted.`)) return;
      try {await json(await fetch(`${API}/recordings/${encodeURIComponent(session.id)}`, {method:'DELETE'}));
        try {localStorage.removeItem(notesDraftKey('recording', session.id));} catch {}
        if (notesStates.recording?.id === session.id) {notesStates.recording = null; notesEditors.recording?.setMarkdown('');}
        await loadRecoveries();}
      catch (error) {message.textContent = `Could not discard recording: ${error.message}`;}
    });
    const actions = document.createElement('div'); actions.className = 'recovery-actions'; actions.append(review, button, discard);
    const summary = document.createElement('div'); summary.className = 'recovery-summary'; summary.append(title, message);
    if (liveElsewhere) {
      try {
        const preview = await json(await fetch(`${API}/recordings/${encodeURIComponent(session.id)}/preview`));
        const latest = preview.segments?.at(-1)?.text;
        if (latest) {
          const excerpt = document.createElement('p'); excerpt.className = 'remote-live-preview'; excerpt.textContent = latest;
          summary.append(excerpt);
        }
      } catch {}
    }
    row.append(summary);
    if (!liveElsewhere) row.append(actions);
    container.append(row);
  }
}
let recordingDestinationSession=null,recordingDestinationSaving=false;
const recordingDestinationForm=document.querySelector('#recording-notebook-form');
const recordingDestinationStatus=document.querySelector('#recording-notebook-status');
async function refreshRecordingDestination(session=null){
  recordingDestinationSession=session;
  recordingDestinationForm.hidden=true;
  recordingDestinationStatus.textContent='';
  try{
    const response=await json(await fetch(`${API}/notebooks`));
    const books=response.notebooks||response;
    const selected=capture?.session.notebook_id??recordingDestinationSession?.notebook_id??pendingNotebookId;
    document.querySelector('#recording-notebook-name').textContent=books.find(b=>b.id===selected)?.name||'No notebook';
    const select=document.querySelector('#recording-notebook-select');select.replaceChildren(new Option('No notebook',''));
    books.forEach(b=>select.add(new Option(b.name,b.id)));select.value=selected||'';
  }catch(error){recordingDestinationStatus.textContent=error.message;}
}
document.querySelector('#recording-notebook-change').addEventListener('click',async()=>{
  await refreshRecordingDestination(recordingDestinationSession);recordingDestinationForm.hidden=false;document.querySelector('#recording-notebook-select').focus();
});
document.querySelector('#recording-notebook-cancel').addEventListener('click',()=>{recordingDestinationForm.hidden=true;document.querySelector('#recording-notebook-change').focus();});
recordingDestinationForm.addEventListener('submit',async event=>{
  event.preventDefault();if(recordingDestinationSaving)return;
  const current=capture,session=current?.session||recordingDestinationSession;
  const notebookId=document.querySelector('#recording-notebook-select').value||null;
  recordingDestinationSaving=true;recordingDestinationForm.querySelectorAll('button,select').forEach(e=>e.disabled=true);captureControls();
  const save=async()=>{
    try{
      if(session){
        await current?.titleUpdate;
        const updated=await json(await fetch(`${API}/recordings/${encodeURIComponent(session.id)}/notebook`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({notebook_id:notebookId})}));
        if(current)current.session=updated;else recordingDestinationSession=updated;
      }else pendingNotebookId=notebookId;
      await refreshRecordingDestination(recordingDestinationSession);recordingDestinationStatus.textContent='Notebook saved.';
    }catch(error){recordingDestinationStatus.textContent=error.message;}
    finally{recordingDestinationSaving=false;recordingDestinationForm.querySelectorAll('button,select').forEach(e=>e.disabled=false);captureControls();}
  };
  const update=save();if(current)current.destinationUpdate=update;await update;
});
const microphonePicker=document.querySelector('#recording-microphone');
const microphoneStatus=document.querySelector('#recording-input-status');
let microphoneSwitching=false;
async function refreshMicrophones(){
  if(!navigator.mediaDevices?.enumerateDevices)return;
  try{
    const devices=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='audioinput'&&d.deviceId&&d.deviceId!=='default');
    const selected=capture?.input?.deviceId??window.StellisRecordingInput.preference();
    microphonePicker.replaceChildren(new Option('System default',''));
    devices.forEach((d,i)=>microphonePicker.add(new Option(d.label||`Microphone ${i+1}`,d.deviceId)));
    if(selected&&!devices.some(d=>d.deviceId===selected))microphonePicker.add(new Option('Saved microphone unavailable',selected));
    microphonePicker.value=selected;
  }catch{microphoneStatus.textContent='Could not list microphones. You can try the system default.';}
}
microphonePicker.addEventListener('change',async()=>{
  const current=capture,id=microphonePicker.value;
  if(!current){window.StellisRecordingInput.remember(id);microphoneStatus.textContent='Microphone selected for the next recording.';return;}
  if(microphoneSwitching||current.stopping)return;
  microphoneSwitching=true;microphonePicker.disabled=true;microphoneStatus.textContent='Switching microphone…';
  try{
    await current.input.switchTo(id);
    if(capture===current&&!current.stopping){microphonePicker.value=current.input.deviceId;document.querySelector('#recording-source-label').textContent=`${current.input.label} (English)`;microphoneStatus.textContent='Microphone changed.';}
  }catch(error){microphonePicker.value=current.input.deviceId;microphoneStatus.textContent=captureErrorMessage(error);}
  finally{microphoneSwitching=false;captureControls();}
});
navigator.mediaDevices?.addEventListener('devicechange',refreshMicrophones);
refreshMicrophones();
function captureErrorMessage(error) {
  if (error?.name === 'NotAllowedError' || error?.name === 'PermissionDeniedError') return 'Microphone access was denied. Allow this site to use the microphone, then try again.';
  if (error?.name === 'OverconstrainedError') return 'The selected microphone is unavailable. Choose another microphone.';
  if (error?.name === 'NotFoundError') return 'No microphone was found. Connect one and try again.';
  if (error?.name === 'NotReadableError') return 'The microphone is busy or disconnected. Check the device and try again.';
  return error?.message || 'Recording could not start.';
}
async function saveRecordingTitle(current) {
  const title = document.querySelector('#recording-title').value.trim() || 'New recording';
  if (title === current.session.title) return;
  current.session = await json(await fetch(`${API}/recordings/${encodeURIComponent(current.session.id)}`, {
    method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title}),
  }));
}
async function startCapture() {
  if (capture || captureRequesting) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    recordingState.textContent = 'Recording unavailable';
    recordingDetail.textContent = 'This browser does not provide microphone recording.';
    return;
  }
  const mediaType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']
    .find(candidate => MediaRecorder.isTypeSupported(candidate));
  if (!mediaType) { recordingDetail.textContent = 'This browser has no supported recording format.'; return; }
  recordingState.textContent = 'Requesting microphone access…';
  captureRequesting = true; captureControls();
  let stream, input;
  let waveformContext = null;
  try {waveformContext = new AudioContext(); waveformContext.resume().catch(() => {});} catch {}
  try {
    input = await window.StellisRecordingInput.open(microphonePicker.value);
    stream = input.stream;
    refreshMicrophones();
    document.querySelector('#recording-source-label').textContent = `${input.label} (English)`;
    const recorder = new MediaRecorder(stream, {mimeType:mediaType});
    const session = await json(await fetch(`${API}/recordings`, {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({title:document.querySelector('#recording-title').value.trim() || 'New recording',media_type:mediaType.split(';')[0],
        notebook_id:pendingNotebookId, calendar_provider:pendingCalendarEvent?.provider,
        calendar_event_id:pendingCalendarEvent?.id})}));
    pendingNotebookId = null; pendingCalendarEvent = null;
    capture = {recorder, stream, input, session, sequence:0, pending:Promise.resolve(), uploadError:null,
      startedAt:Date.now(), pausedAt:null, totalPausedMs:0, timer:null, stopping:false,
      previewPending:false, previewRetryAfter:0, lastPreviewSequence:-1};
    document.querySelector('#recording-transcript-preview').replaceChildren();
    document.querySelector('#recording-transcript-state').textContent = 'Listening…';
    document.querySelector('#live-recording-preview-lines').replaceChildren();
    document.querySelector('#live-recording-preview-status').hidden = false;
    document.querySelector('#live-recording-preview-status').textContent = 'Listening for speech';
    document.querySelector('#recording-live-peek-text').textContent = 'Listening for speech';
    recordingImages = []; document.querySelector('#recording-image-list').replaceChildren();
    recorder.addEventListener('dataavailable', event => {
      if (!event.data.size) return;
      const current = capture;
      const sequence = current.sequence++;
      current.pending = current.pending.then(async () => {
        if (current.uploadError) return;
        const response = await fetch(`${API}/recordings/${encodeURIComponent(session.id)}/chunks/${sequence}`,
          {method:'PUT',headers:{'Content-Type':session.media_type},body:event.data});
        await json(response);
        recordingDetail.textContent = 'Saved locally';
        updateRecordingPreview(current);
      }).catch(error => {
        current.uploadError = error;
        recordingState.textContent = 'Saving stopped';
        recordingDetail.textContent = `Local storage failed: ${error.message}. Stop recording, then recover saved chunks.`;
        if (recorder.state !== 'inactive') recorder.stop();
      });
    });
    recorder.addEventListener('stop', () => finishCapture());
    input.onended = () => {
      if (capture?.recorder === recorder && recorder.state !== 'inactive') {
        recordingState.textContent = 'Microphone disconnected';
        recordingDetail.textContent = 'Saving audio captured before the device stopped.';
        fetch(`${API}/recordings/${encodeURIComponent(session.id)}/events`, {method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({kind:'device_ended',media_position_ms:Math.round(captureElapsedMs())})}).catch(()=>{});
        recorder.stop();
      }
    };
    recorder.start(1000);
    refreshRecordingDestination();
    try {capture.stopWaveform = drawRecordingWaveform(capture, waveformContext);} catch {
      waveformContext?.close().catch(() => {}); capture.stopWaveform = () => {};}
    capture.timer = setInterval(captureControls, 250);
    recordingState.textContent = 'Recording'; recordingDetail.textContent = 'Saving locally';
    loadNotes('recording', session.id).catch(error => {notesElements('recording').status.textContent = error.message;});
    loadImages('recording', session.id).catch(error => {recordingDetail.textContent = error.message;});
    loadRecordingChat(session.id).catch(error => {document.querySelector('#recording-chat-status').textContent = error.message;});
    captureControls(); await loadRecoveries();
  } catch (error) {
    input?.close();
    stream?.getTracks().forEach(track => track.stop());
    waveformContext?.close().catch(() => {});
    if (capture?.recorder?.state === 'inactive') capture = null;
    captureControls();
    recordingState.textContent = 'Could not start recording';
    recordingDetail.textContent = captureErrorMessage(error);
    loadRecoveries().catch(() => {});
  } finally {
    captureRequesting = false; captureControls();
  }
}
async function finishCapture() {
  const current = capture;
  if (!current || current.stopping) return;
  current.stopping = true;
  captureFinalizing = true; captureControls();
  clearInterval(current.timer);
  current.stopWaveform?.();
  current.input?.close();
  current.stream.getTracks().forEach(track => track.stop());
  recordingState.textContent = 'Saving recording…';
  await current.pending;
  capture = null; captureControls();
  if (notesStates.recording?.id === current.session.id && !(await flushNotes(notesStates.recording))) {
    recordingState.textContent = 'Recording needs recovery';
    recordingDetail.textContent = 'Save your notes, then open All conversations to save the recording.';
    clearCaptureHeartbeat(current.session.id);
    await loadRecoveries(); captureFinalizing = false; captureControls(); return;
  }
  if (current.uploadError) {
    recordingState.textContent = 'Recording interrupted';
    recordingDetail.textContent = 'Some audio could not be stored. Open All conversations to save the audio that was captured.';
    clearCaptureHeartbeat(current.session.id);
    await loadRecoveries(); captureFinalizing = false; captureControls(); return;
  }
  try {
    await current.titleUpdate;
    await current.destinationUpdate;
    await saveRecordingTitle(current);
    const result = await json(await fetch(`${API}/recordings/${encodeURIComponent(current.session.id)}/finalize`, {method:'POST'}));
    recordingState.textContent = 'Recording saved'; recordingDetail.textContent = '';
    await loadRecoveries();
    history.pushState({}, '', `/conversation/${encodeURIComponent(result.conversation.id)}`);
    await showDetail(result.conversation.id); setStatus('Recording saved.', true);
  } catch (error) {
    recordingState.textContent = 'Recording needs recovery'; recordingDetail.textContent = error.message;
    clearCaptureHeartbeat(current.session.id);
    await loadRecoveries();
  } finally {
    captureFinalizing = false;
    clearCaptureHeartbeat(current.session.id);
    captureControls();
  }
}
document.querySelector('#record-button').addEventListener('click', () => {
  // The demo can't record (the server refuses demo recordings): the upload offers the example recording.
  if (demoGuest()) { window.StellisHealthCapture.open('upload', {notebookId: null}); return; }
  if (capture) {
    pendingNotebookId = null; pendingCalendarEvent = null;
    history.pushState({}, '', '/recording');
        return;
  }
  if ((pendingTranscriptEdits.size || pendingCleanEdits.size) && !window.confirm('Discard unsaved transcript changes and start recording?')) return;
  pendingTranscriptEdits.clear(); pendingCleanEdits.clear();
  history.pushState({}, '', '/recording');
  showRecordingWorkspace();
  startCapture();
});
document.querySelector('#back-from-recording').addEventListener('click', () => {
  if (captureRequesting || captureFinalizing) return;
  history.pushState({}, '', '/'); showList().catch(error => setStatus(error.message));
});
document.querySelector('#recording-title').addEventListener('change', () => {
  if (!capture) return;
  const current = capture;
  current.titleUpdate = (current.titleUpdate || Promise.resolve()).then(() => saveRecordingTitle(current))
    .catch(error => { recordingDetail.textContent = `Title could not be saved: ${error.message}`; });
  captureControls();
});
document.querySelector('#start-recording').addEventListener('click', startCapture);
document.querySelector('#capture-header-pause').addEventListener('click', () => document.querySelector('#pause-recording').click());
document.querySelector('#capture-header-stop').addEventListener('click', () => document.querySelector('#stop-recording').click());
document.querySelector('#stop-recording').addEventListener('click', () => {
  if (capture?.recorder.state !== 'inactive') capture.recorder.stop();
});
document.querySelector('#pause-recording').addEventListener('click', async () => {
  if (!capture) return;
  const current = capture;
  const kind = current.recorder.state === 'paused' ? 'resume' : 'pause';
  const position = Math.round(captureElapsedMs());
  if (kind === 'pause') {current.recorder.pause(); current.pausedAt = Date.now(); recordingState.textContent = 'Paused';}
  else {current.totalPausedMs += Date.now() - current.pausedAt; current.pausedAt = null; current.recorder.resume(); recordingState.textContent = 'Recording';}
  captureControls();
  try {await json(await fetch(`${API}/recordings/${encodeURIComponent(current.session.id)}/events`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,media_position_ms:position})}));}
  catch (error) {recordingDetail.textContent = `Pause status could not be saved: ${error.message}`;}
});
document.querySelector('#import-button').addEventListener('click', () => {
  if (demoGuest()) { window.StellisHealthCapture.open('upload', {notebookId: null}); return; }
  importStatus.textContent = '';
  importDialog.showModal();
});
document.querySelector('#recording-import').addEventListener('click', () => document.querySelector('#import-button').click());
document.querySelector('#import-close').addEventListener('click', () => importDialog.close());
importDialog.addEventListener('close', () => { pendingNotebookId = null; });
importDialog.addEventListener('cancel', event => {
  if (fileInput.disabled) event.preventDefault();
});
fileInput.addEventListener('change', () => importFile(fileInput.files[0]));
dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); fileInput.click(); }
});
for (const name of ['dragenter', 'dragover']) dropZone.addEventListener(name, event => {
  event.preventDefault(); dropZone.classList.add('drag-over');
});
for (const name of ['dragleave', 'drop']) dropZone.addEventListener(name, event => {
  event.preventDefault(); dropZone.classList.remove('drag-over');
});
dropZone.addEventListener('drop', event => importFile(event.dataTransfer.files[0]));
function syncPlayerControls() {
  const duration = Number.isFinite(player.duration) ? player.duration : Number(seekSlider.max) || 0;
  const position = Number.isFinite(player.currentTime) ? player.currentTime : 0;
  seekSlider.max = String(Math.max(duration, 1));
  seekSlider.value = String(Math.min(position, duration));
  seekSlider.setAttribute('aria-valuetext', `${time(position)} of ${time(duration)}`);
  document.querySelector('.seek-timeline').style.setProperty('--seek-progress', `${duration ? Math.min(100, position / duration * 100) : 0}%`);
  document.querySelector('#current-time').textContent = time(position);
  document.querySelector('#audio-duration').textContent = time(duration);
  playPauseButton.textContent = player.paused ? '▶' : '❚❚';
  playPauseButton.setAttribute('aria-label', player.paused ? 'Play' : 'Pause');
}
function renderSeekSegments(segments) {
  const track = document.querySelector('#seek-segments'); track.replaceChildren();
  const duration = Number(seekSlider.max) * 1000;
  if (!duration) return;
  const intervals = [];
  for (const item of segments) {
    if (!Number.isFinite(item.start_ms) || !Number.isFinite(item.end_ms)) continue;
    const last = intervals.at(-1);
    if (last && item.start_ms - last[1] <= 1500) last[1] = Math.max(last[1], item.end_ms);
    else intervals.push([item.start_ms, item.end_ms]);
  }
  const fragment = document.createDocumentFragment();
  for (const [start, end] of intervals) {
    const mark = document.createElement('span');
    mark.style.left = `${Math.max(0, Math.min(100, start / duration * 100))}%`;
    mark.style.width = `${Math.max(.06, Math.min(100, end / duration * 100) - Math.max(0, start / duration * 100))}%`;
    fragment.append(mark);
  }
  track.append(fragment);
}
document.querySelector('#back-five').addEventListener('click', () => { player.currentTime = Math.max(0, player.currentTime - 5); syncPlayerControls(); });
document.querySelector('#forward-five').addEventListener('click', () => { player.currentTime = Math.min(player.duration || Number(seekSlider.max), player.currentTime + 5); syncPlayerControls(); });
playPauseButton.addEventListener('click', () => {
  if (player.paused) player.play().catch(() => { processingStatus.textContent = 'Audio could not play.'; });
  else player.pause();
});
function showTranscriptAtPlaybackPosition() {
  if (document.querySelector('#transcript-card').hidden || transcriptEditMode || !transcriptSegments.length) return;
  const ms = player.currentTime * 1000;
  const segment = transcriptSegments.find(item => item.start_ms <= ms && ms < item.end_ms) ||
    transcriptSegments.find(item => item.start_ms > ms) || transcriptSegments.at(-1);
  if (!segment) return;
  const word = segment.words?.find(item => item.start_ms <= ms && ms < item.end_ms) ||
    segment.words?.find(item => item.start_ms > ms);
  const row = transcriptView.querySelector(`[data-segment-id="${CSS.escape(segment.id)}"]`);
  (row?.querySelector(word ? `[data-word-index="${segment.words.indexOf(word)}"]` : '.segment-text') || row)
    ?.scrollIntoView({block: 'center'});
}
seekSlider.addEventListener('input', () => {
  player.currentTime = Number(seekSlider.value);
  syncPlayerControls();
  showTranscriptAtPlaybackPosition();
});
seekSlider.addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  event.preventDefault();
  player.currentTime = Math.max(0, Math.min(player.duration || Number(seekSlider.max), player.currentTime + (event.key === 'ArrowRight' ? 10 : -10)));
  syncPlayerControls();
  showTranscriptAtPlaybackPosition();
});
const speedMenu = document.querySelector('#playback-speed-menu');
async function startSilenceScan(id) {
  if (!skipSilenceCheckbox.checked || id !== activeConversationId) return;
  skipSilenceStatus.textContent = 'Measuring silence…';
  const job = await json(await fetch(`${API}/conversations/${encodeURIComponent(id)}/silence-scan`, {method:'POST'}));
  async function accept(current) {
    if (!skipSilenceCheckbox.checked || id !== activeConversationId) return;
    if (current.state === 'succeeded') {
      silenceSpans = (current.metrics?.spans || []).filter(span =>
        Number.isFinite(span.start_ms) && Number.isFinite(span.end_ms) && span.end_ms > span.start_ms);
      skipSilenceStatus.textContent = silenceSpans.length ? `${silenceSpans.length} ${silenceSpans.length === 1 ? 'gap' : 'gaps'}` : 'No long pauses';
    } else if (current.state === 'failed') {
      silenceSpans = [];
      skipSilenceStatus.textContent = current.error || 'Silence scan failed.';
    } else {
      silenceScanTimer = setTimeout(async () => {
        try { await accept(await json(await fetch(`${API}/jobs/${encodeURIComponent(current.id)}`))); }
        catch (error) { skipSilenceStatus.textContent = error.message; }
      }, 1200);
    }
  }
  await accept(job);
}
skipSilenceCheckbox.addEventListener('change', () => {
  clearTimeout(silenceScanTimer);
  silenceSpans = [];
  skipSilenceStatus.textContent = '';
  if (skipSilenceCheckbox.checked && activeConversationId)
    startSilenceScan(activeConversationId).catch(error => { skipSilenceStatus.textContent = error.message; });
});
for (const button of speedMenu.querySelectorAll('[data-speed]')) button.addEventListener('click', () => {
  player.playbackRate = Number(button.dataset.speed);
  speedMenu.open = false;
  document.querySelector('#speed-button').focus();
});
function syncSpeed() {
  document.querySelector('#speed-button').textContent = `${player.playbackRate}x`;
  for (const button of speedMenu.querySelectorAll('[data-speed]')) button.setAttribute('aria-pressed', String(Number(button.dataset.speed) === player.playbackRate));
}
player.addEventListener('ratechange', syncSpeed);
player.addEventListener('seeked', () => { manualSeekUntil = performance.now() + 450; });
for (const name of ['loadedmetadata', 'timeupdate', 'durationchange', 'play', 'pause', 'ended']) player.addEventListener(name, syncPlayerControls);
syncSpeed();
player.addEventListener('timeupdate', () => {
  if (skipSilenceCheckbox.checked && !player.paused && performance.now() >= manualSeekUntil) {
    const position = player.currentTime * 1000;
    const gap = silenceSpans.find(span => span.start_ms <= position && position < span.end_ms);
    if (gap) { player.currentTime = gap.end_ms / 1000; return; }
  }
  const ms = player.currentTime * 1000;
  const active = transcriptSegments.find(segment => segment.start_ms <= ms && ms < segment.end_ms);
  for (const row of transcriptView.querySelectorAll('.segment.active')) row.classList.remove('active');
  if (active) transcriptView.querySelector(`[data-segment-id="${active.id}"]`)?.classList.add('active');
  activeWordElement?.classList.remove('active-word'); activeWordElement = null;
  const activeWord = active?.words?.find(word => word.start_ms <= ms && ms < word.end_ms);
  if (activeWord) {
    const index = active.words.indexOf(activeWord);
    activeWordElement = transcriptView.querySelector(`[data-segment-id="${active.id}"] [data-word-index="${index}"]`);
    activeWordElement?.classList.add('active-word');
    if (activeWordElement && !document.querySelector('#transcript-card').hidden && !player.paused &&
        Date.now() - lastIntentionalScroll > 2500 && Date.now() - lastAutoFollow > 700) {
      const rect = activeWordElement.getBoundingClientRect();
      if (rect.top < 90 || rect.bottom > window.innerHeight - 130) {
        lastAutoFollow = Date.now(); activeWordElement.scrollIntoView({block:'center',behavior:'smooth'});
      }
    }
  }
  const edited = document.querySelector('#edited-transcript');
  for (const row of edited.querySelectorAll('.segment.active')) row.classList.remove('active');
  if (active) edited.querySelector(`[data-segment-id="${active.id}"]`)?.classList.add('active');
});
processButton.addEventListener('click', () => beginProcessing(activeConversationId));
alignWordsButton.addEventListener('click', async () => {
  if (!activeConversationId) return;
  alignWordsButton.disabled = true;
  processingStatus.textContent = 'Starting on-device word timing…';
  try {
    const job = await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/word-alignment`, {method:'POST'}));
    watchJob(job);
  } catch (error) { processingStatus.textContent = error.message; alignWordsButton.disabled = false; }
});
rematchButton.addEventListener('click', async () => {
  if (!activeConversationId) return;
  rematchButton.disabled = true;
  processingStatus.textContent = 'Starting on-device speaker rematch…';
  try {
    const job = await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/speaker-rematch`, {method:'POST'}));
    watchJob(job);
  } catch (error) {
    processingStatus.textContent = error.message;
    rematchButton.disabled = false;
  }
});
transcriptView.addEventListener('mouseup', () => setTimeout(inspectSelection, 0));
transcriptView.addEventListener('keyup', event => { if (event.key === 'Shift' || event.key.startsWith('Arrow')) inspectSelection(); });
document.querySelector('#show-highlights').addEventListener('click', () => {
  highlightsOnly = !highlightsOnly;
  if (activeConversationId && currentTranscript) showTranscript(activeConversationId).catch(error => processingStatus.textContent = error.message);
});
document.querySelector('#show-rematch-changes').addEventListener('click', () => {
  rematchChangesOnly = !rematchChangesOnly;
  if (activeConversationId && currentTranscript) showTranscript(activeConversationId).catch(error => processingStatus.textContent = error.message);
});
window.addEventListener('popstate', () => route().catch(error => setStatus(error.message)));
// A notes toolbar menu (Callout, Edit table) near the right edge opens leftward, so it stays on screen.
document.addEventListener('toggle', event => {
  const menu = event.target;
  if (!menu.matches?.('.notes-table-actions') || !menu.open) return;
  const list = menu.querySelector(':scope > div');
  list.classList.remove('opens-left');
  if (list.getBoundingClientRect().right > document.documentElement.clientWidth - 8) list.classList.add('opens-left');
}, true);
document.addEventListener('click', event => {
  const link = event.target.closest('a[href="/"], a[href="/recording"], a[href^="/conversation/"], a[href^="/person/"], a[href="/vocabulary"], a[href^="/vocabulary/"], a[href="/settings"], a[href^="/notebook/"], a[href="/todos"], a[href="/timeline"], a[href="/records"], a[href="/summary"], a[href="/trends"], a[href^="/chat"], a[href^="/page/"], a[href^="/document/"]');
  if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  // Pages change in place, so the next one is drawn without reloading the app and flashing a blank page
  // (owner, 2026-10-29); a recording also keeps running across them. A dialog the link sat in closes.
  event.preventDefault(); link.closest('dialog[open]')?.close(); history.pushState({}, '', link.getAttribute('href'));
  route().catch(error => setStatus(error.message));
});
document.querySelector('#library-search-form').addEventListener('submit', submitLibrarySearch);
document.querySelector('#library-search-input').addEventListener('input', () => {
  librarySearchResults = []; librarySearchTotal = 0;
  document.querySelector('#library-search-controls').hidden = true;
  document.querySelector('#library-search-results').replaceChildren();
  document.querySelector('#library-search-status').textContent = '';
});
document.querySelector('#library-search-kind').addEventListener('change', renderLibrarySearch);
document.querySelector('#auto-categories').addEventListener('change', async event => {
  const checkbox = event.target;
  checkbox.disabled = true;
  try {
    const setting = await json(await fetch(`${API}/categories/automation`, {method:'PUT',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:checkbox.checked})}));
    setCategoryStatus(checkbox.checked && !setting.configured);
  } catch (error) {
    checkbox.checked = !checkbox.checked;
    document.querySelector('#category-status').textContent = error.message;
  }
  checkbox.disabled = false;
});
document.querySelector('#retry-categorization').addEventListener('click', async () => {
  try {
    await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/categorization`, {method:'POST'}));
    await refreshCategorizationStatus();
  } catch (error) { document.querySelector('#categorization-status').textContent = error.message; }
});
document.querySelector('#create-category-form').addEventListener('submit', async event => {
  event.preventDefault();
  const input = document.querySelector('#new-category-name');
  try { await json(await fetch(`${API}/categories`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:input.value})}));
    input.value = ''; await showList(); }
  catch (error) { document.querySelector('#category-status').textContent = error.message; }
});
const tagsDialog = document.querySelector('#tags-dialog');
const peopleDialog = document.querySelector('#people-dialog');
function openTags() { tagsDialog.showModal(); document.querySelector('#new-category-name').focus(); }
document.querySelector('#open-tags').addEventListener('click', openTags);
document.querySelector('#new-tag').addEventListener('click', openTags);
document.querySelector('#close-tags').addEventListener('click', () => tagsDialog.close());
document.querySelector('#open-people').addEventListener('click', async () => {
  peopleDialog.showModal();
  try { await loadPeoplePanel(); } catch (error) { setStatus(error.message); }
});
document.querySelector('#close-people').addEventListener('click', () => peopleDialog.close());
document.querySelector('#show-trash').addEventListener('click', () => showTrash().catch(error => document.querySelector('#trash-status').textContent = error.message));
document.querySelector('#back-from-trash').addEventListener('click', () => showList().catch(error => setStatus(error.message)));
document.querySelector('#person-rename-form').addEventListener('submit', async event => {
  event.preventDefault();
  try { await json(await fetch(`${API}/persons/${encodeURIComponent(activePersonId)}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.querySelector('#person-rename-input').value})})); await showPerson(activePersonId); }
  catch (error) { document.querySelector('#person-status').textContent = error.message; }
});
document.querySelector('#person-merge-button').addEventListener('click', async () => {
  const source = document.querySelector('#person-merge-source').value;
  if (!source || !window.confirm('Merge this person into the open profile? Confirmed conversation links will move here.')) return;
  try { await json(await fetch(`${API}/persons/${encodeURIComponent(activePersonId)}/merge`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_id:source})})); await showPerson(activePersonId); }
  catch (error) { document.querySelector('#person-status').textContent = error.message; }
});
document.querySelector('#person-delete').addEventListener('click', async () => {
  if (!activePersonId || !window.confirm('Delete this empty person profile?')) return;
  try { await json(await fetch(`${API}/persons/${encodeURIComponent(activePersonId)}`, {method:'DELETE'}));
    history.pushState({}, '', '/'); await showList(); }
  catch (error) { document.querySelector('#person-status').textContent = error.message; }
});
const renameConversationDialog = document.querySelector('#rename-conversation-dialog');
const conversationCategoriesDialog = document.querySelector('#conversation-categories-dialog');
document.querySelector('#open-conversation-categories').addEventListener('click', async () => {
  if (!activeConversationId) return;
  const id = activeConversationId;
  conversationMore.open = false;
  try {
    const item = await json(await fetch(`${API}/conversations/${encodeURIComponent(id)}`));
    if (activeConversationId !== id) return;
    await renderConversationCategories(item.category_ids);
    conversationCategoriesDialog.showModal();
    document.querySelector('#close-conversation-categories').focus();
  } catch (error) { setStatus(error.message); }
});
document.querySelector('#close-conversation-categories').addEventListener('click', () => conversationCategoriesDialog.close());
conversationCategoriesDialog.addEventListener('close', () => {
  if (activeConversationId) conversationMore.querySelector('summary').focus();
});
const renameConversationInput = document.querySelector('#rename-conversation-input');
const renameConversationStatus = document.querySelector('#rename-conversation-status');
const renameConversationSave = document.querySelector('#rename-conversation-save');
document.querySelector('#rename-conversation').addEventListener('click', () => {
  if (!activeConversationId) return;
  renameConversationInput.value = document.querySelector('#page-title').textContent;
  renameConversationStatus.textContent = '';
  renameConversationDialog.showModal();
  renameConversationInput.focus();
  renameConversationInput.select();
});
document.querySelector('#rename-conversation-cancel').addEventListener('click', () => renameConversationDialog.close());
renameConversationDialog.addEventListener('close', () => conversationMore.querySelector('summary').focus());
document.querySelector('#rename-conversation-form').addEventListener('submit', async event => {
  event.preventDefault();
  const title = renameConversationInput.value.trim();
  if (!title) { renameConversationStatus.textContent = 'Enter a title.'; renameConversationInput.focus(); return; }
  if (title === document.querySelector('#page-title').textContent) { renameConversationDialog.close(); return; }
  renameConversationSave.disabled = true;
  renameConversationStatus.textContent = '';
  try {
    const updated = await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title}),
    }));
    document.querySelector('#page-title').textContent = updated.title;
    renameConversationDialog.close();
  } catch (error) { renameConversationStatus.textContent = error.message; }
  finally { renameConversationSave.disabled = false; }
});
document.querySelector('#trash-conversation').addEventListener('click', async () => {
  if (!activeConversationId || !window.confirm('Delete this conversation? You can restore it from Recently Deleted for 30 days.')) return;
  try {
    await json(await fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}/trash`, {method:'POST'}));
    player.pause(); player.removeAttribute('src'); player.load();
    history.pushState({}, '', '/');
    await showList();
  } catch (error) { setStatus(error.message); }
});
document.querySelector('#copy-transcript').addEventListener('click', async () => {
  if (!currentTranscript?.segments?.length) return;
  if (pendingTranscriptEdits.size) { setStatus('Save transcript edits first.'); return; }
  const paragraphs = groupTranscriptSegments(currentTranscript.segments).map(group =>
    `[${time(group[0].start_ms / 1000)}] ${group[0].speaker_display_name}: ${group.map(segment => segment.text.trim()).join(' ')}`);
  try {
    await navigator.clipboard.writeText(paragraphs.join('\n\n'));
    setStatus('Transcript copied.', true);
  } catch (error) { console.warn('Transcript copy failed', error); setStatus('Could not copy transcript.'); }
});
document.querySelector('#transcript-search-input').addEventListener('input', () => {
  updateTranscriptSearch();
});
document.querySelector('#transcript-search-input').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); moveTranscriptSearch(event.shiftKey ? -1 : 1); }
});
document.querySelector('#transcript-search-speaker').addEventListener('change', () => {
  updateTranscriptSearch();
});
document.querySelector('#transcript-search-previous').addEventListener('click', () => moveTranscriptSearch(-1));
document.querySelector('#transcript-search-next').addEventListener('click', () => moveTranscriptSearch(1));
document.querySelector('#transcript-search-clear').addEventListener('click', () => {
  document.querySelector('#transcript-search-input').value = '';
  document.querySelector('#transcript-search-speaker').value = '';
  transcriptSearchIndex = -1; refreshTranscriptSearch();
});
document.querySelector('#vocabulary-form').addEventListener('submit', async event => {
  event.preventDefault();
  const input = document.querySelector('#vocabulary-input');
  const terms = input.value.split(/[,\n]/).map(value => value.trim()).filter(Boolean);
  if (!terms.length) { vocabularyStatus.textContent = 'Enter a term.'; return; }
  if (terms.length > 200 || terms.some(term => term.length > 100)) {
    vocabularyStatus.textContent = 'Add up to 200 terms, each at most 100 characters.'; return;
  }
  const button = document.querySelector('#vocabulary-form button[type=submit]'); button.disabled = true;
  try {
    const result = await json(await fetch(`${API}/vocabulary/batch`, {method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({terms,kind:vocabularyKind})}));
    input.value = '';
    vocabularyStatus.textContent = `${result.added.length} added${result.skipped.length ? `, ${result.skipped.length} already saved` : ''}.`;
    await loadVocabulary();
  } catch (error) { vocabularyStatus.textContent = error.message; }
  finally { button.disabled = false; }
});
document.querySelector('#vocabulary-select-all').addEventListener('change', event => {
  vocabularySelected.clear();
  for (const checkbox of document.querySelectorAll('#vocabulary-list .vocab-row input[type=checkbox]')) {
    checkbox.checked = event.target.checked;
    if (checkbox.checked) vocabularySelected.add(checkbox.value);
  }
  syncVocabularySelection();
});
document.querySelector('#vocabulary-move-selected').addEventListener('click', async () => {
  if (!vocabularySelected.size) return;
  const count = vocabularySelected.size;
  const target = vocabularyKind === 'name' ? 'other' : 'name';
  try {
    await json(await fetch(`${API}/vocabulary/move`, {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ids:[...vocabularySelected],kind:target})}));
    vocabularyStatus.textContent = `${count} moved.`; await loadVocabulary();
  } catch (error) { vocabularyStatus.textContent = error.message; }
});
const vocabularyDeleteDialog = document.querySelector('#vocabulary-delete-dialog');
document.querySelector('#vocabulary-delete-selected').addEventListener('click', () => {
  if (!vocabularySelected.size) return;
  document.querySelector('#vocabulary-delete-message').textContent =
    `Delete ${vocabularySelected.size} selected ${vocabularySelected.size === 1 ? 'term' : 'terms'}?`;
  vocabularyDeleteDialog.showModal();
  document.querySelector('#vocabulary-delete-cancel').focus();
});
document.querySelector('#vocabulary-delete-cancel').addEventListener('click', () => vocabularyDeleteDialog.close());
vocabularyDeleteDialog.addEventListener('close', () => {
  const button = document.querySelector('#vocabulary-delete-selected');
  if (!document.querySelector('#vocabulary-panel').hidden && !button.disabled) button.focus();
});
document.querySelector('#vocabulary-delete-confirm').addEventListener('click', async () => {
  const ids = [...vocabularySelected];
  const button = document.querySelector('#vocabulary-delete-confirm'); button.disabled = true;
  try {
    await json(await fetch(`${API}/vocabulary/delete`, {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ids})}));
    vocabularyDeleteDialog.close(); vocabularyStatus.textContent = `${ids.length} deleted.`;
    await loadVocabulary(); document.querySelector('#vocabulary-input').focus();
  } catch (error) { vocabularyStatus.textContent = error.message; vocabularyDeleteDialog.close(); }
  finally { button.disabled = false; }
});
loadRecoveries().catch(error => setStatus(error.message));
window.addEventListener('storage', event => {
  if (!event.key?.startsWith('stellis-recording-live-')) return;
  let before; let after;
  try { before = JSON.parse(event.oldValue); after = JSON.parse(event.newValue); } catch {}
  if (before?.state !== after?.state || !before || !after)
    loadRecoveries().catch(error => setStatus(error.message));
});
setInterval(() => {
  if (document.querySelector('.remote-live-recording')) loadRecoveries().catch(error => setStatus(error.message));
}, 5000);

// Home notebooks, the notebook page, and account settings.
let libraryNotebooks = [];
let notebookPalette = [];
let notebookPresets = [];
let pendingNotebookId = null;
// A calendar meeting whose notes the next recording starts from.
let pendingCalendarEvent = null;
let addingToNotebook = null;
let activeNotebookId = null;
let notebookTodos = null; let notebookItems = []; let notebookFilter = 'all';
let calendarOutcome = new URLSearchParams(location.search).get('calendar');
const connectionError = new URLSearchParams(location.search).get('connection_error');
if (calendarOutcome) history.replaceState(history.state, '', location.pathname);
function calendarOutcomeText() { return calendarOutcome === 'connected' ? 'Connected' : (connectionError || 'Connection expired. Connect again.'); }
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon'); svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use'); use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}
function capturedAt(item) { return item.recorded_at || item.created_at; }
function shortDate(value) {
  const date = new Date(value);
  const options = {month:'short', day:'numeric'};
  if (date.getFullYear() !== new Date().getFullYear()) options.year = 'numeric';
  return date.toLocaleDateString(undefined, options);
}
function paintNotebook(element, notebook) {
  element.style.setProperty('--notebook', window.StellisNotebookItems.fillFor(notebook.color));
  element.style.setProperty('--notebook-ink', window.StellisNotebookItems.inkFor(notebook.color));
}
function hideAppPages() {
  const rail = document.querySelector('#home-rail');
  rail.querySelector('#settings-link').removeAttribute('aria-current');
  rail.querySelectorAll('.rail-pin').forEach(link => {if(link.id === 'rail-meetings')link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
  if (rail.parentElement !== listView) {
    listView.prepend(rail);
  }
  document.querySelector('#page-view').hidden = true;
  document.querySelector('#document-view').hidden = true;
  document.querySelector('#settings-view').hidden = true;
  document.querySelector('#notebook-view').hidden = true;
  document.querySelector('#todos-view').hidden = true;
  document.querySelector('#timeline-view').hidden = true;
  document.querySelector('#records-view').hidden = true;
  document.querySelector('#summary-view').hidden = true;
  document.querySelector('#trends-view').hidden = true;
  document.querySelector('#agent-chat-view').hidden = true;
  notebookTodos?.destroy?.(); notebookTodos = null;
  document.body.classList.remove('settings-page', 'notebook-page', 'todos-page', 'timeline-page', 'records-page', 'summary-page', 'trends-page', 'note-page', 'document-page', 'agent-chat-page');
  activeNotebookId = null;
}
async function leaveForAppPage(bodyClass, title) {
  await flushNotes(notesStates.conversation);
  await leaveNote();
  await flushEnhanced();
  document.body.classList.remove('profile-page', 'conversation-page', 'recording-page');
  recordingPanel.hidden = true;
  if (conversationCategoriesDialog.open) conversationCategoriesDialog.close();
  conversationMore.open = false; conversationMore.hidden = true;
  clearTimeout(jobTimer); clearTimeout(aiRunTimer); clearTimeout(enhancedPollTimer);
  clearTimeout(recordingChatTimer); clearTimeout(cleanRunTimer); clearTimeout(silenceScanTimer);
  player.pause(); player.removeAttribute('src'); player.load();
  activeConversationId = null; activePersonId = null;
  listView.hidden = true; detailView.hidden = true;
  for (const selector of ['#trash-view', '#person-view', '#vocabulary-panel']) document.querySelector(selector).hidden = true;
  hideAppPages();
  document.body.classList.add(bodyClass);
  document.querySelector('main').prepend(document.querySelector('#home-rail'));
  document.querySelector('#page-title').textContent = title;
  if (['settings-page', 'todos-page', 'note-page'].includes(bodyClass)) {
    await reloadLibrary();
    for (const link of document.querySelectorAll('#home-rail .rail-pin')) link.removeAttribute('aria-current');
    if (bodyClass === 'settings-page') document.querySelector('#settings-link').setAttribute('aria-current', 'page');
  }
}
function meetingLink(item, className) {
  const link = document.createElement('a'); link.className = className;
  link.href = `/conversation/${encodeURIComponent(item.id)}`;
  const title = document.createElement('span'); title.textContent = item.title;
  const date = document.createElement('time'); date.dateTime = capturedAt(item); date.textContent = shortDate(capturedAt(item));
  link.append(title, date);
  return link;
}
function notebookCard(notebook) {
  const card = document.createElement('article'); card.className = 'notebook-card'; paintNotebook(card, notebook);
  const head = document.createElement('div'); head.className = 'notebook-card-head';
  const name = document.createElement('h3'); const link = document.createElement('a');
  link.href = `/notebook/${encodeURIComponent(notebook.id)}`; link.textContent = notebook.name; name.append(link);
  const add = document.createElement('button'); add.type = 'button'; add.className = 'notebook-plus';
  add.setAttribute('aria-label', `Add to ${notebook.name}`); add.title = 'Add a note or conversation'; add.append(icon('plus'));
  add.addEventListener('click', () => openNotebookAdd(notebook));
  head.append(icon('notebook'), name, add);
  card.append(head);
  // Newest first across records, meetings and notes; meetings and notes drag out to another notebook card, a tag, or No notebook.
  const entries = [
    ...libraryDocuments.filter(item => inNotebook(item, notebook.id)).map(item => ({kind: 'document', item, at: item.record_date || item.created_at})),
    ...notebook.conversations.map(item => ({kind: 'conversation', item, at: capturedAt(item)})),
    ...(notebook.pages || []).map(item => ({kind: 'page', item, at: item.updated_at}))]
    .sort((x, y) => new Date(y.at) - new Date(x.at));
  const [latest, ...older] = entries;
  // An empty notebook shows only its name and its add button: no placeholder line.
  if (!latest) { card.classList.add('is-empty'); return card; }
  card.append(cardItem(latest, 'notebook-latest'));
  if (notebook.progress.length) {
    const progress = document.createElement('ul'); progress.className = 'notebook-progress'; progress.setAttribute('aria-label', 'Progress');
    for (const point of notebook.progress) {
      const item = document.createElement('li'); const text = document.createElement('span'); text.textContent = point;
      item.append(icon('circle-check'), text); progress.append(item);
    }
    card.append(progress);
  }
  if (older.length) {
    const history = document.createElement('ul'); history.className = 'notebook-history';
    for (const entry of older.slice(0, 3)) { const row = document.createElement('li'); row.append(cardItem(entry, 'notebook-meeting')); history.append(row); }
    card.append(history);
  }
  return card;
}
function cardItem({kind, item, at}, className) {
  // A record, a meeting and a note each link to their own page.
  const row = document.createElement(kind === 'page' ? 'button' : 'a'); row.className = `${className} card-item`;
  if (kind === 'conversation') row.href = `/conversation/${encodeURIComponent(item.id)}`;
  else if (kind === 'document') row.href = `/document/${encodeURIComponent(item.id)}`;
  else row.type = 'button';
  const title = document.createElement('span'); title.textContent = item.title;
  const date = document.createElement('time'); date.dateTime = at; date.textContent = window.StellisListDate.formatListDate(at);
  row.append(icon(kind === 'conversation' ? 'microphone' : 'file-text'), title, date);
  if (kind === 'page') row.addEventListener('click', () => openNote(item.id));
  if (kind === 'document') return row;
  row.draggable = true;
  row.addEventListener('dragstart', event => {
    event.dataTransfer.setData(kind === 'conversation' ? CONVERSATION_DRAG : PAGE_DRAG, item.id);
    event.dataTransfer.effectAllowed = 'copyMove'; row.classList.add('dragging');
  });
  row.addEventListener('dragend', () => row.classList.remove('dragging'));
  return row;
}
function renderRailTags() {
  // The sidebar and the Recent conversations widget filter by the same tag pills.
  const untagged = libraryConversations.filter(item => !item.category_ids.length).length;
  const options = [...libraryCategories.filter(item => item.conversation_count).map(item => [item.id, item.name]),
    ...(untagged ? [['untagged', 'Untagged']] : [])];
  const build = (pillClass, clearClass) => {
    const pills = options.map(([id, name]) => {
      const pill = document.createElement('button'); pill.type = 'button'; pill.className = pillClass;
      pill.textContent = name; pill.setAttribute('aria-pressed', String(railTags.has(id)));
      pill.addEventListener('click', () => {
        if (railTags.has(id)) railTags.delete(id); else railTags.add(id);
        renderRailTags(); renderConversationList();
      });
      return pill;
    });
    if (railTags.size) {
      const clear = document.createElement('button'); clear.type = 'button'; clear.className = clearClass;
      clear.textContent = 'Clear';
      clear.addEventListener('click', () => { railTags.clear(); renderRailTags(); renderConversationList(); });
      pills.push(clear);
    }
    return pills;
  };
  document.querySelector('#rail-tags').replaceChildren(...build('rail-filter-pill', 'rail-filter-clear'));
  document.querySelector('#recent-tags').replaceChildren(...build('filter-pill', 'filter-clear'));
}
// Recent conversations: the newest meetings under the same tag filter as the sidebar, ten at a time.
let recentShown = 10;
function renderRecent() {
  const filtering = railTags.size > 0;
  const kept = libraryConversations.filter(item => !filtering || item.category_ids.some(id => railTags.has(id)) ||
    (railTags.has('untagged') && !item.category_ids.length));
  const rows = document.querySelector('#recent-rows'); const more = document.querySelector('#recent-more');
  rows.replaceChildren(...kept.slice(0, recentShown).map(item => {
    const row = document.createElement('li'); row.className = 'list-widget-row recent-widget-row';
    const link = document.createElement('a'); link.className = 'list-widget-text'; link.draggable = false; link.href = `/conversation/${encodeURIComponent(item.id)}`;
    const title = document.createElement('strong'); title.textContent = item.title; link.append(icon('microphone'), title);
    const job = item.processing_job;
    const note = job && ['queued', 'running'].includes(job.state) ? 'Processing' : job?.state === 'failed' ? 'Processing stopped' : item.topic_line;
    if (note) { const topic = document.createElement('small'); topic.textContent = note; link.append(topic); }
    const date = document.createElement('time'); date.className = 'list-widget-date'; date.dateTime = capturedAt(item);
    date.textContent = window.StellisListDate.formatListDate(capturedAt(item));
    row.append(link, date);
    row.draggable = true;
    row.addEventListener('dragstart', event => {
      event.dataTransfer.setData(CONVERSATION_DRAG, item.id); event.dataTransfer.effectAllowed = 'copyMove'; row.classList.add('dragging');
    });
    row.addEventListener('dragend', () => row.classList.remove('dragging'));
    return row;
  }));
  if (!kept.length && filtering) {
    const empty = document.createElement('li'); empty.className = 'recent-empty';
    empty.textContent = 'No conversations with these tags';
    rows.append(empty);
  }
  // With no conversations at all the section is left out (owner, 2026-10-27); Record in the heading starts one.
  document.querySelector('#recent-widget').hidden = !libraryConversations.length;
  more.hidden = kept.length <= recentShown;
}
document.querySelector('#recent-more').addEventListener('click', () => { recentShown += 10; renderRecent(); });
// Sidebar rows and Home cards share these drops; Move to keeps the keyboard and touch route.
const CONVERSATION_DRAG = 'application/x-stellis-conversation';
const PAGE_DRAG = 'application/x-stellis-page';
const NOTEBOOK_DRAG = 'application/x-stellis-notebook';
function acceptDrops(target, accepts = {}) {
  // `accepts` maps a drag type to a function that makes the move and returns the status line.
  const types = Object.keys(accepts);
  const carried = event => types.find(type => event.dataTransfer.types.includes(type));
  // Chromium sends dragleave with no relatedTarget when crossing child elements, so count enters and leaves.
  let depth = 0;
  target.addEventListener('dragenter', event => {
    if (!carried(event)) return;
    depth += 1; target.classList.add('drop-target');
  });
  target.addEventListener('dragover', event => { if (carried(event)) event.preventDefault(); });
  target.addEventListener('dragleave', event => {
    if (carried(event) && --depth <= 0) { depth = 0; target.classList.remove('drop-target'); }
  });
  target.addEventListener('drop', async event => {
    const type = carried(event);
    if (!type) return;
    event.preventDefault(); event.stopPropagation(); depth = 0; target.classList.remove('drop-target');
    try {
      const message = await accepts[type](event.dataTransfer.getData(type));
      if (message) { setStatus(message, true); await reloadLibrary(); }
    } catch (error) { setStatus(error.message); }
  });
}
function acceptConversationDrop(card, place) {
  acceptDrops(card, {[CONVERSATION_DRAG]: id => { const item = conversationById(id); return item && place(item); }});
}
async function putJson(path, body) {
  return json(await fetch(`${API}${path}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}));
}
async function moveConversation(item, notebook) {
  // A conversation sits in one notebook, so a drop moves it; no notebook takes it out.
  if (!item) return '';
  if ((item.notebook_id || null) === (notebook?.id || null)) return `${item.title} is already ${notebook ? `in ${notebook.name}` : 'without a notebook'}`;
  await putJson(`/conversations/${encodeURIComponent(item.id)}/notebook`, {notebook_id: notebook?.id ?? null});
  return notebook ? `Moved ${item.title} to ${notebook.name}` : `Removed ${item.title} from its notebook`;
}
async function movePage(page, notebook) {
  if (!page) return '';
  if (page.notebook_id === (notebook?.id || null)) return `${page.title} is already ${notebook ? `in ${notebook.name}` : 'without a notebook'}`;
  await putJson(`/pages/${encodeURIComponent(page.id)}/notebook`, {notebook_id: notebook?.id ?? null});
  return notebook ? `Moved ${page.title} to ${notebook.name}` : `Removed ${page.title} from its notebook`;
}
async function moveNotebook(book, collection) {
  if (!book) return '';
  const current = libraryCollections.find(item => item.notebook_ids.includes(book.id));
  if ((current?.id || null) === (collection?.id || null)) return `${book.name} is already ${collection ? `in ${collection.name}` : 'outside a collection'}`;
  await putJson(`/notebooks/${encodeURIComponent(book.id)}/collection`, {collection_id: collection?.id ?? null});
  return collection ? `Moved ${book.name} into ${collection.name}` : `Moved ${book.name} out of ${current.name}`;
}
const dropOnNotebook = moveConversation;
async function dropOnCategory(item, group) {
  if (item.category_ids.includes(group.id)) return `${item.title} is already in ${group.name}`;
  await json(await fetch(`${API}/conversations/${encodeURIComponent(item.id)}/categories/${encodeURIComponent(group.id)}`, {method:'PUT'}));
  return `Added ${item.title} to ${group.name}`;
}
function tagCard(tag) {
  // A compact card: the tag's five newest conversations.
  const items = libraryConversations.filter(item => item.category_ids.includes(tag.id));
  const card = document.createElement('article'); card.className = 'notebook-card tag-card';
  const head = document.createElement('div'); head.className = 'notebook-card-head';
  const filter = document.createElement('button'); filter.type = 'button'; filter.className = 'category-card-name';
  filter.textContent = tag.name; filter.title = `Show ${tag.name} in recent conversations`;
  filter.addEventListener('click', () => { railTags = new Set([tag.id]); renderRailTags(); renderConversationList(); });
  const count = document.createElement('small'); count.textContent = items.length;
  head.append(icon('tag'), filter, count);
  card.append(head);
  if (items.length) {
    const rows = document.createElement('ul'); rows.className = 'tag-list';
    for (const item of items.slice(0, 5)) { const row = document.createElement('li'); row.append(meetingLink(item, 'notebook-meeting')); rows.append(row); }
    card.append(rows);
  } else {
    const empty = document.createElement('p'); empty.className = 'notebook-empty'; empty.textContent = 'Drag a conversation here';
    card.append(empty);
  }
  acceptConversationDrop(card, item => dropOnCategory(item, tag));
  return card;
}
let setupTimer;
let setupMisses = 0;
const setupRetry = document.querySelector('#setup-retry');
async function watchSetup() {
  clearTimeout(setupTimer);
  const card = document.querySelector('#setup-card');
  if ((await sessionStatus.catch(() => ({}))).guest) { card.hidden = true; return; }
  const status = document.querySelector('#setup-status');
  const progress = document.querySelector('#setup-progress');
  let state;
  try {
    state = await json(await fetch(`${API}/setup`));
    setupMisses = 0;
  } catch {
    // A deploy restarts the server for a few seconds; check again quietly before saying so,
    // and keep checking afterwards so the card clears once the server answers.
    setupMisses += 1;
    setupTimer = setTimeout(watchSetup, setupMisses <= 3 ? 1000 * 2 ** (setupMisses - 1) : 30000);
    if (setupMisses <= 3) return;
    card.hidden = false;
    progress.hidden = true;
    status.textContent = 'Transcription setup status is unavailable';
    setupRetry.textContent = 'Check again';
    setupRetry.dataset.action = 'check';
    setupRetry.hidden = false;
    return;
  }
  card.hidden = state.state === 'ready';
  if (card.hidden) return;
  const waiting = state.state === 'idle';
  const failed = state.state === 'failed';
  setupRetry.hidden = !waiting && !failed;
  setupRetry.textContent = waiting ? 'Start setup' : 'Try again';
  setupRetry.dataset.action = 'start';
  progress.hidden = waiting || failed;
  if (state.total_bytes > 0 && state.stage === 'receiving') {
    progress.value = Math.min(1, (state.downloaded_bytes || 0) / state.total_bytes);
  } else {
    progress.removeAttribute('value');
  }
  if (waiting) {
    status.textContent = 'Speech models are waiting to download';
  } else if (failed) {
    status.textContent = 'Sorry transcription setup failed';
  } else if (state.stage === 'verifying') {
    status.textContent = 'Checking the downloaded speech model';
  } else if (state.stage === 'receiving') {
    const size = (bytes) => bytes >= 1e6 ?
        `${Math.round(bytes / 1e5) / 10} MB` : `${Math.ceil(bytes / 1000)} KB`;
    const received = size(state.downloaded_bytes || 0);
    const total = state.total_bytes ? size(state.total_bytes) : null;
    status.textContent = !state.downloaded_bytes ?
        'Waiting for speech model data' : total === null ?
        `Downloaded ${received} of the current speech model` :
        `Downloaded ${received} of ${total} for the current speech model`;
  } else {
    status.textContent = 'Connecting to the speech model download';
  }
  if (!waiting && !failed) setupTimer = setTimeout(watchSetup, 2000);
}
setupRetry.addEventListener('click', async () => {
  setupRetry.disabled = true;
  try {
    if (setupRetry.dataset.action !== 'check') {
      await json(await fetch(`${API}/setup`, {method: 'POST'}));
    }
    setupMisses = 0;
    await watchSetup();
  } catch {
    document.querySelector('#setup-status').textContent = 'Sorry transcription setup could not start';
    setupRetry.hidden = false;
  } finally {
    setupRetry.disabled = false;
  }
});
function renderCategoryGroups() {
  // Most used first, so the busiest tags lead.
  const tags = [...libraryCategories].sort((a, b) => b.conversation_count - a.conversation_count ||
    a.name.localeCompare(b.name));
  const grid = document.querySelector('#category-group-grid');
  grid.replaceChildren(...tags.map(tagCard));
  if (!tags.length) {
    const empty = document.createElement('p'); empty.className = 'recent-empty'; empty.textContent = 'Tags group conversations across notebooks';
    grid.append(empty);
  }
}
function renderNotebooks() {
  // Cards group under their collection; notebooks outside any collection follow. A card takes meetings and notes dropped on it.
  const root = document.querySelector('#notebook-groups');
  const byId = new Map(libraryNotebooks.map(book => [book.id, book]));
  const placed = new Set(libraryCollections.flatMap(collection => collection.notebook_ids));
  const card = notebook => {
    const node = notebookCard(notebook);
    acceptDrops(node, {[CONVERSATION_DRAG]: id => moveConversation(conversationById(id), notebook),
      [PAGE_DRAG]: id => movePage(pageById(id), notebook)});
    return node;
  };
  const group = (name, color, books, kind) => {
    const section = document.createElement('div'); section.className = 'notebook-group';
    // A folder whose only notebook carries its name would just say it twice.
    const echoes = books.length === 1 && books[0].name.trim().toLowerCase() === String(name).trim().toLowerCase();
    if (name && !echoes) {
      const head = document.createElement('h3'); head.className = 'notebook-group-head';
      const lead = icon(kind); if (color) lead.style.setProperty('--tint', color);
      const label = document.createElement('span'); label.textContent = name;
      head.append(lead, label); section.append(head);
    }
    const grid = document.createElement('div'); grid.className = 'notebook-grid'; grid.append(...books.map(card));
    section.append(grid); return section;
  };
  const groups = libraryCollections.map(collection => ({name: collection.name, color: collection.color, kind: 'folder',
    books: collection.notebook_ids.map(id => byId.get(id)).filter(Boolean)})).filter(item => item.books.length);
  const loose = libraryNotebooks.filter(book => !placed.has(book.id));
  if (loose.length || !groups.length) groups.push({name: groups.length ? 'Notebooks' : '', kind: 'notebook', books: loose});
  const sections = groups.map(item => group(item.name, item.color, item.books, item.kind));
  const create = document.createElement('button'); create.type = 'button'; create.className = 'notebook-new';
  const label = document.createElement('span');
  label.textContent = 'Add Notebook';
  create.append(icon('plus'), label);
  create.addEventListener('click', () => openNotebookDialog());
  // One Add Notebook per screen (#232): the tile is the empty state's action; otherwise the heading's + and the sidebar suffice.
  if (!libraryNotebooks.length) sections[sections.length - 1].querySelector('.notebook-grid').append(create);
  // Dragging an item here takes it out of its notebook.
  const strays = libraryConversations.filter(item => !byId.has(item.notebook_id)).length + libraryPages.filter(page => !page.notebook_id).length;
  const none = document.createElement('div'); none.className = 'notebook-none';
  const noneLabel = document.createElement('span'); noneLabel.textContent = 'No notebook';
  const noneCount = document.createElement('small'); noneCount.textContent = strays;
  none.append(icon('inbox'), noneLabel, noneCount);
  none.title = 'Drop a meeting or note here to take it out of its notebook';
  acceptDrops(none, {[CONVERSATION_DRAG]: id => moveConversation(conversationById(id), null),
    [PAGE_DRAG]: id => movePage(pageById(id), null)});
  // The No notebook row appears only when something is in it.
  root.replaceChildren(...sections, ...(strays ? [none] : []));
  window.StellisNotebookWork?.home();
}

// Notebook settings offer preset swatches and a custom colour with a native picker.
// The text field holds the value; the swatches and the picker write to it.
let colourPickers = 0;
function colourPicker(host, selected) {
  const id = ++colourPickers;
  const swatches = document.createElement('div'); swatches.className = 'swatch-row';
  const text = document.createElement('input'); text.type = 'text'; text.id = `colour-text-${id}`; text.className = 'colour-text';
  text.maxLength = 7; text.placeholder = '#RRGGBB'; text.spellcheck = false; text.autocomplete = 'off'; text.setAttribute('inputmode', 'text');
  const wheel = document.createElement('input'); wheel.type = 'color'; wheel.className = 'colour-wheel'; wheel.setAttribute('aria-label', 'Pick a custom colour');
  const radios = notebookPresets.map((color, index) => {
    const option = document.createElement('label'); option.className = 'swatch'; option.style.setProperty('--swatch', color);
    const radio = document.createElement('input'); radio.type = 'radio'; radio.name = `notebook-colour-${id}`; radio.value = color;
    const label = document.createElement('span'); label.className = 'visually-hidden'; label.textContent = `Colour ${index + 1}, ${color}`;
    radio.addEventListener('change', () => set(color));
    option.append(radio, label); swatches.append(option);
    return radio;
  });
  const set = value => {
    text.value = value;
    if (window.StellisNotebookItems.isHexColour(value)) wheel.value = value.toLowerCase();
    for (const radio of radios) radio.checked = radio.value.toUpperCase() === value.toUpperCase();
    text.setAttribute('aria-invalid', String(!window.StellisNotebookItems.isHexColour(value)));
  };
  text.addEventListener('input', () => set(text.value.trim().startsWith('#') ? text.value.trim() : `#${text.value.trim()}`));
  wheel.addEventListener('input', () => set(wheel.value.toUpperCase()));
  const custom = document.createElement('div'); custom.className = 'colour-custom';
  const label = document.createElement('label'); label.htmlFor = text.id; label.textContent = 'Custom colour';
  custom.append(label, wheel, text);
  host.replaceChildren(swatches, custom);
  set(selected);
  return {value: () => text.value.trim().toUpperCase(), valid: () => window.StellisNotebookItems.isHexColour(text.value.trim())};
}
function openNotebookDialog() { window.StellisHealthCatalogue.open(); }
document.querySelector('#new-notebook').addEventListener('click', openNotebookDialog);

const notebookAddDialog = document.querySelector('#notebook-add-dialog');
async function openNotebookAdd(notebook) {
  addingToNotebook = notebook;
  document.querySelector('#notebook-add-title').textContent = `Add to ${notebook.name}`;
  document.querySelector('#notebook-add-status').textContent = '';
  if (!libraryConversations.length) libraryConversations = await json(await fetch(`${API}/conversations`));
  const candidates = libraryConversations.filter(item => item.notebook_id !== notebook.id).slice(0, 40);
  const container = document.querySelector('#notebook-add-list');
  container.replaceChildren(...candidates.map(item => {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'notebook-add-row';
    const dot = document.createElement('span'); dot.className = 'recent-dot'; dot.setAttribute('aria-hidden', 'true');
    const other = libraryNotebooks.find(book => book.id === item.notebook_id);
    if (other) dot.style.setProperty('--dot', other.color);
    const title = document.createElement('span'); title.textContent = item.title;
    const date = document.createElement('time'); date.textContent = shortDate(capturedAt(item));
    button.append(dot, title, date);
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await json(await fetch(`${API}/conversations/${encodeURIComponent(item.id)}/notebook`,
          {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({notebook_id:notebook.id})}));
        notebookAddDialog.close();
        if (activeNotebookId) await showNotebook(activeNotebookId); else await showList();
      } catch (error) { document.querySelector('#notebook-add-status').textContent = error.message; button.disabled = false; }
    });
    return button;
  }));
  if (!candidates.length) {
    const empty = document.createElement('p'); empty.textContent = 'Every conversation is already here'; container.append(empty);
  }
  notebookAddDialog.showModal();
}
document.querySelector('#notebook-add-close').addEventListener('click', () => notebookAddDialog.close());
document.querySelector('#notebook-add-note').addEventListener('click', () => {
  notebookAddDialog.close();
  newNoteIn(addingToNotebook, document.querySelector('#notebook-add-note'));
});
document.querySelector('#notebook-add-record').addEventListener('click', () => {
  notebookAddDialog.close();
  if (!capture) pendingNotebookId = addingToNotebook.id;
  document.querySelector('#record-button').click();
});
document.querySelector('#notebook-add-import').addEventListener('click', () => {
  notebookAddDialog.close();
  document.querySelector('#import-button').click();
  pendingNotebookId = addingToNotebook.id;
});
document.querySelector('#home-new-note').addEventListener('click', async () => {
  const button = document.querySelector('#home-new-note'); button.disabled = true;
  try {
    const page = await json(await fetch(`${API}/pages`, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'}));
    await openNote(page.id);
  } catch (error) { setStatus(error.message); }
  finally { button.disabled = false; }
});
// The demo can't record or import a recording (the server refuses demo recordings), so both open the
// upload, where the example visit recording is offered.
const demoGuest = () => document.body.classList.contains('is-demo-guest');
document.querySelector('#home-record').addEventListener('click', () => {
  if (demoGuest()) { window.StellisHealthCapture.open('upload', {notebookId: null}); return; }
  pendingNotebookId = null; pendingCalendarEvent = null; document.querySelector('#record-button').click();
});
document.querySelector('#home-import').addEventListener('click', () => {
  if (demoGuest()) { window.StellisHealthCapture.open('upload', {notebookId: null}); return; }
  pendingNotebookId = null; document.querySelector('#import-button').click();
});

// A note created inside a notebook opens at once in the note view.
async function newNoteIn(notebook, button) {
  button.disabled = true;
  try {
    const page = await json(await fetch(`${API}/pages`, {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({notebook_id: notebook.id})}));
    await openNote(page.id);
  } catch (error) { setStatus(error.message); }
  finally { button.disabled = false; }
}
// The notebook page: a tinted header band, Overview and List tabs, and the items table with its filters.
const activeNotebook = () => libraryNotebooks.find(item => item.id === activeNotebookId);
async function notebookTodoCounts(id) {
  const [open, done] = await Promise.all(['open', 'done'].map(async status =>
    json(await fetch(`${API}/todos?${new URLSearchParams({notebook_id: id, status})}`))));
  return window.StellisNotebookItems.countTodos(open.groups, done.groups);
}
function paintNotebookItems() {
  const {FILTERS, filterItems} = window.StellisNotebookItems;
  const filters = document.querySelector('#notebook-filters');
  filters.replaceChildren(...FILTERS.map(filter => {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'todo-filter';
    button.setAttribute('aria-pressed', String(filter.id === notebookFilter));
    button.append(icon(filter.icon), Object.assign(document.createElement('span'), {textContent: filter.label}));
    button.addEventListener('click', () => { notebookFilter = filter.id; paintNotebookItems(); });
    return button;
  }));
  const rows = filterItems(notebookItems, notebookFilter);
  const host = document.querySelector('#notebook-items-table');
  // No placeholder line for an empty list: the filters and the upload area above stay the actions.
  if (!rows.length) { host.replaceChildren(); return; }
  const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; };
  const table = el('table', 'items-table');
  const head = el('thead'); const headRow = el('tr');
  const tagNames = new Map(libraryCategories.map(tag => [tag.id, tag.name]));
  // A column shows only when some row has something in it.
  const hasTags = rows.some(item => item.categoryIds.some(id => tagNames.has(id)));
  const hasTodos = rows.some(item => item.todos.open || item.todos.done);
  const columns = [['Type', 'items-icon'], ['Title', 'items-title'], ['Date', 'items-date'],
    ...(hasTags ? [['Tags', 'items-tags']] : []), ...(hasTodos ? [['Check-ins', 'items-todos']] : [])];
  for (const [label, className] of columns) {
    const cell = el('th', className); cell.scope = 'col';
    cell.append(el('span', className === 'items-icon' ? 'visually-hidden' : '', label)); headRow.append(cell);
  }
  head.append(headRow);
  const body = el('tbody');
  for (const item of rows) {
    const meeting = item.kind === 'conversation';
    const row = el('tr');
    const lead = el('td', 'items-icon'); lead.append(icon(meeting ? 'microphone' : 'file-text'));
    lead.title = meeting ? 'Meeting' : item.kind==='document'?'Document':'Note';
    const title = el('td', 'items-title'); const link = el('a', '', item.title || 'Untitled');
    link.href = `/${item.kind==='conversation'?'conversation':item.kind==='document'?'document':'page'}/${encodeURIComponent(item.id)}`; title.append(link);
    const date = el('td', 'items-date'); const stamp = el('time', '', window.StellisListDate.formatListDate(item.date));
    stamp.dateTime = String(item.date); stamp.title = window.StellisListDate.formatListDate(item.date, {format: 'iso'}); date.append(stamp);
    const tags = el('td', 'items-tags');
    for (const id of item.categoryIds) if (tagNames.has(id)) tags.append(el('span', 'tag-chip', tagNames.get(id)));
    const todos = el('td', 'items-todos');
    if (item.todos.open || item.todos.done) {
      todos.append(el('span', 'visually-hidden', `${item.todos.open} open, ${item.todos.done} completed`));
      for (const [name, count, label] of [['circle', item.todos.open, 'open'], ['circle-check', item.todos.done, 'completed']]) {
        const part = el('span', 'items-count'); part.title = `${count} ${label}`; part.setAttribute('aria-hidden', 'true');
        part.append(icon(name), document.createTextNode(String(count))); todos.append(part);
      }
    }
    row.append(lead, title, date, ...(hasTags ? [tags] : []), ...(hasTodos ? [todos] : [])); body.append(row);
  }
  table.append(head, body);
  host.replaceChildren(table);
}
async function loadNotebookItems(notebook) {
  const counts = await notebookTodoCounts(notebook.id);
  const tags = new Map(libraryConversations.map(item => [item.id, item.category_ids || []]));
  notebookItems = window.StellisNotebookItems.buildItems({conversations: notebook.conversations, pages: notebook.pages || [],
    documents:libraryDocuments.filter(item=>inNotebook(item, notebook.id)),
    tagsByConversation: tags, counts});
  paintNotebookItems();
}
const notebookTabs = ['chat','overview','tasks','list','measurements','activity'];
function showNotebookTab(tab) {
  for (const name of notebookTabs) document.querySelector(`#notebook-tab-${name}`).setAttribute('aria-selected', String(name === tab));
  document.querySelector('#notebook-panel').setAttribute('aria-labelledby', `notebook-tab-${tab}`);
  document.querySelector('#notebook-chat').hidden = tab !== 'chat';
  document.querySelector('#notebook-measurements').hidden = tab !== 'measurements';
  if(tab==='measurements'&&activeNotebookId){
    const url=new URL(location.href);url.searchParams.set('tab',tab);history.replaceState({},'',url);
    window.StellisDashTrends.mount(document.querySelector('#notebook-measurements'),{notebookId:activeNotebookId}).catch(error=>setStatus(error.message));
  } else if(new URLSearchParams(location.search).get('tab')==='measurements'){
    const url=new URL(location.href);url.searchParams.set('tab',tab);history.replaceState({},'',url);
  }
  document.querySelector('#notebook-activity').hidden = tab !== 'activity';
  document.querySelector('#notebook-brief-empty').hidden = tab !== 'overview' || document.querySelector('#notebook-view').dataset.hasSummary === 'true';
  document.querySelector('#notebook-overview').classList.toggle('no-summary', tab === 'tasks');
  document.querySelector('#notebook-overview').hidden = !['overview','tasks'].includes(tab);
  document.querySelector('#notebook-todos').hidden = tab !== 'tasks';
  // Check-ins shows only check-ins; levels and the health timeline belong to Brief (#231: they were identical).
  document.querySelector('#notebook-levels').hidden = tab !== 'overview';
  // The tabs sit right under the notebook header; the timeline and courses are Brief content.
  document.querySelector('#notebook-health').hidden = tab !== 'overview';
  document.querySelector('#notebook-summary').hidden = tab !== 'overview' || document.querySelector('#notebook-view').dataset.hasSummary !== 'true';
  document.querySelector('#notebook-panel .notebook-items').hidden = tab !== 'list';
  document.querySelector('#notebook-filters').hidden = tab !== 'list';
  paintNotebookItems();
}

async function showNotebook(id) {
  await leaveForAppPage('notebook-page', 'Notebook');
  const [books, conversations, collections, categories] = await Promise.all([fetch(`${API}/notebooks`).then(json),
    fetch(`${API}/conversations`).then(json), fetch(`${API}/collections`).then(json), fetch(`${API}/categories`).then(json)]);
  libraryNotebooks = books.notebooks; notebookPalette = books.palette; notebookPresets = books.presets;
  libraryConversations = conversations; libraryCollections = collections.collections; libraryCategories = categories;
  const notebook = libraryNotebooks.find(item => item.id === id);
  if (!notebook) {
    history.replaceState({}, '', '/'); await showList(); setStatus('That notebook no longer exists'); return;
  }
  activeNotebookId = id; notebookFilter = 'all';
  document.querySelector('#page-title').textContent = notebook.name;
  paintNotebook(document.querySelector('#notebook-hero'), notebook);
  document.querySelector('#notebook-name').textContent = notebook.name;
  const collection = libraryCollections.find(item => item.notebook_ids.includes(id));
  document.querySelector('#notebook-collection').hidden = !collection;
  document.querySelector('#notebook-collection-name').textContent = collection?.name || '';
  // Latest summary: the newest meeting's Summary overview, shown as written.
  const lines = new Map(conversations.map(item => [item.id, item.summary_line]));
  const latest = window.StellisNotebookItems.latestSummary(notebook.conversations, lines);
  document.querySelector('#notebook-view').dataset.hasSummary = String(Boolean(latest));
  const summary = document.querySelector('#notebook-summary'); summary.hidden = !latest;
  document.querySelector('#notebook-overview').classList.toggle('no-summary', !latest);
  if (latest) {
    const link = document.querySelector('#notebook-summary-link');
    link.href = `/conversation/${encodeURIComponent(latest.id)}`; link.textContent = latest.title;
    const when = document.querySelector('#notebook-summary-date');
    when.dateTime = String(latest.date); when.textContent = window.StellisListDate.formatListDate(latest.date);
    document.querySelector('#notebook-summary-text').textContent = latest.text;
  }
  const requestedTab=new URLSearchParams(location.search).get('tab');
  showNotebookTab(notebookTabs.includes(requestedTab)?requestedTab:'chat');
  document.querySelector('#notebook-view').hidden = false;
  const rail=document.querySelector('#home-rail');document.querySelector('main').prepend(rail);
  rail.querySelectorAll('.rail-pin').forEach(link=>link.removeAttribute('aria-current'));
  await reloadLibrary();
  notebookTodos?.destroy?.();
  notebookTodos = mountTodos(document.querySelector('#notebook-todos'), {scope: id, onChange: refreshNotebookItems});
  await loadNotebookItems(notebook);
  window.StellisNotebookHealth?.mount(document.querySelector('#notebook-health'), notebook.id).catch(() => {});
  window.StellisRecordDrop?.mount(document.querySelector('#notebook-drop'), {notebookId: notebook.id,
    onUploaded: async () => { await reloadLibrary(); await refreshNotebookItems(); }});
  await Promise.all([
    window.StellisNotebookChat.mount(document.querySelector('#notebook-chat'), notebook,
      {onNote: trigger => newNoteIn(notebook, trigger),
       onRecord: () => { if (!capture) pendingNotebookId = notebook.id; document.querySelector('#record-button').click(); }}),
    window.StellisNotebookWork.mount(document.querySelector('#notebook-activity'),notebook,async()=>{await refreshNotebookItems();notebookTodos?.refresh?.();})]);
  captureControls();
}
// Adding or completing a to-do changes the counts, and adding one can create the notebook's To-dos note.
async function refreshNotebookItems() {
  if (!activeNotebookId) return;
  const books = await json(await fetch(`${API}/notebooks`)); libraryNotebooks = books.notebooks;
  const notebook = activeNotebook(); if (notebook) await loadNotebookItems(notebook);
}
for (const tab of notebookTabs) {
  const button=document.querySelector(`#notebook-tab-${tab}`);button.addEventListener('click',()=>showNotebookTab(tab));
  button.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?notebookTabs.length-1:(notebookTabs.indexOf(tab)+(event.key==='ArrowRight'?1:-1)+notebookTabs.length)%notebookTabs.length;showNotebookTab(notebookTabs[next]);document.querySelector('#notebook-tab-'+notebookTabs[next]).focus();});
}
document.querySelector('#notebook-add').addEventListener('click', () => { const notebook = activeNotebook(); if (notebook) openNotebookAdd(notebook); });
document.querySelector('#notebook-new-note').addEventListener('click', event => { const notebook = activeNotebook(); if (notebook) newNoteIn(notebook, event.currentTarget); });
document.querySelector('#notebook-record').addEventListener('click', () => {
  const notebook = activeNotebook();
  if (notebook && !capture) pendingNotebookId = notebook.id;
  document.querySelector('#record-button').click();
});
document.querySelector('#notebook-delete').addEventListener('click', async () => {
  const notebook = activeNotebook();
  if (!notebook || !window.confirm(`Delete the notebook “${notebook.name}”? Its conversations stay in your library.`)) return;
  try {
    await json(await fetch(`${API}/notebooks/${encodeURIComponent(notebook.id)}`, {method:'DELETE'}));
    history.pushState({}, '', '/'); await showList();
  } catch (error) { setStatus(error.message); }
});
// Export downloads a ZIP of the notebook: plain Markdown files, or the same files with Stellis IDs kept for re-import.
const EXPORT_LABELS = {markdown: 'Markdown', stellis: 'Stellis Health'};
async function exportNotebook(notebook, format) {
  setStatus(`Preparing the ${EXPORT_LABELS[format]} export of ${notebook.name}…`);
  try {
    const response = await fetch(`${API}/export?${new URLSearchParams({scope: 'notebook', id: notebook.id, format})}`);
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'The export failed.');
    const blob = await response.blob();
    const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(response.headers.get('content-disposition') || '')?.[1];
    const link = document.createElement('a'); link.href = URL.createObjectURL(blob);
    link.download = name ? decodeURIComponent(name) : `${notebook.name}.zip`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    setStatus(`Exported ${notebook.name} as ${link.download}`, true);
  } catch (error) { setStatus(error.message); }
}
document.querySelector('#notebook-export-markdown').addEventListener('click', () => { const notebook = activeNotebook(); if (notebook) exportNotebook(notebook, 'markdown'); });
document.querySelector('#notebook-export-stellis').addEventListener('click', () => { const notebook = activeNotebook(); if (notebook) exportNotebook(notebook, 'stellis'); });
// Settings: name, colour, collection, and whether meetings suggest to-dos.
const notebookSettingsDialog = document.querySelector('#notebook-settings-dialog');
let settingsColour = null;
function fillTodoOptions(notebook, collection, value) {
  // "Like <collection>" follows the collection's own setting; with no collection it resolves to Off.
  const select = document.querySelector('#notebook-settings-todo');
  const options = collection ? [['inherit', `Like ${collection.name}`], ['on', 'On'], ['off', 'Off']] : [['inherit', 'Off'], ['on', 'On']];
  select.replaceChildren(...options.map(([key, label]) => new Option(label, key)));
  select.value = options.some(([key]) => key === value) ? value : 'inherit';
}
async function openNotebookSettings(notebook) {
  document.querySelector('#notebook-settings-name').value = notebook.name;
  document.querySelector('#notebook-settings-status').textContent = '';
  settingsColour = colourPicker(document.querySelector('#notebook-settings-colour'), notebook.color);
  const current = libraryCollections.find(item => item.notebook_ids.includes(notebook.id));
  const select = document.querySelector('#notebook-settings-collection');
  const catalogue = await fetch(`${API}/health-catalogue`).then(json);
  const options = new Map(catalogue.collections.map(c => [c.collection_id, c.display_name]));
  for (const c of libraryCollections) if (!c.taxonomy_id) options.set(c.id, c.name);
  select.replaceChildren(...[...options].map(([id,name]) => new Option(name,id)));
  document.querySelector('#notebook-settings-description').value = notebook.description || '';
  document.querySelector('#notebook-settings-archive').checked = false;
  const features = document.querySelector('#notebook-settings-features');
  features.replaceChildren();
  const legend = document.createElement('legend'); legend.textContent = 'Features'; features.append(legend);
  const enabled = JSON.parse(notebook.capabilities_json || 'null');
  for (const [key, title] of Object.entries({timeline:'Timeline',trends:'Measurements',check_ins:'Check-ins',intervention_history:'Intervention history'})) {
    const label=document.createElement('label');const input=document.createElement('input');
    input.type='checkbox';input.value=key;input.checked=enabled === null || enabled.includes(key);
    label.append(input,document.createTextNode(title)); features.append(label);
  }

  select.value = current?.taxonomy_id || current?.id || 'fitness';
  fillTodoOptions(notebook, current, notebook.todo_list);
  notebookSettingsDialog.showModal();
}
document.querySelector('#notebook-settings-collection').addEventListener('change', event => {
  const todo = document.querySelector('#notebook-settings-todo');
  fillTodoOptions(activeNotebook(), libraryCollections.find(item => item.id === event.target.value || item.taxonomy_id === event.target.value), todo.value);
});
document.querySelector('#notebook-edit').addEventListener('click', () => { const notebook = activeNotebook(); if (notebook) openNotebookSettings(notebook); });
document.querySelector('#notebook-settings-cancel').addEventListener('click', () => notebookSettingsDialog.close());
document.querySelector('#notebook-settings-form').addEventListener('submit', async event => {
  event.preventDefault();
  const notebook = activeNotebook(); const status = document.querySelector('#notebook-settings-status');
  if (!settingsColour.valid()) { status.textContent = 'Enter the colour as #RRGGBB'; return; }
  const save = document.querySelector('#notebook-settings-save'); save.disabled = true;
  const post = (path, method, body) => fetch(`${API}${path}`, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}).then(json);
  try {
    await post(`/notebooks/${encodeURIComponent(notebook.id)}`, 'PATCH', {name: document.querySelector('#notebook-settings-name').value,
      color: settingsColour.value(), todo_list: document.querySelector('#notebook-settings-todo').value,
      description: document.querySelector('#notebook-settings-description').value,
      archived: document.querySelector('#notebook-settings-archive').checked,
      capabilities:[...document.querySelectorAll('#notebook-settings-features input:checked')].map(input=>input.value)});
    const wanted = document.querySelector('#notebook-settings-collection').value || null;
    const current = libraryCollections.find(item => item.notebook_ids.includes(notebook.id))?.id || null;
    if (wanted !== current) await post(`/notebooks/${encodeURIComponent(notebook.id)}/collection`, 'PUT', {collection_id: wanted});
    notebookSettingsDialog.close();
    if (document.querySelector('#notebook-settings-archive').checked) await showList();
    else await showNotebook(notebook.id);
  } catch (error) { status.textContent = error.message; }
  finally { save.disabled = false; }
});

const moveNotebookDialog = document.querySelector('#move-notebook-dialog');
let moveRequest = null;
function openMoveDialog({heading, none, choices, current, empty, apply}) {
  // One dialog serves every Move to path: a choice of destinations, or none to take the item out.
  moveRequest = apply;
  document.querySelector('#move-notebook-title').textContent = heading;
  document.querySelector('#move-notebook-options').replaceChildren(...[{id:'', name:none, color:null}, ...choices].map(book => {
    const option = document.createElement('label'); option.className = 'move-notebook-option';
    const radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'move-notebook'; radio.value = book.id;
    radio.checked = (current || '') === book.id;
    const dot = document.createElement('span'); dot.className = 'recent-dot'; dot.setAttribute('aria-hidden', 'true');
    if (book.color) dot.style.setProperty('--dot', book.color);
    const name = document.createElement('span'); name.textContent = book.name;
    option.append(radio, dot, name);
    return option;
  }));
  document.querySelector('#move-notebook-status').textContent = choices.length ? '' : empty;
  moveNotebookDialog.showModal();
}
const moveToNotebook = (item, move) => openMoveDialog({heading: 'Move to notebook', none: 'No notebook', choices: libraryNotebooks,
  current: item.notebook_id, empty: 'Create a notebook on the home page first',
  apply: id => move(item, libraryNotebooks.find(book => book.id === id) || null)});
const openMoveConversation = item => moveToNotebook(item, moveConversation);
const openMovePage = page => moveToNotebook(page, movePage);
function openMoveNotebookToCollection(book) {
  openMoveDialog({heading: 'Move to collection', none: 'No collection', choices: libraryCollections,
    current: libraryCollections.find(item => item.notebook_ids.includes(book.id))?.id, empty: 'Create a collection in the sidebar first',
    apply: id => moveNotebook(book, libraryCollections.find(item => item.id === id) || null)});
}
document.querySelector('#move-to-notebook').addEventListener('click', async () => {
  conversationMore.open = false;
  const [books, current] = await Promise.all([fetch(`${API}/notebooks`).then(json),
    fetch(`${API}/conversations/${encodeURIComponent(activeConversationId)}`).then(json)]);
  libraryNotebooks = books.notebooks;
  openMoveConversation(current);
});
document.querySelector('#move-notebook-cancel').addEventListener('click', () => moveNotebookDialog.close());
document.querySelector('#move-notebook-form').addEventListener('submit', async event => {
  event.preventDefault();
  const value = document.querySelector('input[name="move-notebook"]:checked')?.value ?? '';
  try {
    const message = await moveRequest(value);
    moveNotebookDialog.close();
    setStatus(message, true);
    if (!listView.hidden) await reloadLibrary();
  } catch (error) { document.querySelector('#move-notebook-status').textContent = error.message; }
});
document.querySelector('#new-collection').addEventListener('click', openNotebookDialog);
// The To-dos widget is one component: Home mounts it beside the other widgets, the To-dos page gives it the full width,
// and a notebook page passes its notebook ID as the scope.
let homeTodos = null; let pageTodos = null;
function mountTodos(host, {scope = 'all', linkTitle = false, onChange = null} = {}) {
  if (!window.StellisTodos) return null;
  return window.StellisTodos.mount(host, {scope: {notebookId: scope}, icon, setStatus, linkTitle,
    formatListDate: window.StellisListDate.formatListDate,
    notebooks: async () => libraryNotebooks.length ? libraryNotebooks : (await json(await fetch(`${API}/notebooks`))).notebooks,
    // Adding a to-do can create the To-dos note, so the sidebar and cards reload; the widget has already refreshed itself.
    onChange: onChange || (() => { if (!listView.hidden) loadLibrary().then(() => { renderNotebooks(); renderRailTags(); renderConversationList(); }).catch(() => {}); })});
}
// A note (a page, ADR 0058) opens at /page/<id>: title, notebook, tags, and the shared notes editor.
const noteTitle = document.querySelector('#note-title');
const noteStatus = document.querySelector('#note-status');
let activeNoteId = null; let activeNoteTags = [];
async function openNote(id) {
  history.pushState({}, '', `/page/${encodeURIComponent(id)}`);
  await route().catch(error => setStatus(error.message));
}
async function leaveNote() {
  const state = notesStates.page;
  suggestionsUi.page.clear();
  if (!state) return;
  await flushNotes(state);
  clearTimeout(state.timer);
  notesStates.page = null; activeNoteId = null; pageImages = [];
}
function renderNoteTags() {
  const host = document.querySelector('#note-tags'); host.replaceChildren();
  for (const tag of libraryCategories) {
    const pill = document.createElement('button'); pill.type = 'button'; pill.className = 'note-tag';
    pill.setAttribute('aria-pressed', String(activeNoteTags.includes(tag.id)));
    pill.append(icon('tag'), document.createTextNode(tag.name));
    pill.addEventListener('click', async () => {
      const next = activeNoteTags.includes(tag.id) ? activeNoteTags.filter(id => id !== tag.id) : [...activeNoteTags, tag.id];
      try {
        const saved = await putJson(`/pages/${encodeURIComponent(activeNoteId)}/categories`, {category_ids: next});
        activeNoteTags = saved.category_ids; renderNoteTags(); noteStatus.textContent = '';
        reloadLibrary().catch(() => {});
      } catch (error) { noteStatus.textContent = error.message; }
    });
    host.append(pill);
  }
  if (!libraryCategories.length) { const none = document.createElement('small'); none.textContent = 'Create tags from the ⋯ menu on Home'; host.append(none); }
}
let noteRequest = 0;
let documentRequest = 0;

/**
 * Shows the document page for one uploaded document.
 * @param {string} id
 */
async function showDocument(id) {
  const request = ++documentRequest;
  await leaveForAppPage('document-page', 'Document');
  // The page shows at once with a loader, never blank, while the record is read.
  const loading = document.querySelector('#document-status');
  if (request === documentRequest) {
    document.querySelector('#document-body').replaceChildren();
    loading.textContent = 'Loading the record…'; loading.classList.add('is-loading');
    document.querySelector('#document-view').hidden = false;
  }
  let record;
  try {
    record = await json(await fetch(`${API}/documents/${encodeURIComponent(id)}`));
  } catch {
    if (request === documentRequest) {
      history.replaceState({}, '', '/');
      await showList();
      setStatus('That document no longer exists');
    }
    return;
  }
  if (request !== documentRequest) {
    return;
  }
  document.querySelector('#page-title').textContent = record.title;
  const books = libraryNotebooks.length ?
      libraryNotebooks :
      (await json(await fetch(`${API}/notebooks`))).notebooks;
  const book = books.find((item) => item.id === record.notebook_id);
  const back = document.querySelector('#document-back');
  back.href = book ? `/notebook/${encodeURIComponent(book.id)}` : '/records';
  back.textContent = book ? book.name : 'Records';
  const status = document.querySelector('#document-status');
  status.textContent = ''; status.classList.remove('is-loading');
  const page = Number(new URLSearchParams(location.search).get('page')) || null;
  window.StellisDocumentView.render(
      document.querySelector('#document-body'), record, page,
      async (item, button) => {
        if (!window.confirm(`Delete “${item.title}” and its file?`)) {
          return;
        }
        button.disabled = true;
        try {
          await json(await fetch(
              `${API}/documents/${encodeURIComponent(item.id)}`,
              {method: 'DELETE'}));
          history.pushState({}, '', back.getAttribute('href'));
          await route();
        } catch (error) {
          status.textContent = error.message;
          button.disabled = false;
        }
      }, async () => {
        if (request === documentRequest) await showDocument(id);
      });
  document.querySelector('#document-view').hidden = false;
  document.querySelector('main').prepend(document.querySelector('#home-rail'));
  activeNotebookId = record.notebook_id;
  document.querySelector('#document-view').dataset.notebookId=record.notebook_id||'';
  const markRail = () => {
    for (const link of document.querySelectorAll('#home-rail .rail-pin')) link.removeAttribute('aria-current');
    for (const link of document.querySelectorAll('#home-rail a[href]')) {
      if (record.notebook_id && new URL(link.href).pathname === '/notebook/'+record.notebook_id) link.setAttribute('aria-current','page');
    }
  };
  // The record is drawn: the page shows now, and its report, results and sidebar fill in place, each
  // with its own loader (owner, 2026-10-29: a loader, never a lag).
  markRail(); delete document.documentElement.dataset.routing;
  await reloadLibrary();
  markRail();
}

async function showNote(id) {
  const request = ++noteRequest;
  await leaveForAppPage('note-page', 'Note');
  if (request !== noteRequest) return;
  let page;
  try { page = await json(await fetch(`${API}/pages/${encodeURIComponent(id)}`)); }
  catch {
    history.replaceState({}, '', '/'); await showList(); setStatus('That note no longer exists'); return;
  }
  const [books, categories] = await Promise.all([fetch(`${API}/notebooks`).then(json), fetch(`${API}/categories`).then(json)]);
  if (request !== noteRequest) return;
  libraryNotebooks = books.notebooks; libraryCategories = categories;
  activeNoteId = id; activeNoteTags = page.category_ids;
  noteTitle.value = page.title; noteStatus.textContent = '';
  document.querySelector('#page-title').textContent = page.title;
  const select = document.querySelector('#note-notebook');
  select.replaceChildren(new Option('No notebook', ''), ...libraryNotebooks.map(book => new Option(book.name, book.id)));
  select.value = page.notebook_id || '';
  renderNoteTags();
  document.querySelector('#page-view').hidden = false;
  notesStates.page = {id}; // claims the note before the editor loads, so late image uploads know their owner
  await loadImages('page', id);
  if (request !== noteRequest) return;
  await loadNotes('page', id);
  suggestionsUi.page.load().catch(() => {});
  // Existing notes open for reading. Focusing Lexical selects the end and
  // scrolls long documents past their title before the person can read them.
  if (page.title === 'Untitled' && !page.markdown) noteTitle.select();
  captureControls();
}
noteTitle.addEventListener('change', async () => {
  if (!activeNoteId) return;
  try {
    const page = await json(await fetch(`${API}/pages/${encodeURIComponent(activeNoteId)}`, {method:'PATCH',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({title: noteTitle.value})}));
    noteTitle.value = page.title; document.querySelector('#page-title').textContent = page.title; noteStatus.textContent = '';
    reloadLibrary().catch(() => {});
  } catch (error) { noteStatus.textContent = error.message; }
});
noteTitle.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); notesEditors.page?.focus(); } });
document.querySelector('#note-notebook').addEventListener('change', async event => {
  if (!activeNoteId) return;
  try {
    await putJson(`/pages/${encodeURIComponent(activeNoteId)}/notebook`, {notebook_id: event.target.value || null});
    noteStatus.textContent = ''; reloadLibrary().catch(() => {});
  } catch (error) { noteStatus.textContent = error.message; }
});
document.querySelector('#note-delete').addEventListener('click', async () => {
  if (!activeNoteId || !window.confirm('Delete this note? You can restore it from Recently Deleted for 30 days.')) return;
  try {
    await flushNotes(notesStates.page);
    await json(await fetch(`${API}/pages/${encodeURIComponent(activeNoteId)}/trash`, {method:'POST'}));
    try { localStorage.removeItem(notesDraftKey('page', activeNoteId)); } catch {}
    history.pushState({}, '', '/'); await showList();
  } catch (error) { noteStatus.textContent = error.message; }
});
async function showTodos() {
  await leaveForAppPage('todos-page', 'Check-ins');
  const books = await json(await fetch(`${API}/notebooks`)); libraryNotebooks = books.notebooks;
  document.querySelector('#todos-view').hidden = false;
  pageTodos?.destroy?.();
  pageTodos = mountTodos(document.querySelector('#todos-page-widget'));
  captureControls();
}

/**
 * Shows an app page beside the rail and marks its rail link as current.
 * @param {string} bodyClass
 * @param {string} title
 * @param {string} viewSelector
 * @param {string} railId
 * @return {!Promise<!Element>} The page's view.
 */
async function showRailPage(bodyClass, title, viewSelector, railId) {
  await leaveForAppPage(bodyClass, title);
  // The page shows at once with a loader, never blank while a busy server answers; the loader goes as
  // soon as the page puts in its own content.
  const shown = document.querySelector(viewSelector);
  if (!shown.querySelector(':scope > :not(.page-loading)')) {
    const loading = document.createElement('p'); loading.className = 'page-loading'; loading.setAttribute('role', 'status');
    loading.textContent = 'Loading…';
    shown.replaceChildren(loading); shown.hidden = false;
    const gone = new MutationObserver(() => {
      if (shown.querySelector(':scope > :not(.page-loading)')) { shown.querySelector(':scope > .page-loading')?.remove(); gone.disconnect(); }
    });
    gone.observe(shown, {childList: true});
  }
  const books = await json(await fetch(`${API}/notebooks`));
  libraryNotebooks = books.notebooks;
  const view = document.querySelector(viewSelector);
  view.hidden = false;
  const rail = document.querySelector('#home-rail');
  document.querySelector('main').prepend(rail);
  for (const link of rail.querySelectorAll('.rail-pin')) {
    if (link.id === railId) {
      link.setAttribute('aria-current', 'page');
    } else {
      link.removeAttribute('aria-current');
    }
  }
  await reloadLibrary();
  return view;
}

/** Shows the health timeline with its notebook filter. */
async function showTimeline() {
  const view = await showRailPage(
      'timeline-page', 'Timeline', '#timeline-view', 'rail-timeline');
  await window.StellisTimeline.mount(view, libraryNotebooks);
  healthHistoryViews(view, 'timeline');
}

/** Shows the cited summary of the person's health. */
async function showSummary() {
  const view = await showRailPage(
      'summary-page', 'Health summary', '#summary-view', 'rail-summary');
  await window.StellisSummary.mount(view);
}

/** Shows vital signs over time, with logging and graphs. */
async function showTrends() {
  const view = await showRailPage(
      'trends-page', 'Trends', '#trends-view', 'rail-trends');
  await window.StellisTrends.mount(view);
}

/** Shows every record, with upload and sorting into notebooks. */
async function showRecords() {
  const view = await showRailPage(
      'records-page', 'Records', '#records-view', 'rail-records');
  await window.StellisRecords.mount(view, reloadLibrary);
  healthHistoryViews(view, 'records');
}

async function showAgentChat() {
  await leaveForAppPage('agent-chat-page', 'Chat');
  document.querySelector('#agent-chat-view').hidden = false;
  const rail = document.querySelector('#home-rail');
  document.querySelector('main').prepend(rail);
  rail.querySelectorAll('.rail-pin').forEach(link => {if(link.id === 'rail-chat')link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
  await reloadLibrary();
  await window.StellisAgentChat.show(location.pathname.split('/')[2] || null);
  captureControls();
}

async function accountRequest(path, method, body) {
  return json(await fetch(`${API}/account${path}`, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}));
}
function settingsForm(selector, statusSelector, action) {
  const form = document.querySelector(selector);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const status = document.querySelector(statusSelector); const button = form.querySelector('button[type="submit"]');
    status.textContent = ''; status.classList.remove('success'); button.disabled = true;
    try { status.textContent = await action(); status.classList.add('success'); }
    catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
}
document.querySelector('#keep-debug-data').addEventListener('change', async ({target}) => {
  const status = document.querySelector('#debug-data-status');
  status.classList.remove('success');
  try {
    await accountRequest('/debug-data', 'PUT', {keep: target.checked});
    status.textContent = target.checked ? 'Keeping debug data' : 'Debug data off';
    status.classList.add('success');
  } catch (error) { target.checked = !target.checked; status.textContent = error.message; }
});
settingsForm('#profile-form', '#profile-status', async () => {
  await accountRequest('', 'PATCH', {name:document.querySelector('#profile-name').value, email:document.querySelector('#profile-email').value});
  return 'Profile saved';
});
settingsForm('#username-form', '#username-status', async () => {
  const result = await accountRequest('/username', 'POST',
    {username:document.querySelector('#account-username').value, password:document.querySelector('#username-password').value});
  document.querySelector('#username-password').value = '';
  return `Username changed to ${result.username}`;
});
settingsForm('#password-form', '#password-status', async () => {
  const next = document.querySelector('#new-password').value;
  if (next !== document.querySelector('#confirm-password').value) throw Error('The new passwords do not match');
  await accountRequest('/password', 'POST', {current_password:document.querySelector('#current-password').value, new_password:next});
  for (const id of ['#current-password', '#new-password', '#confirm-password']) document.querySelector(id).value = '';
  return 'Password changed and other sessions signed out';
});
settingsForm('#delete-account-form', '#delete-status', async () => {
  if (document.querySelector('#delete-confirm').value !== 'DELETE') throw Error('Type DELETE in capitals to confirm');
  await accountRequest('', 'DELETE', {password:document.querySelector('#delete-password').value, confirm:'DELETE'});
  location.replace('/auth');
  return 'Account deleted';
});
async function loadSettingsCalendars() {
  const container = document.querySelector('#settings-calendars');
  // On a direct /settings load this runs before the deferred calendar.js; DOMContentLoaded follows it.
  if (!window.StellisCalendar) await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once:true}));
  // Only providers this deployment can connect are listed; with none, the calendar block stays hidden.
  const rows = (await window.StellisCalendar.request('/connections')).filter(item => item.connected || item.connectable);
  document.querySelector('#settings-calendar-block').hidden = !rows.length;
  container.replaceChildren(...rows.map(item => {
    const row = document.createElement('div'); row.className = 'integration-row';
    const text = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = window.StellisCalendar.labels[item.provider];
    const state = document.createElement('small');
    state.textContent = item.connected ? 'Connected' : 'Not connected';
    text.append(name, state);
    row.append(icon('calendar'), text);
    const action = document.createElement('button'); action.type = 'button';
    action.textContent = item.connected ? 'Disconnect' : 'Connect';
    action.addEventListener('click', async () => {
      action.disabled = true; document.querySelector('#settings-calendar-status').textContent = '';
      try {
        if (item.connected) {
          await window.StellisCalendar.request(`/${item.provider}/disconnect`, {method:'POST'});
          await loadSettingsCalendars();
        }
        else location.assign((await window.StellisCalendar.request(`/${item.provider}/authorize?return_to=/settings`)).url);
      } catch (error) { document.querySelector('#settings-calendar-status').textContent = error.message; action.disabled = false; }
    });
    row.append(action);
    return row;
  }));
}
async function showSettings() {
  await leaveForAppPage('settings-page', 'Account settings');
  for (const id of ['#profile-status', '#username-status', '#password-status', '#delete-status', '#settings-calendar-status', '#debug-data-status'])
    document.querySelector(id).textContent = '';
  if (calendarOutcome) { document.querySelector('#settings-calendar-status').textContent = calendarOutcomeText(); calendarOutcome = null; }
  document.querySelector('#settings-view').hidden = false;
  // Example records (and a demo guest) run in a workspace with no account of its own to read.
  const examples = await window.StellisExampleWorkspace?.settings();
  const guest = examples || document.body.classList.contains('is-demo-guest');
  const [account, terms] = await Promise.all([guest ? {ok: false} : fetch(`${API}/account`), fetch(`${API}/vocabulary`).then(json)]);
  const signedIn = account.ok;
  document.querySelector('#keep-debug-data').disabled = !signedIn;
  document.querySelector('#debug-data-download').hidden = !signedIn;
  for (const form of document.querySelectorAll('#settings-view form')) {
    form.querySelectorAll('input, button').forEach(control => { control.disabled = !signedIn; });
  }
  if (signedIn) {
    const profile = await account.json();
    document.querySelector('#profile-name').value = profile.name;
    document.querySelector('#profile-email').value = profile.email;
    document.querySelector('#account-username').value = profile.username;
    document.querySelector('#keep-debug-data').checked = profile.keep_debug_data;
  }
  // Without an account of its own (the demo), the sections that change an account are left out rather
  // than shown empty and locked (owner, 2026-10-29).
  for (const id of ['#settings-profile-title', '#settings-signin-title', '#settings-integrations-title', '#settings-debug-title', '#settings-delete-title'])
    document.querySelector(id).closest('.settings-section').hidden = !signedIn;
  await window.StellisHealthPreferences.mountSettings();
  const names = terms.filter(term => term.kind === 'name').length;
  document.querySelector('#settings-vocabulary-count').textContent =
    `${names} ${names === 1 ? 'name' : 'names'} and ${terms.length - names} other ${terms.length - names === 1 ? 'term' : 'terms'}`;
  await loadSettingsCalendars().catch(error => { document.querySelector('#settings-calendar-status').textContent = error.message; });
  loadDateFormat();
  const operator = (await fetch(`${API}/settings/runtime-profile`).then(json).catch(() => ({operator: false}))).operator;
  // Accounts on the deployment's shared keys see no provider or model settings.
  for (const section of document.querySelectorAll('[data-operator-only]')) section.hidden = !operator;
  if (operator) {
    await loadNebiusKey().catch(error => { document.querySelector('#nebius-key-status').textContent = error.message; });
    await loadTavilyKey().catch(error => { document.querySelector('#tavily-key-status').textContent = error.message; });
  }
  import("/static/runtime-settings.js").then(module => module.load());
  captureControls();
  if (focusKeyInput() && operator) {
    document.querySelector('#settings-ai-title').scrollIntoView({block: 'start'});
    document.querySelector('#nebius-key-input').focus({preventScroll: true});
  }
}
function focusKeyInput() {
  try { const wanted = sessionStorage.getItem('stellis-focus-key') === '1'; sessionStorage.removeItem('stellis-focus-key'); return wanted; }
  catch { return false; }
}
document.addEventListener('click', event => {
  if (!event.target.closest('a[data-add-key]')) return;
  try { sessionStorage.setItem('stellis-focus-key', '1'); } catch {}
});
function showNebiusKey(state) {
  // The saved key is never sent back; the page only learns whether one exists.
  document.querySelector('#nebius-key-input').value = '';
  document.querySelector('#nebius-key-remove').hidden = state.source !== 'saved';
  document.querySelector('#nebius-key-save').textContent = state.source === 'saved' ? 'Replace key' : 'Save key';
  document.querySelector('#nebius-key-status').textContent = state.source === 'saved' ? 'A key is saved on this device.' :
    state.source === 'environment' ? 'Using the key from this computer\'s environment.' : 'No key saved for transcript AI';
}
async function loadNebiusKey() {
  const [key, runtime] = await Promise.all([
    fetch(`${API}/settings/nebius-key`).then(json),
    fetch(`${API}/settings/runtime-profile`).then(json)
  ]);
  showNebiusKey(key);
  document.querySelector('#runtime-profile-status').textContent = runtime.label;
  document.querySelector('#runtime-profile-note').textContent = runtime.local_inference ?
    'AI inference uses the configured local model server. Connected tools may contact their services.' :
    'AI runs on Nebius Token Factory with only what each task needs';
  document.querySelector('#nebius-key-form').hidden = runtime.local_inference;
}
function showTavilyKey(state) {
  document.querySelector('#tavily-key-input').value = '';
  document.querySelector('#tavily-key-remove').hidden = state.source !== 'saved';
  document.querySelector('#tavily-key-save').textContent = state.source === 'saved' ? 'Replace key' : 'Save key';
  document.querySelector('#tavily-key-status').textContent = state.source === 'saved' ?
    'Key saved.' :
    state.source === 'environment' ? 'Using the key from this computer' : 'No key saved';
}
async function loadTavilyKey() { showTavilyKey(await json(await fetch(`${API}/settings/tavily-key`))); }
document.querySelector('#tavily-key-form').addEventListener('submit', async event => {
  event.preventDefault();
  const input = document.querySelector('#tavily-key-input');
  const status = document.querySelector('#tavily-key-status');
  if (!input.value.trim()) { status.textContent = 'Paste your Tavily API key first.'; return; }
  const save = document.querySelector('#tavily-key-save'); save.disabled = true;
  status.textContent = 'Saving key…';
  try {
    showTavilyKey(await json(await fetch(`${API}/settings/tavily-key`, {method:'PUT',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({key:input.value})})));
  } catch (error) { status.textContent = error.message; }
  finally { save.disabled = false; }
});
document.querySelector('#tavily-key-remove').addEventListener('click', async () => {
  try { showTavilyKey(await json(await fetch(`${API}/settings/tavily-key`, {method:'DELETE'}))); }
  catch (error) { document.querySelector('#tavily-key-status').textContent = error.message; }
});

function showDateFormatExample() {
  const {formatListDate, getDateFormat} = window.StellisListDate;
  const tomorrow = new Date(Date.now() + 86400000);
  const inFiveDays = new Date(Date.now() + 5 * 86400000);
  const inTwoWeeks = new Date(Date.now() + 14 * 86400000);
  document.querySelector('#date-format-example').textContent =
    `Example: ${[tomorrow, inFiveDays, inTwoWeeks].map(date => formatListDate(date.toISOString(), {format:getDateFormat()})).join(', ')}`;
}
document.querySelector('#date-format-select').addEventListener('change', event => {
  window.StellisListDate.setDateFormat(event.target.value); showDateFormatExample();
});
function loadDateFormat() {
  document.querySelector('#date-format-select').value = window.StellisListDate.getDateFormat();
  showDateFormatExample();
}
document.querySelector('#nebius-key-form').addEventListener('submit', async event => {
  event.preventDefault();
  const input = document.querySelector('#nebius-key-input');
  if (!input.value.trim()) { document.querySelector('#nebius-key-status').textContent = 'Paste your Nebius API key first.'; return; }
  const save = document.querySelector('#nebius-key-save'); save.disabled = true;
  document.querySelector('#nebius-key-status').textContent = 'Saving key…';
  try {
    showNebiusKey(await json(await fetch(`${API}/settings/nebius-key`, {method: 'PUT',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key: input.value})})));
    await refreshAI();
  } catch (error) { document.querySelector('#nebius-key-status').textContent = error.message; }
  finally { save.disabled = false; }
});
document.querySelector('#nebius-key-remove').addEventListener('click', async () => {
  try { showNebiusKey(await json(await fetch(`${API}/settings/nebius-key`, {method: 'DELETE'}))); await refreshAI(); }
  catch (error) { document.querySelector('#nebius-key-status').textContent = error.message; }
});

// Edit widgets: choose which Home widgets show. The choice is kept per browser.
const WIDGETS_KEY = 'stellis-home-widgets';
const HOME_WIDGETS = [['brief', 'Daily brief', 'sparkles'], ['todos', 'Check-ins', 'circle-check'], ['coming-up', 'Coming up', 'calendar'], ['notebooks', 'Notebooks', 'notebook'],
  ['recent', 'Recent conversations', 'clock'], ['tags', 'Tags', 'tag']];
// The daily brief is off until the person turns it on.
const hiddenWidgets = new Set((() => { try { return JSON.parse(localStorage.getItem(WIDGETS_KEY)) || ['brief']; } catch { return ['brief']; } })());
function applyWidgets() {
  for (const [id] of HOME_WIDGETS) for (const node of document.querySelectorAll(`#list-view [data-widget="${id}"]`)) node.classList.toggle('widget-off', hiddenWidgets.has(id));
  document.querySelector('.home-columns').classList.toggle('single-column', hiddenWidgets.has('todos'));
}
function saveWidgets() { try { localStorage.setItem(WIDGETS_KEY, JSON.stringify([...hiddenWidgets])); } catch {} }
const widgetsDialog = document.querySelector('#widgets-dialog');
document.querySelector('#edit-widgets').addEventListener('click', () => {document.querySelector('#library-more').open = false; window.StellisHealthDashboard?.customize();});
document.querySelector('#close-widgets').addEventListener('click', () => widgetsDialog.close());
applyWidgets();
window.StellisAppReady=route().catch(error => setStatus(error.message)).finally(() => delete document.documentElement.dataset.routing);
window.StellisNavigate=async path=>{await window.StellisAppReady;if(location.pathname+location.search!==path)history.pushState({},'',path);await route();};

// My Health's heading has one call control; adding goes through + Add, the library and the goals form (#231).
document.getElementById('dashboard-call').addEventListener('click', event => window.StellisCoachCall?.open({kind:event.currentTarget.dataset.kind||'call', chat:false}));
// Call Stellis's handle slides like an incoming call's, so a swipe along the pill calls as a tap does
// (owner, 2026-10-31: "Call swiping doesn't work"). Short of 60% of the way it slides back.
(function swipeToCall(button) {
  const handle = button.querySelector('.call-launch-handle');
  let origin = null, offset = 0, moved = false, quietUntil = 0;
  const room = () => Math.max(0, button.clientWidth - handle.offsetWidth - 8);
  const place = x => { offset = Math.max(0, Math.min(room(), x)); handle.style.translate = offset ? `${offset}px 0` : ''; };
  button.addEventListener('pointerdown', event => { if (event.button > 0) return; origin = event.clientX; moved = false; button.setPointerCapture(event.pointerId); });
  button.addEventListener('pointermove', event => {
    if (origin === null) return;
    if (Math.abs(event.clientX - origin) > 6) moved = true;
    if (moved) { button.classList.add('is-sliding'); place(event.clientX - origin); }
  });
  const release = cancelled => {
    if (origin === null) return;
    origin = null; button.classList.remove('is-sliding');
    const far = moved && !cancelled && offset >= room() * .6;
    place(0);
    if (!moved) return;
    quietUntil = Date.now() + 500;
    if (far) window.StellisCoachCall?.open({kind:button.dataset.kind||'call', chat:false});
  };
  button.addEventListener('pointerup', () => release(false));
  button.addEventListener('pointercancel', () => release(true));
  // A slide is not also a tap.
  button.addEventListener('click', event => { if (Date.now() < quietUntil) event.stopImmediatePropagation(); }, true);
})(document.getElementById('dashboard-call'));

function healthHistoryViews(view, active) {
  const nav = document.createElement('nav'); nav.className = 'dashboard-history-links';
  nav.setAttribute('aria-label', 'Health history views');
  for (const [key,label] of [['timeline','Timeline'],['records','Records']]) {
    const link = document.createElement('a'); link.href = '/'+key; link.textContent = label;
    if (active === key) link.setAttribute('aria-current', 'page');
    nav.append(link);
  }
  view.prepend(nav);
}

// Reuse the recording destination and its existing microphone lifecycle.
window.startHealthRecording = notebookId => {
  if (!capture) pendingNotebookId = notebookId || null;
  document.querySelector('#record-button').click();
};

window.addEventListener('health-capture-saved', () => {
  if (location.pathname === '/') window.StellisHealthDashboard?.reload({books:libraryNotebooks,collections:libraryCollections});
  const notebookId = location.pathname.match(/^\/notebook\/([^/]+)$/)?.[1];
  if (notebookId) window.StellisNotebookHealth?.mount(document.querySelector('#notebook-health'), notebookId).catch(()=>{});
  if (location.pathname === '/trends') window.StellisTrends?.mount(document.querySelector('#trends-view'));
});

window.addEventListener('stellis:attachment-filed',async()=>{try{await reloadLibrary();await refreshNotebookItems();}catch(error){setStatus(error.message);}});
