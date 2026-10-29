"""Passages outside every acoustic turn get an uncertain provisional speaker."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from stellis import processing, speaker_fallback, storage
from stellis.server import app
from test_import import mp3_bytes


def passage(index: int, start: int, end: int, cluster: str | None, coverage: float = 0.9) -> dict[str, object]:
    return {"id": f"seg-{index}", "start_ms": start, "end_ms": end, "text": f"Passage {index}.",
            "speaker_cluster_id": cluster, "speaker_uncertain": cluster is None,
            "speaker_coverage": coverage if cluster else 0, "words": []}


class FillUnassignedTest(unittest.TestCase):
    def test_embedding_picks_closest_voice_prototype(self) -> None:
        segments = [passage(0, 0, 3000, "speaker-1"), passage(1, 3000, 6000, "speaker-2"),
                    passage(2, 6000, 9000, None)]
        turns = [{"start_ms": 0, "end_ms": 3000, "speaker_cluster_id": "speaker-1"},
                 {"start_ms": 3000, "end_ms": 6000, "speaker_cluster_id": "speaker-2"}]
        embeddings = {0: np.array([1.0, 0.0]), 1: np.array([0.0, 1.0]), 2: np.array([0.95, 0.31])}
        self.assertEqual(speaker_fallback.fill_unassigned(segments, turns, embeddings), 1)
        # Time alone would pick the adjacent speaker-2 turn.
        self.assertEqual(segments[2]["speaker_cluster_id"], "speaker-1")
        self.assertEqual(segments[2]["speaker_match_source"], "embedding_fallback")
        self.assertTrue(segments[2]["speaker_uncertain"])

    def test_nearest_turn_without_an_embedding(self) -> None:
        segments = [passage(0, 0, 3000, "speaker-1"), passage(1, 20000, 20500, None)]
        turns = [{"start_ms": 0, "end_ms": 3000, "speaker_cluster_id": "speaker-1"},
                 {"start_ms": 21000, "end_ms": 25000, "speaker_cluster_id": "speaker-2"}]
        speaker_fallback.fill_unassigned(segments, turns, {0: np.array([1.0, 0.0])})
        self.assertEqual(segments[1]["speaker_cluster_id"], "speaker-2")
        self.assertEqual(segments[1]["speaker_match_source"], "nearest_turn")

    def test_no_turns_uses_neighbour_then_single_speaker(self) -> None:
        segments = [passage(0, 0, 1000, None), passage(1, 1000, 2000, None)]
        speaker_fallback.fill_unassigned(segments, [])
        self.assertEqual({item["speaker_cluster_id"] for item in segments}, {"speaker-1"})
        self.assertEqual({item["speaker_match_source"] for item in segments}, {"single_speaker"})
        segments = [passage(0, 0, 1000, "speaker-3"), passage(1, 1000, 2000, None)]
        speaker_fallback.fill_unassigned(segments, [])
        self.assertEqual(segments[1]["speaker_cluster_id"], "speaker-3")
        self.assertEqual(segments[1]["speaker_match_source"], "nearest_passage")

    def test_filled_passages_are_not_evidence_for_each_other(self) -> None:
        segments = [passage(0, 0, 1000, None), passage(1, 1000, 2000, None),
                    passage(2, 9000, 12000, "speaker-2")]
        speaker_fallback.fill_unassigned(segments, [])
        self.assertEqual([item["speaker_match_source"] for item in segments[:2]],
                         ["nearest_passage", "nearest_passage"])
        self.assertEqual(segments[0]["speaker_cluster_id"], "speaker-2")

    def test_short_or_uncertain_passages_are_not_prototypes(self) -> None:
        segments = [passage(0, 0, 3000, "speaker-1"), passage(1, 3000, 4000, "speaker-2"),
                    passage(2, 4000, 7000, "speaker-3", coverage=0.5), passage(3, 7000, 10000, None)]
        self.assertEqual(speaker_fallback.prototype_indices(segments), [0])


class DisplayAndJobTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        response = self.client.post("/api/v1/conversations/import",
                                    files={"file": ("talk.mp3", mp3_bytes(), "audio/mpeg")})
        self.conversation_id = response.json()["conversation"]["id"]

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def _save_legacy(self) -> None:
        segments = [passage(0, 0, 3000, "speaker-a"), passage(1, 3000, 3500, None),
                    passage(2, 9000, 12000, "speaker-b")]
        turns = [{"start_ms": 0, "end_ms": 2900, "speaker_cluster_id": "speaker-a"},
                 {"start_ms": 9000, "end_ms": 12000, "speaker_cluster_id": "speaker-b"}]
        storage.save_transcript({"conversation_id": self.conversation_id, "revision": 1,
                                 "pipeline_version": "legacy-test", "language": "en",
                                 "segments_json": json.dumps(segments), "turns_json": json.dumps(turns),
                                 "updated_at": processing.now()})

    def test_legacy_generation_never_shows_unknown_speaker(self) -> None:
        self._save_legacy()
        data = self.client.get(f"/api/v1/conversations/{self.conversation_id}/transcript").json()
        names = [item["speaker_display_name"] for item in data["segments"]]
        self.assertEqual(names, ["Speaker 1", "Speaker 1", "Speaker 2"])
        self.assertTrue(data["segments"][1]["speaker_uncertain"])
        self.assertNotIn("Unknown speaker", json.dumps(data))

    def test_manual_labels_keep_precedence_over_the_fallback(self) -> None:
        self._save_legacy()
        revision = storage.get_transcript(self.conversation_id)["revision"]
        revision = storage.set_speaker_label(self.conversation_id, "cluster", "speaker-a", "Avery", revision)
        storage.set_speaker_label(self.conversation_id, "segment", "seg-1", "Jordan", revision)
        segments = storage.get_display_transcript(self.conversation_id)["segments"]
        self.assertEqual([item["speaker_display_name"] for item in segments], ["Avery", "Jordan", "Speaker 2"])
        self.assertEqual(segments[1]["speaker_label_source"], "manual_segment")

    def test_processing_job_assigns_passages_outside_turns(self) -> None:
        model = Path(self.temp.name, "model.bin")
        model.write_bytes(b"model")
        job = processing.request_processing(self.conversation_id)
        segments = [{"start_ms": 0, "end_ms": 3000, "text": "Covered by a turn."},
                    {"start_ms": 5000, "end_ms": 7000, "text": "Skipped by segmentation."}]
        turns = [{"start_ms": 0, "end_ms": 3000, "speaker_cluster_id": "speaker-1"}]

        def vectors(_wav, _model, items):
            return np.ones((len(items), 2), dtype=np.float32) / np.sqrt(2), np.ones(len(items), dtype=bool)

        with patch.object(processing, "model_paths", return_value=(model, model, model)), \
             patch.object(processing, "_command"), \
             patch.object(processing, "_transcribe", return_value=segments), \
             patch.object(processing, "_diarize", return_value=turns), \
             patch("stellis.rematch.extract_embeddings", side_effect=vectors) as extract:
            processing.run_job(str(job["id"]))
        result = self.client.get(f"/api/v1/jobs/{job['id']}").json()
        self.assertEqual(result["state"], "succeeded", result.get("error"))
        self.assertEqual(result["metrics"]["fallback_speaker_segment_count"], 1)
        self.assertEqual(len(extract.call_args.args[2]), 2)
        stored = json.loads(storage.get_transcript(self.conversation_id)["segments_json"])
        self.assertEqual([item["speaker_cluster_id"] for item in stored], ["speaker-1", "speaker-1"])
        self.assertEqual(stored[1]["speaker_match_source"], "embedding_fallback")
        self.assertTrue(stored[1]["speaker_uncertain"])


if __name__ == "__main__":
    unittest.main()
