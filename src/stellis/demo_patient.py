"""Isolated, disposable Maya workspaces and verified yearly synthetic packages."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import threading
import uuid
import zipfile
import weakref
from datetime import date, timedelta

from stellis import documents, notebooks, storage, story_clock

COOKIE = 'stellis-demo'
# Not HttpOnly: the browser's story clock reads it before any other script runs (#252).
STORY_COOKIE = 'stellis-story-days'
MAX_ZIP_BYTES = 10_000_000
_LOCK = threading.RLock()
_REQUEST_LOCKS = weakref.WeakKeyDictionary()
_CLEARING = set()
# The demo's content (Maya's records, recording, year bundles and guide) is data, kept apart from the
# product: STELLIS_DEMO_DIR names the folder the deployment supplies; development falls back to the copy here.
STATIC = Path(os.environ.get('STELLIS_DEMO_DIR') or Path(__file__).parent / 'static' / 'maya')


def available() -> bool:
    """Whether the demo's content is installed; without it the demo is off."""
    return (STATIC / 'guide.json').is_file()


def _control() -> Path:
    return storage.base_dir() / 'maya-demo.json'


def _write(path: Path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temporary, path)


def workspace() -> Path:
    """Resolves a generation whose database and files never share the real account root."""
    with _LOCK:
        path = _control()
        if not path.exists():
            _write(path, {'generation': uuid.uuid4().hex})
        generation = json.loads(path.read_text())['generation']
        if not isinstance(generation, str) or not re.fullmatch('[a-f0-9]{32}', generation):
            raise ValueError('Reset the demo workspace')
        return storage.base_dir() / 'demos' / generation


def clearing():
    return str(storage.base_dir()) in _CLEARING


def begin_clear():
    account = str(storage.base_dir())
    if account in _CLEARING:
        return False
    _CLEARING.add(account)
    return True


def finish_clear():
    _CLEARING.discard(str(storage.base_dir()))


def request_lock():
    """Coordinate demo responses and resets within the account's server loop."""
    locks = _REQUEST_LOCKS.setdefault(asyncio.get_running_loop(), {})
    return locks.setdefault(str(storage.base_dir()), asyncio.Lock())


def existing_workspace():
    """Resolve an existing generation without resurrecting a cleared demo."""
    with _LOCK:
        return workspace() if _control().exists() else None


def _state():
    path = storage.data_dir() / 'demo-history.json'
    return json.loads(path.read_text()) if path.exists() else {'years': {}}


def _save(state):
    _write(storage.data_dir() / 'demo-history.json', state)


def guide():
    return json.loads((STATIC / 'guide.json').read_text())


def years() -> list[str]:
    """The example corpus's years in order: 2027 to 2031 since the five-year shift (#252)."""
    return sorted({step['year'] for step in guide()['steps']})


def status():
    if not _control().exists():
        return {'years': {}, 'documents': 0, 'notebooks': []}
    with storage.demo_workspace(workspace()):
        return {**_state(), 'generation': json.loads(_control().read_text())['generation'], 'documents': len(documents.list_all()), 'notebooks': notebooks.list_notebooks()}


MAX_BIOMETRICS_BYTES = 500_000
MAX_READINGS = 1500
MAX_PERIODS = 40
BIOMETRICS_FORMAT = 'stellis-maya-biometrics-v1'


def _chapter(year: str) -> dict:
    return next(item for item in guide()['chapters'] if item['year'] == year)


def _check_biometrics(year: str, raw: bytes) -> dict:
    """Checks a year's biometrics file against its published hash and shape before anything is saved."""
    if len(raw) > MAX_BIOMETRICS_BYTES or hashlib.sha256(raw).hexdigest() != _chapter(year).get('biometrics_sha256'):
        raise ValueError('The biometrics in this demo ZIP do not match the synthetic corpus')
    data = json.loads(raw)
    readings_, periods = data.get('readings'), data.get('periods')
    if (data.get('format') != BIOMETRICS_FORMAT or str(data.get('year')) != year or not isinstance(readings_, list)
            or not isinstance(periods, list) or len(readings_) > MAX_READINGS or len(periods) > MAX_PERIODS):
        raise ValueError('The biometrics in this demo ZIP are not readable')
    for item in readings_:
        if not isinstance(item, dict) or not str(item.get('taken_at', '')).startswith(year):
            raise ValueError('A biometric reading falls outside its year')
    return data


def _save_biometrics(data: dict) -> dict:
    """Saves a year's readings and period logs in one transaction; the app's own checks apply."""
    from datetime import datetime, timezone
    from stellis import readings, trackers
    # A reading later than the demo date waits; adding a year moves that date past the year's last record.
    now = datetime.now(timezone.utc)
    due = [item for item in data['readings'] if datetime.fromisoformat(str(item['taken_at'])) <= now]
    periods = [item for item in data['periods'] if date.fromisoformat(str(item['start'])) <= now.date()]
    with storage.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for item in due:
            readings.add(str(item['measure']), float(item['value']),
                         float(item['value2']) if item.get('value2') is not None else None,
                         str(item['taken_at']), 'self', None, str(item.get('note') or ''), conn=conn)
        for item in periods:
            trackers.save('period', {'start': item['start'], 'end': item.get('end'), 'flow': item['flow'],
                                     'note': item.get('note') or ''}, conn=conn)
    return {'readings': len(due), 'periods': len(periods), 'later': len(data['readings']) - len(due)}


def story() -> dict:
    """The workspace's story date, or the real date when none is set, with the scrub's range."""
    saved = story_clock.stored(storage.data_dir())
    latest = story_clock.reached(storage.data_dir())
    return {'date': (saved or date.today()).isoformat(), 'set': saved is not None,
            'days': story_clock.days(saved), 'first': story_clock.FIRST.isoformat(), 'last': story_clock.LAST.isoformat(),
            'latest': latest.isoformat() if latest else None,
            'looking_back': story_clock.looking_back(storage.data_dir()) is not None}


def move_story(value: str) -> dict:
    try:
        chosen = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('Choose a date such as 2028-06-01') from exc
    story_clock.save(storage.data_dir(), chosen)
    return story()


def _advance_story(expected: list[dict], biometrics: dict) -> None:
    """Moves the story date to the morning after a newly added year's last record, so all of it shows."""
    days = [str(item['date']) for item in expected]
    days += [str(item['taken_at'])[:10] for item in biometrics['readings']]
    days += [str(item.get('end') or item['start']) for item in biometrics['periods']]
    last = max(date.fromisoformat(day) for day in days) + timedelta(days=1)
    current = story_clock.stored(storage.data_dir())
    if current is None or last > current:
        story_clock.save(storage.data_dir(), last, advance=True)


def import_year(content: bytes):
    """Validates every member before writing the known synthetic PDFs and biometrics."""
    if len(content) > MAX_ZIP_BYTES:
        raise ValueError('Choose a demo ZIP under 10 MB')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            if len(names) != len(set(names)) or len(members) > 30 or any(
                    item.file_size > 5_000_000 or item.flag_bits & 1 for item in members
            ) or sum(item.file_size for item in members) > 10_000_000:
                raise ValueError('This demo ZIP has unsupported contents')
            package = json.loads(archive.read('demo-package.json'))
            year = str(package['year'])
            expected = [item for item in guide()['steps'] if item['year'] == year]
            if package.get('format') != 'stellis-maya-year-v2' or year not in years():
                raise ValueError('Choose a Maya yearly demo ZIP')
            if not expected or set(names) != {'demo-package.json', 'biometrics.json', *(item['filename'] for item in expected)}:
                raise ValueError('The demo ZIP must contain exactly this year’s records and biometrics')
            validated = []
            for item in expected:
                data = archive.read(item['filename'])
                if hashlib.sha256(data).hexdigest() != item['sha256']:
                    raise ValueError('A demo PDF does not match the synthetic corpus')
                validated.append((item, data))
            biometrics = _check_biometrics(year, archive.read('biometrics.json'))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeError, RuntimeError, StopIteration) as exc:
        raise ValueError('Choose a valid Maya yearly demo ZIP') from exc
    state = _state()
    if year in state['years']:
        return review(year)
    next_year = next((y for y in years() if y not in state['years']), None)
    if year != next_year:
        raise ValueError(f'Upload the {next_year} demo ZIP next')
    if any(not value.get('filed') for value in state['years'].values()):
        raise ValueError('Finish this year before uploading the next year')
    _advance_story(expected, biometrics)
    added = []
    for item, data in validated:
        record, _ = documents.create(item['filename'], data)
        documents.update(record['id'], title=item['title'])
        added.append({'document_id': item['document_id'], 'id': record['id']})
    counts = _save_biometrics(biometrics)
    state['years'][year] = {'records': added, 'biometrics': counts, 'dates_confirmed': False, 'filed': False}
    _save(state)
    return review(year)


def review(year: str):
    state = _state()
    if year not in state['years']:
        raise ValueError('Upload this year’s demo ZIP first')
    known = {item['document_id']: item for item in guide()['steps']}
    records = [{**documents.get(entry['id']), 'corpus_id': known[entry['document_id']]['document_id']}
               for entry in state['years'][year]['records']]
    return {**state['years'][year], 'year': year, 'documents': records}


def confirm_dates(year: str, values: dict):
    data = review(year)
    known = {item['document_id']: item for item in guide()['steps']}
    for record in data['documents']:
        value = values.get(record['id'], '')
        try:
            date.fromisoformat(value)
        except (ValueError, TypeError) as exc:
            raise ValueError('Confirm a visit or test date for every report') from exc
        if value != known[record['corpus_id']]['date']:
            raise ValueError(f"Check the visit or test date in {record['corpus_id']}")
    for record in data['documents']:
        documents.update(record['id'], record_date=values[record['id']])
        from stellis import health_facts
        health_facts.prepare(record['id'])
    state = _state(); state['years'][year]['dates_confirmed'] = True; _save(state)
    return review(year)


def _library() -> dict[str, dict]:
    """Each notebook with its family and how many records it holds, for before-and-after rounds."""
    from stellis import collections
    families = {c['id']: c['name'] for c in collections.list_collections()}
    from stellis import notebook_sources
    return {str(b['id']): {'name': b['name'], 'family': families.get(b.get('collection_id')),
                           'records': len(notebook_sources.list_sources(str(b['id'])))} for b in notebooks.list_notebooks()}


def begin_round(year: str):
    """Snapshots the library before Stellis sorts a year's records."""
    data = review(year)
    if not data['dates_confirmed']:
        raise ValueError('Confirm report dates before Stellis sorts the records')
    state = _state(); state['years'][year]['before'] = _library(); _save(state)
    return review(year)


# Each statement moves a row's added time to the day its source is dated, as if Maya added every
# record when it arrived. Values are only ever moved earlier, to a date that is already saved.
_NOON = "'T12:00:00+00:00'"
_SOURCE_DATE = "(SELECT d.record_date || " + _NOON + " FROM documents d WHERE d.id = {column} AND d.record_date IS NOT NULL)"
_DATE_BY_SOURCES = [
    "UPDATE documents SET created_at = record_date || " + _NOON + " WHERE record_date IS NOT NULL AND record_date || " + _NOON + " < created_at",
    "UPDATE readings SET created_at = taken_at WHERE taken_at < created_at",
    "UPDATE reproductive_records SET created_at = json_extract(data, '$.start') || " + _NOON
    + " WHERE kind = 'period' AND json_extract(data, '$.start') || " + _NOON + " < created_at",
    *[f"UPDATE {table} SET created_at = MIN(created_at, COALESCE({_SOURCE_DATE.format(column=f'{table}.{column}')}, created_at))"
      for table, column in [('reproductive_records', 'source_document_id'), ('care_events', 'source_document_id'),
                            ('report_measurement_runs', 'document_id'), ('health_facts', 'document_id')]],
    "UPDATE routine_reviews SET created_at = MIN(created_at, COALESCE(" + _SOURCE_DATE.format(column='routine_reviews.source_id')
    + ", created_at)) WHERE source_kind = 'document'",
    "UPDATE notebooks SET created_at = MIN(created_at, COALESCE((SELECT MIN(d.record_date) || " + _NOON + " FROM documents d"
    " WHERE d.record_date IS NOT NULL AND (d.notebook_id = notebooks.id OR EXISTS (SELECT 1 FROM notebook_record_links l"
    " WHERE l.notebook_id = notebooks.id AND l.source_id = 'document:' || d.id))), created_at))",
    "UPDATE collections SET created_at = MIN(created_at, COALESCE((SELECT MIN(n.created_at) FROM notebooks n"
    " WHERE n.collection_id = collections.id), created_at))",
]


def date_by_sources() -> None:
    """Dates each example record, result, care change and notebook by when its source happened (#252).

    The demo date scrub then shows a year's records on their own days, not all at once when the
    year was added.
    """
    import sqlite3
    with storage.connect() as conn:
        for statement in _DATE_BY_SOURCES:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as error:
                # A workspace that has not used a feature yet has no table for it.
                if 'no such table' not in str(error):
                    raise


def finish_round(year: str, reasons: dict[str, str] | None = None):
    """Closes a year: records what Stellis created or grew, and why, in the organization history."""
    data = review(year)
    state = _state()
    before, after = state['years'][year].get('before') or {}, _library()
    created = [{**book, 'id': key, 'reason': (reasons or {}).get(book['name'], '')}
               for key, book in after.items() if key not in before]
    grown = [{**book, 'id': key, 'added': book['records'] - before[key]['records'], 'reason': (reasons or {}).get(book['name'], '')}
             for key, book in after.items() if key in before and book['records'] > before[key]['records']]
    from stellis import sorting
    year_ids = {entry['id'] for entry in state['years'][year]['records']}
    loose = [item for item in sorting.unsorted() if item.kind == 'document' and item.id in year_ids]
    date_by_sources()
    state['years'][year].update(filed=True, before=None)
    state.setdefault('rounds', []).append({
        'year': year, 'records': len(data['documents']), 'biometrics': data.get('biometrics') or {},
        'created': created, 'grown': grown, 'unsorted': len(loose),
        'notebooks': len(after), 'families': len({b['family'] for b in after.values() if b['family']})})
    _save(state)
    return state['rounds'][-1]


def care_status(year: str) -> dict:
    """Where the year's care step stands: not_started, running or done, with each record's changes."""
    review(year)
    return _state()['years'][year].get('care') or {'state': 'not_started', 'done': 0, 'total': 0, 'records': []}


def _number(value):
    """150.0 reads as 150; other values are shown as written."""
    return f'{value:g}' if isinstance(value, float) else str(value)


# Each record's care review is tried this many times, waiting longer after each failure: Token
# Factory can be briefly unavailable, and a review that misquotes its note often reads correctly again.
CARE_ATTEMPTS = 3
CARE_RETRY_WAIT = 2.0


def _care_summary(proposals: list[dict]) -> list[dict]:
    return [{'action': p['action'], 'kind': p['kind'],
             'name': p['data'].get('name') or p['data'].get('title') or p['data'].get('phase') or p['kind'].capitalize(),
             'dose': ' '.join(_number(p['data'][k]) for k in ('dose_amount', 'dose_unit') if p['data'].get(k) not in (None, '')),
             'effective_date': p.get('effective_date'), 'end': p['data'].get('end') or '', 'quote': p['quote']}
            for p in proposals if p['action'] != 'keep']


async def apply_year_care(year: str) -> dict:
    """Reads each of the year's records for care instructions, in date order, and applies them.

    Runs after the person chooses Add care changes. Order matters: a dose change in one note
    needs the course an earlier note started. Each record keeps its own review, with Undo on
    its page. A record whose review still fails after CARE_ATTEMPTS tries is listed with the reason;
    running the step again reads only those records.
    """
    from stellis import agent_gateway, routine_review
    data = review(year)
    if not _state()['years'][year].get('filed'):
        raise ValueError('File this year’s records first')
    records = sorted(data['documents'], key=lambda r: (r.get('record_date') or '', r['corpus_id']))

    def save(update):
        with _LOCK:
            state = _state(); state['years'][year]['care'] = {**(state['years'][year].get('care') or {}), **update}; _save(state)

    read = {r['id']: r for r in (_state()['years'][year].get('care') or {}).get('records', []) if not r.get('error')}
    save({'state': 'running', 'done': 0, 'total': len(records), 'records': []})
    done = []
    for index, record in enumerate(records, 1):
        entry = read.get(record['id'])
        if entry is None:
            entry = {'id': record['id'], 'title': record['title'], 'date': record.get('record_date'), 'changes': [], 'error': None}
            for attempt in range(1, CARE_ATTEMPTS + 1):
                try:
                    current = await routine_review.prepare('document', record['id'])
                    while current['state'] == 'preparing':
                        await asyncio.sleep(1)
                        current = routine_review.get('document', record['id'])
                    chosen = [p for p in current.get('proposals', [])]
                    if current['state'] == 'pending' and chosen:
                        routine_review.apply(current['id'], [p['id'] for p in chosen])
                    entry['changes'] = _care_summary(chosen) if current['state'] in ('pending', 'applied') else []
                    entry['error'] = None
                    break
                except (ValueError, LookupError, agent_gateway.GatewayError) as error:
                    entry['error'] = str(error)
                    if attempt < CARE_ATTEMPTS:
                        await asyncio.sleep(CARE_RETRY_WAIT * attempt)
        done.append(entry)
        save({'done': index, 'records': done})
    date_by_sources()
    save({'state': 'done'})
    return care_status(year)


def start_year_care(year: str) -> dict:
    """Starts the year's care step in the background, in this workspace; the dialog polls care_status."""
    import contextvars
    status = care_status(year)
    if not _state()['years'][year].get('filed'):
        raise ValueError('File this year’s records first')
    # A finished year runs again only to read the records that failed.
    if status['state'] == 'running' or (status['state'] == 'done' and not any(r.get('error') for r in status['records'])):
        return status
    # Saved before the job starts, so a status read straight after this request sees it running.
    with _LOCK:
        state = _state()
        state['years'][year]['care'] = status = {**status, 'state': 'running', 'done': 0, 'total': len(state['years'][year]['records'])}
        _save(state)
    context = contextvars.copy_context()
    threading.Thread(target=lambda: context.run(asyncio.run, apply_year_care(year)), daemon=True,
                     name=f'example-care-{year}').start()
    return status


def history():
    return _state().get('rounds', [])


# One click adds a year's pack (owner, 2026-10-28): the pipeline runs on its own and the dialog shows
# each stage as it finishes. The records are fictional, so nothing asks the visitor to confirm a date,
# a notebook or a figure.
PACK_STAGES = ('read', 'date', 'file', 'extract', 'done')


def _pack(year: str, **update) -> dict:
    with _LOCK:
        state = _state()
        pack = state.setdefault('packs', {}).setdefault(year, {'stage': 'read', 'counts': {}, 'error': None})
        if update:
            counts = {**pack['counts'], **update.pop('counts', {})}
            pack.update(update, counts=counts)
            _save(state)
        return dict(pack)


def pack_status(year: str) -> dict:
    """Where the year's one-click pack stands: its stage, what each finished stage found, and next steps."""
    if year not in years():
        raise ValueError('There is no example pack for that year')
    pack = (_state().get('packs') or {}).get(year)
    if pack is None:
        filed = _state()['years'].get(year, {}).get('filed')
        pack = {'stage': 'done' if filed else 'waiting', 'counts': {}, 'error': None}
    return {**pack, 'year': year, 'next': next_steps(year) if pack['stage'] == 'done' else None}


async def add_year(year: str) -> dict:
    """Reads, dates, files and extracts one year's records, resuming after a stage that failed."""
    from stellis import health_facts, sorting
    path = STATIC / 'years' / f'maya-{year}.zip'
    try:
        _pack(year, stage='read', error=None)
        if year not in _state()['years']:
            await asyncio.to_thread(import_year, path.read_bytes())
        data = review(year)
        _pack(year, stage='date', counts={'records': len(data['documents']),
                                          'readings': (data.get('biometrics') or {}).get('readings', 0)})
        if not data['dates_confirmed']:
            guided = {item['document_id']: item['date'] for item in guide()['steps']}
            await asyncio.to_thread(confirm_dates, year, {r['id']: guided[r['corpus_id']] for r in data['documents']})
        dated = sorted(r['record_date'] for r in review(year)['documents'] if r.get('record_date'))
        _pack(year, stage='file', counts={'first': dated[0] if dated else None, 'last': dated[-1] if dated else None})
        if not _state()['years'][year].get('filed'):
            await asyncio.to_thread(begin_round, year)
            reasons = {}
            # A reply that files nothing is tried again; after three, the stage stops with Try again
            # rather than closing the year with every record left unsorted.
            proposal = {'groups': []}
            for _ in range(3):
                try:
                    proposal = await sorting.suggest()
                except sorting.SortingError:
                    continue
                if proposal['groups']:
                    break
            if not proposal['groups']:
                raise ValueError('Stellis couldn’t sort these records. Try again.')
            for group in proposal['groups']:
                sorting.apply(group['notebook_name'], group['items'], group['notebook_id'], group.get('collection_id'))
                reasons[group['notebook_name']] = group.get('reason', '')
            await asyncio.to_thread(finish_round, year, reasons)
        round_ = next((r for r in reversed(history()) if r['year'] == year), {})
        _pack(year, stage='extract', counts={'created': len(round_.get('created', [])), 'grown': len(round_.get('grown', []))})
        await apply_year_care(year)
        ids = {r['id'] for r in review(year)['documents']}
        for record_id in ids:
            health_facts.accept_all(record_id)
        care = care_status(year)
        _pack(year, stage='done', counts={
            'results': sum(1 for fact in health_facts.accepted() if fact['document_id'] in ids),
            'care': sum(len(r['changes']) for r in care['records'])})
    except Exception as error:  # The dialog shows the stage that stopped, and Try again resumes there.
        _pack(year, error=str(error) or 'This step stopped')
    return pack_status(year)


def start_add_year(year: str) -> dict:
    """Starts the year's pack in the background, in this workspace; the dialog polls pack_status."""
    import contextvars
    status = pack_status(year)
    if status['stage'] == 'done' or status.get('running'):
        return status
    _pack(year, error=None, running=True)
    context = contextvars.copy_context()

    def run():
        try:
            asyncio.run(add_year(year))
        finally:
            _pack(year, running=False)
    threading.Thread(target=lambda: context.run(run), daemon=True, name=f'example-pack-{year}').start()
    return pack_status(year)


def next_steps(year: str) -> dict:
    """Where to go once a year is in: a report as Stellis read it, its notebooks, questions and views."""
    from stellis import health_facts
    from urllib.parse import quote
    data = review(year)
    ids = {r['id']: r for r in data['documents']}
    # The report with the most results shows the pipeline best: each value tied to its line.
    results = {}
    for fact in health_facts.accepted():
        if fact['document_id'] in ids:
            results[fact['document_id']] = results.get(fact['document_id'], 0) + 1
    report = ids[max(results, key=results.get)] if results else None
    round_ = next((r for r in reversed(history()) if r['year'] == year), {})
    books = [{'name': b['name'], 'href': '/notebook/' + quote(b['id'])} for b in round_.get('created', []) + round_.get('grown', [])][:3]
    asked, questions = set(), []
    linked = {r['id']: r.get('notebook_ids') or [] for r in documents.list_all()}
    by_corpus = {r['corpus_id']: {**r, 'notebook_ids': linked.get(r['id'], [])} for r in data['documents']}
    for step in guide()['steps']:
        record = by_corpus.get(step['document_id'])
        question = (step.get('question') or '').strip()
        # Short questions read as suggestions; the long benchmark prompts stay out of the dialog.
        if record and question and len(question) <= 90 and question not in asked and record.get('notebook_ids'):
            asked.add(question)
            questions.append({'text': question, 'href': f"/notebook/{quote(record['notebook_ids'][0])}?ask={quote(question)}"})
    # A year whose own questions are all long still offers one, built from the notebook it filled.
    if not questions:
        for book in (round_.get('created', []) + round_.get('grown', []))[:2]:
            text = f"Summarize my {book['name']} records from {year}"
            questions.append({'text': text, 'href': f"/notebook/{quote(book['id'])}?ask={quote(text)}"})
    return {'report': {'title': report['title'], 'href': '/document/' + quote(report['id'])} if report else None,
            'notebooks': books, 'questions': questions[:3]}


async def populate():
    """Adds every remaining year the way a person would: upload, dates, Stellis's sorting, approval.

    Stellis proposes the notebooks with its real sorting, and every proposal is applied as if
    approved. Runs inside the example workspace.
    """
    from stellis import health_facts, sorting
    for path in sorted((STATIC / 'years').glob('maya-*.zip')):
        year = path.stem.split('-')[1]
        if _state()['years'].get(year, {}).get('filed'):
            continue
        data = await asyncio.to_thread(import_year, path.read_bytes())
        if not data['dates_confirmed']:
            dates = {item['document_id']: item['date'] for item in guide()['steps']}
            await asyncio.to_thread(confirm_dates, year, {record['id']: dates[record['corpus_id']] for record in data['documents']})
        await asyncio.to_thread(begin_round, year)
        reasons = {}
        try:
            proposal = await sorting.suggest()
        except sorting.SortingError:
            proposal = {'groups': []}
        for group in proposal['groups']:
            sorting.apply(group['notebook_name'], group['items'], group['notebook_id'], group.get('collection_id'))
            reasons[group['notebook_name']] = group.get('reason', '')
        await asyncio.to_thread(finish_round, year, reasons)
        await apply_year_care(year)
        for record in review(year)['documents']:
            health_facts.accept_all(record['id'])
    return status()


SNAPSHOT_MAX_BYTES = 60_000_000


def seed():
    """Fills a fresh example workspace from the prebuilt snapshot in one step (#247).

    The snapshot is Maya's five years as the real pipeline filed them:
    imported, sorted into notebooks, results accepted and care applied. Seeding asks the person to
    confirm nothing. Any earlier generation is replaced.

    Raises:
        ValueError: The snapshot does not match its manifest, or holds a path outside the workspace.
    """
    manifest = json.loads((STATIC / 'snapshot.json').read_text())
    raw = (STATIC / 'snapshot.zip').read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['sha256']:
        raise ValueError('The demo snapshot does not match its manifest')
    with _LOCK:
        if _control().exists():
            clear()
        target = workspace()
        root = target.resolve()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            if sum(info.file_size for info in entries) > SNAPSHOT_MAX_BYTES:
                raise ValueError('The demo snapshot is larger than expected')
            for info in entries:
                path = (root / info.filename).resolve()
                if not path.is_relative_to(root):
                    raise ValueError('The demo snapshot holds a path outside its workspace')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(info))
        # The snapshot holds every year, so the story moves to after the last of them, whatever date an
        # earlier, emptier demo had reached (#258: the dashboard otherwise showed only January 2027).
        if manifest.get('story_date'):
            story_clock.save(target, date.fromisoformat(manifest['story_date']), advance=True)
    return status()


def clear():
    """Removes only the active demo generation, including its derived records and chats."""
    with _LOCK:
        if not _control().exists():
            return
        path = workspace()
        _control().unlink()
        shutil.rmtree(path, ignore_errors=True)
