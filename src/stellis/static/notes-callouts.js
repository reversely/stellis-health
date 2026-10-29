// Callouts (#279): Obsidian-style `> [!type] Title` blocks with a multi-block body.
//   > [!warning] Check the dates
//   > Body paragraph.
//   >
//   > - a list inside the body
// Type words are stored in lowercase, so `[!WARNING]` is written back as `[!warning]`.
// A callout is a container (a shadow root, like a table cell) whose first child is the title paragraph and whose
// other children are the body. GitHub draws NOTE, TIP, IMPORTANT, WARNING and CAUTION natively; other type words
// show as block quotes there.
import {
  $createParagraphNode, $createTextNode, $getSelection, $isParagraphNode, $isRangeSelection, COMMAND_PRIORITY_HIGH,
  ElementNode, KEY_ENTER_COMMAND,
} from 'lexical';
import {$findMatchingParent} from '@lexical/utils';
import {$convertToMarkdownString, $generateNodesFromMarkdownString} from '@lexical/markdown';

export const CALLOUT_TYPES = ['note', 'tip', 'important', 'warning', 'caution'];
const HEADER = /^\s{0,3}>\s*\[!([A-Za-z][\w-]*)\][+-]?(?:[ \t]+(.*))?$/;
const QUOTE_LINE = /^\s{0,3}>/;
const TYPE_WORD = /^[A-Za-z][\w-]*$/;

const defaultTitle = kind => {
  const words = kind.toLowerCase().replace(/[-_]+/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
};

export class CalloutNode extends ElementNode {
  __kind;
  static getType() {return 'callout';}
  static clone(node) {return new CalloutNode(node.__kind, node.__key);}
  constructor(kind = 'note', key) {super(key); this.__kind = TYPE_WORD.test(kind) ? kind.toLowerCase() : 'note';}
  getKind() {return this.getLatest().__kind;}
  setKind(kind) {
    if (!TYPE_WORD.test(kind)) return this;
    const writable = this.getWritable(); writable.__kind = kind.toLowerCase(); return writable;
  }
  createDOM() {
    const element = document.createElement('div');
    element.className = 'note-callout';
    this.#decorate(element);
    return element;
  }
  updateDOM(prev, element) {
    if (prev.__kind !== this.__kind) this.#decorate(element);
    return false;
  }
  #decorate(element) {
    element.dataset.callout = this.__kind;
    element.style.setProperty('--callout-title', JSON.stringify(defaultTitle(this.__kind)));
  }
  // Copied HTML is a plain block quote tagged with the type.
  exportDOM() {
    const element = document.createElement('blockquote');
    element.dataset.callout = this.__kind.toLowerCase();
    return {element};
  }
  isShadowRoot() {return true;}
  canBeEmpty() {return false;}
  exportJSON() {return {...super.exportJSON(), type:'callout', version:1, kind:this.__kind};}
  static importJSON(data) {return new CalloutNode(data.kind);}
}

export const $isCalloutNode = node => node instanceof CalloutNode;

// The first child is always the title paragraph and there is always somewhere to type the body.
function $ensureShape(callout) {
  if (!$isParagraphNode(callout.getFirstChild())) callout.splice(0, 0, [$createParagraphNode()]);
  if (callout.getChildrenSize() < 2) callout.append($createParagraphNode());
}

export function $createCalloutNode(kind = 'note', title = '') {
  const callout = new CalloutNode(kind);
  const heading = $createParagraphNode();
  if (title) heading.append($createTextNode(title));
  callout.append(heading, $createParagraphNode());
  return callout;
}

// Wraps the top-level block holding the caret as the body of a new callout; inside a callout it changes the type.
export function $insertCallout(kind) {
  const selection = $getSelection();
  if (!$isRangeSelection(selection)) return;
  const existing = $findMatchingParent(selection.anchor.getNode(), $isCalloutNode);
  if (existing) {existing.setKind(kind); return;}
  const block = selection.anchor.getNode().getTopLevelElementOrThrow();
  const callout = $createCalloutNode(kind);
  block.insertBefore(callout);
  callout.getLastChild().replace(block);
  block.selectEnd();
}

export function calloutTransformer(getTransformers) {
  return {
    dependencies:[CalloutNode],
    export(node) {
      if (!$isCalloutNode(node)) return null;
      const inner = $convertToMarkdownString(getTransformers(), node);
      const split = inner.indexOf('\n');
      const title = (split < 0 ? inner : inner.slice(0, split)).trim();
      const body = (split < 0 ? '' : inner.slice(split + 1)).replace(/^\n+|\n+$/g, '');
      return [`> [!${node.getKind()}]${title ? ` ${title}` : ''}`,
        ...(body ? body.split('\n').map(line => line ? `> ${line}` : '>') : [])].join('\n');
    },
    regExpStart:HEADER,
    type:'multiline-element',
    replace() {},
    handleImportAfterStartMatch({lines, rootNode, startLineIndex, startMatch}) {
      const body = [];
      let last = startLineIndex;
      while (last + 1 < lines.length && QUOTE_LINE.test(lines[last + 1]) && !HEADER.test(lines[last + 1])) {
        last++;
        body.push(lines[last].replace(/^\s{0,3}> ?/, ''));
      }
      const callout = $createCalloutNode(startMatch[1]);
      const title = (startMatch[2] || '').trim();
      const transformers = getTransformers();
      const heading = title ? $generateNodesFromMarkdownString(title, transformers)[0] : null;
      if ($isParagraphNode(heading)) callout.getFirstChild().replace(heading);
      else if (title) callout.getFirstChild().append($createTextNode(title));
      const nodes = $generateNodesFromMarkdownString(body.join('\n'), transformers);
      if (nodes.length) {callout.getLastChild().remove(); callout.append(...nodes);}
      $ensureShape(callout);
      rootNode.append(callout);
      return [true, last];
    },
  };
}

export function registerCallouts(editor) {
  const unregister = [editor.registerNodeTransform(CalloutNode, $ensureShape)];
  // Enter in the title moves to the body; Enter on an empty last line leaves the callout.
  unregister.push(editor.registerCommand(KEY_ENTER_COMMAND, event => {
    const selection = $getSelection();
    if (!$isRangeSelection(selection) || !selection.isCollapsed() || event?.shiftKey) return false;
    const callout = $findMatchingParent(selection.anchor.getNode(), $isCalloutNode);
    if (!callout) return false;
    const block = $findMatchingParent(selection.anchor.getNode(), node => callout.is(node.getParent()));
    if (!block) return false;
    if (block.is(callout.getFirstChild())) {
      event?.preventDefault();
      callout.getChildAtIndex(1).selectStart();
      return true;
    }
    if ($isParagraphNode(block) && !block.getTextContentSize() && block.is(callout.getLastChild())) {
      event?.preventDefault();
      if (callout.getChildrenSize() > 2) block.remove();
      const after = $createParagraphNode();
      callout.insertAfter(after);
      after.select();
      return true;
    }
    return false;
  }, COMMAND_PRIORITY_HIGH));
  return () => unregister.forEach(release => release());
}
