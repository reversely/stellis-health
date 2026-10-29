"""Cross-source search keeps notebook scope and playable evidence targets."""

import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from stellis import notebooks, pages, source_search, storage


class SourceSearchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.book = notebooks.create("Water Treatment")["id"]
        self.other = notebooks.create("Bridge")["id"]
        self.conversation_id = str(uuid.uuid4())
        stamp = datetime.now(timezone.utc).isoformat()
        storage.add_conversation({"id": self.conversation_id, "title": "RFI review", "source_sha256": self.conversation_id,
                                  "audio_filename": "test.mp3", "audio_media_type": "audio/mpeg",
                                  "duration_ms": 5000, "created_at": stamp, "notebook_id": self.book})
        storage.save_transcript({"conversation_id": self.conversation_id, "revision": 1, "language": "en",
                                 "segments_json": json.dumps([{"id": "rfi-passage", "start_ms": 1200, "end_ms": 3000,
                                    "text": "The valve drawing remains pending.", "speaker_cluster_id": "speaker-1",
                                    "speaker_uncertain": False, "words": []}]), "turns_json": "[]", "updated_at": stamp})
        with storage.connect() as conn:
            conn.execute("INSERT INTO meeting_notes (id, conversation_id, markdown, updated_at) VALUES (?, ?, ?, ?)",
                         (str(uuid.uuid4()), self.conversation_id, "Valve review on Friday", stamp))
        self.page = pages.create("Valve specification", self.book, "The isolation valve is stainless steel.")["id"]
        self.other_page = pages.create("Valve procurement", self.other, "Order a spare valve.")["id"]

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_ranked_results_open_passage_note_and_page(self):
        rows = source_search.search_sources("valve", self.book)
        self.assertEqual({row["source_type"] for row in rows}, {"passage", "note", "page"})
        self.assertFalse(any(row["source_id"] == self.other_page for row in rows))
        passage = next(row for row in rows if row["source_type"] == "passage")
        self.assertEqual(passage["target"], f"/conversation/{self.conversation_id}?at=rfi-passage&t=1200")
        self.assertEqual(passage["start_ms"], 1200)
        self.assertTrue(all("[" in row["excerpt"] for row in rows))
        self.assertEqual(len(source_search.search_sources("valve")), 4)

    def test_index_reflects_edits_and_deleted_pages(self):
        self.assertTrue(source_search.search_sources("stainless", self.book))
        with storage.connect() as conn:
            conn.execute("UPDATE pages SET trashed_at = ? WHERE id = ?", (datetime.now(timezone.utc).isoformat(), self.page))
        self.assertEqual(source_search.search_sources("stainless", self.book), [])
        with self.assertRaises(ValueError):
            source_search.search_sources('"')


if __name__ == "__main__":
    unittest.main()
