import {
  $createParagraphNode, $createRangeSelection, $createTextNode, $getRoot, $getSelection,
  $isElementNode, $isRangeSelection, $setSelection, createEditor, DecoratorNode,
  COMMAND_PRIORITY_EDITOR, FORMAT_TEXT_COMMAND, REDO_COMMAND, UNDO_COMMAND,
} from 'lexical';
import {$insertNodeToNearestRoot} from '@lexical/utils';
import {$createHeadingNode, HeadingNode, QuoteNode, registerRichText} from '@lexical/rich-text';
import {CodeNode} from '@lexical/code-core';
import {$createHorizontalRuleNode, $isHorizontalRuleNode, HorizontalRuleNode} from '@lexical/extension';
import {
  INSERT_CHECK_LIST_COMMAND, INSERT_ORDERED_LIST_COMMAND, INSERT_UNORDERED_LIST_COMMAND,
  ListItemNode, ListNode, registerCheckList, registerList,
} from '@lexical/list';
import {$toggleLink, AutoLinkNode, LinkNode, TOGGLE_LINK_COMMAND} from '@lexical/link';
import {createEmptyHistoryState, registerHistory} from '@lexical/history';
import {$setBlocksType} from '@lexical/selection';
import {
  $createTableNodeWithDimensions, $deleteTableColumnAtSelection, $deleteTableRowAtSelection,
  $insertTableColumnAtSelection, $insertTableRowAtSelection, $isTableNode,
  INSERT_TABLE_COMMAND, TableCellNode, TableNode, TableRowNode,
  registerTablePlugin, registerTableSelectionObserver,
} from '@lexical/table';
import {
  $convertFromMarkdownString, $convertToMarkdownString,
  BOLD_ITALIC_STAR, BOLD_STAR, CODE, HEADING, INLINE_CODE, ITALIC_STAR, LINK,
  ORDERED_LIST, QUOTE, STRIKETHROUGH, UNORDERED_LIST, registerMarkdownShortcuts,
} from '@lexical/markdown';
import {CalloutNode, $insertCallout, calloutTransformer, registerCallouts} from './notes-callouts.js';
import {EMBED_NODES, $insertBookmark, configureEmbeds, embedTransformers, httpUrl, registerEmbedPaste} from './notes-embeds.js';
import {
  $insertColumns, $insertToggle, CONTAINER_NODES, columnsTransformer, registerContainers, toggleTransformer,
} from './notes-containers.js';
import {escapedTextTransformer} from './notes-escapes.js';
import {registerSlashMenu} from './notes-slash.js';
import {registerCleanCopy} from './notes-clipboard.js';
import {$captureDates, $collectDates, $restoreDates, registerTasks, taskListTransformer} from './notes-tasks.js';

// A line of `---`, `***` or `___` is a divider; it is written back as `---`.
const HR = {
  dependencies:[HorizontalRuleNode],
  export:node => $isHorizontalRuleNode(node) ? '---' : null,
  regExp:/^(---|\*\*\*|___)\s?$/,
  replace(parent, children, match, isImport) {
    const line = $createHorizontalRuleNode();
    if (isImport || parent.getNextSibling() != null) parent.replace(line);
    else parent.insertBefore(line);
    line.selectNext();
  },
  type:'element',
};

const IMAGE_MARKDOWN = /^!\[([^\]\n]*)\]\(stellis-image:([0-9a-f-]{36})\)$/i;

class MeetingImageNode extends DecoratorNode {
  __imageId; __caption; __src; __onOpen;
  static getType() {return 'meeting-image';}
  static clone(node) {return new MeetingImageNode(node.__imageId, node.__caption, node.__src, node.__onOpen, node.__key);}
  constructor(imageId, caption, src, onOpen, key) {
    super(key); this.__imageId = imageId; this.__caption = caption; this.__src = src; this.__onOpen = onOpen;
  }
  isInline() {return false;}
  createDOM() {
    const figure = document.createElement('figure'); figure.className = 'note-image';
    const button = document.createElement('button'); button.type = 'button'; button.contentEditable = 'false';
    button.setAttribute('aria-label', `Expand image${this.__caption ? `: ${this.__caption}` : ''}`);
    const image = document.createElement('img'); image.src = this.__src; image.alt = this.__caption || 'Meeting screenshot';
    button.append(image); button.addEventListener('click', () => this.__onOpen?.(this.__src, image.alt));
    figure.append(button);
    if (this.__caption) {const caption = document.createElement('figcaption'); caption.textContent = this.__caption; figure.append(caption);}
    return figure;
  }
  updateDOM() {return false;}
  // Copied HTML carries a plain image, not the expand button and its handlers.
  exportDOM() {
    const image = document.createElement('img'); image.src = this.__src; image.alt = this.__caption || 'Meeting screenshot';
    return {element:image};
  }
  getTextContent() {return this.__caption || 'Meeting screenshot';}
  exportJSON() {return {...super.exportJSON(), type:'meeting-image', version:1,
    imageId:this.__imageId, caption:this.__caption, src:this.__src};}
  static importJSON(data) {return new MeetingImageNode(data.imageId, data.caption, data.src, null);}
}

function parseTableRow(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith('|') || !trimmed.endsWith('|')) return null;
  const cells = []; let cell = ''; let escape = false;
  for (const character of trimmed.slice(1, -1)) {
    if (escape) {cell += character; escape = false;}
    else if (character === '\\') escape = true;
    else if (character === '|') {cells.push(cell.trim()); cell = '';}
    else cell += character;
  }
  cells.push(cell.trim());
  return cells;
}
function tableMarkdown(node) {
  if (!$isTableNode(node)) return null;
  const rows = node.getChildren().map(row => row.getChildren().map(cell =>
    cell.getTextContent().replace(/\r?\n/g, ' ').replace(/\\/g, '\\\\').replace(/\|/g, '\\|')));
  if (!rows.length || !rows[0].length) return '';
  const width = rows[0].length;
  const render = cells => `| ${cells.map(value => value || ' ').join(' | ')} |`;
  return [render(rows[0]), render(Array(width).fill('---')),
    ...rows.slice(1).map(row => render(Array.from({length:width}, (_, i) => row[i] || '')))].join('\n');
}

// Leaf blocks are the elements that directly hold text (paragraph, heading, list item, table cell paragraph).
function leafBlocks(node, out = []) {
  if (!$isElementNode(node)) return out;
  const children = node.getChildren();
  if (children.some(child => $isElementNode(child) && !child.isInline())) children.forEach(child => leafBlocks(child, out));
  else out.push(node);
  return out;
}
// A caret is kept as (leaf block number, characters before the end of the block's visible text).
// Measuring from the end survives both a task ID appended to the line and a prefix that the
// reload rewrites, such as a typed `[ ] ` becoming a check box. The server's ID is not visible text.
const TRAILING_TASK_ID = / \^t[0-9a-z]{6}$/;
const visibleLength = block => block.getTextContent().replace(TRAILING_TASK_ID, '').length;
function $capturePoint(point) {
  const leaves = leafBlocks($getRoot());
  let node = point.getNode();
  while (node && !leaves.includes(node)) node = node.getParent();
  if (!node) return null;
  let offset = 0;
  if (point.type === 'text') {
    for (const text of node.getAllTextNodes()) {
      if (text.is(point.getNode())) {offset += point.offset; break;}
      offset += text.getTextContentSize();
    }
  } else if (point.getNode().is(node)) {
    offset = node.getChildren().slice(0, point.offset).reduce((sum, child) => sum + child.getTextContentSize(), 0);
  }
  return {block:leaves.indexOf(node), fromEnd:Math.max(0, visibleLength(node) - offset)};
}
function $restorePoint(point, saved) {
  const leaves = leafBlocks($getRoot());
  const leaf = leaves[Math.min(saved.block, leaves.length - 1)];
  if (!leaf) return;
  let remaining = Math.max(0, visibleLength(leaf) - saved.fromEnd);
  for (const text of leaf.getAllTextNodes()) {
    const size = text.getTextContentSize();
    if (remaining <= size) {point.set(text.getKey(), remaining, 'text'); return;}
    remaining -= size;
  }
  point.set(leaf.getKey(), 0, 'element');
}

export const NOTES_NODES = [HeadingNode, QuoteNode, CodeNode, HorizontalRuleNode, ListNode, ListItemNode, LinkNode, AutoLinkNode,
  TableNode, TableRowNode, TableCellNode, MeetingImageNode, CalloutNode, ...CONTAINER_NODES, ...EMBED_NODES];
export const NOTES_THEME = {heading:{h1:'note-heading-one',h2:'note-heading-two',h3:'note-heading-three'},
  list:{ul:'note-bullets',ol:'note-numbers',listitem:'note-list-item',
    listitemChecked:'note-check-done',listitemUnchecked:'note-check-open'},
  text:{bold:'note-bold',italic:'note-italic',strikethrough:'note-strike',code:'note-code'},
  quote:'note-quote', code:'note-code-block', hr:'note-divider', table:'note-table',
  tableCell:'note-table-cell', tableCellHeader:'note-table-header'};

// The Markdown dialect shared by the editor and the headless round-trip test.
export function notesTransformers({imageUrlForId = () => '', onOpenImage = () => {}} = {}) {
  // A callout reads and writes its body with the same list, so it asks for it when it runs.
  const transformers = [
    calloutTransformer(() => transformers),
    toggleTransformer(() => transformers),
    columnsTransformer(() => transformers),
    {dependencies:[MeetingImageNode], export:node => node instanceof MeetingImageNode ?
      `![${node.__caption.replace(/[\]\\]/g, '')}](stellis-image:${node.__imageId})` : null,
      regExp:IMAGE_MARKDOWN, type:'element', replace(parent, children, match) {
        parent.replace(new MeetingImageNode(match[2], match[1], imageUrlForId(match[2]), onOpenImage));
      }},
    ...embedTransformers(),
    {dependencies:[TableNode, TableRowNode, TableCellNode], export:tableMarkdown,
      regExpStart:/^\|.+\|\s*$/, type:'multiline-element', replace() {},
      handleImportAfterStartMatch({lines, rootNode, startLineIndex}) {
        const header = parseTableRow(lines[startLineIndex]);
        const divider = parseTableRow(lines[startLineIndex + 1] || '');
        if (!header || !divider || header.length !== divider.length || !header.length || header.length > 32 ||
          !divider.every(cell => /^:?-{3,}:?$/.test(cell))) return null;
        const rows = [header]; let line = startLineIndex + 2;
        while (line < lines.length && rows.length < 256) {
          const cells = parseTableRow(lines[line]);
          if (!cells || cells.length !== header.length) break;
          rows.push(cells); line++;
        }
        const table = $createTableNodeWithDimensions(rows.length, header.length, true);
        table.getChildren().forEach((row, rowIndex) => row.getChildren().forEach((cell, colIndex) => {
          const paragraph = cell.getFirstChild(); paragraph.clear();
          paragraph.append($createTextNode(rows[rowIndex][colIndex]));
        }));
        rootNode.append(table);
        return [true, line - 1];
      }},
    // The task list must precede UNORDERED_LIST, or a saved `- [ ]` reloads as a bullet with literal brackets.
    HEADING, QUOTE, CODE, HR, taskListTransformer(), UNORDERED_LIST, ORDERED_LIST,
    INLINE_CODE, BOLD_ITALIC_STAR, BOLD_STAR, ITALIC_STAR, STRIKETHROUGH, LINK, escapedTextTransformer(),
  ];
  return transformers;
}

export function createNotesEditor(root, toolbar, onChange, onError, options = {}) {
  const imageUrlForId = options.imageUrlForId || (() => '');
  const onOpenImage = options.onOpenImage || (() => {});
  configureEmbeds({onOpenImage});
  const markdown = notesTransformers({imageUrlForId, onOpenImage});
  const editor = createEditor({
    namespace:'StellisNotes',
    nodes:NOTES_NODES,
    theme:NOTES_THEME,
    onError(error){onError(error);},
  });
  const unregister = [registerRichText(editor), registerList(editor), registerCheckList(editor),
    registerTablePlugin(editor), registerTableSelectionObserver(editor),
    editor.registerCommand(TOGGLE_LINK_COMMAND, payload => {$toggleLink(payload); return true;}, COMMAND_PRIORITY_EDITOR),
    registerHistory(editor, createEmptyHistoryState(), 300), registerMarkdownShortcuts(editor, markdown)];
  root.contentEditable = 'true'; root.setAttribute('role','textbox'); root.setAttribute('aria-multiline','true');
  editor.setRootElement(root);
  // Dates are tracked only where the server indexes to-dos (saved conversations).
  unregister.push(registerCallouts(editor));
  unregister.push(registerContainers(editor));
  unregister.push(registerEmbedPaste(editor, root));
  unregister.push(registerCleanCopy(editor, {nodes:NOTES_NODES, theme:NOTES_THEME, transformers:markdown}));
  unregister.push(registerTasks(editor, root, {dates:!!options.todoDates}));
  unregister.push(editor.registerUpdateListener(({editorState, dirtyElements, dirtyLeaves, tags}) => {
    if (tags.has('notes-load') || (!dirtyElements.size && !dirtyLeaves.size)) return;
    editorState.read(() => onChange($convertToMarkdownString(markdown), $collectDates()));
  }));
  const captureSelection = () => {
    let snapshot = null;
    editor.getEditorState().read(() => {const selected = $getSelection(); if ($isRangeSelection(selected)) snapshot = selected.clone();});
    return snapshot;
  };
  unregister.push(registerSlashMenu(editor, root, {onPickImage:options.onPickImage, onError}));
  const inTable = () => {
    const selected = $getSelection(); let node = selected?.anchor?.getNode();
    while (node && !$isTableNode(node)) node = node.getParent();
    return node && $isTableNode(node) ? node : null;
  };
  toolbar.addEventListener('mousedown', event => {if (event.target.closest('button')) event.preventDefault();});
  toolbar.addEventListener('click', event => {
    const action = event.target.closest('button')?.dataset.noteAction;
    if (!action) return;
    if (action === 'bold' || action === 'italic') editor.dispatchCommand(FORMAT_TEXT_COMMAND, action);
    else if (action === 'bullets') editor.dispatchCommand(INSERT_UNORDERED_LIST_COMMAND, undefined);
    else if (action === 'numbers') editor.dispatchCommand(INSERT_ORDERED_LIST_COMMAND, undefined);
    else if (action === 'checklist') editor.dispatchCommand(INSERT_CHECK_LIST_COMMAND, undefined);
    else if (action === 'undo') editor.dispatchCommand(UNDO_COMMAND, undefined);
    else if (action === 'redo') editor.dispatchCommand(REDO_COMMAND, undefined);
    else if (action === 'table') editor.dispatchCommand(INSERT_TABLE_COMMAND, {rows:'2',columns:'2',includeHeaders:{rows:true,columns:false}});
    else if (action === 'toggle') editor.update(() => $insertToggle());
    else if (action === 'columns') editor.update(() => $insertColumns(2));
    else if (action.startsWith('callout-')) editor.update(() => $insertCallout(action.slice(8)));
    else if (action === 'bookmark') {
      const url = httpUrl((window.prompt('Link card URL') || '').trim());
      if (url) editor.update(() => $insertBookmark(url)); else onError(new Error('Enter a valid http or https address.'));
    }
    else if (action === 'image') options.onPickImage?.(captureSelection());
    else if (['add-row','add-column','delete-row','delete-column','delete-table'].includes(action)) editor.update(() => {
      const table = inTable();
      if (!table) {onError(new Error('Place the cursor in a table.')); return;}
      if (action === 'add-row') {
        if (table.getChildrenSize() >= 256) onError(new Error('Table has reached 256 rows.'));
        else $insertTableRowAtSelection();
      } else if (action === 'add-column') {
        if (table.getFirstChild()?.getChildrenSize() >= 32) onError(new Error('Table has reached 32 columns.'));
        else $insertTableColumnAtSelection();
      }
      else if (action === 'delete-row') $deleteTableRowAtSelection();
      else if (action === 'delete-column') $deleteTableColumnAtSelection();
      else {const paragraph = $createParagraphNode(); table.insertAfter(paragraph); table.remove(); paragraph.select();}
    });
    else if (action === 'heading' || action === 'paragraph') editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) $setBlocksType(selection, () =>
        action === 'heading' ? $createHeadingNode('h2') : $createParagraphNode());
    });
    else if (action === 'link') {
      const answer = window.prompt('Link URL');
      if (!answer) return;
      try {const url = new URL(answer);
        if (!['https:','http:','mailto:'].includes(url.protocol)) throw new Error('Unsupported link');
        editor.dispatchCommand(TOGGLE_LINK_COMMAND, url.href);
      } catch {onError(new Error('Enter a valid link URL.'));}
    }
    if (action !== 'image') editor.focus();
    event.target.closest('.notes-table-actions')?.removeAttribute('open');
  });
  return {
    // keepSelection puts the caret back where it was after the reload, for a server-side text change.
    // dates is the server's {id: {due_date, due_time}}; unsaved dates on the same text are carried across a keepSelection reload.
    setMarkdown(value, {keepSelection = false, dates = {}, onLoaded} = {}) {
      editor.update(() => {
        const carried = keepSelection && options.todoDates ? $captureDates() : [];
        const before = keepSelection ? $getSelection() : null;
        const anchor = $isRangeSelection(before) ? $capturePoint(before.anchor) : null;
        const focus = anchor && $isRangeSelection(before) ? $capturePoint(before.focus) : null;
        $convertFromMarkdownString(value || '', markdown);
        if (options.todoDates) $restoreDates(dates, carried);
        if (!anchor || !focus) return;
        const selection = $createRangeSelection();
        $restorePoint(selection.anchor, anchor); $restorePoint(selection.focus, focus);
        $setSelection(selection);
      }, {tag:keepSelection ? ['notes-load', 'history-merge'] : 'notes-load', onUpdate:onLoaded});
    },
    getMarkdown() {let value=''; editor.getEditorState().read(() => {value=$convertToMarkdownString(markdown);}); return value;},
    getTodoDates() {let value={}; editor.getEditorState().read(() => {value=$collectDates();}); return value;},
    captureSelection,
    insertImage(imageId, caption = '', snapshot = null) {
      editor.update(() => {
        if (snapshot) $setSelection(snapshot);
        const image = new MeetingImageNode(imageId, caption, imageUrlForId(imageId), onOpenImage);
        $insertNodeToNearestRoot(image);
        const next = $createParagraphNode(); image.insertAfter(next); next.select();
      });
    },
    focus(){editor.focus();},
    destroy(){for(const release of unregister) release(); editor.setRootElement(null);},
  };
}
