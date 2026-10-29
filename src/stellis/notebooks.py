"""Named notebook workspaces with primary destinations and shared record links."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from stellis import storage, suggestions
from stellis.todos import markdown_inline_text

PALETTE = ("#495575", "#56465C", "#594F4F", "#547980", "#45ADA8")
# Seven added swatches; each holds white text at 4.5:1 or better.
PRESETS = PALETTE + ("#5B5F97", "#6B4E71", "#4F6F52", "#8A5A44", "#3F6C8C", "#7A4E5E", "#5E6B3F")
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")
_SECTIONS = {"overview": "overview", "topics": "topics", "decisions": "decisions", "action proposals": "action_items"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _name(value: str) -> str:
    name = " ".join(value.split())
    if not 1 <= len(name) <= 80:
        raise ValueError("Enter a notebook name of at most 80 characters")
    return name


def _color(value: str | None, fallback_index: int) -> str:
    if value is None:
        return PALETTE[fallback_index % len(PALETTE)]
    # Every preset is a #RRGGBB value, so one rule covers presets and custom colours.
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError("Choose a preset colour or a #RRGGBB hex value")
    return value.upper()


def _markdown_sections(markdown: str) -> dict[str, list[str]]:
    """Read the edited Summary back into its fields; headings match the editor's section titles."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        heading = re.match(r"^#{1,6}\s+(.*)$", line)
        if heading:
            current = _SECTIONS.get(heading.group(1).strip().casefold())
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        text = markdown_inline_text(bullet.group(1)) if bullet else ""
        if current and text:
            sections.setdefault(current, []).append(text)
    return sections


def summary_points(conversation_ids: list[str]) -> dict[str, dict[str, object]]:
    """Return the overview line, the rail topic line, and up to three progress points from each latest successful Summary."""
    if not conversation_ids:
        return {}
    marks = ",".join("?" for _ in conversation_ids)
    with storage.connect() as conn:
        rows = conn.execute(f"""SELECT conversation_id, result_json, edited_markdown FROM enhanced_notes_runs
            WHERE state = 'succeeded' AND conversation_id IN ({marks}) ORDER BY created_at""",
            conversation_ids).fetchall()
    points: dict[str, dict[str, object]] = {}
    for row in rows:
        if row["edited_markdown"]:
            fields = _markdown_sections(str(row["edited_markdown"]))
        else:
            result = json.loads(row["result_json"] or "{}")
            fields = {key: [str(item.get("text", "")) for item in result.get(key, []) if item.get("text")]
                      for key in _SECTIONS.values()}
        progress = (fields.get("decisions", []) + fields.get("action_items", [])) or fields.get("topics", [])
        overview = fields.get("overview", [])
        # Topics are short noun phrases, so the first one previews a conversation in the narrow rail.
        topic = (fields.get("topics") or overview or [None])[0]
        # Later rows overwrite earlier ones, so the newest successful run wins.
        points[str(row["conversation_id"])] = {"summary_line": overview[0] if overview else None,
                                               "topic_line": topic, "progress": progress[:3]}
    return points


def list_notebooks() -> list[dict[str, object]]:
    """List notebooks with their conversations newest first and the latest meeting's progress."""
    with storage.connect() as conn:
        books = [dict(row) for row in conn.execute("""SELECT n.id, n.name, n.color, n.created_at, n.collection_id, n.todo_list, n.template_id, n.description, n.catalogue_version, n.archived_at, n.capabilities_json, n.recommendation_evidence_json,
            c.todo_list AS collection_todo_list FROM notebooks n LEFT JOIN collections c ON c.id = n.collection_id
            WHERE n.archived_at IS NULL ORDER BY n.created_at""")]
        rows = conn.execute("""SELECT id, title, duration_ms, created_at, recorded_at, notebook_id FROM conversations
            WHERE notebook_id IS NOT NULL AND trashed_at IS NULL
            ORDER BY COALESCE(recorded_at, created_at) DESC, created_at DESC""").fetchall()
    with storage.connect() as conn:
        linked = conn.execute("""SELECT l.notebook_id, c.id, c.title, c.duration_ms, c.created_at, c.recorded_at
            FROM notebook_record_links l JOIN conversations c ON l.source_id = 'conversation:' || c.id
            WHERE c.trashed_at IS NULL AND (c.notebook_id IS NULL OR c.notebook_id != l.notebook_id)""").fetchall()
    rows = list(rows) + list(linked)
    from stellis import pages
    page_groups = pages.by_notebook()
    members: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        members.setdefault(str(row["notebook_id"]), []).append(dict(row))
    latest = [items[0]["id"] for items in members.values()]
    points = summary_points([str(item) for item in latest])
    for book in books:
        book["todo_enabled"] = suggestions.resolve_todo_list(book["todo_list"], book.pop("collection_todo_list"))
        items = members.get(str(book["id"]), [])
        book["conversations"] = items
        book["pages"] = page_groups.get(str(book["id"]), [])
        book["progress"] = points.get(str(items[0]["id"]), {}).get("progress", []) if items else []
    # The most recently active notebook leads.
    books.sort(key=lambda book: str((book["conversations"][0].get("recorded_at") or book["conversations"][0]["created_at"])
                                    if book["conversations"] else book["created_at"]), reverse=True)
    return books


def category_groups(limit: int = 5) -> list[dict[str, object]]:
    """The most used categories, shaped like notebooks, for the home page groups."""
    with storage.connect() as conn:
        rows = conn.execute("""SELECT c.id AS category_id, c.name AS category_name, v.id, v.title, v.duration_ms,
                v.created_at, v.recorded_at
            FROM categories c JOIN conversation_categories cc ON cc.category_id = c.id
            JOIN conversations v ON v.id = cc.conversation_id AND v.trashed_at IS NULL
            ORDER BY COALESCE(v.recorded_at, v.created_at) DESC, v.created_at DESC""").fetchall()
    groups: dict[str, dict[str, object]] = {}
    for row in rows:
        group = groups.setdefault(str(row["category_id"]), {"id": str(row["category_id"]), "name": str(row["category_name"]),
                                                            "conversations": []})
        group["conversations"].append({key: row[key] for key in ("id", "title", "duration_ms", "created_at", "recorded_at")})
    chosen = sorted(groups.values(), key=lambda group: (-len(group["conversations"]), str(group["name"]).casefold()))[:limit]
    points = summary_points([str(group["conversations"][0]["id"]) for group in chosen])
    for index, group in enumerate(chosen):
        group["color"] = PALETTE[index % len(PALETTE)]
        group["progress"] = points.get(str(group["conversations"][0]["id"]), {}).get("progress", [])
    return chosen


def create(name: str, color: str | None = None) -> dict[str, object]:
    value = _name(name)
    with storage.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM notebooks").fetchone()[0]
        record = {"id": str(uuid.uuid4()), "name": value, "color": _color(color, count), "created_at": _now()}
        try:
            conn.execute("INSERT INTO notebooks (id, name, normalized, color, created_at) VALUES (?, ?, ?, ?, ?)",
                         (record["id"], value, value.casefold(), record["color"], record["created_at"]))
        except sqlite3.IntegrityError as exc:
            raise ValueError("A notebook with this name already exists") from exc
    return record


def update(notebook_id: str, *, name: str | None = None, color: str | None = None,
           todo_list: str | None = None, description: str | None = None,
           archived: bool | None = None, capabilities: list[str] | None = None) -> dict[str, object]:
    with storage.connect() as conn:
        row = conn.execute("SELECT id, name, color, created_at, todo_list FROM notebooks WHERE id = ?", (notebook_id,)).fetchone()
        if row is None:
            raise LookupError("Notebook not found")
        if capabilities is not None:
            from stellis import notebook_health
            if any(key not in notebook_health.OPTIONAL for key in capabilities):
                raise ValueError("Choose registered notebook features")
            conn.execute("UPDATE notebooks SET capabilities_json = ? WHERE id = ?", (json.dumps(list(dict.fromkeys(capabilities))),notebook_id))
        if description is not None:
            if len(description) > 1000:
                raise ValueError("Keep the description under 1000 characters")
            conn.execute("UPDATE notebooks SET description = ? WHERE id = ?", (description.strip(), notebook_id))
        if archived is not None:
            conn.execute("UPDATE notebooks SET archived_at = ? WHERE id = ?", (_now() if archived else None, notebook_id))
        if todo_list is not None and todo_list not in suggestions.NOTEBOOK_TODO_LIST:
            raise ValueError("Choose inherit, on, or off for the to-do list")
        setting = todo_list if todo_list is not None else str(row["todo_list"])
        value = _name(name) if name is not None else str(row["name"])
        tint = _color(color, 0) if color is not None else str(row["color"])
        try:
            conn.execute("UPDATE notebooks SET name = ?, normalized = ?, color = ?, todo_list = ? WHERE id = ?",
                         (value, value.casefold(), tint, setting, notebook_id))
        except sqlite3.IntegrityError as exc:
            raise ValueError("A notebook with this name already exists") from exc
    return {"id": notebook_id, "name": value, "color": tint, "created_at": row["created_at"], "todo_list": setting,
            "todo_enabled": suggestions.todo_enabled(notebook_id)}


def delete(notebook_id: str) -> bool:
    """Remove a notebook; its conversations stay in the library without a notebook."""
    with storage.connect() as conn:
        conn.execute("UPDATE conversations SET notebook_id = NULL WHERE notebook_id = ?", (notebook_id,))
        conn.execute("UPDATE recording_sessions SET notebook_id = NULL WHERE notebook_id = ?", (notebook_id,))
        conn.execute("UPDATE pages SET notebook_id = NULL WHERE notebook_id = ?", (notebook_id,))
        return conn.execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,)).rowcount > 0


def exists(notebook_id: str) -> bool:
    with storage.connect() as conn:
        return conn.execute("SELECT 1 FROM notebooks WHERE id = ?", (notebook_id,)).fetchone() is not None


def assign(conversation_id: str, notebook_id: str | None) -> dict[str, object]:
    if notebook_id is not None and not exists(notebook_id):
        raise LookupError("Notebook not found")
    with storage.connect() as conn:
        changed = conn.execute("UPDATE conversations SET notebook_id = ? WHERE id = ? AND trashed_at IS NULL",
                               (notebook_id, conversation_id)).rowcount
    if not changed:
        raise LookupError("Conversation not found")
    return {"id": conversation_id, "notebook_id": notebook_id}
