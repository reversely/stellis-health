"""Collections: optional, non-nesting groups of notebooks."""

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import library, notebooks, storage
from stellis.server import app


class CollectionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name, "STELLIS_AUTH_MODE": "open"})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def _make(self, path: str, **body) -> dict:
        response = self.client.post(f"/api/v1/{path}", json=body)
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()

    def test_create_rename_recolor_and_duplicate_name(self):
        first = self.client.post("/api/v1/collections", json={"name": "  Mission   work "})
        self.assertEqual(first.status_code, 201)
        record = first.json()
        self.assertEqual((record["name"], record["color"], record["notebook_ids"]), ("Mission work", notebooks.PALETTE[0], []))
        self.assertEqual(self._make("collections", name="Second")["color"], notebooks.PALETTE[1])
        patched = self.client.patch(f"/api/v1/collections/{record['id']}", json={"name": "Flight", "color": notebooks.PALETTE[3].lower()})
        self.assertEqual((patched.json()["name"], patched.json()["color"]), ("Flight", notebooks.PALETTE[3]))
        self.assertEqual(self.client.post("/api/v1/collections", json={"name": "FLIGHT"}).status_code, 409)
        self.assertEqual(self.client.patch(f"/api/v1/collections/{record['id']}", json={"color": "#00000"}).status_code, 400)
        custom = self.client.patch(f"/api/v1/collections/{record['id']}", json={"color": "#0a1b2c"})
        self.assertEqual(custom.json()["color"], "#0A1B2C")
        self.assertEqual(self.client.patch("/api/v1/collections/missing", json={"name": "x"}).status_code, 404)
        with storage.connect() as conn:
            self.assertNotIn("parent_id", {row["name"] for row in conn.execute("PRAGMA table_info(collections)")})

    def test_assign_move_delete_keeps_notebook_and_erase_all(self):
        one, two = self._make("collections", name="One"), self._make("collections", name="Two")
        a, b = self._make("notebooks", name="A"), self._make("notebooks", name="B")
        for book in (a, b):
            self.assertEqual(self.client.put(f"/api/v1/notebooks/{book['id']}/collection", json={"collection_id": one["id"]}).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/collections").json()["collections"][0]["notebook_ids"], [a["id"], b["id"]])
        self.client.put(f"/api/v1/notebooks/{a['id']}/collection", json={"collection_id": two["id"]})
        listed = {item["id"]: item["notebook_ids"] for item in self.client.get("/api/v1/collections").json()["collections"]}
        self.assertEqual(listed, {one["id"]: [b["id"]], two["id"]: [a["id"]]})
        self.assertEqual(self.client.put(f"/api/v1/notebooks/{a['id']}/collection", json={"collection_id": "missing"}).status_code, 404)
        self.assertEqual(self.client.put("/api/v1/notebooks/missing/collection", json={"collection_id": None}).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/collections/{one['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/v1/collections/{one['id']}").status_code, 404)
        books = {item["id"]: item["collection_id"] for item in self.client.get("/api/v1/notebooks").json()["notebooks"]}
        self.assertEqual(books, {a["id"]: two["id"], b["id"]: None})
        self.client.put(f"/api/v1/notebooks/{a['id']}/collection", json={"collection_id": None})
        self.client.put(f"/api/v1/notebooks/{b['id']}/collection", json={"collection_id": two["id"]})
        library.erase_all()
        with storage.connect() as conn:
            for table in ("collections", "notebooks"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)


if __name__ == "__main__":
    unittest.main()
