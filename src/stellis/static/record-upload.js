/**
 * @fileoverview Uploads one file as a record: MP3 audio becomes a recording
 * and PDF or image files become documents. Shared by the Records page and the
 * notebook Add source dialog.
 */
(function(root) {
'use strict';

const ACCEPT = [
  '.mp3', 'audio/mpeg', '.pdf', 'application/pdf', '.png', 'image/png', '.jpg',
  '.jpeg', 'image/jpeg'
].join(',');

/**
 * Picks the endpoint for an uploaded file from its name.
 * @param {string} name
 * @return {?string} "audio", "document", or null for other files.
 */
function uploadKind(name) {
  if (/\.mp3$/i.test(name)) {
    return 'audio';
  }
  if (/\.(pdf|png|jpe?g)$/i.test(name)) {
    return 'document';
  }
  return null;
}

/**
 * @typedef {{outcome: string, kind: ?string, record: ?Object,
 *     message: string}}
 */
let UploadResult;

/**
 * Uploads one file, filed in a notebook when one is given.
 * @param {!File} file
 * @param {?string} notebookId
 * @return {!Promise<!UploadResult>} outcome is "added", "duplicate",
 *     "refused" for a file type the app does not take, or "failed".
 */
async function uploadFile(file, notebookId) {
  const kind = uploadKind(file.name);
  if (!kind) {
    return {
      outcome: 'refused',
      kind,
      record: null,
      message: 'choose an MP3, PDF or image',
    };
  }
  const body = new FormData();
  body.append('file', file);
  if (notebookId) {
    body.append('notebook_id', notebookId);
  }
  if (kind === 'audio' && file.lastModified) {
    body.append('recorded_at', new Date(file.lastModified).toISOString());
  }
  const path = kind === 'audio' ? '/conversations/import' : '/documents';
  try {
    const result = await root.StellisDom.api(path, {method: 'POST', body});
    const record = result.document || result.conversation;
    if (result.duplicate) {
      return {outcome: 'duplicate', kind, record, message: 'already uploaded'};
    }
    const message = kind === 'audio' ? 'added and transcribing' : 'added';
    return {outcome: 'added', kind, record, message};
  } catch (error) {
    return {outcome: 'failed', kind, record: null, message: error.message};
  }
}

/**
 * Example files offered beside a file picker, for a visitor with none of their own. Only the demo
 * fills this list (#258); each entry is {label, files: [{name, url, type}]}.
 * @type {!Array<{label: string, files: !Array<{name: string, url: string, type: string}>}>}
 */
const examples = [];

/**
 * A row of the example sets that fit a picker, hidden while there are none; it fills in when the
 * demo adds its sets (the `stellis-upload-examples` event). Choosing one fetches its files and hands
 * them to `use`, the same as files chosen from the device.
 * @param {boolean} multiple Whether the picker takes several files.
 * @param {function(!Array<!File>)} use
 * @return {!Element}
 */
function exampleChoices(multiple, use) {
  const dom = root.StellisDom;
  const row = dom.el('div', null, 'upload-examples');
  const render = () => {
    const sets = examples.filter(set => multiple || set.files.length === 1);
    row.hidden = !sets.length;
    row.replaceChildren(dom.el('span', 'Example files', 'upload-examples-label'));
    for (const set of sets) {
      const choice = dom.button(set.label, 'coach-button is-quiet');
      choice.addEventListener('click', async () => {
        choice.disabled = true;
        try {
          use(await Promise.all(set.files.map(async (item) => {
            const response = await fetch(item.url);
            if (!response.ok) {
              throw new Error(`${item.name} could not load`);
            }
            return new File([await response.blob()], item.name, {type: item.type});
          })));
        } finally {
          choice.disabled = false;
        }
      });
      row.append(choice);
    }
  };
  root.addEventListener('stellis-upload-examples', render);
  render();
  return row;
}

const exported = {
  ACCEPT,
  examples,
  exampleChoices,
  uploadKind,
  uploadFile
};
root.StellisRecordUpload = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
