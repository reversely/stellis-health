"""Registry of tracked health items on the shared record tables.

Each kind declares its form fields, the days an entry is expected, what one
dated entry holds and the dates derived from its data. Every kind stores its
data as typed JSON in ``reproductive_records`` and its dated outcomes in
``pill_doses``; the table names predate the registry and stay for
compatibility. See ADR 0086.
"""
from __future__ import annotations

from contextlib import nullcontext
import dataclasses
import datetime as dt
import json
import re
import uuid
from typing import Callable

from stellis import documents, goals, notebooks, storage

_TIME = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
STATUSES = ('taken', 'skipped')
# Bounds the schedule walk for run-out dates: about five years of daily doses.
MAX_SCHEDULE_DAYS = 1830
WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def _table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS reproductive_records (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS pill_doses (
        pill_id TEXT NOT NULL, day TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'taken',
        PRIMARY KEY (pill_id, day))''')
    records = {row['name'] for row in conn.execute('PRAGMA table_info(reproductive_records)')}
    # Plain columns rather than foreign keys: a deleted notebook or document
    # leaves the record intact and the link is dropped when read.
    for column in ('notebook_id', 'source_document_id', 'source_page', 'created_at'):
        if column not in records:
            conn.execute(f'ALTER TABLE reproductive_records ADD COLUMN {column} TEXT')
    conn.execute('''CREATE TABLE IF NOT EXISTS tracker_regimes (id TEXT PRIMARY KEY, record_id TEXT NOT NULL, effective_date TEXT NOT NULL, data TEXT NOT NULL, source TEXT NOT NULL, UNIQUE(record_id,effective_date))''')
    outcomes = {row['name'] for row in conn.execute('PRAGMA table_info(pill_doses)')}
    if 'status' not in outcomes:
        conn.execute("ALTER TABLE pill_doses ADD COLUMN status TEXT NOT NULL DEFAULT 'taken'")
    if 'value' not in outcomes:
        conn.execute('ALTER TABLE pill_doses ADD COLUMN value REAL')
    for column,definition in [('taken_at','TEXT'),('time_zone','TEXT'),('dose_amount','REAL'),('dose_unit','TEXT'),('logged_at','TEXT')]:
        if column not in outcomes:conn.execute(f'ALTER TABLE pill_doses ADD COLUMN {column} {definition}')


def date(value) -> dt.date:
    """Reads a calendar date without a timezone conversion."""
    return dt.date.fromisoformat(value)


def _text(data, key, limit, label, required=True):
    value = ' '.join(str(data.get(key) or '').split()) if key != 'note' else str(data.get(key) or '').strip()
    if required and not value:
        raise ValueError(f'Enter {label}')
    if len(value) > limit:
        raise ValueError(f'Keep {label} under {limit + 1} characters')
    return value


def _number(data, key, label, low, high, integer=False, default=None):
    value = data.get(key, default)
    if value in (None, ''):
        if default is None:
            return None
        value = default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'Enter a number for {label}') from None
    if integer and not number.is_integer():
        raise ValueError(f'Enter a whole number for {label}')
    if not low <= number <= high:
        raise ValueError(f'Enter {label} between {low:g} and {high:g}')
    return int(number) if integer else number


def _optional_date(data, key):
    return date(data[key]).isoformat() if data.get(key) else ''


# Schedules -----------------------------------------------------------------

def _pack_expected(record, day):
    offset = (day - date(record['start'])).days
    return offset >= 0 and offset % (record['active'] + record['break_days']) < record['active']


def _course_expected(record, day):
    if record.get('course_state') == 'planned':
        return False
    start = date(record['start'])
    if day < start or (record.get('end') and day > date(record['end'])):
        return False
    schedule = record['schedule_type']
    if schedule == 'daily':
        return True
    if schedule == 'every':
        return (day - start).days % record['every_days'] == 0
    if schedule == 'weekdays':
        return day.weekday() in record['weekdays']
    return False


def _never(record, day):
    return False


def _course_loggable(record, day):
    """Scheduled days, or any day of the course for an as-needed medicine."""
    if record.get('course_state') == 'planned':return False
    if record['schedule_type'] != 'as_needed':
        return _course_expected(record, day)
    return date(record['start']) <= day and not (record.get('end') and day > date(record['end']))


def _next_expected(kind, record, after):
    """The first expected day after ``after``, or None within the bound."""
    day = after
    for _ in range(MAX_SCHEDULE_DAYS):
        day += dt.timedelta(days=1)
        if record.get('end') and day > date(record['end']):
            return None
        if kind.expected(record, day):
            return day
    return None


# Validation per kind ---------------------------------------------------------

def _validate_period(data, record_id, conn):
    start = date(data['start'])
    if start > dt.date.today():
        raise ValueError('Start date cannot be in the future')
    end = date(data['end']) if data.get('end') else None
    if end and (end < start or end > dt.date.today()):
        raise ValueError('End date must be between start date and today')
    if data.get('flow') not in {'Spotting', 'Light', 'Moderate', 'Heavy'}:
        raise ValueError('Choose a flow')
    for row in conn.execute("SELECT id, data FROM reproductive_records WHERE kind = 'period'"):
        other = json.loads(row['data'])
        if row['id'] != record_id and start <= date(other.get('end') or dt.date.today().isoformat()) and \
                date(other['start']) <= (end or dt.date.today()):
            raise ValueError('Period dates overlap an existing entry')
    return data


def _validate_pill(data, record_id, conn):
    date(data['start'])
    if not 1 <= data['active'] <= 365 or not 0 <= data['break_days'] <= 365:
        raise ValueError('Enter a valid pack length')
    dt.time.fromisoformat(data['time'])
    _text(data, 'name', 120, 'a name')
    return data


def _validate_larc(data, record_id, conn):
    start = date(data['start'])
    if start > dt.date.today():
        raise ValueError('Start date cannot be in the future')
    if data['type'] not in {'Hormonal IUD', 'Copper IUD', 'Implant'}:
        raise ValueError('Choose a device type')
    if date(data['renewal']) <= start:
        raise ValueError('Renewal date must follow insertion date')
    if data.get('end') and not start <= date(data['end']) <= dt.date.today():
        raise ValueError('Removal date must be between insertion date and today')
    if not 0 <= data['notice'] <= 365:
        raise ValueError('Enter a notice between 0 and 365 days')
    _text(data, 'name', 120, 'a name')
    return data


def _validate_course(data, record_id, conn):
    clean = {
        'course_state': str(data.get('course_state') or 'active'),
        'name': _text(data, 'name', 120, 'a medicine name'),
        'dose_amount': _number(data, 'dose_amount', 'the dose', 0.001, goals.MAX_DOSE),
        'dose_unit': str(data.get('dose_unit') or ''),
        'schedule_type': str(data.get('schedule_type') or ''),
        'start': date(data.get('start') or '').isoformat() if data.get('start') else '',
        'end': _optional_date(data, 'end'),
        'units_per_fill': _number(data, 'units_per_fill', 'units in each fill', 1, 10_000, integer=True),
        'supply_unit': str(data.get('supply_unit') or ''),
        'units_per_dose': _number(data, 'units_per_dose', 'units in each dose', 0.001, 1000, default=1),
        'repeats': _number(data, 'repeats', 'repeats', 0, 20, integer=True, default=0),
        'time': str(data.get('time') or ''),
        'reminder': bool(data.get('reminder')),
        'note': _text(data, 'note', 500, 'the note', required=False),
    }
    if clean['course_state'] not in {'active', 'planned'}:
        raise ValueError('Choose active or planned')
    if clean['dose_amount'] is None:
        raise ValueError('Enter the dose')
    if clean['dose_unit'] not in goals.DOSE_UNITS:
        raise ValueError('Choose a dose unit')
    if clean['supply_unit'] and clean['supply_unit'] not in goals.DOSE_UNITS:
        raise ValueError('Choose a supply unit')
    if not clean['start']:
        raise ValueError('Enter a start date')
    if clean['end'] and clean['end'] < clean['start']:
        raise ValueError('End date must follow the start date')
    schedule = clean['schedule_type']
    if schedule not in SCHEDULES:
        raise ValueError('Choose a schedule')
    if schedule == 'every':
        clean['every_days'] = _number(data, 'every_days', 'days between doses', 2, 30, integer=True)
        if clean['every_days'] is None:
            raise ValueError('Enter days between doses')
    if schedule == 'weekdays':
        days = data.get('weekdays') or []
        if not isinstance(days, list) or not days or any(d not in range(7) for d in days):
            raise ValueError('Choose at least one weekday')
        clean['weekdays'] = sorted(set(days))
    if clean['time'] and not _TIME.match(clean['time']):
        raise ValueError('Choose a reminder time')
    return clean


DATING = {'last_period': 'First day of last period', 'transfer': 'Embryo transfer', 'ultrasound': 'Dating ultrasound'}
# Days from the gestational start to the due date, and from conception to a transfer by embryo age.
PREGNANCY_DAYS = 280
CONCEPTION_DAY = 14


def _validate_treatment(data, record_id, conn):
    start=date(data.get('start') or '').isoformat()
    end=_optional_date(data,'end')
    if end and end<start:raise ValueError('End date must follow the start')
    return {'name':_text(data,'name',120,'a treatment name'),'phase':_text(data,'phase',120,'a phase',required=False),'start':start,'end':end,'note':_text(data,'note',500,'the note',required=False)}


def pregnancy_start(record) -> dt.date:
    """The gestational start (first day of the last period, or its equivalent)."""
    basis = date(record['basis_date'])
    if record['dating_basis'] == 'transfer':
        return basis - dt.timedelta(days=CONCEPTION_DAY + record['embryo_day'])
    if record['dating_basis'] == 'ultrasound':
        return basis - dt.timedelta(days=record['scan_weeks'] * 7 + record['scan_days'])
    return basis


def _validate_pregnancy(data, record_id, conn):
    basis = data.get('dating_basis')
    if basis not in DATING:
        raise ValueError('Choose how the pregnancy is dated')
    if not data.get('basis_date'):
        raise ValueError('Enter the dating date')
    clean = {'dating_basis': basis, 'basis_date': date(data['basis_date']).isoformat(),
             'booked_birth': _optional_date(data, 'booked_birth'),
             'note': _text(data, 'note', 500, 'the note', required=False)}
    if clean['basis_date'] > dt.date.today().isoformat():
        raise ValueError('The dating date cannot be in the future')
    if basis == 'transfer':
        clean['embryo_day'] = _number(data, 'embryo_day', 'the embryo age in days', 2, 7, integer=True)
        if clean['embryo_day'] is None:
            raise ValueError('Enter the embryo age in days')
    if basis == 'ultrasound':
        clean['scan_weeks'] = _number(data, 'scan_weeks', 'weeks at the scan', 4, 42, integer=True)
        clean['scan_days'] = _number(data, 'scan_days', 'days at the scan', 0, 6, integer=True, default=0)
        if clean['scan_weeks'] is None:
            raise ValueError('Enter the weeks at the scan')
    if clean['booked_birth'] and clean['booked_birth'] <= pregnancy_start(clean).isoformat():
        raise ValueError('The booked birth date must follow the start of the pregnancy')
    return clean


def basis_text(record) -> str:
    """How the due date was worked out, in words."""
    when = date(record['basis_date']).strftime('%B %-d, %Y')
    if record['dating_basis'] == 'transfer':
        return f"Dated from the day-{record['embryo_day']} embryo transfer on {when}."
    if record['dating_basis'] == 'ultrasound':
        return f"Dated from an ultrasound on {when} at {record['scan_weeks']} weeks, {record['scan_days']} days."
    return f"Dated from the first day of the last period on {when}."


def _pregnancy_derived(record, outcomes, today):
    due = pregnancy_start(record) + dt.timedelta(days=PREGNANCY_DAYS)
    items = [{'key': 'due', 'date': due.isoformat(), 'status': 'due', 'title': 'Due date', 'detail': basis_text(record)}]
    if record.get('booked_birth'):
        items.append({'key': 'birth', 'date': record['booked_birth'], 'status': 'scheduled', 'title': 'Booked birth',
                      'detail': 'The timeline ends here; the due date stays for reference.'})
    return items


SCHEDULES = {'daily': 'Every day', 'every': 'Every few days', 'weekdays': 'Chosen weekdays',
             'as_needed': 'As needed'}


# Derived dates ---------------------------------------------------------------

def _course_fills(record, outcomes):
    """Calculated run-out days, one per fill.

    Each scheduled dose consumes ``units_per_dose``; a dose logged as skipped
    consumes nothing. A fill runs out on the last dose day it covers.
    """
    per_fill, per_dose = record.get('units_per_fill'), record.get('units_per_dose') or 1
    if not per_fill or record['schedule_type'] == 'as_needed':
        return []
    doses_per_fill = int(per_fill // per_dose)
    if doses_per_fill < 1:
        return []
    fills, consumed = [], 0
    targets = [doses_per_fill * (n + 1) for n in range(record.get('repeats', 0) + 1)]
    day = date(record['start'])
    end = date(record['end']) if record.get('end') else None
    for _ in range(MAX_SCHEDULE_DAYS):
        if end and day > end:
            break
        if _course_expected(record, day) and outcomes.get(day.isoformat(), {}).get('status') != 'skipped':
            consumed += 1
            if consumed == targets[len(fills)]:
                fills.append(day.isoformat())
                if len(fills) == len(targets):
                    break
        day += dt.timedelta(days=1)
    return [{'fill': n + 1, 'of': len(targets), 'date': value, 'doses': doses_per_fill}
            for n, value in enumerate(fills)]


def _course_derived(record, outcomes, today):
    derived = []
    for fill in _course_fills(record, outcomes):
        derived.append({'key': f"fill-{fill['fill']}", 'date': fill['date'], 'status': 'calculated',
                        'title': f"{record['name']}: fill {fill['fill']} of {fill['of']} runs out",
                        'detail': f"{fill['doses']} doses per fill, {schedule_text(record).lower()} "
                                  f"from {record['start']}"})
    if record.get('end'):
        derived.append({'key': 'end', 'date': record['end'], 'status': 'planned',
                        'title': f"{record['name']}: last planned dose", 'detail': ''})
    return derived


def _larc_derived(record, outcomes, today):
    if record.get('end'):
        return []
    return [{'key': 'renewal', 'date': record['renewal'], 'status': 'scheduled',
             'title': f"{record['name']} renewal", 'detail': record['type']}]


def _none(record, outcomes, today):
    return []


def schedule_text(record) -> str:
    """Describes a course schedule, such as "Every 2 days"."""
    schedule = record.get('schedule_type')
    if schedule == 'every':
        return 'Every other day' if record['every_days'] == 2 else f"Every {record['every_days']} days"
    if schedule == 'weekdays':
        names = [WEEKDAYS[d] for d in record['weekdays']]
        return 'On ' + (names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1])
    return SCHEDULES.get(schedule, '')


# Registry --------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Kind:
    """One kind of tracked item.

    Attributes:
        key: Stored kind.
        label: Singular name, such as "Medicine course".
        plural: Name of the group, such as "Medicines".
        area: Group in joined logging and navigation.
        icon: Tabler icon name.
        fields: Form fields; the browser renders forms from these.
        validate: Cleans and checks data; raises ValueError.
        expected: Whether an entry is expected on a day.
        logs: Whether dated outcomes (taken or skipped) can be logged.
        loggable: Whether an outcome may be logged on a day; ``expected`` when None.
        derive: Calculated and planned dates for projections.
        gate: Health preference that must be true for the kind to show, or None.
        add: Label of the action that creates one.
        description: One line for the empty state.
    """
    key: str
    label: str
    plural: str
    area: str
    icon: str
    fields: tuple[dict, ...]
    validate: Callable
    expected: Callable = _never
    logs: bool = False
    loggable: Callable | None = None
    derive: Callable = _none
    gate: str | None = None
    add: str = ''
    description: str = ''

    def can_log(self, record, day) -> bool:
        return self.logs and (self.loggable or self.expected)(record, day)

    def schema(self) -> dict:
        return {'key': self.key, 'label': self.label, 'plural': self.plural, 'area': self.area,
                'icon': self.icon, 'logs': self.logs, 'gate': self.gate,
                'add': self.add or f'Add {self.label.lower()}', 'description': self.description,
                'fields': list(self.fields)}


def _field(key, label, type_, **options):
    return {'key': key, 'label': label, 'type': type_, 'required': options.pop('required', True), **options}


_LINKS = (
    _field('notebook_id', 'Notebook', 'notebook', required=False),
    _field('source_document_id', 'Source record', 'document', required=False),
    _field('source_page', 'Source page', 'number', required=False, min=1, max=documents.MAX_PAGES),
)

KINDS = {kind.key: kind for kind in (
    Kind('period', 'Period', 'Cycle', 'Cycle', 'calendar', (
        _field('start', 'Period start', 'date'), _field('end', 'Period end', 'date', required=False),
        _field('flow', 'Flow', 'choice', choices=['Spotting', 'Light', 'Moderate', 'Heavy']),
        _field('note', 'Symptoms and notes', 'textarea', required=False, max=500)),
        _validate_period, gate='experiences_periods', add='Log period'),
    Kind('pill', 'Pill pack', 'Pills', 'Contraception', 'circle-check', (
        _field('name', 'Pill name', 'text', max=120), _field('start', 'Pack start', 'date'),
        _field('active', 'Active pills', 'number', min=1, max=365),
        _field('break_days', 'Break days', 'number', min=0, max=365),
        _field('time', 'Reminder time', 'time'), _field('reminder', 'In-app reminders', 'boolean', required=False)),
        _validate_pill, _pack_expected, logs=True, gate='experiences_periods'),
    Kind('larc', 'Device', 'Devices', 'Contraception', 'clock', (
        _field('name', 'Device name', 'text', max=120), _field('start', 'Insertion date', 'date'),
        _field('type', 'Device type', 'choice', choices=['Hormonal IUD', 'Copper IUD', 'Implant']),
        _field('renewal', 'Renewal date', 'date'),
        _field('notice', 'Reminder notice in days', 'number', min=0, max=365),
        _field('end', 'Removal date', 'date', required=False),
        _field('reminder', 'In-app reminders', 'boolean', required=False)),
        _validate_larc, derive=_larc_derived, gate='experiences_periods'),
    Kind('treatment','Treatment phase','Treatment phases','Care','calendar',(_field('name','Treatment','text',max=120),_field('phase','Phase','text',max=120,required=False),_field('start','Start date','date'),_field('end','End date','date',required=False),_field('note','Notes','textarea',required=False,max=500)),_validate_treatment),
    Kind('medicine', 'Medicine course', 'Medicines', 'Medicines', 'pill', (
        _field('name', 'Medicine', 'text', max=120),
        _field('course_state', 'Course status', 'choice', choices=['active','planned']),
        _field('dose_amount', 'Dose', 'number', min=0.001, max=goals.MAX_DOSE, step='any'),
        _field('dose_unit', 'Unit', 'choice', choices=list(goals.DOSE_UNITS)),
        _field('schedule_type', 'Schedule', 'choice', choices=list(SCHEDULES), labels=SCHEDULES),
        _field('every_days', 'Days between doses', 'number', min=2, max=30,
               when={'field': 'schedule_type', 'is': 'every'}),
        _field('weekdays', 'Weekdays', 'weekdays', choices=list(WEEKDAYS),
               when={'field': 'schedule_type', 'is': 'weekdays'}),
        _field('start', 'Start date', 'date'), _field('end', 'End date', 'date', required=False),
        _field('units_per_fill', 'Units in each fill', 'number', required=False, min=1, max=10_000,
               hint='From the prescription, such as 45 tablets'),
        _field('supply_unit', 'Supply unit', 'choice', required=False, choices=list(goals.DOSE_UNITS)),
        _field('units_per_dose', 'Units in each dose', 'number', required=False, min=0.001, max=1000,
               step='any', default=1),
        _field('repeats', 'Repeats', 'number', required=False, min=0, max=20, default=0),
        _field('time', 'Reminder time', 'time', required=False),
        _field('reminder', 'In-app reminders', 'boolean', required=False),
        _field('note', 'Notes', 'textarea', required=False, max=500)) + _LINKS,
        _validate_course, _course_expected, logs=True, loggable=_course_loggable,
        derive=_course_derived, description='Log doses and see when each fill runs out'),
    Kind('pregnancy', 'Pregnancy', 'Pregnancy', 'Pregnancy', 'calendar', (
        _field('dating_basis', 'Dated from', 'choice', choices=list(DATING), labels=DATING),
        _field('basis_date', 'Date', 'date'),
        _field('embryo_day', 'Embryo age in days', 'number', min=2, max=7, when={'field': 'dating_basis', 'is': 'transfer'}),
        _field('scan_weeks', 'Weeks at the scan', 'number', min=4, max=42, when={'field': 'dating_basis', 'is': 'ultrasound'}),
        _field('scan_days', 'Days at the scan', 'number', min=0, max=6, default=0,
               when={'field': 'dating_basis', 'is': 'ultrasound'}),
        _field('booked_birth', 'Booked birth date', 'date', required=False,
               hint='If the clinic books an induction or a caesarean'),
        _field('note', 'Notes', 'textarea', required=False, max=500)) + _LINKS,
        _validate_pregnancy, derive=_pregnancy_derived, description='See the weeks, the due date and what is coming up'),
)}
# Each stored record may carry these links whatever its kind.
LINK_KEYS = ('notebook_id', 'source_document_id', 'source_page')


def kinds() -> list[dict]:
    """Schemas for every registered kind, in registry order."""
    return [kind.schema() for kind in KINDS.values()]


def _kind(key) -> Kind:
    if key not in KINDS:
        raise ValueError('Choose a listed tracker')
    return KINDS[key]


def _links(data, existing=None, conn=None):
    """Checks notebook and source links; an absent key keeps the stored link."""
    links = {key: (existing or {}).get(key) for key in LINK_KEYS}
    for key in LINK_KEYS:
        if key in data:
            links[key] = data[key] or None
    if links['notebook_id'] and not (conn.execute('SELECT 1 FROM notebooks WHERE id=?',(links['notebook_id'],)).fetchone() if conn is not None else notebooks.exists(links['notebook_id'])):
        raise ValueError('Choose an existing notebook')
    if links['source_document_id']:
        try:
            record = dict(conn.execute('SELECT * FROM documents WHERE id=?',(links['source_document_id'],)).fetchone() or {}) if conn is not None else documents.get(links['source_document_id'])
            if not record:raise LookupError('Source not found')
        except LookupError:
            raise ValueError('Choose an existing source record') from None
        if links['source_page'] not in (None, ''):
            page = _number({'p': links['source_page']}, 'p', 'the source page', 1, record['page_count'], integer=True)
            links['source_page'] = str(page)
    else:
        links['source_page'] = None
    return links


def save(kind_key, data, record_id=None, conn=None):
    """Validates and stores a tracked item.

    Returns:
        The stored record with its ``id`` and ``kind``.
    """
    kind = _kind(kind_key)
    incoming = {key: data[key] for key in LINK_KEYS if key in data}
    fields = {key: value for key, value in data.items() if key not in LINK_KEYS}
    record_id = record_id or str(uuid.uuid4())
    with (nullcontext(conn) if conn is not None else storage.connect()) as connection:
        _table(connection)
        existing = connection.execute('SELECT * FROM reproductive_records WHERE id = ?', (record_id,)).fetchone()
    if existing and existing['kind'] != kind.key:
        raise ValueError('Tracker type cannot change')
    links = _links(incoming, dict(existing) if existing else None, conn)
    with (nullcontext(conn) if conn is not None else storage.connect()) as connection:
        _table(connection)
        clean = kind.validate(fields, record_id, connection)
        connection.execute('''INSERT INTO reproductive_records
            (id, kind, data, notebook_id, source_document_id, source_page, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET data=excluded.data, notebook_id=excluded.notebook_id,
                source_document_id=excluded.source_document_id, source_page=excluded.source_page''',
                     (record_id, kind.key, json.dumps(clean), links['notebook_id'],
                      links['source_document_id'], links['source_page'],
                      dt.datetime.now(dt.timezone.utc).isoformat()))
    return {'id': record_id, 'kind': kind.key, **clean, **links}


def delete(record_id):
    """Removes a tracked item and its dated outcomes."""
    with storage.connect() as conn:
        _table(conn)
        conn.execute('DELETE FROM pill_doses WHERE pill_id = ?', (record_id,))
        conn.execute('DELETE FROM reproductive_records WHERE id = ?', (record_id,))
    return {'deleted': True}


def log(record_id, day, status, value=None, conn=None, *, taken_at=None, time_zone=None):
    """Saves or clears one dated outcome.

    Args:
        record_id: The tracked item.
        day: ISO date, today or earlier.
        status: "taken", "skipped", or None to clear the day.
        value: Optional amount when it differs from the usual dose.
        conn: An open connection, so joined logging can share one transaction.
    """
    if status not in (None, *STATUSES):
        raise ValueError('Choose a listed dose status')
    selected = date(day)
    if selected > dt.date.today():
        raise ValueError('Dose date cannot be in the future')
    if value is not None:
        value = _number({'v': value}, 'v', 'the amount', 0.001, goals.MAX_DOSE)
    if conn is None:
        with storage.connect() as own:
            return log(record_id, day, status, value, own, taken_at=taken_at,time_zone=time_zone)
    _table(conn)
    row = conn.execute('SELECT kind, data FROM reproductive_records WHERE id = ?', (record_id,)).fetchone()
    kind = KINDS.get(row['kind']) if row else None
    if kind is None or not kind.logs:
        raise ValueError('Tracked item was not found')
    regime=at_date(json.loads(row['data']),record_id,selected,conn)
    if not kind.can_log(regime, selected):
        raise ValueError('Choose an active pill day' if kind.key == 'pill' else 'Choose a scheduled dose day')
    if taken_at:
        if status!='taken':raise ValueError('An administration time belongs to a taken dose')
        instant=dt.datetime.fromisoformat(taken_at)
        if instant.tzinfo is None:raise ValueError('The dose time needs a time zone')
        if instant.date()!=selected:raise ValueError('Dose time must match the selected local day')
        if instant.astimezone(dt.timezone.utc)>dt.datetime.now(dt.timezone.utc):raise ValueError('Dose time cannot be in the future')
        if time_zone:
            from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
            try:zone=ZoneInfo(time_zone)
            except ZoneInfoNotFoundError as exc:raise ValueError('Choose a valid time zone') from exc
            if instant.astimezone(dt.timezone.utc).astimezone(zone).isoformat()!=instant.isoformat():raise ValueError('The time zone does not match the dose time')
        taken_at=instant.isoformat()
    elif time_zone:raise ValueError('Add the actual dose time before its time zone')
    if status:
        conn.execute('''INSERT INTO pill_doses (pill_id,day,status,value,taken_at,time_zone,dose_amount,dose_unit,logged_at) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(pill_id,day) DO UPDATE SET status=excluded.status,value=excluded.value,taken_at=excluded.taken_at,time_zone=excluded.time_zone,dose_amount=excluded.dose_amount,dose_unit=excluded.dose_unit,logged_at=excluded.logged_at''',
            (record_id,day,status,value,taken_at,time_zone,value if value is not None else regime.get('dose_amount'),regime.get('dose_unit'),dt.datetime.now(dt.timezone.utc).isoformat()))
    else:
        conn.execute('DELETE FROM pill_doses WHERE pill_id = ? AND day = ?', (record_id, day))
    return {'saved': True}


def _live_links(record):
    """Drops links whose notebook or document no longer exists."""
    if record['notebook_id'] and not notebooks.exists(record['notebook_id']):
        record['notebook_id'] = None
    record['source_title'] = None
    if record['source_document_id']:
        try:
            record['source_title'] = documents.get(record['source_document_id'])['title']
        except LookupError:
            record['source_document_id'] = record['source_page'] = None
    if record['source_page']:
        record['source_page'] = int(record['source_page'])
    return record


def summary(record, today) -> dict:
    """Today's state and derived dates for one stored record."""
    kind = KINDS[record['kind']]
    outcomes = record['outcomes']
    expected = kind.expected(record, today)
    taken = sum(1 for item in outcomes.values() if item['status'] == 'taken')
    result = {
        'expected_today': expected,
        'loggable_today': kind.can_log(record, today),
        'today_status': outcomes.get(today.isoformat(), {}).get('status'),
        'next_expected': None,
        'taken_count': taken,
        'skipped_count': len(outcomes) - taken,
        'derived': kind.derive(record, outcomes, today),
    }
    if kind.expected is not _never:
        upcoming = _next_expected(kind, record, today)
        result['next_expected'] = upcoming.isoformat() if upcoming else None
    if kind.key == 'pregnancy':
        start = pregnancy_start(record)
        elapsed = (today - start).days
        result['pregnancy'] = {
            'start': start.isoformat(), 'due': (start + dt.timedelta(days=PREGNANCY_DAYS)).isoformat(),
            'weeks': elapsed // 7, 'days': elapsed % 7,
            'trimester': 'First trimester' if elapsed < 14 * 7 else 'Second trimester' if elapsed < 28 * 7 else 'Third trimester',
            'booked_birth': record.get('booked_birth') or None, 'basis_text': basis_text(record)}
    if kind.key == 'medicine':
        result['schedule_text'] = schedule_text(record)
        start = date(record['start'])
        if record.get('end'):
            span = (date(record['end']) - start).days or 1
            result['progress'] = max(0.0, min(1.0, (today - start).days / span))
    return result


def list_records(kinds_filter=None, notebook_id=None, today=None) -> list[dict]:
    """Stored records with outcomes and today's summary.

    Args:
        kinds_filter: Kind keys to include; all when None.
        notebook_id: Only records linked to this notebook.
        today: The person's local date; the server date when None.
    """
    today = today or dt.date.today()
    with storage.connect() as conn:
        _table(conn)
        outcomes = {}
        for row in conn.execute('SELECT * FROM pill_doses ORDER BY day'):
            outcomes.setdefault(row['pill_id'], {})[row['day']] = {key:row[key] for key in ('status','value','taken_at','time_zone','dose_amount','dose_unit','logged_at')}
        rows = conn.execute('SELECT * FROM reproductive_records ORDER BY id').fetchall()
        versions = [dict(r) for r in conn.execute('SELECT * FROM tracker_regimes ORDER BY effective_date')]
    result = []
    for row in rows:
        if row['kind'] not in KINDS or (kinds_filter and row['kind'] not in kinds_filter):
            continue
        record = _live_links({'id': row['id'], 'kind': row['kind'], **at_date(json.loads(row['data']), row['id'], today, versions=versions),
                              **{key: row[key] for key in LINK_KEYS}, 'created_at': row['created_at']})
        if notebook_id and record['notebook_id'] != notebook_id:
            continue
        record['regimes'] = [{'effective_date':v['effective_date'],'data':json.loads(v['data']),'source':json.loads(v['source'])} for v in versions if v['record_id']==row['id']]
        record['outcomes'] = outcomes.get(row['id'], {})
        record['summary'] = summary(record, today)
        result.append(record)
    return result


def at_date(data, record_id, day, conn=None, versions=None):
    """Resolve the approved regime effective on this day, retaining earlier history."""
    if versions is None:
        if conn is None:
            with storage.connect() as own:
                _table(own)
                return at_date(data, record_id, day, own)
        if 'id' in data:
            base=conn.execute('SELECT data FROM reproductive_records WHERE id=?',(record_id,)).fetchone()
            if base:data={**data,**json.loads(base['data'])}
        versions = [dict(r) for r in conn.execute('SELECT * FROM tracker_regimes WHERE record_id=? ORDER BY effective_date', (record_id,))]
    result = dict(data)
    for regime in versions:
        if regime['record_id'] == record_id and regime['effective_date'] <= day.isoformat():
            result.update(json.loads(regime['data']))
    return result
