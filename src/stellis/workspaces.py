"""Unlocked workspaces in memory: opened at sign-in, saved encrypted, closed when idle or signed out.

The gateway owns this registry. A workspace folder lives under STELLIS_WORKSPACE_DIR, which the
hosted image mounts as a RAM disk, so records exist in readable form only while their owner is
signed in. Data keys stay in this process's memory and are never written.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path

from stellis import vault

logger = logging.getLogger(__name__)

IDLE_SECONDS = int(os.environ.get("STELLIS_WORKSPACE_IDLE_SECONDS", "1800"))
CHECKPOINT_SECONDS = int(os.environ.get("STELLIS_WORKSPACE_CHECKPOINT_SECONDS", "300"))


def enabled() -> bool:
    return os.environ.get("STELLIS_LOCKED_WORKSPACES") == "1"


class Workspaces:
    """Data keys and activity for each unlocked account."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.ram = Path(os.environ.get("STELLIS_WORKSPACE_DIR") or root / ".workspaces")
        self.keys: dict[int, bytes] = {}
        self.active: dict[int, float] = {}
        self.saved: dict[int, float] = {}
        self.locks: dict[int, asyncio.Lock] = {}

    def path(self, account_id: int) -> Path:
        return self.ram / str(account_id)

    def unlocked(self, account_id: int) -> bool:
        return account_id in self.keys

    def touch(self, account_id: int) -> None:
        self.active[account_id] = time.monotonic()

    def _lock(self, account_id: int) -> asyncio.Lock:
        return self.locks.setdefault(account_id, asyncio.Lock())

    async def open(self, account_id: int, password: str) -> dict[str, object]:
        """Unlock with the password just checked at sign-in; create the vault on first use.

        Returns the latest version (`head`) and, when the account had none, a new recovery code
        (`recovery_code`) to show the person once.
        """
        async with self._lock(account_id):
            if account_id in self.keys:
                key = self.keys[account_id]
            elif vault.exists(self.root, account_id):
                key = await asyncio.to_thread(vault.unlock_key, self.root, account_id, password)
            else:
                key = await asyncio.to_thread(vault.create, self.root, account_id, password)
                # Records written before locked workspaces move in and are encrypted straight away.
                folder = self.path(account_id)
                shutil.rmtree(folder, ignore_errors=True)
                await asyncio.to_thread(vault.adopt, self.root, account_id, folder)
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                await asyncio.to_thread(vault.save, self.root, account_id, key, folder)
            return await self._unlocked(account_id, key)

    async def recover(self, account_id: int, code: str, password: str) -> dict[str, object]:
        """Unlock with the recovery code, then wrap the same key with the new password."""
        async with self._lock(account_id):
            key = await asyncio.to_thread(vault.recover_key, self.root, account_id, code)
            await asyncio.to_thread(vault.rewrap, self.root, account_id, key, password)
            return await self._unlocked(account_id, key)

    async def _unlocked(self, account_id: int, key: bytes) -> dict[str, object]:
        """Restore the workspace into RAM unless it is open already; caller holds the account lock."""
        if account_id not in self.keys:
            # Always from the saved file: a folder left from before a crash may be older or partial.
            folder = self.path(account_id)
            shutil.rmtree(folder, ignore_errors=True)
            await asyncio.to_thread(vault.restore, self.root, account_id, key, folder)
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.keys[account_id] = key
            self.saved[account_id] = time.time()
        self.touch(account_id)
        result: dict[str, object] = {"head": vault.head(self.root, account_id)}
        if not vault.has_recovery(self.root, account_id):
            result["recovery_code"] = await asyncio.to_thread(vault.add_recovery, self.root, account_id, key)
        return result

    async def checkpoint(self, account_id: int) -> None:
        """Save an encrypted copy of a workspace that changed since its last save."""
        async with self._lock(account_id):
            key = self.keys.get(account_id)
            folder = self.path(account_id)
            if key is None or not folder.is_dir() or _changed_since(folder) <= self.saved.get(account_id, 0):
                return
            started = time.time()
            await asyncio.to_thread(vault.save, self.root, account_id, key, folder)
            self.saved[account_id] = started

    async def close(self, account_id: int, stop) -> None:
        """Stop the account's process with `stop`, save, and erase the readable copy and its key."""
        async with self._lock(account_id):
            key = self.keys.get(account_id)
            if key is None:
                return
            await stop(account_id)
            folder = self.path(account_id)
            if folder.is_dir():
                await asyncio.to_thread(vault.save, self.root, account_id, key, folder)
            shutil.rmtree(folder, ignore_errors=True)
            self.keys.pop(account_id, None)
            self.active.pop(account_id, None)
            self.saved.pop(account_id, None)

    async def rewrap(self, account_id: int, password: str) -> None:
        key = self.keys.get(account_id)
        if key is not None:
            await asyncio.to_thread(vault.rewrap, self.root, account_id, key, password)

    async def destroy(self, account_id: int) -> None:
        async with self._lock(account_id):
            self.keys.pop(account_id, None)
            self.active.pop(account_id, None)
            shutil.rmtree(self.path(account_id), ignore_errors=True)
            await asyncio.to_thread(vault.destroy, self.root, account_id)

    def busy(self, account_id: int) -> bool:
        """A transcription or other job still running keeps the workspace open."""
        database = self.path(account_id) / "stellis.sqlite3"
        if not database.is_file():
            return False
        try:
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
                return bool(conn.execute(
                    "SELECT count(*) FROM processing_jobs WHERE state IN ('queued', 'running')").fetchone()[0])
        except sqlite3.Error:
            return False

    async def tend(self, stop) -> None:
        """Every minute: checkpoint changed workspaces and close idle ones."""
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for account_id in list(self.keys):
                try:
                    if now - self.active.get(account_id, now) > IDLE_SECONDS and not self.busy(account_id):
                        await self.close(account_id, stop)
                    elif time.time() - self.saved.get(account_id, 0) > CHECKPOINT_SECONDS:
                        await self.checkpoint(account_id)
                except Exception:
                    logger.exception("Could not save the workspace for account %s", account_id)


def _changed_since(folder: Path) -> float:
    return max((path.stat().st_mtime for path in folder.rglob("*") if path.is_file()), default=0)
