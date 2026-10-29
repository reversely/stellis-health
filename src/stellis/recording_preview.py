"""Incremental provisional ASR from verified durable recording chunks."""

from __future__ import annotations

import atexit
import base64
import contextlib
import hashlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from stellis import recording, storage, tools

_LOCK = threading.Lock()
_WORKERS: dict[str, "_Worker"] = {}


def _snapshot(recording_id: str) -> tuple[list[dict[str, object]], int]:
    with storage.connect() as conn:
        session = conn.execute("SELECT state FROM recording_sessions WHERE id = ?", (recording_id,)).fetchone()
        if session is None:
            raise LookupError("Recording session not found")
        if session["state"] != "recording":
            raise RuntimeError("Recording is already finalized")
        rows = [dict(row) for row in conn.execute(
            "SELECT sequence, size_bytes, sha256 FROM recording_chunks WHERE recording_id = ? ORDER BY sequence",
            (recording_id,),
        )]
    if [row["sequence"] for row in rows] != list(range(len(rows))):
        raise RuntimeError("Recording chunks are incomplete")
    return rows, len(rows) - 1


def _stored(recording_id: str) -> dict[str, object]:
    path = recording._directory(recording_id) / "preview.json"
    if not path.exists():
        return {"state": "waiting", "through_sequence": -1, "segments": []}
    return json.loads(path.read_text())


def _binary() -> str:
    configured = os.environ.get("STELLIS_AUDIO_WORKER", "").strip()
    binary = configured or shutil.which("stellis-audio-worker")
    if not binary or not Path(binary).is_file():
        raise RuntimeError("Local live transcription worker unavailable")
    return binary


def _model_dir(name: str) -> str:
    from stellis import models

    online, offline = models.live_paths()
    value = str(online if name == "STELLIS_ONLINE_ASR_MODEL_DIR" else offline)
    if not Path(value).is_dir():
        raise RuntimeError("Local live transcription model unavailable")
    return value


def installed() -> bool:
    """Report asset presence, separately from a successful live recognition run."""
    try:
        binary = _binary()
        if not os.access(binary, os.X_OK):
            return False
        for name in ("STELLIS_ONLINE_ASR_MODEL_DIR", "STELLIS_PARAKEET_MODEL_DIR"):
            directory = Path(_model_dir(name))
            for choices in (("tokens.txt",), ("encoder.int8.onnx", "encoder.onnx"),
                            ("decoder.int8.onnx", "decoder.onnx"), ("joiner.int8.onnx", "joiner.onnx")):
                if not any((directory / file).is_file() and (directory / file).stat().st_size for file in choices):
                    return False
        return True
    except (OSError, RuntimeError):
        return False


class _Worker:
    """One ffmpeg decoder and one Rust recognizer for one recording session."""

    def __init__(self) -> None:
        command = [_binary(), _model_dir("STELLIS_ONLINE_ASR_MODEL_DIR"),
                   _model_dir("STELLIS_PARAKEET_MODEL_DIR")]
        self.through = -1
        self.reply: dict[str, object] | None = None
        self.segments: list[dict[str, object]] = []
        self.error: str | None = None
        self.changed = threading.Event()
        self.stopping = threading.Event()
        self.pending: queue.Queue[tuple[Path, int, str]] = queue.Queue(maxsize=256)
        self.ffmpeg = subprocess.Popen(
            [tools.ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
             "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            self.asr = subprocess.Popen(
                command,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except Exception:
            self.ffmpeg.kill()
            self.ffmpeg.wait()
            if self.ffmpeg.stdin:
                self.ffmpeg.stdin.close()
            if self.ffmpeg.stdout:
                self.ffmpeg.stdout.close()
            raise
        self.thread = threading.Thread(target=self._pump, name="stellis-live-asr", daemon=True)
        self.writer = threading.Thread(target=self._write, name="stellis-live-audio", daemon=True)
        self.thread.start()
        self.writer.start()

    def _write(self) -> None:
        try:
            assert self.ffmpeg.stdin
            while not self.stopping.is_set():
                try:
                    path, size, digest = self.pending.get(timeout=0.2)
                except queue.Empty:
                    continue
                data = path.read_bytes()
                if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                    raise RuntimeError("A saved recording chunk failed integrity verification")
                self.ffmpeg.stdin.write(data)
                self.ffmpeg.stdin.flush()
        except (OSError, RuntimeError, ValueError) as exc:
            if not self.stopping.is_set():
                self.error = str(exc)
                self.changed.set()

    def _pump(self) -> None:
        try:
            assert self.ffmpeg.stdout and self.asr.stdin and self.asr.stdout
            while pcm := self.ffmpeg.stdout.read(6400):
                if len(pcm) % 4:
                    raise RuntimeError("Invalid decoded audio frame")
                request = json.dumps({"pcm": base64.b64encode(pcm).decode("ascii")}) + "\n"
                self.asr.stdin.write(request.encode("ascii"))
                self.asr.stdin.flush()
                line = self.asr.stdout.readline()
                if not line:
                    raise RuntimeError("Local live transcription worker stopped")
                reply = json.loads(line)
                if not isinstance(reply, dict) or not isinstance(reply.get("segments"), list):
                    raise RuntimeError("Invalid live transcription response")
                self.segments.extend(reply["segments"])
                reply["segments"] = self.segments
                self.reply = reply
                self.changed.set()
        except (OSError, ValueError, RuntimeError) as exc:
            self.error = str(exc)
            self.changed.set()

    def feed(self, path: Path, size: int, digest: str, sequence: int) -> bool:
        if self.error:
            raise RuntimeError(self.error)
        if self.ffmpeg.poll() is not None or self.asr.poll() is not None:
            raise RuntimeError("Local live transcription worker stopped")
        try:
            self.pending.put_nowait((path, size, digest))
        except queue.Full:
            return False
        self.through = sequence
        return True

    def stop(self) -> None:
        self.stopping.set()
        for process in (self.ffmpeg, self.asr):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.thread.join(timeout=2)
        self.writer.join(timeout=2)
        for process in (self.ffmpeg, self.asr):
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    # Closing flushes audio still buffered for a process that has exited.
                    with contextlib.suppress(OSError):
                        pipe.close()


def _result(reply: dict[str, object], through: int) -> dict[str, object]:
    duration = max(0, int(reply.get("duration_ms", 0)))
    segments: list[dict[str, object]] = []
    for item in reply.get("segments", []):
        if not isinstance(item, dict):
            continue
        start = max(0, int(item.get("start_ms", 0)))
        end = min(duration, int(item.get("end_ms", 0)))
        content = str(item.get("text", "")).strip()
        if content and start < end:
            segments.append({"start_ms": start, "end_ms": end, "text": content})
    partial = str(reply.get("partial", "")).strip()
    start = max(0, int(reply.get("partial_start_ms", 0)))
    if partial and start < duration:
        segments.append({"start_ms": start, "end_ms": duration, "text": partial})
    return {"state": "ready", "through_sequence": through, "segments": segments}


def _save(recording_id: str, result: dict[str, object]) -> None:
    directory = recording._directory(recording_id)
    fd, name = tempfile.mkstemp(prefix=".preview-", dir=directory)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(result, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, directory / "preview.json")
    finally:
        Path(name).unlink(missing_ok=True)


def latest(recording_id: str) -> dict[str, object]:
    """Read the latest persisted provisional text without starting ASR."""
    if recording.get_session(recording_id) is None:
        raise LookupError("Recording session not found")
    return _stored(recording_id)


def generate(recording_id: str) -> dict[str, object]:
    """Verify and feed new chunks, then save the latest incremental recognizer result."""
    with _LOCK:
        rows, through = _snapshot(recording_id)
        prior = _stored(recording_id)
        if through < 2:
            return prior
        worker = _WORKERS.get(recording_id)
        if worker and (worker.error or worker.ffmpeg.poll() is not None or worker.asr.poll() is not None):
            worker.stop()
            _WORKERS.pop(recording_id)
            worker = None
        if worker is None:
            worker = _Worker()
            _WORKERS[recording_id] = worker
        worker.changed.clear()
        directory = recording._directory(recording_id)
        new_chunks = worker.through < through
        for row in rows[worker.through + 1:]:
            source = directory / f"{row['sequence']:08d}.chunk"
            data = source.read_bytes()
            if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise RuntimeError("A saved recording chunk failed integrity verification")
            if not worker.feed(source, int(row["size_bytes"]), str(row["sha256"]), int(row["sequence"])):
                break  # The decoder catches up without delaying durable chunk capture.
        if new_chunks or worker.reply is None:
            worker.changed.wait(timeout=1.5)
        if worker.error:
            raise RuntimeError(worker.error)
        if worker.reply is None:
            return prior
        candidate = _result(worker.reply, worker.through)
        old_end = max((int(item["end_ms"]) for item in prior.get("segments", [])), default=0)
        new_end = max((int(item["end_ms"]) for item in candidate["segments"]), default=0)
        if new_end < old_end:
            return prior  # After a server restart, replay saved chunks before replacing text.
        if recording.get_session(recording_id)["state"] == "recording":
            _save(recording_id, candidate)
        return candidate


def stop(recording_id: str) -> None:
    with _LOCK:
        worker = _WORKERS.pop(recording_id, None)
        if worker:
            worker.stop()


def _stop_all() -> None:
    with _LOCK:
        workers = list(_WORKERS.values())
        _WORKERS.clear()
    for worker in workers:
        worker.stop()


atexit.register(_stop_all)
