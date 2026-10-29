"""Meeting notes survive recording finalization and reject stale writes."""

from __future__ import annotations

from stellis import tools
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis.server import app


class NotesWorkflowTest(unittest.TestCase):
    def test_recording_notes_move_to_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STELLIS_DATA_DIR": directory}):
            client = TestClient(app)
            session = client.post("/api/v1/recordings", json={"title": "Notes", "media_type": "audio/webm"}).json()
            endpoint = f"/api/v1/recordings/{session['id']}/notes"
            self.assertEqual(client.get(endpoint).json()["revision"], 0)
            saved = client.put(endpoint, json={"markdown": "# Interview\n- Follow up", "expected_revision": 0})
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()["revision"], 1)
            self.assertEqual(client.put(endpoint, json={"markdown": "Lost", "expected_revision": 0}).status_code, 409)
            self.assertEqual(client.get(endpoint).json()["markdown"], "# Interview\n- Follow up")

            media = Path(directory) / "sample.webm"
            subprocess.run([tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "sine=frequency=440:duration=2", "-c:a", "libopus", str(media)],
                           check=True, timeout=20)
            encoded = media.read_bytes()
            midpoint = len(encoded) // 2
            for sequence, chunk in enumerate((encoded[:midpoint], encoded[midpoint:])):
                self.assertEqual(client.put(f"/api/v1/recordings/{session['id']}/chunks/{sequence}",
                                            content=chunk).status_code, 200)
            finished = client.post(f"/api/v1/recordings/{session['id']}/finalize")
            self.assertEqual(finished.status_code, 200, finished.text)
            conversation_id = finished.json()["conversation"]["id"]
            conversation_notes = f"/api/v1/conversations/{conversation_id}/notes"
            self.assertEqual(client.get(conversation_notes).json()["markdown"], "# Interview\n- Follow up")
            updated = client.put(conversation_notes,
                                 json={"markdown": "# Interview\n- Follow up\n- Send draft", "expected_revision": 1})
            self.assertEqual(updated.json()["revision"], 2)
            self.assertEqual(client.get(endpoint).status_code, 404)
            client.close()


if __name__ == "__main__":
    unittest.main()
