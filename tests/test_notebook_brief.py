"""Notebook Brief: reference intervals from source lines, levels, coming up and the day strip."""
import datetime as dt
import uuid

import pymupdf
import pytest

from stellis import care_events, documents, health_facts, notebook_brief, notebooks, storage, trackers


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLIS_DATA_DIR', str(tmp_path))


TODAY = dt.date(2026, 8, 21)


@pytest.mark.parametrize('quote,value,expected', [
    ('Ferritin 13 ug/L 15-150 L', '13', {'low': 15, 'high': 150, 'flag': 'low'}),
    ('Hemoglobin 122 g/L 120-160', '122', {'low': 120, 'high': 160, 'flag': None}),
    ('TSH 6.1 mIU/L 0.4 – 4.0 H', '6.1', {'low': .4, 'high': 4, 'flag': 'high'}),
    ('Ferritin 42 ug/L', '42', {'low': None, 'high': None, 'flag': None}),
    ('Collected 2026-06-15 Ferritin 13 ug/L 150-15', '13', {'low': None, 'high': None, 'flag': None}),
])
def test_reference_comes_from_the_source_line(quote, value, expected):
    assert notebook_brief.reference(quote, value) == expected


def test_status_prefers_the_lab_flag():
    point = {'value': 13, 'low': 15, 'high': 150, 'flag': 'low'}
    assert notebook_brief.status(point) == {'label': 'Low', 'tone': 'alert', 'by_lab': True}
    assert notebook_brief.status({**point, 'flag': None})['label'] == 'Below range'
    assert notebook_brief.status({**point, 'value': 40, 'flag': None})['label'] == 'In range'
    assert notebook_brief.status({**point, 'low': None, 'flag': None}) is None


def report(book):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 60), 'Lab report')
        record, _ = documents.create(f'lab-{uuid.uuid4().hex[:6]}.pdf', pdf.tobytes())
    documents.update(record['id'], notebook_id=book, move=True)
    return record['id']


def fact(document_id, name, value, unit, date, quote):
    with storage.connect() as conn:
        health_facts._create_table(conn)
        conn.execute('''INSERT INTO health_facts (id, document_id, page, kind, name, value, unit, fact_date,
            quote, status, created_at) VALUES (?, ?, 1, 'lab_result', ?, ?, ?, ?, ?, 'accepted', ?)''',
                     (str(uuid.uuid4()), document_id, name, value, unit, date, quote, date))


def test_levels_group_by_name_and_unit_and_mark_other_notebooks():
    blood = notebooks.create('Blood Health')['id']
    pregnancy = notebooks.create('Pregnancy')['id']
    now, before, elsewhere = report(blood), report(blood), report(pregnancy)
    fact(now, 'Ferritin', '13', 'ug/L', '2026-06-15', 'Ferritin 13 ug/L 15-150 L')
    fact(before, 'ferritin', '42', 'µg/L', '2025-10-10', 'Ferritin 42 ug/L 15-150')
    fact(elsewhere, 'Ferritin', '9', 'ug/L', '2025-03-01', 'Ferritin 9 ug/L 15-150 L')
    fact(elsewhere, 'hCG', '1200', 'IU/L', '2025-03-01', 'hCG 1200 IU/L')
    fact(now, 'Comment', 'normal', '', '2026-06-15', 'Comment normal')
    levels = notebook_brief.levels(blood)
    assert [level['name'] for level in levels] == ['Ferritin']
    ferritin = levels[0]
    assert [p['value'] for p in ferritin['points']] == [9, 42, 13]
    assert [p['own'] for p in ferritin['points']] == [False, True, True]
    assert (ferritin['latest']['value'], ferritin['previous']['value']) == (13, 42)
    assert ferritin['flagged'] == {'low': 2, 'high': 0, 'total': 3}
    assert ferritin['reference'] == {'low': 15, 'high': 150, 'shared': True}
    assert ferritin['other_notebooks'] == ['Pregnancy']
    assert ferritin['latest']['status']['label'] == 'Low'


def test_coming_up_and_strip_from_courses_and_care_events():
    book = notebooks.create('Blood Health')['id']
    course = trackers.save('medicine', dict(name='Ferrous sulfate', dose_amount=300, dose_unit='mg',
                                            schedule_type='every', every_days=2, start='2026-06-19',
                                            units_per_fill=45, supply_unit='tablets', repeats=1, notebook_id=book))
    trackers.log(course['id'], '2026-08-20', 'taken')
    care_events.add('Repeat CBC and ferritin', '2026-09-20T09:00:00+00:00', 'planned', book)
    care_events.add('Earlier visit', '2026-08-01T09:00:00+00:00', 'scheduled', book)
    result = notebook_brief.brief(book, TODAY)
    upcoming = result['coming_up']
    assert [item['title'] for item in upcoming] == [
        'Ferrous sulfate: fill 1 of 2 runs out', 'Repeat CBC and ferritin', 'Ferrous sulfate: fill 2 of 2 runs out']
    assert upcoming[0]['detail'] == '45 tablets per fill, every other day from June 19. 1 repeat allowed.'
    assert (upcoming[1]['precision'], upcoming[1]['status'], upcoming[1]['detail']) == ('month', 'planned', 'No date booked.')
    days = {day['date']: day for day in result['strip']}
    assert len(result['strip']) == 15 and days['2026-08-21']['today']
    assert days['2026-08-20']['marks'] == [{'record_id': course['id'], 'state': 'taken'}]
    assert days['2026-08-18']['marks'] == [{'record_id': course['id'], 'state': 'missed'}]
    assert days['2026-08-22']['marks'] == [{'record_id': course['id'], 'state': 'expected'}]
    assert days['2026-08-21']['marks'] == []
    assert result['treatments'] == [{'name': 'Ferrous sulfate', 'start': '2026-06-19', 'end': None}]


def test_notebook_readings_are_levels_too():
    from stellis import readings
    book = notebooks.create('Heart')['id']
    readings.add('weight', 70.2, taken_at='2026-08-20T08:00:00+00:00', notebook_id=book)
    readings.add('weight', 70.6, taken_at='2026-08-21T08:00:00+00:00', notebook_id=book)
    level = notebook_brief.levels(book)[0]
    assert (level['name'], level['unit'], level['latest']['value']) == ('Weight', 'kg', 70.6)
    assert level['reference'] == {'low': None, 'high': None, 'shared': True}


def test_unknown_notebook():
    with pytest.raises(LookupError):
        notebook_brief.brief('missing', TODAY)


def test_pregnancy_brief_is_pending_only_and_ends_at_a_booked_birth():
    book = notebooks.create('Pregnancy')['id']
    today = dt.date(2025, 7, 25)
    record = trackers.save('pregnancy', dict(dating_basis='transfer', basis_date='2024-11-15', embryo_day=5,
                                             notebook_id=book))
    care_events.add('Review if the baby has not arrived', '2025-07-31T09:00:00+00:00', 'scheduled', book)
    care_events.add('Postnatal check', '2025-08-20T09:00:00+00:00', 'scheduled', book)
    care_events.add('Earlier scan', '2025-07-01T09:00:00+00:00', 'scheduled', book)
    result = notebook_brief.brief(book, today)
    assert result['pregnancy']['id'] == record['id']
    assert [item['title'] for item in result['coming_up']] == [
        'Review if the baby has not arrived', 'Due date', 'Postnatal check']
    days = {day['date']: day['marks'] for day in result['strip']}
    assert days['2025-07-31'] == [{'event': 'Review if the baby has not arrived', 'state': 'event', 'status': 'scheduled'}]
    assert days['2025-08-03'] == [{'event': 'Due date', 'state': 'event', 'status': 'due'}]
    trackers.save('pregnancy', {**record, 'booked_birth': '2025-07-30'}, record['id'])
    titles = [item['title'] for item in notebook_brief.brief(book, today)['coming_up']]
    assert titles == ['Booked birth', 'Due date']


def test_a_finished_pregnancy_leaves_the_brief():
    book = notebooks.create('Pregnancy')['id']
    trackers.save('pregnancy', dict(dating_basis='last_period', basis_date='2023-01-01', notebook_id=book))
    assert notebook_brief.brief(book, dt.date(2025, 7, 25))['pregnancy'] is None
