"""Named calendar anchors with separate target and confirmed occurrence dates."""
from datetime import date
import uuid

from stellis import storage


def _table(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS health_milestones (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, target_date TEXT NOT NULL,
        date_kind TEXT NOT NULL, actual_date TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '')''')


def entries():
    with storage.connect() as connection:
        _table(connection)
        return [dict(row) for row in connection.execute('SELECT * FROM health_milestones ORDER BY target_date, id')]


def save(data, milestone_id=None):
    """Preserve the target when a person confirms the event occurred."""
    title = data['title'].strip()
    if not title or len(title) > 120:
        raise ValueError('Enter a milestone name under 121 characters')
    target = date.fromisoformat(data['target_date']).isoformat()
    date_kind = data['date_kind']
    if date_kind not in {'scheduled', 'estimated'}:
        raise ValueError('Choose a scheduled or estimated date')
    actual = data.get('actual_date', '')
    if actual:
        actual = date.fromisoformat(actual).isoformat()
        if actual > date.today().isoformat():
            raise ValueError('Confirmed date cannot be in the future')
    note = data.get('note', '').strip()
    if len(note) > 500:
        raise ValueError('Keep notes under 501 characters')
    with storage.connect() as connection:
        _table(connection)
        if milestone_id and not connection.execute('SELECT 1 FROM health_milestones WHERE id=?', (milestone_id,)).fetchone():
            raise ValueError('Milestone could not be found')
        milestone_id = milestone_id or str(uuid.uuid4())
        connection.execute('''INSERT INTO health_milestones VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET title=excluded.title, target_date=excluded.target_date,
            date_kind=excluded.date_kind, actual_date=excluded.actual_date, note=excluded.note''',
                           (milestone_id, title, target, date_kind, actual, note))
    return {'id': milestone_id, 'title': title, 'target_date': target, 'date_kind': date_kind, 'actual_date': actual, 'note': note}


def delete(milestone_id):
    with storage.connect() as connection:
        _table(connection)
        connection.execute('DELETE FROM health_milestones WHERE id=?', (milestone_id,))
    return {'deleted': True}
