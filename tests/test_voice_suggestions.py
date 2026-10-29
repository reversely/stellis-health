"""Known speakers are suggested by voice, and names say where they came from (#210)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from stellis import storage, voices
from stellis.server import app
from test_import import mp3_bytes, wav_bytes

RNG = np.random.default_rng(7)
RILEY, OTHER = RNG.normal(size=192), RNG.normal(size=192)


def voice(center: np.ndarray, noise: float = 0.15) -> np.ndarray:
    vector = center + noise * np.linalg.norm(center) / 14 * RNG.normal(size=192)
    return vector / np.linalg.norm(vector)


class VoiceSuggestionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        self.known = self._conversation("known.mp3", mp3_bytes())
        self.fresh = self._conversation("fresh.mp3", mp3_bytes(wav_bytes()[:-2] + b"\x01\x00"))

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def _conversation(self, name: str, audio: bytes) -> str:
        conversation = self.client.post("/api/v1/conversations/import",
                                        files={"file": (name, audio, "audio/mpeg")}).json()["conversation"]["id"]
        segments = [{"id": f"{name}-{index}", "start_ms": index * 100, "end_ms": index * 100 + 90,
                     "text": f"words {index}", "speaker_cluster_id": "speaker-1" if index < 4 else "speaker-2",
                     "speaker_uncertain": False, "words": []} for index in range(8)]
        storage.save_transcript({"conversation_id": conversation, "revision": 1, "pipeline_version": "test",
                                 "language": "en", "segments_json": json.dumps(segments), "turns_json": "[]",
                                 "updated_at": datetime.now(timezone.utc).isoformat()})
        return conversation

    def _index(self, conversation: str, first: np.ndarray, second: np.ndarray) -> None:
        segments = json.loads(storage.get_transcript(conversation)["segments_json"])
        vectors = np.stack([voice(first if index < 4 else second) for index in range(8)])
        voices.save(conversation, segments, vectors, np.ones(8, dtype=bool))

    def _confirm_speaker(self, conversation: str, segment_id: str, name: str) -> dict:
        revision = storage.get_transcript(conversation)["revision"]
        response = self.client.put(f"/api/v1/conversations/{conversation}/person-links", json={
            "name": name, "segment_id": segment_id, "scope": "cluster", "expected_revision": revision})
        self.assertEqual(response.status_code, 200, response.text)
        return self.client.get(f"/api/v1/conversations/{conversation}/transcript").json()

    def test_speaker_wide_confirmation_marks_only_the_clicked_passage_as_manual(self) -> None:
        after = self._confirm_speaker(self.known, "known.mp3-2", "Riley Quinn")
        origins = {item["id"]: item.get("speaker_label_origin") for item in after["segments"][:4]}
        self.assertEqual(origins["known.mp3-2"], "manual")
        self.assertEqual({origins[key] for key in origins if key != "known.mp3-2"}, {"propagated"})
        self.assertEqual(after["segments"][5]["speaker_label_origin"], "automatic")

    def test_a_group_mostly_named_for_one_person_shows_that_name_throughout(self) -> None:
        after = self._confirm_speaker(self.known, "known.mp3-0", "Riley Quinn")
        person = after["segments"][0]["person_id"]
        with storage.connect() as conn:
            conn.execute("DELETE FROM person_links WHERE conversation_id = ? AND segment_id = 'known.mp3-3'", (self.known,))
        segments = self.client.get(f"/api/v1/conversations/{self.known}/transcript").json()["segments"]
        self.assertEqual(segments[3]["speaker_display_name"], "Riley Quinn")
        self.assertEqual(segments[3]["speaker_label_origin"], "propagated")
        self.assertNotIn("person_id", segments[3])
        self.assertEqual(segments[5]["speaker_display_name"], "Speaker 2")
        self.assertEqual(self.client.get(f"/api/v1/persons/{person}").json()["total_link_count"], 3)

    def test_a_known_voice_is_suggested_and_one_click_confirms_it(self) -> None:
        self._index(self.known, RILEY, OTHER)
        self._index(self.fresh, OTHER, RILEY)
        person = self._confirm_speaker(self.known, "known.mp3-0", "Riley Quinn")["segments"][0]["person_id"]
        fresh = self.client.get(f"/api/v1/conversations/{self.fresh}/transcript").json()["segments"]
        self.assertEqual({item.get("speaker_suggestion", {}).get("person_id") for item in fresh[4:]}, {person})
        self.assertTrue(all("speaker_suggestion" not in item for item in fresh[:4]))
        # Nothing is confirmed until the user clicks.
        self.assertTrue(all(not item.get("person_id") for item in fresh))
        self.assertEqual(self.client.get(f"/api/v1/persons/{person}").json()["total_link_count"], 4)
        revision = storage.get_transcript(self.fresh)["revision"]
        confirmed = self.client.put(f"/api/v1/conversations/{self.fresh}/person-links", json={
            "person_id": person, "segment_id": "fresh.mp3-4", "scope": "cluster", "expected_revision": revision})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        after = self.client.get(f"/api/v1/conversations/{self.fresh}/transcript").json()["segments"]
        self.assertEqual({item.get("person_id") for item in after[4:]}, {person})
        self.assertTrue(all("speaker_suggestion" not in item for item in after))

    def test_an_unfamiliar_voice_gets_no_suggestion(self) -> None:
        self._index(self.known, RILEY, OTHER)
        self._index(self.fresh, RNG.normal(size=192), RNG.normal(size=192))
        self._confirm_speaker(self.known, "known.mp3-0", "Riley Quinn")
        fresh = self.client.get(f"/api/v1/conversations/{self.fresh}/transcript").json()["segments"]
        self.assertTrue(all("speaker_suggestion" not in item for item in fresh))

    def test_older_links_are_classified_by_how_they_were_written(self) -> None:
        stamp = datetime.now(timezone.utc).isoformat()
        with storage.connect() as conn:
            person = conn.execute("INSERT INTO persons (id, name, name_key, created_at, updated_at) "
                                  "VALUES ('p', 'Devon', 'devon', ?, ?) RETURNING id", (stamp, stamp)).fetchone()["id"]
            for index, when in [(0, "batch"), (1, "batch"), (2, "batch"), (5, "alone")]:
                conn.execute("INSERT INTO person_links (conversation_id, segment_id, person_id, start_ms, confirmed_at, origin) "
                             "VALUES (?, ?, ?, 0, ?, NULL)", (self.known, f"known.mp3-{index}", person, when))
        with storage.connect() as conn:
            origins = dict(conn.execute("SELECT segment_id, origin FROM person_links WHERE conversation_id = ?",
                                        (self.known,)).fetchall())
        self.assertEqual(origins, {"known.mp3-0": "propagated", "known.mp3-1": "propagated",
                                   "known.mp3-2": "propagated", "known.mp3-5": "manual"})

    def test_a_recording_with_no_speech_indexes_without_failing(self) -> None:
        from stellis import processing
        storage.save_transcript({"conversation_id": self.fresh, "revision": 2, "pipeline_version": "test",
                                 "language": "en", "segments_json": "[]", "turns_json": "[]",
                                 "updated_at": datetime.now(timezone.utc).isoformat()})
        job = processing.request_voice_index(self.fresh)
        processing.run_voice_index_job(str(job["id"]))
        self.assertEqual(storage.get_job(str(job["id"]))["state"], "succeeded")

    def test_deleting_a_conversation_removes_its_voices(self) -> None:
        self._index(self.known, RILEY, OTHER)
        self.client.post(f"/api/v1/conversations/{self.known}/trash")
        self.assertEqual(self.client.delete(f"/api/v1/trash/{self.known}").status_code, 200)
        self.assertFalse(voices.has_index(self.known))


if __name__ == "__main__":
    unittest.main()
