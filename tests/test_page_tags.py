"""Page tags: replace semantics, counts, search, and deletion."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import library, pages, storage
from stellis.server import app

API = "/api/v1"


class PageTagsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        self.tag_a = library.create_category("Tag A")["id"]
        self.tag_b = library.create_category("Tag B")["id"]

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def page(self, title: str, markdown: str = "") -> str:
        return pages.create(title, markdown=markdown)["id"]

    def tag(self, page_id: str, *ids: str):
        return self.client.put(f"{API}/pages/{page_id}/categories", json={"category_ids": list(ids)})

    def counts(self) -> dict:
        return {c["id"]: c["page_count"] for c in self.client.get(f"{API}/categories").json()}

    def test_set_replace_clear_and_lists(self) -> None:
        page_id = self.page("Plan")
        self.assertEqual(self.tag(page_id, self.tag_a, self.tag_a, self.tag_b).json()["category_ids"],
                         sorted([self.tag_a, self.tag_b]))
        self.assertEqual(self.tag(page_id, self.tag_b).json()["category_ids"], [self.tag_b])
        self.assertEqual(self.client.get(f"{API}/pages/{page_id}").json()["category_ids"], [self.tag_b])
        self.assertEqual(self.client.get(f"{API}/pages").json()[0]["category_ids"], [self.tag_b])
        self.assertEqual(self.counts()[self.tag_b], 1)
        self.assertEqual(self.tag(page_id).json()["category_ids"], [])
        self.assertEqual(self.counts()[self.tag_b], 0)
        self.assertEqual(self.tag("missing", self.tag_a).status_code, 404)
        self.assertEqual(self.tag(page_id, "missing").status_code, 404)

    def test_notebook_and_trash_lists_carry_tags(self) -> None:
        notebook = self.client.post(f"{API}/notebooks", json={"name": "Work"}).json()["id"]
        page_id = pages.create("Plan", notebook)["id"]
        self.tag(page_id, self.tag_a)
        book = next(b for b in self.client.get(f"{API}/notebooks").json()["notebooks"] if b["id"] == notebook)
        self.assertEqual(book["pages"][0]["category_ids"], [self.tag_a])
        self.client.post(f"{API}/pages/{page_id}/trash")
        self.assertEqual(self.counts()[self.tag_a], 0)
        item = next(i for i in self.client.get(f"{API}/trash").json() if i["id"] == page_id)
        self.assertEqual(item["category_ids"], [self.tag_a])

    def test_search_and_uncategorized_filter(self) -> None:
        tagged = self.page("Zebra roadmap", "- [ ] ship the quokka feature\n")
        self.page("Other", "quokka notes")
        self.tag(tagged, self.tag_a)
        hits = self.client.get(f"{API}/search", params={"q": "zebra"}).json()["results"]
        self.assertEqual([(h["kind"], h["page_id"], h["title"]) for h in hits], [("page", tagged, "Zebra roadmap")])
        body = self.client.get(f"{API}/search", params={"q": "quokka"}).json()["results"]
        self.assertEqual(len(body), 2)
        self.assertNotIn("<!--", body[0]["excerpt"] + body[1]["excerpt"])
        only = self.client.get(f"{API}/search", params={"q": "quokka", "category_id": self.tag_a}).json()["results"]
        self.assertEqual([h["page_id"] for h in only], [tagged])
        none = self.client.get(f"{API}/search", params={"q": "quokka", "category_id": "uncategorized"}).json()["results"]
        self.assertEqual(len(none), 1)
        self.assertNotEqual(none[0]["page_id"], tagged)
        pages.move_to_trash(tagged)
        self.assertEqual(self.client.get(f"{API}/search", params={"q": "zebra"}).json()["total"], 0)

    def test_deletion_removes_links(self) -> None:
        kept, gone, erased = self.page("A"), self.page("B"), self.page("C")
        for page_id in (kept, gone, erased):
            self.tag(page_id, self.tag_a)
        pages.move_to_trash(gone)
        self.assertEqual(self.client.delete(f"{API}/trash/{gone}").status_code, 200)
        self.assertEqual(self.client.delete(f"{API}/categories/{self.tag_a}").status_code, 200)
        self.assertEqual(self.client.get(f"{API}/pages/{kept}").json()["category_ids"], [])
        tag_b = self.tag_b
        self.tag(erased, tag_b)
        library.erase_all()
        with storage.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM page_categories").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
