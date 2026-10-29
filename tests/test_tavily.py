"""Account-local Tavily credentials, used only by Stellis's own research client."""

import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from stellis import tavily


class TavilyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name,
                                        "STELLIS_ACCOUNT_ID": "2"}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_saved_key_is_private_and_removable(self):
        tavily.save_key("tvly-private-test-credential")
        self.assertEqual(tavily.available_key(), "tvly-private-test-credential")
        self.assertEqual(stat.S_IMODE(tavily._file().stat().st_mode), 0o600)
        self.assertEqual(tavily.key_status(), {"configured": True, "source": "saved"})
        tavily.remove_key()
        self.assertFalse(tavily.key_status()["configured"])

    def test_owner_key_falls_back_to_the_host_key(self):
        with patch.dict(os.environ, {"STELLIS_ACCOUNT_ID": "1"}), \
             patch.object(tavily, "_host_key", return_value="tvly-original-host-key"):
            tavily.save_key("tvly-new-owner-key")
            self.assertEqual(tavily.available_key(), "tvly-new-owner-key")
            tavily.remove_key()
            self.assertEqual(tavily.available_key(), "tvly-original-host-key")
            self.assertEqual(tavily.key_status()["source"], "environment")

    def test_saving_a_key_never_gives_an_agent_a_search_tool(self):
        with patch.dict(os.environ, {"STELLIS_ACCOUNT_ID": "1",
                                     "STELLIS_NEMOCLAW_SANDBOX": "test-sandbox"}), \
             patch("subprocess.run") as run:
            tavily.save_key("tvly-owner-test-key")
            tavily.remove_key()
        run.assert_not_called()
