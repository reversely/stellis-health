"""Tests for a notebook's sources, their status and the saved selection."""

import datetime
import os
import tempfile
import unittest
import uuid
from unittest import mock

from fastapi import testclient

from stellis import documents
from stellis import notebook_sources
from stellis import notebooks
from stellis import pages
import sample_records
from stellis import server
from stellis import storage

LAB_PDF = sample_records.FOLDER / "2026-04-02-lab-results.pdf"


def _stamp(minute: int) -> str:
    moment = datetime.datetime(2026,
                               10,
                               21,
                               12,
                               minute,
                               tzinfo=datetime.timezone.utc)
    return moment.isoformat()


def add_conversation(notebook_id: str, title: str, minute: int, *,
                     transcript: bool) -> str:
    """Stores a conversation, with an empty transcript when asked."""
    conversation_id = str(uuid.uuid4())
    storage.add_conversation({
        "id": conversation_id,
        "title": title,
        "source_sha256": conversation_id,
        "audio_filename": "a.mp3",
        "audio_media_type": "audio/mpeg",
        "duration_ms": 5000,
        "created_at": _stamp(minute),
        "notebook_id": notebook_id,
    })
    if transcript:
        storage.save_transcript({
            "conversation_id": conversation_id,
            "revision": 1,
            "language": "en",
            "segments_json": "[]",
            "turns_json": "[]",
            "updated_at": _stamp(minute),
        })
    return conversation_id


def add_job(conversation_id: str, state: str) -> None:
    """Stores a speech job in the given state."""
    storage.add_job({
        "id": str(uuid.uuid4()),
        "conversation_id": conversation_id,
        "pipeline_version": str(uuid.uuid4()),
        "state": state,
        "stage": state,
        "progress": 0.0,
        "error": None,
        "metrics_json": None,
        "vocabulary_json": "[]",
        "created_at": _stamp(30),
        "updated_at": _stamp(30),
    })


class NotebookSourcesTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ,
                                      {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.book = notebooks.create("Heart")["id"]
        self.recording = add_conversation(self.book,
                                          "Voice note",
                                          1,
                                          transcript=True)
        self.audio = add_conversation(self.book,
                                      "Clinic visit.mp3",
                                      2,
                                      transcript=True)
        with storage.connect() as conn:
            conn.execute(
                """
                INSERT INTO recording_sessions (id, title, media_type, state,
                    conversation_id, notebook_id, created_at, updated_at)
                VALUES (?, 'Voice note', 'audio/webm', 'finalized',
                    ?, ?, ?, ?)""",
                (str(uuid.uuid4()), self.recording, self.book, _stamp(1),
                 _stamp(1)))
        self.note = pages.create("Questions for Dr. Lee", self.book,
                                 "Ask about the dose")["id"]
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def sources(self) -> dict[str, dict[str, object]]:
        response = self.client.get(f"/api/v1/notebooks/{self.book}/sources")
        return {item["id"]: item for item in response.json()["sources"]}

    def test_lists_each_kind_with_its_type(self):
        other = notebooks.create("Skin")["id"]
        add_conversation(other, "Elsewhere", 5, transcript=True)
        documents.create("lab.pdf", LAB_PDF.read_bytes(), self.book,
                         "2026-04-02")

        sources = self.sources()

        types = {key: item["type"] for key, item in sources.items()}
        self.assertEqual(types.pop("conversation:" + self.recording),
                         "recording")
        self.assertEqual(types.pop("conversation:" + self.audio), "audio")
        self.assertEqual(types.pop("page:" + self.note), "note")
        self.assertEqual(list(types.values()), ["pdf"])
        self.assertTrue(all(item["selected"] for item in sources.values()))

    def test_document_sorts_by_its_record_date(self):
        documents.create("lab.pdf", LAB_PDF.read_bytes(), self.book,
                         "1999-01-01")

        listed = notebook_sources.list_sources(self.book)

        self.assertEqual((listed[-1]["type"], listed[-1]["created_at"]),
                         ("pdf", "1999-01-01"))

    def test_source_without_text_cannot_be_selected(self):
        queued = add_conversation(self.book, "Queued", 10, transcript=False)
        failed = add_conversation(self.book, "Broken", 11, transcript=False)
        add_job(queued, "queued")
        add_job(failed, "failed")

        sources = self.sources()

        self.assertEqual((sources["conversation:" + queued]["status"],
                          sources["conversation:" + failed]["status"]),
                         ("processing", "failed"))
        self.assertFalse(sources["conversation:" + queued]["selected"])
        self.assertFalse(sources["conversation:" + failed]["selected"])

    def test_selection_persists_and_new_sources_start_checked(self):
        response = self.client.put(
            f"/api/v1/notebooks/{self.book}/sources/selection",
            json={"excluded": ["page:" + self.note, "page:unknown"]})
        later = add_conversation(self.book, "Follow-up", 20, transcript=True)

        sources = self.sources()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(sources["page:" + self.note]["selected"])
        self.assertTrue(sources["conversation:" + later]["selected"])
        with storage.connect() as conn:
            stored = [
                row[0] for row in conn.execute(
                    "SELECT source_id FROM notebook_source_exclusions")
            ]
        self.assertEqual(stored, ["page:" + self.note])

    def test_trashed_notes_leave_and_unknown_notebooks_are_404(self):
        with storage.connect() as conn:
            conn.execute("UPDATE pages SET trashed_at = ? WHERE id = ?",
                         (_stamp(40), self.note))

        listed = self.sources()
        missing = self.client.get("/api/v1/notebooks/missing/sources")
        missing_selection = self.client.put(
            "/api/v1/notebooks/missing/sources/selection",
            json={"excluded": []})

        self.assertNotIn("page:" + self.note, listed)
        self.assertEqual((missing.status_code, missing_selection.status_code),
                         (404, 404))

    def test_full_notebook_refuses_new_sources_from_every_path(self):
        loose = pages.create("Loose note", None, "text")["id"]
        other = notebooks.create("Other")["id"]
        elsewhere = add_conversation(other, "Elsewhere", 6, transcript=True)

        with mock.patch.object(notebook_sources, "LIMIT", 3):
            attempts = [
                self.client.post("/api/v1/pages",
                                 json={
                                     "title": "New",
                                     "notebook_id": self.book
                                 }),
                self.client.put(f"/api/v1/pages/{loose}/notebook",
                                json={"notebook_id": self.book}),
                self.client.put(f"/api/v1/conversations/{elsewhere}/notebook",
                                json={"notebook_id": self.book}),
                self.client.post(
                    "/api/v1/documents",
                    files={"file": ("lab.pdf", LAB_PDF.read_bytes())},
                    data={"notebook_id": self.book}),
            ]
            refiled = self.client.put(f"/api/v1/pages/{self.note}/notebook",
                                      json={"notebook_id": self.book})
            unfiled = self.client.post("/api/v1/pages", json={"title": "Loose"})

        for response in attempts:
            self.assertEqual((response.status_code, response.json()["detail"]),
                             (409, "This notebook is full at 3 sources"))
        self.assertEqual((refiled.status_code, unfiled.status_code), (200, 201))
        self.assertEqual(len(notebook_sources.list_sources(self.book)), 3)
        self.assertEqual(self.client.get("/api/v1/documents").json(), [])


if __name__ == "__main__":
    unittest.main()
