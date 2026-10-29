// The slash menu (#317): typing `/` on an empty line, or after a space, opens a filterable list of blocks.
// Plain JavaScript on Lexical's update listener; @lexical/react is not used. The block each row inserts is the
// same node or command the toolbar and the Markdown shortcuts already use.
import {
  $createParagraphNode, $getSelection, $isElementNode, $isParagraphNode, $isRangeSelection, $isTextNode,
} from 'lexical';
import {$createHeadingNode, $createQuoteNode} from '@lexical/rich-text';
import {$createCodeNode, $isCodeNode} from '@lexical/code-core';
import {$createHorizontalRuleNode} from '@lexical/extension';
import {INSERT_CHECK_LIST_COMMAND, INSERT_ORDERED_LIST_COMMAND, INSERT_UNORDERED_LIST_COMMAND} from '@lexical/list';
import {$setBlocksType} from '@lexical/selection';
import {$createTableNodeWithDimensions} from '@lexical/table';
import {$findMatchingParent} from '@lexical/utils';
import {$insertCallout} from './notes-callouts.js';
import {$currentBlock, $insertColumns, $insertToggle} from './notes-containers.js';
import {BookmarkNode, VideoEmbedNode, httpUrl, videoEmbed} from './notes-embeds.js';

// Tabler Icons outline paths (MIT), recorded in icons.NOTICES.txt.
const ICONS = {
  text:['M4 20l3 0', 'M14 20l7 0', 'M6.9 15l6.9 0', 'M10.2 6.3l5.8 13.7', 'M5 20l6 -16l2 0l7 16'],
  h1:['M19 18v-8l-2 2', 'M4 6v12', 'M12 6v12', 'M11 18h2', 'M3 18h2', 'M4 12h8', 'M3 6h2', 'M11 6h2'],
  h2:['M17 12a2 2 0 1 1 4 0c0 .591 -.417 1.318 -.816 1.858l-3.184 4.143l4 0', 'M4 6v12', 'M12 6v12', 'M11 18h2', 'M3 18h2', 'M4 12h8', 'M3 6h2', 'M11 6h2'],
  h3:['M19 14a2 2 0 1 0 -2 -2', 'M17 16a2 2 0 1 0 2 -2', 'M4 6v12', 'M12 6v12', 'M11 18h2', 'M3 18h2', 'M4 12h8', 'M3 6h2', 'M11 6h2'],
  list:['M9 6l11 0', 'M9 12l11 0', 'M9 18l11 0', 'M5 6l0 .01', 'M5 12l0 .01', 'M5 18l0 .01'],
  numbers:['M11 6h9', 'M11 12h9', 'M12 18h8', 'M4 16a2 2 0 1 1 4 0c0 .591 -.5 1 -1 1.5l-3 2.5h4', 'M6 10v-6l-2 2'],
  todo:['M9 11l3 3l8 -8', 'M20 12v6a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2h9'],
  quote:['M10 11h-4a1 1 0 0 1 -1 -1v-3a1 1 0 0 1 1 -1h3a1 1 0 0 1 1 1v6c0 2.667 -1.333 4.333 -4 5', 'M19 11h-4a1 1 0 0 1 -1 -1v-3a1 1 0 0 1 1 -1h3a1 1 0 0 1 1 1v6c0 2.667 -1.333 4.333 -4 5'],
  callout:['M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0', 'M12 9h.01', 'M11 12h1v4h1'],
  toggle:['M9 6l6 6l-6 6'],
  code:['M7 8l-4 4l4 4', 'M17 8l4 4l-4 4', 'M14 4l-4 16'],
  divider:['M3 12l0 .01', 'M7 12l10 0', 'M21 12l0 .01'],
  table:['M3 5a2 2 0 0 1 2 -2h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14z', 'M3 10h18', 'M10 3v18'],
  columns:['M4 4m0 2a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2z', 'M12 4l0 16'],
  image:['M15 8h.01', 'M3 6a3 3 0 0 1 3 -3h12a3 3 0 0 1 3 3v12a3 3 0 0 1 -3 3h-12a3 3 0 0 1 -3 -3v-12z', 'M3 16l5 -5c.928 -.893 2.072 -.893 3 0l5 5', 'M14 14l1 -1c.928 -.893 2.072 -.893 3 0l3 3'],
  video:['M15 10l4.553 -2.276a1 1 0 0 1 1.447 .894v6.764a1 1 0 0 1 -1.447 .894l-4.553 -2.276v-4z', 'M3 6m0 2a2 2 0 0 1 2 -2h8a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-8a2 2 0 0 1 -2 -2z'],
  bookmark:['M18 7v14l-6 -4l-6 4v-14a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4z'],
};

// Puts a new block where the caret's line is: in place of an empty paragraph, else after the block. A block that
// ends the document gets a paragraph after it, and the caret moves into the new block (or past a decorator).
function $place(node) {
  const selection = $getSelection();
  if (!$isRangeSelection(selection)) return false;
  const block = $currentBlock(selection);
  if (!block) return false;
  if ($isParagraphNode(block) && !block.getTextContentSize()) block.replace(node); else block.insertAfter(node);
  if (!node.getNextSibling()) node.insertAfter($createParagraphNode());
  if ($isElementNode(node)) (node.getFirstDescendant() ?? node).selectStart();
  else node.getNextSibling().selectStart();
  return true;
}
const $convert = make => {
  const selection = $getSelection();
  if ($isRangeSelection(selection)) $setBlocksType(selection, make);
};

// `shortcut` is the Markdown that makes the same block. `run` receives the editor and, for rows that ask for a
// web address, the address; it runs inside editor.update after the typed `/query` has been removed.
export const SLASH_ITEMS = [
  {id:'text', section:'Basic blocks', label:'Text', icon:'text', shortcut:'', keywords:'paragraph plain',
    description:'Plain text.', run:() => $convert($createParagraphNode)},
  {id:'h1', section:'Basic blocks', label:'Heading 1', icon:'h1', shortcut:'#', keywords:'title large',
    description:'Large section heading.', run:() => $convert(() => $createHeadingNode('h1'))},
  {id:'h2', section:'Basic blocks', label:'Heading 2', icon:'h2', shortcut:'##', keywords:'subtitle medium',
    description:'Medium section heading.', run:() => $convert(() => $createHeadingNode('h2'))},
  {id:'h3', section:'Basic blocks', label:'Heading 3', icon:'h3', shortcut:'###', keywords:'small',
    description:'Small section heading.', run:() => $convert(() => $createHeadingNode('h3'))},
  {id:'bullets', section:'Basic blocks', label:'Bulleted list', icon:'list', shortcut:'-', keywords:'unordered ul',
    description:'A list with bullets.', command:INSERT_UNORDERED_LIST_COMMAND},
  {id:'numbers', section:'Basic blocks', label:'Numbered list', icon:'numbers', shortcut:'1.', keywords:'ordered ol',
    description:'A list with numbers.', command:INSERT_ORDERED_LIST_COMMAND},
  {id:'todo', section:'Basic blocks', label:'To-do', icon:'todo', shortcut:'[]', keywords:'task checkbox checklist',
    description:'A task with a check box.', command:INSERT_CHECK_LIST_COMMAND},
  {id:'quote', section:'Basic blocks', label:'Quote', icon:'quote', shortcut:'>', keywords:'blockquote',
    description:'A quoted passage.', run:() => $convert($createQuoteNode)},
  {id:'callout', section:'Basic blocks', label:'Callout', icon:'callout', shortcut:'> [!note]', keywords:'note tip warning',
    description:'A highlighted note with a title.', run:() => $insertCallout('note')},
  {id:'toggle', section:'Basic blocks', label:'Toggle', icon:'toggle', shortcut:'<details>', keywords:'collapse fold details',
    description:'A title that shows or hides its content.', run:() => $insertToggle()},
  {id:'code', section:'Basic blocks', label:'Code', icon:'code', shortcut:'```', keywords:'snippet monospace',
    description:'A block of code.', run:() => $convert($createCodeNode)},
  {id:'divider', section:'Basic blocks', label:'Divider', icon:'divider', shortcut:'---', keywords:'line rule separator hr',
    description:'A horizontal line.', run:() => $place($createHorizontalRuleNode())},
  {id:'table', section:'Layout', label:'Table', icon:'table', shortcut:'| a |', keywords:'grid rows columns',
    description:'A table with a header row.',
    run:() => {
      const table = $createTableNodeWithDimensions(2, 2, {rows:true, columns:false});
      $place(table);
    }},
  {id:'columns', section:'Layout', label:'2 columns', icon:'columns', shortcut:'<columns>', keywords:'two side by side layout',
    description:'Two blocks side by side.', run:() => $insertColumns(2)},
  {id:'image', section:'Media', label:'Image', icon:'image', shortcut:'![]()', keywords:'picture photo upload',
    description:'Upload an image.', needs:'image'},
  {id:'video', section:'Media', label:'Video', icon:'video', shortcut:'![](url)', keywords:'youtube vimeo loom embed',
    description:'Embed a YouTube, Vimeo, or Loom video.', needs:'video-url',
    run:(url) => $place(new VideoEmbedNode(url))},
  {id:'bookmark', section:'Media', label:'Link card', icon:'bookmark', shortcut:'<bookmark>', keywords:'bookmark web link preview',
    description:'A card that previews a web address.', needs:'url',
    run:(url) => $place(new BookmarkNode(url))},
];

const normalize = text => text.toLowerCase().replace(/[^a-z0-9]/g, '');
// Rows whose label or keywords contain the typed text, in menu order. An empty query lists every row.
export function filterItems(query, items = SLASH_ITEMS) {
  const wanted = normalize(query);
  if (!wanted) return items;
  return items.filter(item => normalize(`${item.label} ${item.keywords}`).includes(wanted));
}
// The next highlighted row for an arrow key, wrapping at both ends.
export const stepIndex = (index, delta, length) => length ? (index + delta + length) % length : -1;

// The slash command in the text before the caret: `/` starts the line or follows a space, and the query after it
// does not start with a space. Returns the query and where the `/` is, or null.
const SLASH = /(^|\s)\/(\S[^/]{0,30})?$/;
export function slashMatch(before) {
  const found = SLASH.exec(before);
  return found ? {query:found[2] || '', start:found.index + found[1].length} : null;
}

// The slash command at the caret, or null: a collapsed caret in a text node outside code and links.
function $slashAtCaret() {
  const selection = $getSelection();
  if (!$isRangeSelection(selection) || !selection.isCollapsed() || selection.anchor.type !== 'text') return null;
  const node = selection.anchor.getNode();
  if (!$isTextNode(node) || $findMatchingParent(node, parent => $isCodeNode(parent) || parent.getType() === 'link')) return null;
  const before = node.getTextContent().slice(0, selection.anchor.offset);
  const found = slashMatch(before);
  if (!found) return null;
  // The first text node of a line starts it; a later one follows the text before it, which must end in a space.
  if (found.start === 0 && node.getPreviousSibling() && !/\s$/.test(node.getPreviousSibling().getTextContent())) return null;
  return {...found, nodeKey:node.getKey(), blockKey:node.getTopLevelElementOrThrow().getKey()};
}

// Removes the typed `/query` and leaves the caret where it stood.
function $removeSlashText() {
  const found = $slashAtCaret();
  if (!found) return;
  const selection = $getSelection();
  const node = selection.anchor.getNode();
  const end = selection.anchor.offset;
  node.spliceText(found.start, end - found.start, '', true);
  if (node.getTextContentSize() === 0) {
    const previous = node.getPreviousSibling(); const parent = node.getParent();
    node.remove();
    if (previous) previous.selectEnd(); else parent.select(0, 0);
  }
}

// Runs a row: removes the typed text, then inserts the block. `url` is the address a row asked for.
export function $applySlashItem(editor, item, {url} = {}) {
  $removeSlashText();
  if (item.command) editor.dispatchCommand(item.command, undefined);
  else if (item.run) item.run(url);
}

// Where the menu goes: below the caret, or above it when there is not room, kept inside the viewport.
export function placeMenu(anchor, size, viewport, margin = 8, gap = 6) {
  const width = Math.min(size.width, viewport.width - margin * 2);
  const left = Math.max(margin, Math.min(anchor.left, viewport.width - width - margin));
  const roomBelow = viewport.height - anchor.bottom - gap - margin;
  const roomAbove = anchor.top - gap - margin;
  const below = size.height <= roomBelow || roomBelow >= roomAbove;
  const room = Math.max(120, below ? roomBelow : roomAbove);
  const height = Math.min(size.height, room);
  return {left, width, maxHeight:room, flipped:!below, top:below ? anchor.bottom + gap : anchor.top - gap - height};
}

function svg(name) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', 'stroke-width':'2',
    'stroke-linecap':'round', 'stroke-linejoin':'round', 'aria-hidden':'true', class:'icon'})) element.setAttribute(key, value);
  for (const d of ICONS[name]) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', d); element.append(path);
  }
  return element;
}

// options: onPickImage(snapshot) opens the image picker (the Image row is left out without it), captureSelection(),
// onError(error), and askUrl(item) which returns an address or null (the default asks with window.prompt).
export function registerSlashMenu(editor, root, options = {}) {
  const items = SLASH_ITEMS.filter(item => item.needs !== 'image' || options.onPickImage);
  const askUrl = options.askUrl || (item => window.prompt(item.id === 'video' ? 'Video URL (YouTube, Vimeo, or Loom)' : 'Link card URL'));
  const idBase = `slash-${Math.random().toString(36).slice(2, 8)}`;
  let menu = null; let rows = []; let matches = []; let active = 0; let current = null; let dismissed = null;

  const close = () => {
    if (!menu) return;
    menu.remove(); menu = null; rows = []; matches = []; current = null;
    root.removeAttribute('aria-activedescendant'); root.removeAttribute('aria-controls');
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('pointerdown', onPointer, true);
  };
  const mark = () => {
    rows.forEach((row, index) => {row.classList.toggle('active', index === active); row.setAttribute('aria-selected', String(index === active));});
    const row = rows[active];
    if (row) {root.setAttribute('aria-activedescendant', row.id); row.scrollIntoView?.({block:'nearest'});}
  };
  const caretRect = () => {
    const range = window.getSelection()?.rangeCount ? window.getSelection().getRangeAt(0) : null;
    let box = range ? range.getClientRects()[0] || range.getBoundingClientRect() : null;
    if (!box || (!box.width && !box.height && !box.top)) {
      const block = editor.getElementByKey(current.blockKey);
      box = block ? block.getBoundingClientRect() : root.getBoundingClientRect();
    }
    return box;
  };
  const position = () => {
    if (!menu) return;
    menu.style.maxHeight = ''; menu.style.width = '';
    const box = caretRect();
    const size = {width:menu.offsetWidth, height:menu.scrollHeight};
    const spot = placeMenu({left:box.left, top:box.top, bottom:box.bottom},
      size, {width:window.innerWidth, height:window.innerHeight});
    Object.assign(menu.style, {left:`${spot.left}px`, top:`${spot.top}px`, width:`${spot.width}px`, maxHeight:`${spot.maxHeight}px`});
    menu.dataset.flipped = String(spot.flipped);
  };
  const render = () => {
    if (!menu) {
      menu = document.createElement('div'); menu.className = 'note-slash-menu'; menu.id = `${idBase}-menu`;
      menu.setAttribute('role', 'listbox'); menu.setAttribute('aria-label', 'Insert a block');
      menu.addEventListener('mousedown', event => event.preventDefault());
      (root.closest('dialog') || document.body).append(menu);
      root.setAttribute('aria-controls', menu.id);
      document.addEventListener('keydown', onKey, true);
      document.addEventListener('pointerdown', onPointer, true);
    }
    menu.replaceChildren(); rows = [];
    let section = null;
    matches.forEach((item, index) => {
      if (item.section !== section) {
        section = item.section;
        const heading = document.createElement('small'); heading.setAttribute('role', 'presentation'); heading.textContent = section;
        menu.append(heading);
      }
      const row = document.createElement('div'); row.id = `${idBase}-${item.id}`; row.className = 'note-slash-row';
      row.setAttribute('role', 'option'); row.title = item.description;
      const label = document.createElement('span'); label.className = 'note-slash-label'; label.textContent = item.label;
      row.append(svg(item.icon), label);
      if (item.shortcut) {const hint = document.createElement('kbd'); hint.textContent = item.shortcut; row.append(hint);}
      row.addEventListener('mousemove', () => {if (active !== index) {active = index; mark();}});
      row.addEventListener('click', () => choose(item));
      menu.append(row); rows.push(row);
    });
    const foot = document.createElement('small'); foot.className = 'note-slash-foot'; foot.setAttribute('role', 'presentation');
    foot.textContent = 'Close menu (esc)'; menu.append(foot);
    mark(); position();
  };

  function choose(item) {
    const target = current;
    close();
    let url = null;
    if (item.needs === 'url' || item.needs === 'video-url') {
      // Ask while the caret is still in the text, then check the address before changing anything.
      url = httpUrl((askUrl(item) || '').trim());
      if (!url || (item.needs === 'video-url' && !videoEmbed(url))) {
        options.onError?.(new Error(item.needs === 'video-url' ? 'Enter a YouTube, Vimeo, or Loom address.' : 'Enter a valid http or https address.'));
        editor.focus(); return;
      }
    }
    dismissed = null;
    let snapshot = null;
    editor.update(() => {
      if (!target || !$slashAtCaret()) return;
      $applySlashItem(editor, item, {url});
      if (item.needs === 'image') snapshot = $getSelection()?.clone?.() ?? null;
    }, {onUpdate:() => {if (item.needs === 'image') options.onPickImage(snapshot);}});
    if (item.needs !== 'image') editor.focus();
  }
  function onKey(event) {
    if (!menu || event.isComposing) return;
    const take = () => {event.preventDefault(); event.stopPropagation();};
    if (event.key === 'Escape') {
      take(); dismissed = `${current.nodeKey}:${current.start}`; close();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      take(); active = stepIndex(active, event.key === 'ArrowDown' ? 1 : -1, matches.length); mark();
    } else if (event.key === 'Enter' && matches[active]) {take(); choose(matches[active]);}
  }
  function onPointer(event) {if (menu && !menu.contains(event.target)) close();}

  const unregister = [editor.registerUpdateListener(({editorState, tags}) => {
    if (tags.has('notes-load')) {close(); return;}
    let found = null;
    editorState.read(() => {found = $slashAtCaret();});
    if (!found) {dismissed = null; close(); return;}
    if (dismissed === `${found.nodeKey}:${found.start}`) return;
    const list = filterItems(found.query, items);
    if (!list.length) {close(); return;}
    const same = current && current.nodeKey === found.nodeKey && current.start === found.start;
    current = found; matches = list;
    active = same ? Math.min(active, list.length - 1) : 0;
    render();
  })];
  const onBlur = () => close();
  root.addEventListener('blur', onBlur);
  const onResize = () => position();
  window.addEventListener('resize', onResize);
  window.addEventListener('scroll', onResize, true);
  return () => {
    close(); unregister.forEach(release => release());
    root.removeEventListener('blur', onBlur); window.removeEventListener('resize', onResize);
    window.removeEventListener('scroll', onResize, true);
  };
}
