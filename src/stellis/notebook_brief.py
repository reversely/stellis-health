"""A notebook's Brief: day strip, medicine courses, coming up and levels.

Everything is projected from records: tracker courses linked to the notebook,
its care events, and accepted lab facts from its documents. Reference
intervals and lab flags come from each fact's quoted source line.
"""
from __future__ import annotations

import datetime as dt

from stellis import care_events, health_facts, notebook_sources, notebooks, readings, trackers
from stellis.health_facts import number as _number, reference, status

STRIP_BEFORE = 3
STRIP_AFTER = 11
LEVEL_KINDS = ('lab_result', 'vital_sign')
def _key(name, unit):
    return (' '.join(name.casefold().split()), unit.replace('µ', 'u').replace('μ', 'u').casefold().strip())


def levels(notebook_id: str) -> list[dict]:
    """Numeric lab and vital facts: this notebook's tests (has_own) and those only other notebooks hold."""
    names = {book['id']: book['name'] for book in notebooks.list_notebooks()}
    own_documents = {source['id'].partition(':')[2] for source in notebook_sources.list_sources(notebook_id)
                     if source['id'].startswith('document:')}
    series = {}
    for fact in health_facts.accepted():
        value = _number(fact['value'])
        if fact['kind'] not in LEVEL_KINDS or value is None:
            continue
        own = fact['document_id'] in own_documents
        point = {'date': fact['date'], 'value': value, 'own': own, 'title': fact['document_title'],
                 'page': fact['page'], 'target': fact['target'], 'notebook': names.get(fact['notebook_id']),
                 **reference(fact['quote'], fact['value'])}
        point['status'] = status(point)
        entry = series.setdefault(_key(fact['name'], fact['unit']),
                                  {'name': fact['name'], 'unit': fact['unit'], 'points': [], 'has_own': False})
        entry['points'].append(point)
        entry['has_own'] = entry['has_own'] or own
    # Readings logged into this notebook join as their own measures.
    for reading in readings.stored_points(notebook_id):
        measure = readings.MEASURES[reading['measure']]
        point = {'date': reading['time'][:10], 'value': reading['value'], 'value2': reading['value2'], 'own': True,
                 'title': reading['title'], 'page': None, 'target': reading['target'], 'notebook': names.get(notebook_id),
                 'low': None, 'high': None, 'flag': None, 'status': None}
        entry = series.setdefault(_key(measure.label, measure.unit),
                                  {'name': measure.label, 'unit': measure.unit, 'points': [], 'has_own': True})
        entry['points'].append(point)
    result = []
    for key, entry in series.items():
        # Tests found only in other notebooks come too, marked, for the All notebooks view.
        points = sorted(entry['points'], key=lambda point: point['date'])
        mine = [point for point in points if point['own']] or points
        lows = [point['low'] for point in points if point['low'] is not None]
        highs = [point['high'] for point in points if point['high'] is not None]
        # The most common interval stands for "most reports"; each point keeps its own.
        common_low = max(set(lows), key=lows.count) if lows else None
        common_high = max(set(highs), key=highs.count) if highs else None
        result.append({
            'key': '|'.join(key), 'name': entry['name'], 'unit': entry['unit'], 'points': points,
            'latest': mine[-1], 'previous': mine[-2] if len(mine) > 1 else None,
            'flagged': {'low': sum(p['flag'] == 'low' for p in points), 'high': sum(p['flag'] == 'high' for p in points),
                        'total': len(points)},
            'reference': {'low': common_low, 'high': common_high,
                          'shared': len(set(lows)) <= 1 and len(set(highs)) <= 1},
            'other_notebooks': sorted({p['notebook'] for p in points if not p['own'] and p['notebook']}),
            'has_own': entry['has_own'],
        })
    # Newest first; names A to Z within a date.
    return sorted(sorted(result, key=lambda level: level['name'].casefold()),
                  key=lambda level: level['latest']['date'], reverse=True)


def _course_detail(record, item):
    if not item['key'].startswith('fill-'):
        return 'Last planned dose'
    unit = record.get('supply_unit') or 'units'
    repeats = record.get('repeats') or 0
    allowed = 'No repeats' if not repeats else f"{repeats} repeat{'s' if repeats > 1 else ''} allowed"
    return (f"{record['units_per_fill']} {unit} per fill, {trackers.schedule_text(record).lower()} "
            f"from {dt.date.fromisoformat(record['start']):%B %-d}. {allowed}.")


def _source(record):
    if not record.get('source_document_id'):
        return None
    page = record['source_page']
    return {'title': record['source_title'] + (f', page {page}' if page else ''),
            'target': f"/document/{record['source_document_id']}" + (f'?page={page}' if page else '')}


def coming_up(notebook_id, today, courses, pregnancy=None) -> list[dict]:
    """Pending items only: calculated course dates, pregnancy dates and care events from today on.

    A booked birth ends the pending timeline there; the due date stays for reference.
    """
    items = []
    if pregnancy:
        for item in pregnancy['summary']['derived']:
            if item['date'] >= today.isoformat():
                items.append({'date': item['date'], 'precision': 'day', 'title': item['title'], 'detail': item['detail'],
                              'status': item['status'], 'source': _source(pregnancy)})
    for record in courses:
        source = _source(record)
        for item in record['summary']['derived']:
            if item['date'] >= today.isoformat():
                items.append({'date': item['date'], 'precision': 'day', 'title': item['title'],
                              'detail': _course_detail(record, item), 'status': item['status'], 'source': source})
    for event in care_events.list_all(notebook_id):
        when = dt.datetime.fromisoformat(event['starts_at']).date()
        if when >= today:
            planned = event['status'] == 'planned'
            detail = ' '.join(part for part in ['No date booked.' if planned else '', event['note']] if part)
            items.append({'date': when.isoformat(), 'precision': 'month' if planned else 'day',
                          'title': event['title'], 'detail': detail, 'status': event['status'],
                          'source': {'title': 'Appointment details', 'target': event['target']}})
    ends = (pregnancy or {}).get('booked_birth')
    if ends:
        items = [item for item in items if item['date'] <= ends or item['status'] == 'due']
    return sorted(items, key=lambda item: item['date'])


def strip(today, courses, pending=()) -> list[dict]:
    """Days around today with each course's logged or expected dose and each pending event."""
    days = []
    for offset in range(-STRIP_BEFORE, STRIP_AFTER + 1):
        day = today + dt.timedelta(days=offset)
        marks = []
        for record in courses:
            kind = trackers.KINDS[record['kind']]
            logged = record['outcomes'].get(day.isoformat(), {}).get('status')
            if logged:
                marks.append({'record_id': record['id'], 'state': logged})
            elif kind.expected(record, day):
                marks.append({'record_id': record['id'], 'state': 'expected' if day >= today else 'missed'})
        for item in pending:
            if item['date'] == day.isoformat() and item['precision'] == 'day':
                marks.append({'event': item['title'], 'state': 'event', 'status': item['status']})
        days.append({'date': day.isoformat(), 'today': offset == 0, 'marks': marks})
    return days


def brief(notebook_id: str, today: dt.date | None = None) -> dict:
    """The Brief for one notebook.

    Raises:
        LookupError: The notebook does not exist.
    """
    if not notebooks.exists(notebook_id):
        raise LookupError('Notebook not found')
    today = today or dt.date.today()
    records = trackers.list_records(notebook_id=notebook_id, today=today)
    courses = [record for record in records if trackers.KINDS[record['kind']].logs and record['kind'] != 'pill']
    # The latest pregnancy that still has something ahead.
    pregnancies = [record for record in records if record['kind'] == 'pregnancy'
                   and max(item['date'] for item in record['summary']['derived']) >= today.isoformat()]
    pregnancy = max(pregnancies, key=lambda record: record['summary']['pregnancy']['start'], default=None)
    treatments = [{'name': record['name'], 'start': record['start'], 'end': record.get('end') or None}
                  for record in courses]
    pending = coming_up(notebook_id, today, courses, pregnancy)
    return {'today': today.isoformat(), 'strip': strip(today, courses, pending), 'courses': courses,
            'pregnancy': pregnancy, 'coming_up': pending, 'levels': levels(notebook_id), 'treatments': treatments}
