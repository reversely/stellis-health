"""Notebooks, capture-time ordering, and account settings."""

import io
import json
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import auth, calendar, notebooks, storage
from stellis.server import app
from test_import import mp3_bytes


def tone_mp3(frequency_step: int) -> bytes:
    """A distinct one-second clip so each import has its own hash."""
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(struct.pack("<16000h", *([frequency_step * 50] * 16000)))
    return mp3_bytes(output.getvalue())


class SummaryMarkdownTest(unittest.TestCase):
    def test_edited_summary_bullets_read_as_plain_text(self) -> None:
        sections = notebooks._markdown_sections("## Decisions\n- Ship **v2** with `snake_case`\n- See [the plan](https://a.example)\n")
        self.assertEqual(sections, {"decisions": ["Ship v2 with snake_case", "See the plan"]})


class NotebookAccountTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name, "STELLIS_AUTH_MODE": "required",
                                           "STELLIS_DEV_ACCOUNT": "0", "STELLIS_DEV_PASSWORD": ""})
        self.env.start()
        self.client = TestClient(app, follow_redirects=False)
        self.password = "correct horse battery staple"
        self.client.post("/api/v1/auth/register", json={"username": "owner", "password": self.password})
        auth.complete_onboarding(auth.account_id_for_username("owner"))
        self.capabilities = patch("stellis.server.processing.capabilities",
                                  return_value={"features": {"transcribe": False, "diarize": False}})
        self.capabilities.start()

    def tearDown(self):
        self.capabilities.stop()
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def _import(self, name: str, step: int, **form: str) -> dict:
        response = self.client.post("/api/v1/conversations/import", data=form,
                                    files={"file": (name, tone_mp3(step), "audio/mpeg")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["conversation"]

    def test_list_orders_by_capture_time_and_rejects_implausible_times(self):
        older = self._import("older.mp3", 1, recorded_at="2026-10-15T14:17:00Z")
        newer = self._import("newer.mp3", 2, recorded_at="2026-10-16T16:38:00Z")
        unknown = self._import("unknown.mp3", 3, recorded_at="2999-01-01T00:00:00Z")
        self.assertIsNone(unknown["recorded_at"])
        self.assertEqual(older["recorded_at"], "2026-10-15T14:17:00+00:00")
        ids = [item["id"] for item in self.client.get("/api/v1/conversations").json()]
        # The import without a capture time falls back to now, so it leads.
        self.assertEqual(ids, [unknown["id"], newer["id"], older["id"]])

    def test_notebook_lifecycle_progress_and_assignment(self):
        created = self.client.post("/api/v1/notebooks", json={"name": "  Migration   Project "})
        self.assertEqual(created.status_code, 200)
        book = created.json()
        self.assertEqual(book["name"], "Migration Project")
        self.assertEqual(book["color"], "#495575")
        self.assertEqual(self.client.post("/api/v1/notebooks", json={"name": "migration project"}).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/notebooks", json={"name": "X", "color": "red"}).status_code, 400)
        second = self.client.post("/api/v1/notebooks", json={"name": "Hiring"}).json()
        self.assertEqual(second["color"], "#56465C")
        preset = self.client.post("/api/v1/notebooks", json={"name": "Preset", "color": "#5b5f97"}).json()
        custom = self.client.post("/api/v1/notebooks", json={"name": "Custom", "color": "#ff0000"}).json()
        self.assertEqual((preset["color"], custom["color"]), ("#5B5F97", "#FF0000"))
        for extra in (preset, custom):
            self.client.delete(f"/api/v1/notebooks/{extra['id']}")
        listing = self.client.get("/api/v1/notebooks").json()
        self.assertEqual((listing["palette"], listing["presets"][:5], len(listing["presets"])),
                         (["#495575", "#56465C", "#594F4F", "#547980", "#45ADA8"], ["#495575", "#56465C", "#594F4F", "#547980", "#45ADA8"], 12))

        first = self._import("first.mp3", 4, recorded_at="2026-09-29T10:00:00Z", notebook_id=book["id"])
        latest = self._import("latest.mp3", 5, recorded_at="2026-10-15T10:00:00Z")
        self.assertEqual(self.client.put(f"/api/v1/conversations/{latest['id']}/notebook",
                                         json={"notebook_id": book["id"]}).status_code, 200)
        self.assertEqual(self.client.put(f"/api/v1/conversations/{latest['id']}/notebook",
                                         json={"notebook_id": "missing"}).status_code, 404)
        with storage.connect() as conn:
            conn.execute("""INSERT INTO enhanced_notes_runs (id, conversation_id, input_hash, transcript_revision,
                source_identity_hash, notes_revision, notes_hash, model_id, prompt_version, state, stage, result_json,
                edited_markdown, source_starts_json, created_at, updated_at)
                VALUES ('run', ?, 'h', 1, 'h', 0, 'h', 'm', 'p', 'succeeded', 'done', ?, ?, '{}', 'a', 'a')""",
                (latest["id"], json.dumps({"overview": [{"text": "Generated overview"}], "topics": [],
                                           "decisions": [{"text": "Ship the cutover"}], "action_items": []}),
                 "## Overview\n\n- Edited overview\n\n## Topics\n\n- Cutover timing\n\n## Decisions\n\n- Freeze writes\n\n## Action proposals\n\n- Rehearse rollback\n"))

        listing = self.client.get("/api/v1/notebooks").json()
        self.assertEqual(listing["palette"][0], "#495575")
        migration = next(item for item in listing["notebooks"] if item["id"] == book["id"])
        self.assertEqual([item["id"] for item in migration["conversations"]], [latest["id"], first["id"]])
        # The user's edited Summary wins over the generated result.
        self.assertEqual(migration["progress"], ["Freeze writes", "Rehearse rollback"])
        lines = {item["id"]: item["summary_line"] for item in self.client.get("/api/v1/conversations").json()}
        self.assertEqual(lines[latest["id"]], "Edited overview")
        self.assertIsNone(lines[first["id"]])
        # The rail previews the first topic, which is shorter than the overview.
        topics = {item["id"]: item["topic_line"] for item in self.client.get("/api/v1/conversations").json()}
        self.assertEqual(topics[latest["id"]], "Cutover timing")
        self.assertIsNone(topics[first["id"]])
        # A notebook created today leads one whose newest meeting is older.
        self.assertEqual([item["id"] for item in listing["notebooks"]], [second["id"], book["id"]])

        renamed = self.client.patch(f"/api/v1/notebooks/{book['id']}", json={"name": "Cutover", "color": "#45ada8"})
        self.assertEqual(renamed.json()["color"], "#45ADA8")
        self.assertEqual(self.client.delete(f"/api/v1/notebooks/{book['id']}").status_code, 200)
        remaining = {item["id"]: item["notebook_id"] for item in self.client.get("/api/v1/conversations").json()}
        self.assertIsNone(remaining[latest["id"]])
        self.assertEqual(self.client.get("/settings").status_code, 200)
        self.assertEqual(self.client.get(f"/notebook/{second['id']}").status_code, 200)

    def test_recording_session_carries_notebook(self):
        book = self.client.post("/api/v1/notebooks", json={"name": "Standups"}).json()
        self.assertEqual(self.client.post("/api/v1/recordings", json={"title": "Daily", "media_type": "audio/webm",
                                                                      "notebook_id": "missing"}).status_code, 400)
        session = self.client.post("/api/v1/recordings", json={"title": "Daily", "media_type": "audio/webm",
                                                               "notebook_id": book["id"]}).json()
        with storage.connect() as conn:
            row = conn.execute("SELECT notebook_id FROM recording_sessions WHERE id = ?", (session["id"],)).fetchone()
        self.assertEqual(row["notebook_id"], book["id"])

    def test_profile_username_and_password_changes(self):
        self.assertEqual(self.client.get("/api/v1/account").json(),
                         {"username": "owner", "name": "", "email": "", "keep_debug_data": False})
        self.assertEqual(self.client.patch("/api/v1/account", json={"name": "A", "email": "nope"}).status_code, 400)
        updated = self.client.patch("/api/v1/account", json={"name": " Ada  Lovelace ", "email": "ada@example.com"})
        self.assertEqual(updated.json(), {"username": "owner", "name": "Ada Lovelace", "email": "ada@example.com",
                                          "keep_debug_data": False})
        self.assertEqual(self.client.post("/api/v1/account/username",
                                          json={"username": "ada", "password": "wrong password"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/account/username",
                                          json={"username": "ada", "password": self.password}).json()["username"], "ada")

        other = TestClient(app, follow_redirects=False)
        self.assertEqual(other.post("/api/v1/auth/login", json={"username": "ada", "password": self.password}).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/account/password", json={
            "current_password": "wrong password", "new_password": "a much longer passphrase"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/account/password", json={
            "current_password": self.password, "new_password": "a much longer passphrase"}).status_code, 200)
        # The current session stays signed in; every other session is signed out.
        self.assertEqual(self.client.get("/api/v1/account").status_code, 200)
        self.assertEqual(other.get("/api/v1/account").status_code, 401)
        other.close()
        self.assertEqual(self.client.post("/api/v1/auth/login", json={"username": "ada", "password": self.password}).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/auth/login",
                                          json={"username": "ada", "password": "a much longer passphrase"}).status_code, 200)

    def test_delete_account_erases_all_local_data(self):
        self.client.post("/api/v1/notebooks", json={"name": "Project"})
        conversation = self._import("talk.mp3", 6)
        self.client.post("/api/v1/vocabulary", json={"term": "CubeSat", "kind": "other"})
        with calendar._connect() as conn:
            conn.execute("INSERT INTO connections (provider, client_id, access_token, refresh_token, expires_at) VALUES ('google', 'c', 'a', 'r', 0)")
        media = Path(self.temp.name, "media")
        self.assertTrue(any(media.iterdir()))
        self.assertEqual(self.client.request("DELETE", "/api/v1/account",
                                             json={"password": self.password, "confirm": "delete"}).status_code, 400)
        self.assertEqual(self.client.request("DELETE", "/api/v1/account",
                                             json={"password": "wrong password", "confirm": "DELETE"}).status_code, 403)
        deleted = self.client.request("DELETE", "/api/v1/account", json={"password": self.password, "confirm": "DELETE"})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["conversations"], 1)
        self.assertFalse(auth.has_account())
        self.assertFalse(any(media.iterdir()))
        with storage.connect() as conn:
            for table in ("conversations", "notebooks", "vocabulary", "persons"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0], len(storage.INITIAL_CATEGORIES))
        self.assertEqual(self.client.get("/api/v1/conversations").status_code, 401)
        self.assertTrue(self.client.get("/api/v1/auth/status").json()["registration_open"])
        self.assertIsNone(storage.get_conversation(conversation["id"], include_trashed=True))
        self.assertFalse(any(item["connected"] for item in calendar.connections()))


if __name__ == "__main__":
    unittest.main()
