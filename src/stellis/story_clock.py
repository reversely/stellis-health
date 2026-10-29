"""Demo story clock (#252): a demo workspace can run on a later date than the real one.

Temporary override. Maya's example years run past the real date, so a demo workspace stores a
story date and Stellis reads "today" from it. ``install()`` makes every ``datetime`` import inside
the ``stellis`` package resolve to a copy whose ``date.today()`` and ``datetime.now()`` add the
active workspace's offset in whole days. Other libraries keep the real module, and
``time.time()`` stays real, so sessions, rate limits and expiries are unaffected. Outside a demo
workspace the offset is zero. Remove the ``install()`` call in ``stellis/__init__.py`` to turn the
override off.
"""
from __future__ import annotations

import builtins
import datetime as _real
import json
import types
from contextvars import ContextVar
from pathlib import Path

FILE = 'story-date.json'
# The example corpus, after its five-year shift: setup in 2026, records 2027 to 2031, each year
# added the morning after its last record.
FIRST = _real.date(2026, 1, 1)
LAST = _real.date(2032, 1, 1)
# The short tour adds one follow-up record (March 2032) after the five years, so the clock may run to here.
FOLLOWUPS = _real.date(2032, 4, 1)

offset: ContextVar[_real.timedelta] = ContextVar('stellis_story_offset', default=_real.timedelta(0))


class date(_real.date):
    @classmethod
    def today(cls):
        day = _real.date.today() + offset.get()
        return cls(day.year, day.month, day.day)


class datetime(_real.datetime):
    @classmethod
    def now(cls, tz=None):
        return super().now(tz) + offset.get()

    @classmethod
    def today(cls):
        return cls.now()

    @classmethod
    def utcnow(cls):
        return super().utcnow() + offset.get()


_module = types.ModuleType('datetime', _real.__doc__)
_module.__dict__.update({name: value for name, value in vars(_real).items() if not name.startswith('__')})
_module.date, _module.datetime = date, datetime


def install() -> None:
    """Gives modules in the ``stellis`` package the story-clock ``datetime`` module."""
    if getattr(builtins.__import__, 'story_clock', False):
        return
    original = builtins.__import__

    def story_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'datetime' and level == 0 and globals and str(globals.get('__name__', '')).startswith('stellis'):
            return _module
        return original(name, globals, locals, fromlist, level)

    story_import.story_clock = True
    builtins.__import__ = story_import


def _read(folder: Path) -> dict[str, _real.date]:
    try:
        saved = json.loads((folder / FILE).read_text())
        return {key: _real.date.fromisoformat(saved[key]) for key in ('date', 'reached') if saved.get(key)}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def stored(folder: Path) -> _real.date | None:
    """The story date saved in a demo workspace, or None when it runs on the real date."""
    return _read(folder).get('date')


def reached(folder: Path) -> _real.date | None:
    """The latest story date the workspace's records were added at."""
    saved = _read(folder)
    return saved.get('reached') or saved.get('date')


def looking_back(folder: Path) -> _real.date | None:
    """The story date when the scrub has moved it before the latest records, else None.

    Pages then show the workspace as of that day and changes are refused until it moves forward.
    """
    story, latest = stored(folder), reached(folder)
    return story if story and latest and story < latest else None


def days(story: _real.date | None) -> int:
    return (story - _real.date.today()).days if story else 0


def save(folder: Path, story: _real.date, *, advance: bool = False) -> int:
    """Saves a workspace's story date and returns its offset in days.

    Args:
        folder: The demo workspace.
        story: A date inside the corpus's range, or up to its follow-ups.
        advance: Records are being added at this date, so it also becomes the latest reached.
    """
    if not FIRST <= story <= FOLLOWUPS:
        raise ValueError(f'Choose a date from {FIRST.year} to {FOLLOWUPS.year}')
    latest = reached(folder)
    if advance and (latest is None or story > latest):
        latest = story
    temporary = folder / (FILE + '.tmp')
    temporary.write_text(json.dumps({'date': story.isoformat(), 'reached': (latest or story).isoformat()}) + '\n')
    temporary.replace(folder / FILE)
    offset.set(_real.timedelta(days=days(story)))
    return days(story)


# A row's own date (a report's record date, a result's date, a reading's time) places it in the
# story; rows without one are placed by when they were added.
_OWN_DATE = ('record_date', 'fact_date', 'taken_at')
# Setup rather than records: Stellis's agents exist on every date.
_ALWAYS = frozenset({'agents'})


def shadow_later_rows(conn, day: _real.date) -> None:
    """Hides rows dated after ``day`` from every unqualified query on this connection.

    A TEMP view named like a table shadows it, so the app's queries read the workspace as it stood
    on that day. Views cannot be written to; the demo middleware refuses changes meanwhile.
    """
    limit = day.isoformat()
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM main.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "AND sql NOT LIKE 'CREATE VIRTUAL%'")]
    for table in tables:
        columns = {row[1] for row in conn.execute(f'PRAGMA main.table_info("{table}")')}
        dated = [f'"{column}"' for column in _OWN_DATE if column in columns]
        if 'created_at' in columns:
            dated.append('"created_at"')
        if dated and table not in _ALWAYS:
            when = f'COALESCE({", ".join(dated)})' if len(dated) > 1 else dated[0]
            conn.execute(f'CREATE TEMP VIEW IF NOT EXISTS "{table}" AS SELECT * FROM main."{table}" '
                         f'WHERE {when} IS NULL OR substr({when}, 1, 10) <= \'{limit}\'')
