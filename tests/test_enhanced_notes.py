"""Enhanced Notes preserve versions, edits, note dependencies, and verified sources."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import ai, enhanced_notes, storage
from stellis.server import app
from test_import import wav_bytes, mp3_bytes


class EnhancedNotesWorkflowTest(unittest.TestCase):
    def test_image_references_do_not_expose_local_ids_to_provider(self) -> None:
        image_id = "12345678-1234-1234-1234-123456789abc"
        markdown = f"Agenda\n\n![Shared screen](stellis-image:{image_id})\n\n| Topic | Owner |"
        cleaned = enhanced_notes._notes_for_prompt(markdown)
        self.assertIn("[Image: Shared screen]", cleaned)
        self.assertIn("| Topic | Owner |", cleaned)
        self.assertNotIn(image_id, cleaned)

    def test_prompt_text_drops_ids_and_the_limit_counts_it(self) -> None:
        cleaned = enhanced_notes._notes_for_prompt("- [ ] call ^tabc123\n<columns>\n<column>\nleft\n</column>\n</columns>")
        self.assertEqual(cleaned, "- [ ] call\nleft")
        # 700 task lines are over 8,000 bytes stored, and under it once the 11-byte IDs are dropped.
        stored = "".join(f"- [ ] t ^t{index:06d}\n" for index in range(700))
        self.assertGreater(len(stored.encode()), enhanced_notes.MAX_INPUT_NOTES)
        self.assertLess(len(enhanced_notes._notes_for_prompt(stored).encode()), enhanced_notes.MAX_INPUT_NOTES)

    def test_generate_edit_refresh_and_note_staleness(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STELLIS_DATA_DIR": directory}):
            client = TestClient(app)
            imported = client.post("/api/v1/conversations/import",
                                   files={"file": ("talk.mp3", mp3_bytes(), "audio/mpeg")}).json()
            conversation_id = imported["conversation"]["id"]
            segment = {"id":"seg-1", "start_ms":250, "end_ms":900, "text":"Avery will send the CubeSat draft.",
                       "speaker_cluster_id":"speaker-1", "speaker_uncertain":False, "words":[]}
            storage.save_transcript({"conversation_id":conversation_id,"revision":1,"language":"en",
                                     "segments_json":json.dumps([segment]),"turns_json":"[]",
                                     "updated_at":datetime.now(timezone.utc).isoformat()})
            base = f"/api/v1/conversations/{conversation_id}"
            client.put(f"{base}/notes", json={"markdown":"Follow up on CubeSat draft.","expected_revision":0})
            with patch.object(ai, "_key", return_value="test-key"), \
                 patch.object(enhanced_notes.storage, "background"):
                first = client.post(f"{base}/enhanced-notes", json={}).json()
                repeated = client.post(f"{base}/enhanced-notes", json={}).json()
            self.assertEqual(first["id"], repeated["id"])
            requests = []
            payload = {"overview":[{"text":"Avery will send the draft.","source_segment_ids":["s1"]}],
                       "topics":[],"decisions":[],"action_items":[]}
            def respond(messages, max_tokens, metadata, kind):
                requests.append(messages)
                return payload
            with patch.object(ai, "_key", return_value="test-key"), patch.object(ai, "_request", side_effect=respond):
                enhanced_notes._run(first["id"])
            finished = client.get(f"/api/v1/enhanced-notes/{first['id']}").json()
            self.assertEqual(finished["state"], "succeeded")
            self.assertEqual(finished["result"]["overview"][0]["source_segment_ids"], ["seg-1"])
            self.assertEqual(finished["source_starts"]["seg-1"], 250)
            self.assertIn("Follow up on CubeSat draft", requests[-1][-1]["content"])
            edit = client.put(f"/api/v1/enhanced-notes/{first['id']}",
                              json={"markdown":"## Overview\n\n- Edited note", "expected_revision":0})
            self.assertEqual(edit.status_code, 200)
            self.assertEqual(edit.json()["edit_revision"], 1)
            self.assertEqual(client.put(f"/api/v1/enhanced-notes/{first['id']}",
                                        json={"markdown":"Lost", "expected_revision":0}).status_code, 409)
            with patch.object(ai, "_key", return_value="test-key"), patch.object(enhanced_notes.storage, "background"):
                refreshed = client.post(f"{base}/enhanced-notes", json={"force":True}).json()
            self.assertNotEqual(refreshed["id"], first["id"])
            self.assertEqual(len(client.get(f"{base}/enhanced-notes").json()), 2)
            invalid = {"overview":[{"text":"Invented","source_segment_ids":["s99"]}],
                       "topics":[],"decisions":[],"action_items":[]}
            with patch.object(ai, "_key", return_value="test-key"), patch.object(ai, "_request", return_value=invalid), \
                    self.assertLogs("stellis.enhanced_notes", "WARNING") as logs:
                enhanced_notes._run(refreshed["id"])
            failed = client.get(f"/api/v1/enhanced-notes/{refreshed['id']}").json()
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(failed["error_code"], "invalid_source")
            self.assertEqual(failed["error_message"], "The model cited a passage outside its evidence")
            self.assertIn("invalid_source", logs.output[0])
            self.assertEqual(client.get(f"/api/v1/enhanced-notes/{first['id']}").json()["edited_markdown"],
                             "## Overview\n\n- Edited note")
            client.put(f"{base}/notes", json={"markdown":"Revised raw note", "expected_revision":1})
            self.assertTrue(client.get(f"/api/v1/enhanced-notes/{first['id']}").json()["stale"])
            self.assertEqual(client.get(f"/api/v1/enhanced-notes/{first['id']}").json()["edited_markdown"],
                             "## Overview\n\n- Edited note")
            client.close()


if __name__ == "__main__":
    unittest.main()
