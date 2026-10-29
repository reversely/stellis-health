// Turns an AI summary into Markdown without letting model text become structure (#278). Each item is one
// line of plain text: line breaks are folded to spaces and every character that Markdown or the notes dialect
// reads as syntax gets a backslash, so `- [ ] x`, `<details>`, a heading, a table row, a fence or an image
// reference inside an item stays literal text. The same characters are the ones `todos.plain_markdown` unescapes.
(function (root) {
  const SYNTAX = /[\\`*_~[\]()#>!|<-]/g;

  function escapeItemText(value) {
    const text = String(value ?? '').replace(/\s+/g, ' ').trim()
      .replace(SYNTAX, character => `\\${character}`)
      .replace(/^(\d+)([.)])/, '$1\\$2')  // a leading `1.` starts a nested list
      .replace(/^\+/, '\\+');
    return text;
  }

  function summaryMarkdown(result, fields) {
    return fields.filter(([field]) => Array.isArray(result?.[field]) && result[field].length)
      .map(([field, title]) => `## ${title}\n\n${result[field].map(item => `- ${escapeItemText(item?.text)}`).join('\n')}`)
      .join('\n\n');
  }

  const api = {escapeItemText, summaryMarkdown};
  root.StellisSummaryText = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
