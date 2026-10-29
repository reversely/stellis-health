"""Tracker registry: medicine course schedules, run-out dates, links and migration."""
import datetime as dt

import pymupdf
import pytest
from fastapi.testclient import TestClient

from stellis import calendar_sources, documents, notebooks, storage, trackers


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLIS_DATA_DIR', str(tmp_path))


def course(**changes):
    data = dict(name='Ferrous sulfate', dose_amount=300, dose_unit='mg', schedule_type='every',
                every_days=2, start='2026-06-19', units_per_fill=45, units_per_dose=1, repeats=1)
    return trackers.save('medicine', {**data, **changes})


def stored(record_id, today='2026-08-21'):
    return next(r for r in trackers.list_records(today=dt.date.fromisoformat(today)) if r['id'] == record_id)


def fills(record_id):
    return [item['date'] for item in stored(record_id)['summary']['derived'] if item['key'].startswith('fill-')]


def test_every_other_day_fill_runs_out_on_last_covered_dose():
    saved = course()
    # 45 doses every other day from June 19 cover up to the 45th dose day.
    assert fills(saved['id']) == ['2026-09-15', '2026-12-14']
    first = stored(saved['id'])['summary']['derived'][0]
    assert first['status'] == 'calculated'
    assert 'fill 1 of 2' in first['title']


def test_skipped_dose_extends_the_fill_by_one_scheduled_dose():
    saved = course()
    trackers.log(saved['id'], '2026-06-21', 'skipped')
    assert fills(saved['id'])[0] == '2026-09-17'
    trackers.log(saved['id'], '2026-06-21', 'taken')
    assert fills(saved['id'])[0] == '2026-09-15'


@pytest.mark.parametrize('changes,expected,unexpected', [
    (dict(schedule_type='daily'), ['2026-06-19', '2026-06-20'], ['2026-06-18']),
    (dict(), ['2026-06-19', '2026-06-21'], ['2026-06-20']),
    (dict(schedule_type='weekdays', weekdays=[0, 4]), ['2026-06-19', '2026-06-22'], ['2026-06-20', '2026-06-23']),
    (dict(end='2026-06-25'), ['2026-06-25'], ['2026-06-27']),
])
def test_schedule_rules(changes, expected, unexpected):
    saved = course(**changes)
    for day in expected:
        trackers.log(saved['id'], day, 'taken')
    for day in unexpected:
        with pytest.raises(ValueError, match='scheduled dose day'):
            trackers.log(saved['id'], day, 'taken')


def test_as_needed_logs_any_day_in_the_course_and_has_no_run_out():
    saved = course(schedule_type='as_needed', end='2026-06-30')
    trackers.log(saved['id'], '2026-06-20', 'taken', 150)
    with pytest.raises(ValueError):
        trackers.log(saved['id'], '2026-07-01', 'taken')
    record = stored(saved['id'])
    assert record['outcomes']['2026-06-20']['status']=='taken'
    assert record['outcomes']['2026-06-20']['value']==150
    assert record['outcomes']['2026-06-20']['dose_amount']==150
    assert record['outcomes']['2026-06-20']['taken_at'] is None
    assert fills(saved['id']) == []
    assert record['summary']['next_expected'] is None


def test_today_summary_and_next_dose():
    saved = course()
    trackers.log(saved['id'], '2026-08-20', 'taken')
    summary = stored(saved['id'], today='2026-08-21')['summary']
    assert summary['expected_today'] is False
    assert summary['next_expected'] == '2026-08-22'
    assert summary['taken_count'] == 1
    assert summary['schedule_text'] == 'Every other day'
    assert stored(saved['id'], today='2026-08-22')['summary']['expected_today'] is True
    assert summary['loggable_today'] is False


def test_weekday_schedule_reads_as_a_phrase():
    saved = course(schedule_type='weekdays', weekdays=[4, 0, 2])
    assert stored(saved['id'])['summary']['schedule_text'] == 'On Mon, Wed and Fri'


def test_end_date_limits_fills_and_adds_a_planned_last_dose():
    saved = course(end='2026-10-01')
    derived = stored(saved['id'])['summary']['derived']
    assert [item['key'] for item in derived] == ['fill-1', 'end']
    assert derived[-1]['status'] == 'planned'
    assert 0 < stored(saved['id'])['summary']['progress'] < 1


def test_edit_keeps_outcomes_and_kind_cannot_change():
    saved = course()
    trackers.log(saved['id'], '2026-06-19', 'taken')
    trackers.save('medicine', {**saved, 'dose_amount': 200}, saved['id'])
    record = stored(saved['id'])
    assert record['dose_amount'] == 200
    assert set(record['outcomes'])=={'2026-06-19'}
    assert record['outcomes']['2026-06-19']['status']=='taken'
    assert record['outcomes']['2026-06-19']['dose_amount']==300
    assert record['outcomes']['2026-06-19']['taken_at'] is None
    with pytest.raises(ValueError, match='cannot change'):
        trackers.save('pill', dict(name='Pack', start='2026-01-01', active=21, break_days=7, time='09:00'),
                      saved['id'])


@pytest.mark.parametrize('changes,message', [
    (dict(dose_unit='spoons'), 'dose unit'),
    (dict(schedule_type='hourly'), 'schedule'),
    (dict(every_days=1), 'days between doses'),
    (dict(schedule_type='weekdays', weekdays=[]), 'weekday'),
    (dict(end='2026-06-01'), 'follow the start'),
    (dict(units_per_fill=4.5), 'whole number'),
    (dict(name=''), 'medicine name'),
    (dict(time='25:00'), 'reminder time'),
])
def test_course_validation(changes, message):
    with pytest.raises(ValueError, match=message):
        course(**changes)


def pdf(pages=2):
    with pymupdf.open() as doc:
        for _ in range(pages):
            doc.new_page().insert_text((40, 60), 'Ferrous sulfate 300 mg, 45 tablets, 1 repeat')
        return doc.tobytes()


def test_links_validate_and_deleted_sources_are_dropped():
    with pytest.raises(ValueError, match='notebook'):
        course(notebook_id='missing')
    with pytest.raises(ValueError, match='source record'):
        course(source_document_id='missing')
    book = notebooks.create('Blood Health')
    record, _ = documents.create('prescription.pdf', pdf())
    with pytest.raises(ValueError, match='source page'):
        course(source_document_id=record['id'], source_page=3)
    saved = course(notebook_id=book['id'], source_document_id=record['id'], source_page=2)
    linked = stored(saved['id'])
    assert (linked['notebook_id'], linked['source_page'], linked['source_title']) == (book['id'], 2, record['title'])
    assert [r['id'] for r in trackers.list_records(notebook_id=book['id'])] == [saved['id']]
    # An edit without link keys keeps the stored links.
    trackers.save('medicine', {k: v for k, v in saved.items() if k not in trackers.LINK_KEYS}, saved['id'])
    assert stored(saved['id'])['notebook_id'] == book['id']
    documents.delete(record['id'])
    assert stored(saved['id'])['source_document_id'] is None


def test_legacy_tables_gain_columns_without_losing_records():
    with storage.connect() as conn:
        conn.execute('CREATE TABLE reproductive_records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL)')
        conn.execute('''INSERT INTO reproductive_records VALUES ('pack', 'pill',
            '{"name": "Pack", "start": "2026-01-01", "active": 21, "break_days": 7, "time": "09:00"}')''')
        conn.execute('CREATE TABLE pill_doses (pill_id TEXT, day TEXT, PRIMARY KEY (pill_id, day))')
        conn.execute("INSERT INTO pill_doses VALUES ('pack', '2026-01-02')")
    record = trackers.list_records()[0]
    assert record['name'] == 'Pack' and record['notebook_id'] is None
    assert set(record['outcomes'])=={'2026-01-02'}
    assert record['outcomes']['2026-01-02']['status']=='taken'
    assert record['outcomes']['2026-01-02']['taken_at'] is None
    assert record['outcomes']['2026-01-02']['logged_at'] is None


def test_course_dates_reach_the_calendar_without_the_cycle_preference():
    saved = course()
    events = calendar_sources.derived_events('2026-09-01', '2026-09-30', '2026-08-21')
    assert [(e['start_date'], e['date_status'], e['track_id']) for e in events
            if e['start_date'] <= '2026-12-31'][:1] == [('2026-09-15', 'Calculated', 'medicines')]
    assert events[0]['source_id'] == saved['id']


def test_routes_round_trip():
    from stellis import server
    client = TestClient(server.app)
    kinds = client.get('/api/v1/trackers/kinds').json()['kinds']
    medicine = next(kind for kind in kinds if kind['key'] == 'medicine')
    assert medicine['gate'] is None and medicine['add'] == 'Add medicine course'
    assert next(kind for kind in kinds if kind['key'] == 'period')['gate'] == 'experiences_periods'
    created = client.post('/api/v1/trackers/medicine', json=dict(
        name='Iron', dose_amount=300, dose_unit='mg', schedule_type='daily', start='2026-06-19')).json()
    assert client.put(f"/api/v1/trackers/records/{created['id']}/outcomes/2026-06-19",
                      json={'status': 'taken'}).json() == {'saved': True}
    listed = client.get('/api/v1/trackers', params={'today': '2026-06-20'}).json()['records']
    assert listed[0]['summary']['taken_count'] == 1
    assert client.post('/api/v1/trackers/medicine', json={'name': 'x'}).status_code == 422
    assert client.put('/api/v1/trackers/records/missing', json={}).status_code == 404


@pytest.mark.parametrize('data,start,due', [
    (dict(dating_basis='transfer', basis_date='2024-11-15', embryo_day=5), '2024-10-27', '2025-08-03'),
    (dict(dating_basis='transfer', basis_date='2024-11-15', embryo_day=3), '2024-10-29', '2025-08-05'),
    (dict(dating_basis='last_period', basis_date='2023-06-01'), '2023-06-01', '2024-03-07'),
    # 280 days across a leap day.
    (dict(dating_basis='last_period', basis_date='2024-01-01'), '2024-01-01', '2024-10-07'),
    (dict(dating_basis='ultrasound', basis_date='2024-12-13', scan_weeks=6, scan_days=5), '2024-10-27', '2025-08-03'),
])
def test_pregnancy_dating(data, start, due):
    saved = trackers.save('pregnancy', data)
    summary = stored(saved['id'], today='2025-07-25')['summary']
    assert (summary['pregnancy']['start'], summary['pregnancy']['due']) == (start, due)
    assert summary['derived'][0] == {'key': 'due', 'date': due, 'status': 'due', 'title': 'Due date',
                                     'detail': trackers.basis_text(saved)}


def test_pregnancy_weeks_trimester_and_booked_birth():
    saved = trackers.save('pregnancy', dict(dating_basis='transfer', basis_date='2024-11-15', embryo_day=5,
                                            booked_birth='2025-07-30'))
    summary = stored(saved['id'], today='2025-07-25')['summary']
    assert (summary['pregnancy']['weeks'], summary['pregnancy']['days'], summary['pregnancy']['trimester']) == \
        (38, 5, 'Third trimester')
    assert [item['key'] for item in summary['derived']] == ['due', 'birth']
    assert stored(saved['id'], today='2024-12-13')['summary']['pregnancy']['trimester'] == 'First trimester'


@pytest.mark.parametrize('data,message', [
    (dict(dating_basis='guess', basis_date='2024-11-15'), 'how the pregnancy'),
    (dict(dating_basis='transfer', basis_date='2024-11-15'), 'embryo age'),
    (dict(dating_basis='ultrasound', basis_date='2024-11-15'), 'weeks at the scan'),
    (dict(dating_basis='last_period', basis_date='2099-01-01'), 'future'),
    (dict(dating_basis='last_period', basis_date='2024-06-01', booked_birth='2024-05-01'), 'booked birth'),
])
def test_pregnancy_validation(data, message):
    with pytest.raises(ValueError, match=message):
        trackers.save('pregnancy', data)
