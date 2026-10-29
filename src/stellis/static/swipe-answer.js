/**
 * @fileoverview One swipe-to-answer control for every incoming Stellis call (onboarding and Home).
 *
 * The whole track is the grip: a drag from anywhere on it moves the handle, and passing 60% of the
 * way answers. A tap or click anywhere on the track also answers, and so do Enter and Space on the
 * handle, so pointer, touch and keyboard paths stay equivalent.
 */
(function(root) {
'use strict';

/**
 * @param {{label: (string|undefined), hint: (string|undefined), onAnswer: function()}} options
 * @return {!Element} The track, with reset() and setDisabled(boolean) methods.
 */
function create({label = 'Answer the call', hint = 'Swipe to answer', onAnswer}) {
  const dom = root.StellisDom;
  const track = dom.el('div', null, 'swipe-answer');
  const hintText = dom.el('span', hint, 'swipe-answer-hint');
  hintText.setAttribute('aria-hidden', 'true');
  const handle = dom.button(null, 'swipe-answer-handle');
  handle.setAttribute('aria-label', label);
  handle.append(dom.icon('phone'));
  track.append(hintText, handle);
  let origin = null, offset = 0, dragged = false, answered = false;
  const room = () => Math.max(0, track.clientWidth - handle.offsetWidth - 8);
  const place = x => {
    offset = Math.max(0, Math.min(room(), x));
    handle.style.translate = `${offset}px 0`;
    hintText.style.opacity = String(1 - offset / Math.max(1, room()));
  };
  const answer = () => {
    if (answered || handle.disabled) return;
    answered = true;
    place(room());
    onAnswer();
  };
  track.addEventListener('pointerdown', event => {
    if (event.button > 0 || handle.disabled) return;
    origin = event.clientX - offset;
    dragged = false;
    track.setPointerCapture(event.pointerId);
    track.classList.add('is-dragging');
  });
  track.addEventListener('pointermove', event => {
    if (origin === null) return;
    const x = event.clientX - origin;
    if (Math.abs(x) > 6) dragged = true;
    if (dragged) place(x);
  });
  track.addEventListener('pointerup', () => {
    if (origin === null) return;
    origin = null;
    track.classList.remove('is-dragging');
    // Short of 60%, the handle glides back from where it was let go.
    if (!dragged || offset >= room() * 0.6) answer();
    else place(0);
  });
  track.addEventListener('pointercancel', () => {
    origin = null;
    track.classList.remove('is-dragging');
    place(0);
  });
  // A keyboard press arrives as a click with no pointer detail.
  handle.addEventListener('click', event => { if (event.detail === 0) answer(); });
  track.reset = () => { answered = false; place(0); };
  track.setDisabled = disabled => { handle.disabled = disabled; track.classList.toggle('is-disabled', disabled); };
  return track;
}

root.StellisSwipeAnswer = {create};
})(window);
