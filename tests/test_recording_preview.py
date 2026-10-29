"""The live preview reuses one decoder and preserves verified chunk recovery."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import recording_preview, tools
from stellis.server import app


FAKE_WORKER = '''#!/usr/bin/env python3
import base64
import json
import sys
count = 0
for line in sys.stdin:
    request = json.loads(line)
    count += len(base64.b64decode(request["pcm"])) // 4
    print(json.dumps({"segments": [], "partial": "spoken sample", "partial_start_ms": 0,
                      "duration_ms": count * 1000 // 16000}), flush=True)
'''


class RecordingPreviewTest(unittest.TestCase):
    def test_preview_reuses_worker_and_survives_readback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "fake-worker"
            binary.write_text(FAKE_WORKER)
            binary.chmod(0o700)
            online = root / "online"; online.mkdir()
            parakeet = root / "parakeet"; parakeet.mkdir()
            environment = {"STELLIS_DATA_DIR": directory, "STELLIS_AUDIO_WORKER": str(binary),
                           "STELLIS_ONLINE_ASR_MODEL_DIR": str(online),
                           "STELLIS_PARAKEET_MODEL_DIR": str(parakeet)}
            with patch.dict(os.environ, environment):
                client = TestClient(app)
                session = client.post("/api/v1/recordings", json={"title": "Preview", "media_type": "audio/webm"}).json()
                path = root / "sample.webm"
                subprocess.run([tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                                "-i", "sine=frequency=440:duration=2", "-c:a", "libopus", str(path)],
                               check=True, timeout=20)
                data = path.read_bytes()
                step = (len(data) + 4) // 5
                chunks = [data[index:index + step] for index in range(0, len(data), step)]
                for sequence, chunk in enumerate(chunks[:3]):
                    response = client.put(f"/api/v1/recordings/{session['id']}/chunks/{sequence}", content=chunk)
                    self.assertEqual(response.status_code, 200)
                first = client.post(f"/api/v1/recordings/{session['id']}/preview")
                self.assertEqual(first.status_code, 200, first.text)
                pid = recording_preview._WORKERS[session["id"]].ffmpeg.pid
                for sequence, chunk in enumerate(chunks[3:], start=3):
                    response = client.put(f"/api/v1/recordings/{session['id']}/chunks/{sequence}", content=chunk)
                    self.assertEqual(response.status_code, 200)
                for _ in range(30):
                    response = client.post(f"/api/v1/recordings/{session['id']}/preview")
                    self.assertEqual(response.status_code, 200, response.text)
                    result = response.json()
                    if result["segments"] and result["segments"][0]["end_ms"] >= 1500:
                        break
                    time.sleep(0.1)
                self.assertEqual(result["segments"][0]["text"], "spoken sample")
                self.assertLessEqual(result["segments"][0]["end_ms"], 2100)
                self.assertEqual(recording_preview._WORKERS[session["id"]].ffmpeg.pid, pid)
                self.assertEqual(client.get(f"/api/v1/recordings/{session['id']}/preview").json(), result)
                recording_preview.stop(session["id"])
                for _ in range(30):
                    recovered = client.post(f"/api/v1/recordings/{session['id']}/preview").json()
                    worker_reply = recording_preview._WORKERS[session["id"]].reply
                    if worker_reply and worker_reply["duration_ms"] >= 1500:
                        break
                    time.sleep(0.1)
                self.assertEqual(recovered["segments"][0]["text"], "spoken sample")
                self.assertNotEqual(recording_preview._WORKERS[session["id"]].ffmpeg.pid, pid)
                finalized = client.post(f"/api/v1/recordings/{session['id']}/finalize")
                self.assertEqual(finalized.status_code, 200, finalized.text)
                self.assertNotIn(session["id"], recording_preview._WORKERS)
                self.assertFalse((root / "recordings" / session["id"] / "preview.json").exists())
                client.close()


if __name__ == "__main__":
    unittest.main()
