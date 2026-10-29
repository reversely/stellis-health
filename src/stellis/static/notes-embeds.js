// Link cards, video embeds and external images (#281, #282), plus the "Paste as" menu.
//   <bookmark url="https://example.com/a">Caption</bookmark>   a link card (preview from POST /link-previews)
//   ![caption](https://youtu.be/ID)                             a YouTube, Vimeo or Loom player
//   ![caption](https://example.com/photo.png)                   an external image
// Page text is only ever written with textContent, and a URL is used only after it parses as http or https.
import {
  $createParagraphNode, $createTextNode, $getNodeByKey, $getSelection, $isRangeSelection, $isRootOrShadowRoot,
  COMMAND_PRIORITY_HIGH, DecoratorNode, PASTE_COMMAND,
} from 'lexical';
import {$createLinkNode, $isLinkNode} from '@lexical/link';
import {$isCodeNode} from '@lexical/code-core';
import {$findMatchingParent, $insertNodeToNearestRoot} from '@lexical/utils';

const hooks = {apiBase:'/api/v1', fetchPreview:null, onOpenImage:() => {}};
// The app may swap the preview loader; by default the editor calls the device API itself.
export function configureEmbeds(options = {}) {Object.assign(hooks, options);}

const MAX_URL = 2048;
const IMAGE_EXTENSION = /\.(?:png|jpe?g|gif|webp|avif|bmp|svg)$/i;

// A URL that parses as http or https with no whitespace, quotes or angle brackets; null otherwise.
export function httpUrl(raw) {
  if (typeof raw !== 'string' || raw.length > MAX_URL || /[\s"<>]/.test(raw)) return null;
  try {
    const url = new URL(raw);
    return (url.protocol === 'https:' || url.protocol === 'http:') && url.hostname ? raw : null;
  } catch {return null;}
}
export const isImageUrl = raw => {
  const url = httpUrl(raw);
  return !!url && IMAGE_EXTENSION.test(new URL(url).pathname);
};

const YOUTUBE_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com',
  'youtube-nocookie.com', 'www.youtube-nocookie.com']);
const VIMEO_HOSTS = new Set(['vimeo.com', 'www.vimeo.com', 'player.vimeo.com']);
const LOOM_HOSTS = new Set(['loom.com', 'www.loom.com']);
const VIDEO_HOSTS = [YOUTUBE_HOSTS, VIMEO_HOSTS, LOOM_HOSTS, new Set(['youtu.be', 'www.youtu.be'])];
const YOUTUBE_ID = /^[\w-]{11}$/;

function youtubeStart(params) {
  const raw = params.get('start') || params.get('t') || '';
  if (/^\d+$/.test(raw)) return Number(raw);
  const parts = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/.exec(raw);
  return parts && raw ? Number(parts[1] || 0) * 3600 + Number(parts[2] || 0) * 60 + Number(parts[3] || 0) : 0;
}

// The host's own embed URL for a video link, or null when the host is not YouTube, Vimeo or Loom or no video ID is found.
export function videoEmbed(raw) {
  const href = httpUrl(raw);
  if (!href) return null;
  const url = new URL(href);
  const host = url.hostname.toLowerCase();
  const path = url.pathname.split('/').filter(Boolean);
  if (host === 'youtu.be' || host === 'www.youtu.be' || YOUTUBE_HOSTS.has(host)) {
    const id = host.endsWith('youtu.be') ? path[0]
      : path[0] === 'watch' ? url.searchParams.get('v') : ['embed', 'shorts', 'live', 'v'].includes(path[0]) ? path[1] : null;
    if (!YOUTUBE_ID.test(id || '')) return null;
    const start = youtubeStart(url.searchParams);
    return {provider:'youtube', src:`https://www.youtube-nocookie.com/embed/${id}${start ? `?start=${start}` : ''}`};
  }
  if (VIMEO_HOSTS.has(host)) {
    const at = path[0] === 'video' ? 1 : path.findIndex(part => /^\d+$/.test(part));
    const id = path[at];
    if (!/^\d{1,12}$/.test(id || '')) return null;
    const hash = /^[0-9a-f]{6,16}$/i.test(path[at + 1] || '') ? path[at + 1] : url.searchParams.get('h');
    return {provider:'vimeo', src:`https://player.vimeo.com/video/${id}${hash && /^[0-9a-f]+$/i.test(hash) ? `?h=${hash}` : ''}`};
  }
  if (LOOM_HOSTS.has(host) && ['share', 'embed'].includes(path[0]) && /^[0-9a-f]{32}$/i.test(path[1] || '')) {
    return {provider:'loom', src:`https://www.loom.com/embed/${path[1]}`};
  }
  return null;
}
const isVideoHost = raw => {
  const href = httpUrl(raw);
  return !!href && VIDEO_HOSTS.some(hosts => hosts.has(new URL(href).hostname.toLowerCase()));
};

// Attributes are written without quotes, angle brackets or spaces, and captions without markup characters.
const exportUrl = url => {
  try {return new URL(url).href;} catch {return '';}
};
const escapeCaption = text => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\r?\n/g, ' ');
const unescapeCaption = text => text.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
const imageCaption = text => text.replace(/[\]\\\r\n]/g, '');

const ICONS = {
  link:['M9 15l6 -6', 'M11 6l.463 -.536a5 5 0 0 1 7.071 7.072l-.534 .464', 'M13 18l-.397 .534a5.068 5.068 0 0 1 -7.127 0a4.972 4.972 0 0 1 0 -7.071l.524 -.463'],
  bookmark:['M18 7v14l-6 -4l-6 4v-14a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4'],
  photo:['M15 8h.01', 'M3 6a3 3 0 0 1 3 -3h12a3 3 0 0 1 3 3v12a3 3 0 0 1 -3 3h-12a3 3 0 0 1 -3 -3v-12', 'M3 16l5 -5c.928 -.893 2.072 -.893 3 0l5 5', 'M14 14l1 -1c.928 -.893 2.072 -.893 3 0l3 3'],
  video:['M15 10l4.553 -2.276a1 1 0 0 1 1.447 .894v6.764a1 1 0 0 1 -1.447 .894l-4.553 -2.276v-4', 'M3 8a2 2 0 0 1 2 -2h8a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-8a2 2 0 0 1 -2 -2l0 -8'],
  x:['M18 6l-12 12', 'M6 6l12 12'],
};
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', 'stroke-width':'2',
    'stroke-linecap':'round', 'stroke-linejoin':'round', 'aria-hidden':'true', class:'icon'})) svg.setAttribute(key, value);
  for (const d of ICONS[name]) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', d); svg.append(path);
  }
  return svg;
}

// A block that sits between paragraphs always leaves somewhere to type after it.
function $afterBlock(node) {
  if (!node.getNextSibling()) node.insertAfter($createParagraphNode());
}
function $removeBlock(node) {
  if (!node.getPreviousSibling() && !node.getNextSibling()) node.insertAfter($createParagraphNode());
  const next = node.getNextSibling() || node.getPreviousSibling();
  node.remove();
  next?.selectStart?.();
}
function removeButton(editor, key, label) {
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'note-embed-remove'; button.contentEditable = 'false';
  button.setAttribute('aria-label', label); button.title = label; button.append(icon('x'));
  button.addEventListener('click', event => {
    event.preventDefault(); event.stopPropagation();
    editor.update(() => {const node = $getNodeByKey(key); if (node) $removeBlock(node);});
  });
  return button;
}

const previews = new Map();
function loadPreview(url) {
  if (!previews.has(url)) {
    const request = hooks.fetchPreview ? hooks.fetchPreview(url) : fetch(`${hooks.apiBase}/link-previews`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url}),
    }).then(response => {if (!response.ok) throw new Error('No preview'); return response.json();});
    previews.set(url, Promise.resolve(request).catch(error => {previews.delete(url); throw error;}));
  }
  return previews.get(url);
}

const hostOf = url => {try {return new URL(url).hostname.replace(/^www\./, '');} catch {return url;}};

export class BookmarkNode extends DecoratorNode {
  __url; __caption;
  static getType() {return 'bookmark';}
  static clone(node) {return new BookmarkNode(node.__url, node.__caption, node.__key);}
  constructor(url, caption = '', key) {super(key); this.__url = url; this.__caption = caption;}
  isInline() {return false;}
  createDOM(config, editor) {
    const block = document.createElement('div'); block.className = 'note-bookmark'; block.contentEditable = 'false';
    const card = document.createElement('a'); card.className = 'note-bookmark-card';
    card.href = this.__url; card.target = '_blank'; card.rel = 'noopener noreferrer';
    const text = document.createElement('span'); text.className = 'note-bookmark-text';
    const title = document.createElement('strong'); title.textContent = this.__caption || hostOf(this.__url);
    const description = document.createElement('span'); description.className = 'note-bookmark-description'; description.hidden = true;
    const site = document.createElement('small'); site.className = 'note-bookmark-site';
    const favicon = document.createElement('img'); favicon.alt = ''; favicon.hidden = true;
    favicon.referrerPolicy = 'no-referrer'; favicon.loading = 'lazy';
    const address = document.createElement('span'); address.textContent = this.__url;
    site.append(favicon, address);
    text.append(title, description, site);
    const picture = document.createElement('img'); picture.className = 'note-bookmark-image'; picture.alt = '';
    picture.hidden = true; picture.referrerPolicy = 'no-referrer'; picture.loading = 'lazy';
    card.append(text, picture);
    block.append(card, removeButton(editor, this.__key, 'Remove link card'));
    const caption = this.__caption;
    loadPreview(this.__url).then(data => {
      if (!caption && data.title) title.textContent = data.title;
      if (data.description) {description.textContent = data.description; description.hidden = false;}
      if (data.site_name) address.textContent = `${data.site_name}, ${hostOf(this.__url)}`;
      const iconUrl = httpUrl(data.icon_url); if (iconUrl) {favicon.src = iconUrl; favicon.hidden = false;}
      const image = httpUrl(data.image_url); if (image) {picture.src = image; picture.hidden = false;}
      picture.addEventListener('error', () => {picture.hidden = true;});
      favicon.addEventListener('error', () => {favicon.hidden = true;});
    }).catch(() => {});
    return block;
  }
  updateDOM() {return false;}
  exportDOM() {
    const link = document.createElement('a'); link.href = this.__url; link.textContent = this.__caption || this.__url;
    return {element:link};
  }
  getTextContent() {return this.__caption || this.__url;}
  exportJSON() {return {...super.exportJSON(), type:'bookmark', version:1, url:this.__url, caption:this.__caption};}
  static importJSON(data) {return new BookmarkNode(data.url, data.caption);}
}

export class VideoEmbedNode extends DecoratorNode {
  __url; __caption;
  static getType() {return 'video-embed';}
  static clone(node) {return new VideoEmbedNode(node.__url, node.__caption, node.__key);}
  constructor(url, caption = '', key) {super(key); this.__url = url; this.__caption = caption;}
  isInline() {return false;}
  createDOM(config, editor) {
    const figure = document.createElement('figure'); figure.className = 'note-embed'; figure.contentEditable = 'false';
    const embed = videoEmbed(this.__url);
    const frame = document.createElement('div'); frame.className = 'note-embed-frame';
    if (embed) {
      const iframe = document.createElement('iframe');
      iframe.src = embed.src; iframe.title = this.__caption || `${embed.provider} video`;
      iframe.loading = 'lazy'; iframe.allowFullscreen = true;
      iframe.setAttribute('allow', 'fullscreen; picture-in-picture; encrypted-media');
      iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox');
      iframe.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
      frame.append(iframe);
    }
    figure.append(frame);
    if (this.__caption) {const caption = document.createElement('figcaption'); caption.textContent = this.__caption; figure.append(caption);}
    figure.append(removeButton(editor, this.__key, 'Remove video'));
    return figure;
  }
  updateDOM() {return false;}
  exportDOM() {
    const link = document.createElement('a'); link.href = this.__url; link.textContent = this.__caption || this.__url;
    return {element:link};
  }
  getTextContent() {return this.__caption || this.__url;}
  exportJSON() {return {...super.exportJSON(), type:'video-embed', version:1, url:this.__url, caption:this.__caption};}
  static importJSON(data) {return new VideoEmbedNode(data.url, data.caption);}
}

export class ExternalImageNode extends DecoratorNode {
  __url; __caption;
  static getType() {return 'external-image';}
  static clone(node) {return new ExternalImageNode(node.__url, node.__caption, node.__key);}
  constructor(url, caption = '', key) {super(key); this.__url = url; this.__caption = caption;}
  isInline() {return false;}
  createDOM(config, editor) {
    const figure = document.createElement('figure'); figure.className = 'note-image note-image-external'; figure.contentEditable = 'false';
    const button = document.createElement('button'); button.type = 'button';
    const alt = this.__caption || 'Image';
    button.setAttribute('aria-label', `Expand image${this.__caption ? `: ${this.__caption}` : ''}`);
    const image = document.createElement('img'); image.alt = alt; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer';
    image.src = this.__url;
    image.addEventListener('error', () => {
      const note = document.createElement('p'); note.className = 'note-image-failed';
      note.textContent = `This image couldn't be loaded from ${hostOf(this.__url)}.`;
      button.replaceWith(note);
    });
    button.append(image); button.addEventListener('click', () => hooks.onOpenImage(image.currentSrc || image.src, alt));
    figure.append(button);
    if (this.__caption) {const caption = document.createElement('figcaption'); caption.textContent = this.__caption; figure.append(caption);}
    figure.append(removeButton(editor, this.__key, 'Remove image'));
    return figure;
  }
  updateDOM() {return false;}
  exportDOM() {
    const image = document.createElement('img'); image.src = this.__url; image.alt = this.__caption || 'Image';
    return {element:image};
  }
  getTextContent() {return this.__caption || 'Image';}
  exportJSON() {return {...super.exportJSON(), type:'external-image', version:1, url:this.__url, caption:this.__caption};}
  static importJSON(data) {return new ExternalImageNode(data.url, data.caption);}
}

export const EMBED_NODES = [BookmarkNode, VideoEmbedNode, ExternalImageNode];

// The importer empties the line's text before it calls replace; a transformer that declines puts the text back.
const decline = (children, match) => {
  children[0]?.setTextContent(match[0] + (children[0].getTextContent() || ''));
  return false;
};
const BOOKMARK = /^<bookmark\s+url="(https?:\/\/[^"\s]*)"\s*>([^<]*)<\/bookmark>\s*$/;
const MEDIA = /^!\[([^\]\n]*)\]\((https?:\/\/[^)\s]+)\)\s*$/;

export function embedTransformers() {
  return [
    {dependencies:[BookmarkNode], type:'element', regExp:BOOKMARK,
      export:node => node instanceof BookmarkNode && exportUrl(node.__url) ?
        `<bookmark url="${exportUrl(node.__url)}">${escapeCaption(node.__caption)}</bookmark>` : null,
      replace(parent, children, match) {
        if (!httpUrl(match[1])) return decline(children, match);
        parent.replace(new BookmarkNode(match[1], unescapeCaption(match[2]).trim()));
      }},
    // Placed before LINK. A video host with no usable video ID is left as text, so it stays a link.
    {dependencies:[VideoEmbedNode, ExternalImageNode], type:'element', regExp:MEDIA,
      export:node => node instanceof VideoEmbedNode || node instanceof ExternalImageNode ?
        `![${imageCaption(node.__caption)}](${exportUrl(node.__url)})` : null,
      replace(parent, children, match) {
        if (!httpUrl(match[2])) return decline(children, match);
        if (isVideoHost(match[2])) {
          if (!videoEmbed(match[2])) return decline(children, match);
          parent.replace(new VideoEmbedNode(match[2], match[1]));
        } else parent.replace(new ExternalImageNode(match[2], match[1]));
      }},
  ];
}

export function $insertBookmark(url, caption = '') {
  const node = new BookmarkNode(url, caption);
  $insertNodeToNearestRoot(node);
  $afterBlock(node);
  node.getNextSibling()?.selectStart?.();
}

// The "Paste as" menu: a pasted web address lands as a link first; Link keeps it, the others replace it with a block.
function pasteChoices(url) {
  const choices = [{label:'Link', icon:'link', make:null}];
  if (videoEmbed(url)) choices.push({label:'Embed video', icon:'video', make:() => new VideoEmbedNode(url)});
  if (isImageUrl(url)) choices.push({label:'Embed image', icon:'photo', make:() => new ExternalImageNode(url)});
  choices.push({label:'Bookmark', icon:'bookmark', make:() => new BookmarkNode(url)});
  return choices;
}
function $replaceLink(key, make) {
  const link = $getNodeByKey(key);
  if (!$isLinkNode(link)) return;
  const block = $findMatchingParent(link, node => { const parent = node.getParent(); return !!parent && $isRootOrShadowRoot(parent); });
  if (!block) return;
  const node = make();
  link.remove();
  if (block.getType() === 'paragraph' && !block.getTextContent().trim()) block.replace(node);
  else block.insertAfter(node);
  $afterBlock(node);
  node.getNextSibling()?.selectStart?.();
}

export function registerEmbedPaste(editor, root) {
  let menu = null; let release = null;
  const close = () => {release?.(); release = null; menu?.remove(); menu = null;};
  const open = (key, url) => {
    close();
    const anchor = editor.getElementByKey(key);
    if (!anchor) return;
    menu = document.createElement('div'); menu.className = 'note-paste-menu'; menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', 'Paste as');
    const heading = document.createElement('small'); heading.textContent = 'Paste as'; menu.append(heading);
    const buttons = pasteChoices(url).map(choice => {
      const button = document.createElement('button'); button.type = 'button'; button.setAttribute('role', 'menuitem');
      button.append(icon(choice.icon), document.createTextNode(choice.label));
      button.addEventListener('mousedown', event => event.preventDefault());
      button.addEventListener('click', () => {
        close();
        if (choice.make) editor.update(() => $replaceLink(key, choice.make));
        editor.focus();
      });
      menu.append(button);
      return button;
    });
    document.body.append(menu);
    const box = anchor.getBoundingClientRect(); const width = menu.offsetWidth;
    menu.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - width - 8))}px`;
    const height = menu.offsetHeight;
    const below = box.bottom + 6 + height <= window.innerHeight - 8;
    menu.style.top = `${Math.max(8, below ? box.bottom + 6 : box.top - height - 6)}px`;
    let active = -1;
    const mark = () => buttons.forEach((button, index) => button.classList.toggle('active', index === active));
    // The keys the menu uses are stopped before the editor sees them: its Escape handler would blur the editor.
    const onKey = event => {
      const take = () => {event.preventDefault(); event.stopPropagation();};
      if (event.key === 'Escape') {take(); close();}
      else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        take(); active = (active + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length; mark();
      } else if (event.key === 'Enter' && active >= 0) {take(); buttons[active].click();}
      else if (!['Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) close();
    };
    const onPointer = event => {if (!menu?.contains(event.target)) close();};
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onPointer, true);
    release = () => {document.removeEventListener('keydown', onKey, true); document.removeEventListener('pointerdown', onPointer, true);};
  };
  const unregister = editor.registerCommand(PASTE_COMMAND, event => {
    const data = event?.clipboardData;
    if (!data || data.getData('application/x-lexical-editor')) return false;
    const text = data.getData('text/plain').trim();
    const url = httpUrl(text);
    const selection = $getSelection();
    if (!url || !$isRangeSelection(selection) || !selection.isCollapsed()) return false;
    const node = selection.anchor.getNode();
    if ($findMatchingParent(node, parent => $isCodeNode(parent) || $isLinkNode(parent))) return false;
    event.preventDefault();
    const link = $createLinkNode(url);
    link.append($createTextNode(url));
    selection.insertNodes([link]);
    link.selectNext();
    const key = link.getKey();
    // The menu opens after the update has drawn the link, so it can sit under it.
    setTimeout(() => open(key, url), 0);
    return true;
  }, COMMAND_PRIORITY_HIGH);
  return () => {close(); unregister();};
}
