// To-do lines in the notes editor (ADR 0059): hidden IDs, canceled status, typed dates, and the cancel controls.
// The Markdown stays the stored form: `- [ ] text ^tabc123`. The editor keeps the ID, the canceled flag and the
// resolved due date in node state, so none of them is visible text and none is copied when Enter starts a new item.
import {
  $getNearestNodeFromDOMNode, $getNodeByKey, $getRoot, $getSelection, $getState, $hasUpdateTag, $isRangeSelection,
  $isTextNode, $setState, COMMAND_PRIORITY_HIGH, KEY_DOWN_COMMAND, TextNode, createState,
} from 'lexical';
import {$findMatchingParent} from '@lexical/utils';
import {$isListItemNode, $isListNode, ListItemNode} from '@lexical/list';
import {CHECK_LIST} from '@lexical/markdown';
import {parse as parseDates} from 'chrono-node';

const ID_AT_END = /(?:^| )\^(t[0-9a-z]{6})\s*$/;
const CANCELED_MARK = '\u0001'; // marks a canceled item in exported text until its `[ ]` becomes `[-]`
const ICONS = {
  dots:['M4 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0', 'M11 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0', 'M18 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0'],
  done:['M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0', 'M9 12l2 2l4 -4'],
  cancel:['M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0', 'M10 10l4 4m0 -4l-4 4'],
  reopen:['M9 14l-4 -4l4 -4', 'M5 10h11a4 4 0 1 1 0 8h-1'],
};

// resetOnCopyNode keeps Enter from copying an item's ID, status or date onto the new item.
const idState = createState('todoId', {parse:value => typeof value === 'string' && /^t[0-9a-z]{6}$/.test(value) ? value : '', resetOnCopyNode:true});
const canceledState = createState('todoCanceled', {parse:value => value === true, resetOnCopyNode:true});
const dueDateState = createState('todoDueDate', {parse:value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '', resetOnCopyNode:true});
const dueTimeState = createState('todoDueTime', {parse:value => typeof value === 'string' && /^\d{2}:\d{2}$/.test(value) ? value : '', resetOnCopyNode:true});
// The words the date came from. A date is only worked out again when these words change, so "tomorrow"
// keeps the day it was typed on instead of sliding forward every time the note is opened.
const duePhraseState = createState('todoDuePhrase', {parse:value => typeof value === 'string' ? value : '', resetOnCopyNode:true});

const pad = number => String(number).padStart(2, '0');

// First natural-language date in a task line, as {phrase, date: 'YYYY-MM-DD', time: 'HH:MM' | ''}, or null.
export function findDue(text, now = new Date()) {
  const found = parseDates(text, now, {forwardDate:true})[0];
  if (!found) return null;
  const day = found.start.date();
  return {phrase:found.text, date:`${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`,
    time:found.start.isCertain('hour') ? `${pad(day.getHours())}:${pad(day.getMinutes())}` : ''};
}

const $isTaskItem = node => $isListItemNode(node) && $isListNode(node.getParent()) && node.getParent().getListType() === 'check'
  && !node.getChildren().some($isListNode);
const $ownText = item => item.getChildren().map(child => child.getTextContent()).join('');
// A typed `[ ] ` is still text in a bullet until the save turns it into a check box.
const $matchText = item => $ownText(item).replace(/^\[[ xX-]\] /, '');
const $statusOf = item => $getState(item, canceledState) ? 'canceled' : item.getChecked() ? 'done' : 'open';
function $setStatus(item, status) {
  item.setChecked(status === 'done');
  $setState(item, canceledState, status === 'canceled');
}
function $setDue(item, due) {
  $setState(item, dueDateState, due?.date || '');
  $setState(item, dueTimeState, due?.time || '');
  $setState(item, duePhraseState, due?.phrase || '');
}
// Every list item that holds its own text, whatever its list type: a typed `- [ ] ` is a bullet until the save reloads it.
const $listItemsIn = root => {
  const out = [];
  const walk = node => node.getChildren?.().forEach(child => {
    if ($isListItemNode(child) && !child.getChildren().some($isListNode)) out.push(child); else walk(child);
  });
  walk(root);
  return out;
};
const $taskItemsIn = root => {
  const out = [];
  const walk = node => node.getChildren?.().forEach(child => {
    if ($isTaskItem(child)) out.push(child); else walk(child);
  });
  walk(root);
  return out;
};

// The CHECK_LIST transformer, extended to read `[-]` and the trailing ID and to write both back.
export function taskListTransformer() {
  return {
    ...CHECK_LIST,
    regExp:/^(\s*)(?:[-*+]\s)?\s?(\[(\s|x|-)?\])\s/i,
    replace(parent, children, match, isImport) {
      const canceled = match[3] === '-';
      const adjusted = Object.assign([...match], {index:match.index, input:match.input});
      if (canceled) adjusted[3] = ' ';
      let id = '';
      const last = children.at(-1);
      if (isImport && $isTextNode(last)) {
        const tail = ID_AT_END.exec(last.getTextContent());
        if (tail) {id = tail[1]; last.setTextContent(last.getTextContent().slice(0, tail.index));}
      }
      if (CHECK_LIST.replace(parent, children, adjusted, isImport) === false) return false;
      const item = $findMatchingParent(children[0] ?? $getSelection()?.anchor?.getNode(), $isListItemNode);
      if (!item) return undefined;
      if (canceled) $setState(item, canceledState, true);
      if (id) $setState(item, idState, id);
      return undefined;
    },
    export(node, exportChildren, selection) {
      const withTaskFields = child => {
        const text = exportChildren(child);
        if (!$isListItemNode(child)) return text;
        const marked = (!child.getChecked() && $getState(child, canceledState) ? CANCELED_MARK : '') + text;
        const id = $getState(child, idState);
        return id && text ? `${marked} ^${id}` : marked;
      };
      const out = CHECK_LIST.export(node, withTaskFields, selection);
      return out == null ? out : out.replaceAll(`[ ] ${CANCELED_MARK}`, '[-] ').replaceAll(CANCELED_MARK, '');
    },
  };
}

// After a Markdown load: give each item the due date the server holds for its ID. After a reload that the user
// caused (keepSelection), a line that had no ID before is new: it takes the date from the words it was typed
// with, and a line that had a date on the same text keeps it.
export function $restoreDates(dates = {}, carried = []) {
  const spare = [...carried];
  for (const item of $listItemsIn($getRoot())) {
    const stored = dates[$getState(item, idState)];
    const text = $matchText(item);
    if (stored?.due_date) {
      $setState(item, dueDateState, stored.due_date);
      $setState(item, dueTimeState, stored.due_time || '');
      $setState(item, duePhraseState, findDue(text)?.phrase || '');
      continue;
    }
    const at = spare.findIndex(entry => entry.text === text);
    if (at < 0) continue;
    const [entry] = spare.splice(at, 1);
    if (entry.date) {
      $setState(item, dueDateState, entry.date); $setState(item, dueTimeState, entry.time);
      $setState(item, duePhraseState, entry.phrase);
    } else if (!entry.id && $isTaskItem(item)) {
      $setDue(item, findDue(text));
    }
  }
}

// What the open document holds, to carry across a reload. Call inside editor.read or update.
export function $captureDates() {
  return $listItemsIn($getRoot()).map(item => ({text:$matchText(item), id:$getState(item, idState),
    date:$getState(item, dueDateState), time:$getState(item, dueTimeState), phrase:$getState(item, duePhraseState)}));
}
// {id: {due_date, due_time}} for every item with an ID, null for one with no date. Call inside editor.read.
export function $collectDates() {
  const out = {};
  for (const item of $taskItemsIn($getRoot())) {
    const id = $getState(item, idState);
    if (!id) continue;
    const date = $getState(item, dueDateState);
    out[id] = date ? {due_date:date, due_time:$getState(item, dueTimeState) || null} : null;
  }
  return out;
}

// `[-] `, `[x] ` or `[ ] ` typed at the start of a to-do line sets its status. (Lexical's own shortcut only
// works on a line outside any list.)
function $typedMarker(text) {
  if ($hasUpdateTag('notes-load') || text.getPreviousSibling()) return;
  const item = text.getParent();
  const marker = /^\[( |x|X|-)\] /.exec(text.getTextContent());
  if (!marker || !$isTaskItem(item)) return;
  text.spliceText(0, marker[0].length, '', true);
  $setStatus(item, marker[1] === '-' ? 'canceled' : marker[1] === ' ' ? 'open' : 'done');
}

// Re-reads a line's date when its words change. Skipped while a document loads, so opening a note never
// invents dates from old text.
function $syncDue(text) {
  if ($hasUpdateTag('notes-load')) return;
  const item = $findMatchingParent(text, $isListItemNode);
  if (!item || !$isTaskItem(item)) return;
  const found = findDue($ownText(item));
  const phrase = $getState(item, duePhraseState);
  if (found) {
    if (found.phrase !== phrase) $setDue(item, found);
  } else if (phrase) {
    $setDue(item, null);
  }
}

function label(date, time) {
  const day = globalThis.StellisListDate?.formatListDate(date) || date;
  if (!time) return day;
  const at = new Date(2000, 0, 1, +time.slice(0, 2), +time.slice(3));
  return `${day} ${at.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'})}`;
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', 'stroke-width':'2',
    'stroke-linecap':'round', 'stroke-linejoin':'round', 'aria-hidden':'true'})) svg.setAttribute(key, value);
  for (const d of ICONS[name]) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', d); svg.append(path);
  }
  return svg;
}

// The line menu: one ⋯ button that follows the pointer or caret to a to-do line and offers Complete, Cancel, Reopen.
function registerTaskMenu(editor, root) {
  const host = root.parentElement;
  host.classList.add('note-task-host');
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || '');
  const hint = keys => `${mac ? '⌘' : 'Ctrl+'}${keys}`;
  const more = document.createElement('button');
  more.type = 'button'; more.className = 'note-task-more'; more.hidden = true;
  more.setAttribute('aria-label', 'To-do actions'); more.setAttribute('aria-haspopup', 'menu'); more.append(icon('dots'));
  const menu = document.createElement('div');
  menu.className = 'note-task-menu'; menu.hidden = true; menu.setAttribute('role', 'menu');
  const choices = [['done', 'Complete', hint('D'), 'done'], ['canceled', 'Cancel', hint('⇧D'), 'cancel'], ['open', 'Reopen', '', 'reopen']];
  for (const [status, name, keys, glyph] of choices) {
    const option = document.createElement('button');
    option.type = 'button'; option.dataset.status = status; option.setAttribute('role', 'menuitem');
    const text = document.createElement('span'); text.textContent = name;
    const shortcut = document.createElement('kbd'); shortcut.textContent = keys;
    option.append(icon(glyph), text, shortcut); menu.append(option);
  }
  host.append(more, menu);
  let current = null; // DOM element of the line the button sits on

  const place = element => {
    current = element;
    if (!element) {more.hidden = true; menu.hidden = true; return;}
    const box = element.getBoundingClientRect(); const around = host.getBoundingClientRect();
    more.style.top = `${box.top - around.top + host.scrollTop - 1}px`;
    more.hidden = false;
    menu.style.top = `${box.top - around.top + host.scrollTop + 32}px`;
  };
  const lineAt = node => node?.closest?.('li.note-check-open, li.note-check-done, li.note-check-canceled') || null;
  root.addEventListener('pointermove', event => {if (menu.hidden) place(lineAt(event.target) || current);});
  host.addEventListener('pointerleave', () => {if (menu.hidden && !root.contains(document.activeElement)) place(null);});
  more.addEventListener('mousedown', event => event.preventDefault());
  more.addEventListener('click', () => {
    const open = menu.hidden;
    if (open && current) {
      const status = editor.read(() => {
        const node = $getNearestNodeFromDOMNode(current);
        return $isListItemNode(node) ? $statusOf(node) : 'open';
      });
      for (const option of menu.children) option.hidden = option.dataset.status === status;
    }
    menu.hidden = !open;
  });
  menu.addEventListener('mousedown', event => event.preventDefault());
  menu.addEventListener('click', event => {
    const status = event.target.closest('button')?.dataset.status;
    if (!status || !current) return;
    editor.update(() => {
      const node = $getNearestNodeFromDOMNode(current);
      if ($isListItemNode(node)) $setStatus(node, status);
    });
    menu.hidden = true; editor.focus();
  });
  document.addEventListener('pointerdown', event => {if (!menu.hidden && !menu.contains(event.target) && event.target !== more) menu.hidden = true;});
  root.addEventListener('keydown', event => {if (event.key === 'Escape' && !menu.hidden) {menu.hidden = true; event.stopPropagation();}});
  const release = editor.registerUpdateListener(({editorState}) => {
    if (!menu.hidden) return;
    editorState.read(() => {
      const selection = $getSelection();
      const item = $isRangeSelection(selection) ? $findMatchingParent(selection.anchor.getNode(), $isListItemNode) : null;
      const element = item && $isTaskItem(item) ? editor.getElementByKey(item.getKey()) : null;
      if (element) place(element); else if (!root.matches(':hover')) place(null);
    });
  });
  return () => {release(); more.remove(); menu.remove(); host.classList.remove('note-task-host');};
}

// Registers the to-do behaviour on an editor. `dates` turns on typed-date parsing and its display.
export function registerTasks(editor, root, {dates = false} = {}) {
  const release = [
    // Checking a canceled item completes it; the two states are exclusive.
    editor.registerNodeTransform(ListItemNode, item => {
      if (item.getChecked() && $getState(item, canceledState)) $setState(item, canceledState, false);
    }),
    editor.registerMutationListener(ListItemNode, mutations => {
      for (const [key, kind] of mutations) {
        if (kind === 'destroyed') continue;
        const element = editor.getElementByKey(key);
        if (!element) continue;
        const state = editor.getEditorState().read(() => {
          const node = $getNodeByKey(key);
          return node && $isTaskItem(node) ? {canceled:$getState(node, canceledState), checked:node.getChecked(),
            date:$getState(node, dueDateState), time:$getState(node, dueTimeState)} : null;
        });
        element.classList.toggle('note-check-canceled', !!state && state.canceled && !state.checked);
        const shown = dates && state?.date ? label(state.date, state.time) : '';
        if (shown) element.dataset.due = shown; else delete element.dataset.due;
        element.classList.toggle('note-check-dated', !!shown);
      }
    }, {skipInitialization:false}),
    editor.registerCommand(KEY_DOWN_COMMAND, event => {
      if (event.key.toLowerCase() !== 'd' || !(event.metaKey || event.ctrlKey) || event.altKey) return false;
      const selection = $getSelection();
      if (!$isRangeSelection(selection)) return false;
      const items = new Set();
      for (const node of selection.getNodes()) {
        const item = $findMatchingParent(node, $isListItemNode);
        if (item && $isTaskItem(item)) items.add(item);
      }
      if (!items.size) return false;
      event.preventDefault();
      const target = event.shiftKey ? 'canceled' : 'done';
      // The shortcut toggles: the whole selection leaves the status when every line already has it.
      const next = [...items].every(item => $statusOf(item) === target) ? 'open' : target;
      for (const item of items) $setStatus(item, next);
      return true;
    }, COMMAND_PRIORITY_HIGH),
    editor.registerNodeTransform(TextNode, $typedMarker),
    registerTaskMenu(editor, root),
  ];
  if (dates) release.push(editor.registerNodeTransform(TextNode, $syncDue));
  return () => release.forEach(stop => stop());
}
