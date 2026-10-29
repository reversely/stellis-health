"""Standalone Markdown pages: revisioned writes, notebooks, and Recently Deleted."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import library, pages, storage, todos
from stellis.server import app

API = "/api/v1"


class PagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def new_page(self, **body) -> dict:
        response = self.client.post(f"{API}/pages", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_create_save_and_limits(self) -> None:
        page = self.new_page(title="  Plan \n  notes ")
        self.assertEqual((page["title"], page["revision"], page["markdown"]), ("Plan notes", 0, ""))
        self.assertEqual(self.new_page()["title"], "Untitled")
        self.assertEqual(self.client.post(f"{API}/pages", json={"title": "x" * 201}).status_code, 400)
        self.assertEqual(self.client.patch(f"{API}/pages/{page['id']}", json={"title": ""}).json()["title"], "Untitled")
        url = f"{API}/pages/{page['id']}/markdown"
        saved = self.client.put(url, json={"markdown": "# Hi", "expected_revision": 0})
        self.assertEqual(saved.json()["revision"], 1)
        self.assertEqual(self.client.put(url, json={"markdown": "Lost", "expected_revision": 0}).status_code, 409)
        self.assertEqual(self.client.put(url, json={"markdown": "é" * 100_001, "expected_revision": 1}).status_code, 413)
        fetched = self.client.get(f"{API}/pages/{page['id']}").json()
        self.assertEqual((fetched["markdown"], fetched["revision"]), ("# Hi", 1))
        self.assertEqual(self.client.get(f"{API}/pages/missing").status_code, 404)

    def test_save_returns_and_reads_todo_dates(self) -> None:
        page = self.client.post(f"{API}/pages", json={"markdown": "- [ ] a"}).json()
        task_id = todos.parse_tasks(page["markdown"])[0].id
        due = {"due_date": "2026-11-02", "due_time": "09:30"}
        saved = self.client.put(f"{API}/pages/{page['id']}/markdown", json={
            "markdown": page["markdown"], "expected_revision": page["revision"], "todo_dates": {task_id: due, "tzzzzzz": due}})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["todo_dates"], {task_id: due})
        self.assertEqual(self.client.get(f"{API}/pages/{page['id']}").json()["todo_dates"], {task_id: due})
        cleared = self.client.put(f"{API}/pages/{page['id']}/markdown", json={
            "markdown": page["markdown"], "expected_revision": saved.json()["revision"], "todo_dates": {task_id: None}})
        self.assertEqual(cleared.json()["todo_dates"], {})

    def test_notebook_filter_assign_and_delete(self) -> None:
        notebook = self.client.post(f"{API}/notebooks", json={"name": "Work"}).json()
        loose = self.new_page(title="Loose")
        filed = self.new_page(title="Filed")
        url = f"{API}/pages/{filed['id']}/notebook"
        self.assertEqual(self.client.put(url, json={"notebook_id": "unknown"}).status_code, 404)
        self.assertEqual(self.client.put(url, json={"notebook_id": notebook["id"]}).status_code, 200)
        ids = lambda query: [item["id"] for item in self.client.get(f"{API}/pages{query}").json()]
        self.assertEqual(ids("?notebook_id=none"), [loose["id"]])
        self.assertEqual(ids(f"?notebook_id={notebook['id']}"), [filed["id"]])
        self.assertEqual(ids(""), [filed["id"], loose["id"]])
        listed = next(book for book in self.client.get(f"{API}/notebooks").json()["notebooks"] if book["id"] == notebook["id"])
        self.assertEqual([item["id"] for item in listed["pages"]], [filed["id"]])
        self.client.delete(f"{API}/notebooks/{notebook['id']}")
        self.assertIsNone(self.client.get(f"{API}/pages/{filed['id']}").json()["notebook_id"])

    def test_trash_restore_delete_and_erase(self) -> None:
        page = self.new_page(title="Doomed")
        trash = f"{API}/trash/{page['id']}"
        self.assertEqual(self.client.post(f"{API}/pages/{page['id']}/trash").status_code, 200)
        self.assertEqual(self.client.get(f"{API}/pages/{page['id']}").status_code, 404)
        items = self.client.get(f"{API}/trash").json()
        self.assertEqual([(item["id"], item["kind"]) for item in items], [(page["id"], "page")])
        self.assertEqual(self.client.post(f"{trash}/restore").status_code, 200)
        self.assertEqual(self.client.get(f"{API}/pages/{page['id']}").status_code, 200)
        self.client.post(f"{API}/pages/{page['id']}/trash")
        self.assertEqual(self.client.delete(trash).status_code, 200)
        self.assertEqual(self.client.delete(trash).status_code, 404)

        old = self.new_page(title="Old")
        recent = self.new_page(title="Recent")
        self.client.post(f"{API}/pages/{old['id']}/trash")
        self.client.post(f"{API}/pages/{recent['id']}/trash")
        with storage.connect() as conn:
            conn.execute("UPDATE pages SET trashed_at = ? WHERE id = ?",
                         ((datetime.now(timezone.utc) - timedelta(days=31)).isoformat(), old["id"]))
        self.assertEqual(library.delete_expired()["deleted"], [old["id"]])
        self.assertEqual([item["id"] for item in self.client.get(f"{API}/trash").json()], [recent["id"]])

        library.erase_all()
        self.assertEqual(pages.list_pages(), [])
        self.assertEqual(self.client.get(f"{API}/trash").json(), [])


if __name__ == "__main__":
    unittest.main()
