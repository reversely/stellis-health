"""The deployment's AI budget, shared by every account process.

Each demo start makes a new guest account, so a per-account allowance resets with every restart.
This budget is one pool for the whole deployment, demo guests and accounts alike, kept beside the
sign-in database (``STELLIS_AUTH_DB_DIR``) outside the per-account workspaces (owner, 2026-10-30).
``STELLIS_AI_BUDGET_USD`` sets it on first use; the owner can turn it off in Settings.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from stellis import storage

MESSAGE = "This demo has used its AI credits. Answers Stellis has already given can still be opened."

# US dollars per million tokens (input, output), Nebius Token Factory model catalog, read 2026-10-30.
PRICES = {
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "nvidia/Nemotron-3_5-Lightning": (0.06, 0.24),
    "zai-org/GLM-5.3-Flash": (0.15, 0.50),
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 3.00),
}
# A model missing from the table is charged at the highest listed price, so the budget never runs over.
FALLBACK = max(PRICES.values())
SEARCH_PRICE = 0.008  # One Tavily basic search, pay-as-you-go credit price.


def _connect() -> sqlite3.Connection:
    root = Path(os.environ.get("STELLIS_AUTH_DB_DIR") or storage.base_dir())
    conn = sqlite3.connect(root / "deployment.sqlite3", timeout=30, isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS ai_budget (id INTEGER PRIMARY KEY CHECK (id = 1), "
                 "limit_usd REAL NOT NULL, spent_usd REAL NOT NULL DEFAULT 0, enabled INTEGER NOT NULL)")
    configured = os.environ.get("STELLIS_AI_BUDGET_USD", "").strip()
    if configured:
        conn.execute("INSERT OR IGNORE INTO ai_budget (id, limit_usd, enabled) VALUES (1, ?, 1)", (float(configured),))
    return conn


def budget() -> dict | None:
    """The budget as limit, spent and whether it is on, or None when the deployment has none."""
    with _connect() as conn:
        row = conn.execute("SELECT limit_usd, spent_usd, enabled FROM ai_budget WHERE id = 1").fetchone()
    if row is None:
        return None
    return {"limit_usd": row[0], "spent_usd": round(row[1], 4), "enabled": bool(row[2])}


def exhausted() -> bool:
    current = budget()
    return bool(current and current["enabled"] and current["spent_usd"] >= current["limit_usd"])


def cost(model: str | None, usage: object) -> float:
    """What one response cost, from the tokens Token Factory reported."""
    if not isinstance(usage, dict):
        return 0.0
    price_in, price_out = PRICES.get(str(model or ""), FALLBACK)

    def tokens(*keys: str) -> float:
        # Providers name the counts differently; only a number counts.
        return next((float(usage[key]) for key in keys if isinstance(usage.get(key), (int, float))), 0.0)

    split = tokens("prompt_tokens", "input_tokens", "input") * price_in + tokens("completion_tokens", "output_tokens", "output") * price_out
    # A reply that reports only a total is charged at the output price, so the budget never undercounts.
    return max(split, tokens("total_tokens", "total") * price_out) / 1_000_000


def charge(usd: float) -> None:
    """Adds a spend to the shared pool in one write, so concurrent accounts cannot both take the last cent."""
    if usd <= 0:
        return
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE ai_budget SET spent_usd = spent_usd + ? WHERE id = 1", (usd,))
        conn.execute("COMMIT")


def set_enabled(enabled: bool) -> dict | None:
    """Turns the limit on or off; spending keeps being counted either way."""
    with _connect() as conn:
        conn.execute("UPDATE ai_budget SET enabled = ? WHERE id = 1", (int(enabled),))
    return budget()
