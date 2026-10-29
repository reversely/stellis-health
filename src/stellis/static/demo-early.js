/**
 * @fileoverview Runs before the app's first paint (#258), so nothing flashes while the demo sets up. A
 * demo that is just starting shows its context slide first: until demo-guide.js puts the slide up the
 * page stays covered in the slide's paper colour, four seconds at most. A tour in progress reserves its
 * docked panel's room from the first paint.
 */
(() => {
  let step = null, met = false, small = false;
  try {
    step = sessionStorage.getItem('stellis-demo-step');
    met = sessionStorage.getItem('stellis-demo-met') === '1';
    small = sessionStorage.getItem('stellis-demo-small') === '1';
  } catch {}
  if (step === null || Number(step) < 0) return;
  const root = document.documentElement;
  // A tour in progress keeps the room its docked panel takes from the first paint, so a page that a
  // step opens never draws full width and then narrows.
  if (met) { root.classList.add('has-demo-panel'); if (small) root.classList.add('demo-is-small'); return; }
  if (step !== '0') return;
  root.classList.add('demo-arriving');
  setTimeout(() => root.classList.remove('demo-arriving'), 4000);
})();
