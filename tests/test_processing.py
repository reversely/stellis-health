"""Durable speech-job behavior without downloading native model binaries."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import processing, storage
from stellis.server import app
from test_import import wav_bytes, mp3_bytes


class ProcessingWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        response = self.client.post("/api/v1/conversations/import", files={"file": ("talk.mp3", mp3_bytes(), "audio/mpeg")})
        self.conversation_id = response.json()["conversation"]["id"]

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_completed_transcription_starts_summary_without_opening_conversation(self) -> None:
        job = processing.request_processing(self.conversation_id)
        storage.update_job(str(job["id"]), state="succeeded", stage="complete", updated_at=processing.now())

        class OneJobQueue:
            def __init__(self) -> None:
                self.read = False

            def get(self):
                if self.read:
                    raise KeyboardInterrupt
                self.read = True
                return str(job["id"]), None

            def task_done(self) -> None:
                pass

        with patch.object(processing, "_STOPPING", threading.Event()), \
             patch.object(processing, "_QUEUE", OneJobQueue()), \
             patch.object(processing.subprocess, "Popen",
                          return_value=SimpleNamespace(pid=0, wait=lambda timeout=None: 0, poll=lambda: 0)), \
             patch("stellis.categorization.request"), \
             patch("stellis.enhanced_notes.request") as summary:
            with self.assertRaises(KeyboardInterrupt):
                processing._worker_loop()
        summary.assert_called_once_with(self.conversation_id)

    def test_job_queued_in_a_demo_runs_against_the_demo(self) -> None:
        # #258: the worker thread and its child started outside the demo, so a demo
        # recording's job was never found and stayed queued.
        demo = Path(self.temp.name, "demo")
        with storage.demo_workspace(demo):
            conversation_id = str(uuid.uuid4())
            storage.add_conversation({"id": conversation_id, "title": "Visit", "source_sha256": conversation_id,
                "audio_filename": "visit.mp3", "audio_media_type": "audio/mpeg", "duration_ms": 1,
                "created_at": processing.now()})
            job = processing.request_processing(conversation_id)
            storage.update_job(str(job["id"]), state="succeeded", stage="complete", updated_at=processing.now())
        queued = []
        with patch.object(processing._QUEUE, "put", side_effect=queued.append):
            with storage.demo_workspace(demo):
                processing._enqueue(str(job["id"]))
        self.assertEqual(queued, [(str(job["id"]), str(demo))])
        self.assertIsNone(storage.get_job(str(job["id"])))

        class OneJobQueue:
            items = [queued[0]]
            def get(self):
                if not self.items:
                    raise KeyboardInterrupt
                return self.items.pop()
            def task_done(self) -> None:
                pass

        launched, summarized = [], []
        def popen(args, **kwargs):
            launched.append(args)
            return SimpleNamespace(pid=0, wait=lambda timeout=None: 0, poll=lambda: 0)
        with patch.object(processing, "_STOPPING", threading.Event()), \
             patch.object(processing, "_QUEUE", OneJobQueue()), \
             patch.object(processing.subprocess, "Popen", side_effect=popen), \
             patch("stellis.categorization.request"), \
             patch("stellis.enhanced_notes.request", side_effect=lambda cid: summarized.append((cid, storage.in_demo()))):
            with self.assertRaises(KeyboardInterrupt):
                processing._worker_loop()
        self.assertEqual(launched[0][-2:], [str(job["id"]), str(demo)])
        self.assertEqual(summarized, [(conversation_id, True)])

    def test_background_thread_keeps_the_demo_workspace(self) -> None:
        seen = threading.Event()
        found = []
        with storage.demo_workspace(Path(self.temp.name, "demo")):
            storage.background(lambda: (found.append(storage.in_demo()), seen.set()))
        self.assertTrue(seen.wait(5))
        self.assertEqual(found, [True])

    def test_persisted_job_and_transcript(self) -> None:
        model = Path(self.temp.name, "model.bin")
        model.write_bytes(b"model")
        job = processing.request_processing(self.conversation_id)
        again = processing.request_processing(self.conversation_id)
        self.assertEqual(job["id"], again["id"])
        segments = [{"start_ms": 100, "end_ms": 900, "text": "A test sentence."}]
        turns = [{"start_ms": 100, "end_ms": 900, "speaker_cluster_id": "speaker-1"}]
        with patch.object(processing, "model_paths", return_value=(model, model, model)), \
             patch.object(processing, "_command"), \
             patch.object(processing, "_transcribe", return_value=segments), \
             patch.object(processing, "_diarize", return_value=turns), \
             patch.object(processing, "_group_speakers", return_value=(False, None, None)):
            processing.run_job(str(job["id"]))
        result = self.client.get(f"/api/v1/jobs/{job['id']}").json()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["metrics"]["cluster_count"], 1)
        transcript = self.client.get(f"/api/v1/conversations/{self.conversation_id}/transcript").json()
        self.assertEqual(transcript["segments"][0]["provenance"], "on_device_asr")
        self.assertEqual(transcript["segments"][0]["speaker_cluster_id"], "speaker-1")
        self.assertTrue(transcript["segments"][0]["speaker_uncertain"])
        self.assertEqual(storage.get_transcript(self.conversation_id)["revision"], 1)
        processing.run_job(str(job["id"]))
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job['id']}").json()["state"], "succeeded")

    def test_alignment_marks_ambiguous_turn(self) -> None:
        segments = [{"start_ms": 0, "end_ms": 3000, "text": "We talked over each other."}]
        turns = [
            {"start_ms": 0, "end_ms": 1500, "speaker_cluster_id": "speaker-1"},
            {"start_ms": 1500, "end_ms": 3000, "speaker_cluster_id": "speaker-2"},
        ]
        result, fraction = processing.align_segments(self.conversation_id, segments, turns)
        self.assertTrue(result[0]["speaker_uncertain"])
        self.assertEqual(fraction, 0)

    def test_clear_word_boundary_splits_speaker_change_without_shifting_other_ids(self) -> None:
        text = "Hello there. Great, thanks."
        words = [
            {"text": "Hello", "start_offset": 0, "end_offset": 5, "start_ms": 0, "end_ms": 300},
            {"text": "there.", "start_offset": 6, "end_offset": 12, "start_ms": 300, "end_ms": 700},
            {"text": "Great,", "start_offset": 13, "end_offset": 19, "start_ms": 900, "end_ms": 1200},
            {"text": "thanks.", "start_offset": 20, "end_offset": 27, "start_ms": 1200, "end_ms": 1550},
        ]
        segments = [
            {"start_ms": 0, "end_ms": 1600, "text": text, "words": words},
            {"start_ms": 1800, "end_ms": 2500, "text": "Next passage."},
        ]
        turns = [
            {"start_ms": 0, "end_ms": 800, "speaker_cluster_id": "speaker-1"},
            {"start_ms": 900, "end_ms": 2500, "speaker_cluster_id": "speaker-2"},
        ]
        result, _ = processing.align_segments(self.conversation_id, segments, turns)
        self.assertEqual([item["text"] for item in result],
                         ["Hello there.", "Great, thanks.", "Next passage."])
        self.assertEqual([item["speaker_cluster_id"] for item in result],
                         ["speaker-1", "speaker-2", "speaker-2"])
        self.assertEqual((result[1]["start_ms"], result[1]["end_ms"]), (900, 1600))
        self.assertEqual([(word["start_offset"], word["end_offset"]) for word in result[1]["words"]],
                         [(0, 6), (7, 14)])
        self.assertEqual(result[2]["id"],
                         str(uuid.uuid5(uuid.UUID(self.conversation_id), "segment:1:1800:2500")))

    def test_missing_word_timing_or_short_speaker_flip_does_not_split(self) -> None:
        words = [
            {"text": "One", "start_offset": 0, "end_offset": 3, "start_ms": 0, "end_ms": 300},
            {"text": "voice", "start_offset": 4, "end_offset": 9, "start_ms": 300, "end_ms": 700},
            {"text": "brief", "start_offset": 10, "end_offset": 15, "start_ms": 700, "end_ms": 1000},
            {"text": "return", "start_offset": 16, "end_offset": 22, "start_ms": 1000, "end_ms": 1500},
        ]
        segment = {"start_ms": 0, "end_ms": 1500, "text": "One voice brief return", "words": words}
        turns = [
            {"start_ms": 0, "end_ms": 700, "speaker_cluster_id": "speaker-1"},
            {"start_ms": 700, "end_ms": 1000, "speaker_cluster_id": "speaker-2"},
            {"start_ms": 1000, "end_ms": 1500, "speaker_cluster_id": "speaker-1"},
        ]
        self.assertEqual(len(processing.split_segments_at_speaker_changes([segment], turns)), 1)
        self.assertEqual(len(processing.split_segments_at_speaker_changes(
            [{**segment, "words": []}], turns)), 1)
        self.assertEqual(len(processing.split_segments_at_speaker_changes(
            [{**segment, "words": [{**words[0], "start_offset": 1}, *words[1:]]}], turns)), 1)

    def test_short_vocabulary_prompt_cannot_drop_spoken_opening(self) -> None:
        storage.add_vocabulary("Riley")
        job = processing.request_processing(self.conversation_id)
        model = Path(self.temp.name, "model.bin")
        model.write_bytes(b"model")
        prompted = [{"start_ms": 0, "end_ms": 250, "text": "-"},
                    {"start_ms": 500, "end_ms": 950, "text": "Later words"}]
        plain = [{"start_ms": 0, "end_ms": 450, "text": "Opening words are here"},
                 {"start_ms": 500, "end_ms": 950, "text": "Later words"}]
        turns = [{"start_ms": 0, "end_ms": 450, "speaker_cluster_id": "speaker-1"},
                 {"start_ms": 500, "end_ms": 950, "speaker_cluster_id": "speaker-1"}]

        def decode(command: list[str], timeout: int) -> None:
            Path(command[-1]).write_bytes(wav_bytes())

        def transcribe(audio: Path, whisper: Path, output: Path,
                       vocabulary: list[str]) -> list[dict[str, object]]:
            return prompted if vocabulary else plain

        with patch.object(processing, "model_paths", return_value=(model, model, model)), \
             patch.object(processing, "_command", side_effect=decode), \
             patch.object(processing, "_transcribe", side_effect=transcribe), \
             patch.object(processing, "_diarize", return_value=turns), \
             patch.object(processing, "_group_speakers", return_value=(False, None, None)):
            processing.run_job(str(job["id"]))
        saved = self.client.get(f"/api/v1/conversations/{self.conversation_id}/transcript").json()
        status = self.client.get(f"/api/v1/jobs/{job['id']}").json()
        self.assertEqual(status["state"], "succeeded")
        self.assertTrue(status["metrics"]["vocabulary_fallback"])
        self.assertEqual(saved["segments"][0]["text"], "Opening words are here")

    def test_public_real_speech_fixture_with_installed_whisper(self) -> None:
        model = processing.model_paths()[0]
        if not model.is_file():
            self.skipTest("Local whisper.cpp model is not installed")
        audio = Path(__file__).parent / "fixtures" / "jfk.wav"
        segments = processing._transcribe(audio, model, Path(self.temp.name, "jfk"), [])
        self.assertIn("fellow Americans", " ".join(str(item["text"]) for item in segments))
        self.assertEqual(segments[0]["start_ms"], 0)
        self.assertLessEqual(max(int(item["end_ms"]) for item in segments), 11000)

    def test_vocabulary_reaches_whisper_initial_prompt(self) -> None:
        from stellis import asr
        self.assertEqual(asr._params(["ORBIT", "CubeSat"], None, 4)["initial_prompt"], "Vocabulary: ORBIT, CubeSat")
        self.assertNotIn("initial_prompt", asr._params([], None, 4))
        self.assertNotIn("n_max_text_ctx", asr._params(["ORBIT"], None, 4))
        self.assertEqual(asr._params([], 0, 4)["n_max_text_ctx"], 0)
        # Beam search with five beams matches whisper-cli's defaults.
        self.assertEqual(asr._params([], None, 4)["beam_search"]["beam_size"], 5)

    def test_native_asr_times_cannot_outlive_source_audio(self) -> None:
        audio_path = Path(self.temp.name, "audio.wav")
        audio_path.write_bytes(wav_bytes())
        output = Path(self.temp.name, "bounded")

        passages = [
            {"offsets": {"from": 0, "to": 30000}, "text": " Test", "tokens": [
                {"text": " Test", "offsets": {"from": 100, "to": 30000}, "p": .8},
            ]},
            {"offsets": {"from": 1200, "to": 30000}, "text": "Beyond audio"},
        ]
        with patch.object(processing, "_run_asr", return_value=passages):
            result = processing._transcribe(audio_path, Path("/model.bin"), output, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["end_ms"], 1000)
        self.assertEqual(result[0]["words"][0]["end_ms"], 1000)

    def test_native_token_times_form_words_without_invented_offsets(self) -> None:
        item = {"offsets": {"from": 1000, "to": 2000}, "text": " Space flight, now.", "tokens": [
            {"text": " Space", "offsets": {"from": 1050, "to": 1300}, "p": .9},
            {"text": " flight", "offsets": {"from": 1310, "to": 1620}, "p": .8},
            {"text": ",", "offsets": {"from": 1620, "to": 1660}, "p": .7},
            {"text": " now", "offsets": {"from": 1700, "to": 1900}, "p": .95},
            {"text": ".", "offsets": {"from": 1900, "to": 1950}, "p": .9},
        ]}
        words = processing._measured_words(item)
        self.assertEqual([(word["text"], word["start_ms"], word["end_ms"]) for word in words],
                         [("Space", 1050, 1300), ("flight,", 1310, 1660), ("now.", 1700, 1950)])
        self.assertEqual([(word["start_offset"], word["end_offset"]) for word in words],
                         [(0, 5), (6, 13), (14, 18)])
        self.assertEqual(words[1]["confidence"], .7)
        item["text"] = "Different words."
        self.assertEqual(processing._measured_words(item), [])

    def test_word_alignment_reuses_timing_without_rewriting_edited_source(self) -> None:
        stamp = processing.now()
        source = [
            {"id":"old-1", "start_ms":0, "end_ms":1000, "text":"Hello there",
             "speaker_cluster_id":"speaker-1", "speaker_uncertain":False, "words":[]},
            {"id":"old-2", "start_ms":1000, "end_ms":2000, "text":"Again now",
             "speaker_cluster_id":"speaker-2", "speaker_uncertain":False, "words":[]},
        ]
        storage.save_transcript({"conversation_id":self.conversation_id, "revision":1,
                                 "pipeline_version":"source-v1", "language":"en",
                                 "segments_json":json.dumps(source), "turns_json":"[]", "updated_at":stamp})
        revision = storage.edit_transcript_segment(self.conversation_id, "old-1", "Hello Avery", 1)
        storage.set_speaker_label(self.conversation_id, "segment", "old-1", "Avery", revision)
        measured = [{**source[0], "id":"new-1", "words":[
            {"text":"Hello", "start_ms":100, "end_ms":350, "confidence":.9},
            {"text":"there", "start_ms":400, "end_ms":850, "confidence":.8}]},
                    {**source[1], "id":"new-2", "words":[
            {"text":"Again", "start_ms":1100, "end_ms":1400, "confidence":.85},
            {"text":"now", "start_ms":1500, "end_ms":1850, "confidence":.9}]}]
        storage.save_transcript({"conversation_id":self.conversation_id, "revision":1,
                                 "pipeline_version":"timed-v1", "language":"en",
                                 "segments_json":json.dumps(measured), "turns_json":"[]", "updated_at":stamp})
        with patch.object(processing._QUEUE, "put"):
            response = self.client.post(f"/api/v1/conversations/{self.conversation_id}/word-alignment",
                                        json={"source_generation_version":"source-v1"})
            self.assertEqual(response.status_code, 200)
            job = response.json()
            self.assertEqual(processing.request_word_alignment(self.conversation_id, "source-v1")["id"], job["id"])
        processing.run_job(str(job["id"]))
        result = self.client.get(f"/api/v1/conversations/{self.conversation_id}/transcript").json()
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job['id']}").json()["state"], "succeeded")
        self.assertEqual([item["id"] for item in result["segments"]], ["old-1", "old-2"])
        self.assertEqual(result["segments"][0]["text"], "Hello Avery")
        self.assertEqual(result["segments"][0]["speaker_display_name"], "Avery")
        self.assertEqual(result["segments"][0]["words"], [])
        self.assertEqual([word["text"] for word in result["segments"][1]["words"]], ["Again", "now"])

    def test_failed_job_can_be_requeued_without_losing_audio(self) -> None:
        job = processing.request_processing(self.conversation_id)
        environment = {**os.environ,
                       "STELLIS_WHISPER_MODEL": "/missing-asr",
                       "STELLIS_SEGMENTATION_MODEL": "/missing-seg",
                       "STELLIS_EMBEDDING_MODEL": "/missing-embed"}
        completed = subprocess.run([sys.executable, "-m", "stellis.worker", str(job["id"])],
                                   env=environment, capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0)
        failed = storage.get_job(str(job["id"]))
        self.assertEqual(failed["state"], "failed")
        retried = processing.request_processing(self.conversation_id)
        self.assertEqual(retried["id"], job["id"])
        self.assertEqual(retried["state"], "queued")
        media = storage.data_dir() / "media" / f"{self.conversation_id}.mp3"
        self.assertTrue(media.is_file())


if __name__ == "__main__":
    unittest.main()
