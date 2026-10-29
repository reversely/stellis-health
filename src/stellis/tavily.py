"""Account-local Tavily credential status and storage.

Only Stellis's own research client (``stellis.research``) uses the key. It is
never handed to an agent as a search tool, so every query passes the privacy
gate first.
"""

from __future__ import annotations

import os
from pathlib import Path
from stellis import runtime_profile, storage


def _file() -> Path:
    return storage.data_dir() / "tavily-api-key"


def saved_key() -> str | None:
    try:
        return _file().read_text().strip() or None
    except FileNotFoundError:
        return None


def _host_key() -> str | None:
    """Read the owner's host fallback, or the deployment key every account shares."""
    if os.environ.get("STELLIS_ACCOUNT_ID", "1") != "1" and not runtime_profile.shared_provider_keys():
        return None
    value = os.environ.get("TAVILY_API_KEY")
    if value:
        return value
    path = Path(__file__).resolve().parents[2] / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.startswith("TAVILY_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def available_key() -> str | None:
    """Use this account's saved key, with the owner's existing host key as fallback."""
    return saved_key() or _host_key()


def key_status() -> dict[str, object]:
    """Report availability without returning the credential."""
    if saved_key():
        return {"configured": True, "source": "saved"}
    if available_key():
        return {"configured": True, "source": "environment"}
    return {"configured": False, "source": None}


def save_key(value: str) -> dict[str, object]:
    key = value.strip()
    if not 16 <= len(key) <= 512 or any(character.isspace() for character in key):
        raise ValueError("Enter the Tavily API key exactly as shown")
    path = _file()
    try:
        descriptor = os.open(path.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(key)
            output.flush()
            os.fsync(output.fileno())
        os.replace(path.with_suffix(".tmp"), path)
    finally:
        path.with_suffix(".tmp").unlink(missing_ok=True)
    return key_status()


def remove_key() -> dict[str, object]:
    _file().unlink(missing_ok=True)
    return key_status()
