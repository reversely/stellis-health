"""Recoverable browser recording API checks using real encoded WebM audio."""

from __future__ import annotations

from stellis import tools
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis.server import app


class RecordingWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def _webm(self) -> bytes:
        path = Path(self.temp.name) / "sample.webm"
        subprocess.run([tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=2", "-c:a", "libopus", str(path)],
                       check=True, timeout=20)
        return path.read_bytes()

    def test_interrupted_chunks_are_recoverable_and_imported(self) -> None:
        session = self.client.post("/api/v1/recordings", json={"title":"Mic check", "media_type":"audio/webm"}).json()
        data = self._webm()
        midpoint = len(data) // 2
        for sequence, chunk in enumerate((data[:midpoint], data[midpoint:])):
            saved = self.client.put(f"/api/v1/recordings/{session['id']}/chunks/{sequence}", content=chunk)
            self.assertEqual(saved.status_code, 200)
        duplicate = self.client.put(f"/api/v1/recordings/{session['id']}/chunks/0", content=data[:midpoint])
        self.assertTrue(duplicate.json()["duplicate"])
        conflict = self.client.put(f"/api/v1/recordings/{session['id']}/chunks/0", content=b"different")
        self.assertEqual(conflict.status_code, 409)
        pause = self.client.post(f"/api/v1/recordings/{session['id']}/events",
                                 json={"kind":"pause", "media_position_ms":500})
        self.assertEqual(pause.status_code, 200)
        renamed = self.client.patch(f"/api/v1/recordings/{session['id']}", json={"title":"Interview mic check"})
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["title"], "Interview mic check")
        unfinished = self.client.get("/api/v1/recordings").json()
        self.assertEqual(unfinished[0]["chunk_count"], 2)
        self.assertEqual(unfinished[0]["title"], "Interview mic check")
        with patch("stellis.server.processing.capabilities", return_value={"features": {"transcribe": True, "diarize": True}}), \
             patch("stellis.server.processing.request_processing") as start_speech:
            recovered = self.client.post(f"/api/v1/recordings/{session['id']}/finalize")
        self.assertEqual(recovered.status_code, 200)
        conversation = recovered.json()["conversation"]
        start_speech.assert_called_once_with(conversation["id"])
        self.assertEqual(conversation["title"], "Interview mic check")
        self.assertGreater(conversation["duration_ms"], 1000)
        audio = self.client.get(f"/api/v1/conversations/{conversation['id']}/audio")
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.headers["content-type"], "audio/webm")
        self.assertGreater(len(audio.content), 1000)
        self.assertEqual(self.client.get("/api/v1/recordings").json(), [])
        self.assertEqual(self.client.post(f"/api/v1/recordings/{session['id']}/finalize").status_code, 200)
        self.assertEqual(self.client.patch(f"/api/v1/recordings/{session['id']}", json={"title":"Later"}).status_code, 409)

    def test_a_live_preview_that_fails_to_stop_still_queues_transcription(self) -> None:
        session = self.client.post("/api/v1/recordings", json={"title":"Visit", "media_type":"audio/webm"}).json()
        self.client.put(f"/api/v1/recordings/{session['id']}/chunks/0", content=self._webm())
        with patch("stellis.recording_preview.stop", side_effect=BrokenPipeError(32, "Broken pipe")), \
             patch("stellis.server.processing.capabilities", return_value={"features": {"transcribe": True, "diarize": True}}), \
             patch("stellis.server.processing.request_processing") as start_speech, \
             self.assertLogs("stellis.recording", "ERROR"):
            finished = self.client.post(f"/api/v1/recordings/{session['id']}/finalize")
        self.assertEqual(finished.status_code, 200)
        start_speech.assert_called_once_with(finished.json()["conversation"]["id"])

    def test_closing_a_preview_pipe_after_its_process_exits_is_quiet(self) -> None:
        from stellis import recording_preview

        class Pipe:
            def close(self):
                raise BrokenPipeError(32, "Broken pipe")

        process = SimpleNamespace(poll=lambda: 0, wait=lambda timeout=None: 0, stdin=Pipe(), stdout=None)
        worker = SimpleNamespace(stopping=threading.Event(), ffmpeg=process, asr=process,
                                 thread=threading.Thread(target=lambda: None), writer=threading.Thread(target=lambda: None))
        worker.thread.start(); worker.writer.start()
        recording_preview._Worker.stop(worker)

    def test_notebook_assignment_survives_rename_recovery_and_finalization(self):
        from stellis import notebooks, recording
        book = notebooks.create("Recording destination")
        session = self.client.post("/api/v1/recordings", json={"title":"Destination", "media_type":"audio/webm"}).json()
        endpoint = f"/api/v1/recordings/{session['id']}"
        destination = endpoint + "/notebook"
        self.assertEqual(self.client.put(destination, json={"notebook_id":"missing"}).status_code, 400)
        self.assertIsNone(recording.get_session(session['id'])['notebook_id'])
        assigned = self.client.put(destination, json={"notebook_id":book['id']})
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.json()['notebook_id'], book['id'])
        renamed = self.client.patch(endpoint, json={"title":"Renamed"})
        self.assertEqual(renamed.json()['notebook_id'], book['id'])
        self.assertEqual(self.client.put(destination, json={"notebook_id":None}).status_code, 200)
        self.assertIsNone(recording.get_session(session['id'])['notebook_id'])
        with recording._mutation(session['id']):
            self.assertEqual(self.client.put(destination, json={"notebook_id":book['id']}).status_code, 409)
        self.client.put(destination, json={"notebook_id":book['id']})
        self.assertEqual(self.client.get('/api/v1/recordings').json()[0]['notebook_id'], book['id'])
        self.client.put(endpoint+'/chunks/0', content=self._webm())
        result=self.client.post(endpoint+'/finalize')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['conversation']['notebook_id'], book['id'])
        self.assertEqual(self.client.put(destination, json={"notebook_id":None}).status_code, 409)

    def test_filing_undo_preserves_recording_and_duplicate_import_does_not_refile(self):
        import asyncio
        import io
        from fastapi import UploadFile
        from stellis import importer, notebook_filing, notebooks, storage
        book=notebooks.create('Meeting destination')
        session=self.client.post('/api/v1/recordings',json={'title':'Recorded review','media_type':'audio/webm','notebook_id':book['id']}).json()
        endpoint='/api/v1/recordings/'+session['id']
        self.assertEqual(self.client.put(endpoint+'/chunks/0',content=self._webm()).status_code,200)
        result=self.client.post(endpoint+'/finalize')
        self.assertEqual(result.status_code,200)
        meeting=result.json()['conversation'];identifier=meeting['id']
        history=notebook_filing.history(book['id'])
        self.assertEqual(len(history),1)
        self.assertEqual(history[0]['items'][0]['target'],'/conversation/'+identifier)
        media=self.client.get('/api/v1/conversations/'+identifier+'/audio').content
        storage.rename_conversation(identifier,'Edited title')
        undo='/api/v1/notebooks/'+book['id']+'/filings/'+history[0]['id']+'/undo'
        self.assertEqual(self.client.post(undo).json()['unfiled'],[identifier])
        self.assertIsNone(storage.get_conversation(identifier)['notebook_id'])
        self.assertEqual(storage.get_conversation(identifier)['title'],'Edited title')
        self.assertEqual(self.client.get('/api/v1/conversations/'+identifier+'/audio').content,media)
        self.assertEqual(self.client.post(undo).json()['unfiled'],[identifier])
        self.assertIsNone(self.client.post(endpoint+'/finalize').json()['conversation']['notebook_id'])
        other=notebooks.create('Other')
        duplicate,is_duplicate=asyncio.run(importer.import_upload(UploadFile(io.BytesIO(media),filename='same.webm'),browser_recording=True,notebook_id=other['id']))
        self.assertTrue(is_duplicate)
        self.assertEqual(duplicate['id'],identifier)
        self.assertEqual(notebook_filing.history(other['id']),[])
        self.assertEqual(len(notebook_filing.history(book['id'])),1)

    def test_empty_or_gapped_recording_remains_recoverable(self) -> None:
        session = self.client.post("/api/v1/recordings", json={"title":"Gap", "media_type":"audio/webm"}).json()
        endpoint = f"/api/v1/recordings/{session['id']}"
        self.assertEqual(self.client.post(f"{endpoint}/finalize").status_code, 400)
        self.assertEqual(self.client.put(f"{endpoint}/chunks/1", content=b"partial").status_code, 200)
        self.assertEqual(self.client.post(f"{endpoint}/finalize").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/recordings").json()[0]["chunk_count"], 1)
        self.assertEqual(self.client.delete(endpoint).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/recordings").json(), [])


if __name__ == "__main__":
    unittest.main()
