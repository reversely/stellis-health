"""Durable browser microphone chunks and recovery into normal conversations."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile

from stellis import notes, storage, tools
from stellis.importer import ImportFailure, MAX_AUDIO_BYTES, MAX_UPLOAD_LABEL, import_upload

logger = logging.getLogger(__name__)

MAX_CHUNK_BYTES = 8_000_000
RECORDING_TYPES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a"}


_MUTATING = set()
_MUTATION_LOCK = threading.Lock()


@contextmanager
def _mutation(recording_id):
    """Reject overlapping finalization/metadata writes without blocking the event loop."""
    key = (str(storage.data_dir()), recording_id)
    with _MUTATION_LOCK:
        if key in _MUTATING:
            raise RuntimeError("Recording is being saved. Try again shortly.")
        _MUTATING.add(key)
    try:
        yield
    finally:
        with _MUTATION_LOCK:
            _MUTATING.discard(key)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _directory(recording_id: str) -> Path:
    try:
        recording_id = str(uuid.UUID(recording_id))
    except ValueError as exc:
        raise ValueError("Invalid recording session ID") from exc
    root = storage.data_dir() / "recordings" / recording_id
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def _public(row) -> dict[str, object]:
    return {key: row[key] for key in ("id", "title", "media_type", "state",
                                      "conversation_id", "notebook_id", "created_at", "updated_at")}


def create_session(title: str, media_type: str, notebook_id: str | None = None,
                   calendar_event: tuple[str, str] | None = None) -> dict[str, object]:
    """Start a recoverable local recording before microphone capture begins."""
    if media_type not in RECORDING_TYPES:
        raise ValueError("This browser recording format is unsupported")
    cleaned = title.strip()[:180] or "New recording"
    stamp = _stamp()
    record = {"id": str(uuid.uuid4()), "title": cleaned, "media_type": media_type,
              "state": "recording", "conversation_id": None, "notebook_id": notebook_id,
              "created_at": stamp, "updated_at": stamp}
    _directory(record["id"])
    with storage.connect() as conn:
        conn.execute(
            """INSERT INTO recording_sessions
               (id, title, media_type, state, conversation_id, notebook_id, created_at, updated_at)
               VALUES (:id, :title, :media_type, :state, :conversation_id, :notebook_id, :created_at, :updated_at)""",
            record,
        )
        if calendar_event:
            notes.move_event_to_recording(conn, *calendar_event, record["id"])
    return record


def list_sessions() -> list[dict[str, object]]:
    """List unfinished sessions with their durable chunk count."""
    with storage.connect() as conn:
        rows = conn.execute(
            """SELECT s.*, COUNT(c.sequence) AS chunk_count,
                      COALESCE(SUM(c.size_bytes), 0) AS stored_bytes
               FROM recording_sessions s LEFT JOIN recording_chunks c ON c.recording_id = s.id
               WHERE s.state = 'recording' GROUP BY s.id ORDER BY s.created_at DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_session(recording_id: str) -> dict[str, object] | None:
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM recording_sessions WHERE id = ?", (recording_id,)).fetchone()
    return dict(row) if row else None


def rename_session(recording_id: str, title: str) -> dict[str, object]:
    with _mutation(recording_id):
        return _rename_session(recording_id, title)


def _rename_session(recording_id: str, title: str) -> dict[str, object]:
    """Save the visible title while a recording remains unfinished."""
    cleaned = title.strip()[:180]
    if not cleaned:
        raise ValueError("Enter a recording title")
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state FROM recording_sessions WHERE id = ?", (recording_id,)).fetchone()
        if row is None:
            raise LookupError("Recording session not found")
        if row["state"] != "recording":
            raise RuntimeError("Recording is already finalized")
        conn.execute("UPDATE recording_sessions SET title = ?, updated_at = ? WHERE id = ?",
                     (cleaned, _stamp(), recording_id))
    session = get_session(recording_id)
    assert session is not None
    return _public(session)


def assign_notebook(recording_id: str, notebook_id: str | None) -> dict[str, object]:
    """Save a validated destination before finalization freezes recording metadata."""
    notebook_id = notebook_id or None
    with _mutation(recording_id), storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state FROM recording_sessions WHERE id=?", (recording_id,)).fetchone()
        if row is None:
            raise LookupError("Recording session not found")
        if row["state"] != "recording":
            raise RuntimeError("Recording is already finalized")
        if notebook_id and not conn.execute("SELECT 1 FROM notebooks WHERE id=?", (notebook_id,)).fetchone():
            raise ValueError("Notebook not found")
        conn.execute("UPDATE recording_sessions SET notebook_id=?, updated_at=? WHERE id=?", (notebook_id, _stamp(), recording_id))
    return _public(get_session(recording_id))


def discard_session(recording_id: str) -> bool:
    with _mutation(recording_id):
        return _discard_session(recording_id)


def _discard_session(recording_id: str) -> bool:
    """Delete an unfinished capture after the user explicitly discards it."""
    from stellis import recording_preview
    recording_preview.stop(recording_id)
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state FROM recording_sessions WHERE id = ?", (recording_id,)).fetchone()
        if row is None:
            return False
        if row["state"] != "recording":
            raise RuntimeError("A saved conversation cannot be discarded here")
        image_ids = [str(item["id"]) for item in conn.execute(
            "SELECT id FROM conversation_images WHERE recording_id = ?", (recording_id,))]
        conn.execute("DELETE FROM conversation_images WHERE recording_id = ?", (recording_id,))
        conn.execute("DELETE FROM recording_ai_runs WHERE recording_id = ?", (recording_id,))
        conn.execute("DELETE FROM meeting_notes WHERE recording_id = ?", (recording_id,))
        conn.execute("DELETE FROM recording_chunks WHERE recording_id = ?", (recording_id,))
        conn.execute("DELETE FROM recording_events WHERE recording_id = ?", (recording_id,))
        conn.execute("DELETE FROM recording_sessions WHERE id = ?", (recording_id,))
    shutil.rmtree(_directory(recording_id), ignore_errors=True)
    from stellis import images
    images.remove_files(image_ids)
    return True


def record_event(recording_id: str, kind: str, media_position_ms: int) -> dict[str, object]:
    """Record pauses and device endings against the captured media timeline."""
    if kind not in {"pause", "resume", "device_ended"} or media_position_ms < 0:
        raise ValueError("Invalid recording event")
    stamp = _stamp()
    result = {"id": str(uuid.uuid4()), "kind": kind,
              "media_position_ms": media_position_ms, "created_at": stamp}
    with storage.connect() as conn:
        session = conn.execute("SELECT state FROM recording_sessions WHERE id = ?", (recording_id,)).fetchone()
        if session is None:
            raise LookupError("Recording session not found")
        if session["state"] != "recording":
            raise RuntimeError("Recording is already finalized")
        conn.execute(
            """INSERT INTO recording_events (id, recording_id, kind, media_position_ms, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (result["id"], recording_id, kind, media_position_ms, stamp),
        )
    return result


def save_chunk(recording_id: str, sequence: int, data: bytes) -> dict[str, object]:
    """Store one bounded MediaRecorder chunk, idempotently by sequence and digest."""
    if sequence < 0 or sequence > 1_000_000:
        raise ValueError("Invalid recording chunk sequence")
    if not data or len(data) > MAX_CHUNK_BYTES:
        raise ValueError("Recording chunk is empty or too large")
    digest = hashlib.sha256(data).hexdigest()
    directory = _directory(recording_id)
    descriptor, name = tempfile.mkstemp(prefix=".chunk-", dir=directory)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        temp.chmod(0o600)
        with storage.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = conn.execute("SELECT state FROM recording_sessions WHERE id = ?", (recording_id,)).fetchone()
            if session is None:
                raise LookupError("Recording session not found")
            if session["state"] != "recording":
                raise RuntimeError("Recording is already finalized")
            prior = conn.execute("SELECT sha256, size_bytes FROM recording_chunks WHERE recording_id = ? AND sequence = ?",
                                 (recording_id, sequence)).fetchone()
            if prior:
                if prior["sha256"] != digest or prior["size_bytes"] != len(data):
                    raise RuntimeError("This chunk sequence already contains different audio")
                return {"sequence": sequence, "stored_bytes": len(data), "duplicate": True}
            total = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM recording_chunks WHERE recording_id = ?",
                                 (recording_id,)).fetchone()[0]
            if int(total) + len(data) > MAX_AUDIO_BYTES:
                raise ValueError(f"Recording exceeds the {MAX_UPLOAD_LABEL} limit")
            destination = directory / f"{sequence:08d}.chunk"
            os.replace(temp, destination)
            conn.execute(
                """INSERT INTO recording_chunks (recording_id, sequence, size_bytes, sha256, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (recording_id, sequence, len(data), digest, _stamp()),
            )
            conn.execute("UPDATE recording_sessions SET updated_at = ? WHERE id = ?",
                         (_stamp(), recording_id))
        return {"sequence": sequence, "stored_bytes": len(data), "duplicate": False}
    finally:
        temp.unlink(missing_ok=True)


async def finalize_session(recording_id: str) -> dict[str, object]:
    with _mutation(recording_id):
        return await _finalize_session(recording_id)


async def _finalize_session(recording_id: str) -> dict[str, object]:
    """Concatenate ordered chunks and import the resulting browser audio."""
    session = get_session(recording_id)
    if session is None:
        raise LookupError("Recording session not found")
    if session["state"] == "finalized":
        conversation = storage.get_conversation(str(session["conversation_id"]))
        if conversation is None:
            raise RuntimeError("Finalized recording has no conversation")
        return {"conversation": conversation, "duplicate": True}
    with storage.connect() as conn:
        rows = conn.execute("SELECT sequence, size_bytes, sha256 FROM recording_chunks WHERE recording_id = ? ORDER BY sequence",
                            (recording_id,)).fetchall()
    if not rows or [row["sequence"] for row in rows] != list(range(len(rows))):
        raise ValueError("Recording has no complete, ordered chunks to recover")
    directory = _directory(recording_id)
    descriptor, name = tempfile.mkstemp(prefix=".joined-", dir=directory)
    joined = Path(name)
    remuxed: Path | None = None
    try:
        with os.fdopen(descriptor, "wb") as output:
            for row in rows:
                source = directory / f"{row['sequence']:08d}.chunk"
                data = source.read_bytes()
                if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                    raise ImportFailure("A stored recording chunk failed integrity verification")
                output.write(data)
            output.flush()
            os.fsync(output.fileno())
        suffix = RECORDING_TYPES[str(session["media_type"])]
        remux_descriptor, remux_name = tempfile.mkstemp(prefix=".ready-", suffix=suffix, dir=directory)
        os.close(remux_descriptor)
        remuxed = Path(remux_name)
        try:
            subprocess.run([tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(joined),
                            "-c", "copy", str(remuxed)], check=True, capture_output=True, timeout=300)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ImportFailure("The saved browser audio could not be assembled") from exc
        with remuxed.open("rb") as source:
            upload = UploadFile(file=source, filename=f"{session['title']}{suffix}")
            conversation, duplicate = await import_upload(
                upload, browser_recording=True, recorded_at=str(session["created_at"]),
                notebook_id=session.get("notebook_id"))
        notes.attach_to_conversation(recording_id, str(conversation["id"]))
        from stellis import images
        images.attach_to_conversation(recording_id, str(conversation["id"]), int(conversation["duration_ms"]))
        with storage.connect() as conn:
            conn.execute("UPDATE recording_sessions SET state = 'finalized', conversation_id = ?, updated_at = ? WHERE id = ?",
                         (conversation["id"], _stamp(), recording_id))
        from stellis import recording_preview
        # The recording is saved; the provisional preview's cleanup never fails it.
        try:
            recording_preview.stop(recording_id)
        except Exception:
            logger.exception("Could not stop the live preview for a finalized recording")
        for row in rows:
            (directory / f"{row['sequence']:08d}.chunk").unlink(missing_ok=True)
        (directory / "preview.json").unlink(missing_ok=True)
        return {"conversation": conversation, "duplicate": duplicate}
    finally:
        joined.unlink(missing_ok=True)
        if remuxed is not None:
            remuxed.unlink(missing_ok=True)
