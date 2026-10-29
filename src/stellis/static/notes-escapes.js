// Backslash escapes on export (#320). Lexical's Markdown export escapes characters such as `*` inside a line but
// writes a block marker at the start of plain text as it is, so a paragraph that reads "[ ] x" is saved as `[ ] x`
// and comes back as a to-do. Plain text that starts a line (or a list or quote line) and would be read as a block
// or as one of the dialect's tags gets a backslash before its first character; the importer drops it again.
import {$isElementNode, $isLineBreakNode, $isTextNode} from 'lexical';
import {$isCodeNode} from '@lexical/code-core';
import {$isListItemNode} from '@lexical/list';
import {$isQuoteNode} from '@lexical/rich-text';
import {$isCalloutNode} from './notes-callouts.js';
import {$isToggleNode} from './notes-containers.js';

const BLOCK_START = [
  /^#{1,6}(?=\s|$)/,                     // heading
  /^[-+*](?=\s|$)/,                      // bullet
  /^([-*_])\1{2,}\s*$/,                  // divider
  /^>/,                                  // quote or callout
  /^\[[ xX-]?\](?=\s|$)/,                // to-do
  /^\d+(?=[.)](\s|$))/,                  // numbered list: the escape goes before the dot
  /^<\/?(?:details|summary|bookmark|columns?)(?=[\s>/]|$)/i, // dialect tags
];

// The text with a backslash before the character that makes it a block, or the text itself.
export function escapeLineStart(text) {
  const [, space, rest] = /^(\s*)([\s\S]*)$/.exec(text);
  for (const pattern of BLOCK_START) {
    const found = pattern.exec(rest);
    if (!found) continue;
    // A numbered marker escapes its dot (`1\.`); every other marker escapes its first character.
    const at = /^\d/.test(rest) ? found[0].length : 0;
    return `${space}${rest.slice(0, at)}\\${rest.slice(at)}`;
  }
  return text;
}

// A line starts at the first child of a paragraph, list item or quote, and after a line break. A callout or toggle
// title is not a line of its own (it sits after `> [!note]` or inside `<summary>`), and code is written as it is.
function $startsLine(node) {
  const before = node.getPreviousSibling();
  if (before && !$isLineBreakNode(before)) return false;
  const parent = node.getParent();
  if (!$isElementNode(parent) || $isCodeNode(parent) || parent.getType() === 'heading') return false;
  const grand = parent.getParent();
  if (!before && ($isCalloutNode(grand) || $isToggleNode(grand)) && grand.getFirstChild()?.is(parent)) return false;
  return parent.getType() === 'paragraph' || $isListItemNode(parent) || $isQuoteNode(parent);
}

// A text-match transformer that only exports: it has no import or typing behaviour (its patterns never match).
export function escapedTextTransformer() {
  return {
    dependencies:[], importRegExp:/(?!)/, regExp:/(?!)/, trigger:'\u0000', type:'text-match',
    export(node, exportChildren, exportFormat) {
      if (!$isTextNode(node) || node.getFormat() !== 0 || !$startsLine(node)) return null;
      return escapeLineStart(exportFormat(node, node.getTextContent()));
    },
  };
}
