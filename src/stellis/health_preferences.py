"""Explicit health-feature choices in the current account workspace."""
from stellis import storage


def _table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS health_preferences (
        id INTEGER PRIMARY KEY CHECK (id = 1), experiences_periods INTEGER)''')


def preferences():
    with storage.connect() as conn:
        _table(conn)
        row = conn.execute('SELECT experiences_periods FROM health_preferences WHERE id = 1').fetchone()
    value = row['experiences_periods'] if row else None
    return {'experiences_periods': None if value is None else bool(value)}


def save(experiences_periods):
    if experiences_periods is not None and type(experiences_periods) is not bool:
        raise ValueError('Choose whether you experience periods')
    with storage.connect() as conn:
        _table(conn)
        conn.execute('INSERT OR REPLACE INTO health_preferences VALUES (1, ?)', (experiences_periods,))
    return preferences()
