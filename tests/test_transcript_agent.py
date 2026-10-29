"""The Transcript Agent proposes to-do suggestions after a Summary succeeds (ADR 0059, #263)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import ai, enhanced_notes, note_rules, notebooks, storage, suggestions, todos, transcript_agent
from stellis.server import app
from test_import import mp3_bytes

API = "/api/v1"
SUMMARY = {"overview": [{"text": "Avery will send the draft.", "source_segment_ids": ["s1"]}], "topics": [],
           "decisions": [], "action_items": [
               {"text": "Avery sends the CubeSat draft", "source_segment_ids": ["s1"]},
               {"text": "Book the venue", "source_segment_ids": ["s2"]},
               {"text": "Check with legal", "source_segment_ids": ["s2"]}]}



def _run_id(background):
    """The run id handed to the transcript agent's background run, if one started."""
    runs = [call.args[1] for call in background.call_args_list if call.args[0] is transcript_agent._run]
    return runs[-1] if runs else None

class TranscriptAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def setup_meeting(self, todo_list="on", page=True) -> tuple[str, str, dict[str, str]]:
        notebook_id = self.client.post(f"{API}/notebooks", json={"name": f"Work {todo_list}"}).json()["id"]
        self.client.patch(f"{API}/notebooks/{notebook_id}", json={"todo_list": todo_list})
        if page:
            self.client.post(f"{API}/pages", json={"notebook_id": notebook_id, "markdown":
                             "- [ ] Book the venue\n- [ ] Email legal\n- [ ] Order badges"})
        open_todos = {item["text"]: item["id"]
                      for group in todos.gather(notebook_id)["groups"] for item in group["items"]}
        conversation_id = self.client.post(f"{API}/conversations/import",
                                           files={"file": ("talk.mp3", mp3_bytes(), "audio/mpeg")}).json()["conversation"]["id"]
        notebooks.assign(conversation_id, notebook_id)
        segments = [{"id": "seg-1", "start_ms": 250, "end_ms": 900, "text": "Avery will send the CubeSat draft.",
                     "speaker_cluster_id": "speaker-1", "speaker_uncertain": False, "words": []},
                    {"id": "seg-2", "start_ms": 1000, "end_ms": 2000, "text": "Legal is done and we booked the venue.",
                     "speaker_cluster_id": "speaker-1", "speaker_uncertain": False, "words": []}]
        storage.save_transcript({"conversation_id": conversation_id, "revision": 1, "language": "en",
                                 "segments_json": json.dumps(segments), "turns_json": "[]",
                                 "updated_at": datetime.now(timezone.utc).isoformat()})
        return notebook_id, conversation_id, open_todos

    def summarize(self, conversation_id: str) -> str | None:
        """Run a Summary to success and return the agent run it queued, if any."""
        with patch.object(ai, "_key", return_value="test-key"), patch.object(enhanced_notes.storage, "background"):
            summary = enhanced_notes.request(conversation_id, force=True)
        with patch.object(ai, "_key", return_value="test-key"), patch.object(ai, "_request", return_value=SUMMARY), \
                patch.object(transcript_agent.storage, "background") as thread:
            enhanced_notes._run(summary["id"])
        return _run_id(thread)

    def agent(self, run_id: str, payload: dict) -> dict:
        with patch.object(ai, "_key", return_value="test-key"), patch.object(ai, "_request", return_value=payload):
            transcript_agent._run(run_id)
        response = self.client.get(f"{API}/conversations/{self.conversation_id}/agent-runs")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()[0]

    def test_summary_and_to_dos_see_the_notebook_goals(self) -> None:
        notebook_id, self.conversation_id, _ = self.setup_meeting()
        self.client.post(f"{API}/pages", json={"notebook_id": notebook_id, "title": "Goals",
                                                "markdown": "- [ ] Bring my A1C under 7"})
        self.assertIn("Bring my A1C under 7", note_rules.goals(notebook_id))
        with patch.object(ai, "_key", return_value="test-key"), patch.object(enhanced_notes.storage, "background"):
            summary = enhanced_notes.request(self.conversation_id, force=True)
        with patch.object(ai, "_key", return_value="test-key"), \
                patch.object(ai, "_request", return_value=SUMMARY) as request, \
                patch.object(transcript_agent.storage, "background") as thread:
            enhanced_notes._run(summary["id"])
        summary_messages = request.call_args.args[0]
        self.assertIn("never guess what was meant", summary_messages[0]["content"])
        self.assertIn("Bring my A1C under 7", summary_messages[-1]["content"])
        with patch.object(ai, "_key", return_value="test-key"), \
                patch.object(ai, "_request", return_value={"adds": [], "closes": []}) as request:
            transcript_agent._run(_run_id(thread))
        self.assertIn("Bring my A1C under 7", request.call_args.args[0][-1]["content"])

    def test_adds_skip_duplicates_and_rerun_supersedes(self) -> None:
        _, self.conversation_id, open_todos = self.setup_meeting()
        payload = {"adds": [
            {"item": 1, "text": "Send the CubeSat draft to Avery.", "existing_todo_id": None},
            {"item": 2, "text": "  book THE venue ", "existing_todo_id": None},
            {"item": 3, "text": "Check with legal", "existing_todo_id": open_todos["Email legal"]}], "closes": []}
        run_id = self.summarize(self.conversation_id)
        run = self.agent(run_id, payload)
        self.assertEqual((run["state"], run["stage"], run["prompt_version"]), ("succeeded", "complete", transcript_agent.PROMPT_VERSION))
        self.assertEqual(run["result"], {"added": 1, "closed": 0})
        pending = suggestions.list_pending("conversation", self.conversation_id)
        self.assertEqual([item["values"]["text"] for item in pending], ["Send the CubeSat draft to Avery"])
        self.assertEqual(pending[0]["author"], "transcript-agent")
        self.assertEqual(pending[0]["evidence"], [{"segment_id": "seg-1", "quote": "Avery will send the CubeSat draft."}])
        self.agent(self.summarize(self.conversation_id), payload)
        again = suggestions.list_pending("conversation", self.conversation_id)
        self.assertEqual(len(again), 1)
        self.assertNotEqual(again[0]["id"], pending[0]["id"])

    def test_close_needs_a_cited_quote(self) -> None:
        _, self.conversation_id, open_todos = self.setup_meeting()
        payload = {"adds": [], "closes": [
            {"todo_id": open_todos["Email legal"], "status": "done", "segment_id": "s2", "quote": "Legal is done"},
            {"todo_id": open_todos["Order badges"], "status": "canceled", "segment_id": "s2", "quote": ""},
            {"todo_id": open_todos["Book the venue"], "status": "done", "segment_id": "s1", "quote": "we booked the venue"}]}
        self.agent(self.summarize(self.conversation_id), payload)
        pending = suggestions.list_pending("conversation", self.conversation_id) + suggestions.list_pending(
            "page", todos.gather()["groups"][0]["source"]["id"])
        self.assertEqual([(item["todo_id"], item["values"]["status"]) for item in pending],
                         [(open_todos["Email legal"], "done")])
        self.assertEqual(pending[0]["evidence"], [{"segment_id": "seg-2", "quote": "Legal is done"}])

    def test_off_setting_and_missing_key_skip_silently(self) -> None:
        _, conversation_id, _ = self.setup_meeting(todo_list="off")
        self.assertIsNone(self.summarize(conversation_id))
        _, conversation_id, _ = self.setup_meeting()
        with patch.object(ai, "_key", return_value=None):
            self.assertIsNone(transcript_agent.start(conversation_id, "summary-run"))
        self.assertEqual(transcript_agent.list_runs(conversation_id), [])

    def test_empty_run_is_skipped_without_a_model_call(self) -> None:
        _, self.conversation_id, _ = self.setup_meeting(page=False)
        with patch.object(ai, "_key", return_value="test-key"), patch.object(enhanced_notes.storage, "background"):
            summary = enhanced_notes.request(self.conversation_id, force=True)
        with patch.object(ai, "_key", return_value="test-key"), patch.object(transcript_agent.storage, "background") as thread, \
                patch.object(ai, "_request", return_value={**SUMMARY, "action_items": []}):
            enhanced_notes._run(summary["id"])
        with patch.object(ai, "_key", return_value="test-key"), patch.object(ai, "_request") as request:
            transcript_agent._run(_run_id(thread))
        request.assert_not_called()
        run = self.client.get(f"{API}/conversations/{self.conversation_id}/agent-runs").json()[0]
        self.assertEqual((run["state"], run["result"]["skipped"]), ("skipped", "no_action_items_or_open_todos"))

    def test_long_transcript_keeps_cited_and_todo_passages(self) -> None:
        filler = [{"id": f"f{i}", "text": "x" * 900} for i in range(40)]
        segments = (filler[:20] + [{"id": "cite", "text": "Avery sends it"}] + filler[20:]
                    + [{"id": "close", "text": "The venue is booked."}])
        items = [{"text": "Send", "source_segment_ids": ["cite"]}]
        kept = transcript_agent._select(items, [{"text": "Book the venue"}], segments)
        ids = [segment["id"] for segment in kept]
        self.assertIn("cite", ids)
        self.assertEqual(ids[-1], "close")
        self.assertEqual(ids, [segment["id"] for segment in segments if segment["id"] in ids])
        self.assertLessEqual(sum(len(s["text"]) + 40 for s in kept), transcript_agent.MAX_EVIDENCE_CHARS)
