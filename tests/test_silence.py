"""Playback silence detection uses measured local energy, not transcript gaps."""

from __future__ import annotations

import math
import random
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from stellis import silence

RATE = 16000


def _wav(path: Path, parts: list[tuple[str, float]]) -> int:
    randomizer = random.Random(7)
    samples: list[int] = []
    for kind, seconds in parts:
        for index in range(round(RATE * seconds)):
            if kind == "tone":
                value = int(10000 * math.sin(2 * math.pi * 440 * index / RATE))
            elif kind == "noise":
                value = randomizer.randint(-5000, 5000)
            else:
                value = 0
            samples.append(value)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return len(samples) * 1000 // RATE


class SilenceDetectionTest(unittest.TestCase):
    def test_only_long_measured_silence_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audio.wav"
            duration = _wav(path, [("tone", 1), ("silence", 2.4), ("tone", 1),
                                   ("silence", 0.7), ("noise", 1)])
            spans = silence.measured_spans(path, duration)
        self.assertEqual(len(spans), 1)
        self.assertGreaterEqual(spans[0]["start_ms"], 1100)
        self.assertLessEqual(spans[0]["start_ms"], 1300)
        self.assertGreaterEqual(spans[0]["end_ms"], 3100)
        self.assertLessEqual(spans[0]["end_ms"], 3300)

    def test_music_and_noise_remain_playable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audio.wav"
            duration = _wav(path, [("tone", 2), ("noise", 2)])
            self.assertEqual(silence.measured_spans(path, duration), [])

    def test_measured_gap_inside_speech_is_not_skipped(self) -> None:
        spans = [{'start_ms': 1000, 'end_ms': 8000}, {'start_ms': 12000, 'end_ms': 17000}]
        segments = [{'start_ms': 0, 'end_ms': 9000}, {'start_ms': 14000, 'end_ms': 15000}]
        self.assertEqual(silence.exclude_speech(spans, segments), [
            {'start_ms': 12000, 'end_ms': 13750}, {'start_ms': 15250, 'end_ms': 17000}])


if __name__ == "__main__":
    unittest.main()
