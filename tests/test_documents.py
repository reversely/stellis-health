"""Tests for uploaded documents, their page text, search and the samples."""

import io
import os
import tempfile
import unittest
from unittest import mock

from fastapi import testclient
from PIL import Image

from stellis import documents
from stellis import notebooks
import sample_records
from stellis import search
from stellis import server
from stellis import source_search

LAB_PDF = "2026-04-02-lab-results.pdf"
VISIT_PDF = "2026-03-26-annual-exam.pdf"
LAB_PHOTO = "2025-03-23-lab-results-photo.png"


def _sample(name: str) -> bytes:
    return (sample_records.FOLDER / name).read_bytes()


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buffer, "PNG")
    return buffer.getvalue()


class CleanPageTextTest(unittest.TestCase):

    def test_rejoins_wrapped_lines_and_keeps_short_lines_as_breaks(self):
        text = ("Glucose was within range and the creatinine was\n"
                "stable.\n"
                "Plan:\n"
                "Recheck   in   a year.")
        self.assertEqual(
            documents.clean_page_text(text),
            "Glucose was within range and the creatinine was stable.\n"
            "Plan:\nRecheck in a year.")

    def test_headings_list_items_and_table_rows_keep_their_lines(self):
        text = ("History:\n"
                "No chest pain, palpitations, fainting, falls or vomiting.\n"
                "Assessment:\n"
                "Plan:\n"
                "1. Stop hydrochlorothiazide 25 mg. Continue lisinopril.\n"
                "2. Fluids as advised.\n"
                "Weight (kg)           93.8          91.0\n"
                "Waist (cm)            110           107")

        self.assertEqual(
            documents.clean_page_text(text),
            "History:\nNo chest pain, palpitations, fainting, falls or "
            "vomiting.\nAssessment:\nPlan:\n1. Stop hydrochlorothiazide "
            "25 mg. Continue lisinopril.\n2. Fluids as advised.\n"
            "Weight (kg) 93.8 91.0\nWaist (cm) 110 107")

    def test_empty_page(self):
        self.assertEqual(documents.clean_page_text(" \n\n"), "")


class CheckUploadTest(unittest.TestCase):

    def test_accepts_matching_type_and_bytes(self):
        self.assertEqual(documents.check_upload("a.PDF", b"%PDF-1.7"),
                         "application/pdf")
        self.assertEqual(documents.check_upload("a.jpeg", b"\xff\xd8\xff"),
                         "image/jpeg")

    def test_rejects_other_files(self):
        cases = [
            ("notes.txt", b"hello", "Choose a PDF, PNG or JPEG file"),
            ("empty.pdf", b"", "The file is empty"),
            ("fake.pdf", b"<html>", "This file is not a PDF file"),
        ]
        for name, content, message in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(documents.DocumentError, message):
                    documents.check_upload(name, content)

    def test_rejects_large_files(self):
        with mock.patch.object(documents, "MAX_BYTES", 10):
            with self.assertRaisesRegex(documents.DocumentError, "20 MB"):
                documents.check_upload("big.pdf", b"%PDF-" + b"0" * 20)


class DocumentsApiTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ,
                                      {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.book = notebooks.create("Heart")["id"]
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def upload(self, name: str, content: bytes, notebook_id: str = ""):
        data = {"notebook_id": notebook_id} if notebook_id else {}
        return self.client.post("/api/v1/documents",
                                files={"file": (name, content)},
                                data=data)

    def test_pdf_text_is_stored_per_page_and_searchable(self):
        response = self.upload(LAB_PDF, _sample(LAB_PDF), self.book)

        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()["document"]
        self.assertEqual(
            (record["status"], record["page_count"], record["notebook_id"]),
            ("ready", 1, self.book))
        self.assertIn("Creatinine [Mass/volume] in Blood: 3.4 mg/dL",
                      record["pages"][0]["text"])
        hits = [
            item for item in search.search_library("creatinine")["results"]
            if item["kind"] == "document"
        ]
        self.assertEqual((hits[0]["document_id"], hits[0]["page"]),
                         (record["id"], 1))
        sources = source_search.search_sources("creatinine", self.book)
        self.assertEqual(sources[0]["target"],
                         f"/document/{record['id']}?page=1")

    def test_file_is_served_back_and_repeat_uploads_are_duplicates(self):
        content = _sample(LAB_PDF)
        record = self.upload(LAB_PDF, content).json()["document"]

        served = self.client.get(f"/api/v1/documents/{record['id']}/file")

        self.assertEqual((served.status_code, served.headers["content-type"]),
                         (200, "application/pdf"))
        self.assertEqual(served.content, content)
        self.assertTrue(self.upload("again.pdf", content).json()["duplicate"])

    def test_images_need_text(self):
        response = self.upload("Lab photo.png", _png())

        record = response.json()["document"]
        self.assertEqual((record["status"], record["page_count"],
                          record["title"], record["media_type"]),
                         ("needs_text", 1, "Lab photo", "image/png"))

    def test_unreadable_files_are_refused(self):
        cases = [
            ("broken.pdf", b"%PDF-1.4 nonsense", "This PDF could not be read"),
            ("fake.png", b"\x89PNG\r\n\x1a\nbroken",
             "This image could not be read"),
        ]
        for name, content, message in cases:
            with self.subTest(name=name):
                response = self.upload(name, content)
                self.assertEqual(
                    (response.status_code, response.json()["detail"]),
                    (400, message))

    def test_unknown_notebook_is_refused(self):
        response = self.upload(LAB_PDF, _sample(LAB_PDF), "missing")

        self.assertEqual(response.status_code, 404)

    def test_rename_date_move_and_delete(self):
        record = self.upload(VISIT_PDF, _sample(VISIT_PDF)).json()["document"]

        moved = self.client.patch(f"/api/v1/documents/{record['id']}",
                                  json={
                                      "title": "Annual exam",
                                      "record_date": "2026-03-26",
                                      "notebook_id": self.book,
                                      "move": True,
                                  }).json()
        path, _ = documents.file_path(record["id"])
        deleted = self.client.delete(f"/api/v1/documents/{record['id']}")

        self.assertEqual(
            (moved["title"], moved["record_date"], moved["notebook_id"]),
            ("Annual exam", "2026-03-26", self.book))
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(path.exists())
        self.assertEqual(
            self.client.get(f"/api/v1/documents/{record['id']}").status_code,
            404)
        self.assertEqual(
            self.client.get(f"/document/{record['id']}").status_code, 200)

    def test_bad_record_date_is_refused(self):
        record = self.upload(VISIT_PDF, _sample(VISIT_PDF)).json()["document"]

        response = self.client.patch(f"/api/v1/documents/{record['id']}",
                                     json={"record_date": "March 2026"})

        self.assertEqual(response.status_code, 400)


class SampleRecordsTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ,
                                      {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)

    def test_one_patient_loads_once_and_unsorted(self):
        first = sample_records.load()
        second = sample_records.load()

        loaded = documents.list_all()
        self.assertEqual((first["added"], first["total"], second["added"]),
                         (50, 50, 0))
        self.assertIsNone(first["notebook_id"])
        self.assertEqual({item["notebook_id"] for item in loaded}, {None})
        dates = [item["record_date"] for item in loaded]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual((dates[-1], dates[0]), ("1997-12-04", "2026-04-02"))
        needs_text = sorted((item["title"], item["record_date"])
                            for item in loaded
                            if item["status"] == "needs_text")
        self.assertEqual(needs_text,
                         [("Food diary", "2024-04-12"),
                          ("Lab results", "2025-03-23"),
                          ("Questions for my annual exam", "2026-03-22")])

    def test_loads_into_one_notebook_on_request(self):
        result = sample_records.load("Denver")

        self.assertEqual(len(documents.list_all(str(result["notebook_id"]))),
                         50)

    def test_manifests_list_every_sample_file_once(self):
        files = [record["file"] for record in sample_records.manifest()]
        on_disk = {
            path.name
            for path in sample_records.FOLDER.iterdir()
            if path.suffix in (".pdf", ".png")
        }
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual(set(files), on_disk)
        self.assertIn(LAB_PHOTO, files)


if __name__ == "__main__":
    unittest.main()
