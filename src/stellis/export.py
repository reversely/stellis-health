"""Folder export of pages and conversations as a ZIP of Markdown files (ADR 0058, #284)."""

from __future__ import annotations

import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from stellis import images, storage, todos

FORMATS = ("markdown", "stellis")
SCOPES = ("collection", "notebook", "all")
NO_NOTEBOOK = "No notebook"
MANIFEST = "stellis-export.json"
MANIFEST_VERSION = 1
MAX_NAME = 100
_IMAGE_REFERENCE = re.compile(r"!\[([^\]\n]*)\]\(stellis-image:([0-9a-f-]{36})\)", re.IGNORECASE)
_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')
_SUMMARY_FIELDS = (("overview", "Overview"), ("topics", "Topics"), ("decisions", "Decisions"),
                   ("action_items", "Action proposals"))


def safe_name(value: str, taken: set[str], suffix: str = "") -> str:
    """A file or folder name with no separators or leading dots, capped in length and unique in `taken`.

    `taken` holds lower-cased names already used in the same folder, so collisions on case-insensitive
    file systems are also resolved. The chosen name is added to it.
    """
    base = " ".join(_UNSAFE.sub(" ", value).split()).strip(" .") or "Untitled"
    base = base[:MAX_NAME].rstrip(" .") or "Untitled"
    name, count = base + suffix, 1
    while name.lower() in taken:
        count += 1
        tail = f" ({count})"
        name = base[:MAX_NAME - len(tail)].rstrip(" .") + tail + suffix
    taken.add(name.lower())
    return name


def _summary_markdown(conn, conversation_id: str) -> tuple[str, str | None]:
    """The latest finished Summary: the user's edited text, else the generated points as Markdown."""
    row = conn.execute("""SELECT id, result_json, edited_markdown FROM enhanced_notes_runs
        WHERE conversation_id = ? AND state = 'succeeded' ORDER BY created_at DESC LIMIT 1""",
                       (conversation_id,)).fetchone()
    if row is None:
        return "", None
    if row["edited_markdown"] is not None:
        return str(row["edited_markdown"]), str(row["id"])
    result = json.loads(str(row["result_json"] or "{}"))
    parts = [f"## {title}\n\n" + "\n".join(f"- {item['text']}" for item in result[field])
             for field, title in _SUMMARY_FIELDS if result.get(field)]
    return "\n\n".join(parts), str(row["id"])


def _demote(markdown: str) -> str:
    """Move headings one level down so a Summary nests under its own heading; code fences are untouched."""
    lines = markdown.split("\n")
    return "\n".join(f"#{line}" if not fenced and line.startswith("#") else line
                     for line, fenced in zip(lines, todos._fence_flags(lines)))


def _collect(conn, scope: str, item_id: str | None) -> dict[str, object]:
    collections = {str(r["id"]): dict(r) for r in conn.execute(
        "SELECT id, name, color, created_at FROM collections ORDER BY created_at, rowid")}
    notebooks = {str(r["id"]): dict(r) for r in conn.execute(
        "SELECT id, name, color, created_at, collection_id FROM notebooks ORDER BY created_at, rowid")}
    if scope == "collection":
        if item_id not in collections:
            raise LookupError("Collection not found")
        notebooks = {key: book for key, book in notebooks.items() if book["collection_id"] == item_id}
        collections = {item_id: collections[item_id]}
    elif scope == "notebook":
        if item_id not in notebooks:
            raise LookupError("Notebook not found")
        notebooks = {item_id: notebooks[item_id]}
        collections = {key: value for key, value in collections.items() if key == notebooks[item_id]["collection_id"]}
    where, args = "", ()
    if scope != "all":
        where = f" AND notebook_id IN ({','.join('?' * len(notebooks))})" if notebooks else " AND 0"
        args = tuple(notebooks)
    tags = {"page": {}, "conversation": {}}
    for kind, table, column in (("page", "page_categories", "page_id"),
                                ("conversation", "conversation_categories", "conversation_id")):
        for row in conn.execute(f"SELECT t.{column} AS owner, c.id, c.name FROM {table} t "
                                "JOIN categories c ON c.id = t.category_id ORDER BY c.name"):
            tags[kind].setdefault(str(row["owner"]), []).append({"id": row["id"], "name": row["name"]})
    pages = [dict(r) | {"kind": "page"} for r in conn.execute(
        "SELECT id, title, markdown, revision, notebook_id, created_at FROM pages "
        f"WHERE trashed_at IS NULL{where} ORDER BY created_at, rowid", args)]
    conversations = []
    for row in conn.execute("SELECT c.id, c.title, c.notebook_id, c.created_at, n.markdown, n.revision "
                            "FROM conversations c LEFT JOIN meeting_notes n ON n.conversation_id = c.id "
                            f"WHERE c.trashed_at IS NULL{where.replace('notebook_id', 'c.notebook_id')} "
                            "ORDER BY c.created_at, c.rowid", args):
        summary, run_id = _summary_markdown(conn, str(row["id"]))
        conversations.append({"kind": "conversation", "id": row["id"], "title": row["title"],
                              "notebook_id": row["notebook_id"], "created_at": row["created_at"],
                              "markdown": row["markdown"] or "", "revision": row["revision"] or 0,
                              "summary": summary, "summary_run_id": run_id})
    for item in pages + conversations:
        item["tags"] = tags[str(item["kind"])].get(str(item["id"]), [])
    return {"collections": collections, "notebooks": notebooks, "items": pages + conversations}


def _image_file(conn, image_id: str) -> Path | None:
    for table in ("page_images", "conversation_images"):
        row = conn.execute(f"SELECT id, media_type FROM {table} WHERE id = ?", (image_id.lower(),)).fetchone()
        if row:
            path = images.path_for(dict(row))
            return path if path.is_file() else None
    return None


def _document(item: dict[str, object], body: str, summary: str) -> str:
    text = f"# {item['title']}\n\n"
    if item["kind"] == "page":
        return text + body.strip("\n") + "\n"
    return (text + "## My Notes\n\n" + (body.strip("\n") or "_No notes._") + "\n\n## Summary\n\n"
            + (_demote(summary).strip("\n") or "_No summary._") + "\n")


def build(scope: str, item_id: str | None, export_format: str) -> Path:
    """Write the export ZIP to a temporary file in the data directory and return its path.

    The caller deletes the file. Images are streamed from disk into the archive, so memory use does
    not grow with their size.
    """
    if scope not in SCOPES or export_format not in FORMATS:
        raise ValueError("Choose a valid export scope and format")
    plain = export_format == "markdown"
    with storage.connect() as conn:
        data = _collect(conn, scope, item_id)
        descriptor, name = tempfile.mkstemp(prefix=".export-", suffix=".zip", dir=storage.data_dir())
        archive_path = Path(name)
        try:
            with open(descriptor, "wb") as handle, zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
                manifest = _write(conn, archive, data, plain, scope, item_id, export_format)
                if not plain:
                    archive.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
        except BaseException:
            archive_path.unlink(missing_ok=True)
            raise
    return archive_path


def _write(conn, archive: zipfile.ZipFile, data: dict, plain: bool, scope: str, item_id: str | None,
           export_format: str) -> dict[str, object]:
    top: set[str] = {NO_NOTEBOOK.lower()}
    folders: dict[str | None, str] = {None: NO_NOTEBOOK}
    parents: dict[str, str] = {}
    for collection_id, collection in data["collections"].items():
        parents[collection_id] = safe_name(str(collection["name"]), top)
    inner: dict[str, set[str]] = {key: set() for key in parents}
    for notebook_id, book in data["notebooks"].items():
        parent = parents.get(str(book["collection_id"]))
        if parent:
            folders[notebook_id] = f"{parent}/{safe_name(str(book['name']), inner[str(book['collection_id'])])}"
        else:
            folders[notebook_id] = safe_name(str(book["name"]), top)
    used: dict[str, set[str]] = {}
    written_images: set[str] = set()
    entries = []
    for item in data["items"]:
        folder = folders.get(item["notebook_id"], NO_NOTEBOOK)
        names = used.setdefault(folder, set())
        path = f"{folder}/{safe_name(str(item['title']), names, '.md')}"
        referenced: list[dict[str, str]] = []

        def relink(match: re.Match) -> str:
            alt, image_id = match.group(1), match.group(2).lower()
            source = _image_file(conn, image_id)
            if source is None:
                return match.group(0) if not plain else alt
            target = f"{folder}/images/{source.name}"
            if target not in written_images:
                written_images.add(target)
                archive.write(source, target, compress_type=zipfile.ZIP_STORED)
            if not any(entry["id"] == image_id for entry in referenced):
                referenced.append({"id": image_id, "path": target})
            return match.group(0) if not plain else f"![{alt}](images/{source.name})"

        def convert(text: str) -> str:
            # The plain format strips IDs first, then relinks; Stellis format keeps text verbatim and
            # only stages the image files so the manifest can map each stellis-image ID to its file.
            return _IMAGE_REFERENCE.sub(relink, todos.plain_markdown(text) if plain else text)

        archive.writestr(path, _document(item, convert(str(item["markdown"])), convert(str(item.get("summary", "")))))
        entries.append({"kind": item["kind"], "id": item["id"], "title": item["title"], "path": path,
                        "notebook_id": item["notebook_id"], "tags": item["tags"], "revision": item["revision"],
                        "created_at": item["created_at"], "images": referenced,
                        **({"summary_run_id": item["summary_run_id"]} if item["kind"] == "conversation" else {})})
    notebook_list = [{"id": key, "name": book["name"], "color": book["color"], "collection_id": book["collection_id"],
                      "folder": folders[key]} for key, book in data["notebooks"].items()]
    return {"version": MANIFEST_VERSION, "format": export_format, "scope": scope, "scope_id": item_id,
            "exported_at": datetime.now(timezone.utc).isoformat(), "layout": "title-heading",
            "collections": [{"id": key, "name": value["name"], "color": value["color"], "folder": parents[key]}
                            for key, value in data["collections"].items()],
            "notebooks": notebook_list, "items": entries}


def archive_name(scope: str, item_id: str | None, export_format: str) -> str:
    """Download name: the collection or notebook name, or `stellis`, then the format."""
    label = "stellis"
    if scope != "all":
        with storage.connect() as conn:
            row = conn.execute(f"SELECT name FROM {'collections' if scope == 'collection' else 'notebooks'} WHERE id = ?",
                               (item_id,)).fetchone()
        label = safe_name(str(row["name"]), set()) if row else label
    return f"{label}-{export_format}.zip"
