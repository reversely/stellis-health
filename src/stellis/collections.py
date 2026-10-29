"""Approved health collections and preserved legacy notebook groups."""

from __future__ import annotations

import sqlite3
import uuid

from stellis import storage, suggestions
from stellis.notebooks import _color, _now


class NameTaken(ValueError):
    """Another collection already has this name, ignoring case."""


def _name(value: str) -> str:
    name = " ".join(value.split())
    if not 1 <= len(name) <= 80:
        raise ValueError("Enter a collection name of at most 80 characters")
    return name


def list_collections() -> list[dict[str, object]]:
    """List collections by creation, each with its notebook IDs in notebook creation order."""
    with storage.connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT id, name, color, created_at, todo_list, taxonomy_id FROM collections ORDER BY created_at, rowid")]
        members = conn.execute("SELECT id, collection_id FROM notebooks WHERE collection_id IS NOT NULL ORDER BY created_at, rowid")
        by_collection: dict[str, list[str]] = {}
        for row in members:
            by_collection.setdefault(str(row["collection_id"]), []).append(str(row["id"]))
    for row in rows:
        row["notebook_ids"] = by_collection.get(str(row["id"]), [])
    return rows


def create(name: str, color: str | None = None) -> dict[str, object]:
    value = _name(name)
    with storage.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
        record = {"id": str(uuid.uuid4()), "name": value, "color": _color(color, count), "created_at": _now()}
        try:
            conn.execute("INSERT INTO collections (id, name, normalized, color, created_at) VALUES (?, ?, ?, ?, ?)",
                         (record["id"], value, value.casefold(), record["color"], record["created_at"]))
        except sqlite3.IntegrityError as exc:
            raise NameTaken("A collection with this name already exists") from exc
    return record | {"todo_list": "off", "notebook_ids": []}


def update(collection_id: str, *, name: str | None = None, color: str | None = None,
           todo_list: str | None = None) -> dict[str, object]:
    with storage.connect() as conn:
        row = conn.execute("SELECT id, name, color, created_at, todo_list, taxonomy_id FROM collections WHERE id = ?", (collection_id,)).fetchone()
        if row is None:
            raise LookupError("Collection not found")
        if todo_list is not None and todo_list not in suggestions.COLLECTION_TODO_LIST:
            raise ValueError("Choose on or off for the to-do list")
        setting = todo_list if todo_list is not None else str(row["todo_list"])
        if row["taxonomy_id"] and name is not None and name != row["name"]:
            raise ValueError("Health collection names come from the catalogue")
        value = _name(name) if name is not None else str(row["name"])
        tint = _color(color, 0) if color is not None else str(row["color"])
        try:
            conn.execute("UPDATE collections SET name = ?, normalized = ?, color = ?, todo_list = ? WHERE id = ?",
                         (value, value.casefold(), tint, setting, collection_id))
        except sqlite3.IntegrityError as exc:
            raise NameTaken("A collection with this name already exists") from exc
        ids = [str(item["id"]) for item in conn.execute(
            "SELECT id FROM notebooks WHERE collection_id = ? ORDER BY created_at, rowid", (collection_id,))]
    return {"id": collection_id, "name": value, "color": tint, "created_at": row["created_at"], "todo_list": setting,
            "notebook_ids": ids}


def delete(collection_id: str) -> bool:
    """Remove a collection; its notebooks stay without a collection."""
    with storage.connect() as conn:
        conn.execute("UPDATE notebooks SET collection_id = NULL WHERE collection_id = ?", (collection_id,))
        return conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,)).rowcount > 0


def assign_notebook(notebook_id: str, collection_id: str | None) -> dict[str, object]:
    with storage.connect() as conn:
        from stellis import health_catalogue
        if collection_id and health_catalogue.collection(collection_id):
            collection_id = health_catalogue.ensure_collection(conn, collection_id)
        if collection_id is not None and conn.execute("SELECT 1 FROM collections WHERE id = ?", (collection_id,)).fetchone() is None:
            raise LookupError("Collection not found")
        if not conn.execute("UPDATE notebooks SET collection_id = ? WHERE id = ?", (collection_id, notebook_id)).rowcount:
            raise LookupError("Notebook not found")
    return {"id": notebook_id, "collection_id": collection_id}
