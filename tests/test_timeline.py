"""Tests that the health timeline sorts records by their clinical date."""

import os
import tempfile
import unittest
import uuid
from unittest import mock

from fastapi import testclient

from stellis import documents
from stellis import health_facts
from stellis import notebooks
import sample_records
from stellis import server
from stellis import storage

FOLDER = sample_records.FOLDER


class TimelineTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ,
                                      {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.book = notebooks.create("Heart")["id"]
        # Newest first, so upload order is the reverse of clinical order.
        self.recent, _ = documents.create(
            "recent.pdf", (FOLDER / "2026-04-02-lab-results.pdf").read_bytes(),
            self.book, "2026-04-02")
        self.old, _ = documents.create(
            "old.pdf",
            (FOLDER / "1997-12-04-hospital-admission.pdf").read_bytes(), None,
            "1997-12-04")
        conversation_id = str(uuid.uuid4())
        storage.add_conversation({
            "id": conversation_id,
            "title": "Voice note",
            "source_sha256": conversation_id,
            "audio_filename": "a.mp3",
            "audio_media_type": "audio/mpeg",
            "duration_ms": 5000,
            "created_at": "2026-10-21T12:00:00+00:00",
            "recorded_at": "2019-05-01T09:00:00+00:00",
            "notebook_id": self.book,
        })
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def add_fact(self, status: str, date: str) -> None:
        health_facts.list_for(self.recent["id"])  # Creates the table.
        with storage.connect() as conn:
            conn.execute(
                """
                INSERT INTO health_facts (id, document_id, page, kind, name,
                    value, unit, fact_date, quote, status, created_at)
                VALUES (?, ?, 1, 'lab_result', 'Creatinine', '3.4', 'mg/dL', ?,
                    'Creatinine 3.4', ?, '2026-10-21')""",
                (str(uuid.uuid4()), self.recent["id"], date, status))

    def timeline(self, query: str = "") -> list[dict[str, object]]:
        response = self.client.get("/api/v1/timeline" + query)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["entries"]

    def test_entries_sort_by_clinical_date_not_upload_date(self):
        self.add_fact("accepted", "2020-02-21")
        self.add_fact("suggested", "2021-01-01")

        entries = self.timeline()

        self.assertEqual([(entry["kind"], entry["date"]) for entry in entries],
                         [("document", "2026-04-02"), ("fact", "2020-02-21"),
                          ("recording", "2019-05-01"),
                          ("document", "1997-12-04")])
        fact = entries[1]
        self.assertEqual((fact["title"], fact["detail"], fact["source"]),
                         ("Creatinine", "3.4 mg/dL", self.recent["title"]))
        self.assertEqual(fact["target"],
                         f"/document/{self.recent['id']}?page=1")

    def test_same_day_documents_come_before_their_facts(self):
        self.add_fact("accepted", "2026-04-02")

        entries = self.timeline()

        self.assertEqual([entry["kind"] for entry in entries[:2]],
                         ["document", "fact"])

    def test_filters_by_notebook_and_kind(self):
        in_book = self.timeline(f"?notebook_id={self.book}")
        documents_only = self.timeline("?kinds=document")
        unknown = self.client.get("/api/v1/timeline?kinds=surgery")

        self.assertEqual({entry["date"] for entry in in_book},
                         {"2026-04-02", "2019-05-01"})
        self.assertEqual({entry["kind"] for entry in documents_only},
                         {"document"})
        self.assertEqual(unknown.status_code, 400)


if __name__ == "__main__":
    unittest.main()
