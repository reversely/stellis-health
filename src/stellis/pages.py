"""Standalone revisioned Markdown pages (ADR 0058)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from stellis import storage, suggestions, todos
from stellis.notes import MAX_MARKDOWN
from stellis.todos import TooLarge

COLUMNS = "id, title, markdown, revision, notebook_id, created_at, updated_at"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title(value: str | None) -> str:
    title = " ".join((value or "").split())
    if not title:
        return "Untitled"
    if len(title) > 200:
        raise ValueError("Enter a title of at most 200 characters")
    return title


def _check_notebook(conn, notebook_id: str | None) -> None:
    if notebook_id is not None and conn.execute("SELECT 1 FROM notebooks WHERE id = ?", (notebook_id,)).fetchone() is None:
        raise LookupError("Notebook not found")


def _check_markdown(markdown: str) -> None:
    if not isinstance(markdown, str) or len(markdown.encode("utf-8")) > MAX_MARKDOWN:
        raise TooLarge("Page is too large")


def _active(conn, page_id: str):
    row = conn.execute(f"SELECT {COLUMNS} FROM pages WHERE id = ? AND trashed_at IS NULL", (page_id,)).fetchone()
    if row is None:
        raise LookupError("Page not found")
    return row


def page_tags(conn, page_ids: list[str] | None = None) -> dict[str, list[str]]:
    """Tag IDs per page ID, in ID order; all pages when `page_ids` is None."""
    rows = conn.execute("SELECT page_id, category_id FROM page_categories ORDER BY category_id").fetchall()
    wanted = None if page_ids is None else set(page_ids)
    tags: dict[str, list[str]] = {}
    for row in rows:
        if wanted is None or row["page_id"] in wanted:
            tags.setdefault(str(row["page_id"]), []).append(str(row["category_id"]))
    return tags


def _full(conn, page_id: str) -> dict[str, object]:
    return {**dict(_active(conn, page_id)), "category_ids": page_tags(conn, [page_id]).get(page_id, []),
            "todo_dates": todos.due_dates(conn, "page", page_id)}


def _with_tags(conn, rows) -> list[dict[str, object]]:
    tags = page_tags(conn)
    return [{**dict(row), "category_ids": tags.get(str(row["id"]), [])} for row in rows]


def set_categories(page_id: str, category_ids: list[str]) -> dict[str, object]:
    """Replace a page's tags; an unknown page or tag raises LookupError."""
    wanted = list(dict.fromkeys(category_ids))
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _active(conn, page_id)
        for category_id in wanted:
            if conn.execute("SELECT 1 FROM categories WHERE id = ?", (category_id,)).fetchone() is None:
                raise LookupError("Category not found")
        conn.execute("DELETE FROM page_categories WHERE page_id = ?", (page_id,))
        conn.executemany("INSERT INTO page_categories (page_id, category_id) VALUES (?, ?)",
                         [(page_id, category_id) for category_id in wanted])
    return {"id": page_id, "category_ids": sorted(wanted)}


def create(title: str | None = None, notebook_id: str | None = None, markdown: str = "") -> dict[str, object]:
    with storage.connect() as conn:
        return create_in_transaction(conn, title, notebook_id, markdown)


def create_in_transaction(conn, title=None, notebook_id=None, markdown=""):
    """Create a page as part of a caller-owned atomic operation."""
    name = _title(title)
    _check_markdown(markdown)
    stamp, page_id = _stamp(), str(uuid.uuid4())
    _check_notebook(conn, notebook_id)
    markdown = todos.reindex(conn, "page", page_id, markdown)
    conn.execute("INSERT INTO pages (id, title, markdown, notebook_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (page_id, name, markdown, notebook_id, stamp, stamp))
    return _full(conn, page_id)


def get(page_id: str) -> dict[str, object]:
    with storage.connect() as conn:
        return _full(conn, page_id)


def list_pages(notebook_id: str | None = None) -> list[dict[str, object]]:
    """List active pages: one notebook, `"none"` for pages without a notebook, or all."""
    where, args = "trashed_at IS NULL", ()
    if notebook_id == "none":
        where += " AND notebook_id IS NULL"
    elif notebook_id is not None:
        where, args = where + " AND (notebook_id = ? OR EXISTS (SELECT 1 FROM notebook_record_links l WHERE l.notebook_id = ? AND l.source_id = 'page:' || pages.id))", (notebook_id,notebook_id)
    with storage.connect() as conn:
        rows = conn.execute(f"SELECT id, title, notebook_id, created_at, updated_at FROM pages WHERE {where} "
                            "ORDER BY updated_at DESC, created_at DESC", args).fetchall()
        return _with_tags(conn, rows)


def rename(page_id: str, title: str | None) -> dict[str, object]:
    name = _title(title)
    with storage.connect() as conn:
        _active(conn, page_id)
        conn.execute("UPDATE pages SET title = ?, updated_at = ? WHERE id = ?", (name, _stamp(), page_id))
        return _full(conn, page_id)


def save(page_id: str, markdown: str, expected_revision: int, todo_dates: dict | None = None) -> dict[str, object]:
    """Save a whole document with optimistic revision protection, as notes.save does."""
    _check_markdown(markdown)
    if expected_revision < 0:
        raise ValueError("Invalid page revision")
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _active(conn, page_id)
        if int(row["revision"]) != expected_revision:
            raise RuntimeError("Notes changed in another tab")
        markdown = todos.reindex(conn, "page", page_id, markdown, todo_dates)
        conn.execute("UPDATE pages SET markdown = ?, revision = ?, updated_at = ? WHERE id = ?",
                     (markdown, expected_revision + 1, _stamp(), page_id))
        return _full(conn, page_id)


def assign_notebook(page_id: str, notebook_id: str | None) -> dict[str, object]:
    with storage.connect() as conn:
        _check_notebook(conn, notebook_id)
        _active(conn, page_id)
        conn.execute("UPDATE pages SET notebook_id = ? WHERE id = ?", (notebook_id, page_id))
    return {"id": page_id, "notebook_id": notebook_id}


def move_to_trash(page_id: str) -> dict[str, object]:
    with storage.connect() as conn:
        changed = conn.execute("UPDATE pages SET trashed_at = ? WHERE id = ? AND trashed_at IS NULL",
                               (_stamp(), page_id)).rowcount
    if not changed:
        raise LookupError("Page not found")
    return {"id": page_id, "trashed": True}


def trashed() -> list[dict[str, object]]:
    with storage.connect() as conn:
        rows = conn.execute("SELECT id, title, notebook_id, created_at, updated_at, trashed_at FROM pages "
                            "WHERE trashed_at IS NOT NULL ORDER BY trashed_at DESC").fetchall()
        return _with_tags(conn, rows)


def restore(page_id: str) -> dict[str, object]:
    with storage.connect() as conn:
        changed = conn.execute("UPDATE pages SET trashed_at = NULL WHERE id = ? AND trashed_at IS NOT NULL", (page_id,)).rowcount
    if not changed:
        raise LookupError("Page not found in Recently Deleted")
    return {"id": page_id, "trashed": False}


def _remove_image_files(image_ids: list[str]) -> None:
    if image_ids:
        from stellis import images  # Pillow loads only when a page had images.
        images.remove_files(image_ids)


def delete_permanently(page_id: str, *, expired_before: str | None = None) -> dict[str, object]:
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""DELETE FROM page_categories WHERE page_id = ? AND EXISTS (
            SELECT 1 FROM pages WHERE id = ? AND trashed_at IS NOT NULL AND (? IS NULL OR trashed_at <= ?))""",
                     (page_id, page_id, expired_before, expired_before))
        image_ids = [str(row["id"]) for row in conn.execute("""SELECT id FROM page_images WHERE page_id = ? AND EXISTS (
            SELECT 1 FROM pages WHERE id = ? AND trashed_at IS NOT NULL AND (? IS NULL OR trashed_at <= ?))""",
                                                              (page_id, page_id, expired_before, expired_before))]
        conn.executemany("DELETE FROM page_images WHERE id = ?", [(image_id,) for image_id in image_ids])
        deleted = conn.execute("DELETE FROM pages WHERE id = ? AND trashed_at IS NOT NULL AND (? IS NULL OR trashed_at <= ?)",
                               (page_id, expired_before, expired_before)).rowcount
        if deleted:
            todos.delete_source(conn, "page", page_id)
            suggestions.delete_source(conn, "page", page_id)
    if not deleted:
        raise LookupError("Page not found in Recently Deleted")
    _remove_image_files(image_ids)
    return {"id": page_id, "deleted": True}


def delete_expired(cutoff: str) -> list[str]:
    with storage.connect() as conn:
        ids = [str(row["id"]) for row in conn.execute(
            "SELECT id FROM pages WHERE trashed_at IS NOT NULL AND trashed_at <= ?", (cutoff,))]
        image_ids: list[str] = []
        for page_id in ids:
            image_ids += [str(row["id"]) for row in conn.execute("SELECT id FROM page_images WHERE page_id = ?", (page_id,))]
            conn.execute("DELETE FROM page_images WHERE page_id = ?", (page_id,))
            conn.execute("DELETE FROM page_categories WHERE page_id = ?", (page_id,))
        conn.execute("DELETE FROM pages WHERE trashed_at IS NOT NULL AND trashed_at <= ?", (cutoff,))
        for page_id in ids:
            todos.delete_source(conn, "page", page_id)
            suggestions.delete_source(conn, "page", page_id)
    _remove_image_files(image_ids)
    return ids


def by_notebook() -> dict[str, list[dict[str, object]]]:
    """Active pages grouped by notebook ID, newest first, for notebook listings."""
    with storage.connect() as conn:
        rows = conn.execute("SELECT id, title, updated_at, notebook_id FROM pages "
                            "WHERE notebook_id IS NOT NULL AND trashed_at IS NULL ORDER BY updated_at DESC").fetchall()
        tags = page_tags(conn)
    with storage.connect() as conn:
        linked = conn.execute("""SELECT p.id,p.title,p.updated_at,l.notebook_id FROM notebook_record_links l
            JOIN pages p ON l.source_id = 'page:' || p.id WHERE p.trashed_at IS NULL
            AND (p.notebook_id IS NULL OR p.notebook_id != l.notebook_id)""").fetchall()
    rows = list(rows) + list(linked)
    groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault(str(row["notebook_id"]), []).append(
            {**{key: row[key] for key in ("id", "title", "updated_at")}, "category_ids": tags.get(str(row["id"]), [])})
    return groups
