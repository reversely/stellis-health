"""Live AI Chat uses a frozen local preview and survives recording finalization."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from stellis import agents, notebooks, recording, recording_ai, tools
from stellis.server import app


class RecordingChatWorkflowTest(unittest.TestCase):
    def test_preview_question_and_saved_source_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STELLIS_DATA_DIR": directory}):
            client = TestClient(app)
            book = notebooks.create("Water Treatment")
            agent = agents.assistant()
            session = client.post("/api/v1/recordings", json={"title":"Live chat", "media_type":"audio/webm",
                                                              "notebook_id":book["id"]}).json()
            base = f"/api/v1/recordings/{session['id']}"
            with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": ""}):
                self.assertEqual(client.post(f"{base}/chat", json={"question":"What happened?"}).status_code, 409)
            preview = {"state":"ready", "through_sequence":2,
                       "segments":[{"start_ms":500,"end_ms":1200,"text":"Avery will send the draft."}]}
            (recording._directory(session["id"]) / "preview.json").write_text(json.dumps(preview))
            with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": "stellis-claw"}), \
                 patch.object(recording_ai.storage, "background"):
                started = client.post(f"{base}/chat", json={"question":"What will Avery send?"}).json()
            self.assertEqual(started["agent_id"], agent["id"])
            reply = {"agent_message": {"content": "Avery will send the draft. [Live transcript 0s](/recording?t=500)"},
                     "cited_sources": [{"source_id": "live-0-500", "target": "/recording?t=500"}]}
            with patch.object(recording_ai.agent_runtime, "ask", new_callable=AsyncMock,
                              return_value=reply) as ask:
                recording_ai._run(started["id"])
            self.assertEqual(ask.await_args.args[:3], (agent["id"], "What will Avery send?", book["id"]))
            self.assertEqual(ask.await_args.args[3][0]["excerpt"], "Avery will send the draft.")
            result = client.get(f"/api/v1/recording-chat/{started['id']}").json()
            self.assertEqual(result["state"], "succeeded")
            self.assertEqual(result["source_starts"][result["result"]["source_segment_ids"][0]], 500)

            with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": "stellis-claw"}), \
                 patch.object(recording_ai.storage, "background"):
                failed = client.post(f"{base}/chat", json={"question":"Will this fail?"}).json()
            with patch.object(recording_ai.agent_runtime, "ask", new_callable=AsyncMock,
                              side_effect=RuntimeError("worker stopped")):
                recording_ai._run(failed["id"])
            failed_result = client.get(f"/api/v1/recording-chat/{failed['id']}").json()
            self.assertEqual(failed_result["state"], "failed")
            self.assertEqual(failed_result["error_code"], "unexpected")

            with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": "stellis-claw"}), \
                 patch.object(recording_ai.storage, "background"):
                background = client.post(f"{base}/chat", json={"question":"What does the specification say?"}).json()
            background_reply = {"agent_message": {"content": "See the specification."},
                                "cited_sources": [{"source_id": "saved-document", "target": "/conversation/other"}]}
            with patch.object(recording_ai.agent_runtime, "ask", new_callable=AsyncMock,
                              return_value=background_reply):
                recording_ai._run(background["id"])
            background_result = client.get(f"/api/v1/recording-chat/{background['id']}").json()
            self.assertEqual(background_result["state"], "succeeded")
            self.assertTrue(background_result["result"]["insufficient_evidence"])
            self.assertEqual(background_result["result"]["source_segment_ids"], [])

            media = Path(directory) / "sample.webm"
            subprocess.run([tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "sine=frequency=440:duration=2", "-c:a", "libopus", str(media)],
                           check=True, timeout=20)
            encoded = media.read_bytes()
            self.assertEqual(client.put(f"{base}/chunks/0", content=encoded).status_code, 200)
            finalized = client.post(f"{base}/finalize")
            self.assertEqual(finalized.status_code, 200, finalized.text)
            conversation_id = finalized.json()["conversation"]["id"]
            saved = client.get(f"/api/v1/conversations/{conversation_id}/recording-chat").json()
            self.assertIn(f"/conversation/{conversation_id}?t=500", saved[0]["result"]["answer"])
            self.assertEqual(client.post(f"{base}/chat", json={"question":"Again?"}).status_code, 409)
            self.assertEqual(client.post(f"/api/v1/conversations/{conversation_id}/trash").status_code, 200)
            self.assertEqual(client.delete(f"/api/v1/trash/{conversation_id}").status_code, 200)
            self.assertEqual(client.get(f"/api/v1/recording-chat/{started['id']}").status_code, 404)
            client.close()


if __name__ == "__main__":
    unittest.main()
