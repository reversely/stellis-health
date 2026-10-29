"""Goals a person builds from selectors, with weekly progress and reminders.

Each goal names an activity from ``ACTIVITIES``, an amount where that
activity has one, how many days a week, and an optional reminder time. A day
counts toward a goal when the person logs it, or, for an activity tied to a
measure, when they log a matching reading that day. A reminder asks "Did you
... yet?" from its time each day until the person logs the activity or meets
their weekly goal; "Not yet" keeps a reminder banner up for the rest of that
day. Days are the person's local days, so every read takes their UTC offset.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
import uuid

from stellis import readings
from stellis import storage

MAX_GOALS = 12
MAX_NAME = 40
MAX_DOSE = 10_000
# Dose units, singular and plural.
DOSE_UNITS = {
    "IU": ("IU", "IU"), "mg": ("mg", "mg"), "mcg": ("mcg", "mcg"), "g": ("g", "g"),
    "ml": ("ml", "ml"), "units": ("unit", "units"),
    "tablets": ("tablet", "tablets"), "capsules": ("capsule", "capsules"),
    "drops": ("drop", "drops"), "puffs": ("puff", "puffs"),
}
WEEK_DAYS = 7
HISTORY_WEEKS = 6
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclasses.dataclass(frozen=True)
class Activity:
    """One activity a goal can name.

    Attributes:
        key: Activity ID.
        label: Name in the Activity selector.
        amounts: Amount choices, in ``unit``; empty when an activity has none.
        unit: Unit of each amount: "minutes", "hours" or "servings".
        noun: Singular and plural name of one logged day, such as "walk".
        measure: Readings measure whose readings also count, or None.
    """
    key: str
    label: str
    amounts: tuple[float, ...]
    unit: str
    noun: tuple[str, str]
    measure: str | None = None


ACTIVITIES = {
    activity.key: activity for activity in (
        Activity("walk", "Walk", (10, 15, 20, 30, 45, 60), "minutes",
                 ("walk", "walks")),
        Activity("exercise", "Exercise", (15, 20, 30, 45, 60), "minutes",
                 ("workout", "workouts")),
        Activity("stretch", "Stretch", (5, 10, 15, 20), "minutes",
                 ("stretch", "stretches")),
        Activity("meditate", "Meditate", (5, 10, 15, 20), "minutes",
                 ("session", "sessions")),
        Activity("medicine", "Take a medicine", (), "", ("dose", "doses")),
        Activity("blood_pressure", "Check blood pressure", (), "",
                 ("reading", "readings"), "blood_pressure"),
        Activity("pulse", "Log vital signs", (), "",
                 ("check", "checks"), "pulse"),
        Activity("sleep", "Sleep", (6, 6.5, 7, 7.5, 8, 8.5, 9), "hours",
                 ("night", "nights"), "sleep"),
        Activity("produce", "Eat fruit and vegetables", (3, 4, 5, 6, 7), "servings",
                 ("day", "days")),
    )
}


def _create_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coach_goals (
            id TEXT PRIMARY KEY,
            activity TEXT NOT NULL,
            amount REAL,
            medicine TEXT NOT NULL DEFAULT '',
            dose_amount REAL,
            dose_unit TEXT NOT NULL DEFAULT '',
            times_per_week INTEGER NOT NULL,
            reminder TEXT,
            created_at TEXT NOT NULL)""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(coach_goals)")}
    if "dose_amount" not in columns:  # Tables made before doses had units.
        conn.execute("ALTER TABLE coach_goals ADD COLUMN dose_amount REAL")
        conn.execute("ALTER TABLE coach_goals ADD COLUMN dose_unit TEXT "
                     "NOT NULL DEFAULT ''")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coach_goal_days (
            goal_id TEXT NOT NULL REFERENCES coach_goals(id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            PRIMARY KEY (goal_id, day))""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coach_reminders_pending (
            goal_id TEXT NOT NULL REFERENCES coach_goals(id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            PRIMARY KEY (goal_id, day))""")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def options() -> list[dict[str, object]]:
    """Lists each activity with its amount choices, for the selectors."""
    return [{"key": a.key, "label": a.label, "amounts": list(a.amounts),
             "unit": a.unit, "noun": list(a.noun), "measure": a.measure}
            for a in ACTIVITIES.values()]


def dose_units() -> list[str]:
    """Lists dose units for the dose unit selector."""
    return list(DOSE_UNITS)


def _clean(fields: dict[str, object]) -> tuple:
    """Checks a goal's selector values.

    Returns:
        Activity key, amount, medicine, dose amount, dose unit, days a
        week and reminder.

    Raises:
        ValueError: A value is outside its selector's choices.
    """
    activity = ACTIVITIES.get(str(fields.get("activity") or ""))
    if activity is None:
        raise ValueError("Choose an activity from the list")
    amount = fields.get("amount")
    if activity.amounts:
        if amount is None or float(amount) not in activity.amounts:
            raise ValueError(f"Choose an amount for {activity.label.lower()}")
        amount = float(amount)
    else:
        amount = None
    medicine = " ".join(str(fields.get("medicine") or "").split())
    dose = fields.get("dose_amount")
    unit = str(fields.get("dose_unit") or "")
    if activity.key == "medicine":
        if not medicine:
            raise ValueError("Enter a medicine name")
        if len(medicine) > MAX_NAME:
            raise ValueError(f"Keep a medicine name under {MAX_NAME} characters")
        if dose is not None:
            dose = float(dose)
            if not 0 < dose <= MAX_DOSE:
                raise ValueError("Enter a dose above 0")
            if unit not in DOSE_UNITS:
                raise ValueError("Choose a dose unit")
        else:
            unit = ""
    else:
        medicine, dose, unit = "", None, ""
    times = fields.get("times_per_week")
    if not isinstance(times, int) or not 1 <= times <= WEEK_DAYS:
        raise ValueError("Choose 1 to 7 times a week")
    reminder = fields.get("reminder") or None
    if reminder is not None and not _TIME.match(str(reminder)):
        raise ValueError("Choose a reminder time")
    return activity.key, amount, medicine, dose, unit, times, reminder


def draft(fields: dict[str, object]) -> dict[str, object]:
    """Checks selector values proposed for a new goal, without saving them.

    Returns:
        Checked values, as ``create`` takes them, plus ``title_parts``.

    Raises:
        ValueError: A value is outside its selector's choices.
    """
    activity, amount, medicine, dose, unit, times, reminder = _clean(fields)
    parts = title_parts(ACTIVITIES[activity], amount, medicine,
                        dose_text(dose, unit))
    return {"activity": activity, "amount": amount, "medicine": medicine,
            "dose_amount": dose, "dose_unit": unit, "times_per_week": times,
            "reminder": reminder, "title_parts": parts,
            "title": " ".join(parts)}


def create(fields: dict[str, object]) -> dict[str, str]:
    """Adds a goal from selector values.

    Args:
        fields: ``activity``, ``amount``, ``medicine``, ``dose_amount``,
            ``dose_unit``, ``times_per_week`` and ``reminder`` ("HH:MM" or
            None).

    Returns:
        New goal's ``id``.

    Raises:
        ValueError: A value is invalid, or a person already has MAX_GOALS.
    """
    values = _clean(fields)
    goal_id = str(uuid.uuid4())
    with storage.connect() as conn:
        _create_tables(conn)
        count = conn.execute("SELECT COUNT(*) FROM coach_goals").fetchone()[0]
        if count >= MAX_GOALS:
            raise ValueError(f"Keep at most {MAX_GOALS} goals")
        conn.execute(
            "INSERT INTO coach_goals (id, activity, amount, medicine, "
            "dose_amount, dose_unit, times_per_week, reminder, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (goal_id, *values, _now().isoformat(timespec="seconds")))
    return {"id": goal_id}


def update(goal_id: str, fields: dict[str, object]) -> None:
    """Replaces a goal's selector values.

    Raises:
        LookupError: No goal has ``goal_id``.
        ValueError: A value is invalid.
    """
    values = _clean(fields)
    with storage.connect() as conn:
        _create_tables(conn)
        changed = conn.execute(
            "UPDATE coach_goals SET activity = ?, amount = ?, medicine = ?, "
            "dose_amount = ?, dose_unit = ?, times_per_week = ?, reminder = ? "
            "WHERE id = ?",
            (*values, goal_id)).rowcount
    if not changed:
        raise LookupError("Goal not found")


def delete(goal_id: str) -> None:
    """Deletes a goal and its logged days; readings stay.

    Raises:
        LookupError: No goal has ``goal_id``.
    """
    with storage.connect() as conn:
        _create_tables(conn)
        if not conn.execute("DELETE FROM coach_goals WHERE id = ?",
                            (goal_id,)).rowcount:
            raise LookupError("Goal not found")


def _require(conn, goal_id: str) -> None:
    _create_tables(conn)
    if not conn.execute("SELECT 1 FROM coach_goals WHERE id = ?",
                        (goal_id,)).fetchone():
        raise LookupError("Goal not found")


def _day(text: str) -> str:
    try:
        day = datetime.date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Send a date as YYYY-MM-DD") from exc
    if day > _now().date() + datetime.timedelta(days=1):
        raise ValueError("A log cannot be in the future")
    return day.isoformat()


def mark(goal_id: str, day: str, done: bool, conn=None) -> None:
    """Logs or clears a goal's activity on a day.

    Args:
        conn: An open connection, so joined logging can share one transaction.

    Raises:
        LookupError: No goal has ``goal_id``.
        ValueError: ``day`` is not a date or is in the future.
    """
    day = _day(day)
    if conn is None:
        with storage.connect() as own:
            return mark(goal_id, day, done, own)
    _require(conn, goal_id)
    conn.execute("DELETE FROM coach_goal_days WHERE goal_id = ? AND day = ?",
                 (goal_id, day))
    if done:
        conn.execute("INSERT INTO coach_goal_days VALUES (?, ?)",
                     (goal_id, day))


def not_yet(goal_id: str, day: str) -> None:
    """Records "Not yet" on a reminder, so its banner stays up all day.

    Raises:
        LookupError: No goal has ``goal_id``.
        ValueError: ``day`` is not a date or is in the future.
    """
    day = _day(day)
    with storage.connect() as conn:
        _require(conn, goal_id)
        conn.execute("INSERT OR IGNORE INTO coach_reminders_pending "
                     "VALUES (?, ?)", (goal_id, day))


def _local_day(time: str, offset: datetime.timedelta) -> datetime.date:
    """Local date of an ISO time; a bare date is already local."""
    if len(time) <= 10:
        return datetime.date.fromisoformat(time)
    moment = datetime.datetime.fromisoformat(time)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return (moment.astimezone(datetime.timezone.utc) + offset).date()


def sentence(fields: dict[str, object]) -> str:
    """A goal as one sentence with its schedule, such as "Walk 20 minutes, 4 days a week".

    Raises:
        ValueError: A value is outside its selector's choices.
    """
    checked = draft(fields)
    activity = ACTIVITIES[checked["activity"]]
    times = checked["times_per_week"]
    days = "every day" if times == WEEK_DAYS else (
        f"{times} {'night' if activity.key == 'sleep' else 'day'}{'s' if times != 1 else ''} a week")
    return f"{checked['title']}, {days}"


def dose_text(amount: float | None, unit: str) -> str:
    """Formats a dose, such as "500 mg" or "1 tablet"; '' for none."""
    if amount is None or unit not in DOSE_UNITS:
        return ""
    one, many = DOSE_UNITS[unit]
    return f"{amount:g} {one if amount == 1 else many}"


def title_parts(activity: Activity, amount: float | None, medicine: str,
                dose: str) -> list[str]:
    """Splits a goal's name into words and values, alternating.

    Even indexes hold words and odd indexes hold values, which Home shows
    as pills: ["Take", "500 mg", "of", "metformin"] or ["Walk", "30
    minutes"].
    """
    if activity.key == "medicine":
        return ["Take", dose, "of", medicine] if dose else ["Take", medicine]
    if activity.key == "sleep":
        return ["Sleep at least", f"{amount:g} hours"]
    if activity.key == "produce":
        return ["Eat", f"{amount:g} servings", "of fruit and vegetables"]
    if amount is not None:
        return [activity.label, f"{amount:g} {activity.unit}"]
    return [activity.label]


def reminder_question(activity: Activity, amount: float | None,
                      medicine: str, dose: str) -> list[str]:
    """Splits a reminder's question into words and values, alternating.

    Returns:
        Parts as ``title_parts`` alternates them, such as ["Did you take",
        "500 mg", "of", "metformin", "yet?"].
    """
    if activity.key == "medicine":
        if dose:
            return ["Did you take", dose, "of", medicine, "yet?"]
        return ["Did you take", medicine, "yet?"]
    if activity.key == "sleep":
        return ["Did you log last night's sleep yet?"]
    if activity.key == "blood_pressure":
        return ["Did you check your blood pressure yet?"]
    if activity.key == "pulse":
        return ["Have you logged your vital signs?"]
    if activity.key == "produce":
        return ["Did you eat", f"{amount:g} servings", "of fruit and vegetables yet?"]
    return [f"Did you {activity.label.lower()} for", f"{amount:g} {activity.unit}",
            "yet?"]


def overview(today: str, utc_offset: int, now: str) -> dict[str, object]:
    """Lists goals with their progress, and reminders due now.

    Args:
        today: Person's local date, as YYYY-MM-DD.
        utc_offset: Person's offset from UTC in minutes, east positive.
        now: Person's local time, as HH:MM.

    Returns:
        ``goals``, oldest first, each with its selector values, ``title``,
        ``title_parts`` (see ``title_parts``), ``noun``, ``this_week`` (days logged since Monday), ``done_today``
        and ``weeks`` (logged days in each of HISTORY_WEEKS weeks, oldest
        first, ending this week); ``reminders`` due now, each with
        ``goal_id``, ``ask`` (see ``reminder_question``), ``question``,
        ``title``, ``reminder`` and ``state`` ("ask" before
        an answer, "pending" after "Not yet"); ``activities``; and
        ``dose_units``.
    """
    day = datetime.date.fromisoformat(today)
    if abs(utc_offset) > 14 * 60:
        raise ValueError("Send a UTC offset within 14 hours")
    if not _TIME.match(now):
        raise ValueError("Send a time as HH:MM")
    offset = datetime.timedelta(minutes=utc_offset)
    monday = day - datetime.timedelta(days=day.weekday())
    first = monday - datetime.timedelta(weeks=HISTORY_WEEKS - 1)
    series = {item["key"]: item["points"] for item in readings.series()}
    with storage.connect() as conn:
        _create_tables(conn)
        rows = conn.execute(
            "SELECT * FROM coach_goals ORDER BY created_at, rowid").fetchall()
        logged = conn.execute(
            "SELECT goal_id, day FROM coach_goal_days WHERE day >= ?",
            (first.isoformat(),)).fetchall()
        pending = {(row["goal_id"], row["day"]) for row in conn.execute(
            "SELECT * FROM coach_reminders_pending WHERE day = ?",
            (day.isoformat(),))}
    goals, reminders = [], []
    for row in rows:
        activity = ACTIVITIES[row["activity"]]
        done = {datetime.date.fromisoformat(log["day"])
                for log in logged if log["goal_id"] == row["id"]}
        for point in series.get(activity.measure, []) if activity.measure else []:
            if activity.key == "pulse" and point["source"] != "camera":
                continue
            if activity.key == "sleep" and float(point["value"]) < row["amount"]:
                continue
            done.add(_local_day(str(point["time"]), offset))
        weeks = [sum(1 for n in range(WEEK_DAYS) if start + datetime.timedelta(
            days=n) in done) for start in (
                first + datetime.timedelta(weeks=w)
                for w in range(HISTORY_WEEKS))]
        dose = dose_text(row["dose_amount"], row["dose_unit"])
        parts = title_parts(activity, row["amount"], row["medicine"], dose)
        name = " ".join(parts)
        goal = {
            "id": row["id"], "activity": activity.key, "amount": row["amount"],
            "medicine": row["medicine"], "dose_amount": row["dose_amount"],
            "dose_unit": row["dose_unit"], "title_parts": parts,
            "times_per_week": row["times_per_week"],
            "reminder": row["reminder"], "title": name,
            "noun": list(activity.noun), "measure": activity.measure,
            "this_week": weeks[-1], "done_today": day in done,
            "weeks": weeks,
        }
        goals.append(goal)
        due = (row["reminder"] and now >= row["reminder"]
               and not goal["done_today"]
               and goal["this_week"] < row["times_per_week"])
        if due:
            ask = reminder_question(activity, row["amount"],
                                    row["medicine"], dose)
            reminders.append({
                "goal_id": row["id"], "ask": ask, "question": " ".join(ask),
                "title": name, "reminder": row["reminder"],
                "state": "pending" if (row["id"], day.isoformat()) in pending
                         else "ask"})
    return {"goals": goals, "reminders": reminders, "activities": options(),
            "dose_units": dose_units()}
