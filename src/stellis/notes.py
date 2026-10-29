"""Revisioned Markdown notes carried from capture to a saved conversation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from stellis import storage, todos

MAX_MARKDOWN = todos.MAX_MARKDOWN


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(row) -> dict[str, object]:
    if row is None:
        return {"markdown": "", "revision": 0, "updated_at": None}
    return {"markdown": str(row["markdown"]), "revision": int(row["revision"]),
            "updated_at": str(row["updated_at"])}


def get(scope: str, item_id: str) -> dict[str, object]:
    """Read a note by its active recording or conversation owner."""
    if scope not in {"conversation", "recording"}:
        raise ValueError("Invalid note scope")
    with storage.connect() as conn:
        if scope == "conversation":
            owner = conn.execute("SELECT id FROM conversations WHERE id = ? AND trashed_at IS NULL",
                                 (item_id,)).fetchone()
            field = "conversation_id"
        else:
            owner = conn.execute("SELECT id FROM recording_sessions WHERE id = ? AND state = 'recording'",
                                 (item_id,)).fetchone()
            field = "recording_id"
        if owner is None:
            raise LookupError("Recording or conversation not found")
        row = conn.execute(f"SELECT * FROM meeting_notes WHERE {field} = ?", (item_id,)).fetchone()
        dates = todos.due_dates(conn, "conversation", item_id) if scope == "conversation" else {}
    return _public(row) | {"todo_dates": dates}


def save(scope: str, item_id: str, markdown: str, expected_revision: int,
         todo_dates: dict | None = None) -> dict[str, object]:
    """Save a whole document with optimistic revision protection."""
    if not isinstance(markdown, str) or len(markdown.encode("utf-8")) > MAX_MARKDOWN:
        raise ValueError("Notes are too large")
    if expected_revision < 0:
        raise ValueError("Invalid note revision")
    if scope not in {"conversation", "recording"}:
        raise ValueError("Invalid note scope")
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if scope == "conversation":
            owner = conn.execute("SELECT id FROM conversations WHERE id = ? AND trashed_at IS NULL",
                                 (item_id,)).fetchone()
            field = "conversation_id"
        else:
            owner = conn.execute("SELECT id FROM recording_sessions WHERE id = ? AND state = 'recording'",
                                 (item_id,)).fetchone()
            field = "recording_id"
        if owner is None:
            raise LookupError("Recording or conversation not found")
        row = conn.execute(f"SELECT * FROM meeting_notes WHERE {field} = ?", (item_id,)).fetchone()
        current_revision = int(row["revision"]) if row else 0
        if current_revision != expected_revision:
            raise RuntimeError("Notes changed in another tab")
        stamp = _stamp()
        if scope == "conversation":
            markdown = todos.reindex(conn, "conversation", item_id, markdown, todo_dates)
        if row:
            conn.execute("UPDATE meeting_notes SET markdown = ?, revision = ?, updated_at = ? WHERE id = ?",
                         (markdown, current_revision + 1, stamp, row["id"]))
        else:
            conn.execute(f"INSERT INTO meeting_notes (id, {field}, markdown, revision, updated_at) VALUES (?, ?, ?, 1, ?)",
                         (str(uuid.uuid4()), item_id, markdown, stamp))
        updated = conn.execute(f"SELECT * FROM meeting_notes WHERE {field} = ?", (item_id,)).fetchone()
        dates = todos.due_dates(conn, "conversation", item_id) if scope == "conversation" else {}
    return _public(updated) | {"todo_dates": dates}


def attach_to_conversation(recording_id: str, conversation_id: str) -> None:
    """Keep capture notes when saved audio becomes a conversation, including deduplicated audio."""
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        captured = conn.execute("SELECT * FROM meeting_notes WHERE recording_id = ?", (recording_id,)).fetchone()
        if captured is None:
            return
        existing = conn.execute("SELECT * FROM meeting_notes WHERE conversation_id = ?", (conversation_id,)).fetchone()
        if existing is None:
            indexed = todos.reindex(conn, "conversation", conversation_id, str(captured["markdown"]), enforce_limit=False)
            conn.execute("UPDATE meeting_notes SET recording_id = NULL, conversation_id = ?, markdown = ?, updated_at = ? WHERE id = ?",
                         (conversation_id, indexed, _stamp(), captured["id"]))
            return
        captured_text = str(captured["markdown"]).strip()
        if captured_text:
            existing_text = str(existing["markdown"]).strip()
            combined = f"{existing_text}\n\n---\n\n{captured_text}" if existing_text else captured_text
            if len(combined.encode("utf-8")) > MAX_MARKDOWN:
                raise ValueError("Combined notes exceed the size limit")
            combined = todos.reindex(conn, "conversation", conversation_id, combined)
            conn.execute("UPDATE meeting_notes SET markdown = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                         (combined, _stamp(), existing["id"]))
        conn.execute("DELETE FROM meeting_notes WHERE id = ?", (captured["id"],))


EVENT_PROVIDERS = ("google", "microsoft")


def _event_key(provider: str, event_id: str) -> tuple[str, str]:
    if provider not in EVENT_PROVIDERS or not 1 <= len(event_id) <= 1024:
        raise ValueError("Invalid calendar event")
    return provider, event_id


def get_event(provider: str, event_id: str) -> dict[str, object]:
    """Read the notes written ahead of a calendar meeting; an untouched meeting has empty notes."""
    key = _event_key(provider, event_id)
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM event_notes WHERE provider = ? AND event_id = ?", key).fetchone()
    return _public(row)


def save_event(provider: str, event_id: str, markdown: str, expected_revision: int) -> dict[str, object]:
    key = _event_key(provider, event_id)
    if not isinstance(markdown, str) or len(markdown.encode("utf-8")) > MAX_MARKDOWN:
        raise ValueError("Notes are too large")
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM event_notes WHERE provider = ? AND event_id = ?", key).fetchone()
        current_revision = int(row["revision"]) if row else 0
        if current_revision != expected_revision:
            raise RuntimeError("Notes changed in another tab")
        conn.execute("""INSERT INTO event_notes (provider, event_id, markdown, revision, updated_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(provider, event_id) DO UPDATE SET markdown = excluded.markdown, revision = excluded.revision,
            updated_at = excluded.updated_at""", (*key, markdown, current_revision + 1, _stamp()))
        updated = conn.execute("SELECT * FROM event_notes WHERE provider = ? AND event_id = ?", key).fetchone()
    return _public(updated)


def events_with_notes() -> set[tuple[str, str]]:
    with storage.connect() as conn:
        rows = conn.execute("SELECT provider, event_id FROM event_notes WHERE TRIM(markdown) != ''").fetchall()
    return {(str(row["provider"]), str(row["event_id"])) for row in rows}


def move_event_to_recording(conn, provider: str, event_id: str, recording_id: str) -> None:
    """Start a recording's My Notes from its meeting's notes, inside the caller's transaction."""
    key = _event_key(provider, event_id)
    row = conn.execute("SELECT markdown FROM event_notes WHERE provider = ? AND event_id = ?", key).fetchone()
    if row is None:
        return
    if str(row["markdown"]).strip():
        conn.execute("INSERT INTO meeting_notes (id, recording_id, markdown, revision, updated_at) VALUES (?, ?, ?, 1, ?)",
                     (str(uuid.uuid4()), recording_id, row["markdown"], _stamp()))
    conn.execute("DELETE FROM event_notes WHERE provider = ? AND event_id = ?", key)
