"""An account that opts in keeps its intake calls, answer audio and Recently Deleted."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from stellis import debug_data, intake, library, pages
from stellis.server import app


class DebugDataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name, "STELLIS_AUTH_MODE": "required",
                                           "STELLIS_ACCOUNT_ID": "1"})
        self.env.start()
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def register(self, keep):
        response = self.client.post("/api/v1/auth/register", json={
            "username": "denver", "password": "a long enough password", "keep_debug_data": keep})
        self.assertEqual(response.status_code, 200, response.text)

    def call_with_one_answer(self):
        intake.opening()
        intake.add_person_turn("I want to keep the weight off.")

    def test_sign_up_choice_shows_in_settings_and_can_change(self):
        self.register(True)
        self.assertTrue(self.client.get("/api/v1/account").json()["keep_debug_data"])
        changed = self.client.put("/api/v1/account/debug-data", json={"keep": False})
        self.assertFalse(changed.json()["keep_debug_data"])
        self.assertFalse(debug_data.enabled())

    def test_an_opted_in_account_keeps_calls_audio_and_deleted_items(self):
        self.register(True)
        self.call_with_one_answer()
        intake.reset()
        debug_data.save_answer_audio(np.zeros(1600, dtype=np.float32), 16000, "keep the weight off")
        page = pages.create("Old note")
        pages.move_to_trash(page["id"])
        later = datetime.now(timezone.utc) + timedelta(days=library.RETENTION_DAYS + 1)
        self.assertEqual(library.delete_expired(now=later)["deleted"], [])
        self.assertEqual(len(pages.trashed()), 1)
        calls = (debug_data.directory() / "intake-calls.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(calls[0])["call"]["turns"][1]["text"], "I want to keep the weight off.")
        download = self.client.get("/api/v1/account/debug-data")
        names = zipfile.ZipFile(io.BytesIO(download.content)).namelist()
        self.assertIn("intake-calls.jsonl", names)
        self.assertIn("intake-answers.jsonl", names)
        self.assertTrue(any(name.startswith("intake-audio/") and name.endswith(".wav") for name in names))

    def test_an_account_that_did_not_opt_in_saves_nothing(self):
        self.register(False)
        self.call_with_one_answer()
        intake.reset()
        debug_data.save_answer_audio(np.zeros(1600, dtype=np.float32), 16000, "keep the weight off")
        self.assertFalse(debug_data.directory().exists())
        page = pages.create("Old note")
        pages.move_to_trash(page["id"])
        later = datetime.now(timezone.utc) + timedelta(days=library.RETENTION_DAYS + 1)
        self.assertEqual(library.delete_expired(now=later)["deleted"], [page["id"]])
        self.assertEqual(pages.trashed(), [])

    def test_deleting_the_account_erases_debug_data(self):
        self.register(True)
        self.call_with_one_answer()
        intake.reset()
        library.erase_all()
        self.assertFalse(debug_data.directory().exists())


if __name__ == "__main__":
    unittest.main()
