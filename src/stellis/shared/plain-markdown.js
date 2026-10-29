// JS twin of todos.plain_markdown: strips task IDs, turns bookmarks into links, and unwraps columns,
// outside code fences. Shared cases: tests/fixtures/markdown_plain.json.
(function (root) {
  const FENCE = /^ {0,3}(`{3,}|~{3,})/;
  const ID_SUFFIX = /[ ]\^t[0-9a-z]{6}(?=[ \t]*\r?$)/gm;
  const BOOKMARK = /<bookmark\s+url="([^"]*)"\s*>([\s\S]*?)<\/bookmark>/g;
  const COLUMN_TAG = /<\/?columns?(?:\s[^>]*)?>/g;
  const COLUMN_LINE = /^<\/?columns?(?:\s[^>]*)?>$/;

  function fenceFlags(lines) {
    const flags = [];
    let opener = null;
    for (const line of lines) {
      const match = FENCE.exec(line);
      if (opener === null) {
        flags.push(!!match);
        if (match) opener = match[1];
      } else {
        flags.push(true);
        if (match && match[1][0] === opener[0] && match[1].length >= opener.length && !line.trim().replaceAll(opener[0], '')) opener = null;
      }
    }
    return flags;
  }

  function plainRun(text) {
    text = text.replace(ID_SUFFIX, '');
    text = text.replace(BOOKMARK, (_, url, label) => label.trim() ? `[${label.trim()}](${url})` : url);
    const kept = [];
    let between = false;
    for (const line of text.split('\n')) {
      const tag = line.trim();
      if (COLUMN_LINE.test(tag)) {
        if (tag.startsWith('</column>')) between = true;
        else if (tag.startsWith('<column') && !tag.startsWith('<columns') && between && kept.length && kept.at(-1) !== '') {
          kept.push('');
          between = false;
        }
        continue;
      }
      between = false;
      kept.push(line.replace(COLUMN_TAG, ''));
    }
    return kept.join('\n');
  }

  function plainMarkdown(markdown) {
    const lines = markdown.split('\n');
    const flags = fenceFlags(lines);
    const out = [];
    let run = [];
    const flush = () => {if (run.length) out.push(...plainRun(run.join('\n')).split('\n')); run = [];};
    lines.forEach((line, index) => {
      if (flags[index]) {flush(); out.push(line);} else run.push(line);
    });
    flush();
    return out.join('\n');
  }

  const api = {plainMarkdown};
  root.StellisPlainMarkdown = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
