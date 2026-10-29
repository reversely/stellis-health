"""Conservative, local silence detection for optional playback skipping."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from stellis import tools

THRESHOLD_DB = -35
MIN_DETECT_SECONDS = 1.2
EDGE_SECONDS = 0.2
MIN_SKIP_SECONDS = 1.5
SPEECH_GUARD_MS = 250
VERSION = "silence-35db-1200ms-edge200ms-speechguard250ms-v2"
_START = re.compile(r"silence_start:\s*([0-9.]+)")
_END = re.compile(r"silence_end:\s*([0-9.]+)")


def measured_spans(path: Path, duration_ms: int) -> list[dict[str, int]]:
    """Scan audio energy with ffmpeg and retain bounded, speech-safe gaps."""
    completed = subprocess.run(
        [tools.ffmpeg(), "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
         "-i", str(path), "-vn", "-ac", "1",
         "-af", f"silencedetect=noise={THRESHOLD_DB}dB:d={MIN_DETECT_SECONDS}",
         "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=1800,
    )
    if completed.returncode:
        raise RuntimeError("Could not measure silence in this recording")
    spans: list[dict[str, int]] = []
    started: float | None = None
    duration = max(0, duration_ms / 1000)
    for line in completed.stderr.splitlines():
        begin = _START.search(line)
        if begin:
            started = float(begin.group(1))
        end = _END.search(line)
        if end and started is not None:
            _append(spans, started, float(end.group(1)), duration)
            started = None
    if started is not None:
        _append(spans, started, duration, duration)
    return spans


def _append(spans: list[dict[str, int]], start: float, end: float, duration: float) -> None:
    start_ms = round(max(0, start + EDGE_SECONDS) * 1000)
    end_ms = round(min(duration, end - EDGE_SECONDS) * 1000)
    if end_ms - start_ms >= MIN_SKIP_SECONDS * 1000:
        spans.append({"start_ms": start_ms, "end_ms": end_ms})


def exclude_speech(spans: list[dict[str, int]], segments: list[dict[str, object]]) -> list[dict[str, int]]:
    """Keep only measured low-energy gaps outside timed transcript passages."""
    guarded = sorted((max(0, int(item["start_ms"]) - SPEECH_GUARD_MS),
                      int(item["end_ms"]) + SPEECH_GUARD_MS) for item in segments)
    result: list[dict[str, int]] = []
    for span in spans:
        pieces = [(span["start_ms"], span["end_ms"])]
        for speech_start, speech_end in guarded:
            if speech_end <= span["start_ms"]:
                continue
            if speech_start >= span["end_ms"]:
                break
            pieces = [(left, right) for start, end in pieces
                      for left, right in ((start, min(end, speech_start)),
                                          (max(start, speech_end), end))
                      if right - left >= MIN_SKIP_SECONDS * 1000]
            if not pieces:
                break
        result.extend({"start_ms": start, "end_ms": end} for start, end in pieces)
    return result
