"""Uploaded documents: PDFs and images filed into a notebook, with page text.

Digital PDFs provide text on upload. Scanned pages and images offer local
recognition and revision-checked corrections. Each page records how its text
was obtained. The account retains the original file.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import pathlib
import re
import uuid

from stellis import storage

MAX_BYTES = 20_000_000
MAX_LABEL = "20 MB"
MAX_PAGES = 300
MAX_TITLE = 180
MAX_PAGE_TEXT = 100_000
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

_PDF = "application/pdf"
_SIGNATURES = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}
# A line shorter than this share of the page's longest line ends a paragraph.
_PARAGRAPH_END = 0.7
_HEADING_CHARS = 60
_LIST_ITEM = re.compile(r"^(\d+[.)]|[-*•])\s")
_COLUMN_GAP = re.compile(r"\S {3,}\S")
_SUMMARY_COLUMNS = ("id, title, filename, media_type, page_count, status, notebook_id, "
                    "record_date, created_at")


class DocumentConflict(ValueError):
    """A page changed while an operation was in progress."""


class DocumentError(ValueError):
    """An upload cannot become a document. The message is safe to show."""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _create_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            media_type TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            size INTEGER NOT NULL,
            page_count INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('ready', 'needs_text')),
            notebook_id TEXT REFERENCES notebooks(id) ON DELETE SET NULL,
            record_date TEXT,
            created_at TEXT NOT NULL)""")
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(documents)")
    }
    if "record_date" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN record_date TEXT")
    for name, definition in {"title_source":"TEXT NOT NULL DEFAULT 'manual'", "title_revision":"INTEGER NOT NULL DEFAULT 0"}.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS document_pages (
            document_id TEXT NOT NULL
                REFERENCES documents(id) ON DELETE CASCADE,
            page_number INTEGER NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY (document_id, page_number))""")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(document_pages)")}
    for name, definition in {
            "text_source": "TEXT NOT NULL DEFAULT 'embedded'",
            "ocr_status": "TEXT NOT NULL DEFAULT 'idle'",
            "revision": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE document_pages ADD COLUMN {name} {definition}")
    conn.execute("UPDATE document_pages SET text_source = 'none' "
                 "WHERE text = '' AND text_source = 'embedded'")


def _directory() -> pathlib.Path:
    path = storage.data_dir() / "documents"
    path.mkdir(exist_ok=True, mode=0o700)
    return path


def _require_notebook(conn, notebook_id: str | None) -> None:
    if notebook_id is None:
        return
    row = conn.execute("SELECT 1 FROM notebooks WHERE id = ?",
                       (notebook_id,)).fetchone()
    if row is None:
        raise LookupError("Notebook not found")


def title_from_filename(filename: str) -> str:
    """Returns a display title: the file name's stem with spacing tidied."""
    stem = pathlib.Path(filename).stem.replace("_", " ")
    return (" ".join(stem.split()) or "Document")[:MAX_TITLE]


def _starts_line(raw: str, line: str) -> bool:
    """Whether a line begins a new line of its own rather than continuing.

    Headings such as "Plan:", list items such as "2. Fluids" and table rows,
    whose columns are padded with runs of spaces, each keep their own line.
    """
    return bool((line.endswith(":") and len(line) <= _HEADING_CHARS) or
                _LIST_ITEM.match(line) or _COLUMN_GAP.search(raw.strip()))


def _ends_line(raw: str, line: str) -> bool:
    """Whether the next line must start fresh: after a heading or table row."""
    return bool((line.endswith(":") and len(line) <= _HEADING_CHARS) or
                _COLUMN_GAP.search(raw.strip()))


def clean_page_text(text: str) -> str:
    """Rejoins lines a PDF wrapped and keeps paragraph breaks.

    Args:
        text: Text extracted from one PDF page.

    Returns:
        The page text with spacing collapsed within lines. A line much
        shorter than the page's longest line ends a paragraph, and headings,
        list items and table rows keep their own lines.
    """
    raws = [raw for raw in text.splitlines() if raw.strip()]
    if not raws:
        return ""
    lines = [" ".join(raw.split()) for raw in raws]
    paragraph_end = _PARAGRAPH_END * max(len(line) for line in lines)
    joined = lines[0]
    for index in range(1, len(lines)):
        previous, line = lines[index - 1], lines[index]
        separate = (len(previous) < paragraph_end or
                    _starts_line(raws[index], line) or
                    _ends_line(raws[index - 1], previous))
        joined += ("\n" if separate else " ") + line
    return joined


def check_upload(filename: str, content: bytes) -> str:
    """Validates an upload's type and size.

    Args:
        filename: The uploaded file's name.
        content: The file's bytes.

    Returns:
        The file's media type.

    Raises:
        DocumentError: The file is empty, too large, of another type, or its
            first bytes do not match its extension.
    """
    suffix = pathlib.PurePath(filename or "").suffix.lower()
    media_type = MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise DocumentError("Choose a PDF, PNG or JPEG file")
    if not content:
        raise DocumentError("The file is empty")
    if len(content) > MAX_BYTES:
        raise DocumentError(f"Upload a document of at most {MAX_LABEL}")
    if not content.startswith(_SIGNATURES[media_type]):
        raise DocumentError(f"This file is not a {suffix[1:].upper()} file")
    return media_type


def _read_pdf_pages(content: bytes) -> list[str]:
    import pymupdf  # Deferred: only PDF uploads need it.
    try:
        pdf = pymupdf.open(stream=content, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise DocumentError("This PDF could not be read") from exc
    with pdf:
        if pdf.needs_pass:
            raise DocumentError("Remove the PDF password and upload it again")
        if pdf.page_count == 0:
            raise DocumentError("This PDF could not be read")
        if pdf.page_count > MAX_PAGES:
            raise DocumentError(f"Upload a PDF of at most {MAX_PAGES} pages")
        try:
            return [clean_page_text(page.get_text("text")) for page in pdf]
        except (RuntimeError, ValueError) as exc:
            raise DocumentError("This PDF could not be read") from exc


def _check_image(content: bytes) -> None:
    from PIL import Image  # Deferred: only image uploads need it.
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
    except (Image.UnidentifiedImageError, OSError, ValueError) as exc:
        raise DocumentError("This image could not be read") from exc


def _record(conn, row) -> dict[str, object]:
    record = {
        key: row[key] for key in ("id", "title", "filename", "media_type",
                                 "size", "page_count", "status", "notebook_id",
                                 "record_date", "created_at", "title_source", "title_revision")
    }
    pages = conn.execute(
        "SELECT page_number, text, text_source, ocr_status, revision FROM document_pages "
        "WHERE document_id = ? ORDER BY page_number", (row["id"],))
    record["pages"] = [{
        "number": page["page_number"],
        "text": page["text"],
        "text_source": page["text_source"],
        "ocr_status": page["ocr_status"],
        "revision": page["revision"],
    } for page in pages]
    from stellis import document_dates
    record["date_suggestion"] = document_dates.suggest(record["pages"])
    # One labelled date that outranks the rest is the record's date: saving it takes no confirm
    # (owner: no date confirms for a clear date). Conflicting dates still ask.
    suggestion = record["date_suggestion"]
    if not record.get("record_date") and suggestion["status"] == "suggested" and suggestion["date"]:
        conn.execute("UPDATE documents SET record_date = ? WHERE id = ? AND record_date IS NULL",
                     (suggestion["date"], row["id"]))
        record["record_date"] = suggestion["date"]
    from stellis import document_naming
    record["naming"] = document_naming.status(conn, row)
    return record


def create(filename: str,
           content: bytes,
           notebook_id: str | None = None,
           record_date: str | None = None) -> tuple[dict[str, object], bool]:
    """Stores an uploaded PDF or image with its page text.

    Args:
        filename: The uploaded file's name.
        content: The file's bytes.
        notebook_id: The notebook to file the document in, or None.
        record_date: The date the document describes, as YYYY-MM-DD, or None.

    Returns:
        The document, with its pages, and whether the account already had the
        same file. A repeated upload returns the stored document unchanged.

    Raises:
        DocumentError: The file cannot become a document.
        LookupError: The notebook does not exist.
    """
    media_type = check_upload(filename, content)
    digest = hashlib.sha256(content).hexdigest()
    with storage.connect() as conn:
        _create_tables(conn)
        existing = conn.execute("SELECT * FROM documents WHERE sha256 = ?",
                                (digest,)).fetchone()
        if existing:
            return _record(conn, existing), True
        _require_notebook(conn, notebook_id)
    if media_type == _PDF:
        pages = _read_pdf_pages(content)
    else:
        _check_image(content)
        pages = [""]
    source_title = title_from_filename(filename)
    title_source = "filename"
    if media_type == _PDF:
        import pymupdf
        try:
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                metadata_title = " ".join((pdf.metadata.get("title") or "").split())
                if 1 <= len(metadata_title) <= MAX_TITLE:
                    source_title = metadata_title
                    title_source = "metadata"
        except (pymupdf.FileDataError, RuntimeError, ValueError, TypeError):
            pass  # The validated document retains its filename-derived title.
    document_id = str(uuid.uuid4())
    path = _directory() / (document_id +
                           pathlib.PurePath(filename).suffix.lower())
    path.write_bytes(content)
    path.chmod(0o600)
    with storage.connect() as conn:
        _create_tables(conn)
        conn.execute(
            """
            INSERT INTO documents (id, title, filename, media_type, sha256,
                size, page_count, status, notebook_id, record_date, created_at, title_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (document_id, source_title,
             pathlib.PurePath(filename).name[:255], media_type, digest,
             len(content), len(pages), "ready" if any(pages) else "needs_text",
             notebook_id, record_date, _now(), title_source))
        conn.executemany(
            "INSERT INTO document_pages (document_id, page_number, text, text_source) "
            "VALUES (?, ?, ?, ?)", [(document_id, number, text,
                                     "embedded" if text else "none")
                                 for number, text in enumerate(pages, 1)])
    from stellis import document_naming
    return document_naming.enrich(document_id), False


def get(document_id: str) -> dict[str, object]:
    """Returns one document with its pages.

    Raises:
        LookupError: The document does not exist.
    """
    with storage.connect() as conn:
        _create_tables(conn)
        row = conn.execute("SELECT * FROM documents WHERE id = ?",
                           (document_id,)).fetchone()
        if row is None:
            raise LookupError("Document not found")
        return _record(conn, row)


def file_path(document_id: str) -> tuple[pathlib.Path, str]:
    """Returns the stored file's path and media type.

    Raises:
        LookupError: The document does not exist.
    """
    record = get(document_id)
    media_type = str(record["media_type"])
    suffix = pathlib.PurePath(str(record["filename"])).suffix.lower()
    return _directory() / (document_id + suffix), media_type


def list_all(notebook_id: str | None = None) -> list[dict[str, object]]:
    """Lists documents without page text, newest record date first.

    Args:
        notebook_id: Limits the list to one notebook when given.

    Returns:
        One dict per document. Documents without a record date sort by upload
        time.
    """
    where, arguments = ("WHERE (notebook_id = ? OR EXISTS (SELECT 1 FROM notebook_record_links l WHERE l.notebook_id = ? AND l.source_id = 'document:' || documents.id))",
                        (notebook_id, notebook_id)) if notebook_id else ("", ())
    with storage.connect() as conn:
        _create_tables(conn)
        rows = [dict(row) for row in conn.execute(
            f"SELECT {_SUMMARY_COLUMNS} FROM documents {where} "
            "ORDER BY COALESCE(record_date, created_at) DESC", arguments)]
        # Filing links a record to notebooks without changing its own notebook_id.
        linked: dict[str, list[str]] = {}
        for link in conn.execute("SELECT notebook_id, source_id FROM notebook_record_links WHERE source_id LIKE 'document:%'"):
            linked.setdefault(link["source_id"].removeprefix("document:"), []).append(link["notebook_id"])
    for row in rows:
        row["notebook_ids"] = list(dict.fromkeys(([row["notebook_id"]] if row["notebook_id"] else []) + linked.get(row["id"], [])))
    return rows


def all_pages(document_ids: list[str] | None = None) -> list[dict[str, object]]:
    """Lists page text for search and chat, in document and page order.

    Args:
        document_ids: Limits the pages to these documents when given.

    Returns:
        One dict per page with ``document_id``, ``title``, ``notebook_id``,
        ``number`` and ``text``.
    """
    if document_ids is not None and not document_ids:
        return []
    where, arguments = "", ()
    if document_ids is not None:
        where = f"WHERE d.id IN ({','.join('?' * len(document_ids))})"
        arguments = tuple(document_ids)
    with storage.connect() as conn:
        _create_tables(conn)
        rows = conn.execute(
            f"""
            SELECT d.id AS document_id, d.title, d.notebook_id,
                   p.page_number AS number, p.text
            FROM documents d JOIN document_pages p ON p.document_id = d.id
            {where}
            ORDER BY d.created_at DESC, d.id, p.page_number""", arguments)
        return [dict(row) for row in rows]


def update(document_id: str,
           *,
           title: str | None = None,
           record_date: str | None = None,
           notebook_id: str | None = None,
           move: bool = False) -> dict[str, object]:
    """Renames, dates or moves a document.

    Args:
        document_id: The document to change.
        title: A new title, or None to keep it.
        record_date: A new record date as YYYY-MM-DD, or None to keep it.
        notebook_id: The notebook to move to when ``move`` is set; None files
            the document in no notebook.
        move: Whether to change the notebook.

    Returns:
        The document after the change.

    Raises:
        LookupError: The document or the notebook does not exist.
        ValueError: The title or the date is not valid.
    """
    changes: dict[str, object] = {}
    if title is not None:
        value = " ".join(title.split())
        if not 1 <= len(value) <= MAX_TITLE:
            raise ValueError(f"Enter a title of at most {MAX_TITLE} characters")
        changes["title"] = value
    if record_date is not None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record_date):
            raise ValueError("Enter the record date as YYYY-MM-DD")
        try:
            datetime.date.fromisoformat(record_date)
        except ValueError as exc:
            raise ValueError("Enter a valid calendar date") from exc
        changes["record_date"] = record_date
    if move:
        changes["notebook_id"] = notebook_id
    with storage.connect() as conn:
        _create_tables(conn)
        if conn.execute("SELECT 1 FROM documents WHERE id = ?",
                        (document_id,)).fetchone() is None:
            raise LookupError("Document not found")
        if move:
            _require_notebook(conn, notebook_id)
        for column, value in changes.items():
            # Column names come from the fixed keys above, never from input.
            if column == 'title':
                conn.execute("UPDATE documents SET title=?,title_source='manual',title_revision=title_revision+1 WHERE id=?",(value,document_id))
            else:
                conn.execute(f"UPDATE documents SET {column} = ? WHERE id = ?",(value,document_id))
    return get(document_id)


def delete(document_id: str) -> None:
    """Removes a document, its page text and its file.

    Raises:
        LookupError: The document does not exist.
    """
    path, _ = file_path(document_id)
    with storage.connect() as conn:
        conn.execute("DELETE FROM document_title_runs WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM document_pages WHERE document_id = ?",
                     (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    path.unlink(missing_ok=True)


def get_page(record: dict[str, object], number: int) -> dict[str, object]:
    """Returns a page of an already resolved account-local document."""
    for page in record["pages"]:
        if page["number"] == number:
            return page
    raise LookupError("Page not found")


def save_page(document_id: str, number: int, text: str, revision: int,
              *, source: str = "manual") -> dict[str, object]:
    """Saves page text and clears facts derived from its previous wording."""
    if len(text) > MAX_PAGE_TEXT:
        raise ValueError("Page text is too long")
    text = text.strip()
    with storage.connect() as conn:
        _create_tables(conn)
        row = conn.execute("SELECT text, revision FROM document_pages "
                           "WHERE document_id = ? AND page_number = ?",
                           (document_id, number)).fetchone()
        if row is None:
            raise LookupError("Page not found")
        if row["revision"] != revision:
            raise DocumentConflict("This page changed while you were working")
        changed = conn.execute("""UPDATE document_pages SET text = ?, text_source = ?,
                     ocr_status = ?, revision = revision + 1
                     WHERE document_id = ? AND page_number = ? AND revision = ?""",
                     (text, source if text else "none",
                      "ready" if text else "empty", document_id, number, revision)).rowcount
        if not changed:
            raise DocumentConflict("This page changed while you were working")
        if text != row["text"] and conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'health_facts'").fetchone():
            conn.execute("DELETE FROM health_facts WHERE document_id = ? AND page = ?",
                         (document_id, number))
        conn.execute("""UPDATE documents SET status = CASE WHEN EXISTS (
                     SELECT 1 FROM document_pages WHERE document_id = ? AND text != '')
                     THEN 'ready' ELSE 'needs_text' END WHERE id = ?""",
                     (document_id, document_id))
    if source=='manual':
        from stellis import document_naming
        return document_naming.enrich(document_id)
    return get(document_id)


def page_failed(document_id: str, number: int, revision: int) -> None:
    """Records a retryable failure without replacing a newer page revision."""
    with storage.connect() as conn:
        _create_tables(conn)
        changed = conn.execute("""UPDATE document_pages SET ocr_status = 'failed'
                              WHERE document_id = ? AND page_number = ? AND revision = ?""",
                              (document_id, number, revision)).rowcount
        if not changed:
            raise DocumentConflict("This page changed while you were working")
