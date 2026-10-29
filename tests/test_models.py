"""Speech models download on first run, checked by SHA-256, and waiting recordings then transcribe."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stellis import models, processing, storage


class ModelDownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.original_state = dict(models._STATE)
        self.payload = b"model bytes"
        digest = hashlib.sha256(self.payload).hexdigest()
        self.catalog = patch.object(models, "MODELS", (("tiny.bin", "https://example.invalid/tiny.bin", digest, None),))
        self.catalog.start()
        # The live preview pair is covered by its own tests below; here it is already installed.
        self.live = patch.object(models.live_asr_models, "present", return_value=True)
        self.live.start()

    def tearDown(self) -> None:
        self.live.stop()
        self.catalog.stop()
        self.env.stop()
        self.temp.cleanup()
        models._STATE.clear()
        models._STATE.update(self.original_state)

    def _fetch(self, payload: bytes):
        def fetch(url: str, target: Path, index: int, count: int) -> None:
            target.write_bytes(payload)
        return patch.object(models, "_fetch", side_effect=fetch)

    def test_a_verified_download_is_installed_owner_only(self) -> None:
        self.assertEqual(models.missing(), ["tiny.bin"])
        with self._fetch(self.payload):
            models.install()
        installed = models.directory() / "tiny.bin"
        self.assertEqual(installed.read_bytes(), self.payload)
        self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o600)
        self.assertEqual(models.status()["state"], "ready")
        self.assertEqual(models.missing(), [])

    def test_a_download_that_fails_its_checksum_is_not_installed(self) -> None:
        with self._fetch(b"tampered"):
            models.install()
        self.assertFalse((models.directory() / "tiny.bin").exists())
        state = models.status()
        self.assertEqual(state["state"], "failed")
        self.assertIn("checksum", state["error"])

    def test_recordings_imported_while_downloading_are_queued_afterwards(self) -> None:
        with storage.connect() as conn:
            conn.execute("INSERT INTO conversations (id, title, source_sha256, audio_filename, audio_media_type, "
                         "duration_ms, created_at) VALUES ('waiting', 'Waiting', 'hash', 'a.mp3', 'audio/mpeg', 1000, 'now')")
        with self._fetch(self.payload), patch.object(processing, "request_processing") as request:
            models.install()
        request.assert_called_once_with("waiting")

    def test_the_live_pair_installs_after_the_base_models_into_the_shared_folder(self) -> None:
        self.live.stop()
        calls = []

        def install(root: Path, report) -> None:
            calls.append(("live", root))
            report(models.live_asr_models.MODELS[0][0], 50, 100)
            self.assertEqual(models.status()["state"], "downloading")

        with self._fetch(self.payload), patch.object(processing, "request_processing"), \
                patch.object(models, "_queue_waiting_conversations", side_effect=lambda: calls.append(("queue",))), \
                patch.object(models.live_asr_models, "install", side_effect=install):
            self.assertEqual(len(models.missing()), 3)
            models.install()
        # Waiting recordings start transcribing before the larger live download begins.
        self.assertEqual(calls, [("queue",), ("live", models.directory() / "live-asr")])
        self.live.start()
        self.assertEqual(models.status()["state"], "ready")

    def test_live_folders_follow_the_configured_paths(self) -> None:
        with patch.dict(os.environ, {"STELLIS_ONLINE_ASR_MODEL_DIR": "/srv/online",
                                     "STELLIS_PARAKEET_MODEL_DIR": "/srv/offline"}):
            self.assertEqual(models.live_paths(), (Path("/srv/online"), Path("/srv/offline")))
        online, offline = models.live_paths()
        self.assertEqual(online.parent, models.directory() / "live-asr")
        self.assertEqual(offline.name, models.live_asr_models.MODELS[1][0])

    def test_an_unwritable_destination_reports_failure(self) -> None:
        blocker = Path(self.temp.name) / "blocker"
        blocker.write_bytes(b"file")
        with patch.object(models, "directory", return_value=blocker):
            models.install()
            self.assertEqual(models.status()["state"], "failed")

    def test_progress_tracks_bytes_when_the_server_omits_its_size(self) -> None:
        response = io.BytesIO(self.payload)
        response.headers = {}
        with patch.object(models.urllib.request, "urlopen", return_value=response):
            models._fetch("https://example.invalid/model", Path(self.temp.name) / "download", 0, 1)
        state = models.status()
        self.assertEqual(state["downloaded_bytes"], len(self.payload))
        self.assertIsNone(state["total_bytes"])
        self.assertIsNone(state["progress"])

    def test_progress_advances_for_a_known_size(self) -> None:
        response = io.BytesIO(self.payload)
        response.headers = {"Content-Length": str(len(self.payload))}
        with patch.object(models.urllib.request, "urlopen", return_value=response):
            models._fetch("https://example.invalid/model", Path(self.temp.name) / "download", 0, 2)
        state = models.status()
        self.assertEqual(state["downloaded_bytes"], len(self.payload))
        self.assertEqual(state["total_bytes"], len(self.payload))
        self.assertEqual(state["progress"], 0.5)

    def test_hosted_status_uses_the_gateway_installer_and_checks_the_session(self) -> None:
        from fastapi.testclient import TestClient
        from stellis import auth
        from stellis.account_gateway import AccountGateway

        gateway = AccountGateway()
        models._STATE.update(state="downloading", progress=0.4, downloaded_bytes=400,
                             total_bytes=1000, stage="receiving")
        with patch.dict(os.environ, {"STELLIS_AUTH_MODE": "required"}), \
                patch.object(auth, "current_account_id", return_value=None), \
                TestClient(gateway) as client:
            self.assertEqual(client.get("/api/v1/setup").status_code, 401)
            self.assertEqual(client.post("/api/v1/setup").status_code, 401)
            with patch.object(auth, "current_account_id", return_value=2), \
                    patch.object(gateway, "_proxy", side_effect=AssertionError("Account proxy used")):
                state = client.get("/api/v1/setup").json()
                self.assertEqual(state["progress"], 0.4)
                self.assertEqual(state["downloaded_bytes"], 400)
                with patch.object(models, "ensure_in_background") as start:
                    self.assertEqual(client.post("/api/v1/setup").status_code, 200)
                    start.assert_called_once_with()
                with patch.object(models, "ensure_in_background") as start:
                    self.assertEqual(client.post("/api/v1/setup", headers={
                        "Origin": "https://another.example"}).status_code, 403)
                    start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
