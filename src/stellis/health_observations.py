"""Daily health observations, independent of interval-based history."""
from datetime import date
from stellis import storage


def _table(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS health_observations (
        day TEXT NOT NULL, track_id TEXT NOT NULL, value TEXT NOT NULL,
        PRIMARY KEY(day, track_id))''')


def entries():
    with storage.connect() as connection:
        _table(connection)
        return [dict(row) for row in connection.execute('SELECT * FROM health_observations ORDER BY day')]


FLOW = ('Had flow', 'No bleeding', 'Spotting', 'Light', 'Moderate', 'Heavy')


def value_on(day, track_id, connection):
    row = connection.execute('SELECT value FROM health_observations WHERE day=? AND track_id=?',
                             (day, track_id)).fetchone()
    return row['value'] if row else None


def save(day, track_id, value, connection=None):
    """Saves or clears one day's value; ``connection`` lets joined logging share a transaction."""
    if date.fromisoformat(day) > date.today():
        raise ValueError('Choose today or an earlier day')
    if track_id != 'periods' or value not in {None, *FLOW}:
        raise ValueError('Choose a listed flow status')
    if connection is None:
        with storage.connect() as own:
            return save(day, track_id, value, own)
    _table(connection)
    if value is None:
        connection.execute('DELETE FROM health_observations WHERE day=? AND track_id=?', (day, track_id))
    else:
        connection.execute('''INSERT INTO health_observations VALUES (?, ?, ?)
            ON CONFLICT(day, track_id) DO UPDATE SET value=excluded.value''', (day, track_id, value))
    return {'saved': True}
