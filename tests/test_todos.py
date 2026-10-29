"""Task lines are indexed, dated, gathered, and toggled (ADR 0059)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import library, pages, storage, todos
from stellis.server import app

API = "/api/v1"


class PureFunctionsTest(unittest.TestCase):
    def test_parse_tasks(self) -> None:
        markdown = "- [ ] open one ^tabc123\n  * [x] done\n+ [X] Done two\n- [-] canceled  \n- [ ]no\n```\n- [ ] fenced\n```\n- [ ] last"
        tasks = todos.parse_tasks(markdown)
        self.assertEqual([(t.line, t.indent, t.status, t.text, t.id) for t in tasks], [
            (1, "", "open", "open one", "tabc123"), (2, "  ", "done", "done", None),
            (3, "", "done", "Done two", None), (4, "", "canceled", "canceled", None), (9, "", "open", "last", None)])

    def test_empty_task_lines_are_not_tasks_and_ids_stay_off_text(self) -> None:
        tasks = todos.parse_tasks("- [ ] \n- [ ] ^tabc123\n- [ ] a ^tabc123 b\n- [ ] c ^tabc123")
        self.assertEqual([(t.line, t.text, t.id) for t in tasks], [(3, "a ^tabc123 b", None), (4, "c", "tabc123")])

    def test_task_lines_inside_quotes_and_callouts(self) -> None:
        markdown = "> [!note] Plan\n> - [ ] in callout ^tabc123\n> > - [x] nested\n>> * [-] tight\n> - [ ] \n> plain"
        tasks = todos.parse_tasks(markdown)
        self.assertEqual([(t.line, t.indent, t.status, t.text, t.id) for t in tasks], [
            (2, "> ", "open", "in callout", "tabc123"), (3, "> > ", "done", "nested", None),
            (4, ">> ", "canceled", "tight", None)])
        self.assertEqual(todos.set_marker("> > - [ ] nested", "done"), "> > - [x] nested")

    def test_shared_fixture_cases(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures" / "markdown_plain.json").read_text(encoding="utf-8"))
        for case in fixture["plain_markdown"]:
            self.assertEqual(todos.plain_markdown(case["input"]), case["expected"], case["name"])
        for case in fixture["inline_text"]:
            self.assertEqual(todos.markdown_inline_text(case["input"]), case["expected"], case["name"])

    def test_tasks_inside_toggles_and_columns_are_found(self) -> None:
        source = ("<details>\n<summary>Plan</summary>\n\n- [ ] in a toggle ^tabc123\n\n<details>\n<summary>Deep</summary>\n\n"
                  "- [x] nested ^tdef456\n\n</details>\n\n</details>\n\n<columns>\n<column>\n- [-] left\n</column>\n"
                  "<column>\n- [ ] right ^tghi789\n</column>\n</columns>\n")
        found = [(task.text, task.status, task.id) for task in todos.parse_tasks(source)]
        self.assertEqual(found, [("in a toggle", "open", "tabc123"), ("nested", "done", "tdef456"),
                                 ("left", "canceled", None), ("right", "open", "tghi789")])

    def test_plain_markdown(self) -> None:
        source = ("- [ ] call ^tabc123\n<bookmark url=\"https://a.example\">Site</bookmark>\n<bookmark url=\"https://b.example\"></bookmark>\n"
                  "<columns>\n<column>\nleft\n</column>\n<column>\nright\n</column>\n</columns>\n```\n- [ ] keep ^tabc123\n```\n")
        self.assertEqual(todos.plain_markdown(source),
                         "- [ ] call\n[Site](https://a.example)\nhttps://b.example\nleft\n\nright\n```\n- [ ] keep ^tabc123\n```\n")
        self.assertEqual(todos.plain_markdown("a ^tabc1234\nplain"), "a ^tabc1234\nplain")


class TodoIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def conversation(self, title="Call", notebook_id=None) -> str:
        conversation_id = str(uuid.uuid4())
        storage.add_conversation({"id": conversation_id, "title": title, "source_sha256": conversation_id,
                                  "audio_filename": f"{conversation_id}.webm", "audio_media_type": "audio/webm",
                                  "duration_ms": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                                  "notebook_id": notebook_id})
        return conversation_id

    def page(self, markdown="", **body) -> dict:
        response = self.client.post(f"{API}/pages", json={"markdown": markdown, **body})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def save_page(self, page: dict, markdown: str, **body) -> dict:
        response = self.client.put(f"{API}/pages/{page['id']}/markdown",
                                   json={"markdown": markdown, "expected_revision": page["revision"], **body})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def items(self, **query) -> list[dict]:
        groups = self.client.get(f"{API}/todos", params=query).json()["groups"]
        return [item for group in groups for item in group["items"]]

    def test_ids_are_assigned_and_reused_by_text(self) -> None:
        page = self.page("- [ ] Call Sam\n- [ ] Send draft\n- [ ] Send draft")
        ids = [t.id for t in todos.parse_tasks(page["markdown"])]
        self.assertTrue(all(ids) and len(set(ids)) == 3, page["markdown"])
        self.assertRegex(ids[0], r"^t[0-9a-z]{6}$")
        # The editor sends the same lines without IDs, reordered and with one removed.
        saved = self.save_page(page, "- [ ] send  draft\n- [ ] Call Sam")
        self.assertEqual([t.id for t in todos.parse_tasks(saved["markdown"])], [ids[1], ids[0]])
        self.assertEqual({item["id"] for item in self.items()}, {ids[0], ids[1]})
        # A pasted ID owned by another source is replaced.
        other = self.page(f"- [ ] copied ^{ids[0]}")
        self.assertNotIn(ids[0], other["markdown"])

    def test_callout_tasks_get_ids_that_keep_the_markers(self) -> None:
        page = self.page("> [!note] Plan\n> - [ ] call Avery\n> > - [ ] nested")
        lines = page["markdown"].split("\n")
        self.assertRegex(lines[1], r"^> - \[ \] call Avery \^t[0-9a-z]{6}$")
        self.assertRegex(lines[2], r"^> > - \[ \] nested \^t[0-9a-z]{6}$")
        self.assertEqual({item["text"] for item in self.items()}, {"call Avery", "nested"})
        item = next(item for item in self.items() if item["text"] == "call Avery")
        done = self.client.patch(f"{API}/todos/{item['id']}", json={"status": "done"})
        self.assertEqual(done.status_code, 200, done.text)
        stored = self.client.get(f"{API}/pages/{page['id']}").json()["markdown"]
        self.assertIn("> - [x] call Avery ^", stored)
        self.assertIn("> > - [ ] nested ^", stored)

    def test_size_limit_counts_the_added_ids(self) -> None:
        # 16,000 short tasks fit under the limit as typed and exceed it once each line gains " ^tXXXXXX".
        markdown = "\n".join(["- [ ] aaaaa"] * 16_000)
        self.assertLessEqual(len(markdown.encode()), todos.MAX_MARKDOWN)
        response = self.client.post(f"{API}/pages", json={"markdown": markdown})
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.client.get(f"{API}/pages").json(), [])
        self.assertEqual(self.items(), [])

    def test_conversation_notes_index_and_dates(self) -> None:
        conversation_id = self.conversation()
        url = f"{API}/conversations/{conversation_id}/notes"

        def put(markdown: str, revision: int, **body) -> dict:
            response = self.client.put(url, json={"markdown": markdown, "expected_revision": revision, **body})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        saved = put("- [ ] a\n- [ ] b", 0)
        task_id = todos.parse_tasks(saved["markdown"])[0].id
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(self.client.get(url).json()["markdown"], saved["markdown"])

        done = saved["markdown"].replace("- [ ] a", "- [x] a")
        put(done, 1)
        item = next(i for i in self.items(status="done") if i["id"] == task_id)
        self.assertIsNotNone(item["completed_at"])
        self.assertIsNone(item["canceled_at"])
        put(done, 2)
        self.assertEqual(self.items(status="done")[0]["completed_at"], item["completed_at"])

        canceled = done.replace("- [x] a", "- [-] a")
        put(canceled, 3)
        item = self.items(status="canceled")[0]
        self.assertIsNotNone(item["canceled_at"])
        self.assertIsNone(item["completed_at"])
        reopened = canceled.replace("- [-] a", "- [ ] a")
        put(reopened, 4)
        item = next(i for i in self.items() if i["id"] == task_id)
        self.assertEqual((item["completed_at"], item["canceled_at"]), (None, None))

        dates = {task_id: {"due_date": "2026-11-02", "due_time": "09:30"}, "tzzzzzz": {"due_date": "2026-01-01"}}
        with_dates = put(reopened, 5, todo_dates=dates)
        self.assertEqual(with_dates["todo_dates"], {task_id: {"due_date": "2026-11-02", "due_time": "09:30"}})
        self.assertEqual(self.client.get(url).json()["todo_dates"], with_dates["todo_dates"])
        item = next(i for i in self.items() if i["id"] == task_id)
        self.assertEqual((item["due_date"], item["due_time"]), ("2026-11-02", "09:30"))
        self.assertEqual(len(self.items()), 2)
        self.assertEqual(put(reopened, 6, todo_dates={task_id: None})["todo_dates"], {})
        item = next(i for i in self.items() if i["id"] == task_id)
        self.assertEqual((item["due_date"], item["due_time"]), (None, None))
        put("no tasks", 7)
        self.assertEqual(self.items(), [])

    def test_gathering_filters_and_order(self) -> None:
        notebook = self.client.post(f"{API}/notebooks", json={"name": "Work"}).json()["id"]
        older = self.page("- [ ] page todo\n- [x] page done", notebook_id=notebook)
        conversation_id = self.conversation("Weekly", notebook_id=notebook)
        self.client.put(f"{API}/conversations/{conversation_id}/notes",
                        json={"markdown": "- [ ] first\n- [ ] second", "expected_revision": 0})
        loose = self.page("- [ ] loose")
        groups = self.client.get(f"{API}/todos").json()["groups"]
        self.assertEqual([g["source"]["id"] for g in groups], [loose["id"], conversation_id, older["id"]])
        self.assertEqual([i["text"] for i in groups[1]["items"]], ["first", "second"])
        self.assertEqual(groups[1]["source"],
                         {"kind": "conversation", "id": conversation_id, "title": "Weekly", "notebook_id": notebook})
        self.assertEqual({i["text"] for i in self.items(notebook_id=notebook)}, {"page todo", "first", "second"})
        self.assertEqual([i["text"] for i in self.items(notebook_id="none")], ["loose"])
        self.assertEqual([i["text"] for i in self.items(notebook_id="all", status="done")], ["page done"])
        self.client.post(f"{API}/pages/{loose['id']}/trash")
        library.move_to_trash(conversation_id)
        self.assertEqual([i["text"] for i in self.items()], ["page todo"])

    def test_toggle_bumps_revision_and_deletion_removes_items(self) -> None:
        page = self.page("- [ ] keep\n  - [ ] nested ^tabc123\n```\n- [ ] fenced\n```")
        response = self.client.patch(f"{API}/todos/tabc123", json={"status": "done"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()["status"], response.json()["id"]), ("done", "tabc123"))
        self.assertIsNotNone(response.json()["completed_at"])
        current = self.client.get(f"{API}/pages/{page['id']}").json()
        self.assertEqual(current["revision"], page["revision"] + 1)
        lines = current["markdown"].split("\n")
        self.assertEqual(lines[1], "  - [x] nested ^tabc123")
        self.assertTrue(lines[0].startswith("- [ ] keep ^t"))
        stale = self.client.put(f"{API}/pages/{page['id']}/markdown",
                                json={"markdown": "x", "expected_revision": page["revision"]})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.patch(f"{API}/todos/tmissing", json={"status": "open"}).status_code, 404)
        self.assertEqual(self.client.patch(f"{API}/todos/tabc123", json={"status": "later"}).status_code, 422)

        self.client.post(f"{API}/pages/{page['id']}/trash")
        pages.delete_permanently(page["id"])
        conversation_id = self.conversation()
        self.client.put(f"{API}/conversations/{conversation_id}/notes",
                        json={"markdown": "- [ ] x", "expected_revision": 0})
        library.move_to_trash(conversation_id)
        library.delete_expired(now=datetime.now(timezone.utc) + timedelta(days=31))
        with storage.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM todo_items").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
