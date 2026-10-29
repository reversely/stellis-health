"""Locked workspaces: unreadable at rest, refused when changed, chained version to version."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stellis import vault

# Tests use a light scrypt cost; the format records the parameters each key was wrapped with.
FAST = {"n": 1 << 10, "r": 8, "p": 1}


class VaultTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        self.root.mkdir()
        self.workspace = Path(self.temp.name) / "ram" / "7"
        self.workspace.mkdir(parents=True)
        (self.workspace / "media").mkdir()
        (self.workspace / "media" / "visit.webm").write_bytes(b"audio" * 300_000)
        with sqlite3.connect(self.workspace / "stellis.sqlite3") as conn:
            conn.execute("CREATE TABLE notes (body TEXT)")
            conn.execute("INSERT INTO notes VALUES ('Ferritin 11, recheck in June')")
        self.params = patch.object(vault, "SCRYPT", FAST)
        self.params.start()

    def tearDown(self) -> None:
        self.params.stop()
        self.temp.cleanup()

    def _saved(self) -> bytes:
        key = vault.create(self.root, 7, "correct horse battery")
        vault.save(self.root, 7, key, self.workspace)
        return key

    def test_a_saved_workspace_is_unreadable_and_restores_exactly(self) -> None:
        key = self._saved()
        stored = b"".join(path.read_bytes() for path in vault.directory(self.root, 7).iterdir())
        self.assertNotIn(b"Ferritin", stored)
        self.assertNotIn(b"audio", stored)
        self.assertNotIn(key, stored)
        out = Path(self.temp.name) / "restored"
        vault.restore(self.root, 7, vault.unlock_key(self.root, 7, "correct horse battery"), out)
        self.assertEqual((out / "media" / "visit.webm").read_bytes(), b"audio" * 300_000)
        with sqlite3.connect(out / "stellis.sqlite3") as conn:
            self.assertEqual(conn.execute("SELECT body FROM notes").fetchone()[0], "Ferritin 11, recheck in June")

    def test_the_wrong_password_does_not_unlock(self) -> None:
        self._saved()
        with self.assertRaisesRegex(vault.VaultError, "password"):
            vault.unlock_key(self.root, 7, "wrong horse battery")

    def test_any_changed_byte_truncation_or_swap_is_refused(self) -> None:
        key = self._saved()
        path = vault.directory(self.root, 7) / "workspace.enc"
        original = path.read_bytes()
        header_end = original.index(b"\n") + 1
        for name, changed in {
            "flipped byte": original[:-10] + bytes([original[-10] ^ 1]) + original[-9:],
            "truncated": original[:header_end + 4 + vault.CHUNK + 16],
            "header edited": original.replace(b'"version": 1', b'"version": 9', 1),
        }.items():
            with self.subTest(name):
                path.write_bytes(changed)
                with self.assertRaises(vault.VaultError):
                    vault.restore(self.root, 7, key, Path(self.temp.name) / name)
        path.write_bytes(original)
        # Another account's file under this account's name is refused by its header and key.
        other_root = Path(self.temp.name) / "other"
        other_key = vault.create(other_root, 8, "another password!")
        vault.save(other_root, 8, other_key, self.workspace)
        path.write_bytes((vault.directory(other_root, 8) / "workspace.enc").read_bytes())
        with self.assertRaises(vault.VaultError):
            vault.restore(self.root, 7, key, Path(self.temp.name) / "swapped")

    def test_versions_chain_to_the_previous_file(self) -> None:
        key = self._saved()
        first = vault.head(self.root, 7)
        second = vault.save(self.root, 7, key, self.workspace)
        self.assertEqual(second["version"], 2)
        header = json.loads((vault.directory(self.root, 7) / "workspace.enc").open("rb").readline())
        self.assertEqual(header["previous"], first["hash"])
        self.assertTrue((vault.directory(self.root, 7) / "workspace.prev.enc").is_file())

    def test_a_password_change_keeps_the_data_key(self) -> None:
        key = self._saved()
        vault.rewrap(self.root, 7, key, "a new long passphrase")
        self.assertEqual(vault.unlock_key(self.root, 7, "a new long passphrase"), key)
        with self.assertRaises(vault.VaultError):
            vault.unlock_key(self.root, 7, "correct horse battery")

    def test_a_recovery_code_unlocks_the_same_key_and_survives_a_password_change(self) -> None:
        key = self._saved()
        code = vault.add_recovery(self.root, 7, key)
        self.assertRegex(code, r"^([A-Z2-7]{4}-){7}[A-Z2-7]{4}$")
        # Spacing and case do not matter when the code is typed back.
        self.assertEqual(vault.recover_key(self.root, 7, code.replace("-", " ").lower()), key)
        vault.rewrap(self.root, 7, key, "a new long passphrase")
        self.assertEqual(vault.recover_key(self.root, 7, code), key)
        with self.assertRaises(vault.VaultError):
            vault.recover_key(self.root, 7, vault.new_recovery_code())
        self.assertNotIn(code.replace("-", "").encode(), (vault.directory(self.root, 7) / "key.json").read_bytes())

    def test_destroying_the_key_leaves_nothing(self) -> None:
        self._saved()
        vault.destroy(self.root, 7)
        self.assertFalse(vault.exists(self.root, 7))
        self.assertFalse(vault.directory(self.root, 7).exists())

    def test_existing_plain_files_move_into_the_workspace(self) -> None:
        for name in ("auth.sqlite3", "oauth_pending.sqlite3", "stellis.sqlite3", "models", "accounts"):
            target = self.root / name
            target.mkdir() if "." not in name else target.write_text("x")
        account = self.root / "accounts" / "4"
        account.mkdir()
        (account / "stellis.sqlite3").write_text("four")
        workspace_one, workspace_four = Path(self.temp.name) / "w1", Path(self.temp.name) / "w4"
        self.assertTrue(vault.adopt(self.root, 1, workspace_one))
        # Account 1's files sit at the root beside the shared deployment files, which stay.
        self.assertEqual(sorted(p.name for p in workspace_one.iterdir()), ["stellis.sqlite3"])
        for shared in ("auth.sqlite3", "oauth_pending.sqlite3", "models"):
            self.assertTrue((self.root / shared).exists(), shared)
        self.assertTrue(vault.adopt(self.root, 4, workspace_four))
        self.assertEqual((workspace_four / "stellis.sqlite3").read_text(), "four")
        self.assertFalse(account.exists())

    def test_a_database_in_use_is_saved_consistently(self) -> None:
        live = sqlite3.connect(self.workspace / "stellis.sqlite3")
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("INSERT INTO notes VALUES ('written while open')")
        live.commit()
        key = self._saved()
        live.close()
        out = Path(self.temp.name) / "live"
        vault.restore(self.root, 7, key, out)
        with sqlite3.connect(out / "stellis.sqlite3") as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM notes").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
