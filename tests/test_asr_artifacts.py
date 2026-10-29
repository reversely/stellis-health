"""Whisper non-speech tags never become transcript passages."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import processing, storage
from stellis.server import app
from test_import import mp3_bytes, wav_bytes


class NonSpeechTextTest(unittest.TestCase):
    def test_tag_only_text_is_non_speech(self) -> None:
        for text in ["(scissors snipping)", "[BLANK_AUDIO]", " [inaudible] ", "*laughs*",
                     "[Music] [Applause]", "♪ ♪", "...", "(inaudible)."]:
            self.assertTrue(processing.is_non_speech(text), text)

    def test_speech_with_an_inline_tag_is_kept(self) -> None:
        for text in ["So [laughter] we shipped it.", "Yeah.", "(Pause) Okay, next item.", "Part 40k"]:
            self.assertFalse(processing.is_non_speech(text), text)


class TranscribeFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.audio = Path(self.temp.name, "audio.wav")
        self.audio.write_bytes(wav_bytes())
        self.output = Path(self.temp.name, "out")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self, items: list[dict[str, object]]) -> list[dict[str, object]]:
        with patch.object(processing, "_run_asr", return_value=items):
            return processing._transcribe(self.audio, Path("/model.bin"), self.output, [])

    def test_tag_only_passages_are_dropped(self) -> None:
        result = self._run([{"offsets": {"from": 0, "to": 300}, "text": " (scissors snipping)"},
                            {"offsets": {"from": 300, "to": 700}, "text": " So [laughter] we shipped it."},
                            {"offsets": {"from": 700, "to": 900}, "text": " [BLANK_AUDIO]"}])
        self.assertEqual([item["text"] for item in result], ["So [laughter] we shipped it."])

    def test_only_tags_gives_an_empty_transcript_not_an_error(self) -> None:
        self.assertEqual(self._run([{"offsets": {"from": 0, "to": 900}, "text": " [BLANK_AUDIO]"}]), [])

    def test_the_vocabulary_reaches_the_decoder_with_full_context(self) -> None:
        passages = [{"offsets": {"from": 0, "to": 500}, "text": " Hello."}]
        with patch.object(processing, "_run_asr", return_value=passages) as run:
            processing._transcribe(self.audio, Path("/model.bin"), self.output, ["CubeSat"])
        self.assertEqual(run.call_args.args[2], ["CubeSat"])
        self.assertIsNone(run.call_args.args[3])

    def test_no_output_still_raises_for_the_vocabulary_fallback(self) -> None:
        with self.assertRaises(RuntimeError):
            self._run([])


class EmptyTranscriptJobTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        response = self.client.post("/api/v1/conversations/import",
                                    files={"file": ("note.mp3", mp3_bytes(), "audio/mpeg")})
        self.conversation_id = response.json()["conversation"]["id"]

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_silent_recording_succeeds_with_no_passages(self) -> None:
        model = Path(self.temp.name, "model.bin")
        model.write_bytes(b"model")
        job = processing.request_processing(self.conversation_id)
        with patch.object(processing, "model_paths", return_value=(model, model, model)), \
             patch.object(processing, "_command"), \
             patch.object(processing, "_transcribe", return_value=[]), \
             patch.object(processing, "_diarize", return_value=[]):
            processing.run_job(str(job["id"]))
        result = self.client.get(f"/api/v1/jobs/{job['id']}").json()
        self.assertEqual(result["state"], "succeeded", result.get("error"))
        self.assertEqual(json.loads(storage.get_transcript(self.conversation_id)["segments_json"]), [])
        transcript = self.client.get(f"/api/v1/conversations/{self.conversation_id}/transcript").json()
        self.assertEqual(transcript["segments"], [])


if __name__ == "__main__":
    unittest.main()
