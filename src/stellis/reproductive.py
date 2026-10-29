"""Cycle and contraception records, kept as a compatibility layer over ``trackers``."""

from stellis import trackers

KINDS = {'period', 'pill', 'larc'}
date = trackers.date


def save(kind, data, record_id=None):
    """Validate and store a tracker entry."""
    if kind not in KINDS:
        raise ValueError('Choose a listed tracker')
    return trackers.save(kind, data, record_id)


def list_records():
    """Return saved entries and dose dates from the current workspace."""
    records = []
    for record in trackers.list_records(KINDS):
        outcomes = record.pop('outcomes')
        record.pop('summary')
        records.append({**record,
                        'doses': [day for day, item in outcomes.items() if item['status'] == 'taken'],
                        'skipped': [day for day, item in outcomes.items() if item['status'] == 'skipped']})
    return {'records': records}


def delete(record_id):
    return trackers.delete(record_id)


def dose(record_id, day, taken, status=None):
    if status not in {None, 'taken', 'skipped'}:
        raise ValueError('Choose a listed dose status')
    return trackers.log(record_id, day, status or ('taken' if taken else None))


def predicted_periods(today, count=3):
    """Projects the next periods from the averages of past cycles.

    The cycle length is the mean gap between consecutive period starts (gaps marked as a
    tracking break are left out); the period length is the mean of recorded starts to ends.
    Two or more periods are needed. Returns up to ``count`` future ``{start, end, cycle_days,
    period_days, cycles}`` estimates; none are invented without history.
    """
    import datetime as dt
    periods = sorted((r for r in list_records()['records'] if r['kind'] == 'period' and r.get('start')),
                     key=lambda r: r['start'])
    if len(periods) < 2:
        return []
    starts = [dt.date.fromisoformat(r['start']) for r in periods]
    gaps = [(b - a).days for a, b, later in zip(starts, starts[1:], periods[1:])
            if not later.get('tracking_gap_before') and (b - a).days > 0]
    if not gaps:
        return []
    cycle = round(sum(gaps) / len(gaps))
    lengths = [(dt.date.fromisoformat(r['end']) - dt.date.fromisoformat(r['start'])).days + 1
               for r in periods if r.get('end')]
    length = max(1, round(sum(lengths) / len(lengths))) if lengths else 5
    today = dt.date.fromisoformat(today) if isinstance(today, str) else today
    result, start = [], starts[-1]
    while len(result) < count:
        start += dt.timedelta(days=cycle)
        if start <= today:
            continue
        result.append({'start': start.isoformat(), 'end': (start + dt.timedelta(days=length - 1)).isoformat(),
                       'cycle_days': cycle, 'period_days': length, 'cycles': len(gaps)})
    return result
