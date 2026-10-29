"""Debug data an account keeps when its owner opts in.

While the account's "Keep debug data" setting is on, Stellis saves each
intake call before a reset, the audio and text of each spoken answer, and
the model's replies and notes, under ``<data dir>/debug``, and pauses the
30-day sweep of Recently Deleted. The owner downloads it from settings.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import threading
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from stellis import storage

_LOCK = threading.Lock()


def enabled() -> bool:
    """Whether this process's account has opted in."""
    from stellis import auth
    if not auth.enabled():
        return False
    try:
        return auth.keeps_debug_data(int(os.environ.get("STELLIS_ACCOUNT_ID", "1")))
    except (LookupError, ValueError):
        return False


def directory() -> Path:
    return storage.data_dir() / "debug"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def record(kind: str, data: dict[str, object]) -> None:
    """Appends one event to ``<kind>.jsonl`` when the account has opted in."""
    if not enabled():
        return
    folder = directory()
    folder.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": _stamp(), **data}, ensure_ascii=False)
    with _LOCK, (folder / f"{kind}.jsonl").open("a", encoding="utf-8") as log:
        log.write(line + "\n")


def save_answer_audio(samples: np.ndarray, rate: int, heard: str) -> None:
    """Saves one spoken answer as 16-bit WAV with the text heard from it."""
    if not enabled():
        return
    folder = directory() / "intake-audio"
    folder.mkdir(parents=True, exist_ok=True)
    name = _stamp().replace(":", "-") + ".wav"
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(str(folder / name), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm)
    record("intake-answers", {"audio": name, "heard": heard})


def archive() -> bytes:
    """Returns the debug folder and the current intake call as a zip file."""
    from stellis import intake
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("intake-call.json", json.dumps(intake.load(), indent=2, ensure_ascii=False))
        latency = storage.data_dir() / "intake-latency.jsonl"
        if latency.exists():
            bundle.write(latency, "intake-latency.jsonl")
        folder = directory()
        if folder.exists():
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    bundle.write(path, str(path.relative_to(folder)))
    return buffer.getvalue()


def erase() -> None:
    """Deletes the saved debug data, for account deletion."""
    shutil.rmtree(directory(), ignore_errors=True)
