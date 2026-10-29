"""Folder export: ZIP layout, image links, ID handling, sanitized names, and exclusions (#284)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from stellis import export, storage
from stellis.server import app
from test_import import mp3_bytes

API = "/api/v1"


def png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "#3456a0").save(output, format="PNG")
    return output.getvalue()


class ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        post = lambda url, **kw: self.client.post(f"{API}{url}", **kw).json()
        self.collection = post("/collections", json={"name": "Work/Q3: plans"})
        self.book = post("/notebooks", json={"name": "..Hidden"})
        self.client.put(f"{API}/notebooks/{self.book['id']}/collection", json={"collection_id": self.collection["id"]})
        self.loose = post("/notebooks", json={"name": "No notebook"})
        page = post("/pages", json={"title": "../etc/passwd", "notebook_id": self.book["id"]})
        twin = post("/pages", json={"title": "../etc/passwd", "notebook_id": self.book["id"]})
        self.page_id = page["id"]
        image = post(f"/pages/{page['id']}/images", files={"file": ("a.png", png(), "image/png")})
        self.image_id = image["id"]
        self.client.put(f"{API}/pages/{page['id']}/markdown", json={"expected_revision": 0, "markdown":
            f"- [ ] call Ana ^tabc123\n\n![chart](stellis-image:{self.image_id})\n\n"
            '<bookmark url="https://example.com">Example</bookmark>\n'})
        self.client.put(f"{API}/pages/{twin['id']}/markdown", json={"expected_revision": 0, "markdown": "twin"})
        gone = post("/pages", json={"title": "Trashed", "notebook_id": self.book["id"]})
        self.client.post(f"{API}/pages/{gone['id']}/trash")
        post("/pages", json={"title": "Loose page"})
        imported = self.client.post(f"{API}/conversations/import",
                                    files={"file": ("meeting.mp3", mp3_bytes(), "audio/mpeg")}).json()["conversation"]
        self.conversation_id = imported["id"]
        self.client.put(f"{API}/conversations/{self.conversation_id}/notebook", json={"notebook_id": self.book["id"]})
        self.client.put(f"{API}/conversations/{self.conversation_id}/notes",
                        json={"expected_revision": 0, "markdown": "- [ ] send recap ^tdef456"})
        stamp = datetime.now(timezone.utc).isoformat()
        with storage.connect() as conn:
            conn.execute("""INSERT INTO enhanced_notes_runs (id, conversation_id, input_hash, transcript_revision,
                source_identity_hash, notes_revision, notes_hash, model_id, prompt_version, state, stage, result_json,
                source_starts_json, created_at, updated_at) VALUES ('run1', ?, 'h', 0, 's', 1, 'n', 'm', 'p',
                'succeeded', 'done', ?, '{}', ?, ?)""", (self.conversation_id, json.dumps(
                    {"overview": [{"text": "We agreed.", "source_segment_ids": []}], "topics": [],
                     "decisions": [], "action_items": []}), stamp, stamp))

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def fetch(self, **query) -> zipfile.ZipFile:
        response = self.client.get(f"{API}/export", params=query)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/zip")
        return zipfile.ZipFile(io.BytesIO(response.content))

    def test_markdown_format_layout_links_and_stripped_ids(self) -> None:
        archive = self.fetch(scope="all", format="markdown")
        names = sorted(archive.namelist())
        folder = "Work Q3 plans/Hidden"
        self.assertEqual(names, sorted([
            f"{folder}/etc passwd.md", f"{folder}/etc passwd (2).md", f"{folder}/meeting.md",
            f"{folder}/images/{self.image_id}.webp", "No notebook/Loose page.md"]))
        page = archive.read(f"{folder}/etc passwd.md").decode()
        self.assertIn(f"![chart](images/{self.image_id}.webp)", page)
        self.assertIn("[Example](https://example.com)", page)
        self.assertIn("- [ ] call Ana", page)
        for hidden in ("^tabc123", "stellis-image:", "<bookmark"):
            self.assertNotIn(hidden, page)
        conversation = archive.read(f"{folder}/meeting.md").decode()
        self.assertIn("## My Notes\n\n- [ ] send recap\n", conversation)
        self.assertIn("## Summary\n\n### Overview\n\n- We agreed.", conversation)
        self.assertNotIn("Trashed", "".join(names))
        self.assertNotIn(export.MANIFEST, names)
        self.assertEqual(archive.read(f"{folder}/images/{self.image_id}.webp")[:4], b"RIFF")

    def test_stellis_format_keeps_ids_and_writes_a_manifest(self) -> None:
        archive = self.fetch(scope="notebook", id=self.book["id"], format="stellis")
        page = archive.read("Work Q3 plans/Hidden/etc passwd.md").decode()
        self.assertIn("^tabc123", page)
        self.assertIn(f"stellis-image:{self.image_id}", page)
        self.assertIn("<bookmark", page)
        manifest = json.loads(archive.read(export.MANIFEST))
        self.assertEqual((manifest["version"], manifest["format"], manifest["scope"]), (1, "stellis", "notebook"))
        entry = next(item for item in manifest["items"] if item["id"] == self.page_id)
        self.assertEqual((entry["kind"], entry["title"], entry["notebook_id"], entry["revision"]),
                         ("page", "../etc/passwd", self.book["id"], 1))
        self.assertEqual(entry["images"], [{"id": self.image_id, "path": f"Work Q3 plans/Hidden/images/{self.image_id}.webp"}])
        self.assertEqual(manifest["collections"][0]["id"], self.collection["id"])
        self.assertEqual(manifest["notebooks"][0]["collection_id"], self.collection["id"])
        self.assertEqual(len(manifest["items"]), 3)
        self.assertNotIn("No notebook/Loose page.md", archive.namelist())

    def test_scopes_errors_cleanup_and_name_rules(self) -> None:
        names = self.fetch(scope="collection", id=self.collection["id"]).namelist()
        self.assertTrue(all(name.startswith("Work Q3 plans/") for name in names))
        self.assertEqual(self.client.get(f"{API}/export", params={"scope": "notebook", "id": "nope"}).status_code, 404)
        self.assertEqual(self.client.get(f"{API}/export", params={"scope": "notebook"}).status_code, 400)
        self.assertEqual(self.client.get(f"{API}/export", params={"format": "pdf"}).status_code, 422)
        self.assertEqual(list(Path(self.temp.name).glob(".export-*")), [])
        taken: set[str] = set()
        self.assertEqual(export.safe_name("a" * 300, taken), "a" * export.MAX_NAME)
        self.assertEqual(export.safe_name("A" * export.MAX_NAME, taken), "A" * 96 + " (2)")
        self.assertEqual(export.safe_name("...", taken), "Untitled")
