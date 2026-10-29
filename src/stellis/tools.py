"""FFmpeg bundled with the imageio-ffmpeg package, plus the probes Stellis needs from it.

Stellis needs no system install: the package ships an FFmpeg executable for
each platform. STELLIS_FFMPEG points at another FFmpeg when set. FFmpeg's own
stderr report replaces ffprobe, which the package does not include.
"""

from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

_DURATION = re.compile(r"Duration: (\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_PROGRESS = re.compile(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_TIMEBASE = re.compile(r"^#tb 0: (\d+)/(\d+)", re.MULTILINE)


@lru_cache(maxsize=1)
def ffmpeg() -> str:
    configured = os.environ.get("STELLIS_FFMPEG")
    if configured:
        return configured
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _seconds(match: re.Match[str]) -> float:
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def probe(path: Path, timeout: int = 45) -> dict[str, object]:
    """Stream types and duration in seconds; the duration is measured by decoding when the header has none."""
    report = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)],
                            capture_output=True, text=True, timeout=timeout).stderr
    if "Invalid data found" in report or "No such file" in report:
        raise ValueError("The file could not be decoded")
    streams = {kind.lower() for kind in re.findall(r"Stream #\d+:\d+.*?: (Audio|Video|Subtitle|Data):", report)}
    duration = None
    match = _DURATION.search(report)
    if match:
        duration = _seconds(match)
    elif "audio" in streams:
        decoded = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:a:0",
                                  "-f", "null", "-"], capture_output=True, text=True, timeout=timeout).stderr
        times = _PROGRESS.findall(decoded)
        if times:
            hours, minutes, seconds = times[-1]
            duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return {"streams": streams, "duration": duration}


def packet_times(path: Path, timeout: int = 30) -> list[float]:
    """Presentation times, in seconds, of the first audio stream's packets."""
    # -copyts keeps the source timestamps; the live preview locates recent audio by them.
    completed = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-copyts", "-i", str(path),
                                "-map", "0:a:0", "-c", "copy", "-f", "framemd5", "-"],
                               capture_output=True, text=True, timeout=timeout, check=True)
    base = _TIMEBASE.search(completed.stdout)
    if not base:
        return []
    scale = int(base.group(1)) / int(base.group(2))
    times = []
    for line in completed.stdout.splitlines():
        if line and not line.startswith("#"):
            fields = [field.strip() for field in line.split(",")]
            if len(fields) >= 2 and fields[1].lstrip("-").isdigit():
                times.append(int(fields[1]) * scale)
    return times
