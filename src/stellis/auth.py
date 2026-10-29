"""Account registry and sessions for private local workspaces."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator
from pathlib import Path

from stellis import storage

SESSION_DAYS = 7
COOKIE_NAME = "stellis_session"


def enabled() -> bool:
    return os.environ.get("STELLIS_AUTH_MODE") == "required"


def secure_cookies() -> bool:
    return os.environ.get("STELLIS_SECURE_COOKIES") == "1"


def invite_required() -> bool:
    """Sign-up is open unless the deployment sets STELLIS_INVITE_CODE."""
    return bool(os.environ.get("STELLIS_INVITE_CODE", "").strip())


def registration_open() -> bool:
    return enabled()


def invite_accepted(code: str | None) -> bool:
    if not invite_required():
        return True
    expected = os.environ["STELLIS_INVITE_CODE"].strip()
    return secrets.compare_digest((code or "").strip().encode(), expected.encode())


def dev_enabled() -> bool:
    return os.environ.get("STELLIS_DEV_ACCOUNT") == "1" and bool(os.environ.get("STELLIS_DEV_PASSWORD"))


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = Path(os.environ.get("STELLIS_AUTH_DB_DIR") or storage.base_dir()) / "auth.sqlite3"
    conn = sqlite3.connect(path, timeout=30, factory=storage.TolerantConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS account (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
        salt BLOB NOT NULL, verifier BLOB NOT NULL, created_at TEXT NOT NULL
    )""")
    original_schema = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'account'").fetchone()[0]
    if "CHECK(id = 1)" in original_schema:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("""CREATE TABLE account_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
                salt BLOB NOT NULL, verifier BLOB NOT NULL, created_at TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '')""")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(account)")}
            name = "name" if "name" in columns else "''"
            email = "email" if "email" in columns else "''"
            conn.execute(f"INSERT INTO account_new SELECT id, username, salt, verifier, created_at, {name}, {email} FROM account")
            conn.execute("DROP TABLE account")
            conn.execute("ALTER TABLE account_new RENAME TO account")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    account_columns = {row["name"] for row in conn.execute("PRAGMA table_info(account)")}
    for column in ("name", "email"):
        if column not in account_columns:
            conn.execute(f"ALTER TABLE account ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    if "onboarding_completed" not in account_columns:
        conn.execute("ALTER TABLE account ADD COLUMN onboarding_completed INTEGER NOT NULL DEFAULT 1")
    if "keep_debug_data" not in account_columns:
        conn.execute("ALTER TABLE account ADD COLUMN keep_debug_data INTEGER NOT NULL DEFAULT 0")
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token_hash BLOB PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES account(id),
        expires_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dev_sessions (
        token_hash BLOB PRIMARY KEY, expires_at TEXT NOT NULL
    )""")
    # Demo guests (#247): accounts made for one visit to the demo, removed when they expire.
    conn.execute("""CREATE TABLE IF NOT EXISTS demo_guest (
        account_id INTEGER PRIMARY KEY REFERENCES account(id), expires_at TEXT NOT NULL
    )""")
    conn.commit()
    path.chmod(0o600)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


_UNKNOWN_SALT = secrets.token_bytes(32)


def _verifier(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)


def _token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def has_account() -> bool:
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM account LIMIT 1").fetchone() is not None


def register(username: str, password: str, keep_debug_data: bool = False) -> str:
    username = username.strip()
    if not 3 <= len(username) <= 80 or len(password) < 12 or len(password) > 1024:
        raise ValueError("Use a username of 3 to 80 characters and a password of at least 12 characters")
    salt = secrets.token_bytes(32)
    stamp = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        try:
            conn.execute("INSERT INTO account (username, salt, verifier, created_at, onboarding_completed, keep_debug_data) "
                         "VALUES (?, ?, ?, ?, 0, ?)", (username, salt, _verifier(password, salt), stamp, int(keep_debug_data)))
        except sqlite3.IntegrityError as exc:
            raise RuntimeError("That username is taken") from exc
    return username


def login(username: str, password: str) -> str | None:
    if dev_enabled() and username == "dev" and hmac.compare_digest(password, os.environ["STELLIS_DEV_PASSWORD"]):
        token = secrets.token_urlsafe(32)
        expiry = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
        with _connect() as conn:
            conn.execute("INSERT INTO dev_sessions (token_hash, expires_at) VALUES (?, ?)",
                         (_token_hash(token), expiry))
        return token
    with _connect() as conn:
        row = conn.execute("SELECT id, username, salt, verifier FROM account WHERE username = ?", (username,)).fetchone()
    if row is None:
        # Spend the same hashing time so response timing does not reveal which usernames exist.
        _verifier(password, _UNKNOWN_SALT)
        return None
    candidate = _verifier(password, row["salt"])
    if not hmac.compare_digest(row["verifier"], candidate):
        return None
    return create_session(int(row["id"]))


def create_session(account_id: int = 1) -> str:
    token = secrets.token_urlsafe(32)
    expiry = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (datetime.now(timezone.utc).isoformat(),))
        conn.execute("INSERT INTO sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                     (_token_hash(token), account_id, expiry))
    return token


def account_id_for_username(username: str) -> int:
    with _connect() as conn:
        row = conn.execute("SELECT id FROM account WHERE username = ?", (username,)).fetchone()
    if row is None:
        raise LookupError("Account not found")
    return int(row["id"])


def current_account_id(token: str | None) -> int | None:
    if not token:
        return None
    try:
        digest = _token_hash(token)
    except UnicodeEncodeError:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT account_id FROM sessions WHERE token_hash = ? AND expires_at > ?",
                           (digest, datetime.now(timezone.utc).isoformat())).fetchone()
    return int(row["account_id"]) if row else None


def current_account(token: str | None) -> str | None:
    if not token:
        return None
    try:
        digest = _token_hash(token)
    except UnicodeEncodeError:
        return None
    with _connect() as conn:
        row = conn.execute("""SELECT account.username FROM sessions JOIN account ON account.id = sessions.account_id
            WHERE token_hash = ? AND expires_at > ?""", (digest, datetime.now(timezone.utc).isoformat())).fetchone()
        if row is None and dev_enabled():
            dev = conn.execute("SELECT 1 FROM dev_sessions WHERE token_hash = ? AND expires_at > ?",
                               (digest, datetime.now(timezone.utc).isoformat())).fetchone()
            if dev:
                return "dev"
    return str(row["username"]) if row else None


def revoke(token: str | None) -> None:
    if token:
        try:
            digest = _token_hash(token)
        except UnicodeEncodeError:
            return
        with _connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
            conn.execute("DELETE FROM dev_sessions WHERE token_hash = ?", (digest,))


def _check_password(conn: sqlite3.Connection, password: str, account_id: int = 1) -> None:
    row = conn.execute("SELECT salt, verifier FROM account WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise LookupError("Account not found")
    if not hmac.compare_digest(row["verifier"], _verifier(password, row["salt"])):
        raise PermissionError("Your current password is incorrect")


def profile(account_id: int = 1) -> dict[str, object]:
    with _connect() as conn:
        row = conn.execute("SELECT username, name, email, keep_debug_data FROM account WHERE id = ?",
                           (account_id,)).fetchone()
    if row is None:
        raise LookupError("Account not found")
    return {"username": row["username"], "name": row["name"], "email": row["email"],
            "keep_debug_data": bool(row["keep_debug_data"])}


def keeps_debug_data(account_id: int) -> bool:
    """Whether the account's owner opted in to keeping debug data."""
    return bool(profile(account_id)["keep_debug_data"])


def set_keep_debug_data(keep: bool, account_id: int = 1) -> dict[str, object]:
    with _connect() as conn:
        if not conn.execute("UPDATE account SET keep_debug_data = ? WHERE id = ?", (int(keep), account_id)).rowcount:
            raise LookupError("Account not found")
    return profile(account_id)


def update_profile(name: str, email: str, account_id: int = 1) -> dict[str, object]:
    name, email = " ".join(name.split()), email.strip()
    if len(name) > 100:
        raise ValueError("Your name must be at most 100 characters")
    if email and (len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)):
        raise ValueError("Enter an email address like name@example.com")
    with _connect() as conn:
        if not conn.execute("UPDATE account SET name = ?, email = ? WHERE id = ?", (name, email, account_id)).rowcount:
            raise LookupError("Account not found")
    return profile(account_id)


def change_username(username: str, password: str, account_id: int = 1) -> dict[str, object]:
    username = username.strip()
    if not 3 <= len(username) <= 80:
        raise ValueError("Use a username of 3 to 80 characters")
    with _connect() as conn:
        _check_password(conn, password, account_id)
        conn.execute("UPDATE account SET username = ? WHERE id = ?", (username, account_id))
    return profile(account_id)


def change_password(current: str, new: str, keep_token: str | None, account_id: int = 1) -> None:
    """Replace the verifier and sign out every other session."""
    if len(new) < 12 or len(new) > 1024:
        raise ValueError("Your new password must be at least 12 characters")
    salt = secrets.token_bytes(32)
    with _connect() as conn:
        _check_password(conn, current, account_id)
        conn.execute("UPDATE account SET salt = ?, verifier = ? WHERE id = ?", (salt, _verifier(new, salt), account_id))
        keep = _token_hash(keep_token) if keep_token else b""
        conn.execute("DELETE FROM sessions WHERE account_id = ? AND token_hash != ?", (account_id, keep))


def reset_password(new: str, account_id: int) -> None:
    """Set a new password after the account's recovery key unlocked its records, signing out every session."""
    if len(new) < 12 or len(new) > 1024:
        raise ValueError("Your new password must be at least 12 characters")
    salt = secrets.token_bytes(32)
    with _connect() as conn:
        conn.execute("UPDATE account SET salt = ?, verifier = ? WHERE id = ?", (salt, _verifier(new, salt), account_id))
        conn.execute("DELETE FROM sessions WHERE account_id = ?", (account_id,))


def verify_password(password: str, account_id: int = 1) -> None:
    with _connect() as conn:
        _check_password(conn, password, account_id)


GUEST_HOURS = 24


def register_guest(password: str | None = None) -> tuple[str, str]:
    """Creates a demo guest (#247): a normal account with a generated name, already past onboarding.

    Args:
        password: The password to set; the account gateway passes the one it unlocks the
            workspace with. When None, one is generated.

    Returns:
        The username and password. Nobody is shown either; the session is the guest's only key.
    """
    password = password or secrets.token_urlsafe(32)
    username = register(f"demo-{secrets.token_hex(6)}", password)
    expiry = (datetime.now(timezone.utc) + timedelta(hours=GUEST_HOURS)).isoformat()
    with _connect() as conn:
        account_id = conn.execute("SELECT id FROM account WHERE username = ?", (username,)).fetchone()[0]
        conn.execute("UPDATE account SET onboarding_completed = 1 WHERE id = ?", (account_id,))
        conn.execute("INSERT INTO demo_guest (account_id, expires_at) VALUES (?, ?)", (account_id, expiry))
    return username, password


def is_guest(account_id: int | None) -> bool:
    if account_id is None:
        return False
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM demo_guest WHERE account_id = ?", (account_id,)).fetchone() is not None


def expired_guests() -> list[int]:
    """Demo guests whose day is over, to be removed with their workspaces."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        return [row[0] for row in conn.execute("SELECT account_id FROM demo_guest WHERE expires_at <= ?", (now,))]


def delete_account(account_id: int = 1) -> None:
    """Remove one account and its sessions after its private workspace is erased."""
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM demo_guest WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM account WHERE id = ?", (account_id,))


def needs_onboarding(account_id: int | None) -> bool:
    if account_id is None:
        return False
    with _connect() as conn:
        row = conn.execute("SELECT onboarding_completed FROM account WHERE id = ?", (account_id,)).fetchone()
    return bool(row and not row[0])


def complete_onboarding(account_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE account SET onboarding_completed = 1 WHERE id = ?", (account_id,))
