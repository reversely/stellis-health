// Clean copy and cut (#277): text/plain is the note's Markdown without task IDs, text/html stays for rich paste targets.
import {
  $createParagraphNode, $getRoot, $getSelection, $isRangeSelection, $isNodeSelection,
  COMMAND_PRIORITY_HIGH, COPY_COMMAND, CUT_COMMAND, createEditor,
} from 'lexical';
import {
  $generateJSONFromSelectedNodes, $generateNodesFromSerializedNodes, $getClipboardDataFromSelection,
  setLexicalClipboardDataTransfer,
} from '@lexical/clipboard';
import {$convertToMarkdownString} from '@lexical/markdown';
import plain from '../shared/plain-markdown.js';

// The selection is replayed into a scratch editor so the same transformers that save the note write the copy.
export function selectionMarkdown(editor, selection, {nodes, theme, transformers}) {
  const {nodes: serialized} = $generateJSONFromSelectedNodes(editor, selection);
  const scratch = createEditor({namespace:'StellisCopy', nodes, theme, onError(error) {throw error;}});
  let markdown = '';
  scratch.update(() => {
    const root = $getRoot(); root.clear();
    let paragraph = null;
    for (const node of $generateNodesFromSerializedNodes(serialized)) {
      if (!node.isInline?.()) {root.append(node); paragraph = null;}
      else {paragraph ||= $createParagraphNode(); if (!paragraph.isAttached()) root.append(paragraph); paragraph.append(node);}
    }
    markdown = $convertToMarkdownString(transformers);
  }, {discrete:true});
  return plain.plainMarkdown(markdown);
}

export function registerCleanCopy(editor, options) {
  const write = (event, cut) => {
    const selection = $getSelection();
    if (!event?.clipboardData || !(($isRangeSelection(selection) && !selection.isCollapsed()) || $isNodeSelection(selection))) return false;
    event.preventDefault();
    const data = $getClipboardDataFromSelection(selection);
    data['text/plain'] = selectionMarkdown(editor, selection, options);
    setLexicalClipboardDataTransfer(event.clipboardData, data);
    if (cut) {
      if ($isRangeSelection(selection)) selection.removeText();
      else selection.getNodes().forEach(node => node.remove());
    }
    return true;
  };
  const release = [editor.registerCommand(COPY_COMMAND, event => write(event, false), COMMAND_PRIORITY_HIGH),
    editor.registerCommand(CUT_COMMAND, event => write(event, true), COMMAND_PRIORITY_HIGH)];
  return () => release.forEach(fn => fn());
}
