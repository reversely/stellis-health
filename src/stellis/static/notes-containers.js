// Toggles and columns (#280): container blocks that hold any other blocks, to-do lines included.
//   <details>
//   <summary>Title</summary>
//
//   Any blocks.
//
//   </details>
//
//   <columns>
//   <column>
//   Any blocks.
//   </column>
//   <column>
//   Any blocks.
//   </column>
//   </columns>
// A toggle and a column are shadow roots, like a table cell, so the caret and the selection stay inside them.
// A toggle's first child is its title paragraph; the other children are its body. Open or closed is a view
// state kept on the element, not in the Markdown: a saved toggle always reloads open.
// The tags are matched line by line and counted for nesting, and lines inside code fences are skipped.
import {
  $createParagraphNode, $createTextNode, $getSelection, $isElementNode, $isParagraphNode, $isRangeSelection,
  COMMAND_PRIORITY_HIGH, ElementNode, INSERT_LINE_BREAK_COMMAND, KEY_ARROW_DOWN_COMMAND, KEY_BACKSPACE_COMMAND,
  KEY_ENTER_COMMAND,
} from 'lexical';
import {$findMatchingParent} from '@lexical/utils';
import {$convertToMarkdownString, $generateNodesFromMarkdownString} from '@lexical/markdown';
import {$isTableNode} from '@lexical/table';

const DETAILS_OPEN = /^\s{0,3}<details(?:\s+open)?>\s*(?:<summary>(.*?)<\/summary>\s*)?$/;
const DETAILS_OPEN_ANY = /^\s{0,3}<details(?:\s+open)?>/;
const DETAILS_CLOSE = /^\s{0,3}<\/details>\s*$/;
const SUMMARY_LINE = /^\s*<summary>(.*?)<\/summary>\s*$/;
const COLUMNS_OPEN = /^\s{0,3}<columns>\s*$/;
const COLUMNS_CLOSE = /^\s{0,3}<\/columns>\s*$/;
const COLUMN_OPEN = /^\s{0,3}<column>\s*$/;
const COLUMN_CLOSE = /^\s{0,3}<\/column>\s*$/;
const FENCE = /^\s{0,3}(```|~~~)/;
const MAX_COLUMNS = 6;

// Index of the line that closes the tag opened on `start`, or -1. Fenced code and nested pairs are skipped.
export function findClose(lines, start, isOpen, isClose) {
  let depth = 1; let fence = null;
  for (let index = start + 1; index < lines.length; index++) {
    const line = lines[index];
    const mark = FENCE.exec(line)?.[1];
    if (fence) {if (mark === fence) fence = null; continue;}
    if (mark) {fence = mark; continue;}
    if (isOpen(line)) depth++;
    else if (isClose(line) && --depth === 0) return index;
  }
  return -1;
}
const trimBlank = lines => {
  let from = 0; let to = lines.length;
  while (from < to && !lines[from].trim()) from++;
  while (to > from && !lines[to - 1].trim()) to--;
  return lines.slice(from, to);
};
// The title sits inside `<summary>`, so a `<` in it is written as `&lt;` and read back.
const escapeTitle = title => title.replace(/&(?=(?:lt|amp);)/g, '&amp;').replace(/</g, '&lt;');
const unescapeTitle = title => title.replace(/&(lt|amp);/g, (_, name) => name === 'lt' ? '<' : '&');

export class ToggleNode extends ElementNode {
  static getType() {return 'toggle';}
  static clone(node) {return new ToggleNode(node.__key);}
  createDOM() {
    const element = document.createElement('div');
    element.className = 'note-toggle';
    return element;
  }
  updateDOM() {return false;}
  isShadowRoot() {return true;}
  canBeEmpty() {return false;}
  exportJSON() {return {...super.exportJSON(), type:'toggle', version:1};}
  static importJSON() {return new ToggleNode();}
}
export class ColumnsNode extends ElementNode {
  static getType() {return 'columns';}
  static clone(node) {return new ColumnsNode(node.__key);}
  createDOM() {
    const element = document.createElement('div');
    element.className = 'note-columns';
    return element;
  }
  updateDOM() {return false;}
  canBeEmpty() {return false;}
  exportJSON() {return {...super.exportJSON(), type:'columns', version:1};}
  static importJSON() {return new ColumnsNode();}
}
export class ColumnNode extends ElementNode {
  static getType() {return 'column';}
  static clone(node) {return new ColumnNode(node.__key);}
  createDOM() {
    const element = document.createElement('div');
    element.className = 'note-column';
    return element;
  }
  updateDOM() {return false;}
  isShadowRoot() {return true;}
  canBeEmpty() {return false;}
  exportJSON() {return {...super.exportJSON(), type:'column', version:1};}
  static importJSON() {return new ColumnNode();}
}
export const CONTAINER_NODES = [ToggleNode, ColumnsNode, ColumnNode];
export const $isToggleNode = node => node instanceof ToggleNode;
export const $isColumnsNode = node => node instanceof ColumnsNode;
export const $isColumnNode = node => node instanceof ColumnNode;

// A toggle always has a title paragraph first and somewhere to type the body.
function $ensureToggle(toggle) {
  if (!$isParagraphNode(toggle.getFirstChild())) toggle.splice(0, 0, [$createParagraphNode()]);
  if (toggle.getChildrenSize() < 2) toggle.append($createParagraphNode());
}
// Columns hold only columns: stray children are moved into a column of their own.
function $ensureColumns(columns) {
  for (const child of columns.getChildren()) {
    if ($isColumnNode(child)) continue;
    const column = new ColumnNode(); child.insertBefore(column); column.append(child);
  }
}

export function $createToggleNode(title = '') {
  const toggle = new ToggleNode();
  const heading = $createParagraphNode();
  if (title) heading.append($createTextNode(title));
  toggle.append(heading, $createParagraphNode());
  return toggle;
}
export function $createColumnsNode(count = 2) {
  const columns = new ColumnsNode();
  for (let index = 0; index < count; index++) {
    const column = new ColumnNode(); column.append($createParagraphNode()); columns.append(column);
  }
  return columns;
}

// The top-level block (or the block of the enclosing container) that holds the caret. A table counts as one block.
export function $currentBlock(selection) {
  const node = selection.anchor.getNode();
  const table = $findMatchingParent(node, $isTableNode);
  const start = table || node;
  return $findMatchingParent(start, parent => {
    const above = parent.getParent();
    return above != null && (above.isShadowRoot?.() || above.getType() === 'root');
  });
}
// Puts a new container after the caret's block, or in place of it when it is an empty paragraph.
function $insertBlock(container) {
  const selection = $getSelection();
  if (!$isRangeSelection(selection)) return false;
  const block = $currentBlock(selection);
  if (!block) return false;
  if ($isParagraphNode(block) && !block.getTextContentSize()) block.replace(container);
  else block.insertAfter(container);
  container.getFirstDescendant()?.selectStart?.() ?? container.selectStart();
  return true;
}
export const $insertToggle = () => $insertBlock($createToggleNode());
export const $insertColumns = (count = 2) => $insertBlock($createColumnsNode(count));

export function toggleTransformer(getTransformers) {
  return {
    dependencies:[ToggleNode],
    export(node) {
      if (!$isToggleNode(node)) return null;
      const inner = $convertToMarkdownString(getTransformers(), node);
      const split = inner.indexOf('\n');
      const title = escapeTitle((split < 0 ? inner : inner.slice(0, split)).trim());
      const body = (split < 0 ? '' : inner.slice(split + 1)).replace(/^\n+|\n+$/g, '');
      return ['<details>', `<summary>${title}</summary>`, ...(body ? ['', body, ''] : []), '</details>'].join('\n');
    },
    regExpStart:DETAILS_OPEN,
    type:'multiline-element',
    replace() {},
    handleImportAfterStartMatch({lines, rootNode, startLineIndex, startMatch}) {
      const close = findClose(lines, startLineIndex, line => DETAILS_OPEN_ANY.test(line), line => DETAILS_CLOSE.test(line));
      if (close < 0) return null;
      let title = startMatch[1]; let from = startLineIndex + 1;
      if (title === undefined && from < close) {
        const summary = SUMMARY_LINE.exec(lines[from]);
        if (summary) {title = summary[1]; from++;}
      }
      const transformers = getTransformers();
      const toggle = new ToggleNode();
      toggle.append($createParagraphNode());
      title = unescapeTitle((title || '').trim());
      const heading = title ? $generateNodesFromMarkdownString(title, transformers)[0] : null;
      if ($isParagraphNode(heading)) toggle.getFirstChild().replace(heading);
      else if (title) toggle.getFirstChild().append($createTextNode(title));
      const nodes = $generateNodesFromMarkdownString(trimBlank(lines.slice(from, close)).join('\n'), transformers);
      toggle.append(...(nodes.length ? nodes : [$createParagraphNode()]));
      $ensureToggle(toggle);
      rootNode.append(toggle);
      return [true, close];
    },
  };
}

export function columnsTransformer(getTransformers) {
  return {
    dependencies:[ColumnsNode, ColumnNode],
    export(node) {
      if (!$isColumnsNode(node)) return null;
      const transformers = getTransformers();
      const columns = node.getChildren().filter($isColumnNode).map(column => {
        const body = $convertToMarkdownString(transformers, column).replace(/^\n+|\n+$/g, '');
        return ['<column>', ...(body ? [body] : []), '</column>'].join('\n');
      });
      return ['<columns>', ...columns, '</columns>'].join('\n');
    },
    regExpStart:COLUMNS_OPEN,
    type:'multiline-element',
    replace() {},
    handleImportAfterStartMatch({lines, rootNode, startLineIndex}) {
      const close = findClose(lines, startLineIndex, line => COLUMNS_OPEN.test(line), line => COLUMNS_CLOSE.test(line));
      if (close < 0) return null;
      const bodies = [];
      for (let index = startLineIndex + 1; index < close; index++) {
        if (!lines[index].trim()) continue;
        if (!COLUMN_OPEN.test(lines[index])) return null; // Text outside a column stays as text, so nothing is lost.
        const end = findClose(lines, index, line => COLUMN_OPEN.test(line), line => COLUMN_CLOSE.test(line));
        if (end < 0 || end >= close) return null;
        bodies.push(trimBlank(lines.slice(index + 1, end)).join('\n'));
        index = end;
      }
      if (!bodies.length || bodies.length > MAX_COLUMNS) return null;
      const transformers = getTransformers();
      const columns = new ColumnsNode();
      for (const body of bodies) {
        const column = new ColumnNode();
        const nodes = $generateNodesFromMarkdownString(body, transformers);
        column.append(...(nodes.length ? nodes : [$createParagraphNode()]));
        columns.append(column);
      }
      rootNode.append(columns);
      return [true, close];
    },
  };
}

// Where the caret is inside the nearest toggle or column: {container, block}, block being the container's own child.
function $inContainer(selection) {
  const container = $findMatchingParent(selection.anchor.getNode(), node => $isToggleNode(node) || $isColumnNode(node));
  if (!container) return null;
  const block = $findMatchingParent(selection.anchor.getNode(), node => container.is(node.getParent()));
  return block ? {container, block} : null;
}
const $isAtEndOf = (selection, block) => {
  const last = block.getLastDescendant();
  if (!last) return true;
  const {anchor} = selection;
  return anchor.getNode().is(last) && (anchor.type === 'element' || anchor.offset >= last.getTextContentSize());
};
// What leaves a container: the toggle itself, or the column group.
const exitTarget = container => $isColumnNode(container) ? container.getParent() : container;
function $leave(container) {
  const target = exitTarget(container);
  const next = target.getNextSibling();
  if ($isElementNode(next)) {next.selectStart(); return;}
  const after = $createParagraphNode();
  target.insertAfter(after);
  after.select();
}
const $wholeEmpty = node => node.getTextContent().trim() === '';

export function setToggleOpen(editor, toggle, open) {
  const element = editor.getElementByKey(toggle.getKey());
  if (!element) return;
  if (open) element.removeAttribute('data-collapsed'); else element.setAttribute('data-collapsed', '');
}

export function registerContainers(editor) {
  const unregister = [
    editor.registerNodeTransform(ToggleNode, $ensureToggle),
    editor.registerNodeTransform(ColumnsNode, $ensureColumns),
  ];
  const high = (command, handler) => unregister.push(editor.registerCommand(command, handler, COMMAND_PRIORITY_HIGH));

  high(KEY_ENTER_COMMAND, event => {
    const selection = $getSelection();
    if (!$isRangeSelection(selection) || !selection.isCollapsed() || event?.shiftKey) return false;
    const found = $inContainer(selection);
    if (!found) return false;
    const {container, block} = found;
    if ($isToggleNode(container) && (event?.metaKey || event?.ctrlKey)) {
      event.preventDefault();
      const open = editor.getElementByKey(container.getKey())?.hasAttribute('data-collapsed');
      setToggleOpen(editor, container, open);
      return true;
    }
    if ($isToggleNode(container) && block.is(container.getFirstChild())) {
      event?.preventDefault();
      setToggleOpen(editor, container, true);
      container.getChildAtIndex(1).selectStart();
      return true;
    }
    // Enter on an empty last line leaves the container. A column keeps one line, so a fresh column can be typed in.
    if ($isParagraphNode(block) && !block.getTextContentSize() && block.is(container.getLastChild()) &&
      ($isToggleNode(container) || container.getChildrenSize() > 1)) {
      event?.preventDefault();
      if (container.getChildrenSize() > ($isToggleNode(container) ? 2 : 1)) block.remove();
      $leave(container);
      return true;
    }
    return false;
  });

  // A line break in a toggle title would split the `<summary>` line, so it starts the body instead.
  high(INSERT_LINE_BREAK_COMMAND, () => {
    const selection = $getSelection();
    if (!$isRangeSelection(selection)) return false;
    const found = $inContainer(selection);
    if (!found || !$isToggleNode(found.container) || !found.block.is(found.container.getFirstChild())) return false;
    found.container.getChildAtIndex(1).selectStart();
    return true;
  });

  high(KEY_ARROW_DOWN_COMMAND, event => {
    const selection = $getSelection();
    if (!$isRangeSelection(selection) || !selection.isCollapsed() || event?.shiftKey) return false;
    const found = $inContainer(selection);
    if (!found || !found.block.is(found.container.getLastChild()) || !$isAtEndOf(selection, found.block)) return false;
    event?.preventDefault();
    $leave(found.container);
    return true;
  });

  // Backspace in an empty container takes it away: an empty toggle, or an empty column (or all of the columns).
  high(KEY_BACKSPACE_COMMAND, event => {
    const selection = $getSelection();
    if (!$isRangeSelection(selection) || !selection.isCollapsed()) return false;
    const found = $inContainer(selection);
    if (!found) return false;
    const {container, block} = found;
    if (!$wholeEmpty(container) || !block.is(container.getFirstChild())) return false;
    event?.preventDefault();
    if ($isToggleNode(container)) {
      const paragraph = $createParagraphNode(); container.replace(paragraph); paragraph.select();
      return true;
    }
    const columns = container.getParent();
    if (columns.getChildrenSize() > 1 && !$wholeEmpty(columns)) {
      const previous = container.getPreviousSibling(); const next = container.getNextSibling();
      container.remove();
      if (previous) previous.selectEnd(); else next.selectStart();
      return true;
    }
    const paragraph = $createParagraphNode(); columns.replace(paragraph); paragraph.select();
    return true;
  });

  // The chevron is drawn in the left padding of the title line; clicking it opens or closes the toggle.
  unregister.push(editor.registerRootListener((root, previous) => {
    previous?.removeEventListener('click', onToggleClick);
    root?.addEventListener('click', onToggleClick);
  }));
  return () => unregister.forEach(release => release());
}

function onToggleClick(event) {
  const title = event.target;
  if (!(title instanceof HTMLElement) || !title.parentElement?.classList.contains('note-toggle') ||
    title !== title.parentElement.firstElementChild) return;
  if (event.clientX - title.getBoundingClientRect().left > 26) return;
  title.parentElement.toggleAttribute('data-collapsed');
}
