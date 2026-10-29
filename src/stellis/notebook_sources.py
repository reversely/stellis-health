"""Sources a notebook's chat can read and which of them the user unchecked.

A source ID names its kind: ``conversation:<id>`` for a recording or an
imported audio file, ``page:<id>`` for a written note, and ``document:<id>``
for a PDF or image. The selection stores unchecked sources only, so a source
added later starts checked.
"""

from __future__ import annotations

from stellis import documents
from stellis import storage

# Broad collection notebooks retain longitudinal history without requiring
# artificial subtopic notebooks solely to fit the former 50-source cap.
LIMIT = 1000

_PDF = "application/pdf"


def _create_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notebook_source_exclusions (
            notebook_id TEXT NOT NULL
                REFERENCES notebooks(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            PRIMARY KEY (notebook_id, source_id))""")


def _require_notebook(conn, notebook_id: str) -> None:
    row = conn.execute("SELECT 1 FROM notebooks WHERE id = ?",
                       (notebook_id,)).fetchone()
    if row is None:
        raise LookupError("Notebook not found")


def _conversation_status(has_transcript: bool, job_state: str | None) -> str:
    """Returns a conversation's status from its transcript and latest job.

    Args:
        has_transcript: Whether the conversation has a transcript.
        job_state: The state of its latest speech job, or None.

    Returns:
        "ready" once a transcript exists, "failed" when the latest speech job
        failed, and "processing" otherwise.
    """
    if has_transcript:
        return "ready"
    return "failed" if job_state == "failed" else "processing"


def _read_conversations(conn, notebook_id: str) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT c.id, c.title,
               COALESCE(c.recorded_at, c.created_at) AS created_at,
               t.conversation_id IS NOT NULL AS has_transcript,
               r.conversation_id IS NOT NULL AS recorded,
               (SELECT j.state FROM processing_jobs j
                WHERE j.conversation_id = c.id AND j.kind = 'speech'
                ORDER BY j.created_at DESC LIMIT 1) AS job_state
        FROM conversations c
        LEFT JOIN transcripts t ON t.conversation_id = c.id
        LEFT JOIN (SELECT DISTINCT conversation_id FROM recording_sessions) r
            ON r.conversation_id = c.id
        WHERE (c.notebook_id = ? OR EXISTS (SELECT 1 FROM notebook_record_links l WHERE l.notebook_id = ? AND l.source_id = 'conversation:' || c.id)) AND c.trashed_at IS NULL""",
        (notebook_id, notebook_id)).fetchall()
    return [{
        "id":
            "conversation:" + row["id"],
        "type":
            "recording" if row["recorded"] else "audio",
        "title":
            row["title"],
        "created_at":
            row["created_at"],
        "status":
            _conversation_status(bool(row["has_transcript"]), row["job_state"]),
    } for row in rows]


def _read_notes(conn, notebook_id: str) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT id, title, created_at FROM pages
        WHERE (notebook_id = ? OR EXISTS (SELECT 1 FROM notebook_record_links l WHERE l.notebook_id = ? AND l.source_id = 'page:' || pages.id)) AND trashed_at IS NULL""",
        (notebook_id, notebook_id)).fetchall()
    return [{
        "id": "page:" + row["id"],
        "type": "note",
        "title": row["title"],
        "created_at": row["created_at"],
        "status": "ready",
    } for row in rows]


def _document_source(document: dict[str, object]) -> dict[str, object]:
    # A scan or photo has no text yet, so it stays listed as needs_text.
    return {
        "id": "document:" + str(document["id"]),
        "type": "pdf" if document["media_type"] == _PDF else "image",
        "title": document["title"],
        "created_at": document["record_date"] or document["created_at"],
        "status": document["status"],
    }


def list_sources(notebook_id: str) -> list[dict[str, object]]:
    """Lists a notebook's sources, newest first.

    Args:
        notebook_id: The notebook to list.

    Returns:
        One dict per source with ``id``, ``type``, ``title``, ``created_at``,
        ``status`` and ``selected``. Only ready sources can be selected.

    Raises:
        LookupError: The notebook does not exist.
    """
    with storage.connect() as conn:
        _create_table(conn)
        _require_notebook(conn, notebook_id)
        excluded = {
            row[0] for row in conn.execute(
                "SELECT source_id FROM notebook_source_exclusions "
                "WHERE notebook_id = ?", (notebook_id,))
        }
        sources = (_read_conversations(conn, notebook_id) +
                   _read_notes(conn, notebook_id))
    sources += [
        _document_source(item) for item in documents.list_all(notebook_id)
    ]
    for source in sources:
        source["selected"] = (source["status"] == "ready" and
                              source["id"] not in excluded)
    return sorted(sources,
                  key=lambda source: str(source["created_at"]),
                  reverse=True)


def require_room(notebook_id: str | None, source_id: str | None = None) -> None:
    """Checks that a notebook can take one more source.

    Args:
        notebook_id: The receiving notebook, or None for no notebook.
        source_id: The source being added, if it already exists. A source
            already in the notebook always fits.

    Raises:
        LookupError: The notebook does not exist.
        ValueError: The notebook already has ``LIMIT`` sources.
    """
    if notebook_id is None:
        return
    current = {str(source["id"]) for source in list_sources(notebook_id)}
    if source_id not in current and len(current) >= LIMIT:
        raise ValueError(f"This notebook is full at {LIMIT} sources")


def save_selection(notebook_id: str,
                   excluded: list[str]) -> list[dict[str, object]]:
    """Replaces a notebook's unchecked sources.

    Args:
        notebook_id: The notebook whose selection changes.
        excluded: Source IDs to uncheck. IDs outside the notebook are ignored.

    Returns:
        The notebook's sources after the change, as ``list_sources`` returns.

    Raises:
        LookupError: The notebook does not exist.
        ValueError: ``excluded`` is not a list of strings.
    """
    if not isinstance(excluded, list) or not all(
            isinstance(item, str) for item in excluded):
        raise ValueError("Send the unchecked sources as a list of source ids")
    known = {str(source["id"]) for source in list_sources(notebook_id)}
    rows = [
        (notebook_id, item) for item in dict.fromkeys(excluded) if item in known
    ]
    with storage.connect() as conn:
        _create_table(conn)
        conn.execute(
            "DELETE FROM notebook_source_exclusions WHERE notebook_id = ?",
            (notebook_id,))
        conn.executemany(
            "INSERT INTO notebook_source_exclusions (notebook_id, source_id) "
            "VALUES (?, ?)", rows)
    return list_sources(notebook_id)


def link(notebook_id: str, source_id: str, evidence=None):
    """Associates an existing account-local source without moving or copying it."""
    import json
    kind, separator, identifier = source_id.partition(':')
    table = {'document':'documents', 'conversation':'conversations', 'page':'pages'}.get(kind)
    if not separator or table is None:
        raise ValueError('Choose an existing record')
    if kind == 'document':
        documents.list_all()
    require_room(notebook_id, source_id)
    with storage.connect() as conn:
        _require_notebook(conn, notebook_id)
        condition = '' if kind == 'document' else ' AND trashed_at IS NULL'
        if conn.execute(f'SELECT 1 FROM {table} WHERE id = ?'+condition, (identifier,)).fetchone() is None:
            raise LookupError('Record not found')
        conn.execute('INSERT OR IGNORE INTO notebook_record_links (notebook_id,source_id,evidence_json) VALUES (?,?,?)',
                     (notebook_id, source_id, json.dumps(evidence or [])))
    return list_sources(notebook_id)


def unlink(notebook_id: str, source_id: str):
    """Removes an additional association and preserves the source record."""
    with storage.connect() as conn:
        _require_notebook(conn, notebook_id)
        conn.execute('DELETE FROM notebook_record_links WHERE notebook_id = ? AND source_id = ?', (notebook_id,source_id))
    return list_sources(notebook_id)
