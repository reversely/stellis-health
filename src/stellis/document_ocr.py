"""Local text recognition for one account's scanned document pages."""

from __future__ import annotations

import json
from contextlib import contextmanager
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import threading
import time

from stellis import documents, runtime_trace, storage

TIMEOUT = 30
_LOCK = threading.Lock()


class OcrError(ValueError):
    """A page could not be read and can be retried."""


@contextmanager
def _slot():
    """Allows one OCR worker across account processes on the same host."""
    import fcntl
    if not _LOCK.acquire(blocking=False):
        raise documents.DocumentConflict("Another page is being read")
    descriptor = None
    try:
        root = pathlib.Path(os.environ.get("STELLIS_AUTH_DB_DIR") or storage.data_dir())
        descriptor = os.open(root / ".ocr.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise documents.DocumentConflict("Another page is being read") from exc
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _LOCK.release()


def read_page(document_id: str, number: int) -> dict[str, object]:
    """Reads one page without replacing embedded or reviewed text."""
    record = documents.get(document_id)
    page = documents.get_page(record, number)
    if page["text_source"] in {"embedded", "manual"} and page["text"]:
        raise ValueError("This page already has text")
    executable = shutil.which(os.environ.get("STELLIS_TESSERACT", "tesseract"))
    if executable is None:
        raise OcrError("Text recognition is unavailable on this server")
    with _slot():
        with runtime_trace.span("document_ocr", model="tesseract", provider="local",
                                execution="Local OCR"):
            started = time.monotonic()
            runtime_trace.event("page_started", page_number=number)
            path, media_type = documents.file_path(document_id)
            try:
                text = _recognize(path, media_type, number, executable)
            except OcrError:
                documents.page_failed(document_id, number, page["revision"])
                raise
            result = documents.save_page(document_id, number, text,
                                         page["revision"], source="ocr")
            runtime_trace.event("page_completed", page_number=number,
                                character_count=len(text),
                                elapsed_ms=round((time.monotonic() - started) * 1000))
    from stellis import document_naming
    return document_naming.enrich(document_id)


def _recognize(path: pathlib.Path, media_type: str, number: int,
               executable: str) -> str:
    worker = pathlib.Path(__file__).with_name("document_ocr_worker.py")
    try:
        process = subprocess.Popen(
            [sys.executable, str(worker), str(path), media_type, str(number), executable],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        raise OcrError("Text recognition could not start") from exc
    try:
        output, _ = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise OcrError("Text recognition timed out on this page") from exc
    if process.returncode or len(output) > 1_000_000:
        raise OcrError("This page could not be read")
    try:
        text = json.loads(output)["text"]
        if not isinstance(text, str) or len(text) > documents.MAX_PAGE_TEXT:
            raise ValueError()
        return text
    except (ValueError, KeyError, TypeError) as exc:
        raise OcrError("This page could not be read") from exc
