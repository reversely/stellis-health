"""The user's Nebius API key is entered in the app and stays on the device (#223)."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import ai
from stellis.server import app

KEY = "nb-test-key-0123456789abcdef"


class NebiusKeyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name}, clear=False)
        self.env.start()
        os.environ.pop("NEBIUS_API_KEY", None)
        self.no_dotenv = patch.object(ai.Path, "is_file", return_value=False)
        self.no_dotenv.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.no_dotenv.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_save_replace_and_remove_without_ever_returning_the_key(self) -> None:
        self.assertEqual(self.client.get("/api/v1/settings/nebius-key").json(), {"configured": False, "source": None})
        saved = self.client.put("/api/v1/settings/nebius-key", json={"key": f"  {KEY}  "})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json(), {"configured": True, "source": "saved"})
        self.assertNotIn(KEY, saved.text)
        self.assertNotIn(KEY, self.client.get("/api/v1/settings/nebius-key").text)
        self.assertEqual(ai._key(), KEY)
        mode = stat.S_IMODE(os.stat(ai._key_file()).st_mode)
        self.assertEqual(mode, 0o600)
        self.client.put("/api/v1/settings/nebius-key", json={"key": KEY + "x"})
        self.assertEqual(ai._key(), KEY + "x")
        self.assertEqual(self.client.delete("/api/v1/settings/nebius-key").json(), {"configured": False, "source": None})
        self.assertIsNone(ai._key())

    def test_capabilities_report_whether_ai_is_configured(self) -> None:
        self.assertFalse(self.client.get("/api/v1/capabilities").json()["ai"]["configured"])
        self.client.put("/api/v1/settings/nebius-key", json={"key": KEY})
        self.assertTrue(self.client.get("/api/v1/capabilities").json()["ai"]["configured"])
        self.client.delete("/api/v1/settings/nebius-key")
        self.assertFalse(self.client.get("/api/v1/capabilities").json()["ai"]["configured"])

    def test_saved_key_wins_over_the_environment_and_removal_falls_back(self) -> None:
        with patch.dict(os.environ, {"NEBIUS_API_KEY": "env-key-abcdefghijklmnop"}):
            self.assertEqual(self.client.get("/api/v1/settings/nebius-key").json()["source"], "environment")
            self.client.put("/api/v1/settings/nebius-key", json={"key": KEY})
            self.assertEqual(ai._key(), KEY)
            self.client.delete("/api/v1/settings/nebius-key")
            self.assertEqual(ai._key(), "env-key-abcdefghijklmnop")

    def test_owner_rotation_updates_managed_inference(self) -> None:
        with patch.dict(os.environ, {"STELLIS_ACCOUNT_ID": "1",
                                          "STELLIS_NEMOCLAW_SANDBOX": "test-sandbox",
                                          "NEBIUS_API_KEY": "nb-original-owner-key"}), \
             patch.object(ai, "_sync_managed_key") as sync:
            self.assertEqual(self.client.put("/api/v1/settings/nebius-key",
                                             json={"key": KEY}).status_code, 200)
            sync.assert_called_once_with(KEY)
            self.assertEqual(ai._key(), KEY)
            self.assertEqual(self.client.delete("/api/v1/settings/nebius-key").status_code, 200)
            self.assertEqual(sync.call_args.args, ("nb-original-owner-key",))
            self.assertEqual(ai._key(), "nb-original-owner-key")

    def test_failed_managed_rotation_does_not_save_new_key(self) -> None:
        with patch.dict(os.environ, {"STELLIS_ACCOUNT_ID": "1",
                                          "STELLIS_NEMOCLAW_SANDBOX": "test-sandbox",
                                          "NEBIUS_API_KEY": "nb-original-owner-key"}), \
             patch.object(ai, "_sync_managed_key",
                          side_effect=RuntimeError("Could not update managed Nebius access")):
            response = self.client.put("/api/v1/settings/nebius-key", json={"key": KEY})
            self.assertEqual(response.status_code, 503)
            self.assertFalse(ai._key_file().exists())

    def test_managed_inference_update_passes_key_only_in_child_environment(self) -> None:
        with patch.dict(os.environ, {"STELLIS_ACCOUNT_ID": "1",
                                          "STELLIS_NEMOCLAW_SANDBOX": "test-sandbox"}), \
             patch.object(ai.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            ai._sync_managed_key(KEY)
        argv = run.call_args.args[0]
        self.assertEqual(argv[-2:], ["--credential", "COMPATIBLE_API_KEY"])
        self.assertNotIn(KEY, argv)
        self.assertEqual(run.call_args.kwargs["env"]["COMPATIBLE_API_KEY"], KEY)

    def test_malformed_keys_are_rejected(self) -> None:
        for value in ("short", "has a space in the middle of it", "x" * 600):
            response = self.client.put("/api/v1/settings/nebius-key", json={"key": value})
            self.assertIn(response.status_code, (400, 422), value)
        self.assertFalse(ai._key_file().exists())


if __name__ == "__main__":
    unittest.main()
