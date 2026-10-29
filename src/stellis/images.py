"""Private, time-anchored screenshots for recordings and conversations."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from stellis import storage

MAX_UPLOAD_BYTES = 12_000_000
MAX_STORED_BYTES = 12_000_000
MAX_PIXELS = 20_000_000
MAX_SIDE = 8192
MAX_CAPTION = 280
ACCEPTED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF"}
# Animations (ADR 0040, 2026-10-18): at most this many frames, and frames x width x height at most
# MAX_ANIMATION_PIXELS, which bounds the memory used to decode one (about 300 frames of 450 x 450).
MAX_FRAMES = 300
MAX_ANIMATION_PIXELS = 60_000_000
EXTENSIONS = {"image/webp": "webp", "image/gif": "gif"}


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _directory() -> Path:
    root = storage.data_dir() / "images"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def _owner(conn, scope: str, item_id: str):
    if scope == "recording":
        row = conn.execute("SELECT id, state FROM recording_sessions WHERE id = ?", (item_id,)).fetchone()
        if not row or row["state"] != "recording":
            raise LookupError("Active recording not found")
    elif scope == "conversation":
        row = conn.execute("SELECT id, duration_ms FROM conversations WHERE id = ? AND trashed_at IS NULL",
                           (item_id,)).fetchone()
        if not row:
            raise LookupError("Conversation not found")
    elif scope == "page":
        row = conn.execute("SELECT id FROM pages WHERE id = ? AND trashed_at IS NULL", (item_id,)).fetchone()
        if not row:
            raise LookupError("Page not found")
    else:
        raise ValueError("Invalid image scope")
    return row


def _field(scope: str) -> str:
    return {"recording": "recording_id", "page": "page_id"}.get(scope, "conversation_id")


def _table(scope: str) -> str:
    # Page images are a separate table so conversation_images keeps its owner constraint (ADR 0058).
    return "page_images" if scope == "page" else "conversation_images"


_PUBLIC_KEYS = ("id", "media_position_ms", "segment_id", "start_offset", "end_offset", "quote", "caption",
                "width", "height", "size_bytes", "created_at", "updated_at")


def _public(row) -> dict[str, object]:
    # A page image row has no time or passage columns, so those keys are left out of its public form.
    columns = set(row.keys())
    return {key: row[key] for key in _PUBLIC_KEYS if key in columns}


def _animated(source: Image.Image, data: bytes) -> tuple[bytes, int, int, str]:
    """Re-encode a GIF or WebP animation frame by frame, keeping durations and loop count.

    Decoded frames are written to a fresh container, so EXIF, XMP, ICC, and GIF comment blocks in the
    upload are not stored. A GIF becomes lossless animated WebP, or a re-encoded GIF when that is smaller.
    """
    width, height = source.size
    if source.n_frames > MAX_FRAMES:
        raise ValueError(f"Animation can have at most {MAX_FRAMES} frames")
    if source.n_frames * width * height > MAX_ANIMATION_PIXELS:
        raise ValueError("Animation is too large")
    is_gif = source.format == "GIF"
    loop = int(source.info.get("loop", 1 if is_gif else 0))
    frames: list[Image.Image] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(source):
        frame.load()
        frames.append(frame.convert("RGBA"))
        durations.append(int(frame.info.get("duration", 100)))
    if all(frame.getextrema()[3][0] == 255 for frame in frames):
        frames = [frame.convert("RGB") for frame in frames]
    output = io.BytesIO()
    frames[0].save(output, format="WEBP", save_all=True, append_images=frames[1:], duration=durations,
                   loop=loop, lossless=True, method=4, exif=b"", xmp=b"", icc_profile=None)
    webp = output.getvalue()
    if not is_gif:
        return webp, width, height, "image/webp"
    # The GIF is re-saved rather than kept, because the upload may carry comment blocks.
    gif = io.BytesIO()
    frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:], duration=durations,
                   loop=loop, disposal=2)
    if len(webp) <= len(gif.getvalue()):
        return webp, width, height, "image/webp"
    return gif.getvalue(), width, height, "image/gif"


def _normalize(data: bytes) -> tuple[bytes, int, int, str]:
    """Return stored bytes, width, height, and media type. Stills become lossless WebP; animations keep frames."""
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Image must be smaller than 12 MB")
    try:
        with Image.open(io.BytesIO(data)) as source:
            animated = getattr(source, "is_animated", False)
            if source.format not in ACCEPTED_FORMATS or (source.format == "GIF" and not animated):
                raise ValueError("Use a PNG, JPEG, WebP, or animated GIF image")
            width, height = source.size
            if width < 1 or height < 1 or width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
                raise ValueError("Image dimensions are too large")
            if animated:
                return _animated(source, data)
            source.load()
            oriented = ImageOps.exif_transpose(source)
            # Re-encode decoded pixels: uploaded metadata and embedded profiles do not survive.
            pixels = oriented.convert("RGBA" if "A" in oriented.getbands() else "RGB")
            output = io.BytesIO()
            pixels.save(output, format="WEBP", lossless=True, method=4)
            encoded = output.getvalue()
            if len(encoded) > MAX_STORED_BYTES:
                raise ValueError("Image is too large after conversion")
            return encoded, pixels.width, pixels.height, "image/webp"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Image could not be opened") from exc


def list_images(scope: str, item_id: str) -> list[dict[str, object]]:
    with storage.connect() as conn:
        _owner(conn, scope, item_id)
        order = "created_at, id" if scope == "page" else "media_position_ms, created_at, id"
        rows = conn.execute(f"SELECT * FROM {_table(scope)} WHERE {_field(scope)} = ? ORDER BY {order}",
                            (item_id,)).fetchall()
    return [_public(row) for row in rows]


def add_page_image(page_id: str, data: bytes, caption: str = "") -> dict[str, object]:
    if len(caption.strip()) > MAX_CAPTION:
        raise ValueError("Caption is too long")
    normalized, width, height, media_type = _normalize(data)
    image_id = str(uuid.uuid4())
    directory = _directory()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".image-", dir=directory)
    temporary = Path(temporary_name)
    destination = directory / f"{image_id}.{EXTENSIONS[media_type]}"
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(normalized)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        with storage.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _owner(conn, "page", page_id)
            stamp = _stamp()
            os.replace(temporary, destination)
            conn.execute("""INSERT INTO page_images
                (id, page_id, caption, filename, media_type, size_bytes, width, height, sha256, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (image_id, page_id, caption.strip(), destination.name, media_type, len(normalized), width, height,
                 hashlib.sha256(normalized).hexdigest(), stamp, stamp))
        return get("page", page_id, image_id)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def add(scope: str, item_id: str, data: bytes, media_position_ms: int,
        segment_id: str | None = None, caption: str = "",
        start_offset: int | None = None, end_offset: int | None = None,
        expected_revision: int | None = None) -> dict[str, object]:
    if media_position_ms < 0 or media_position_ms > 86_400_000:
        raise ValueError("Invalid image time")
    if len(caption.strip()) > MAX_CAPTION:
        raise ValueError("Caption is too long")
    normalized, width, height, media_type = _normalize(data)
    image_id = str(uuid.uuid4())
    filename = f"{image_id}.{EXTENSIONS[media_type]}"
    directory = _directory()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".image-", dir=directory)
    temporary = Path(temporary_name)
    destination = directory / filename
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(normalized)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        with storage.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            owner = _owner(conn, scope, item_id)
            quote = None
            selected_range = any(value is not None for value in (start_offset, end_offset, expected_revision))
            if selected_range and (scope != "conversation" or not segment_id or
                                   start_offset is None or end_offset is None or expected_revision is None):
                raise ValueError("Select text within one passage")
            if scope == "conversation":
                if media_position_ms > int(owner["duration_ms"]):
                    raise ValueError("Image time exceeds recording length")
                if segment_id:
                    transcript = conn.execute("SELECT revision, segments_json FROM transcripts WHERE conversation_id = ?",
                                              (item_id,)).fetchone()
                    if selected_range and (transcript is None or int(transcript["revision"]) != expected_revision):
                        raise ValueError("Transcript changed. Reload and try again")
                    segments = json.loads(str(transcript["segments_json"])) if transcript else []
                    selected = next((segment for segment in segments if segment["id"] == segment_id), None)
                    if selected is None:
                        raise ValueError("Transcript passage changed; choose it again")
                    media_position_ms = int(selected["start_ms"])
                    if selected_range:
                        edit = conn.execute("SELECT text FROM transcript_edits WHERE conversation_id = ? AND segment_id = ?",
                                            (item_id, segment_id)).fetchone()
                        displayed = str(edit["text"]) if edit else str(selected["text"])
                        if start_offset < 0 or end_offset > len(displayed) or start_offset >= end_offset:
                            raise ValueError("Select text within one passage")
                        quote = displayed[start_offset:end_offset]
            elif segment_id:
                raise ValueError("A live image cannot use a final passage ID")
            stamp = _stamp()
            os.replace(temporary, destination)
            conn.execute(f"""INSERT INTO conversation_images
                (id, {_field(scope)}, media_position_ms, segment_id, start_offset, end_offset, quote, caption, filename, media_type,
                 size_bytes, width, height, sha256, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (image_id, item_id, media_position_ms, segment_id, start_offset, end_offset, quote, caption.strip(), filename,
                 media_type, len(normalized), width, height, hashlib.sha256(normalized).hexdigest(), stamp, stamp))
        return get(scope, item_id, image_id)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def get(scope: str, item_id: str, image_id: str) -> dict[str, object]:
    with storage.connect() as conn:
        _owner(conn, scope, item_id)
        row = conn.execute(f"SELECT * FROM {_table(scope)} WHERE id = ? AND {_field(scope)} = ?",
                           (image_id, item_id)).fetchone()
    if row is None:
        raise LookupError("Image not found")
    return dict(row)


def path_for(record: dict[str, object]) -> Path:
    image_id = str(uuid.UUID(str(record["id"])))
    return _directory() / f"{image_id}.{EXTENSIONS.get(str(record.get('media_type')), 'webp')}"


def update_caption(scope: str, item_id: str, image_id: str, caption: str) -> dict[str, object]:
    value = caption.strip()
    if len(value) > MAX_CAPTION:
        raise ValueError("Caption is too long")
    with storage.connect() as conn:
        _owner(conn, scope, item_id)
        changed = conn.execute(f"""UPDATE {_table(scope)} SET caption = ?, updated_at = ?
            WHERE id = ? AND {_field(scope)} = ?""", (value, _stamp(), image_id, item_id)).rowcount
    if not changed:
        raise LookupError("Image not found")
    return _public(get(scope, item_id, image_id))


def delete(scope: str, item_id: str, image_id: str) -> None:
    record = get(scope, item_id, image_id)
    with storage.connect() as conn:
        conn.execute(f"DELETE FROM {_table(scope)} WHERE id = ? AND {_field(scope)} = ?", (image_id, item_id))
    path_for(record).unlink(missing_ok=True)


def copy(image_id: str, scope: str, item_id: str) -> dict[str, object]:
    """Copy an image into another owner (a pasted `stellis-image` reference) and return the new image."""
    uuid.UUID(image_id)
    with storage.connect() as conn:
        source = None
        for table in ("conversation_images", "page_images"):
            source = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (image_id,)).fetchone()
            if source:
                break
        if source is None:
            raise LookupError("Image not found")
        owner = _owner(conn, scope, item_id)
    path = path_for(dict(source))
    if not path.is_file():
        raise LookupError("Image file is missing")
    caption = str(source["caption"])
    if scope == "page":
        return add_page_image(item_id, path.read_bytes(), caption)
    position = int(source["media_position_ms"]) if "media_position_ms" in source.keys() else 0
    if scope == "conversation":
        position = min(position, int(owner["duration_ms"]))
    return add(scope, item_id, path.read_bytes(), position, caption=caption)


def attach_to_conversation(recording_id: str, conversation_id: str, duration_ms: int) -> None:
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""UPDATE conversation_images SET conversation_id = ?, recording_id = NULL,
            media_position_ms = MIN(media_position_ms, ?), updated_at = ? WHERE recording_id = ?""",
                     (conversation_id, duration_ms, _stamp(), recording_id))


def remove_files(image_ids: list[str]) -> None:
    for image_id in image_ids:
        for extension in EXTENSIONS.values():
            (_directory() / f"{image_id}.{extension}").unlink(missing_ok=True)


def recover_orphans() -> None:
    with storage.connect() as conn:
        known = {str(row["id"]) for table in ("conversation_images", "page_images")
                 for row in conn.execute(f"SELECT id FROM {table}")}
    for file in _directory().iterdir():
        if file.name.startswith(".image-") or (file.suffix[1:] in EXTENSIONS.values() and file.stem not in known):
            file.unlink(missing_ok=True)
