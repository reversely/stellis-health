"""Reviewable agent suggestions that add or close to-dos (ADR 0059).

An agent never edits a document. It proposes a change that the user can amend, preview,
accept or reject. Accepting writes the change as an ordinary save of the target document.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, time, timezone

from stellis import storage, todos
from stellis.notes import MAX_MARKDOWN

COLLECTION_TODO_LIST = ("on", "off")
NOTEBOOK_TODO_LIST = ("inherit", "on", "off")
OPERATIONS = ("add_todo", "set_todo_status")
HEADING = "## To-dos"
_TOP_HEADING = re.compile(r"#{1,2} ")


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_todo_list(own: str, inherited: str | None) -> bool:
    """A notebook's own on or off wins; inherit follows its collection; no collection counts as off."""
    if own in ("on", "off"):
        return own == "on"
    return inherited == "on"


def _todo_enabled(conn, notebook_id: str | None) -> bool:
    if notebook_id is None:
        return False
    row = conn.execute("""SELECT n.todo_list, c.todo_list AS inherited FROM notebooks n
        LEFT JOIN collections c ON c.id = n.collection_id WHERE n.id = ?""", (notebook_id,)).fetchone()
    return row is not None and resolve_todo_list(str(row["todo_list"]), row["inherited"])


def todo_enabled(notebook_id: str | None) -> bool:
    with storage.connect() as conn:
        return _todo_enabled(conn, notebook_id)


# Text that would change the line's meaning once written as `- [ ] text` (ADR 0058, ADR 0059).
_TRAILING_ID = re.compile(r"(?:^|\s)\^t[0-9a-z]{6}$")
_LEADING_CHECKBOX = re.compile(r"^(?:[-*+]\s+)?\[[ xX-]\](?:\s|$)")
_DIALECT_TAG = re.compile(r"</?(?:details|summary|bookmark|columns?)\b[^>]*>", re.IGNORECASE)
_BLOCK_PREFIX = re.compile(r"^(?:#{1,6}\s|>|[-*+]\s|\d+[.)]\s|\||```|~~~|---|\*\*\*|___)")


def _check_todo_text(text: str) -> None:
    if _TRAILING_ID.search(text):
        raise ValueError("To-do text cannot end with a task ID")
    if _LEADING_CHECKBOX.match(text):
        raise ValueError("To-do text cannot start with a checkbox")
    if _DIALECT_TAG.search(text):
        raise ValueError("To-do text cannot contain page layout tags")
    if _BLOCK_PREFIX.match(text):
        raise ValueError("To-do text cannot start with Markdown block syntax")


def validate_values(operation: str, values: object) -> dict[str, str]:
    """Return the normalized values for an operation, or raise ValueError."""
    if operation not in OPERATIONS:
        raise ValueError("Invalid suggestion operation")
    if not isinstance(values, dict):
        raise ValueError("Values must be an object")
    allowed = {"text", "due_date", "due_time"} if operation == "add_todo" else {"status"}
    if set(values) - allowed:
        raise ValueError("Unknown suggestion value")
    if operation == "set_todo_status":
        if values.get("status") not in todos.MARKERS:
            raise ValueError("Status must be open, done, or canceled")
        return {"status": values["status"]}
    text = values.get("text")
    text = " ".join(text.split()) if isinstance(text, str) else ""
    if not 1 <= len(text) <= 500:
        raise ValueError("To-do text must be 1 to 500 characters")
    _check_todo_text(text)
    result = {"text": text}
    due_date, due_time = values.get("due_date"), values.get("due_time")
    if due_date is not None:
        if not isinstance(due_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date):
            raise ValueError("Due date must be YYYY-MM-DD")
        date.fromisoformat(due_date)
        result["due_date"] = due_date
    if due_time is not None:
        if due_date is None:
            raise ValueError("A due time needs a due date")
        if not isinstance(due_time, str) or not re.fullmatch(r"\d{2}:\d{2}", due_time):
            raise ValueError("Due time must be HH:MM")
        time.fromisoformat(due_time)
        result["due_time"] = due_time
    return result


def _evidence(evidence: object) -> list[dict[str, str]]:
    if evidence is None:
        return []
    if not isinstance(evidence, list) or not all(
            isinstance(item, dict) and set(item) == {"segment_id", "quote"}
            and all(isinstance(value, str) for value in item.values()) for item in evidence):
        raise ValueError("Evidence must be a list of {segment_id, quote}")
    return evidence


def _document(conn, kind: str, target_id: str) -> dict[str, object]:
    """Read an active target's Markdown, revision and notebook; a conversation without notes is empty."""
    if kind == "conversation":
        owner = conn.execute("SELECT notebook_id FROM conversations WHERE id = ? AND trashed_at IS NULL",
                             (target_id,)).fetchone()
        if owner is None:
            raise LookupError("Conversation not found")
        row = conn.execute("SELECT markdown, revision FROM meeting_notes WHERE conversation_id = ?",
                           (target_id,)).fetchone()
    elif kind == "page":
        owner = row = conn.execute("SELECT notebook_id, markdown, revision FROM pages WHERE id = ? AND trashed_at IS NULL",
                                   (target_id,)).fetchone()
        if owner is None:
            raise LookupError("Page not found")
    else:
        raise ValueError("Invalid suggestion target")
    return {"markdown": str(row["markdown"]) if row else "", "revision": int(row["revision"]) if row else 0,
            "notebook_id": owner["notebook_id"]}


def public(row) -> dict[str, object]:
    def load(column: str):
        return json.loads(row[column]) if row[column] is not None else None
    return {"id": row["id"], "run_id": row["run_id"], "author": row["author"], "target_kind": row["target_kind"],
            "target_id": row["target_id"], "operation": row["operation"], "todo_id": row["todo_id"],
            "values": load("values_json"), "original_values": load("original_values_json"),
            "previous": load("previous_json"), "source_kind": row["source_kind"], "source_id": row["source_id"],
            "evidence": load("evidence_json"), "state": row["state"], "created_at": row["created_at"],
            "amended_at": row["amended_at"], "decided_at": row["decided_at"]}


def propose(author: str, target_kind: str, target_id: str, operation: str, values: dict, *,
            todo_id: str | None = None, run_id: str | None = None, source_kind: str | None = None,
            source_id: str | None = None, evidence: list | None = None) -> dict[str, object]:
    """Record a pending suggestion. An add_todo needs the target's notebook to have the to-do list on."""
    if not isinstance(author, str) or not 1 <= len(author) <= 80:
        raise ValueError("Author must be 1 to 80 characters")
    if target_kind not in todos.SOURCE_KINDS:
        raise ValueError("Invalid suggestion target")
    if (source_kind is None) != (source_id is None) or (source_kind is not None and source_kind not in todos.SOURCE_KINDS):
        raise ValueError("Invalid suggestion source")
    clean = validate_values(operation, values)
    cited = _evidence(evidence)
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        document = _document(conn, target_kind, target_id)
        previous = None
        if operation == "add_todo":
            if todo_id is not None:
                raise ValueError("An add_todo suggestion has no to-do ID")
            if not _todo_enabled(conn, document["notebook_id"]):
                raise PermissionError("The to-do list is off for this notebook")
        else:
            item = conn.execute("SELECT status FROM todo_items WHERE id = ? AND source_kind = ? AND source_id = ?",
                                (todo_id, target_kind, target_id)).fetchone()
            if item is None:
                raise LookupError("To-do not found in this target")
            previous = {"status": item["status"]}
        suggestion_id = str(uuid.uuid4())
        conn.execute("""INSERT INTO suggestions (id, run_id, author, target_kind, target_id, operation, todo_id,
            values_json, previous_json, source_kind, source_id, evidence_json, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                     (suggestion_id, run_id, author, target_kind, target_id, operation, todo_id, json.dumps(clean),
                      json.dumps(previous) if previous else None, source_kind, source_id, json.dumps(cited), _stamp()))
        return public(conn.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone())


def supersede_pending(author: str, source_kind: str, source_id: str) -> int:
    """Retire an author's pending suggestions from one source so a re-run replaces them."""
    with storage.connect() as conn:
        return conn.execute("UPDATE suggestions SET state = 'superseded', decided_at = ? WHERE state = 'pending' "
                            "AND author = ? AND source_kind = ? AND source_id = ?",
                            (_stamp(), author, source_kind, source_id)).rowcount


def amend(suggestion_id: str, values: dict) -> dict[str, object]:
    """Replace a pending suggestion's values. The first amendment keeps the agent's version."""
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if row is None:
            raise LookupError("Suggestion not found")
        if row["state"] != "pending":
            raise RuntimeError("Suggestion is no longer pending")
        clean = validate_values(str(row["operation"]), values)
        original = row["original_values_json"] or row["values_json"]
        conn.execute("UPDATE suggestions SET values_json = ?, original_values_json = ?, amended_at = ? WHERE id = ?",
                     (json.dumps(clean), original, _stamp(), suggestion_id))
        return public(conn.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone())


def _load(conn, ids: list[str]) -> list:
    """Rows for the IDs in creation order; an unknown ID raises before anything changes."""
    unique = list(dict.fromkeys(ids))
    marks = ",".join("?" for _ in unique)
    rows = conn.execute(f"SELECT * FROM suggestions WHERE id IN ({marks}) ORDER BY created_at, rowid", unique).fetchall()
    if len(rows) != len(unique):
        raise LookupError("Suggestion not found")
    return rows


def _by_target(rows: list) -> dict[tuple[str, str], list[str]]:
    groups: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        if row["state"] == "pending":
            groups.setdefault((str(row["target_kind"]), str(row["target_id"])), []).append(str(row["id"]))
    return groups


def _insert_todo(lines: list[str], item: str) -> list[str]:
    """Put an item at the end of the `## To-dos` section, adding the section at the end if missing."""
    fenced = todos._fence_flags(lines)
    start = next((i for i, line in enumerate(lines) if not fenced[i] and line.rstrip() == HEADING), None)
    if start is None:
        body, ended = list(lines), False
        while body and not body[-1].strip():
            body.pop()
            ended = True
        return body + (["", HEADING, "", item] if body else [HEADING, "", item]) + ([""] if ended else [])
    end = next((i for i in range(start + 1, len(lines)) if not fenced[i] and _TOP_HEADING.match(lines[i])), len(lines))
    last = next((i for i in range(end - 1, start, -1) if lines[i].strip()), start)
    if last == start:
        # An empty section: keep one blank line between the heading and the item.
        blank_after = start + 1 < len(lines) and not lines[start + 1].strip()
        at = start + 2 if blank_after else start + 1
        return lines[:at] + ([] if blank_after else [""]) + [item] + lines[at:]
    return lines[:last + 1] + [item] + lines[last + 1:]


def _apply(conn, kind: str, target_id: str, ids: list[str]) -> tuple[str | None, list[str], list[str]]:
    """Apply a target's pending suggestions in order inside the caller's transaction.

    Returns the document as it now stands (None if the target is gone) and the accepted and superseded IDs.
    """
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(f"SELECT * FROM suggestions WHERE id IN ({marks}) AND state = 'pending' "
                        "ORDER BY created_at, rowid", ids).fetchall()
    stamp = _stamp()
    try:
        document = _document(conn, kind, target_id)
    except LookupError:
        conn.execute(f"UPDATE suggestions SET state = 'superseded', decided_at = ? WHERE id IN ({marks}) AND state = 'pending'",
                     [stamp, *ids])
        return None, [], [str(row["id"]) for row in rows]
    lines = str(document["markdown"]).split("\n")
    taken = {str(row["id"]) for row in conn.execute("SELECT id FROM todo_items")}
    dates: dict[str, dict] = {}
    new_ids: dict[str, str] = {}
    accepted: list[str] = []
    superseded: list[str] = []
    for row in rows:
        values = json.loads(row["values_json"])
        if row["operation"] == "add_todo":
            new_id = todos._new_id(taken)
            taken.add(new_id)
            lines = _insert_todo(lines, f"- [ ] {values['text']} ^{new_id}")
            new_ids[str(row["id"])] = new_id
            if "due_date" in values:
                dates[new_id] = {"due_date": values["due_date"], "due_time": values.get("due_time")}
        else:
            task = next((task for task in todos.parse_tasks("\n".join(lines)) if task.id == row["todo_id"]), None)
            if task is None or task.status != json.loads(row["previous_json"])["status"]:
                superseded.append(str(row["id"]))
                continue
            lines[task.line - 1] = todos.set_marker(lines[task.line - 1], values["status"])
        accepted.append(str(row["id"]))
    markdown = "\n".join(lines)
    if accepted:
        if len(markdown.encode("utf-8")) > MAX_MARKDOWN:
            raise ValueError("Accepting these suggestions makes the document too large")
        markdown = todos.reindex(conn, kind, target_id, markdown, dates)
        if kind == "page":
            conn.execute("UPDATE pages SET markdown = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                         (markdown, stamp, target_id))
        elif conn.execute("SELECT 1 FROM meeting_notes WHERE conversation_id = ?", (target_id,)).fetchone():
            conn.execute("UPDATE meeting_notes SET markdown = ?, revision = revision + 1, updated_at = ? "
                         "WHERE conversation_id = ?", (markdown, stamp, target_id))
        else:
            conn.execute("INSERT INTO meeting_notes (id, conversation_id, markdown, revision, updated_at) VALUES (?, ?, ?, 1, ?)",
                         (str(uuid.uuid4()), target_id, markdown, stamp))
    # Set after the reindex, which closes other pending suggestions on a line whose status just changed.
    for suggestion_id in accepted:
        conn.execute("UPDATE suggestions SET state = 'accepted', decided_at = ?, todo_id = COALESCE(?, todo_id) WHERE id = ?",
                     (stamp, new_ids.get(suggestion_id), suggestion_id))
    for suggestion_id in superseded:
        conn.execute("UPDATE suggestions SET state = 'superseded', decided_at = ? WHERE id = ?", (stamp, suggestion_id))
    return markdown, accepted, superseded


def accept(ids: list[str]) -> dict[str, list[str]]:
    """Accept pending suggestions in creation order, one transaction per target. Other states are skipped."""
    accepted: list[str] = []
    superseded: list[str] = []
    with storage.connect() as conn:
        for (kind, target_id), group in _by_target(_load(conn, ids)).items():
            conn.execute("BEGIN IMMEDIATE")
            try:
                _, done, stale = _apply(conn, kind, target_id, group)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            accepted += done
            superseded += stale
    return {"accepted": accepted, "superseded": superseded}


def reject(ids: list[str]) -> dict[str, list[str]]:
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        pending = [str(row["id"]) for row in _load(conn, ids) if row["state"] == "pending"]
        for suggestion_id in pending:
            conn.execute("UPDATE suggestions SET state = 'rejected', decided_at = ? WHERE id = ?", (_stamp(), suggestion_id))
    return {"rejected": pending}


def preview(ids: list[str], decision: str) -> dict[str, list[dict[str, str]]]:
    """Each affected document as it would be after the decision. Nothing is saved."""
    if decision not in ("accept", "reject"):
        raise ValueError("Decision must be accept or reject")
    targets = []
    with storage.connect() as conn:
        for (kind, target_id), group in _by_target(_load(conn, ids)).items():
            conn.execute("BEGIN IMMEDIATE")
            try:
                if decision == "accept":
                    markdown = _apply(conn, kind, target_id, group)[0]
                else:
                    try:
                        markdown = str(_document(conn, kind, target_id)["markdown"])
                    except LookupError:
                        markdown = None
            finally:
                conn.rollback()
            if markdown is not None:
                targets.append({"target_kind": kind, "target_id": target_id, "markdown": markdown})
    return {"targets": targets}


def list_pending(target_kind: str | None = None, target_id: str | None = None,
                 notebook_id: str | None = None) -> list[dict[str, object]]:
    """Pending suggestions for one target, or for every active conversation and page in a notebook."""
    with storage.connect() as conn:
        if notebook_id is not None:
            if conn.execute("SELECT 1 FROM notebooks WHERE id = ?", (notebook_id,)).fetchone() is None:
                raise LookupError("Notebook not found")
            rows = conn.execute("""SELECT * FROM suggestions WHERE state = 'pending' AND (
                (target_kind = 'conversation' AND target_id IN (SELECT id FROM conversations WHERE notebook_id = ? AND trashed_at IS NULL))
                OR (target_kind = 'page' AND target_id IN (SELECT id FROM pages WHERE notebook_id = ? AND trashed_at IS NULL)))
                ORDER BY created_at, rowid""", (notebook_id, notebook_id)).fetchall()
        elif target_kind in todos.SOURCE_KINDS and target_id:
            rows = conn.execute("SELECT * FROM suggestions WHERE state = 'pending' AND target_kind = ? AND target_id = ? "
                                "ORDER BY created_at, rowid", (target_kind, target_id)).fetchall()
        else:
            raise ValueError("Give a target_kind and target_id, or a notebook_id")
    return [public(row) for row in rows]


def delete_source(conn, kind: str, item_id: str) -> None:
    """Remove suggestions that target or cite a permanently deleted conversation or page."""
    conn.execute("DELETE FROM suggestions WHERE (target_kind = ? AND target_id = ?) OR (source_kind = ? AND source_id = ?)",
                 (kind, item_id, kind, item_id))
