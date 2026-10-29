// Transcript filler and stutter cleanup, as hide ranges over unchanged text
(function (root) {
  // um, ah, eh: English-only fillers
  const FILLERS = ['uh', 'uhm', 'umm', 'uhh', 'uhhh', 'ehh', 'ehm', 'ahm', 'hmm', 'hm', 'mmm', 'um', 'ah', 'eh'];
  const FILLER = new RegExp(`(?<![\\p{L}\\p{N}'’]|\\p{L}-)(?:${FILLERS.join('|')})(?![\\p{L}\\p{N}'’]|-\\p{L})[,.]?`, 'giu');
  const WORD = /\p{L}+/gu;

  function hideRun(hidden, start, end) {
    for (let i = start; i < end; i++) hidden[i] = true;
  }

  function stutters(text, hidden) {
    const words = [...text.matchAll(WORD)].map(match => ({start:match.index, end:match.index + match[0].length,
      key:match[0].toLowerCase()}));
    let i = 0;
    while (i < words.length) {
      let j = i + 1;
      while (j < words.length && words[j].key === words[i].key && !text.slice(words[j - 1].end, words[j].start).trim()) j++;
      // 3+ repeats only
      if (j - i >= 3) hideRun(hidden, words[i].end, words[j - 1].end);
      i = j;
    }
  }

  function visibleIndexes(text, hidden) {
    const shown = [];
    for (let i = 0; i < text.length; i++) if (!hidden[i]) shown.push(i);
    return shown;
  }

  /**
   * Repair punctuation and spacing seams after removal.
   * @param {string} text Original passage
   * @param {boolean[]} hidden Per-character hide flags, mutated
   * @param {number[]} upper Letter indexes to capitalise, appended
   */
  function repairSeams(text, hidden, upper) {
    let shown = visibleIndexes(text, hidden);
    const at = k => text[shown[k]];
    // whitespace runs
    for (let k = 1; k < shown.length; k++) if (/\s/.test(at(k)) && /\s/.test(at(k - 1))) hidden[shown[k]] = true;
    shown = visibleIndexes(text, hidden);
    for (let k = 0; k < shown.length; k++) {
      if (at(k) !== ',') continue;
      let p = k - 1;
      while (p >= 0 && /\s/.test(at(p))) p--;
      // doubled, stranded or leading comma
      if (p < 0 || /[,.?!]/.test(at(p))) hidden[shown[k]] = true;
    }
    shown = visibleIndexes(text, hidden);
    for (let k = 0; k < shown.length; k++) {
      // space before , or ., or at either end
      if (/\s/.test(at(k)) && (k === 0 || k === shown.length - 1 || /[,.]/.test(at(k + 1) || ''))) hidden[shown[k]] = true;
    }
    shown = visibleIndexes(text, hidden);
    for (let k = 0; k < shown.length; k++) {
      if (!/\p{Ll}/u.test(at(k))) continue;
      let p = k - 1;
      while (p >= 0 && /\s/.test(at(p))) p--;
      // after ? or ! only, not . (abbreviations)
      if ((p >= 0 && /[?!]/.test(at(p))) || (p < 0 && /\p{Lu}/u.test(text[0]) && shown[k] !== 0)) upper.push(shown[k]);
    }
  }

  function ranges(hidden) {
    const result = [];
    for (let i = 0; i < hidden.length; i++) {
      if (!hidden[i]) continue;
      const start = i;
      while (i < hidden.length && hidden[i]) i++;
      result.push([start, i]);
    }
    return result;
  }

  /**
   * Find fillers and stutters to hide in a passage.
   * @param {string} value Passage text
   * @return {{hidden: number[][], upper: number[]}} Half-open hide ranges and capitalised letter indexes
   */
  function fillerCleanup(value) {
    const text = String(value ?? '');
    const hidden = new Array(text.length).fill(false);
    for (const match of text.matchAll(FILLER)) hideRun(hidden, match.index, match.index + match[0].length);
    stutters(text, hidden);
    if (!hidden.includes(true)) return {hidden:[], upper:[]};
    // all-filler passage kept
    if (!/[\p{L}\p{N}]/u.test(text.split('').filter((_, i) => !hidden[i]).join(''))) return {hidden:[], upper:[]};
    const upper = [];
    repairSeams(text, hidden, upper);
    return {hidden:ranges(hidden), upper};
  }

  /**
   * Passage text after cleanup.
   * @param {string} value Passage text
   * @return {string}
   */
  function cleanedText(value) {
    const text = String(value ?? '');
    const {hidden, upper} = fillerCleanup(text);
    const chars = text.split('');
    for (const index of upper) chars[index] = chars[index].toUpperCase();
    for (const [start, end] of hidden) for (let i = start; i < end; i++) chars[i] = '';
    return chars.join('');
  }

  const api = {fillerCleanup, cleanedText};
  root.StellisTranscriptFillers = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
