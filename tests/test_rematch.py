"""Acoustic rematch keeps source segments and leaves ambiguous voices unresolved."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from stellis import processing, rematch, storage
from stellis.server import app
from test_import import wav_bytes, mp3_bytes


class RematchTest(unittest.TestCase):
    def test_prototypes_assign_clear_vectors_and_preserve_segments(self) -> None:
        segments = []
        vectors = []
        for cluster, vector in [("speaker-1", [1, 0, 0]), ("speaker-2", [0, 1, 0])]:
            for index in range(12):
                segments.append({"id": f"{cluster}-{index}", "start_ms": index * 3000,
                                 "end_ms": index * 3000 + 2500, "text": "unchanged",
                                 "speaker_cluster_id": cluster, "speaker_coverage": .9,
                                 "words": [{"text":"unchanged", "start_ms":index * 3000 + 100,
                                            "end_ms":index * 3000 + 900, "confidence":.8,
                                            "provenance":"whisper_cpp_token_v1"}]})
                vectors.append(vector)
        segments.append({"id": "unclear", "start_ms": 90000, "end_ms": 93000, "text": "also unchanged",
                         "speaker_cluster_id": None, "speaker_coverage": 0, "words": []})
        vectors.append([.707, .707, 0])
        result, metrics = rematch.suggest_speakers(segments, np.asarray(vectors, dtype=np.float32),
                                                    np.ones(25, dtype=bool))
        self.assertEqual(metrics["prototype_count"], 2)
        self.assertEqual(metrics["suggested_segment_count"], 24)
        self.assertEqual(result[-1]["speaker_match_source"], "unresolved")
        self.assertEqual(result[-1]["speaker_cluster_id"], "speaker-1")
        self.assertTrue(result[-1]["speaker_uncertain"])
        self.assertEqual(result[-1]["id"], "unclear")
        self.assertEqual(result[-1]["text"], "also unchanged")
        self.assertEqual(result[0]["words"], segments[0]["words"])

    def test_browser_rematch_request_is_persisted_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STELLIS_DATA_DIR": directory}):
            with TestClient(app) as client:
                imported = client.post("/api/v1/conversations/import", files={"file": ("talk.mp3", mp3_bytes(), "audio/mpeg")})
                conversation_id = imported.json()["conversation"]["id"]
                segments = [{"id": "seg-1", "start_ms": 0, "end_ms": 1500, "text": "Hello",
                             "speaker_cluster_id": "speaker-1", "speaker_coverage": .9, "words": []}]
                storage.save_transcript({"conversation_id": conversation_id, "revision": 1,
                                         "pipeline_version": "speech-v1", "language": "en",
                                         "segments_json": json.dumps(segments), "turns_json": "[]",
                                         "updated_at": datetime.now(timezone.utc).isoformat()})
                model = f"{directory}/embedding.onnx"
                with open(model, "wb") as output:
                    output.write(b"placeholder")
                with patch.object(processing, "model_paths", return_value=(None, None, Path(model))), \
                     patch.object(processing._QUEUE, "put"):
                    first = client.post(f"/api/v1/conversations/{conversation_id}/speaker-rematch")
                    second = client.post(f"/api/v1/conversations/{conversation_id}/speaker-rematch")
                self.assertEqual(first.status_code, 200)
                self.assertEqual(first.json()["kind"], "rematch")
                self.assertEqual(first.json()["id"], second.json()["id"])
                self.assertEqual(storage.get_job(first.json()["id"])["source_generation_version"], "speech-v1")
                revision = storage.edit_transcript_segment(conversation_id, "seg-1", "Hello there", 1)
                revision = storage.set_speaker_label(conversation_id, "cluster", "speaker-1", "Avery", revision)
                storage.add_highlight(conversation_id, "seg-1", 0, 5, revision)
                aligned = [{**segments[0], "speaker_match_source": "embedding_suggestion",
                            "raw_speaker_cluster_id": "speaker-1", "speaker_uncertain": False}]
                with patch.object(processing, "model_paths", return_value=(None, None, Path(model))), \
                     patch.object(processing, "_command"), \
                     patch.object(rematch, "extract_embeddings", return_value=(np.zeros((1, 2)), np.ones(1, dtype=bool))), \
                     patch.object(rematch, "suggest_speakers", return_value=(aligned, {"suggested_segment_fraction": 1.0})):
                    processing.run_rematch_job(first.json()["id"])
                finished = client.get(f"/api/v1/jobs/{first.json()['id']}").json()
                display = client.get(f"/api/v1/conversations/{conversation_id}/transcript").json()
                self.assertEqual(finished["state"], "succeeded")
                self.assertEqual(display["segments"][0]["text"], "Hello there")
                self.assertEqual(display["segments"][0]["speaker_display_name"], "Avery")
                self.assertEqual(display["highlights"][0]["quote"], "Hello")
                self.assertEqual(display["segments"][0]["id"], "seg-1")


if __name__ == "__main__":
    unittest.main()
