"""One calendar projection with explicit account-local tracking preferences."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from stellis import calendar, health_preferences, reproductive


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLIS_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(calendar, 'connections', lambda: [])


def event_view(offset=0):
    return asyncio.run(calendar.event_view('2026-01-01', '2026-01-31', offset))


def test_period_opt_in_hides_features_without_deleting_records():
    record = reproductive.save('period', {'start': '2026-01-02', 'end': '2026-01-05', 'flow': 'Light'})
    assert health_preferences.preferences() == {'experiences_periods': None}
    assert event_view()['events'] == []
    health_preferences.save(True)
    entries = event_view()['events']
    assert entries[0]['source_id'] == record['id']
    assert entries[0]['start_date'] == '2026-01-02'
    health_preferences.save(False)
    assert event_view()['events'] == []
    assert reproductive.list_records()['records'][0]['id'] == record['id']
    health_preferences.save(None)
    assert health_preferences.preferences()['experiences_periods'] is None


def test_appointments_survive_disabling_periods(monkeypatch):
    monkeypatch.setattr(calendar, 'connections', lambda: [{'provider': 'google', 'connected': True}])
    meeting = {'provider': 'google', 'id': 'visit', 'title': 'Visit', 'start': '2026-01-02',
               'end': '2026-01-04', 'all_day': True}
    monkeypatch.setattr(calendar, 'events', AsyncMock(return_value=[meeting]))
    health_preferences.save(False)
    result = event_view()
    assert len(result['events']) == 1
    assert result['events'][0]['kind'] == 'appointment'
    assert result['events'][0]['end_date'] == '2026-01-03'
    assert result['events'][0]['appointment']['id'] == 'visit'


def test_appointment_dates_use_the_viewing_timezone(monkeypatch):
    monkeypatch.setattr(calendar, 'connections', lambda: [{'provider': 'google', 'connected': True}])
    meeting = {'provider': 'google', 'id': 'visit', 'title': 'Visit', 'start': '2026-01-02T01:00:00+00:00',
               'end': '2026-01-02T02:00:00+00:00', 'all_day': False}
    monkeypatch.setattr(calendar, 'events', AsyncMock(return_value=[meeting]))
    assert event_view(480)['events'][0]['start_date'] == '2026-01-01'
    assert event_view(-600)['events'][0]['start_date'] == '2026-01-02'


def test_provider_failure_keeps_local_calendar_entries(monkeypatch):
    monkeypatch.setattr(calendar, 'connections', lambda: [{'provider': 'google', 'connected': True}])
    monkeypatch.setattr(calendar, 'events', AsyncMock(side_effect=calendar.CalendarError('Reconnect calendar')))
    health_preferences.save(True)
    reproductive.save('period', {'start': '2026-01-02', 'end': '2026-01-05', 'flow': 'Light'})
    result = event_view()
    assert result['events'][0]['kind'] == 'period'
    assert result['errors'][0]['provider'] == 'google'


def test_health_preferences_are_workspace_local(tmp_path, monkeypatch):
    health_preferences.save(True)
    monkeypatch.setenv('STELLIS_DATA_DIR', str(tmp_path/'another-account'))
    assert health_preferences.preferences()['experiences_periods'] is None


def test_date_range_is_bounded():
    with pytest.raises(ValueError):
        asyncio.run(calendar.event_view('2026-01-03', '2026-01-01'))
    with pytest.raises(ValueError):
        asyncio.run(calendar.event_view('2026-01-01', '2028-01-01'))


def test_named_timezone_handles_a_month_with_clock_changes(monkeypatch):
    monkeypatch.setattr(calendar, 'connections', lambda: [{'provider': 'google', 'connected': True}])
    provider = AsyncMock(return_value=[])
    monkeypatch.setattr(calendar, 'events', provider)
    asyncio.run(calendar.event_view('2026-03-01', '2026-03-31', time_zone='America/New_York'))
    _, first, until = provider.call_args.args
    assert first.isoformat() == '2026-03-01T05:00:00+00:00'
    assert until.isoformat() == '2026-04-01T04:00:00+00:00'


def test_records_and_plans_keep_their_provenance(monkeypatch):
    from stellis import calendar_sources
    monkeypatch.setattr(calendar_sources.timeline, 'entries', lambda **kwargs: [
        {'title': 'Care letter', 'date': '2026-01-03', 'target': '/document/letter', 'notebook_id': 'nutrition'}])
    monkeypatch.setattr(calendar_sources.todos, 'gather', lambda: {'groups': [
        {'source': {'kind': 'page', 'id': 'plan', 'notebook_id': 'nutrition'},
         'items': [{'id': 'followup', 'text': 'Arrange follow-up', 'due_date': '2026-01-04', 'due_time': '09:00'}]}]})
    entries = event_view()['events']
    assert [entry['kind'] for entry in entries] == ['record', 'plan']
    assert entries[0]['target'] == '/document/letter'
    assert entries[1]['target'] == '/page/plan'
    assert entries[1]['start'] == '2026-01-04T09:00'
    assert all(entry['notebook_id'] == 'nutrition' for entry in entries)
