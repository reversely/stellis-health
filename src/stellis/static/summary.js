/**
 * @fileoverview Health summary page: a cited summary of the person's health
 * written from all their documents, with a button to write it again.
 */
(function(root) {
'use strict';

/**
 * Splits a summary into sections of a heading and cited paragraphs.
 * @param {string} content Text with "## Heading" lines and [[n]] markers.
 * @param {!Array<!Object>} citations
 * @return {!Array<{heading: string, paragraphs: !Array<!Array<!Object>>}>}
 */
function sections(content, citations) {
  const result = [];
  for (const block of content.split(/^## /m)) {
    if (!block.trim()) {
      continue;
    }
    const newline = block.indexOf('\n');
    const heading = newline < 0 ? block.trim() : block.slice(0, newline).trim();
    const body = newline < 0 ? '' : block.slice(newline + 1).trim();
    result.push({
      heading,
      paragraphs: body ? root.StellisNotebookChat.answerParts(body, citations) :
                         [],
    });
  }
  return result;
}

/** The Health summary page. */
class SummaryPage {
  /** @param {!Element} host */
  constructor(host) {
    const dom = root.StellisDom;
    this.dom = dom;
    const head = dom.el('div', null, 'summary-head');
    head.append(
        dom.el('h2', 'Health summary'),
        dom.accuracyNote('summaries', 'Written by a model'));
    this.meta = dom.el('p', null, 'summary-meta');
    this.update = dom.button('Write summary', 'summary-update');
    this.update.addEventListener('click', () => this.refresh());
    this.written = dom.el('p', null, 'summary-meta');
    this.written.hidden = true;
    const details = dom.el('div');
    details.append(this.meta, this.written);
    const actions = dom.el('div', null, 'summary-actions');
    actions.append(details, this.update);
    this.status = dom.el('p', null, 'summary-status');
    this.status.setAttribute('role', 'status');
    this.body = dom.el('div', null, 'summary-body');
    host.replaceChildren(head, actions, this.status, this.body);
  }

  /**
   * @param {!Object} citation
   * @return {!Element}
   */
  citationLink(citation) {
    const link = this.dom.el('a', String(citation.n), 'nb-cite');
    link.href = citation.target;
    const where = citation.page ? `${citation.title}, page ${citation.page}` :
                                  citation.title;
    link.setAttribute('aria-label', `Source ${citation.n}: ${where}`);
    link.title = `${where}: ${citation.excerpt}`;
    return link;
  }

  /** @param {?Object} summary */
  paint(summary) {
    const dom = this.dom;
    if (!summary) {
      // No summary: the Write summary button alone, no placeholder lines (owner, 2026-10-28).
      this.meta.textContent = '';
      this.written.hidden = true;
      this.body.replaceChildren();
      return;
    }
    const when = new Date(summary.created_at).toLocaleDateString();
    this.meta.textContent = summary.selected_document_count == null ?
        'Document coverage unavailable' :
        `Excerpts from ${summary.selected_document_count} of ${
            dom.plural(summary.document_count, 'document')}`;
    this.written.textContent = `Written ${when}`;
    this.written.hidden = false;
    this.update.textContent = 'Write again';
    this.body.replaceChildren(
        ...sections(summary.content, summary.citations).map((section) => {
          const node = dom.el('section', null, 'summary-section');
          node.append(dom.el('h3', section.heading));
          for (const parts of section.paragraphs) {
            const paragraph = dom.el('p');
            for (const part of parts) {
              paragraph.append(
                  part.citation ? this.citationLink(part.citation) : part.text);
            }
            node.append(paragraph);
          }
          return node;
        }));
  }

  async refresh() {
    this.update.disabled = true;
    this.status.textContent = 'Reading your records';
    try {
      const result = await this.dom.api('/summary/refresh', {method: 'POST'});
      this.status.textContent = '';
      this.paint(result.summary);
    } catch (error) {
      this.status.textContent = error.message;
    } finally {
      this.update.disabled = false;
    }
  }

  async load() {
    try {
      this.paint((await this.dom.api('/summary')).summary);
    } catch (error) {
      this.status.textContent = error.message;
    }
  }
}

/**
 * Shows the Health summary page in host.
 * @param {!Element} host
 * @return {!Promise<void>}
 */
function mount(host) {
  return new SummaryPage(host).load();
}

const exported = {
  mount,
  sections
};
root.StellisSummary = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
