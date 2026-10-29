"""Joined logging: items, one transaction for up to ten entries, and exact undo."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from stellis import goals, health_observations, health_preferences, readings, storage, tracker_log, trackers


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLIS_DATA_DIR', str(tmp_path))


TODAY = dt.date.today()
DAY = TODAY.isoformat()


def course(**changes):
    data = dict(name='Ferrous sulfate', dose_amount=300, dose_unit='mg', schedule_type='daily',
                start=(TODAY - dt.timedelta(days=10)).isoformat())
    return trackers.save('medicine', {**data, **changes})


def outcome(record_id):
    with storage.connect() as conn:
        row = conn.execute('SELECT status FROM pill_doses WHERE pill_id=? AND day=?', (record_id, DAY)).fetchone()
    return row['status'] if row else None


def goal_done(goal_id, day=DAY):
    with storage.connect() as conn:
        return conn.execute('SELECT 1 FROM coach_goal_days WHERE goal_id=? AND day=?', (goal_id, day)).fetchone() is not None


def test_items_follow_registry_gates_and_sources():
    iron = course()
    walk = goals.create({'activity': 'walk', 'amount': 20, 'times_per_week': 3})
    readings.add('pulse', 70)
    listed = {item['id']: item for item in tracker_log.items()}
    assert listed['tracker:' + iron['id']]['state']['expected'] is True
    assert listed['tracker:' + iron['id']]['control']['choices'] == [['taken', 'Taken'], ['skipped', 'Skipped']]
    assert listed['goal:' + walk['id']]['control'] == {'type': 'done'}
    assert listed['reading:pulse']['control']['unit'] == 'bpm'
    assert 'observation:periods' not in listed
    health_preferences.save(True)
    assert tracker_log.items()[len([i for i in listed if i.startswith('tracker:')])]['id'] == 'observation:periods'


def test_ended_courses_are_not_offered():
    ended = course(end=(TODAY - dt.timedelta(days=2)).isoformat())
    assert 'tracker:' + ended['id'] not in {item['id'] for item in tracker_log.items()}


def test_batch_saves_every_kind_together_and_undo_restores_exactly():
    health_preferences.save(True)
    iron = course()
    walk = goals.create({'activity': 'walk', 'amount': 20, 'times_per_week': 3})
    health_observations.save(DAY, 'periods', 'Light')
    result = tracker_log.save([
        {'item': 'tracker:' + iron['id'], 'value': 'taken'},
        {'item': 'goal:' + walk['id'], 'value': True},
        {'item': 'observation:periods', 'value': 'Heavy'},
        {'item': 'reading:blood_pressure', 'value': 120, 'value2': 80},
    ], DAY, '00:00')  # Today at 08:30 is a future reading in early-morning runs.
    assert result['saved'] == 4
    assert outcome(iron['id']) == 'taken' and goal_done(walk['id'])
    assert health_observations.entries()[0]['value'] == 'Heavy'
    pressure = [p for p in readings.stored_points() if p['measure'] == 'blood_pressure']
    assert len(pressure) == 1 and pressure[0]['value2'] == 80
    assert tracker_log.undo(result['batch_id']) == {'restored': 4, 'kept': 0}
    assert outcome(iron['id']) is None and not goal_done(walk['id'])
    assert health_observations.entries()[0]['value'] == 'Light'
    assert [p for p in readings.stored_points() if p['measure'] == 'blood_pressure'] == []
    assert tracker_log.undo(result['batch_id']) == {'restored': 0, 'kept': 0}


def test_one_failing_entry_saves_nothing_and_reports_its_position():
    iron = course()
    walk = goals.create({'activity': 'walk', 'amount': 20, 'times_per_week': 3})
    with pytest.raises(tracker_log.EntryErrors) as caught:
        tracker_log.save([
            {'item': 'tracker:' + iron['id'], 'value': 'taken'},
            {'item': 'goal:' + walk['id'], 'value': True},
            {'item': 'reading:pulse', 'value': 999},
        ], DAY)
    assert list(caught.value.errors) == [2]
    assert outcome(iron['id']) is None and not goal_done(walk['id'])
    with storage.connect() as conn:
        tracker_log._table(conn)
        assert conn.execute('SELECT COUNT(*) FROM log_batches').fetchone()[0] == 0


def test_undo_keeps_entries_changed_since():
    iron = course()
    batch = tracker_log.save([{'item': 'tracker:' + iron['id'], 'value': 'taken'}], DAY)
    trackers.log(iron['id'], DAY, 'skipped')
    assert tracker_log.undo(batch['batch_id']) == {'restored': 0, 'kept': 1}
    assert outcome(iron['id']) == 'skipped'


def test_replacing_an_outcome_restores_the_previous_one():
    iron = course()
    trackers.log(iron['id'], DAY, 'skipped')
    batch = tracker_log.save([{'item': 'tracker:' + iron['id'], 'value': 'taken'}], DAY)
    tracker_log.undo(batch['batch_id'])
    assert outcome(iron['id']) == 'skipped'


@pytest.mark.parametrize('entries,day,message', [
    ([], DAY, '1 to 10'),
    ([{'item': f'reading:pulse{i}', 'value': 70} for i in range(11)], DAY, '1 to 10'),
    ([{'item': 'reading:pulse', 'value': 70}] * 2, DAY, 'once'),
    ([{'item': 'reading:pulse', 'value': 70}], (TODAY + dt.timedelta(days=1)).isoformat(), 'earlier'),
])
def test_batch_limits(entries, day, message):
    with pytest.raises(ValueError, match=message):
        tracker_log.save(entries, day)


def test_yesterday_uses_the_schedule_for_that_day():
    every = course(schedule_type='every', every_days=2, start=(TODAY - dt.timedelta(days=2)).isoformat())
    yesterday = (TODAY - dt.timedelta(days=1)).isoformat()
    with pytest.raises(tracker_log.EntryErrors, match='scheduled dose day'):
        tracker_log.save([{'item': 'tracker:' + every['id'], 'value': 'taken'}], yesterday)
    assert tracker_log.save([{'item': 'tracker:' + every['id'], 'value': 'taken'}], DAY)['saved'] == 1


def test_routes_report_per_entry_errors():
    from stellis import server
    client = TestClient(server.app)
    iron = course()
    listed = client.get('/api/v1/trackers/log/items', params={'today': DAY}).json()
    assert listed['max'] == 10
    bad = client.post('/api/v1/trackers/entries', json={'day': DAY, 'entries': [
        {'item': 'tracker:' + iron['id'], 'value': 'taken'}, {'item': 'reading:pulse', 'value': 999}]})
    assert bad.status_code == 422
    assert bad.json()['detail']['errors'] == {'1': bad.json()['detail']['errors']['1']}
    good = client.post('/api/v1/trackers/entries', json={'day': DAY, 'entries': [
        {'item': 'tracker:' + iron['id'], 'value': 'taken'}]}).json()
    assert client.delete('/api/v1/trackers/entries/' + good['batch_id']).json() == {'restored': 1, 'kept': 0}
    assert client.delete('/api/v1/trackers/entries/missing').status_code == 404


@pytest.mark.parametrize('day,utc_day,utc_hour',[('2024-06-11','2024-06-12',1),('2024-01-11','2024-01-12',2)])
def test_reading_clock_uses_the_explicit_browser_zone(day,utc_day,utc_hour):
 tracker_log.save([{'item':'reading:pulse','value':72}],day,'21:30',time_zone='America/Toronto')
 point=next(p for p in readings.stored_points() if p['measure']=='pulse')
 stamp=dt.datetime.fromisoformat(point['time']).astimezone(dt.timezone.utc)
 assert stamp.date().isoformat()==utc_day;assert stamp.hour==utc_hour;assert stamp.minute==30
