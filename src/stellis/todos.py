"""Markdown task lines indexed for gathering, dating and toggling (ADR 0059)."""

from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone

from stellis import storage

# The ID needs a space before it, which may be the one after the checkbox, so an empty task keeps text "".
# Blockquote and callout markers (`>`, nested `> >`) may precede the list marker; they stay in group 1.
TASK_LINE = re.compile(r"^((?:\s*>)*\s*)[-*+] \[( |x|X|-)\] (.*?)\s*(?:(?<=\s)\^(t[0-9a-z]{6}))?\s*$")
MAX_MARKDOWN = 200_000


class TooLarge(ValueError):
    """A document is over the size limit."""

STATUSES = {" ": "open", "x": "done", "X": "done", "-": "canceled"}
MARKERS = {"open": " ", "done": "x", "canceled": "-"}
SOURCE_KINDS = ("conversation", "page")
_ALPHABET = string.digits + string.ascii_lowercase
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ID_SUFFIX = re.compile(r"[ ]\^t[0-9a-z]{6}(?=[ \t]*\r?$)", re.M)
_BOOKMARK = re.compile(r'<bookmark\s+url="([^"]*)"\s*>(.*?)</bookmark>', re.DOTALL)
_COLUMN_TAG = re.compile(r"</?column(?:s)?(?:\s[^>]*)?>")


@dataclass(frozen=True)
class Task:
    line: int  # 1-based line number
    indent: str
    status: str
    text: str
    id: str | None


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fence_flags(lines: list[str]) -> list[bool]:
    """Mark each line that is a fence delimiter or sits inside a fenced code block."""
    flags, opener = [], None
    for line in lines:
        match = _FENCE.match(line)
        if opener is None:
            flags.append(bool(match))
            if match:
                opener = match.group(1)
        else:
            flags.append(True)
            if match and match.group(1)[0] == opener[0] and len(match.group(1)) >= len(opener) \
                    and not line.strip().strip(opener[0]):
                opener = None
    return flags


def parse_tasks(markdown: str) -> list[Task]:
    """Return every task line outside fenced code blocks."""
    lines = markdown.split("\n")
    tasks = []
    for number, (line, fenced) in enumerate(zip(lines, _fence_flags(lines)), start=1):
        match = None if fenced else TASK_LINE.match(line)
        if match and match.group(3):  # an empty task line is not indexed and gets no ID
            tasks.append(Task(number, match.group(1), STATUSES[match.group(2)], match.group(3), match.group(4)))
    return tasks


def normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _new_id(taken: set[str]) -> str:
    while True:
        candidate = "t" + "".join(secrets.choice(_ALPHABET) for _ in range(6))
        if candidate not in taken:
            return candidate


def _transition(status: str, previous, stamp: str) -> dict[str, str | None]:
    """Completion and cancel times: set on first entry to the status, cleared on leaving it."""
    def keep(column: str, wanted: bool) -> str | None:
        if not wanted:
            return None
        return (previous[column] if previous is not None else None) or stamp
    return {"completed_at": keep("completed_at", status == "done"),
            "canceled_at": keep("canceled_at", status == "canceled")}


def _supersede(conn, item_id: str, stamp: str, *, status_only: bool = False) -> None:
    """A user's own edit wins: close pending suggestions on a line whose status changed or that is gone."""
    conn.execute("UPDATE suggestions SET state = 'superseded', decided_at = ? WHERE todo_id = ? AND state = 'pending'"
                 + (" AND operation = 'set_todo_status'" if status_only else ""), (stamp, item_id))


def reindex(conn, source_kind: str, source_id: str, markdown: str, todo_dates: dict | None = None,
            enforce_limit: bool = True) -> str:
    """Sync a source's items with its task lines inside the caller's transaction.

    Returns the Markdown with an ID on every task line; the caller stores that version.
    """
    if source_kind not in SOURCE_KINDS:
        raise ValueError("Invalid to-do source")
    existing = {str(row["id"]): row for row in conn.execute(
        "SELECT * FROM todo_items WHERE source_kind = ? AND source_id = ?", (source_kind, source_id))}
    tasks = parse_tasks(markdown)
    # An ID is kept only when this source owns it, or nobody does, and its first use in the document wins.
    foreign = {str(row["id"]) for row in conn.execute(
        "SELECT id FROM todo_items WHERE NOT (source_kind = ? AND source_id = ?)", (source_kind, source_id))}
    assigned: dict[int, str] = {}
    used: set[str] = set()
    for task in tasks:
        if task.id and task.id not in used and task.id not in foreign:
            assigned[task.line] = task.id
            used.add(task.id)
    # An editor that sends lines without IDs gets the IDs back by identical text.
    free: dict[str, list[str]] = {}
    for item_id, row in existing.items():
        if item_id not in used:
            free.setdefault(normalize(str(row["text"])), []).append(item_id)
    taken = used | foreign | set(existing)
    for task in tasks:
        if task.line in assigned:
            continue
        reusable = free.get(normalize(task.text))
        if reusable:
            item_id = reusable.pop(0)
        else:
            item_id = _new_id(taken)
        taken.add(item_id)
        used.add(item_id)
        assigned[task.line] = item_id

    lines = markdown.split("\n")
    stamp = _stamp()
    for position, task in enumerate(tasks):
        item_id = assigned[task.line]
        if task.id != item_id:
            body = lines[task.line - 1].rstrip()
            if task.id:
                body = body[:-len(task.id) - 2]
            carriage = "\r" if lines[task.line - 1].endswith("\r") else ""
            lines[task.line - 1] = body + f" ^{item_id}" + carriage
        previous = existing.get(item_id)
        dates = _transition(task.status, previous, stamp)
        if previous is None:
            conn.execute("""INSERT INTO todo_items (id, source_kind, source_id, text, status, position,
                created_at, updated_at, completed_at, canceled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (item_id, source_kind, source_id, task.text, task.status, position, stamp, stamp,
                          dates["completed_at"], dates["canceled_at"]))
        elif (previous["text"], previous["status"], previous["position"]) != (task.text, task.status, position):
            if previous["status"] != task.status:
                _supersede(conn, item_id, stamp, status_only=True)
            conn.execute("""UPDATE todo_items SET text = ?, status = ?, position = ?, updated_at = ?,
                completed_at = ?, canceled_at = ? WHERE id = ?""",
                         (task.text, task.status, position, stamp, dates["completed_at"], dates["canceled_at"], item_id))
    for item_id in set(existing) - used:
        _supersede(conn, item_id, stamp)
        conn.execute("DELETE FROM todo_items WHERE id = ?", (item_id,))
    for item_id, due in (todo_dates or {}).items():
        if item_id not in used:
            continue
        date, time = (due.get("due_date"), due.get("due_time")) if due else (None, None)
        conn.execute("UPDATE todo_items SET due_date = ?, due_time = ?, updated_at = ? WHERE id = ?",
                     (date, time if date else None, stamp, item_id))
    markdown = "\n".join(lines)
    # Checked after IDs are added, so the stored version is what stays under the limit; the caller's transaction rolls back.
    # Moving already-accepted capture notes must not fail a recording's finalize over added IDs.
    if enforce_limit and len(markdown.encode("utf-8")) > MAX_MARKDOWN:
        raise TooLarge("Notes are too large")
    return markdown


def due_dates(conn, source_kind: str, source_id: str) -> dict[str, dict[str, str | None]]:
    """Due dates by task ID, so the editor can show them again after a reload."""
    return {str(row["id"]): {"due_date": row["due_date"], "due_time": row["due_time"]} for row in conn.execute(
        "SELECT id, due_date, due_time FROM todo_items WHERE source_kind = ? AND source_id = ? AND due_date IS NOT NULL",
        (source_kind, source_id))}


def delete_source(conn, source_kind: str, source_id: str) -> None:
    conn.execute("DELETE FROM todo_items WHERE source_kind = ? AND source_id = ?", (source_kind, source_id))


ITEM_FIELDS = ("id", "text", "status", "due_date", "due_time", "created_at", "completed_at", "canceled_at")


def gather(notebook_id: str = "all", status: str = "open") -> dict[str, object]:
    """Group matching items by active source, newest source first and items in line order."""
    if status not in MARKERS:
        raise ValueError("Invalid status")
    scope, args = "", []
    if notebook_id == "none":
        scope = " AND s.notebook_id IS NULL"
    elif notebook_id != "all":
        scope, args = " AND s.notebook_id = ?", [notebook_id]
    sources = (("conversation", "conversations", "COALESCE(s.recorded_at, s.created_at)"),
               ("page", "pages", "s.created_at"))
    groups = []
    with storage.connect() as conn:
        pending: dict[str, list[str]] = {}
        for row in conn.execute("SELECT id, todo_id FROM suggestions WHERE state = 'pending' AND todo_id IS NOT NULL "
                                "ORDER BY created_at, rowid"):
            pending.setdefault(str(row["todo_id"]), []).append(str(row["id"]))
        for kind, table, newest in sources:
            for source in conn.execute(
                    f"""SELECT s.id, s.title, s.notebook_id, {newest} AS newest FROM {table} s
                    WHERE s.trashed_at IS NULL{scope} AND EXISTS (SELECT 1 FROM todo_items t
                    WHERE t.source_kind = ? AND t.source_id = s.id AND t.status = ?)""", [*args, kind, status]):
                items = conn.execute(
                    f"SELECT {', '.join(ITEM_FIELDS)} FROM todo_items WHERE source_kind = ? AND source_id = ? "
                    "AND status = ? ORDER BY position", (kind, source["id"], status)).fetchall()
                groups.append((str(source["newest"]), {
                    "source": {"kind": kind, "id": source["id"], "title": source["title"],
                               "notebook_id": source["notebook_id"]},
                    "items": [dict(item) | {"pending_suggestions": pending.get(str(item["id"]), [])} for item in items]}))
    groups.sort(key=lambda group: (group[0], group[1]["source"]["id"]), reverse=True)
    return {"groups": [group for _, group in groups]}


def set_marker(line: str, status: str) -> str:
    """Rewrite the checkbox marker of one task line."""
    return re.sub(r"^((?:\s*>)*\s*[-*+] \[)[ xX-](\])", lambda m: m.group(1) + MARKERS[status] + m.group(2), line, count=1)


def set_status(item_id: str, status: str) -> dict[str, object]:
    """Rewrite one line's marker in its source, bump the source revision, and reindex."""
    if status not in MARKERS:
        raise ValueError("Invalid status")
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item = conn.execute("SELECT * FROM todo_items WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            raise LookupError("To-do not found")
        kind, source_id = str(item["source_kind"]), str(item["source_id"])
        if kind == "conversation":
            row = conn.execute("""SELECT n.id, n.markdown, n.revision FROM meeting_notes n JOIN conversations c
                ON c.id = n.conversation_id WHERE n.conversation_id = ? AND c.trashed_at IS NULL""",
                               (source_id,)).fetchone()
            table = "meeting_notes"
        else:
            row = conn.execute("SELECT id, markdown, revision FROM pages WHERE id = ? AND trashed_at IS NULL",
                               (source_id,)).fetchone()
            table = "pages"
        if row is None:
            raise LookupError("To-do not found")
        lines = str(row["markdown"]).split("\n")
        task = next((task for task in parse_tasks("\n".join(lines)) if task.id == item_id), None)
        if task is None:
            raise LookupError("To-do not found")
        lines[task.line - 1] = set_marker(lines[task.line - 1], status)
        markdown = reindex(conn, kind, source_id, "\n".join(lines))
        conn.execute(f"UPDATE {table} SET markdown = ?, revision = ?, updated_at = ? WHERE id = ?",
                     (markdown, int(row["revision"]) + 1, _stamp(), row["id"]))
        updated = conn.execute(f"SELECT {', '.join(ITEM_FIELDS)} FROM todo_items WHERE id = ?", (item_id,)).fetchone()
    return dict(updated)


def plain_markdown(markdown: str) -> str:
    """Strip task IDs, turn bookmarks into links, and unwrap columns, outside code fences."""
    lines = markdown.split("\n")
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            out.extend(_plain_run("\n".join(run)).split("\n"))
            run.clear()

    for line, fenced in zip(lines, _fence_flags(lines)):
        if fenced:
            flush()
            out.append(line)
        else:
            run.append(line)
    flush()
    return "\n".join(out)


_ESCAPABLE = "\\`*_~[]()#>!|-<"
_ESCAPE = re.compile(r"\\([" + re.escape(_ESCAPABLE) + r"])")
# Private-use stand-ins keep an escaped marker from being read as emphasis until the end.
_LITERAL = {char: chr(0xE000 + index) for index, char in enumerate(_ESCAPABLE)}
_UNLITERAL = {mark: char for char, mark in _LITERAL.items()}
_IMAGE_OR_LINK = re.compile(r"!?\[([^\]\n]*)\]\([^)\n]*\)")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_EMPHASIS = (re.compile(r"\*\*(.+?)\*\*|__(.+?)__|~~(.+?)~~"),
             re.compile(r"\*(.+?)\*|(?<![0-9A-Za-z])_(.+?)_(?![0-9A-Za-z])"))


def markdown_inline_text(text: str) -> str:
    """Reduce one line of Markdown to its readable text.

    Drops a trailing task ID, emphasis and code marks, and link or image targets; keeps the link text or
    image caption; reads backslash escapes back as characters. Shared fixture: tests/fixtures/markdown_plain.json.
    """
    text = _ID_SUFFIX.sub("", text)
    text = _BOOKMARK.sub(lambda m: m.group(2).strip() or m.group(1), text)
    text = _ESCAPE.sub(lambda m: _LITERAL[m.group(1)], text)
    text = _CODE_SPAN.sub(lambda m: m.group(2).translate(str.maketrans(_LITERAL)), text)
    text = _IMAGE_OR_LINK.sub(lambda m: m.group(1), text)
    for pattern in _EMPHASIS:
        text = pattern.sub(lambda m: next(group for group in m.groups() if group is not None), text)
    return " ".join(text.translate(str.maketrans(_UNLITERAL)).split())


def _plain_run(text: str) -> str:
    text = _ID_SUFFIX.sub("", text)
    text = _BOOKMARK.sub(lambda m: f"[{m.group(2).strip()}]({m.group(1)})" if m.group(2).strip() else m.group(1), text)
    kept: list[str] = []
    between = False
    for line in text.split("\n"):
        tag = line.strip()
        if _COLUMN_TAG.fullmatch(tag):
            if tag.startswith("</column>"):
                between = True
            elif tag.startswith("<column") and not tag.startswith("<columns") and between and kept and kept[-1] != "":
                kept.append("")
                between = False
            continue
        between = False
        kept.append(_COLUMN_TAG.sub("", line))
    return "\n".join(kept)
