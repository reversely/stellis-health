"""Development identity store for the separate sync origin; no content records."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

COOKIE = "stellis_sync_session"
SESSION_DAYS = 7


def root() -> Path:
    configured = os.environ.get("STELLIS_SYNC_DATA_DIR")
    path = Path(configured).expanduser() if configured else Path.home() / "Library/Application Support/Stellis Sync Dev"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = root() / "identity.sqlite3"
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, salt BLOB NOT NULL,
        verifier BLOB NOT NULL, created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token_hash BLOB PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
        expires_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS devices (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
        name TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS vaults (
        user_id TEXT PRIMARY KEY REFERENCES users(id), salt TEXT NOT NULL,
        check_iv TEXT NOT NULL, check_ciphertext TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS ciphertext_entries (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id),
        id TEXT NOT NULL, item_id TEXT NOT NULL, iv TEXT NOT NULL,
        ciphertext TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(user_id, id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS media_chunks (
        user_id TEXT NOT NULL REFERENCES users(id), media_id TEXT NOT NULL,
        chunk_index INTEGER NOT NULL, iv TEXT NOT NULL, ciphertext TEXT NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY (user_id, media_id, chunk_index)
    )""")
    conn.commit()
    path.chmod(0o600)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _verifier(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def register(username: str, password: str) -> str:
    username = username.strip()
    if not 3 <= len(username) <= 80 or not 12 <= len(password) <= 1024:
        raise ValueError("Use a username of 3 to 80 characters and a password of at least 12 characters")
    user_id = str(uuid.uuid4())
    with connect() as conn:
        try:
            conn.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                         (user_id, username, salt := secrets.token_bytes(32), _verifier(password, salt), _now()))
        except sqlite3.IntegrityError as exc:
            raise RuntimeError("Username unavailable") from exc
    return user_id


def login(username: str, password: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT id, salt, verifier FROM users WHERE username = ?", (username,)).fetchone()
    # Keep the response and expensive verifier work the same for unknown users.
    salt = row["salt"] if row else b"\0" * 32
    verifier = row["verifier"] if row else b"\0" * 32
    if not hmac.compare_digest(verifier, _verifier(password, salt)) or row is None:
        return None
    return str(row["id"])


def new_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),))
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                     (_digest(token), user_id, (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()))
    return token


def current_user(token: str | None) -> dict[str, str] | None:
    if not token:
        return None
    try:
        digest = _digest(token)
    except UnicodeEncodeError:
        return None
    with connect() as conn:
        row = conn.execute("""SELECT users.id, users.username FROM users JOIN sessions
            ON users.id = sessions.user_id WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
            (digest, _now())).fetchone()
    return {"id": row["id"], "username": row["username"]} if row else None


def revoke(token: str | None) -> None:
    if token:
        try:
            digest = _digest(token)
        except UnicodeEncodeError:
            return
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))


def revoke_all(user_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def devices(user_id: str) -> list[dict[str, str]]:
    with connect() as conn:
        rows = conn.execute("SELECT id, name, created_at FROM devices WHERE user_id = ? AND revoked_at IS NULL ORDER BY created_at",
                            (user_id,)).fetchall()
    return [dict(row) for row in rows]


def add_device(user_id: str, name: str) -> dict[str, str]:
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("Enter a device name")
    record = {"id": str(uuid.uuid4()), "name": name, "created_at": _now()}
    with connect() as conn:
        conn.execute("INSERT INTO devices VALUES (?, ?, ?, ?, NULL)",
                     (record["id"], user_id, name, record["created_at"]))
    return record


def revoke_device(user_id: str, device_id: str) -> bool:
    with connect() as conn:
        result = conn.execute("UPDATE devices SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                              (_now(), device_id, user_id))
    return result.rowcount == 1


def vault(user_id: str) -> dict[str, str] | None:
    with connect() as conn:
        row = conn.execute("SELECT salt, check_iv, check_ciphertext FROM vaults WHERE user_id = ?",
                           (user_id,)).fetchone()
    return dict(row) if row else None


def create_vault(user_id: str, salt: str, check_iv: str, check_ciphertext: str) -> dict[str, str]:
    with connect() as conn:
        try:
            conn.execute("INSERT INTO vaults VALUES (?, ?, ?, ?)",
                         (user_id, salt, check_iv, check_ciphertext))
        except sqlite3.IntegrityError as exc:
            raise RuntimeError("Vault already exists") from exc
    return {"salt": salt, "check_iv": check_iv, "check_ciphertext": check_ciphertext}


def append_entry(user_id: str, record: dict[str, str]) -> dict[str, object]:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("""SELECT seq, item_id, iv, ciphertext, created_at FROM ciphertext_entries
            WHERE user_id = ? AND id = ?""", (user_id, record["id"])).fetchone()
        if existing:
            if any(existing[key] != record[key] for key in ("item_id", "iv", "ciphertext")):
                raise RuntimeError("Entry ID already used")
            return {"seq": existing["seq"], "id": record["id"], "item_id": existing["item_id"],
                    "iv": existing["iv"], "ciphertext": existing["ciphertext"], "created_at": existing["created_at"]}
        stamp = _now()
        cursor = conn.execute("""INSERT INTO ciphertext_entries
            (user_id, id, item_id, iv, ciphertext, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, record["id"], record["item_id"], record["iv"], record["ciphertext"], stamp))
    return {**record, "seq": cursor.lastrowid, "created_at": stamp}


def list_entries(user_id: str, after: int, limit: int) -> list[dict[str, object]]:
    with connect() as conn:
        rows = conn.execute("""SELECT seq, id, item_id, iv, ciphertext, created_at FROM ciphertext_entries
            WHERE user_id = ? AND seq > ? ORDER BY seq LIMIT ?""", (user_id, after, limit)).fetchall()
    return [dict(row) for row in rows]


def append_media_chunk(user_id: str, media_id: str, index: int, iv: str, ciphertext: str) -> None:
    """Store an opaque encrypted audio chunk with idempotent retries."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("""SELECT iv, ciphertext FROM media_chunks
            WHERE user_id = ? AND media_id = ? AND chunk_index = ?""", (user_id, media_id, index)).fetchone()
        if existing:
            if existing["iv"] != iv or existing["ciphertext"] != ciphertext:
                raise RuntimeError("Media chunk already used")
            return
        used = conn.execute("SELECT COALESCE(SUM(LENGTH(ciphertext)), 0) FROM media_chunks WHERE user_id = ?",
                            (user_id,)).fetchone()[0]
        if used + len(ciphertext) > 192 * 1024 * 1024:
            raise OverflowError("Encrypted media storage is full")
        conn.execute("""INSERT INTO media_chunks VALUES (?, ?, ?, ?, ?, ?)""",
                     (user_id, media_id, index, iv, ciphertext, _now()))


def media_chunk(user_id: str, media_id: str, index: int) -> dict[str, str] | None:
    with connect() as conn:
        row = conn.execute("""SELECT iv, ciphertext FROM media_chunks
            WHERE user_id = ? AND media_id = ? AND chunk_index = ?""", (user_id, media_id, index)).fetchone()
    return dict(row) if row else None
