"""Joined logging: record up to ten tracked items in one transaction.

Items come from the tracker registry (doses), daily observations (period
flow), goals and measurements. One batch either saves every entry or none,
and keeps what each entry replaced so Undo restores exactly that state.
See ADR 0086.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

from stellis import dashboard, goals, health_observations, health_preferences, readings, storage, trackers

MAX_ENTRIES = 10
# Measurements logged within this many days join the pinned ones as tiles.
RECENT_READING_DAYS = 90
STATUS_CHOICES = [['taken', 'Taken'], ['skipped', 'Skipped']]


def _table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS log_batches (
        id TEXT PRIMARY KEY, created_at TEXT NOT NULL, entries TEXT NOT NULL,
        undone_at TEXT)''')


def _tracker_items(today, preferences,include_ended_since=None):
    items = []
    for record in trackers.list_records(today=today):
        kind = trackers.KINDS[record['kind']]
        if not kind.logs or (kind.gate and preferences.get(kind.gate) is not True):
            continue
        if record.get('start') and record['start']>today.isoformat():continue
        if record.get('end') and record['end'] < (include_ended_since or today).isoformat():
            continue
        summary = record['summary']
        dose = f"{record['dose_amount']:g} {record['dose_unit']}" if record.get('dose_amount') else ''
        items.append({
            'id': 'tracker:' + record['id'], 'area': kind.area, 'icon': kind.icon, 'title': record['name'],
            'detail': dose, 'notebook_id': record.get('notebook_id'),
            'state': {'expected': summary['expected_today'], 'loggable': summary['loggable_today'],
                      'status': summary['today_status'], 'taken_at': record['outcomes'].get(today.isoformat(),{}).get('taken_at'), 'time_zone':record['outcomes'].get(today.isoformat(),{}).get('time_zone'), 'next': summary['next_expected']},
            'control': {'type': 'status', 'choices': STATUS_CHOICES, 'default': 'taken','dose_unit':record.get('dose_unit'),'default_amount':record.get('dose_amount')},
        })
    return items


def _observation_items(today, preferences):
    period = trackers.KINDS['period']
    if preferences.get(period.gate) is not True:
        return []
    with storage.connect() as conn:
        health_observations._table(conn)
        value = health_observations.value_on(today.isoformat(), 'periods', conn)
    return [{'id': 'observation:periods', 'area': period.area, 'icon': period.icon, 'title': 'Period flow',
             'detail': '', 'notebook_id': None,
             'state': {'expected': False, 'loggable': True, 'status': value, 'next': None},
             'control': {'type': 'choice', 'choices': [[v, v] for v in health_observations.FLOW],
                         'default': health_observations.FLOW[0]}}]


def _goal_items(today):
    now = dt.datetime.now().strftime('%H:%M')
    overview = goals.overview(today.isoformat(), 0, now)
    return [{'id': 'goal:' + goal['id'], 'area': 'Goals', 'icon': 'target', 'title': goal['title'],
             'detail': f"{goal['this_week']} of {goal['times_per_week']} this week", 'notebook_id': None,
             'state': {'expected': False, 'loggable': True, 'status': 'done' if goal['done_today'] else None,
                       'next': None},
             'control': {'type': 'done'}}
            for goal in overview['goals']]


def _reading_items(today):
    pinned = list(dashboard.preferences().get('measurements') or [])
    since = (today - dt.timedelta(days=RECENT_READING_DAYS)).isoformat()
    recent = [point['measure'] for point in readings.stored_points() if point['time'][:10] >= since]
    keys = list(dict.fromkeys(pinned + recent))
    items = []
    for key in keys:
        measure = readings.MEASURES.get(key)
        if measure is None:
            continue
        items.append({'id': 'reading:' + key, 'area': 'Measurements', 'icon': 'activity', 'title': measure.label,
                      'detail': measure.unit, 'notebook_id': None,
                      'state': {'expected': False, 'loggable': True, 'status': None, 'next': None},
                      'control': {'type': 'number', 'unit': measure.unit, 'decimals': measure.decimals,
                                  'min': measure.bounds[0], 'max': measure.bounds[1], 'paired': measure.paired}})
    return items


def items(today=None) -> list[dict]:
    """Every item that can be logged, grouped by area in display order."""
    today = today or dt.date.today()
    preferences = health_preferences.preferences()
    return (_tracker_items(today, preferences) + _observation_items(today, preferences)
            + _goal_items(today) + _reading_items(today))


def _when(day, time):
    selected = trackers.date(day)
    if selected > dt.date.today():
        raise ValueError('Choose today or an earlier day')
    if time:
        dt.time.fromisoformat(time)
    return selected.isoformat()


def _apply(conn, entry, day, time, time_zone=None):
    """Saves one entry and returns what it replaced, for Undo."""
    kind, _, key = str(entry.get('item') or '').partition(':')
    value = entry.get('value')
    if kind == 'tracker':
        if value not in trackers.STATUSES:
            raise ValueError('Choose taken or skipped')
        trackers._table(conn)
        previous = conn.execute('SELECT status,value,taken_at,time_zone,dose_amount,dose_unit,logged_at FROM pill_doses WHERE pill_id=? AND day=?',
                                (key, day)).fetchone()
        taken_at=entry.get('taken_at')
        zone=entry.get('time_zone') or time_zone
        actual_time=entry.get('time') or time
        if not taken_at and actual_time and value=='taken':
            from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
            try:local=ZoneInfo(zone) if zone else dt.datetime.now().astimezone().tzinfo
            except ZoneInfoNotFoundError as exc:raise ValueError('Choose a valid time zone') from exc
            taken_at=dt.datetime.fromisoformat(f'{day}T{actual_time}').replace(tzinfo=local).isoformat()
        trackers.log(key,day,value,entry.get('amount'),conn=conn,taken_at=taken_at,time_zone=zone if taken_at else None)
        after=conn.execute('SELECT status,value,taken_at,time_zone,dose_amount,dose_unit,logged_at FROM pill_doses WHERE pill_id=? AND day=?',(key,day)).fetchone()
        return {'previous':dict(previous) if previous else None,'after':dict(after)}
    if kind == 'observation':
        health_observations._table(conn)
        previous = health_observations.value_on(day, key, conn)
        health_observations.save(day, key, value, conn)
        return {'previous': previous}
    if kind == 'goal':
        goals._require(conn, key)
        previous = conn.execute('SELECT 1 FROM coach_goal_days WHERE goal_id=? AND day=?', (key, day)).fetchone()
        goals.mark(key, day, True, conn)
        return {'previous': previous is not None}
    if kind == 'reading':
        moment=dt.datetime.fromisoformat(f"{day}T{time or '12:00'}")
        if time_zone:
            from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
            try:moment=moment.replace(tzinfo=ZoneInfo(time_zone))
            except ZoneInfoNotFoundError as exc:raise ValueError('Choose a valid time zone') from exc
        taken_at=moment.astimezone().isoformat()
        try:
            number = float(value)
            second = float(entry['value2']) if entry.get('value2') not in (None, '') else None
        except (TypeError, ValueError):
            raise ValueError('Enter a number') from None
        point = readings.add(key, number, second, taken_at, conn=conn)
        return {'reading_id': point['id']}
    raise ValueError('Choose a listed item')


def save(entries, day, time=None, time_zone=None) -> dict:
    """Saves 1 to 10 entries for one date, all or none.

    Returns:
        ``{"batch_id": ..., "saved": n}``.

    Raises:
        EntryErrors: One or more entries failed; nothing was saved.
    """
    if not 1 <= len(entries) <= MAX_ENTRIES:
        raise ValueError(f'Choose 1 to {MAX_ENTRIES} items')
    if len({entry.get('item') for entry in entries}) != len(entries):
        raise ValueError('Choose each item once')
    day = _when(day, time)
    applied, errors = [], {}
    batch_id = str(uuid.uuid4())
    with storage.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _table(conn)
        for index, entry in enumerate(entries):
            try:
                applied.append({**entry, 'day': day, **_apply(conn, entry, day, time, time_zone)})
            except (ValueError, LookupError) as error:
                errors[index] = str(error)
        if errors:
            conn.rollback()
            raise EntryErrors(errors)
        conn.execute('INSERT INTO log_batches (id, created_at, entries) VALUES (?, ?, ?)',
                     (batch_id, dt.datetime.now(dt.timezone.utc).isoformat(), json.dumps(applied)))
    return {'batch_id': batch_id, 'saved': len(applied)}


def undo(batch_id) -> dict:
    """Restores what a batch replaced; entries changed since are left alone."""
    with storage.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _table(conn)
        row = conn.execute('SELECT entries, undone_at FROM log_batches WHERE id=?', (batch_id,)).fetchone()
        if row is None:
            raise LookupError('That log was not found')
        if row['undone_at']:
            return {'restored': 0, 'kept': 0}
        restored = kept = 0
        for entry in json.loads(row['entries']):
            kind, _, key = entry['item'].partition(':')
            day = entry['day']
            if kind == 'tracker':
                current = conn.execute('SELECT status,value,taken_at,time_zone,dose_amount,dose_unit,logged_at FROM pill_doses WHERE pill_id=? AND day=?', (key, day)).fetchone()
                if current is None or (dict(current)!=entry['after'] if entry.get('after') else current['status']!=entry['value']):
                    kept += 1
                    continue
                if entry['previous']:
                    previous=entry['previous']
                    columns=('status','value','taken_at','time_zone','dose_amount','dose_unit','logged_at')
                    conn.execute('UPDATE pill_doses SET '+','.join(k+'=?' for k in columns)+' WHERE pill_id=? AND day=?',tuple(previous.get(k) for k in columns)+(key,day))
                else:
                    conn.execute('DELETE FROM pill_doses WHERE pill_id=? AND day=?', (key, day))
            elif kind == 'observation':
                if health_observations.value_on(day, key, conn) != entry['value']:
                    kept += 1
                    continue
                if entry['previous']:
                    conn.execute('UPDATE health_observations SET value=? WHERE day=? AND track_id=?',
                                 (entry['previous'], day, key))
                else:
                    conn.execute('DELETE FROM health_observations WHERE day=? AND track_id=?', (day, key))
            elif kind == 'goal':
                if not entry['previous']:
                    conn.execute('DELETE FROM coach_goal_days WHERE goal_id=? AND day=?', (key, day))
            elif kind == 'reading':
                conn.execute('DELETE FROM readings WHERE id=?', (entry['reading_id'],))
            restored += 1
        conn.execute('UPDATE log_batches SET undone_at=? WHERE id=?',
                     (dt.datetime.now(dt.timezone.utc).isoformat(), batch_id))
    return {'restored': restored, 'kept': kept}


class EntryErrors(ValueError):
    """Per-entry messages, keyed by the entry's position."""

    def __init__(self, errors):
        super().__init__('; '.join(errors.values()))
        self.errors = errors
