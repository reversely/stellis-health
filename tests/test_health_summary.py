"""Tests for the cited health summary built from all documents."""

import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi import testclient

from stellis import agent_gateway
from stellis import health_summary
from stellis import notebook_chat
from stellis import storage
import sample_records
from stellis import server

REPLY = ("## Overview\nDenver lost weight with a dietitian. [[source:1]]\n\n"
         "## Medications\nHydrochlorothiazide was stopped. [[source:2]]")


class HealthSummaryTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ,
                                      {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def refresh(self, reply: str = REPLY):
        turn = mock.AsyncMock(return_value=(reply, "session"))
        with mock.patch.object(agent_gateway, "turn", turn):
            response = self.client.post("/api/v1/summary/refresh")
        return response, turn

    def test_no_documents_is_a_400(self):
        response, turn = self.refresh()

        self.assertEqual(response.status_code, 400)
        turn.assert_not_awaited()
        self.assertIsNone(self.client.get("/api/v1/summary").json()["summary"])

    def test_summary_reads_recent_and_relevant_pages_and_is_stored(self):
        sample_records.load()

        response, turn = self.refresh()

        self.assertEqual(response.status_code, 200, response.text)
        context = json.loads(turn.await_args.args[0].split("\n\n", 1)[1])
        titles = [source["title"] for source in context["sources"]]
        self.assertLessEqual(len(titles), health_summary.MAX_PASSAGES)
        self.assertIn("2026-04-02",
                      [source["date"] for source in context["sources"]])
        self.assertTrue(any("Telephone encounter" in title for title in titles))
        self.assertTrue(all(source["date"] for source in context["sources"]))
        current = self.client.get("/api/v1/summary").json()
        self.assertEqual(current["document_count"], 47)
        stored = current["summary"]
        self.assertEqual(
            stored["content"],
            REPLY.replace("[[source:1]]",
                          "[[1]]").replace("[[source:2]]", "[[2]]"))
        self.assertEqual([item["n"] for item in stored["citations"]], [1, 2])
        self.assertEqual(stored["document_count"], 47)
        self.assertEqual(stored["selected_document_count"], len({
            citation.source_id for citation in health_summary.select_passages(
                health_summary._sources())}))
        self.assertEqual(stored["passage_count"], len(context["sources"]))

    def test_legacy_summary_retains_unknown_coverage(self):
        with storage.connect() as conn:
            conn.execute("""CREATE TABLE health_summaries (
                id TEXT PRIMARY KEY, content TEXT NOT NULL,
                citations_json TEXT NOT NULL, document_count INTEGER NOT NULL,
                created_at TEXT NOT NULL)""")
            conn.execute("INSERT INTO health_summaries VALUES (?, ?, ?, ?, ?)",
                         ("old", "Existing summary", "[]", 47, "2026-01-01"))
        summary = self.client.get("/api/v1/summary").json()["summary"]
        self.assertEqual(summary["content"], "Existing summary")
        self.assertIsNone(summary["selected_document_count"])
        self.assertIsNone(summary["passage_count"])

    def test_bad_citations_store_nothing(self):
        sample_records.load()

        response, _ = self.refresh("## Overview\nInvented. [[source:999]]")

        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.client.get("/api/v1/summary").json()["summary"])


class PassageSelectionTest(unittest.TestCase):

    def test_long_recent_document_preserves_historical_topic_capacity(self):
        recent = [notebook_chat.Passage(
            "document:new", "Recent", f"Page {number}", "/document/new",
            page=number) for number in range(60)]
        older = [notebook_chat.Passage(
            f"document:old-{topic}-{number}", topic, f"{topic} {number}",
            "/document/old") for topic in health_summary._QUERIES
            for number in range(8)]
        sources = [{"id": "document:new"}]
        passages = recent + older
        ranked = [older[offset:offset + 8] for offset in range(0, 24, 8)]
        with mock.patch.object(notebook_chat, "read_passages", return_value=passages), \
                mock.patch.object(notebook_chat, "rank", side_effect=ranked):
            chosen = health_summary.select_passages(sources)
        self.assertEqual(len(chosen), health_summary.MAX_PASSAGES)
        self.assertEqual(len(set(chosen)), len(chosen))
        self.assertTrue(set(older).issubset(chosen))
        self.assertEqual(len(set(recent).intersection(chosen)), 12)

    def test_recent_sources_share_slots_and_duplicate_matches_release_capacity(self):
        passages = [notebook_chat.Passage(
            f"document:{source}", str(source), f"{source}/{page}", "/document/x")
            for source in range(6) for page in range(20)]
        sources = [{"id": f"document:{source}"} for source in range(6)]
        with mock.patch.object(notebook_chat, "read_passages", return_value=passages), \
                mock.patch.object(notebook_chat, "rank", return_value=[passages[0]]):
            chosen = health_summary.select_passages(sources)
        self.assertEqual(len(chosen), health_summary.MAX_PASSAGES)
        self.assertEqual(len(set(chosen)), len(chosen))
        self.assertEqual(len({passage.source_id for passage in chosen[:12]}), 6)


if __name__ == "__main__":
    unittest.main()
