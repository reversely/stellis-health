"""Vocabulary migration, bulk operations, and bounded speech hints."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import processing, storage
from stellis.server import app


class VocabularyWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name, "STELLIS_AUTH_MODE": "open"})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_legacy_terms_migrate_to_other_without_changing_identity(self) -> None:
        path = Path(self.temp.name, "stellis.sqlite3")
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE vocabulary (id TEXT PRIMARY KEY, term TEXT NOT NULL, "
                         "normalized TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute("INSERT INTO vocabulary VALUES (?, ?, ?, ?, ?)",
                         ("existing-id", "ORBIT", "orbit", "2026-01-01", "2026-01-01"))
        listed = self.client.get("/api/v1/vocabulary").json()
        self.assertEqual([(term["id"], term["term"], term["kind"]) for term in listed],
                         [("existing-id", "ORBIT", "other")])
        self.assertEqual(processing.vocabulary_snapshot(), ["ORBIT"])

    def test_batch_add_move_delete_and_atomic_validation(self) -> None:
        base = "/api/v1/vocabulary"
        added = self.client.post(f"{base}/batch", json={"kind": "other", "terms": ["CubeSat", "LiDAR", "cubesat"]})
        self.assertEqual(added.status_code, 200)
        self.assertEqual([term["term"] for term in added.json()["added"]], ["CubeSat", "LiDAR"])
        self.assertEqual(added.json()["skipped"], ["cubesat"])
        ids = [term["id"] for term in added.json()["added"]]
        self.assertEqual(self.client.post(f"{base}/batch", json={"kind": "name", "terms": ["Riley", ""]}).status_code, 400)
        self.assertEqual(len(self.client.get(base).json()), 2)
        name = self.client.post(f"{base}/batch", json={"kind": "name", "terms": ["Riley"]}).json()["added"][0]
        moved = self.client.post(f"{base}/move", json={"kind": "name", "ids": ids})
        self.assertEqual(moved.json(), {"moved": 2})
        self.assertEqual({item["term"] for item in self.client.get(base, params={"kind": "name"}).json()},
                         {"CubeSat", "LiDAR", "Riley"})
        self.assertEqual(self.client.get(base, params={"kind": "other"}).json(), [])
        self.assertEqual(set(processing.vocabulary_snapshot()), {"CubeSat", "LiDAR", "Riley"})
        missing = self.client.post(f"{base}/delete", json={"ids": [ids[0], "missing"]})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(len(self.client.get(base).json()), 3)
        deleted = self.client.post(f"{base}/delete", json={"ids": [*ids, name["id"]]})
        self.assertEqual(deleted.json(), {"deleted": 3})
        self.assertEqual(self.client.get(base).json(), [])

    def test_large_library_keeps_recent_terms_within_model_prompt(self) -> None:
        terms = [f"VocabularyTerm{index:03d}" for index in range(40)]
        response = self.client.post("/api/v1/vocabulary/batch", json={"kind": "other", "terms": terms})
        self.assertEqual(len(response.json()["added"]), 40)
        snapshot = processing.vocabulary_snapshot()
        self.assertLessEqual(len("Vocabulary: " + ", ".join(snapshot)), 400)
        self.assertIn(terms[-1], snapshot)
        self.assertNotIn(terms[0], snapshot)
        self.assertEqual(len(self.client.get("/api/v1/vocabulary").json()), 40)


if __name__ == "__main__":
    unittest.main()
