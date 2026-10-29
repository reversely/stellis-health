"""Daily answers are explicit, reversible and shared with the calendar."""
from datetime import date, timedelta
import pytest
from stellis import health_observations, health_preferences, calendar_sources


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLIS_DATA_DIR', str(tmp_path))


def test_daily_answer_replace_and_undo():
    day = date.today().isoformat()
    health_observations.save(day, 'periods', 'Light')
    health_observations.save(day, 'periods', 'Heavy')
    assert health_observations.entries() == [{'day': day, 'track_id': 'periods', 'value': 'Heavy'}]
    health_observations.save(day, 'periods', None)
    assert health_observations.entries() == []


def test_calendar_respects_explicit_answer_and_preference():
    day = date.today().isoformat()
    health_observations.save(day, 'periods', 'No bleeding')
    assert calendar_sources.tracking_events(day, day) == []
    health_preferences.save(True)
    events = calendar_sources.tracking_events(day, day)
    assert len(events) == 1
    assert events[0]['title'] == 'No bleeding'
    assert events[0]['start_date'] == events[0]['end_date'] == day
    health_preferences.save(False)
    assert calendar_sources.tracking_events(day, day) == []
    assert len(health_observations.entries()) == 1


def test_future_and_unlisted_status_rejected():
    with pytest.raises(ValueError):
        health_observations.save((date.today()+timedelta(days=1)).isoformat(), 'periods', 'Light')
    with pytest.raises(ValueError):
        health_observations.save(date.today().isoformat(), 'periods', 'Missed')


def test_one_tap_flow_retains_unspecified_intensity_and_calendar_title():
    health_preferences.save(True)
    days = [(date.today()-timedelta(days=offset)).isoformat() for offset in range(3)]
    for day in days:
        health_observations.save(day, 'periods', 'Had flow')
    assert len(health_observations.entries()) == 3
    assert all(event['title'] == 'Had flow' for event in calendar_sources.tracking_events(min(days), max(days)))
    health_observations.save(days[1], 'periods', None)
    assert {entry['day'] for entry in health_observations.entries()} == {days[0], days[2]}
