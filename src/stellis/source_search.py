"""Ranked, source-linked search for agent tools and future cross-source UI."""

from __future__ import annotations

import re
from urllib.parse import quote

from stellis import documents as documents_module, search, storage


def _documents() -> list[tuple[str, str, str, str, str, str, int]]:
    """Project current effective text; saved edits and trash are respected."""
    documents: list[tuple[str, str, str, str, str, str, int]] = []
    conversations = storage.list_conversations()
    ids = [str(item["id"]) for item in conversations]
    notes = search.note_texts(ids)
    for item in conversations:
        conversation_id = str(item["id"])
        notebook_id = str(item["notebook_id"] or "")
        title = str(item["title"])
        if notes.get(conversation_id):
            documents.append(("note", conversation_id, notebook_id, title,
                              notes[conversation_id], f"/conversation/{quote(conversation_id)}", 0))
        display = storage.get_display_transcript(conversation_id)
        for passage in display["segments"] if display else []:
            passage_id = str(passage["id"])
            start_ms = int(passage["start_ms"])
            target = f"/conversation/{quote(conversation_id)}?at={quote(passage_id)}&t={start_ms}"
            documents.append(("passage", passage_id, notebook_id, title,
                              str(passage["text"]), target, start_ms))
    with storage.connect() as conn:
        pages = conn.execute("SELECT id, title, markdown, notebook_id FROM pages WHERE trashed_at IS NULL").fetchall()
    for page in pages:
        page_id = str(page["id"])
        body = search.plain_text(str(page["markdown"]))
        documents.append(("page", page_id, str(page["notebook_id"] or ""), str(page["title"]),
                          f"{page['title']}\n{body}", f"/page/{quote(page_id)}", 0))
    for page in documents_module.all_pages():
        if page["text"]:
            documents.append(("document", str(page["document_id"]), str(page["notebook_id"] or ""),
                              str(page["title"]), str(page["text"]),
                              f"/document/{quote(str(page['document_id']))}?page={page['number']}", 0))
    return documents


def search_sources(query: str, notebook_id: str | None = None, limit: int = 30) -> list[dict[str, object]]:
    """Return FTS5-ranked passages, My Notes and pages with working source targets.

    The projection is rebuilt in one transaction so edits, notebook moves and deletion
    are visible immediately. The separate Library search contract stays unchanged.
    """
    terms = re.findall(r"\w+", query, flags=re.UNICODE)
    if not terms or len(query) > 200:
        raise ValueError("Enter a search query of at most 200 characters")
    if limit < 1 or limit > 100:
        raise ValueError("Choose a limit from 1 to 100")
    expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
    documents = _documents()
    with storage.connect() as conn:
        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
            source_type UNINDEXED, source_id UNINDEXED, notebook_id UNINDEXED,
            title, body, target UNINDEXED, start_ms UNINDEXED)""")
        conn.execute("DELETE FROM source_fts")
        conn.executemany("""INSERT INTO source_fts
            (source_type, source_id, notebook_id, title, body, target, start_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)""", documents)
        scope = """ AND (notebook_id = ? OR EXISTS (SELECT 1 FROM notebook_record_links l
            WHERE l.notebook_id = ? AND l.source_id =
            CASE source_fts.source_type WHEN 'document' THEN 'document:' || source_fts.source_id
            WHEN 'page' THEN 'page:' || source_fts.source_id
            WHEN 'note' THEN 'conversation:' || source_fts.source_id
            WHEN 'passage' THEN 'conversation:' || substr(source_fts.target, 15, 36) END))""" if notebook_id is not None else ""
        parameters: tuple[object, ...] = (expression, notebook_id, notebook_id, limit) if notebook_id is not None else (expression, limit)
        rows = conn.execute(f"""SELECT source_type, source_id, title,
            snippet(source_fts, 4, '[', ']', '…', 48) AS excerpt,
            target, start_ms, bm25(source_fts, 0, 0, 0, 3, 1) AS rank
            FROM source_fts WHERE source_fts MATCH ?{scope}
            ORDER BY rank LIMIT ?""", parameters).fetchall()
    return [dict(row) for row in rows]
