/**
 * Demo story clock (#252), loaded before every other script. Temporary override: in a demo
 * workspace whose story date differs from the real one, the server sets the stellis-story-days
 * cookie, and `new Date()` and `Date.now()` move by that many calendar days. Dates built from a
 * value are untouched. Without the cookie this script changes nothing. Remove its <script> tags
 * to turn the override off.
 */
(() => {
  const match = document.cookie.match(/(?:^|; )stellis-story-days=(-?\d{1,5})(?:;|$)/);
  const days = match ? Number(match[1]) : 0;
  if (!days) return;
  const Real = Date;
  // Calendar days, not 24-hour steps, so a daylight-saving change never moves the time of day.
  const shifted = () => { const at = new Real(Real.now()); at.setDate(at.getDate() + days); return at.getTime(); };
  class StoryDate extends Real {
    constructor(...args) { super(...(args.length ? args : [shifted()])); }
    static now() { return shifted(); }
  }
  globalThis.Date = StoryDate;
})();
