/**
 * @fileoverview Health facts panel on the document page: find facts in the
 * document, then accept or reject each suggestion. A compact form serves the
 * chat document panel, with the count to check in its heading.
 */
(function(root) {
'use strict';

const KIND_LABELS = {
  medication: 'Medication',
  lab_result: 'Lab result',
  vital_sign: 'Vital sign',
  condition: 'Condition',
  allergy: 'Allergy',
};
const panels=new Set();
if(root.addEventListener)root.addEventListener('health-facts-changed',()=>{
  for(const panel of panels){if(panel.host.isConnected)panel.load();else panels.delete(panel);}
});

/**
 * Formats a fact's measured value, such as "3.4 mg/dL".
 * @param {{value: string, unit: string}} fact
 * @return {string}
 */
function measurement(fact) {
  return [fact.value, fact.unit].filter(Boolean).join(' ');
}

/**
 * Splits facts into suggestions and accepted facts.
 * @param {!Array<!Object>} facts
 * @return {{suggested: !Array<!Object>, accepted: !Array<!Object>}}
 */
function partition(facts) {
  return {
    suggested: facts.filter((fact) => fact.status === 'suggested'),
    accepted: facts.filter((fact) => fact.status === 'accepted'),
  };
}

/** The panel for one document. */
class FactsPanel {
  /**
   * @param {!Element} host
   * @param {!Object} record A document as the API returns it.
   * @param {{compact: (boolean|undefined), onChange: (function()|undefined)}=} options
   */
  constructor(host, record, options = {}) {
    const dom = root.StellisDom;
    this.dom = dom;
    this.host = host;
    for(const panel of panels)if(panel.host===host||!panel.host.isConnected)panels.delete(panel);
    panels.add(this);
    this.record = record;
    this.compact = Boolean(options.compact);
    this.onChange = options.onChange || (() => {});
    this.facts = [];
    this.find = dom.button('Find more with AI', 'facts-find');
    this.find.addEventListener('click', () => this.extract());
    this.status = dom.el('p', null, 'facts-status');
    this.status.setAttribute('role', 'status');
    this.suggested = dom.el('ul', null, 'facts-list');
    this.accepted = dom.el('ul', null, 'facts-list');
    this.suggestedTitle = dom.el('h4', 'Awaiting your approval');
    this.acceptAll = dom.button('Approve and save all', 'facts-accept-all coach-button is-primary');
    this.acceptAll.addEventListener('click', () => this.acceptEvery());
    const reviewHead = dom.el('div', null, 'facts-review-head');
    reviewHead.append(this.suggestedTitle, this.acceptAll);
    this.acceptedTitle = dom.el('h4', 'Approved and saved');
    const head = dom.el('div', null, 'facts-head');
    const title = dom.el('h3', 'Results from this report');
    this.count = dom.el('span', null, 'facts-count');
    title.append(' ', this.count);
    const section = dom.el('section', null, this.compact ? 'facts widget-paper is-compact' : 'facts widget-paper');
    if (this.compact) {
      // The panel leads with what is left to check; Accept all sits beside it.
      head.append(title, this.acceptAll);
      reviewHead.hidden = true;
      section.append(head, this.status, this.suggested, this.acceptedTitle, this.accepted,
          this.find);
    } else {
      head.append(title);
      section.append(head, this.status, reviewHead, this.suggested, this.acceptedTitle, this.accepted);
      section.append(this.find);
    }
    const how=dom.el('details',null,'facts-save-explanation');
    how.append(dom.el('summary','How results are saved'),dom.el('p','The original report and extracted candidates are stored for review. Only results you approve are added to your saved health history. Supported measurements then appear in charts and tables, with links to this report. Dismissing a candidate keeps the original report.'));
    section.append(how);host.replaceChildren(section);
    if (record.status !== 'ready') {
      this.find.hidden = true;
      this.status.textContent = 'Facts need text from this document';
    }
  }

  paint() {
    const {suggested, accepted} = partition(this.facts);
    this.suggested.replaceChildren(...suggested.map((fact) => this.row(fact)));
    this.accepted.replaceChildren(...accepted.map((fact) => this.row(fact)));
    this.suggestedTitle.hidden = !suggested.length;
    this.acceptAll.hidden = suggested.length < 2;
    this.acceptedTitle.hidden = !accepted.length;
    this.find.textContent = 'Find more with AI';
    this.count.textContent = `${suggested.length} to approve, ${accepted.length} saved`;
    // A report with no results says so once, without a row of zero counts.
    this.count.hidden = !suggested.length && !accepted.length;
    if (this.compact) this.acceptAll.hidden = !suggested.length;
    const method=this.record.pages?.some(p=>p.text_source==='manual')?'edited page text':this.record.pages?.some(p=>p.text_source==='ocr')?'locally recognised text':'the report’s text';
    const measured=accepted.filter(f=>f.measurement).length;
    this.status.textContent = suggested.length ? `Stellis read ${suggested.length} candidates from ${method}. These are not saved measurements yet. Check the value, unit, date and original row, then approve the results you want to keep.` :
        accepted.length ? `${accepted.length} approved results are saved in your health history. ${measured} supported measurements are available in Measurements.` :
        this.record.record_date || this.record.date_suggestion?.date ? 'No test results on this report' : 'Confirm the visit or test date to prepare dated results.';
    this.metrics ||= this.dom.el('a','View in Measurements','coach-button is-quiet');
    const metric=accepted.find(f=>f.measurement);
    this.metrics.href=this.record.notebook_id?'/notebook/'+encodeURIComponent(this.record.notebook_id)+'?tab=measurements':metric?'/trends?measure='+encodeURIComponent(metric.measurement):'/trends';
    this.metrics.hidden=!metric;
    this.status.after(this.metrics);
  }

  /**
   * @param {!Object} fact
   * @return {!Element}
   */
  row(fact) {
    const dom = this.dom;
    const item = dom.el('li', null, 'fact');
    item.dataset.status = fact.status;
    const range = fact.range_status;
    // A value outside its stated range is tinted so it is checked first.
    if (range?.tone === 'alert') item.classList.add('is-flagged');
    const head = dom.el('div', null, 'fact-head');
    if (!this.compact) head.append(dom.el('span', KIND_LABELS[fact.kind], 'fact-kind'));
    head.append(dom.el('strong', fact.name), dom.el('span', measurement(fact), 'fact-value'));
    if (range) head.append(dom.el('span', range.label, `status-block ${range.tone === 'ok' ? 'is-ok' : 'is-alert'}`));
    if (!this.compact) head.append(dom.el('time', fact.date));
    const page = dom.button(`Source / Page ${fact.page}`, 'fact-page');
    page.addEventListener('click', () => {
      item.dispatchEvent(new CustomEvent('report-focus-fact',{bubbles:true,detail:fact}));
    });
    const quote = dom.el('blockquote', fact.quote, 'fact-quote');
    const proof = dom.el('details', null, 'fact-proof');
    proof.append(dom.el('summary','Transcribed text'),quote);
    item.append(head, proof);
    if (fact.status === 'accepted') {
      head.append(dom.el('span', 'Approved', 'fact-tag'));
      const receipt=dom.el('p',null,'fact-approval');
      const time=dom.el('time');time.dateTime=fact.accepted_at||'';
      time.textContent=fact.accepted_at?new Date(fact.accepted_at).toLocaleString([],{dateStyle:'medium',timeStyle:'short'}):'Approval time unavailable';
      receipt.append('Approved ',time, fact.measurement?'. Added to Measurements':'. Saved in health history');
      item.append(page,receipt);
      return item;
    }
    const choice = (label, glyph) => {
      const control = dom.button(null, 'fact-icon coach-button is-quiet');
      control.append(dom.icon(glyph));
      control.setAttribute('aria-label', `${label} ${fact.name}`);
      control.title=`${label} ${fact.name}`;
      return control;
    };
    const accept = dom.button('Approve and save','coach-button is-primary');accept.setAttribute('aria-label',`Approve and save ${fact.name}`);
    accept.addEventListener('click', () => this.review(fact, true, accept));
    const reject = choice('Dismiss', 'x');
    reject.addEventListener('click', () => this.review(fact, false, reject));
    const actions = dom.el('div', null, 'fact-actions');
    actions.append(page, accept, reject);
    item.append(dom.el('p','Not in your health history yet','fact-pending'),actions);
    return item;
  }

  /** Accepts every suggestion of the document at once. */
  async acceptEvery() {
    this.acceptAll.disabled = true;
    try {
      const result = await this.dom.api(
          `/documents/${encodeURIComponent(this.record.id)}/facts/accept`,
          {method: 'POST'});
      await this.load();
      this.onChange();
      window.dispatchEvent(new Event('health-facts-changed'));
      window.dispatchEvent(new Event('health-capture-saved'));
    } catch (error) {
      this.status.textContent = error.message;
    } finally {
      this.acceptAll.disabled = false;
    }
  }

  /**
   * Accepts or rejects one suggestion.
   * @param {!Object} fact
   * @param {boolean} keep
   * @param {!HTMLButtonElement} control
   * @return {!Promise<void>}
   */
  async review(fact, keep, control) {
    control.disabled = true;
    try {
      if (keep) {
        const saved =
            await this.dom.api(`/facts/${fact.id}/accept`, {method: 'POST'});
        this.facts =
            this.facts.map((item) => item.id === fact.id ? saved : item);
      } else {
        await this.dom.api(`/facts/${fact.id}`, {method: 'DELETE'});
        this.facts = this.facts.filter((item) => item.id !== fact.id);
      }
      this.status.textContent = '';
      this.paint();
      this.onChange();
      window.dispatchEvent(new Event('health-facts-changed'));
      window.dispatchEvent(new Event('health-capture-saved'));
    } catch (error) {
      this.status.textContent = error.message;
      control.disabled = false;
    }
  }

  async extract() {
    this.find.disabled = true;
    this.status.textContent = 'Reading this document';
    try {
      const result = await this.dom.api(
          `/documents/${encodeURIComponent(this.record.id)}/facts/extract`,
          {method: 'POST'});
      this.facts = result.facts;
      const found = partition(this.facts).suggested.length;
      this.status.textContent = found ?
          `${this.dom.plural(found, 'fact')} to review` :
          'No new facts found';
      this.paint();
    } catch (error) {
      this.status.textContent = error.message;
    } finally {
      this.find.disabled = false;
    }
  }

  async load() {
    try {
      const result = await this.dom.api(
          `/documents/${encodeURIComponent(this.record.id)}/facts`);
      this.facts = result.facts;
      this.paint();
    } catch (error) {
      this.status.textContent = error.message;
    }
  }
}

/**
 * Shows a document's health facts in host.
 * @param {!Element} host
 * @param {!Object} record
 * @return {!Promise<void>}
 */
function mount(host, record, options = {}) {
  return new FactsPanel(host, record, options).load();
}

const exported = {
  mount,
  measurement,
  partition
};
root.StellisHealthFacts = exported;
if (typeof module !== 'undefined') {
  module.exports = exported;
}
})(typeof window !== 'undefined' ? window : globalThis);
