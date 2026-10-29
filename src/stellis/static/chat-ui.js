/** Shared chat pieces (#225): the typing indicator and the round send button inside a composer. */
(function(root) {
'use strict';

/**
 * Three dots that stand in for Stellis's next message while it writes.
 * @param {string=} label What a screen reader hears.
 * @return {!HTMLElement}
 */
function typing(label = 'Stellis is writing') {
  const box = document.createElement('div');
  box.className = 'chat-typing';
  box.setAttribute('role', 'status');
  box.setAttribute('aria-label', label);
  for (let i = 0; i < 3; i++) box.append(Object.assign(document.createElement('span'), {className: 'chat-typing-dot'}));
  return box;
}

/**
 * Turns a submit button into the round arrow send control; its accessible name stays the same.
 * @param {!HTMLButtonElement} button
 * @param {string=} label
 * @return {!HTMLButtonElement}
 */
function sendButton(button, label = button.textContent.trim() || 'Send') {
  button.classList.add('chat-send');
  button.setAttribute('aria-label', label);
  button.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-arrow-up"/></svg>';
  return button;
}

/** Marks a composer surface and its text field so they share the chat composer style. */
function composer(surface, input) {
  surface.classList.add('chat-composer');
  input.classList.add('chat-composer-input');
  if (input.tagName === 'TEXTAREA') input.rows = 1;
  // The field grows with the message, up to a few lines, like a messaging app.
  const grow = () => {input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px';};
  input.addEventListener('input', grow);
  requestAnimationFrame(grow);
  return surface;
}

/**
 * Ideas: what Stellis can do next, written from the person's own data. Each has a category tile
 * (Understand, Monitor or Improve, a soft tint with one glyph), a short title, one line of detail,
 * an optional source link and one action. Shared by For you now, the Home start panel and a
 * notebook's Ask welcome.
 * @param {!Array<{icon: string, category: (string|undefined), title: string,
 *     detail: (string|undefined), source: ({title: string, href: string}|undefined),
 *     action: {label: string, href: (string|undefined), command: (string|undefined),
 *     primary: (boolean|undefined)}}>} items
 * @param {function(!Object, !HTMLElement)=} onCommand Runs an action that has a command, not a link.
 * @return {!HTMLUListElement}
 */
function ideas(items, onCommand) {
  const dom = root.StellisDom;
  const list = dom.el('ul', null, 'chat-ideas');
  for (const item of items) {
    const row = dom.el('li', null, 'chat-idea' + (item.kind ? ' is-' + item.kind : ''));
    // The guided tour finds an idea by its command, such as the example records.
    if (item.action?.command) row.dataset.command = item.action.command;
    const mark = dom.el('span', null, 'chat-idea-icon is-' + (item.category || 'understand'));
    mark.append(dom.icon(item.icon || 'sparkles'));
    const words = dom.el('div', null, 'chat-idea-words');
    words.append(dom.el('strong', item.title));
    if (item.detail) words.append(dom.el('span', item.detail, 'chat-idea-detail'));
    if (item.source) {
      const source = dom.el('a', item.source.title, 'chat-idea-source');
      source.href = item.source.href;
      words.append(source);
    }
    let action;
    if (item.action.href) {
      action = dom.el('a', item.action.label, 'chat-idea-action');
      action.href = item.action.href;
    } else {
      action = dom.button(item.action.label, 'chat-idea-action');
      // The guided tour finds one idea's action by its question.
      action.dataset.idea = item.title;
      action.addEventListener('click', () => onCommand?.(item, action));
    }
    if (item.action.primary) action.classList.add('is-primary');
    row.append(mark, words, action);
    list.append(row);
  }
  return list;
}

/**
 * Brings the newest message into view (#258): a reply taller than the feed opens at its first line, so the
 * reader starts at the beginning instead of the last button; anything shorter shows with the end of the feed.
 * @param {?Element} feed The scrolling message list.
 * @param {?Element} item The newest message or exchange.
 */
function showNewest(feed, item) {
  if (!feed) return;
  if (item && item.offsetHeight > feed.clientHeight - 24) feed.scrollTop += item.getBoundingClientRect().top - feed.getBoundingClientRect().top - 12;
  else feed.scrollTop = feed.scrollHeight;
}

root.StellisChatUI = {typing, sendButton, composer, ideas, showNewest};
})(window);
