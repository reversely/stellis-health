/**
 * @fileoverview Tests for the pure helpers behind notebook chat, the source
 * viewer, the add-source dialog and the document page.
 */
const assert = require('node:assert/strict');
const test = require('node:test');

globalThis.StellisDom = require('../src/stellis/static/notebook-dom.js');
const {answerMarkdown, answerParts, sourceTarget, starterQuestions} =
    require('../src/stellis/static/notebook-chat.js');
const {parseTarget} = require('../src/stellis/static/notebook-viewer.js');
const {candidates, toCandidates} =
    require('../src/stellis/static/notebook-add-source.js');
const {uploadKind} = require('../src/stellis/static/record-upload.js');
const {describe, fileUrl} = require('../src/stellis/static/document-view.js');

const VISIT = {
  n: 1,
  title: 'Clinic visit',
  target: '/conversation/a?at=s&t=42000',
  excerpt: 'Keep the dose',
};
const LABS = {
  n: 2,
  title: 'Lab [results]',
  page: 2,
  target: '/document/d?page=2',
  excerpt: 'LDL 85',
};

test('answerParts splits paragraphs and keeps unknown markers as text', () => {
  const parts =
      answerParts('The dose stays. [[1]]\n\nAsk again. [[3]]', [VISIT]);

  assert.deepEqual(parts, [
    [{text: 'The dose stays. '}, {citation: VISIT}],
    [{text: 'Ask again. '}, {text: '[[3]]'}],
  ]);
});

test('answerMarkdown numbers the text and links each source', () => {
  const markdown = answerMarkdown('Dose. [[1]] LDL. [[2]]', [VISIT, LABS]);

  assert.equal(
      markdown,
      'Dose. [1] LDL. [2]\n\n## Sources\n\n' +
          '- 1. [Clinic visit](/conversation/a?at=s&t=42000)\n' +
          '- 2. [Lab results, page 2](/document/d?page=2)');
  assert.equal(answerMarkdown('Not covered', []), 'Not covered');
});

test('sourceTarget turns a typed source ID into its app link', () => {
  assert.equal(sourceTarget('document:a b'), '/document/a%20b');
  assert.equal(sourceTarget('page:p1'), '/page/p1');
});

test('parseTarget reads app links and refuses everything else', () => {
  assert.deepEqual(parseTarget('/conversation/abc?at=seg-1&t=42000'), {
    kind: 'conversation',
    id: 'abc',
    segment: 'seg-1',
    ms: 42000,
    page: null,
    href: '/conversation/abc?at=seg-1&t=42000',
  });
  assert.equal(parseTarget('/document/d1?page=3').page, 3);
  assert.equal(parseTarget('/conversation/abc').ms, null);
  for (const target
           of ['/settings', '/page/x/extra', 'https://example.com/page/x']) {
    assert.equal(parseTarget(target), null, target);
  }
});

test('uploadKind routes audio and documents and refuses other files', () => {
  assert.equal(uploadKind('visit.MP3'), 'audio');
  for (const name of ['lab.pdf', 'scan.PNG', 'photo.jpeg', 'photo.jpg']) {
    assert.equal(uploadKind(name), 'document', name);
  }
  for (const name of ['notes.txt', 'clip.m4a', 'pdf']) {
    assert.equal(uploadKind(name), null, name);
  }
});

test('candidates leaves out this notebook and sorts newest first', () => {
  const items = toCandidates(
      [{
        id: 'c',
        title: 'Lung visit',
        notebook_id: null,
        recorded_at: '2026-03'
      }],
      [{id: 'p', title: 'Skin note', notebook_id: 'b', updated_at: '2026-02'}],
      [{
        id: 'd',
        title: 'Lung report',
        notebook_id: 'a',
        record_date: '2026-01',
        created_at: '2026-04',
      }]);

  assert.deepEqual(
      candidates(items, 'a', '').map((item) => item.kind),
      ['recording', 'note']);
  assert.deepEqual(
      candidates(items, 'x', ' lung ').map((item) => item.id), ['c', 'd']);
  const many = Array.from(
      {length: 40},
      (_, i) => ({title: `n${i}`, notebook_id: null, at: `${i}`}));
  assert.equal(candidates(many, 'a', '').length, 30);
});

test('document helpers describe the file and link to a PDF page', () => {
  const pdf = {
    id: 'd 1',
    media_type: 'application/pdf',
    page_count: 3,
    record_date: '2026-04-02',
  };
  const photo = {id: 'p', media_type: 'image/png', page_count: 1};

  assert.equal(describe(pdf), 'PDF · 3 pages · 2026-04-02');
  assert.equal(describe(photo), 'Image');
  assert.equal(fileUrl(pdf, 2), '/api/v1/documents/d%201/file#page=2');
  assert.equal(fileUrl(photo, 2), '/api/v1/documents/p/file');
});

const {measurement, partition} =
    require('../src/stellis/static/health-facts.js');

test('health fact helpers format values and split by status', () => {
  assert.equal(measurement({value: '3.4', unit: 'mg/dL'}), '3.4 mg/dL');
  assert.equal(measurement({value: '', unit: ''}), '');
  const {suggested, accepted} = partition(
      [{id: 'a', status: 'accepted'}, {id: 'b', status: 'suggested'}]);
  assert.deepEqual(
      [suggested.map((fact) => fact.id), accepted.map((fact) => fact.id)],
      [['b'], ['a']]);
});

const {groupByYear, shortDate} = require('../src/stellis/static/timeline.js');

test('timeline helpers group by year and format short dates', () => {
  const groups = groupByYear(
      [{date: '2026-04-02'}, {date: '2026-03-26'}, {date: '1997-12-04'}]);
  assert.deepEqual(
      groups.map((group) => [group.year, group.entries.length]),
      [['2026', 2], ['1997', 1]]);
  assert.equal(shortDate('2026-04-02'), 'Apr 2');
});

const {keptItems, recordLink} = require('../src/stellis/static/records.js');

test('records helpers link records and keep checked group items', () => {
  assert.equal(
      recordLink({kind: 'recording', id: 'c 1'}), '/conversation/c%201');
  assert.equal(recordLink({kind: 'document', id: 'd'}), '/document/d');
  const items = [
    {kind: 'document', id: 'a', title: 'A'},
    {kind: 'recording', id: 'b', title: 'B'}
  ];
  assert.deepEqual(
      keptItems(items, new Set(['document:a'])),
      [{kind: 'recording', id: 'b'}]);
});

globalThis.StellisNotebookChat =
    require('../src/stellis/static/notebook-chat.js');
const {sections} = require('../src/stellis/static/summary.js');

test('summary sections split headings and cited paragraphs', () => {
  const citation = {n: 1, title: 'Labs', target: '/document/d?page=1'};
  const result = sections(
      '## Overview\nWeight fell. [[1]]\n\nWalks daily.\n## Medications\n',
      [citation]);
  assert.deepEqual(
      result.map((section) => section.heading), ['Overview', 'Medications']);
  assert.deepEqual(
      result[0].paragraphs,
      [[{text: 'Weight fell. '}, {citation}], [{text: 'Walks daily.'}]]);
  assert.deepEqual(result[1].paragraphs, []);
});

test('starter questions come from the notebook\'s own results, dates and sources', () => {
  const point = (date, own = true) => ({date, own});
  const brief = {
    levels: [
      {name: 'Ferritin', unit: 'ug/L', latest: {value: 13, status: {label: 'Low', tone: 'alert'}}, points: [point('2022-01-21'), point('2026-06-15')]},
      {name: 'Ferrous sulfate', unit: 'mg', latest: {value: 300, status: null}, points: [point('2026-06-19')]},
    ],
    coming_up: [{title: 'Repeat CBC and ferritin', status: 'planned'}],
  };
  const sources = [{title: 'Fatigue laboratory investigation', status: 'ready'}, {title: 'CBC results', status: 'ready'}];
  assert.deepEqual(starterQuestions(brief, sources), [
    'What does my Ferritin of 13 ug/L mean?',
    'How has my Ferritin changed since 2022?',
    'What should I ask before Repeat CBC and ferritin?',
    'Summarize Fatigue laboratory investigation',
  ]);
  assert.deepEqual(starterQuestions(null, []), []);
  assert.deepEqual(starterQuestions({levels: [], coming_up: []}, sources),
      ['Summarize Fatigue laboratory investigation', 'What changed across these records?']);
});
