"""Durable, on-device transcription and acoustic diarization jobs."""

from __future__ import annotations

import json
import hashlib
import os
import queue
import re
from bisect import bisect_left, bisect_right
from difflib import SequenceMatcher
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf

from stellis import silence, storage, tools

PIPELINE_VERSION = "whisper-small-en-sherpa-pyannote3-titanet-words-v10"
REMATCH_VERSION = "speaker-embedding-v1"
WORD_ALIGNMENT_VERSION = "word-align-v1"
# Whisper writes bracketed sound tags such as "(scissors snipping)" or
# "[BLANK_AUDIO]" for non-speech audio; a passage of only tags is not speech.
_SOUND_TAG = re.compile(r"[\(\[\*][^\)\]\*]*[\)\]\*]")
_QUEUE: queue.Queue[tuple[str, str | None] | None] = queue.Queue()
_WORKER_LOCK = threading.Lock()
_CHILD_LOCK = threading.Lock()
_REQUEST_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None
_CHILD: subprocess.Popen | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def model_paths() -> tuple[Path, Path, Path]:
    """Resolve model locations without shipping large binaries in Git."""
    root = storage.base_dir() / "models"
    return (
        Path(os.environ.get("STELLIS_WHISPER_MODEL", root / "ggml-small.en.bin")).expanduser(),
        Path(os.environ.get("STELLIS_SEGMENTATION_MODEL", root / "pyannote-segmentation-3.0-int8.onnx")).expanduser(),
        Path(os.environ.get("STELLIS_EMBEDDING_MODEL", root / "nemo-en-titanet-small.onnx")).expanduser(),
    )


def capabilities() -> dict[str, object]:
    """Report actual installed engine readiness to any client."""
    whisper, segmentation, embedding = model_paths()
    asr_ready = whisper.is_file()
    diar_ready = segmentation.is_file() and embedding.is_file()
    return {
        "pipeline_version": pipeline_version(),
        "engines": [
            {"id": "whisper.cpp", "kind": "asr", "model": whisper.name, "languages": ["en"], "ready": asr_ready},
            {"id": "sherpa-onnx", "kind": "diarization", "model": f"{segmentation.name} + {embedding.name}", "languages": [], "ready": diar_ready},
        ],
        "features": {"transcribe": asr_ready, "diarize": diar_ready},
    }


def vocabulary_snapshot() -> list[str]:
    """Fit recently changed terms into the local model's bounded prompt."""
    terms: list[str] = []
    records = sorted(storage.list_vocabulary(),
                     key=lambda item: (str(item["updated_at"]), str(item["id"])), reverse=True)
    for item in records:
        candidate = [*terms, str(item["term"])]
        if len("Vocabulary: " + ", ".join(candidate)) <= 400:
            terms = candidate
    return terms


def pipeline_version(terms: list[str] | None = None) -> str:
    """Include vocabulary in job identity without changing old transcripts."""
    terms = vocabulary_snapshot() if terms is None else terms
    if not terms:
        return PIPELINE_VERSION
    digest = hashlib.sha256(json.dumps(terms, ensure_ascii=False).encode()).hexdigest()[:12]
    return f"{PIPELINE_VERSION}-vocab-{digest}"


def public_job(record: dict[str, object]) -> dict[str, object]:
    """Exclude internal database fields and decode metrics."""
    return {
        key: record[key]
        for key in ("id", "conversation_id", "pipeline_version", "kind", "state", "stage", "progress", "error", "created_at", "updated_at")
    } | {"metrics": json.loads(str(record["metrics_json"])) if record["metrics_json"] else None}


def _enqueue(job_id: str) -> None:
    """Queues a job with the workspace it belongs to, so a demo's job runs against the demo."""
    workspace = storage.workspace_override()
    _QUEUE.put((job_id, str(workspace) if workspace else None))


def start_worker() -> None:
    """Start one device worker and retry unfinished jobs after a restart."""
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER and _WORKER.is_alive():
            return
        with _REQUEST_LOCK:
            # The database owns pending work. Discard stale wakeups from the previous lifecycle.
            while True:
                try:
                    _QUEUE.get_nowait()
                    _QUEUE.task_done()
                except queue.Empty:
                    break
            for job in storage.list_pending_jobs():
                storage.update_job(str(job["id"]), state="queued", stage="resuming", updated_at=now())
                _enqueue(str(job["id"]))
            _STOPPING.clear()
            _WORKER = threading.Thread(target=_worker_loop, name="stellis-speech", daemon=True)
            _WORKER.start()


def request_processing(conversation_id: str) -> dict[str, object]:
    """Queue one job per conversation and pipeline version."""
    with _REQUEST_LOCK:
        terms = vocabulary_snapshot()
        version = pipeline_version(terms)
        existing = storage.get_job_for_conversation(conversation_id, version)
        if existing:
            if existing["state"] == "failed":
                storage.update_job(str(existing["id"]), state="queued", stage="retrying", progress=0,
                                   error=None, updated_at=now())
                _enqueue(str(existing["id"]))
                return storage.get_job(str(existing["id"])) or existing
            return existing
        stamp = now()
        record: dict[str, object] = {
            "id": str(uuid.uuid4()), "conversation_id": conversation_id,
            "pipeline_version": version, "kind": "speech", "state": "queued", "stage": "queued",
            "progress": 0, "error": None, "metrics_json": None,
            "vocabulary_json": json.dumps(terms, ensure_ascii=False),
            "created_at": stamp, "updated_at": stamp,
        }
        storage.add_job(record)
        _enqueue(str(record["id"]))
        return record


def request_rematch(conversation_id: str) -> dict[str, object]:
    """Queue an idempotent acoustic rematch without rerunning transcription."""
    with _REQUEST_LOCK:
        transcript = storage.get_transcript(conversation_id)
        if transcript is None:
            raise LookupError("Transcript is not ready")
        base_version = str(transcript["generation_version"]).split("-" + REMATCH_VERSION)[0]
        if storage.get_transcript_generation(conversation_id, base_version) is None:
            raise LookupError("Original machine generation is unavailable")
        version = f"{base_version}-{REMATCH_VERSION}"
        existing = storage.get_job_for_conversation(conversation_id, version)
        if existing:
            if existing["state"] == "failed":
                storage.update_job(str(existing["id"]), state="queued", stage="retrying", progress=0,
                                   error=None, updated_at=now())
                _enqueue(str(existing["id"]))
                return storage.get_job(str(existing["id"])) or existing
            return existing
        stamp = now()
        record: dict[str, object] = {
            "id": str(uuid.uuid4()), "conversation_id": conversation_id,
            "pipeline_version": version, "source_generation_version": base_version, "kind": "rematch",
            "state": "queued", "stage": "queued", "progress": 0, "error": None,
            "metrics_json": None, "vocabulary_json": "[]", "created_at": stamp, "updated_at": stamp,
        }
        storage.add_job(record)
        _enqueue(str(record["id"]))
        return record


def request_word_alignment(conversation_id: str, source_version: str | None = None) -> dict[str, object]:
    """Queue a source-preserving word timing job for a retained transcript generation."""
    with _REQUEST_LOCK:
        current = storage.get_transcript(conversation_id)
        if current is None:
            raise LookupError("Transcript is not ready")
        source_version = source_version or str(current["generation_version"])
        source = storage.get_transcript_generation(conversation_id, source_version)
        if source is None:
            raise LookupError("Source transcript generation is unavailable")
        version = f"{source_version}-{WORD_ALIGNMENT_VERSION}"
        existing = storage.get_job_for_conversation(conversation_id, version)
        if existing:
            if existing["state"] == "failed":
                storage.update_job(str(existing["id"]), state="queued", stage="retrying", progress=0,
                                   error=None, updated_at=now())
                _enqueue(str(existing["id"]))
                return storage.get_job(str(existing["id"])) or existing
            return existing
        stamp = now()
        record: dict[str, object] = {
            "id": str(uuid.uuid4()), "conversation_id": conversation_id,
            "pipeline_version": version, "source_generation_version": source_version,
            "kind": "word_alignment", "state": "queued", "stage": "queued", "progress": 0,
            "error": None, "metrics_json": None, "vocabulary_json": "[]",
            "created_at": stamp, "updated_at": stamp,
        }
        storage.add_job(record)
        _enqueue(str(record["id"]))
        return record


def request_silence_scan(conversation_id: str) -> dict[str, object]:
    """Queue one measured playback-only silence scan per source recording."""
    if storage.get_transcript(conversation_id) is None:
        raise LookupError("Transcribe before using Skip Silence")
    with _REQUEST_LOCK:
        existing = storage.get_job_for_conversation(conversation_id, silence.VERSION)
        if existing:
            if existing["state"] == "failed":
                storage.update_job(str(existing["id"]), state="queued", stage="queued", progress=0,
                                   error=None, updated_at=now())
                _enqueue(str(existing["id"]))
                return storage.get_job(str(existing["id"])) or existing
            return existing
        stamp = now()
        record: dict[str, object] = {
            "id": str(uuid.uuid4()), "conversation_id": conversation_id,
            "pipeline_version": silence.VERSION, "kind": "silence",
            "state": "queued", "stage": "queued", "progress": 0, "error": None,
            "metrics_json": None, "vocabulary_json": "[]", "created_at": stamp, "updated_at": stamp,
        }
        storage.add_job(record)
        _enqueue(str(record["id"]))
        return record


VOICE_INDEX_VERSION = "titanet-small-voices-v1"


def request_voice_index(conversation_id: str) -> dict[str, object]:
    """Queue fingerprinting of a transcript's passages for speaker suggestions."""
    if storage.get_transcript(conversation_id) is None:
        raise LookupError("Transcript is not ready")
    with _REQUEST_LOCK:
        existing = storage.get_job_for_conversation(conversation_id, VOICE_INDEX_VERSION)
        if existing and existing["state"] in ("queued", "running"):
            return existing
        stamp = now()
        if existing:
            storage.update_job(str(existing["id"]), state="queued", stage="queued", progress=0,
                               error=None, updated_at=stamp)
            _enqueue(str(existing["id"]))
            return storage.get_job(str(existing["id"])) or existing
        record: dict[str, object] = {
            "id": str(uuid.uuid4()), "conversation_id": conversation_id,
            "pipeline_version": VOICE_INDEX_VERSION, "kind": "voice_index",
            "state": "queued", "stage": "queued", "progress": 0, "error": None,
            "metrics_json": None, "vocabulary_json": "[]", "created_at": stamp, "updated_at": stamp,
        }
        storage.add_job(record)
        _enqueue(str(record["id"]))
        return record


def index_missing_voices() -> int:
    """Fingerprint every saved transcript that has no stored voices yet."""
    from stellis import voices
    queued = 0
    for item in storage.list_conversations():
        conversation_id = str(item["id"])
        latest = storage.get_latest_job_for_conversation(conversation_id)
        if latest and latest["kind"] == "speech" and latest["state"] in ("queued", "running"):
            continue  # The speech job stores fingerprints of its own new passages.
        failed_index = bool(latest and latest["kind"] == "voice_index" and latest["state"] == "failed")
        if storage.get_transcript(conversation_id) is not None and (failed_index or not voices.has_index(conversation_id)):
            try:
                request_voice_index(conversation_id)
                queued += 1
            except LookupError:
                continue
    return queued


def run_voice_index_job(job_id: str) -> None:
    from stellis import rematch, voices

    job = storage.get_job(job_id)
    if not job or job["state"] == "succeeded":
        return
    conversation_id = str(job["conversation_id"])
    try:
        conversation = storage.get_conversation(conversation_id)
        transcript = storage.get_transcript(conversation_id)
        if conversation is None or transcript is None:
            raise RuntimeError("Transcript is not ready")
        segments = json.loads(str(transcript["segments_json"]))
        if not segments:
            # A recording with no speech has nothing to fingerprint.
            storage.update_job(job_id, state="succeeded", stage="complete", progress=1.0,
                               metrics_json=json.dumps({"indexed_passages": 0}), error=None, updated_at=now())
            return
        embedding_model = model_paths()[2]
        if not embedding_model.is_file():
            raise RuntimeError("Speaker embedding model is unavailable")
        media = storage.data_dir() / "media" / str(conversation["audio_filename"])
        if not media.is_file():
            raise RuntimeError("Source audio is missing")
        with tempfile.TemporaryDirectory(prefix="stellis-voices-", dir=storage.data_dir()) as temporary:
            wav = Path(temporary) / "audio.wav"
            _stage(job_id, "decoding for voice matching", 0.1)
            _command([tools.ffmpeg(), "-nostdin", "-y", "-v", "error", "-i", str(media),
                      "-ac", "1", "-ar", "16000", str(wav)], timeout=1800)
            _stage(job_id, "indexing voices", 0.4)
            vectors, valid = rematch.extract_embeddings(wav, embedding_model, segments)
        count = voices.save(conversation_id, segments, vectors, valid)
        storage.update_job(job_id, state="succeeded", stage="complete", progress=1.0,
                           metrics_json=json.dumps({"indexed_passages": count}), error=None, updated_at=now())
    except Exception as exc:
        storage.update_job(job_id, state="failed", stage="failed", error=str(exc), updated_at=now())


def _worker_loop() -> None:
    while True:
        item = _QUEUE.get()
        try:
            if item is None or _STOPPING.is_set():
                return
            job_id, workspace = item
            if workspace:
                with storage.demo_workspace(Path(workspace)):
                    _run_queued(job_id, workspace)
            else:
                _run_queued(job_id, None)
        finally:
            _QUEUE.task_done()


def _run_queued(job_id: str, workspace: str | None) -> None:
    global _CHILD
    try:
        with _CHILD_LOCK:
            if _STOPPING.is_set():
                return
            # Serialize launch with shutdown so it cannot miss a newly started child.
            _CHILD = subprocess.Popen([sys.executable, "-m", "stellis.worker", job_id, *([workspace] if workspace else [])],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            returncode = _CHILD.wait(timeout=14400)
        except subprocess.TimeoutExpired:
            _stop_child(_CHILD)
            returncode = -1
        finally:
            _CHILD = None
        if _STOPPING.is_set():
            return  # Leave interrupted work durable for the next start, including graceful exit 143.
        if returncode:
            storage.update_job(job_id, state="failed", stage="failed",
                               error="Speech worker exited unexpectedly", updated_at=now())
        else:
            job = storage.get_job(job_id)
            if job and job["kind"] == "speech" and job["state"] == "succeeded" and job["stage"] == "complete":
                from stellis import categorization, enhanced_notes
                try:
                    categorization.request(str(job["conversation_id"]))
                except (RuntimeError, LookupError):
                    pass  # The transcript remains ready; classification has its own retry path.
                try:
                    enhanced_notes.request(str(job["conversation_id"]))
                except (RuntimeError, LookupError, ValueError, enhanced_notes.ai.AIError):
                    pass  # Summary failure must not change the completed speech job.
    except Exception:
        # Keep the queue alive even if persistence or a native adapter fails unexpectedly.
        try:
            storage.update_job(job_id, state="failed", stage="failed",
                               error="Unexpected processing failure", updated_at=now())
        except Exception:
            pass


_STOPPING = threading.Event()


def _stop_child(child: subprocess.Popen, grace: float = 5.0) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            return
        try:
            child.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def stop_worker() -> None:
    """Stop native tools and join the dispatcher before storage can be released."""
    global _WORKER
    with _WORKER_LOCK:
        with _CHILD_LOCK:
            _STOPPING.set()
            child = _CHILD
        if child is not None and child.poll() is None:
            _stop_child(child)
        if _WORKER is not None:
            _QUEUE.put(None)  # Wake an idle dispatcher as well as an active one.
            _WORKER.join()
            _WORKER = None


def _stage(job_id: str, name: str, progress: float) -> None:
    storage.update_job(job_id, state="running", stage=name, progress=progress, updated_at=now())


def _command(command: list[str], timeout: int) -> None:
    completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, timeout=timeout)
    if completed.returncode:
        # Native-tool diagnostics may include private file paths or speech text.
        raise RuntimeError(f"{Path(command[0]).name} failed (exit {completed.returncode})")


def _transcribe_progress(job_id: str):
    """Saves Whisper's progress at most every 2 percent, so the job row is not rewritten per passage."""
    saved = [0.12]

    def report(fraction: float) -> None:
        progress = round(0.12 + 0.43 * fraction, 3)
        if progress >= saved[0] + 0.02:
            saved[0] = progress
            storage.update_job(job_id, progress=progress, updated_at=now())
    return report


def _transcribe(audio_path: Path, model: Path, output_base: Path,
                vocabulary: list[str]) -> list[dict[str, object]]:
    segments = _decode(audio_path, model, output_base, vocabulary)
    if not segments:
        raise RuntimeError("The on-device model returned no speech segments")
    segments = _repair_loops(audio_path, model, output_base, vocabulary, segments)
    return [segment for segment in segments if not is_non_speech(str(segment["text"]))]


def _run_asr(audio_path: Path, model: Path, vocabulary: list[str],
             max_context: int | None = None) -> list[dict[str, object]]:
    """Passages from whisper.cpp in whisper-cli's JSON shape (see asr.py)."""
    from stellis import asr

    audio, rate = sf.read(audio_path, dtype="float32", always_2d=True)
    if rate != 16000:
        raise RuntimeError("Decoded audio must be 16 kHz")
    return asr.transcribe(audio[:, 0], model, vocabulary, max_context=max_context)


def _decode(audio_path: Path, model: Path, output_base: Path, vocabulary: list[str],
            max_context: int | None = None, offset_ms: int = 0) -> list[dict[str, object]]:
    audio_info = sf.info(audio_path)
    duration_ms = round(audio_info.frames / audio_info.samplerate * 1000)
    segments: list[dict[str, object]] = []
    for item in _run_asr(audio_path, model, vocabulary, max_context):
        start = max(0, int(item["offsets"]["from"]))
        end = min(duration_ms, int(item["offsets"]["to"]))
        if end > start and str(item["text"]).strip():
            words = []
            for word in _measured_words(item):
                word_start = max(start, int(word["start_ms"]))
                word_end = min(end, int(word["end_ms"]))
                if word_end > word_start:
                    words.append({**word, "start_ms": word_start + offset_ms, "end_ms": word_end + offset_ms})
            segments.append({"start_ms": start + offset_ms, "end_ms": end + offset_ms,
                             "text": str(item["text"]).strip(), "words": words})
    return segments


def is_looping(text: str) -> bool:
    """True when most of a passage is one short phrase repeated, a Whisper decoding loop."""
    words = re.findall(r"[a-z0-9']+", text.casefold())
    for size in (2, 3, 4, 5, 6):
        grams = [" ".join(words[index:index + size]) for index in range(0, len(words) - size + 1)]
        if len(grams) < 8:
            continue
        top = max(set(grams), key=grams.count)
        if grams.count(top) >= 4 and grams.count(top) * size >= 0.6 * len(words):
            return True
    return False


def _repair_loops(audio_path: Path, model: Path, output_base: Path, vocabulary: list[str],
                  segments: list[dict[str, object]]) -> list[dict[str, object]]:
    """Re-decode each stretch where Whisper looped on its own context.

    A loop builds up from text carried across a long file; the same audio decoded on
    its own transcribes normally. Try the vocabulary prompt first, then no carried
    context; if both still loop, keep only the passage's first copy of the phrase."""
    spans: list[list[int]] = []
    for index, segment in enumerate(segments):
        if is_looping(str(segment["text"])):
            if spans and spans[-1][1] == index - 1:
                spans[-1][1] = index
            else:
                spans.append([index, index])
    if not spans:
        return segments
    audio, rate = sf.read(audio_path, dtype="float32")
    repaired: dict[int, list[dict[str, object]]] = {}
    for number, (first, last) in enumerate(spans):
        start = max(0, int(segments[first]["start_ms"]) - 1000)
        end = int(segments[last]["end_ms"]) + 1000
        window = output_base.with_name(f"{output_base.name}-loop-{number}")
        clip = window.with_suffix(".wav")
        sf.write(clip, audio[start * rate // 1000:end * rate // 1000], rate)
        replacement = None
        for terms, max_context in ((vocabulary, None), ([], 0)):
            try:
                attempt = _decode(clip, model, window, terms, max_context, offset_ms=start)
            except RuntimeError:
                continue
            if attempt and not any(is_looping(str(item["text"])) for item in attempt):
                replacement = attempt
                break
        clip.unlink(missing_ok=True)
        if replacement is None:
            replacement = [_first_copy(segments[index]) for index in range(first, last + 1)]
        # Keep neighbouring passages intact: only text inside the looping stretch is replaced.
        low, high = int(segments[first]["start_ms"]), int(segments[last]["end_ms"])
        repaired[first] = [item for item in replacement
                           if low <= (int(item["start_ms"]) + int(item["end_ms"])) / 2 <= high]
        for index in range(first + 1, last + 1):
            repaired[index] = []
    result: list[dict[str, object]] = []
    for index, segment in enumerate(segments):
        result.extend(repaired.get(index, [segment]))
    # Last guard: loop text is never saved, whatever the re-decode produced.
    return [_first_copy(item) if is_looping(str(item["text"])) else item for item in result]


def _first_copy(segment: dict[str, object]) -> dict[str, object]:
    sentences = re.split(r"(?<=[,.!?])\s+", str(segment["text"]))
    kept = [sentence for position, sentence in enumerate(sentences) if sentence not in sentences[:position]]
    return {**segment, "text": " ".join(kept).strip(), "words": []}


def is_non_speech(text: str) -> bool:
    """True when a passage holds only sound tags, music marks, or punctuation."""
    return not re.search(r"[A-Za-z0-9]", _SOUND_TAG.sub(" ", text))


def _speech_turn_coverage(segments: list[dict[str, object]],
                          turns: list[dict[str, object]]) -> float:
    """Measure how much diarized speech has lexical ASR text."""
    if not turns:
        return 0.0
    intervals = sorted((int(item["start_ms"]), int(item["end_ms"])) for item in segments
                       if re.search(r"[A-Za-z]", str(item["text"]))
                       and not str(item["text"]).strip().startswith("["))
    merged: list[list[int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    speech_ms = sum(max(0, int(turn["end_ms"]) - int(turn["start_ms"])) for turn in turns)
    if not speech_ms:
        return 0.0
    covered_ms = sum(max(0, min(int(turn["end_ms"]), end) - max(int(turn["start_ms"]), start))
                     for turn in turns for start, end in merged)
    return min(1.0, covered_ms / speech_ms)


def _measured_words(item: dict[str, object]) -> list[dict[str, object]]:
    """Join native timed tokens into words only when they reproduce ASR text exactly."""
    tokens = [token for token in item.get("tokens", [])
              if isinstance(token, dict) and isinstance(token.get("text"), str)
              and not token["text"].startswith("[_")]
    raw = "".join(str(token["text"]) for token in tokens)
    displayed = str(item["text"]).strip()
    if not displayed or raw.strip() != displayed:
        return []
    shift = raw.find(displayed)
    if shift < 0:
        return []
    ownership: list[int] = []
    for index, token in enumerate(tokens):
        ownership.extend([index] * len(str(token["text"])))
    segment_start = int(item["offsets"]["from"])
    segment_end = int(item["offsets"]["to"])
    words: list[dict[str, object]] = []
    for match in re.finditer(r"\S+", displayed):
        first = ownership[shift + match.start()]
        last = ownership[shift + match.end() - 1]
        contributing = tokens[first:last + 1]
        try:
            start = max(segment_start, int(contributing[0]["offsets"]["from"]))
            end = min(segment_end, int(contributing[-1]["offsets"]["to"]))
            confidence = min(float(token["p"]) for token in contributing)
        except (KeyError, ValueError, TypeError):
            return []
        if end <= start or (words and start < int(words[-1]["start_ms"])):
            continue
        words.append({"text": match.group(), "start_offset": match.start(),
                      "end_offset": match.end(), "start_ms": start, "end_ms": end,
                      "confidence": round(max(0.0, min(confidence, 1.0)), 3),
                      "provenance": "whisper_cpp_token_v1"})
    return words


def _diarize(audio_path: Path, segmentation: Path, embedding: Path, job_id: str) -> list[dict[str, object]]:
    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(segmentation), window_shift_ratio=0.1,
            ),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding)),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.8),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("Speaker model configuration is invalid")
    audio, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
    if sample_rate != 16000:
        raise RuntimeError("Decoded audio must be 16 kHz")
    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    last_bucket = -1

    def on_progress(done: int, total: int) -> int:
        nonlocal last_bucket
        bucket = int(done * 10 / max(1, total))
        if bucket != last_bucket:
            last_bucket = bucket
            _stage(job_id, "diarizing", 0.55 + 0.4 * done / max(1, total))
        return 0

    result = diarizer.process(audio[:, 0], callback=on_progress).sort_by_start_time()
    return [
        {"start_ms": round(turn.start * 1000), "end_ms": round(turn.end * 1000),
         "speaker_cluster_id": f"speaker-{turn.speaker + 1}"}
        for turn in result if turn.end > turn.start
    ]


def _split_once_at_speaker_change(segment: dict[str, object],
                                  turns: list[dict[str, object]]) -> list[dict[str, object]]:
    """Split one ASR passage only when timed words locate a clear voice change."""
    def dominant_cluster(part_start: int, part_end: int) -> tuple[str | None, float]:
        coverage: dict[str, int] = {}
        for turn in turns:
            overlap = max(0, min(part_end, int(turn["end_ms"])) -
                          max(part_start, int(turn["start_ms"])))
            if overlap:
                cluster = str(turn["speaker_cluster_id"])
                coverage[cluster] = coverage.get(cluster, 0) + overlap
        if not coverage:
            return None, 0.0
        cluster, duration = max(coverage.items(), key=lambda item: item[1])
        return cluster, duration / (part_end - part_start)

    text = str(segment["text"])
    words = segment.get("words", [])
    matches = list(re.finditer(r"\S+", text))
    if not isinstance(words, list) or len(words) < 3 or len(words) != len(matches):
        return [segment]
    if any(not isinstance(word, dict) or word.get("text") != match.group()
           or word.get("start_offset") != match.start() or word.get("end_offset") != match.end()
           for word, match in zip(words, matches)):
        return [segment]
    start, end = int(segment["start_ms"]), int(segment["end_ms"])
    candidates: list[tuple[int, int]] = []
    ordered = sorted(turns, key=lambda turn: int(turn["start_ms"]))
    for old, new in zip(ordered, ordered[1:]):
        if old["speaker_cluster_id"] == new["speaker_cluster_id"]:
            continue
        old_end, new_start = int(old["end_ms"]), int(new["start_ms"])
        if not -100 <= new_start - old_end <= 600:
            continue
        if min(end, old_end) - max(start, int(old["start_ms"])) < 500:
            continue
        if min(end, int(new["end_ms"])) - max(start, new_start) < 250:
            continue
        for index in range(1, len(words)):
            word = words[index]
            word_start, word_end = int(word["start_ms"]), int(word["end_ms"])
            duration = word_end - word_start
            if duration <= 0 or abs(word_start - new_start) > 450:
                continue
            new_overlap = max(0, min(word_end, int(new["end_ms"])) - max(word_start, new_start))
            if new_overlap < max(80, 0.6 * duration):
                continue
            if int(words[index - 1]["end_ms"]) > word_start:
                continue
            old_anchor = any(
                max(0, min(int(prior["end_ms"]), old_end) -
                    max(int(prior["start_ms"]), int(old["start_ms"]))) >=
                max(80, 0.6 * (int(prior["end_ms"]) - int(prior["start_ms"])))
                for prior in words[max(0, index - 3):index]
                if int(prior["end_ms"]) > int(prior["start_ms"])
            )
            if old_anchor and word_start - start >= 500 and end - word_start >= 250:
                left_cluster, left_coverage = dominant_cluster(start, word_start)
                right_cluster, right_coverage = dominant_cluster(word_start, end)
                if (left_cluster != old["speaker_cluster_id"] or left_coverage < 0.55 or
                        right_cluster != new["speaker_cluster_id"] or right_coverage < 0.55):
                    continue
                candidates.append((abs(word_start - new_start), index))
                break
    if not candidates:
        return [segment]
    _, split_index = min(candidates)
    split_offset = int(words[split_index]["start_offset"])
    split_time = int(words[split_index]["start_ms"])
    left_text, right_text = text[:split_offset].rstrip(), text[split_offset:].lstrip()
    if not left_text or not right_text or text[len(left_text):split_offset].strip():
        return [segment]
    left = {**segment, "end_ms": split_time, "text": left_text,
            "words": [dict(word) for word in words[:split_index]]}
    right = {**segment, "start_ms": split_time, "text": right_text,
             "words": [{**word, "start_offset": int(word["start_offset"]) - split_offset,
                        "end_offset": int(word["end_offset"]) - split_offset}
                       for word in words[split_index:]]}
    return [left, right]


def split_segments_at_speaker_changes(segments: list[dict[str, object]],
                                      turns: list[dict[str, object]]) -> list[tuple[int, dict[str, object]]]:
    """Keep original ASR indexes so unrelated passage IDs remain stable."""
    result: list[tuple[int, dict[str, object]]] = []
    for source_index, segment in enumerate(segments):
        parts = [segment]
        for _ in range(3):
            next_parts = [piece for part in parts for piece in _split_once_at_speaker_change(part, turns)]
            if len(next_parts) == len(parts):
                break
            parts = next_parts
        result.extend((source_index, part) for part in parts)
    return result


def align_segments(conversation_id: str, segments: list[dict[str, object]],
                   turns: list[dict[str, object]]) -> tuple[list[dict[str, object]], float]:
    """Split clear speaker changes, then attach acoustic clusters with uncertainty."""
    aligned: list[dict[str, object]] = []
    confident = 0
    for index, segment in split_segments_at_speaker_changes(segments, turns):
        start, end = int(segment["start_ms"]), int(segment["end_ms"])
        overlap: dict[str, int] = {}
        for turn in turns:
            shared = max(0, min(end, int(turn["end_ms"])) - max(start, int(turn["start_ms"])))
            if shared:
                cluster = str(turn["speaker_cluster_id"])
                overlap[cluster] = overlap.get(cluster, 0) + shared
        ranked = sorted(overlap.items(), key=lambda item: item[1], reverse=True)
        best = ranked[0] if ranked else None
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        ratio = best[1] / (end - start) if best else 0
        uncertain = end - start < 1000 or ratio < 0.5 or (best and best[1] - runner_up < 0.15 * (end - start))
        if best and not uncertain:
            confident += 1
        aligned.append({
            "id": str(uuid.uuid5(uuid.UUID(conversation_id), f"segment:{index}:{start}:{end}")),
            **segment,
            "speaker_cluster_id": best[0] if best else None,
            "speaker_uncertain": bool(uncertain),
            "speaker_coverage": round(ratio, 3),
            "provenance": "on_device_asr", "words": segment.get("words", []),
        })
    return aligned, confident / len(aligned) if aligned else 0


def _group_speakers(wav: Path, embedding_model: Path, segments: list[dict[str, object]]):
    """Replace turn-overlap clusters with groups of per-passage voice embeddings.

    Returns whether grouping applied, plus the passage vectors for the voice index."""
    from stellis import rematch, speaker_clustering

    if not segments:
        return False, None, None
    vectors, valid = rematch.extract_embeddings(wav, embedding_model, segments)
    return speaker_clustering.assign(segments, vectors, valid), vectors, valid


def _fill_unassigned_speakers(wav: Path, embedding_model: Path, segments: list[dict[str, object]],
                              turns: list[dict[str, object]]) -> int:
    """Embed only prototype and no-turn passages, then assign the no-turn ones."""
    from stellis import rematch, speaker_fallback

    missing = speaker_fallback.unassigned_indices(segments)
    if not missing:
        return 0
    wanted = sorted(set(missing) | set(speaker_fallback.prototype_indices(segments)))
    vectors, valid = rematch.extract_embeddings(wav, embedding_model, [segments[index] for index in wanted])
    embeddings = {index: vectors[position] for position, index in enumerate(wanted) if valid[position]}
    return speaker_fallback.fill_unassigned(segments, turns, embeddings)


def align_existing_words(source: list[dict[str, object]],
                         measured: list[dict[str, object]]) -> tuple[list[dict[str, object]], float]:
    """Attach only text-matched audio timing to old passages without changing their IDs."""
    available = sorted([(word["start_ms"], segment_index, word_index, word)
                        for segment_index, segment in enumerate(measured)
                        for word_index, word in enumerate(segment.get("words", []))], key=lambda item: item[0])
    times = [item[0] for item in available]
    used: set[tuple[int, int]] = set()
    aligned: list[dict[str, object]] = []
    matched = total = 0

    def normalize(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())

    for segment in source:
        text = str(segment["text"])
        source_words = [part for part in re.finditer(r"\S+", text) if normalize(part.group())]
        total += len(source_words)
        start, end = int(segment["start_ms"]), int(segment["end_ms"])
        left = bisect_left(times, start - 400)
        right = bisect_right(times, end + 400)
        candidates = [item for item in available[left:right] if (item[1], item[2]) not in used]
        source_tokens = [normalize(part.group()) for part in source_words]
        timing_tokens = [normalize(str(item[3]["text"])) for item in candidates]
        words = []
        for block in SequenceMatcher(None, source_tokens, timing_tokens, autojunk=False).get_matching_blocks():
            for offset in range(block.size):
                part = source_words[block.a + offset]
                _, segment_index, word_index, timed = candidates[block.b + offset]
                if not source_tokens[block.a + offset] or not timing_tokens[block.b + offset]:
                    continue
                word_start = max(start, int(timed["start_ms"]))
                word_end = min(end, int(timed["end_ms"]))
                if word_end <= word_start:
                    continue
                words.append({"text": part.group(), "start_offset": part.start(), "end_offset": part.end(),
                              "start_ms": word_start, "end_ms": word_end,
                              "confidence": timed["confidence"],
                              "provenance": "whisper_cpp_reconciled_v1"})
                used.add((segment_index, word_index))
                matched += 1
        aligned.append({**segment, "words": sorted(words, key=lambda item: item["start_offset"])})
    return aligned, matched / total if total else 0.0


def run_word_alignment_job(job_id: str) -> None:
    """Measure words for a retained source transcript without retranscribing its text."""
    job = storage.get_job(job_id)
    if job is None or job["state"] == "succeeded":
        return
    conversation_id = str(job["conversation_id"])
    try:
        source = storage.get_transcript_generation(conversation_id, str(job["source_generation_version"]))
        if source is None:
            raise RuntimeError("Source transcript generation is unavailable")
        measured = None
        for item in reversed(storage.list_transcript_generations(conversation_id)):
            if item["pipeline_version"] == job["source_generation_version"]:
                continue
            candidate = storage.get_transcript_generation(conversation_id, str(item["pipeline_version"]))
            if candidate is None:
                continue
            segments = json.loads(str(candidate["segments_json"]))
            if sum(len(segment.get("words", [])) for segment in segments) > 0:
                measured = segments
                break
        if measured is None:
            conversation = storage.get_conversation(conversation_id)
            if conversation is None:
                raise RuntimeError("Recording is missing")
            model = model_paths()[0]
            if not model.is_file():
                raise RuntimeError("Local speech model is unavailable")
            source_audio = storage.data_dir() / "media" / str(conversation["audio_filename"])
            with tempfile.TemporaryDirectory(prefix="stellis-words-", dir=storage.data_dir()) as temporary:
                workspace = Path(temporary)
                wav = workspace / "audio.wav"
                _stage(job_id, "decoding", 0.05)
                _command([tools.ffmpeg(), "-nostdin", "-y", "-v", "error", "-i", str(source_audio),
                          "-ac", "1", "-ar", "16000", str(wav)], timeout=1800)
                _stage(job_id, "measuring words", 0.2)
                measured = _transcribe(wav, model, workspace / "transcript", vocabulary_snapshot())
        _stage(job_id, "matching source words", 0.9)
        source_segments = json.loads(str(source["segments_json"]))
        aligned, coverage = align_existing_words(source_segments, measured)
        storage.save_transcript({"conversation_id": conversation_id,
                                 "pipeline_version": job["pipeline_version"],
                                 "language": source["language"],
                                 "segments_json": json.dumps(aligned), "turns_json": source["turns_json"],
                                 "updated_at": now()})
        storage.update_job(job_id, state="succeeded", stage="complete", progress=1.0,
                           metrics_json=json.dumps({"segment_count": len(aligned),
                                                    "word_count": sum(len(item["words"]) for item in aligned),
                                                    "word_coverage": round(coverage, 3)}),
                           error=None, updated_at=now())
    except Exception as exc:
        storage.update_job(job_id, state="failed", stage="failed", error=str(exc), updated_at=now())


def run_silence_job(job_id: str) -> None:
    """Measure original-media gaps without changing transcript timing."""
    job = storage.get_job(job_id)
    if not job or job["state"] == "succeeded":
        return
    conversation = storage.get_conversation(str(job["conversation_id"]))
    if not conversation:
        storage.update_job(job_id, state="failed", stage="failed", error="Recording missing", updated_at=now())
        return
    try:
        _stage(job_id, "measuring silence", 0.1)
        source = storage.data_dir() / "media" / str(conversation["audio_filename"])
        if not source.is_file():
            raise RuntimeError("Source audio is missing")
        transcript = storage.get_transcript(str(job["conversation_id"]))
        if transcript is None:
            raise RuntimeError("Transcript is not ready")
        measured = silence.measured_spans(source, int(conversation["duration_ms"]))
        spans = silence.exclude_speech(measured, json.loads(str(transcript["segments_json"])))
        storage.update_job(job_id, state="succeeded", stage="complete", progress=1,
                           metrics_json=json.dumps({"spans": spans, "count": len(spans),
                                                    "threshold_db": silence.THRESHOLD_DB}),
                           error=None, updated_at=now())
    except Exception as exc:
        storage.update_job(job_id, state="failed", stage="failed", error=str(exc), updated_at=now())


def run_job(job_id: str) -> None:
    """Process source audio and save transcript; failures remain retryable."""
    job = storage.get_job(job_id)
    if not job or job["state"] == "succeeded":
        return
    if job["kind"] == "rematch":
        run_rematch_job(job_id)
        return
    if job["kind"] == "word_alignment":
        run_word_alignment_job(job_id)
        return
    if job["kind"] == "voice_index":
        run_voice_index_job(job_id)
        return
    if job["kind"] == "silence":
        run_silence_job(job_id)
        return
    conversation = storage.get_conversation(str(job["conversation_id"]))
    if not conversation:
        storage.update_job(job_id, state="failed", stage="failed", error="Recording missing", updated_at=now())
        return
    started = time.monotonic()
    whisper, segmentation, embedding = model_paths()
    try:
        if not all(path.is_file() for path in (whisper, segmentation, embedding)):
            raise RuntimeError("Speech models are not installed on this device")
        source = storage.data_dir() / "media" / str(conversation["audio_filename"])
        if not source.is_file():
            raise RuntimeError("Source audio is missing")
        with tempfile.TemporaryDirectory(prefix="stellis-speech-", dir=storage.data_dir()) as temporary:
            workspace = Path(temporary)
            wav = workspace / "audio.wav"
            _stage(job_id, "decoding", 0.02)
            _command([tools.ffmpeg(), "-nostdin", "-y", "-v", "error", "-i", str(source),
                      "-ac", "1", "-ar", "16000", str(wav)], timeout=1800)
            _stage(job_id, "transcribing", 0.12)
            from stellis import asr
            # Whisper takes most of the job; move the bar through 12-55% as passages arrive.
            asr.PROGRESS.set(_transcribe_progress(job_id))
            vocabulary = json.loads(str(job["vocabulary_json"]))
            short_prompted_clip = bool(vocabulary) and sf.info(wav).duration <= 60
            prompted_failed = False
            try:
                segments = _transcribe(wav, whisper, workspace / "transcript", vocabulary)
            except RuntimeError:
                if not short_prompted_clip:
                    raise
                prompted_failed = True
                segments = _transcribe(wav, whisper, workspace / "transcript-plain", [])
            asr.PROGRESS.set(None)
            _stage(job_id, "diarizing", 0.55)
            turns = _diarize(wav, segmentation, embedding, job_id)
            prompted_coverage = _speech_turn_coverage(segments, turns)
            vocabulary_fallback = prompted_failed
            if short_prompted_clip and not prompted_failed and turns:
                try:
                    plain = _transcribe(wav, whisper, workspace / "transcript-plain", [])
                except RuntimeError:
                    plain = []
                plain_coverage = _speech_turn_coverage(plain, turns)
                if plain_coverage >= 0.65 and plain_coverage >= prompted_coverage + 0.2:
                    segments = plain
                    vocabulary_fallback = True
            _stage(job_id, "aligning", 0.96)
            aligned, coverage = align_segments(str(conversation["id"]), segments, turns)
            _stage(job_id, "grouping speakers", 0.97)
            grouped, voice_vectors, voice_valid = _group_speakers(wav, embedding, aligned)
            fallback_count = 0 if grouped else _fill_unassigned_speakers(wav, embedding, aligned, turns)
            metrics = {
                "processing_seconds": round(time.monotonic() - started, 2),
                "segment_count": len(aligned), "turn_count": len(turns),
                "cluster_count": len({turn["speaker_cluster_id"] for turn in turns}),
                "confident_segment_fraction": round(coverage, 3),
                "fallback_speaker_segment_count": fallback_count,
                "speaker_grouping": "passage_cluster" if grouped else "acoustic_turns",
                "passage_speaker_count": len({segment["speaker_cluster_id"] for segment in aligned}),
                "vocabulary_fallback": vocabulary_fallback,
                "speech_turn_coverage": round(_speech_turn_coverage(segments, turns), 3),
            }
            record = {
                "conversation_id": conversation["id"], "revision": 1,
                "pipeline_version": job["pipeline_version"], "language": "en",
                "segments_json": json.dumps(aligned), "turns_json": json.dumps(turns),
                "updated_at": now(),
            }
            from stellis import generation_review
            if generation_review.stage(record) is None:
                storage.save_transcript(record)
            if voice_vectors is not None:
                from stellis import voices
                voices.save(str(conversation["id"]), aligned, voice_vectors, voice_valid)
            storage.update_job(job_id, state="succeeded", stage="complete", progress=1.0,
                               metrics_json=json.dumps(metrics), error=None, updated_at=now())
    except Exception as exc:
        storage.update_job(job_id, state="failed", stage="failed", error=str(exc), updated_at=now())


def run_rematch_job(job_id: str) -> None:
    """Decode audio and reuse stored model turns and words for speaker rematch."""
    from stellis import rematch

    job = storage.get_job(job_id)
    if not job:
        return
    conversation_id = str(job["conversation_id"])
    source = storage.get_transcript_generation(conversation_id, str(job["source_generation_version"]))
    conversation = storage.get_conversation(conversation_id)
    if not source or not conversation:
        storage.update_job(job_id, state="failed", stage="failed", error="Source transcript is missing", updated_at=now())
        return
    try:
        embedding_model = model_paths()[2]
        if not embedding_model.is_file():
            raise RuntimeError("Speaker embedding model is unavailable")
        media = storage.data_dir() / "media" / str(conversation["audio_filename"])
        if not media.is_file():
            raise RuntimeError("Source audio is missing")
        segments = json.loads(str(source["segments_json"]))
        with tempfile.TemporaryDirectory(prefix="stellis-rematch-", dir=storage.data_dir()) as temporary:
            wav = Path(temporary) / "audio.wav"
            _stage(job_id, "decoding for speaker rematch", 0.05)
            _command([tools.ffmpeg(), "-nostdin", "-y", "-v", "error", "-i", str(media),
                      "-ac", "1", "-ar", "16000", str(wav)], timeout=1800)
            _stage(job_id, "matching acoustic speakers", 0.35)
            embeddings, valid = rematch.extract_embeddings(wav, embedding_model, segments)
            aligned, metrics = rematch.suggest_speakers(segments, embeddings, valid)
            from stellis import speaker_fallback
            metrics["fallback_speaker_segment_count"] = speaker_fallback.fill_unassigned(
                aligned, json.loads(str(source["turns_json"])))
        _stage(job_id, "saving speaker suggestions", 0.95)
        storage.save_transcript({"conversation_id": conversation_id, "revision": 1,
                                 "pipeline_version": job["pipeline_version"], "language": source["language"],
                                 "segments_json": json.dumps(aligned), "turns_json": source["turns_json"],
                                 "updated_at": now()})
        storage.update_job(job_id, state="succeeded", stage="complete", progress=1.0,
                           metrics_json=json.dumps(metrics), error=None, updated_at=now())
    except Exception as exc:
        storage.update_job(job_id, state="failed", stage="failed", error=str(exc), updated_at=now())
