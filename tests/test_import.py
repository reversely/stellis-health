"""Browser-facing import and playback checks with generated audio."""

from __future__ import annotations

import io
import os
import struct
import subprocess
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis.server import app
from stellis import storage, tools


def wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(struct.pack("<16000h", *([0] * 16000)))
    return output.getvalue()


def mp3_bytes(content: bytes | None = None) -> bytes:
    return subprocess.run(
        [tools.ffmpeg(), "-v", "error", "-f", "wav", "-i", "pipe:0", "-f", "mp3", "pipe:1"],
        input=content if content is not None else wav_bytes(), capture_output=True, check=True,
    ).stdout


class ImportWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_upload_replay_range_and_duplicate(self) -> None:
        self.assertEqual(self.client.get("/api/v1/health").json()["api_version"], "v1")
        content = mp3_bytes()
        with patch("stellis.server.processing.capabilities", return_value={"features": {"transcribe": True, "diarize": True}}):
            imported = self.client.post("/api/v1/conversations/import", files={"file": ("interview.mp3", content, "audio/mpeg")})
        self.assertEqual(imported.status_code, 200)
        record = imported.json()["conversation"]
        self.assertFalse(imported.json()["duplicate"])
        self.assertEqual(imported.json()["processing_job"]["state"], "queued")
        self.assertEqual(imported.json()["processing_job"]["conversation_id"], record["id"])
        self.assertEqual(self.client.get(f"/api/v1/conversations/{record['id']}/processing-job").json()["id"],
                         imported.json()["processing_job"]["id"])
        self.assertGreaterEqual(record["duration_ms"], 1000)
        self.assertLess(record["duration_ms"], 1200)
        self.assertEqual(len(self.client.get("/api/v1/conversations").json()), 1)
        self.assertEqual(self.client.get(f"/api/v1/conversations/{record['id']}").json(), record)
        self.assertEqual(self.client.get(f"/conversation/{record['id']}").status_code, 200)
        audio = self.client.get(f"/api/v1/conversations/{record['id']}/audio", headers={"Range": "bytes=0-15"})
        self.assertEqual(audio.status_code, 206)
        self.assertEqual(audio.content, content[:16])
        self.assertEqual(audio.headers["content-range"], f"bytes 0-15/{len(content)}")
        again = self.client.post("/api/conversations/import", files={"file": ("renamed.mp3", content, "audio/mpeg")})
        self.assertTrue(again.json()["duplicate"])
        self.assertEqual(again.json()["conversation"]["id"], record["id"])
        self.assertEqual(again.json()["processing_job"]["id"], imported.json()["processing_job"]["id"])
        self.assertEqual(len(self.client.get("/api/conversations").json()), 1)
        self.assertEqual(Path(self.temp.name, "stellis.sqlite3").stat().st_mode & 0o777, 0o600)
        # A recording without an imported transcript answers null, so its page logs no error.
        none_yet = self.client.get(f"/api/conversations/{record['id']}/import-reference")
        self.assertEqual((none_yet.status_code, none_yet.json()), (200, None))
        storage.save_import_reference(record["id"], "Speaker 1  00:00\nHello.", "Summary", "export.zip")
        reference = self.client.get(f"/api/conversations/{record['id']}/import-reference")
        self.assertEqual(reference.status_code, 200)
        self.assertIn("Hello.", reference.json()["transcript_text"])

    def test_import_title_drops_middle_dots(self) -> None:
        with patch("stellis.server.processing.capabilities", return_value={"features": {"transcribe": False}}):
            imported = self.client.post("/api/v1/conversations/import",
                                        files={"file": ("Note \u00b7 Oct 15, 2:17 PM \u00b7 57 sec.mp3", mp3_bytes(), "audio/mpeg")})
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json()["conversation"]["title"], "Note Oct 15, 2:17 PM 57 sec")

    def test_zip_is_rejected_and_reference_is_separate(self) -> None:
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("interview/audio.wav", wav_bytes())
            archive.writestr("interview/transcript.txt", "A private transcript")
        imported = self.client.post("/api/conversations/import", files={"file": ("export.zip", archive_bytes.getvalue(), "application/zip")})
        self.assertEqual(imported.status_code, 400)
        self.assertEqual(self.client.get("/api/conversations").json(), [])

    def test_reimport_starts_transcription_after_models_become_ready(self) -> None:
        content = mp3_bytes()
        with patch("stellis.server.processing.capabilities", return_value={"features": {"transcribe": False, "diarize": False}}):
            first = self.client.post("/api/conversations/import", files={"file": ("interview.mp3", content, "audio/mpeg")})
        self.assertIsNone(first.json()["processing_job"])
        with patch("stellis.server.processing.capabilities", return_value={"features": {"transcribe": True, "diarize": True}}):
            again = self.client.post("/api/conversations/import", files={"file": ("interview.mp3", content, "audio/mpeg")})
        self.assertTrue(again.json()["duplicate"])
        self.assertEqual(again.json()["processing_job"]["state"], "queued")

    def test_invalid_files_are_rejected_without_records(self) -> None:
        for name, content in [("empty.mp3", b""), ("bad.mp3", b"not audio"),
                              ("bad.zip", b"not zip"), ("valid.wav", wav_bytes())]:
            with self.subTest(name=name):
                response = self.client.post("/api/conversations/import", files={"file": (name, content)})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/conversations").json(), [])


if __name__ == "__main__":
    unittest.main()
