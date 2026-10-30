"""Locked workspaces: each account's records at rest are one encrypted, versioned file.

A random data key encrypts the workspace; a key derived from the account's password wraps the data
key. Neither the password nor either key is ever written down, so a stored workspace is unreadable
without the password. While the person is signed in, the gateway holds the data key in memory and
the workspace is unpacked into a RAM folder for the account's process.

The file is a JSON header line followed by AES-256-GCM chunks. Every chunk's associated data binds
the header (account, version, previous version's hash) and the chunk's position, so editing,
reordering, truncating or swapping a workspace fails to decrypt. The header's version and previous
hash chain each saved version to the one before, so a browser that remembers the latest version can
tell when an older one is served (`head`).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FORMAT = 1
CHUNK = 1 << 20
# Interactive-login strength (OWASP's scrypt minimum is N=2^17, r=8, p=1).
SCRYPT = {"n": 1 << 17, "r": 8, "p": 1}
# Root entries that belong to the deployment, not to account 1's workspace.
SHARED = {"auth.sqlite3", "oauth_pending.sqlite3", "accounts", "models", "vault", ".account-sockets", ".workspaces",
          "lost+found", "deployment.sqlite3", "deployment.sqlite3-journal", "demo-model-cache"}


class VaultError(RuntimeError):
    """A workspace that cannot be opened: wrong password, or a changed or damaged file."""


def directory(root: Path, account_id: int) -> Path:
    return root / "vault" / str(account_id)


def exists(root: Path, account_id: int) -> bool:
    return (directory(root, account_id) / "key.json").is_file()


def _kek(password: str, salt: bytes, params: dict[str, int]) -> bytes:
    return Scrypt(salt=salt, length=32, n=params["n"], r=params["r"], p=params["p"]).derive(password.encode())


def _wrap(account_id: int, password: str, key: bytes, purpose: str = "key") -> dict[str, object]:
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    sealed = AESGCM(_kek(password, salt, SCRYPT)).encrypt(nonce, key, f"stellis-{purpose}:{account_id}".encode())
    return {"format": FORMAT, "kdf": "scrypt", **SCRYPT, "salt": salt.hex(), "nonce": nonce.hex(), "key": sealed.hex()}


def _unwrap(account_id: int, secret: str, record: dict, purpose: str = "key") -> bytes:
    kek = _kek(secret, bytes.fromhex(record["salt"]), record)
    return AESGCM(kek).decrypt(bytes.fromhex(record["nonce"]), bytes.fromhex(record["key"]),
                               f"stellis-{purpose}:{account_id}".encode())


def _record(root: Path, account_id: int) -> dict:
    return json.loads((directory(root, account_id) / "key.json").read_text())


def _save_record(root: Path, account_id: int, record: dict) -> None:
    _write_atomic(directory(root, account_id) / "key.json", json.dumps(record).encode())


def new_recovery_code() -> str:
    """32 base32 characters (160 bits) in groups of four, shown once at sign-up."""
    raw = base64.b32encode(secrets.token_bytes(20)).decode()
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def _normal(code: str) -> str:
    return "".join(character for character in code.upper() if character.isalnum())


def has_recovery(root: Path, account_id: int) -> bool:
    return "recovery" in _record(root, account_id)


def add_recovery(root: Path, account_id: int, key: bytes) -> str:
    """Wrap the data key with a new recovery code as well, replacing any earlier one; returns the code."""
    code = new_recovery_code()
    record = _record(root, account_id)
    record["recovery"] = _wrap(account_id, _normal(code), key, "recovery")
    _save_record(root, account_id, record)
    return code


def recover_key(root: Path, account_id: int, code: str) -> bytes:
    record = _record(root, account_id)
    try:
        return _unwrap(account_id, _normal(code), record["recovery"], "recovery")
    except Exception as exc:
        raise VaultError("The recovery key does not unlock this workspace") from exc


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def create(root: Path, account_id: int, password: str) -> bytes:
    """A new data key for the account, wrapped with its password; returns the key."""
    key = AESGCM.generate_key(bit_length=256)
    _write_atomic(directory(root, account_id) / "key.json", json.dumps(_wrap(account_id, password, key)).encode())
    return key


def unlock_key(root: Path, account_id: int, password: str) -> bytes:
    try:
        return _unwrap(account_id, password, _record(root, account_id))
    except Exception as exc:
        raise VaultError("The password does not unlock this workspace") from exc


def rewrap(root: Path, account_id: int, key: bytes, password: str) -> None:
    """Wrap the same data key with a new password, as after a password change; the recovery key stays."""
    record = _wrap(account_id, password, key)
    old = _record(root, account_id)
    if "recovery" in old:
        record["recovery"] = old["recovery"]
    _save_record(root, account_id, record)


def destroy(root: Path, account_id: int) -> None:
    """Delete the wrapped key and every saved version: without the key the data is unrecoverable."""
    shutil.rmtree(directory(root, account_id), ignore_errors=True)


def head(root: Path, account_id: int) -> dict[str, object] | None:
    """The latest saved version and its hash, for the browser's rollback check."""
    path = directory(root, account_id) / "workspace.enc"
    if not path.is_file():
        return None
    header = json.loads(path.open("rb").readline())
    return {"version": header["version"], "hash": _file_hash(path)}


def _file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _archive(workspace: Path, skip: set[str] | None = None) -> bytes:
    """A tar of the workspace; SQLite files are copied with the backup API, so a live database is consistent."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path in sorted(workspace.rglob("*")):
            relative = path.relative_to(workspace)
            if skip and relative.parts[0] in skip:
                continue
            if path.is_symlink() or path.name.endswith(("-wal", "-shm", "-journal")):
                continue
            if path.is_dir():
                tar.add(path, arcname=str(relative), recursive=False)
            elif path.suffix == ".sqlite3":
                copy = sqlite3.connect(":memory:")
                with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as source:
                    source.backup(copy)
                data = copy.serialize()
                copy.close()
                info = tarfile.TarInfo(str(relative))
                info.size, info.mode, info.mtime = len(data), 0o600, int(path.stat().st_mtime)
                tar.addfile(info, io.BytesIO(data))
            elif path.is_file():
                tar.add(path, arcname=str(relative), recursive=False)
    return buffer.getvalue()


def _aad(header_hash: bytes, index: int, last: bool) -> bytes:
    return header_hash + index.to_bytes(8, "big") + (b"\x01" if last else b"\x00")


def save(root: Path, account_id: int, key: bytes, workspace: Path, skip: set[str] | None = None) -> dict[str, object]:
    """Encrypt the workspace as the next version, keeping the previous version beside it."""
    folder = directory(root, account_id)
    current = folder / "workspace.enc"
    previous = head(root, account_id)
    header = {"format": FORMAT, "account": account_id, "version": (previous["version"] + 1) if previous else 1,
              "previous": previous["hash"] if previous else None, "nonce": secrets.token_bytes(8).hex()}
    header_line = json.dumps(header, sort_keys=True).encode() + b"\n"
    header_hash = hashlib.sha256(header_line).digest()
    plain, cipher, base = _archive(workspace, skip), AESGCM(key), bytes.fromhex(header["nonce"])
    chunks = [plain[i:i + CHUNK] for i in range(0, len(plain), CHUNK)] or [b""]
    body = bytearray(header_line)
    for index, chunk in enumerate(chunks):
        sealed = cipher.encrypt(base + index.to_bytes(4, "big"), chunk, _aad(header_hash, index, index == len(chunks) - 1))
        body += len(sealed).to_bytes(4, "big") + sealed
    if current.is_file():
        os.replace(current, folder / "workspace.prev.enc")
    _write_atomic(current, bytes(body))
    return {"version": header["version"], "hash": _file_hash(current)}


def restore(root: Path, account_id: int, key: bytes, workspace: Path) -> None:
    """Decrypt the latest version into the (empty) workspace folder, refusing any changed file."""
    path = directory(root, account_id) / "workspace.enc"
    if not path.is_file():
        return
    data = path.read_bytes()
    end = data.index(b"\n") + 1
    header_line, header = data[:end], json.loads(data[:end])
    if header.get("format") != FORMAT or header.get("account") != account_id:
        raise VaultError("This workspace file belongs to another account or format")
    header_hash, base, cipher = hashlib.sha256(header_line).digest(), bytes.fromhex(header["nonce"]), AESGCM(key)
    plain, offset, index = bytearray(), end, 0
    try:
        while offset < len(data):
            size = int.from_bytes(data[offset:offset + 4], "big")
            sealed = data[offset + 4:offset + 4 + size]
            offset += 4 + size
            plain += cipher.decrypt(base + index.to_bytes(4, "big"), sealed, _aad(header_hash, index, offset >= len(data)))
            index += 1
    except Exception as exc:
        raise VaultError("The workspace file was changed or damaged and was not opened") from exc
    if index == 0:
        raise VaultError("The workspace file is empty")
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(bytes(plain)), mode="r") as tar:
        tar.extractall(workspace, filter="data")


def adopt(root: Path, account_id: int, workspace: Path) -> bool:
    """Move an account's existing plain files into its workspace before its first encrypted save."""
    source = root if account_id == 1 else root / "accounts" / str(account_id)
    if not source.is_dir():
        return False
    entries = [entry for entry in source.iterdir() if not (account_id == 1 and entry.name in SHARED)]
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    for entry in entries:
        shutil.move(str(entry), workspace / entry.name)
    if account_id != 1:
        shutil.rmtree(source, ignore_errors=True)
    return bool(entries)
