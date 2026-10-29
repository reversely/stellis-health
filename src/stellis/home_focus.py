"""For you now: the most pressing items across the person's notebooks, for the top of My Health.

Every item comes from the account's own data and links to where it can be acted on. Nothing is
model-written, so the list is fast and reorders as soon as the data changes.
"""
import datetime
from urllib.parse import quote

from stellis import storage

LIMIT = 6
RESULT_LIMIT = 4
UPCOMING_DAYS = 14


def _day_label(day: datetime.date, today: datetime.date) -> str:
    gap = (day - today).days
    if gap == 0:
        return "Today"
    if gap == 1:
        return "Tomorrow"
    if 1 < gap < 7:
        return day.strftime("%A")
    return day.strftime("%b %-d") if day.year == today.year else day.strftime("%b %-d, %Y")


def _date_label(value: str) -> str:
    try:
        return datetime.date.fromisoformat(str(value)[:10]).strftime("%b %-d, %Y")
    except ValueError:
        return str(value)


def _ask(notebook_id: str | None, question: str) -> str:
    """Opens notebook Ask with the question waiting in the box, or Ask Stellis without a notebook."""
    if notebook_id:
        return f"/notebook/{quote(str(notebook_id))}?ask={quote(question)}"
    return "/chat"


def _upcoming(today):
    from stellis import care_events
    items = []
    for event in care_events.list_all():
        if event.get('precision') == 'month':  # A month alone is not yet a date to prepare for.
            continue
        try:
            day = datetime.date.fromisoformat(str(event["starts_at"])[:10])
        except ValueError:
            continue
        gap = (day - today).days
        if 0 <= gap <= UPCOMING_DAYS:
            booked = event["status"] == "scheduled"
            items.append({"kind": "upcoming", "category": "monitor", "rank": 100 - gap, "icon": "calendar",
                          "title": f"{event['title']} is coming up",
                          "detail": f"{_day_label(day, today)}, {'booked' if booked else 'planned'}. Open it to add questions from your records.",
                          "action": {"label": "Open", "href": event["target"]}})
    return items


def _to_confirm():
    """One summary of what Stellis read but the person has not confirmed: results and record dates.

    Confirming happens on each record's page, beside the original, and never blocks an upload
    (owner, 2026-10-29: "a summary of confirmation requests"); this gathers them in one place.
    """
    from stellis import documents, health_facts
    with storage.connect() as conn:
        health_facts._create_table(conn)
        counts = {row["document_id"]: row["n"] for row in conn.execute(
            "SELECT document_id, COUNT(*) AS n FROM health_facts WHERE status = 'suggested' GROUP BY document_id")}
    records = documents.list_all()  # Newest first.
    undated = {record["id"] for record in records if not record.get("record_date")}
    waiting = [record for record in records if record["id"] in counts or record["id"] in undated]
    if not waiting:
        return []
    results, dates = sum(counts.values()), len(undated)
    parts = [f"{results} {'result' if results == 1 else 'results'}"] * bool(results) + [f"{dates} {'date' if dates == 1 else 'dates'}"] * bool(dates)
    titles = [record["title"] for record in waiting]
    where = f"In {titles[0]}" if len(titles) == 1 else f"In {len(titles)} records: " + ", ".join(titles[:2]) + (" and more" if len(titles) > 2 else "")
    return [{"kind": "confirm", "category": "understand", "rank": 90, "icon": "file-text",
             "title": f"I found {' and '.join(parts)} to confirm", "detail": where,
             "action": {"label": "Review", "href": "/document/" + waiting[0]["id"]}}]


def _flagged_results():
    """The latest accepted result for each test, when it is outside the lab's range."""
    from stellis import health_facts
    latest = {}
    for fact in health_facts.accepted():  # Newest date first.
        latest.setdefault(str(fact["name"]).casefold(), fact)
    items = []
    for fact in latest.values():
        status = fact.get("range_status") or {}
        if status.get("tone") != "alert":
            continue
        value = f"{fact['value']} {fact['unit'] or ''}".strip()
        reference = fact.get("reference") or {}
        side = {"low": "below", "high": "above"}.get(reference.get("flag")) or (
            "below" if "below" in status["label"].lower() else "above" if "above" in status["label"].lower() else None)
        bounds = f" of {reference['low']:g}–{reference['high']:g}" if reference.get("low") is not None and reference.get("high") is not None else ""
        where = f", {side} the lab’s range{bounds}" if side else f", {status['label'].lower()}"
        items.append({"kind": "flagged", "category": "understand", "rank": 80, "icon": "alert-circle",
                      "title": f"I can explain your {fact['name']} result",
                      "detail": f"{value} on {_date_label(fact['date'])}{where}.",
                      "action": {"label": "Ask about it", "href": _ask(fact.get("notebook_id"), f"What does my {fact['name']} result of {value} mean?")},
                      "source": {"title": fact["document_title"], "href": fact["target"]}})
    return items


def topic_labels() -> set[str]:
    """Names of notebooks, collections, setup interests and focus areas, casefolded.

    Setup once saved these topic names as goal to-dos ("Pregnancy"); a to-do that is only a
    topic name is not something to do, so For you now leaves it out.
    """
    from stellis import collections, notebooks, onboarding_profile
    labels = {book["name"] for book in notebooks.list_notebooks()}
    labels |= {group["name"] for group in collections.list_collections()}
    labels |= {interest["note"] for interest in onboarding_profile.INTERESTS}
    labels |= {interest["label"] for interest in onboarding_profile.INTERESTS}
    labels |= {choice["label"] for choice in onboarding_profile.focus_choices()}
    labels.add(onboarding_profile.CYCLE_GOAL)
    return {" ".join(label.casefold().split()) for label in labels if label}


def _todos():
    from stellis import todos
    topics = topic_labels()
    items = []
    with storage.connect() as conn:
        waiting = conn.execute("SELECT COUNT(*) FROM suggestions WHERE state = 'pending'").fetchone()[0]
    if waiting:
        items.append({"kind": "suggested", "category": "improve", "rank": 70, "icon": "sparkles",
                      "title": f"I found {waiting} {'to-do' if waiting == 1 else 'to-dos'} in your visits and notes",
                      "detail": "Keep the ones you want, and I’ll remind you.",
                      "action": {"label": "Review", "href": "/todos"}})
    for group in todos.gather()["groups"]:
        for item in [item for item in group["items"] if " ".join(item["text"].casefold().split()) not in topics][:2]:
            items.append({"kind": "todo", "category": "improve", "rank": 65, "icon": "circle-check", "title": item["text"],
                          "detail": "From " + group["source"]["title"],
                          "action": {"label": "Open", "href": "/todos"}})
    return items[:4]


def _empty_notebooks():
    from stellis import notebooks, notebook_sources
    items = []
    for book in notebooks.list_notebooks():
        if book.get("archived_at") or notebook_sources.list_sources(book["id"]):
            continue
        items.append({"kind": "empty", "category": "understand", "rank": 40, "icon": "upload",
                      "title": f"Add a first record to {book['name']}",
                      "detail": "A lab result, a letter or a visit recording. I’ll read it and file it there.",
                      "action": {"label": "Add a record", "href": f"/notebook/{quote(str(book['id']))}"}})
    return items[:2]


def _chat_offer():
    """Always last: a concrete question about the newest record, or an open invitation."""
    from stellis import documents
    records = sorted(documents.list_all(), key=lambda r: str(r.get("record_date") or r.get("created_at") or ""), reverse=True)
    latest = next((r for r in records if r.get("notebook_ids")), None)
    if latest:
        return {"kind": "chat", "category": "understand", "rank": 10, "icon": "message-2", "title": f"Ask me about {latest['title']}",
                "detail": "Plain answers linked to each source line",
                "action": {"label": "Ask", "href": _ask(latest["notebook_ids"][0], f"What should I know about my {latest['title']}?")}}
    return {"kind": "chat", "category": "understand", "rank": 10, "icon": "message-2", "title": "Ask me anything about your health",
            "detail": "Questions, plans or what to bring to a visit.", "action": {"label": "Ask", "href": "/chat"}}


def _number(value):
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def latest_results(limit: int = RESULT_LIMIT) -> list[dict]:
    """The newest accepted result for each charted test, results outside the lab's range first.

    A value read from a report with no range and no standard measurement (a dose, say) is left out,
    so every tile is a test result with its own record behind it.
    """
    from stellis import health_facts
    series = {}
    for fact in health_facts.accepted():  # Newest date first.
        value = _number(fact["value"])
        known = fact.get("measurement") and not str(fact["measurement"]).startswith("report_")
        if fact["kind"] not in ("lab_result", "vital_sign") or value is None or not (known or fact.get("range_status")):
            continue
        series.setdefault(fact.get("measurement") or str(fact["name"]).casefold(), []).append((fact, value))
    tiles = []
    for key, points in series.items():
        fact, value = points[0]
        previous = points[1] if len(points) > 1 else None
        status = fact.get("range_status") or {}
        tiles.append({"key": key, "name": fact["name"], "value": fact["value"], "unit": fact["unit"] or "",
                      "date": fact["date"], "status": status.get("label"), "tone": status.get("tone"),
                      "previous": {"value": previous[0]["value"], "date": previous[0]["date"]} if previous else None,
                      "points": [{"date": f["date"], "value": v} for f, v in reversed(points)],
                      "href": "/trends?measure=" + quote(key),
                      "source": {"title": fact["document_title"], "href": fact["target"]}})
    tiles.sort(key=lambda tile: tile["name"].casefold())
    tiles.sort(key=lambda tile: tile["date"], reverse=True)
    tiles.sort(key=lambda tile: tile["tone"] != "alert")
    return tiles[:limit]


def has_history() -> bool:
    """True once the account holds a record or a logged reading."""
    from stellis import documents, readings
    return bool(documents.list_all() or readings.stored_points())


def starting_ideas(in_example: bool = False) -> list[dict]:
    """First steps for an account with no records yet, each chosen from what is still missing.

    An idea with a ``command`` runs in the page (open the upload dialog, the camera, the example
    records); the rest are links. The example offer is left out inside the example workspace.
    """
    from stellis import notebooks
    ideas = [{"kind": "start", "category": "understand", "icon": "upload", "title": "Add a lab report, letter or prescription",
              "detail": "I’ll read each result, file it in a notebook and show the line it came from.",
              "action": {"label": "Add a record", "command": "upload"}}]
    books = [book for book in notebooks.list_notebooks() if not book.get("archived_at")]
    if not books:
        ideas.append({"kind": "start", "category": "improve", "icon": "notebook", "title": "Tell me what you want to keep track of",
                      "detail": "A short call, and I’ll set up notebooks for your conditions, care and goals.",
                      "action": {"label": "Start setup", "href": "/onboarding"}})
    ideas.append({"kind": "start", "category": "monitor", "icon": "camera", "title": "Take your pulse with the camera",
                  "detail": "About 30 seconds. The video stays on this device.",
                  "action": {"label": "Open camera", "command": "camera"}})
    if not in_example:
        ideas.append({"kind": "start", "category": "understand", "icon": "sparkles", "title": "See how I work with example records",
                      "detail": "A fictional patient's five years of results, kept apart from yours and removed in one click.",
                      "action": {"label": "Try example records", "command": "example"}})
    return ideas


def items(today: datetime.date | None = None) -> list[dict]:
    """The ranked list, highest first, always ending with an offer to chat."""
    today = today or datetime.date.today()
    found = []
    for gather in (lambda: _upcoming(today), _to_confirm, _flagged_results, _todos, _empty_notebooks):
        try:
            found += gather()
        except Exception:  # One unavailable source never blanks the rest of Home.
            continue
    found.sort(key=lambda item: -item["rank"])
    return found[:LIMIT - 1] + [_chat_offer()]
