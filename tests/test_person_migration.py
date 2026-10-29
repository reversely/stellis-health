"""Legacy duplicate Person records collapse without losing passage links."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stellis import storage


class PersonMigrationTest(unittest.TestCase):
    def test_legacy_duplicate_names_keep_active_identity_and_all_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STELLIS_DATA_DIR": directory}):
            database = Path(directory) / "stellis.sqlite3"
            with sqlite3.connect(database) as conn:
                conn.execute("""CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL UNIQUE, audio_filename TEXT NOT NULL,
                    audio_media_type TEXT NOT NULL, duration_ms INTEGER NOT NULL, created_at TEXT NOT NULL)""")
                conn.execute("""CREATE TABLE persons (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, hidden_at TEXT)""")
                conn.execute("""CREATE TABLE person_links (conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    segment_id TEXT NOT NULL, person_id TEXT NOT NULL REFERENCES persons(id),
                    start_ms INTEGER NOT NULL, confirmed_at TEXT NOT NULL,
                    PRIMARY KEY(conversation_id, segment_id))""")
                conn.execute("""INSERT INTO conversations VALUES
                    ('conversation', 'Test', 'hash', 'test.wav', 'audio/wav', 1000, '2020-01-01')""")
                conn.executemany("INSERT INTO persons VALUES (?, ?, ?, ?, ?)", [
                    ("old-hidden", " Devon   Hart ", "2020-01-01", "2020-01-01", "2020-01-02"),
                    ("active", "devon hart", "2021-01-01", "2021-01-01", None),
                    ("other", "Riley", "2020-01-01", "2020-01-01", None),
                ])
                conn.executemany("INSERT INTO person_links VALUES ('conversation', ?, ?, 0, '2020-01-01')", [
                    ("first", "old-hidden"), ("second", "active"), ("third", "other"),
                ])
            for _ in range(2):
                with storage.connect() as conn:
                    people = conn.execute("SELECT id, name_key FROM persons ORDER BY id").fetchall()
                    links = conn.execute("SELECT segment_id, person_id FROM person_links ORDER BY segment_id").fetchall()
                    self.assertEqual({tuple(row) for row in people},
                                     {("active", "devon hart"), ("other", "riley")})
                    self.assertEqual({tuple(row) for row in links},
                                     {("first", "active"), ("second", "active"), ("third", "other")})
                    self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
