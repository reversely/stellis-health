"""Account-local choices for the health dashboard."""
from __future__ import annotations

import json

from stellis import notebooks, readings, storage

DEFAULT_MEASURES = ['weight', 'blood_pressure', 'sleep']


def _table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS dashboard_preferences
        (id INTEGER PRIMARY KEY CHECK (id = 1), choices TEXT NOT NULL)''')


def preferences() -> dict:
    """Returns saved choices, dropping archived or deleted notebooks."""
    books = notebooks.list_notebooks()
    available = {book['id'] for book in books}
    with storage.connect() as conn:
        _table(conn)
        row = conn.execute('SELECT choices FROM dashboard_preferences WHERE id = 1').fetchone()
    choices = json.loads(row['choices']) if row else {
        'notebooks': [book['id'] for book in books[:3]],
        'measurements': list(DEFAULT_MEASURES),
        'care_contexts': [],
    }
    return {**choices, 'notebooks': [key for key in choices['notebooks'] if key in available],
            'care_contexts': [key for key in choices['care_contexts'] if key in available]}


def save(choices: dict) -> dict:
    """Validates complete dashboard choices before saving them atomically."""
    available = {book['id'] for book in notebooks.list_notebooks()}
    for field, allowed in [('notebooks', available), ('care_contexts', available),
                           ('measurements', {m['key'] for m in readings.series()})]:
        values = choices[field]
        if len(values) > 3 or len(set(values)) != len(values) or set(values) - allowed:
            raise ValueError('Choose up to three available items for each section')
    with storage.connect() as conn:
        _table(conn)
        conn.execute('INSERT OR REPLACE INTO dashboard_preferences(id, choices) VALUES (1, ?)',
                     (json.dumps(choices),))
    return preferences()
