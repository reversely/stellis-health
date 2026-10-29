"""The gateway unlocks a workspace at sign-in and keeps only encrypted records on disk."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from stellis import vault

FAST = {"n": 1 << 10, "r": 8, "p": 1}
PASSWORD = "a long test passphrase"


class LockedWorkspaceGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        self.ram = Path(self.temp.name) / "ram"
        self.env = patch.dict(os.environ, {
            "STELLIS_DATA_DIR": str(self.root), "STELLIS_AUTH_MODE": "required",
            "STELLIS_LOCKED_WORKSPACES": "1", "STELLIS_WORKSPACE_DIR": str(self.ram)})
        self.env.start()
        self.params = patch.object(vault, "SCRYPT", FAST)
        self.params.start()
        from stellis.account_gateway import AccountGateway
        self.gateway = AccountGateway()
        # The account process is out of scope here: a stand-in answers proxied requests.
        self.proxied = []

        async def proxy(request, account_id, body=None):
            self.proxied.append((request.url.path, account_id))
            return JSONResponse({"ok": True})

        self.proxy = patch.object(self.gateway, "_proxy", side_effect=proxy)
        self.proxy.start()
        self.stop = patch.object(self.gateway, "_stop", new=AsyncMock())
        self.stop.start()
        self.client = TestClient(self.gateway)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        for item in (self.stop, self.proxy, self.params, self.env):
            item.stop()
        self.temp.cleanup()

    def _register(self, name: str = "maya") -> int:
        response = self.client.post("/api/v1/auth/register", json={"username": name, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        from stellis import auth
        return auth.account_id_for_username(name)

    def test_sign_up_creates_an_encrypted_workspace_and_sign_out_locks_it(self) -> None:
        account = self._register()
        self.assertTrue(vault.exists(self.root, account))
        workspace = self.ram / str(account)
        self.assertTrue(workspace.is_dir())
        self.assertEqual(self.client.get("/api/v1/notebooks").status_code, 200)
        with sqlite3.connect(workspace / "stellis.sqlite3") as conn:
            conn.execute("CREATE TABLE notes (body TEXT)")
            conn.execute("INSERT INTO notes VALUES ('Ferritin 11')")
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 200)
        self.assertFalse(workspace.exists())
        stored = b"".join(p.read_bytes() for p in self.root.rglob("*") if p.is_file())
        self.assertNotIn(b"Ferritin 11", stored)
        # Signed out, requests are refused rather than served from a missing workspace.
        self.assertEqual(self.client.get("/api/v1/notebooks").status_code, 401)
        signed_in = self.client.post("/api/v1/auth/login", json={"username": "maya", "password": PASSWORD})
        self.assertEqual(signed_in.status_code, 200)
        self.assertEqual(signed_in.headers["x-stellis-workspace-version"], "2")
        with sqlite3.connect(workspace / "stellis.sqlite3") as conn:
            self.assertEqual(conn.execute("SELECT body FROM notes").fetchone()[0], "Ferritin 11")

    def test_sign_up_shows_a_recovery_key_that_resets_a_forgotten_password(self) -> None:
        response = self.client.post("/api/v1/auth/register", json={"username": "ana", "password": PASSWORD})
        code = response.json()["recovery_code"]
        self.client.post("/api/v1/auth/logout")
        again = self.client.post("/api/v1/auth/login", json={"username": "ana", "password": PASSWORD})
        self.assertNotIn("recovery_code", again.json())
        self.client.post("/api/v1/auth/logout")
        wrong = self.client.post("/api/v1/auth/recover", json={
            "username": "ana", "recovery_key": "AAAA-BBBB", "new_password": "a brand new passphrase"})
        self.assertEqual(wrong.status_code, 401)
        recovered = self.client.post("/api/v1/auth/recover", json={
            "username": "ana", "recovery_key": code, "new_password": "a brand new passphrase"})
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(self.client.get("/api/v1/notebooks").status_code, 200)
        self.client.post("/api/v1/auth/logout")
        self.assertEqual(self.client.post("/api/v1/auth/login", json={"username": "ana", "password": PASSWORD}).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/auth/login",
                                          json={"username": "ana", "password": "a brand new passphrase"}).status_code, 200)

    def test_a_restart_needs_a_new_sign_in_to_unlock(self) -> None:
        account = self._register()
        self.gateway.workspaces.keys.pop(account)
        response = self.client.get("/api/v1/notebooks")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Sign in again to unlock your records")

    def test_a_password_change_rewraps_the_same_key(self) -> None:
        account = self._register()
        key = self.gateway.workspaces.keys[account]
        changed = self.client.post("/api/v1/account/password",
                                   json={"current_password": PASSWORD, "new_password": "another long passphrase"})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(vault.unlock_key(self.root, account, "another long passphrase"), key)

    def test_deleting_the_account_destroys_its_key_and_versions(self) -> None:
        account = self._register()
        with patch.object(self.gateway, "_proxy", new=AsyncMock(side_effect=self._real_delete)):
            self.client.request("DELETE", "/api/v1/account", json={"password": PASSWORD, "confirm": "DELETE"})
        self.assertFalse(vault.exists(self.root, account))
        self.assertFalse((self.ram / str(account)).exists())

    async def _real_delete(self, request, account_id, body=None):
        # The gateway destroys the vault after the account process confirms deletion.
        await self.gateway.workspaces.destroy(account_id)
        return JSONResponse({"deleted": True})

    def test_existing_plain_records_are_adopted_and_removed_from_disk(self) -> None:
        from stellis import auth
        auth.register("lee", PASSWORD)
        account = auth.account_id_for_username("lee")
        plain = self.root / ("accounts/%d" % account) if account != 1 else self.root
        plain.mkdir(parents=True, exist_ok=True)
        (plain / "visit-notes.txt").write_text("Follitropin 150 IU")
        self.client.post("/api/v1/auth/login", json={"username": "lee", "password": PASSWORD})
        self.assertFalse((plain / "visit-notes.txt").exists())
        self.assertEqual((self.ram / str(account) / "visit-notes.txt").read_text(), "Follitropin 150 IU")
        stored = b"".join(p.read_bytes() for p in self.root.rglob("*") if p.is_file())
        self.assertNotIn(b"Follitropin", stored)


if __name__ == "__main__":
    unittest.main()


class PrivacyPageTest(unittest.TestCase):
    def test_the_policy_is_public_and_states_retention_from_the_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                "STELLIS_DATA_DIR": temp, "STELLIS_AUTH_MODE": "required"}):
            from stellis.account_gateway import AccountGateway
            with TestClient(AccountGateway()) as client:
                page = client.get("/privacy")
                self.assertEqual(page.status_code, 200)
                self.assertIn('data-zdr="off"', page.text)
                with patch.dict(os.environ, {"STELLIS_TOKEN_FACTORY_ZDR": "confirmed"}):
                    self.assertIn('data-zdr="on"', client.get("/privacy").text)
