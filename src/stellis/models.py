"""Download the speech models on first run, verified by SHA-256, with progress the app can show.

The base models make the final transcript; the live pair from `live_asr_models` runs the
recording preview. Both install in one pass, base models first, so final transcripts never wait
on the larger live download.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path

from stellis import live_asr_models, storage

# name, download URL, SHA-256 of the installed file, member to extract from an archive (if any)
MODELS = (
    ("ggml-small.en.bin", "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin",
     "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d", None),
    ("pyannote-segmentation-3.0-int8.onnx",
     "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
     "d582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d", "sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx"),
    ("nemo-en-titanet-small.onnx",
     "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx",
     "ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e", None),
)

_STATE = {"state": "idle", "model": None, "progress": 0.0, "error": None,
          "stage": None, "downloaded_bytes": 0, "total_bytes": None}
_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None


def directory() -> Path:
    # Speech models belong to the install, not the records: the example workspace shares them.
    return storage.base_dir() / "models"


def _installed_path(name: str) -> Path:
    from stellis.processing import model_paths

    overrides = {path.name: path for path in model_paths()}
    return overrides.get(name, directory() / name)


def live_paths() -> tuple[Path, Path]:
    """The streaming and offline Parakeet folders: configured paths, else the shared models folder."""
    root = directory() / "live-asr"
    (online, _), (offline, _) = live_asr_models.MODELS
    return (Path(os.environ.get("STELLIS_ONLINE_ASR_MODEL_DIR") or root / online).expanduser(),
            Path(os.environ.get("STELLIS_PARAKEET_MODEL_DIR") or root / offline).expanduser())


def missing() -> list[str]:
    """Models absent from both their configured path and the models folder."""
    base = [name for name, _, _, _ in MODELS
            if not (directory() / name).is_file() and not _installed_path(name).is_file()]
    live = [name for (name, _), path in zip(live_asr_models.MODELS, live_paths())
            if not live_asr_models.present(name, path)]
    return base + live


def status() -> dict[str, object]:
    with _LOCK:
        state = dict(_STATE)
    if not missing():
        state.update(state="ready", progress=1.0, model=None, error=None, stage=None)
    return state


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _fetch(url: str, target: Path, index: int, count: int) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, target.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with _LOCK:
            _STATE.update(stage="receiving", total_bytes=total or None)
        for block in iter(lambda: response.read(1 << 18), b""):
            output.write(block)
            done += len(block)
            with _LOCK:
                _STATE.update(
                    downloaded_bytes=done,
                    progress=min(1.0, (index + done / total) / count) if total else None)


def install() -> None:
    """Download each missing model; stop at the first failure and leave nothing half-written."""
    try:
        folder = directory()
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        absent = set(missing())
        needed = [model for model in MODELS if model[0] in absent]
        live = [name for name, _ in live_asr_models.MODELS if name in absent]
        steps = len(needed) + len(live)
        for index, (name, url, digest, member) in enumerate(needed):
            with _LOCK:
                _STATE.update(state="downloading", model=name, error=None,
                              stage="connecting", downloaded_bytes=0, total_bytes=None,
                              progress=index / steps)
            with tempfile.TemporaryDirectory(dir=folder) as work:
                download = Path(work) / "download"
                _fetch(url, download, index, steps)
                candidate = download
                if member:
                    with tarfile.open(download) as archive:
                        archive.extract(member, work, filter="data")
                    candidate = Path(work) / member
                with _LOCK:
                    _STATE["stage"] = "verifying"
                if _digest(candidate) != digest:
                    raise RuntimeError(f"{name} did not match its published checksum")
                candidate.chmod(0o600)
                shutil.move(candidate, folder / name)
        if needed:
            _queue_waiting_conversations()
        if live:
            _install_live(live, len(needed), steps)
        with _LOCK:
            _STATE.update(state="ready", model=None, progress=1.0, error=None, stage=None)
    except Exception as exc:  # Network and disk errors remain available until the person retries.
        with _LOCK:
            _STATE.update(state="failed", error=str(exc) or "Download failed")


def _install_live(names: list[str], done: int, steps: int) -> None:
    """Install the live pair into the shared folder; a configured folder elsewhere is the operator's."""
    online, offline = live_paths()
    root = directory() / "live-asr"
    if online.parent != root or offline.parent != root:
        raise RuntimeError("Live preview models are configured outside the models folder")

    def report(name: str, received: int, total: int | None) -> None:
        index = done + names.index(name) if name in names else done
        with _LOCK:
            _STATE.update(state="downloading", model=name, stage="receiving", downloaded_bytes=received,
                          total_bytes=total, progress=min(1.0, (index + received / total) / steps) if total else None)

    with _LOCK:
        _STATE.update(state="downloading", model=names[0], stage="connecting", downloaded_bytes=0,
                      total_bytes=None, progress=done / steps)
    live_asr_models.install(root, report)


def _queue_waiting_conversations() -> None:
    """Transcribe recordings imported while the models were still downloading."""
    from stellis import processing

    for item in storage.list_conversations():
        conversation_id = str(item["id"])
        if storage.get_transcript(conversation_id) is None and storage.get_latest_job_for_conversation(conversation_id) is None:
            processing.request_processing(conversation_id)


def ensure_in_background() -> None:
    global _THREAD
    if not missing():
        return
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return
        _STATE.update(state="downloading", progress=0.0, error=None, stage="connecting",
                      downloaded_bytes=0, total_bytes=None)
        _THREAD = threading.Thread(target=install, name="stellis-models", daemon=True)
        _THREAD.start()
