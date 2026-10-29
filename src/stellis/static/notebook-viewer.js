/**
 * @fileoverview Source viewer beside the notebook chat. It opens a transcript
 * with audio, a note, or a document at the place a citation names.
 */
(function(root) {
'use strict';

/** Base for parsing app links; any other origin is refused. */
const LINK_BASE = 'http://stellis.invalid';
const KINDS = new Set(['conversation', 'document', 'page']);

/**
 * @typedef {{kind: string, id: string, segment: ?string, ms: ?number,
 *     page: ?number, href: string}}
 */
let Place;

/**
 * Reads an app link into the place the viewer opens.
 * @param {string} target A link such as /conversation/a?at=s1&t=4200.
 * @return {?Place} Null for any other origin, path or kind.
 */
function parseTarget(target) {
  const url = new URL(target, LINK_BASE);
  const [, kind, id, extra] = url.pathname.split('/');
  if (url.origin !== LINK_BASE || !KINDS.has(kind) || !id ||
      extra !== undefined) {
    return null;
  }
  const time = Number(url.searchParams.get('t'));
  const page = Number(url.searchParams.get('page'));
  return {
    kind,
    id: decodeURIComponent(id),
    segment: url.searchParams.get('at'),
    ms: url.searchParams.has('t') && Number.isFinite(time) ? time : null,
    page: Number.isInteger(page) && page > 0 ? page : null,
    href: url.pathname + url.search,
  };
}

/**
 * Formats milliseconds as m:ss.
 * @param {number} ms
 * @return {string}
 */
function clock(ms) {
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

/** @typedef {{title: string, focus: ?Element, file: (string|undefined)}} */
let View;

/**
 * Renders a transcript with its audio, seeked to the cited time.
 * @param {!Element} body
 * @param {!Place} place
 * @return {!Promise<!View>}
 */
async function renderConversation(body, place) {
  const dom = root.StellisDom;
  const path = `/conversations/${encodeURIComponent(place.id)}`;
  const [record, transcript] =
      await Promise.all([dom.api(path), dom.api(`${path}/transcript`)]);
  const audio = /** @type {!HTMLAudioElement} */ (
      dom.el('audio', null, 'nb-viewer-audio'));
  audio.controls = true;
  audio.preload = 'metadata';
  audio.src = `/api/v1${path}/audio`;
  if (place.ms !== null) {
    audio.addEventListener('loadedmetadata', () => {
      audio.currentTime = place.ms / 1000;
    }, {once: true});
  }
  const list = dom.el('ol', null, 'nb-viewer-transcript');
  let focus = null;
  for (const segment of transcript.segments) {
    const item = dom.el('li');
    const play = dom.button(null, 'nb-viewer-segment');
    const label =
        `${clock(segment.start_ms)} ${segment.speaker_display_name || ''}`;
    play.append(
        dom.el('span', label.trim(), 'nb-viewer-time'),
        dom.el('span', segment.text));
    play.addEventListener('click', () => {
      audio.currentTime = segment.start_ms / 1000;
      audio.play().catch(() => {});
    });
    if (segment.id === place.segment) {
      item.classList.add('is-target');
      focus = item;
    }
    item.append(play);
    list.append(item);
  }
  body.append(audio, list);
  return {title: record.title, focus};
}

/**
 * Renders a note's text.
 * @param {!Element} body
 * @param {!Place} place
 * @return {!Promise<!View>}
 */
async function renderNote(body, place) {
  const dom = root.StellisDom;
  const page = await dom.api(`/pages/${encodeURIComponent(place.id)}`);
  body.append(
      page.markdown.trim() ? dom.el('p', page.markdown, 'nb-viewer-note') :
                             dom.el('p', 'This note is empty', 'nb-empty'));
  return {title: page.title, focus: null};
}

/**
 * Renders a document's pages, scrolled to the cited page.
 * @param {!Element} body
 * @param {!Place} place
 * @return {!Promise<!View>}
 */
async function renderDocument(body, place) {
  const dom = root.StellisDom;
  const record = await dom.api(`/documents/${encodeURIComponent(place.id)}`);
  const host = dom.el('div', null, 'nb-viewer-document');
  body.append(host);
  const sections = root.StellisDocumentView.renderPages(host, record);
  const file = root.StellisDocumentView.fileUrl(record, place.page);
  return {title: record.title, focus: sections.get(place.page) || null, file};
}

const RENDERERS = {
  conversation: renderConversation,
  page: renderNote,
  document: renderDocument,
};

/** A panel that shows one source at a time. */
class SourceViewer {
  constructor() {
    const dom = root.StellisDom;
    this.element = dom.el('aside', null, 'nb-viewer');
    this.element.hidden = true;
    this.element.setAttribute('aria-labelledby', 'nb-viewer-title');
    this.title = dom.el('h2', null, 'nb-viewer-title');
    this.title.id = 'nb-viewer-title';
    this.title.tabIndex = -1;
    this.open = /** @type {!HTMLAnchorElement} */ (
        dom.el('a', 'Open the whole record', 'nb-viewer-open'));
    const close = dom.button('×', 'nb-viewer-close');
    close.setAttribute('aria-label', 'Close source');
    close.addEventListener('click', () => this.hide());
    // The citation's number as a colour block, then the source's title and kind, date and page.
    this.badge = dom.el('span', null, 'nb-viewer-badge');
    this.meta = dom.el('p', null, 'nb-viewer-meta');
    const titles = dom.el('div', null, 'nb-viewer-titles');
    titles.append(this.title, this.meta);
    const head = dom.el('div', null, 'nb-viewer-head');
    head.append(this.badge, titles, close);
    this.status = dom.el('p', null, 'nb-status');
    this.status.setAttribute('role', 'status');
    this.body = dom.el('div', null, 'nb-viewer-body');
    this.element.append(head, this.status, this.body, this.open);
    this.element.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        this.hide();
      }
    });
    /** @type {?Element} */
    this.trigger = null;
    this.request = 0;
  }

  pauseAudio() {
    this.body.querySelector('audio')?.pause();
  }

  /** Closes the viewer and returns focus to what opened it. */
  hide() {
    if (this.element.hidden) {
      return;
    }
    this.element.hidden = true;
    this.pauseAudio();
    this.element.dispatchEvent(new Event('viewerclose'));
    if (this.trigger?.isConnected) {
      this.trigger.focus();
    }
  }

  /**
   * Opens the source an app link names.
   * @param {string} target
   * @param {?Element=} trigger The control to refocus on close.
   * @param {{number: (string|undefined), meta: (string|undefined)}=} cite
   *     The citation's number and the source's kind, date and page.
   * @return {!Promise<boolean>} False when the link is not a source.
   */
  async show(target, trigger = null, cite = {}) {
    const place = parseTarget(target);
    if (!place) {
      return false;
    }
    const request = ++this.request;
    this.trigger = trigger;
    this.pauseAudio();
    this.body.replaceChildren();
    this.title.textContent = '';
    this.badge.textContent = cite.number || '';
    this.badge.hidden = !cite.number;
    this.meta.textContent = cite.meta || '';
    this.status.textContent = 'Opening';
    this.open.href = place.href;
    this.open.removeAttribute('target');
    this.element.hidden = false;
    this.element.dispatchEvent(new Event('vieweropen'));
    try {
      const view = await RENDERERS[place.kind](this.body, place);
      if (request !== this.request) {
        return true;
      }
      this.title.textContent = view.title;
      this.status.textContent = '';
      if (view.file) {
        this.open.href = view.file;
        this.open.target = '_blank';
        this.open.rel = 'noopener';
      }
      this.title.focus({preventScroll: true});
      view.focus?.scrollIntoView({block: 'center'});
    } catch (error) {
      if (request === this.request) {
        this.status.textContent = error.message;
      }
    }
    return true;
  }
}

/** @return {!SourceViewer} A new, hidden viewer. */
function create() {
  return new SourceViewer();
}

const exported = {
  create,
  parseTarget
};
root.StellisNotebookViewer = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
