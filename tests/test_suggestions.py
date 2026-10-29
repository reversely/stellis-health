"""Agent suggestions add or close to-dos only after the user reviews them (ADR 0059)."""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import pages, storage, suggestions, todos
from stellis.server import app

API = "/api/v1"


class SuggestionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def notebook(self, name="Work", todo_list=None) -> str:
        notebook_id = self.client.post(f"{API}/notebooks", json={"name": name}).json()["id"]
        if todo_list:
            self.assertEqual(self.client.patch(f"{API}/notebooks/{notebook_id}", json={"todo_list": todo_list}).status_code, 200)
        return notebook_id

    def page(self, markdown="", notebook_id=None) -> dict:
        response = self.client.post(f"{API}/pages", json={"markdown": markdown, "notebook_id": notebook_id})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def conversation(self, notebook_id) -> str:
        conversation_id = str(uuid.uuid4())
        storage.add_conversation({"id": conversation_id, "title": "Call", "source_sha256": conversation_id,
                                  "audio_filename": f"{conversation_id}.webm", "audio_media_type": "audio/webm",
                                  "duration_ms": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                                  "notebook_id": notebook_id})
        return conversation_id

    def add(self, page: dict, text: str, **values) -> dict:
        return suggestions.propose("transcript-agent", "page", page["id"], "add_todo", {"text": text, **values},
                                   evidence=[{"segment_id": "s1", "quote": text}])

    def close(self, page: dict, todo_id: str, status: str = "done") -> dict:
        return suggestions.propose("transcript-agent", "page", page["id"], "set_todo_status", {"status": status},
                                   todo_id=todo_id)

    def accept(self, *ids: str) -> dict:
        response = self.client.post(f"{API}/suggestions/accept", json={"ids": list(ids)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def read(self, page: dict) -> dict:
        return self.client.get(f"{API}/pages/{page['id']}").json()

    def test_settings_resolution(self) -> None:
        collection = self.client.post(f"{API}/collections", json={"name": "Clients"}).json()
        self.assertEqual(collection["todo_list"], "off")
        notebook_id = self.notebook()

        def listed() -> dict:
            return next(n for n in self.client.get(f"{API}/notebooks").json()["notebooks"] if n["id"] == notebook_id)

        self.assertEqual((listed()["todo_list"], listed()["todo_enabled"]), ("inherit", False))
        self.client.put(f"{API}/notebooks/{notebook_id}/collection", json={"collection_id": collection["id"]})
        self.assertFalse(listed()["todo_enabled"])
        self.assertEqual(self.client.patch(f"{API}/collections/{collection['id']}", json={"todo_list": "on"}).json()["todo_list"], "on")
        self.assertTrue(listed()["todo_enabled"])
        self.client.patch(f"{API}/notebooks/{notebook_id}", json={"todo_list": "off"})
        self.assertFalse(listed()["todo_enabled"])
        self.client.put(f"{API}/notebooks/{notebook_id}/collection", json={"collection_id": None})
        self.client.patch(f"{API}/notebooks/{notebook_id}", json={"todo_list": "on"})
        self.assertTrue(suggestions.todo_enabled(notebook_id))
        self.assertFalse(suggestions.todo_enabled(None))
        self.assertEqual(self.client.patch(f"{API}/notebooks/{notebook_id}", json={"todo_list": "maybe"}).status_code, 422)
        self.assertEqual(self.client.patch(f"{API}/collections/{collection['id']}", json={"todo_list": "inherit"}).status_code, 422)

    def test_refused_when_the_list_is_off_and_values_are_validated(self) -> None:
        page = self.page(notebook_id=self.notebook())
        with self.assertRaises(PermissionError):
            self.add(page, "Call Sam")
        loose = self.page()
        with self.assertRaises(PermissionError):
            self.add(loose, "Call Sam")
        on = self.page(notebook_id=self.notebook("On", "on"))
        for values in ({"text": ""}, {"text": "x" * 501}, {"text": "a", "due_date": "2026-13-01"},
                       {"text": "a", "due_date": "2026-02-01", "due_time": "25:00"}, {"text": "a", "extra": 1},
                       {"text": "call ^tabc123"}, {"text": "[ ] call"}, {"text": "- [x] call"}, {"text": "call <bookmark url=\"x\">"},
                       {"text": "## call"}, {"text": "> call"}, {"text": "1. call"}, {"text": "```js"}):
            with self.assertRaises(ValueError, msg=values):
                suggestions.propose("transcript-agent", "page", on["id"], "add_todo", values)
        self.assertEqual(suggestions.validate_values("add_todo", {"text": "Email a^tb, mark [x] later, 2 < 3"})["text"],
                         "Email a^tb, mark [x] later, 2 < 3")
        with self.assertRaises(LookupError):
            self.close(on, "tmissing")
        saved = self.page("- [ ] real", on["notebook_id"])
        with self.assertRaises(ValueError):
            self.close(saved, todos.parse_tasks(saved["markdown"])[0].id, "later")

    def test_add_todo_creates_the_heading_then_reuses_it(self) -> None:
        page = self.page("# Notes\n\nIntro", self.notebook(todo_list="on"))
        first = self.add(page, "Call Sam", due_date="2026-11-02", due_time="09:30")
        self.assertEqual(first["evidence"], [{"segment_id": "s1", "quote": "Call Sam"}])
        self.assertEqual(self.accept(first["id"]), {"accepted": [first["id"]], "superseded": []})
        saved = self.read(page)
        task = todos.parse_tasks(saved["markdown"])[0]
        self.assertEqual(saved["markdown"], f"# Notes\n\nIntro\n\n## To-dos\n\n- [ ] Call Sam ^{task.id}")
        self.assertEqual(saved["revision"], page["revision"] + 1)
        item = next(i for g in self.client.get(f"{API}/todos").json()["groups"] for i in g["items"])
        self.assertEqual((item["id"], item["due_date"], item["due_time"]), (task.id, "2026-11-02", "09:30"))
        # A later section does not pull the new item out of To-dos.
        saved = self.client.put(f"{API}/pages/{page['id']}/markdown", json={
            "markdown": saved["markdown"] + "\n\n## Later\n\nText", "expected_revision": saved["revision"]}).json()
        second = self.add(page, "Send draft")
        self.accept(second["id"])
        lines = self.read(page)["markdown"].split("\n")
        self.assertEqual([t.text for t in todos.parse_tasks("\n".join(lines))], ["Call Sam", "Send draft"])
        self.assertLess(lines.index(next(l for l in lines if "Send draft" in l)), lines.index("## Later"))
        self.assertEqual(lines.count("## To-dos"), 1)

    def test_add_todo_in_a_conversation_without_notes(self) -> None:
        conversation_id = self.conversation(self.notebook(todo_list="on"))
        made = suggestions.propose("transcript-agent", "conversation", conversation_id, "add_todo", {"text": "Book room"},
                                   source_kind="conversation", source_id=conversation_id)
        self.accept(made["id"])
        notes = self.client.get(f"{API}/conversations/{conversation_id}/notes").json()
        self.assertEqual(notes["revision"], 1)
        self.assertRegex(notes["markdown"], r"^## To-dos\n\n- \[ \] Book room \^t[0-9a-z]{6}$")

    def test_amend_then_accept_keeps_the_original(self) -> None:
        page = self.page(notebook_id=self.notebook(todo_list="on"))
        made = self.add(page, "Call Sam")
        amended = self.client.patch(f"{API}/suggestions/{made['id']}", json={"values": {"text": "Call Sam Lee", "due_date": "2026-11-03"}})
        self.assertEqual(amended.status_code, 200, amended.text)
        self.assertEqual(amended.json()["original_values"], {"text": "Call Sam"})
        self.assertIsNotNone(amended.json()["amended_at"])
        self.assertEqual(self.client.patch(f"{API}/suggestions/{made['id']}", json={"values": {"text": ""}}).status_code, 400)
        listed = self.client.get(f"{API}/suggestions", params={"target_kind": "page", "target_id": page["id"]}).json()["suggestions"]
        self.assertEqual([s["id"] for s in listed], [made["id"]])
        self.assertEqual(self.client.get(f"{API}/suggestions", params={"notebook_id": page["notebook_id"]}).json()["suggestions"][0]["evidence"],
                         [{"segment_id": "s1", "quote": "Call Sam"}])
        self.accept(made["id"])
        self.assertIn("- [ ] Call Sam Lee ^", self.read(page)["markdown"])
        row = self.client.patch(f"{API}/suggestions/{made['id']}", json={"values": {"text": "Again"}})
        self.assertEqual(row.status_code, 409)
        with storage.connect() as conn:
            stored = conn.execute("SELECT state, original_values_json FROM suggestions").fetchone()
        self.assertEqual((stored["state"], stored["original_values_json"]), ("accepted", '{"text": "Call Sam"}'))

    def test_set_todo_status_and_superseded_suggestions(self) -> None:
        page = self.page("- [ ] one\n- [ ] two\n- [ ] three", self.notebook())
        one, two, three = (t.id for t in todos.parse_tasks(page["markdown"]))
        listed = lambda: {i["id"]: i["pending_suggestions"] for status in ("open", "done", "canceled")
                          for g in self.client.get(f"{API}/todos", params={"status": status}).json()["groups"] for i in g["items"]}
        done, canceled = self.close(page, one), self.close(page, one, "canceled")
        self.assertEqual(listed()[one], [done["id"], canceled["id"]])
        # Accept time: the second suggestion expected an open line.
        self.assertEqual(self.accept(done["id"], canceled["id"]), {"accepted": [done["id"]], "superseded": [canceled["id"]]})
        self.assertIn(f"- [x] one ^{one}", self.read(page)["markdown"])
        self.assertEqual(listed()[one], [])
        # The user's own save wins over a pending change, and so does deleting the line.
        status, gone = self.close(page, two), self.close(page, three)
        current = self.read(page)
        edited = current["markdown"].replace("- [ ] two", "- [-] two").replace(f"- [ ] three ^{three}", "kept text")
        self.client.put(f"{API}/pages/{page['id']}/markdown", json={"markdown": edited, "expected_revision": current["revision"]})
        self.assertEqual(self.client.get(f"{API}/suggestions", params={"target_kind": "page", "target_id": page["id"]}).json()["suggestions"], [])
        with storage.connect() as conn:
            states = {r["id"]: r["state"] for r in conn.execute("SELECT id, state FROM suggestions")}
        self.assertEqual((states[status["id"]], states[gone["id"]]), ("superseded", "superseded"))
        self.assertEqual(self.accept(status["id"]), {"accepted": [], "superseded": []})

    def test_reject_and_preview_save_nothing_and_unknown_ids_change_nothing(self) -> None:
        page = self.page("- [ ] one", self.notebook(todo_list="on"))
        one = todos.parse_tasks(page["markdown"])[0].id
        added, closed = self.add(page, "Call Sam"), self.close(page, one)
        preview = self.client.post(f"{API}/suggestions/preview", json={"ids": [added["id"], closed["id"]], "decision": "accept"})
        self.assertEqual(preview.status_code, 200, preview.text)
        (target,) = preview.json()["targets"]
        self.assertEqual((target["target_kind"], target["target_id"]), ("page", page["id"]))
        self.assertIn(f"- [x] one ^{one}", target["markdown"])
        self.assertIn("- [ ] Call Sam ^", target["markdown"])
        kept = self.client.post(f"{API}/suggestions/preview", json={"ids": [added["id"]], "decision": "reject"}).json()["targets"][0]
        self.assertEqual(kept["markdown"], page["markdown"])
        self.assertEqual(self.read(page), page)
        self.assertEqual(len(self.client.get(f"{API}/suggestions", params={"target_kind": "page", "target_id": page["id"]}).json()["suggestions"]), 2)
        for path in ("accept", "reject"):
            missing = self.client.post(f"{API}/suggestions/{path}", json={"ids": [added["id"], "nope"]})
            self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.client.post(f"{API}/suggestions/preview", json={"ids": ["nope"], "decision": "accept"}).status_code, 404)
        self.assertEqual(self.read(page), page)
        rejected = self.client.post(f"{API}/suggestions/reject", json={"ids": [added["id"]]})
        self.assertEqual(rejected.json(), {"rejected": [added["id"]]})
        self.assertEqual(self.read(page), page)
        self.assertEqual([s["id"] for s in self.client.get(f"{API}/suggestions", params={"target_kind": "page", "target_id": page["id"]}).json()["suggestions"]],
                         [closed["id"]])

    def test_deleting_or_erasing_removes_suggestions(self) -> None:
        notebook_id = self.notebook(todo_list="on")
        page = self.page("- [ ] one", notebook_id)
        self.add(page, "Call Sam")
        conversation_id = self.conversation(notebook_id)
        suggestions.propose("transcript-agent", "page", page["id"], "add_todo", {"text": "From call"},
                            source_kind="conversation", source_id=conversation_id)
        other = self.page(notebook_id=notebook_id)
        self.add(other, "Stays")

        def count() -> int:
            with storage.connect() as conn:
                return conn.execute("SELECT COUNT(*) FROM suggestions").fetchone()[0]

        from stellis import library
        library.move_to_trash(conversation_id)
        library.delete_permanently(conversation_id)
        self.assertEqual(count(), 2)
        pages.move_to_trash(page["id"])
        pages.delete_permanently(page["id"])
        self.assertEqual(count(), 1)
        library.erase_all()
        self.assertEqual(count(), 0)


if __name__ == "__main__":
    unittest.main()
