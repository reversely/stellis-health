"""A Whisper repetition loop is re-decoded instead of saved as text."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stellis import processing
from test_import import wav_bytes

LOOP = "I need to get a table, " * 30


class LoopDetectionTest(unittest.TestCase):
    def test_a_repeated_phrase_is_a_loop(self) -> None:
        self.assertTrue(processing.is_looping(LOOP))
        self.assertTrue(processing.is_looping("I need to get a table, to be honest. " + LOOP))

    def test_ordinary_speech_and_short_repeats_are_not(self) -> None:
        self.assertFalse(processing.is_looping("Yeah, yeah, yeah. So the plan is to ship the importer first and review it."))
        self.assertFalse(processing.is_looping("We tested the parser, then we tested the parser again with the new data and it held up."))


class LoopRepairTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.audio = Path(self.temp.name, "audio.wav")
        self.audio.write_bytes(wav_bytes())
        self.segments = [{"start_ms": 0, "end_ms": 200, "text": "Before.", "words": []},
                         {"start_ms": 200, "end_ms": 600, "text": LOOP, "words": []},
                         {"start_ms": 600, "end_ms": 900, "text": "After.", "words": []}]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_loop_is_replaced_by_the_stretch_decoded_on_its_own(self) -> None:
        fresh = [{"start_ms": 250, "end_ms": 550, "text": "The real words.", "words": []}]
        with patch.object(processing, "_decode", return_value=fresh) as decode:
            result = processing._repair_loops(self.audio, Path("/m.bin"), Path(self.temp.name, "t"), ["CubeSat"],
                                              self.segments)
        self.assertEqual([item["text"] for item in result], ["Before.", "The real words.", "After."])
        self.assertEqual(decode.call_args.args[3], ["CubeSat"])

    def test_without_a_clean_decode_only_the_first_copy_is_kept(self) -> None:
        with patch.object(processing, "_decode", return_value=[{"start_ms": 250, "end_ms": 550, "text": LOOP, "words": []}]):
            result = processing._repair_loops(self.audio, Path("/m.bin"), Path(self.temp.name, "t"), [], self.segments)
        self.assertEqual(result[1]["text"], "I need to get a table,")
        self.assertEqual([item["text"] for item in (result[0], result[2])], ["Before.", "After."])


if __name__ == "__main__":
    unittest.main()
