// Pure helpers for the notebook page: the items table, its filters, the latest summary, and colour checks.
(function (root) {
  const HEX = /^#[0-9A-Fa-f]{6}$/;
  const DARK_INK = '#27222B';
  const LIGHT_INK = '#F7F5F6';
  const FILTERS = [
    {id: 'all', label: 'All', icon: 'list', empty: 'No records yet'},
    {id: 'conversation', label: 'Meetings', icon: 'microphone', empty: 'No meetings yet'},
    {id: 'page', label: 'Notes', icon: 'file-text', empty: 'No notes yet'},
    {id: 'document', label: 'Documents', icon: 'file-text', empty: 'No documents yet'},
  ];

  const isHexColour = value => typeof value === 'string' && HEX.test(value);

  function luminance(hex) {
    const [r, g, b] = [1, 3, 5].map(at => {
      const channel = parseInt(hex.slice(at, at + 2), 16) / 255;
      return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  const contrast = (a, b) => {
    const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (high + 0.05) / (low + 0.05);
  };
  const houseInk = hex => contrast(hex, DARK_INK) > contrast(hex, LIGHT_INK) ? DARK_INK : LIGHT_INK;

  /**
   * The card fill for a custom colour. A mid-tone that neither house ink reaches 4.5:1 on is
   * deepened a little until white does, so the text is never pure black (owner, 2026-10-27).
   */
  function fillFor(hex) {
    if (!isHexColour(hex) || contrast(hex, houseInk(hex)) >= 4.5) return hex;
    let rgb = [1, 3, 5].map(at => parseInt(hex.slice(at, at + 2), 16));
    let fill = hex;
    while (contrast(fill, '#FFFFFF') < 4.5) {
      rgb = rgb.map(channel => Math.floor(channel * 0.95));
      fill = '#' + rgb.map(channel => channel.toString(16).padStart(2, '0')).join('').toUpperCase();
    }
    return fill;
  }

  /** The text colour for a card filled with fillFor(hex). */
  function inkFor(hex) {
    if (!isHexColour(hex)) return LIGHT_INK;
    const preferred = houseInk(hex);
    return contrast(hex, preferred) >= 4.5 ? preferred : '#FFFFFF';
  }

  // Open and completed to-do counts per source ID, from the two GET /todos responses.
  function countTodos(openGroups, doneGroups) {
    const counts = new Map();
    const add = (groups, key) => {
      for (const {source, items} of groups) {
        const entry = counts.get(source.id) || {open: 0, done: 0};
        entry[key] += items.length; counts.set(source.id, entry);
      }
    };
    add(openGroups, 'open'); add(doneGroups, 'done');
    return counts;
  }

  // One row per meeting and note in the notebook, newest first.
  function buildItems({conversations = [], pages = [], documents = [], tagsByConversation = new Map(), counts = new Map()}) {
    const none = {open: 0, done: 0};
    return [
      ...conversations.map(item => ({kind: 'conversation', id: item.id, title: item.title, date: item.recorded_at || item.created_at,
        categoryIds: tagsByConversation.get(item.id) || [], todos: counts.get(item.id) || none})),
      ...pages.map(item => ({kind: 'page', id: item.id, title: item.title, date: item.updated_at,
        categoryIds: item.category_ids || [], todos: counts.get(item.id) || none})),
      ...documents.map(item => ({kind:'document',id:item.id,title:item.title,date:item.record_date||item.created_at,categoryIds:[],todos:none})),
    ].sort((a, b) => String(b.date).localeCompare(String(a.date)));
  }

  const filterItems = (items, filter) => filter === 'all' ? items : items.filter(item => item.kind === filter);

  // The newest meeting that has a Summary overview; its text is passed through unmodified.
  function latestSummary(conversations, lines) {
    for (const item of conversations) {
      const text = lines.get(item.id);
      if (text) return {id: item.id, title: item.title, date: item.recorded_at || item.created_at, text};
    }
    return null;
  }

  const api = {FILTERS, isHexColour, inkFor, fillFor, countTodos, buildItems, filterItems, latestSummary};
  root.StellisNotebookItems = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
