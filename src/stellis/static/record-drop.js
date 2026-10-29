/**
 * @fileoverview A drop zone that files reports into one notebook: drag PDFs or
 * photos onto it, or choose them with the upload button. Each added document
 * links to its page, where the date is suggested and values are checked.
 */
(function(root) {
'use strict';
const dom = () => root.StellisDom;
const el = (tag, text, cls) => dom().el(tag, text, cls);

/**
 * @param {!Element} host
 * @param {{notebookId: string, onUploaded: function()}} options
 */
function mount(host, options) {
  const zone = el('section', null, 'record-drop');
  zone.setAttribute('aria-labelledby', 'record-drop-title');
  const mark = el('span', null, 'record-drop-icon'); mark.append(dom().icon('upload'));
  const text = el('div', null, 'record-drop-text');
  const title = el('h3', 'Drop a report here'); title.id = 'record-drop-title';
  text.append(title, el('p', 'Stellis reads a PDF or photo and you check each value'));
  const input = el('input'); input.type = 'file'; input.multiple = true; input.hidden = true;
  input.accept = '.pdf,application/pdf,.png,image/png,.jpg,.jpeg,image/jpeg';
  const choose = dom().button(null, 'coach-button is-primary');
  choose.append(dom().icon('upload'), el('span', 'Upload a report'));
  choose.addEventListener('click', () => input.click());
  const results = el('ul', null, 'record-drop-results');
  results.setAttribute('aria-live', 'polite');
  zone.append(mark, text, choose, input, results);
  host.replaceChildren(zone);

  async function upload(files) {
    results.replaceChildren();
    let added = false;
    for (const file of files) {
      const row = el('li', `${file.name}: uploading`);
      results.append(row);
      // Only documents belong here; recordings use Record.
      const result = root.StellisRecordUpload.uploadKind(file.name) === 'document' ?
          await root.StellisRecordUpload.uploadFile(file, options.notebookId) :
          {outcome: 'refused', message: 'choose a PDF or photo'};
      row.textContent = `${file.name}: ${result.message}`;
      if (result.record && result.kind === 'document') {
        const review = el('a', 'Check the date and values', 'record-drop-review');
        review.href = `/document/${encodeURIComponent(result.record.id)}`;
        row.append(' ', review);
        added = added || result.outcome === 'added';
      }
    }
    if (added) options.onUploaded();
  }
  input.addEventListener('change', () => { if (input.files.length) upload([...input.files]); input.value = ''; });
  zone.addEventListener('dragover', event => { event.preventDefault(); zone.classList.add('is-over'); });
  zone.addEventListener('dragleave', event => { if (!zone.contains(event.relatedTarget)) zone.classList.remove('is-over'); });
  zone.addEventListener('drop', event => {
    event.preventDefault(); zone.classList.remove('is-over');
    if (event.dataTransfer?.files.length) upload([...event.dataTransfer.files]);
  });
}

root.StellisRecordDrop = {mount};
})(window);
