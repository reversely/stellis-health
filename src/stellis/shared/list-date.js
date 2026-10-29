// Plain-text dates for list rows, and the per-browser Date format setting that controls them.
(function (root) {
  const KEY = 'stellis-date-format';
  const FORMATS = ['relative', 'short', 'iso'];
  const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function getDateFormat(storage) {
    try {
      const value = (storage || root.localStorage).getItem(KEY);
      return FORMATS.includes(value) ? value : 'relative';
    } catch { return 'relative'; }
  }
  function setDateFormat(value, storage) {
    if (!FORMATS.includes(value)) return false;
    try {(storage || root.localStorage).setItem(KEY, value); return true;} catch { return false; }
  }
  // A bare YYYY-MM-DD is a calendar day, not a moment: read it in local time so it never shifts a day.
  function parse(isoDate) {
    const bare = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(isoDate || ''));
    return bare ? new Date(+bare[1], +bare[2] - 1, +bare[3]) : new Date(isoDate);
  }
  const pad = number => String(number).padStart(2, '0');
  const dayNumber = date => Math.round(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000);

  function formatListDate(isoDate, {now = new Date(), format = getDateFormat()} = {}) {
    const date = parse(isoDate);
    if (Number.isNaN(date.getTime())) return '';
    if (format === 'iso') return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    if (format !== 'short') {
      const ahead = dayNumber(date) - dayNumber(now);
      if (ahead === 0) return 'Today';
      if (ahead === 1) return 'Tomorrow';
      if (ahead > 1 && ahead <= 6) return WEEKDAYS[date.getDay()];
    }
    const text = `${MONTHS[date.getMonth()]} ${date.getDate()}`;
    return date.getFullYear() === now.getFullYear() ? text : `${text}, ${date.getFullYear()}`;
  }

  const api = {formatListDate, getDateFormat, setDateFormat, DATE_FORMATS: FORMATS};
  root.StellisListDate = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
