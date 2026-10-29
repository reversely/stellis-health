"""Categories and reversible local conversation removal."""

from __future__ import annotations

import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stellis import storage, suggestions, todos

_LOCK = threading.Lock()
RETENTION_DAYS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expires_at(trashed_at: str) -> str:
    return (datetime.fromisoformat(trashed_at) + timedelta(days=RETENTION_DAYS)).isoformat()


def categories() -> list[dict[str, object]]:
    with storage.connect() as conn:
        rows = conn.execute("""SELECT c.id, c.name,
                (SELECT COUNT(*) FROM conversation_categories cc JOIN conversations v ON v.id = cc.conversation_id
                    WHERE cc.category_id = c.id AND v.trashed_at IS NULL) AS conversation_count,
                (SELECT COUNT(*) FROM page_categories pc JOIN pages p ON p.id = pc.page_id
                    WHERE pc.category_id = c.id AND p.trashed_at IS NULL) AS page_count
            FROM categories c ORDER BY
                CASE c.normalized WHEN 'meetings' THEN 0 WHEN 'interviews' THEN 1
                    WHEN 'workshops' THEN 2 WHEN 'briefings' THEN 3 WHEN 'calls' THEN 4 ELSE 5 END,
                c.name COLLATE NOCASE""").fetchall()
    return [dict(row) for row in rows]


def assignments(conversation_id: str | None = None) -> dict[str, list[str]]:
    with storage.connect() as conn:
        rows = conn.execute("SELECT conversation_id, category_id FROM conversation_categories" +
                            (" WHERE conversation_id = ?" if conversation_id else ""),
                            (conversation_id,) if conversation_id else ()).fetchall()
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(str(row["conversation_id"]), []).append(str(row["category_id"]))
    return result


def create_category(name: str) -> dict[str, object]:
    value = name.strip()
    if not value or len(value) > 80:
        raise ValueError("Enter a category name of at most 80 characters")
    result = {"id": str(uuid.uuid4()), "name": value, "created_at": _now()}
    try:
        with storage.connect() as conn:
            conn.execute("INSERT INTO categories (id, name, normalized, created_at) VALUES (?, ?, ?, ?)",
                         (result["id"], value, value.casefold(), result["created_at"]))
    except sqlite3.IntegrityError as exc:
        raise ValueError("That category already exists") from exc
    return result


def rename_category(category_id: str, name: str) -> dict[str, object]:
    value = name.strip()
    if not value or len(value) > 80:
        raise ValueError("Enter a category name of at most 80 characters")
    try:
        with storage.connect() as conn:
            changed = conn.execute("UPDATE categories SET name = ?, normalized = ? WHERE id = ?",
                                   (value, value.casefold(), category_id)).rowcount
    except sqlite3.IntegrityError as exc:
        raise ValueError("That category already exists") from exc
    if not changed:
        raise LookupError("Category not found")
    return {"id": category_id, "name": value}


def delete_category(category_id: str) -> bool:
    with storage.connect() as conn:
        conn.execute("DELETE FROM conversation_categories WHERE category_id = ?", (category_id,))
        conn.execute("DELETE FROM page_categories WHERE category_id = ?", (category_id,))
        return bool(conn.execute("DELETE FROM categories WHERE id = ?", (category_id,)).rowcount)


def set_category(conversation_id: str, category_id: str, assigned: bool) -> list[str]:
    with storage.connect() as conn:
        if not conn.execute("SELECT 1 FROM conversations WHERE id = ? AND trashed_at IS NULL", (conversation_id,)).fetchone():
            raise LookupError("Conversation not found")
        if not conn.execute("SELECT 1 FROM categories WHERE id = ?", (category_id,)).fetchone():
            raise LookupError("Category not found")
        if assigned:
            conn.execute("INSERT OR IGNORE INTO conversation_categories VALUES (?, ?)",
                         (conversation_id, category_id))
        else:
            conn.execute("DELETE FROM conversation_categories WHERE conversation_id = ? AND category_id = ?",
                         (conversation_id, category_id))
        conn.execute("""INSERT INTO category_decisions (conversation_id, manual_override, state, updated_at)
            VALUES (?, 1, 'manual', ?) ON CONFLICT(conversation_id) DO UPDATE SET
            manual_override = 1, auto_category_id = NULL, state = 'manual', updated_at = excluded.updated_at""",
                     (conversation_id, _now()))
    return assignments(conversation_id).get(conversation_id, [])


def move_to_trash(conversation_id: str) -> dict[str, object]:
    with storage.connect() as conn:
        changed = conn.execute("UPDATE conversations SET trashed_at = ? WHERE id = ? AND trashed_at IS NULL",
                               (_now(), conversation_id)).rowcount
    if not changed:
        raise LookupError("Conversation not found")
    return {"id": conversation_id, "trashed": True}


def restore(conversation_id: str) -> dict[str, object]:
    with storage.connect() as conn:
        changed = conn.execute("UPDATE conversations SET trashed_at = NULL WHERE id = ? AND trashed_at IS NOT NULL",
                               (conversation_id,)).rowcount
    if not changed:
        raise LookupError("Conversation not found in Recently Deleted")
    return {"id": conversation_id, "trashed": False}


def recover_staged_media() -> None:
    """Finish or roll back media staging after an interrupted permanent delete."""
    media_dir = storage.data_dir() / "media"
    if not media_dir.exists():
        return
    for staged in media_dir.glob(".deleting-*"):
        parts = staged.name.split("-")
        if len(parts) < 7:
            continue
        try:
            conversation_id = str(uuid.UUID("-".join(parts[1:6])))
        except ValueError:
            # Filename format is .deleting-<UUID>-<random>; unknown files stay untouched.
            continue
        record = storage.get_conversation(conversation_id, include_trashed=True)
        if record is None:
            staged.unlink(missing_ok=True)
        else:
            filename = str(record["audio_filename"])
            if Path(filename).name != filename:
                continue
            destination = media_dir / filename
            if destination.exists():
                staged.unlink(missing_ok=True)
            else:
                staged.rename(destination)


def delete_permanently(conversation_id: str, *, expired_before: str | None = None) -> dict[str, object]:
    """Remove a trashed item only after its device and AI jobs have settled."""
    with _LOCK:
        with storage.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT audio_filename FROM conversations
                WHERE id = ? AND trashed_at IS NOT NULL AND (? IS NULL OR trashed_at <= ?)""",
                (conversation_id, expired_before, expired_before)).fetchone()
            if row is None:
                raise LookupError("Conversation not found in Recently Deleted")
            for table in ("processing_jobs", "ai_runs", "clean_runs"):
                if conn.execute(f"SELECT 1 FROM {table} WHERE conversation_id = ? AND state IN ('queued', 'running') LIMIT 1",
                                (conversation_id,)).fetchone():
                    raise RuntimeError("Wait for this conversation's processing to finish before permanent deletion")
            filename = str(row["audio_filename"])
            if Path(filename).name != filename:
                raise RuntimeError("Stored media path is invalid")
            media = storage.data_dir() / "media" / filename
            staged = media.with_name(f".deleting-{conversation_id}-{uuid.uuid4().hex}")
            if media.exists():
                media.rename(staged)
            else:
                staged = None
            try:
                sessions = [str(item["id"]) for item in conn.execute(
                    "SELECT id FROM recording_sessions WHERE conversation_id = ?", (conversation_id,))]
                job_ids = [str(item["id"]) for item in conn.execute(
                    "SELECT id FROM processing_jobs WHERE conversation_id = ?", (conversation_id,))]
                image_ids = [str(item["id"]) for item in conn.execute(
                    "SELECT id FROM conversation_images WHERE conversation_id = ?", (conversation_id,))]
                conn.execute("DELETE FROM conversation_images WHERE conversation_id = ?", (conversation_id,))
                conn.execute("DELETE FROM clean_transcript_edits WHERE run_id IN (SELECT id FROM clean_runs WHERE conversation_id = ?)", (conversation_id,))
                conn.execute("DELETE FROM clean_run_reviews WHERE run_id IN (SELECT id FROM clean_runs WHERE conversation_id = ?)", (conversation_id,))
                for session_id in sessions:
                    conn.execute("DELETE FROM recording_chunks WHERE recording_id = ?", (session_id,))
                    conn.execute("DELETE FROM recording_events WHERE recording_id = ?", (session_id,))
                    conn.execute("DELETE FROM recording_ai_runs WHERE recording_id = ?", (session_id,))
                for table in ("category_decisions", "enhanced_notes_runs", "transcript_agent_runs", "meeting_notes", "recording_sessions", "clean_runs", "ai_runs", "transcript_comments", "transcript_highlights",
                              "speaker_label_events", "speaker_labels", "transcript_edit_events",
                              "transcript_edits", "transcript_generations", "transcripts", "processing_jobs",
                              "conversation_categories", "person_links", "passage_voices", "transcript_reviews",
                              "transcript_review_history"):
                    conn.execute(f"DELETE FROM {table} WHERE conversation_id = ?", (conversation_id,))
                todos.delete_source(conn, "conversation", conversation_id)
                suggestions.delete_source(conn, "conversation", conversation_id)
                conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
                conn.commit()
            except Exception:
                conn.rollback()
                if staged is not None:
                    staged.rename(media)
                raise
        if staged is not None:
            staged.unlink(missing_ok=True)
        for session_id in sessions:
            shutil.rmtree(storage.data_dir() / "recordings" / session_id, ignore_errors=True)
        for job_id in job_ids:
            (storage.data_dir() / "job-locks" / f"{job_id}.lock").unlink(missing_ok=True)
        from stellis import images
        images.remove_files(image_ids)
    return {"id": conversation_id, "deleted": True}


def delete_expired(*, now: datetime | None = None) -> dict[str, object]:
    """Sweep expired items; retry busy jobs during the next scheduled sweep.

    An account that keeps debug data keeps Recently Deleted too.
    """
    from stellis import debug_data
    if debug_data.enabled():
        return {"deleted": [], "deferred": []}
    cutoff = ((now or datetime.now(timezone.utc)).astimezone(timezone.utc) -
              timedelta(days=RETENTION_DAYS)).isoformat()
    with storage.connect() as conn:
        expired = [str(row["id"]) for row in conn.execute(
            "SELECT id FROM conversations WHERE trashed_at IS NOT NULL AND trashed_at <= ?",
            (cutoff,)).fetchall()]
    from stellis import pages
    deleted, deferred = pages.delete_expired(cutoff), []
    for conversation_id in expired:
        try:
            delete_permanently(conversation_id, expired_before=cutoff)
            deleted.append(conversation_id)
        except LookupError:
            # Restored or moved to Recently Deleted again after the sweep query.
            continue
        except RuntimeError:
            deferred.append(conversation_id)
    return {"deleted": deleted, "deferred": deferred}


def erase_all() -> dict[str, int]:
    """Remove every conversation, capture, and library record on this device for account deletion."""
    with storage.connect() as conn:
        for table in ("processing_jobs", "ai_runs", "clean_runs", "enhanced_notes_runs", "transcript_agent_runs", "recording_ai_runs"):
            if conn.execute(f"SELECT 1 FROM {table} WHERE state IN ('queued', 'running') LIMIT 1").fetchone():
                raise RuntimeError("Wait for processing to finish before deleting your account")
        conn.execute("UPDATE conversations SET trashed_at = ? WHERE trashed_at IS NULL", (_now(),))
        conversation_ids = [str(row["id"]) for row in conn.execute("SELECT id FROM conversations")]
        captures = [str(row["id"]) for row in conn.execute("SELECT id FROM recording_sessions WHERE state = 'recording'")]
    for conversation_id in conversation_ids:
        delete_permanently(conversation_id)
    from stellis import recording
    for recording_id in captures:
        recording.discard_session(recording_id)
    with storage.connect() as conn:
        page_image_ids = [str(row["id"]) for row in conn.execute("SELECT id FROM page_images")]
        conn.execute("DELETE FROM page_images")
        for table in ("vocabulary", "persons", "page_categories", "pages", "todo_items", "suggestions", "notebooks", "collections", "category_automation", "categories", "event_notes", "link_previews"):
            conn.execute(f"DELETE FROM {table}")
        # Seed the default categories again for the next account.
        conn.execute("DELETE FROM schema_migrations WHERE name = 'initial_categories'")
    from stellis import images
    images.remove_files(page_image_ids)
    from stellis import debug_data
    debug_data.erase()
    return {"conversations": len(conversation_ids), "recordings": len(captures)}
