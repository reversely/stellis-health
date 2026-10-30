"""Daily Token Factory allowance for accounts that use the shared keys.

Each account's database keeps a per-day token total from the usage fields that
Token Factory returns. Callers check the allowance before a request, so an
account over its limit makes no provider call. The owner and accounts with
their own keys have no limit.
"""

from __future__ import annotations

import datetime
import os

from stellis import runtime_profile
from stellis import storage

MESSAGE = "Today's AI allowance is used up and resets at midnight UTC"


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _create_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            day TEXT PRIMARY KEY,
            tokens INTEGER NOT NULL)""")


def limit() -> int | None:
    """Returns this account's daily token limit, or None when it has none."""
    if runtime_profile.local_inference() or runtime_profile.operator():
        return None
    value = os.environ.get("STELLIS_DAILY_TOKEN_LIMIT", "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else None


def used_today() -> int:
    """Returns the tokens this account used since midnight UTC."""
    with storage.connect() as conn:
        _create_table(conn)
        row = conn.execute("SELECT tokens FROM token_usage WHERE day = ?",
                           (_today(),)).fetchone()
    return int(row[0]) if row else 0


def exceeded() -> str | None:
    """Returns the message to show when the deployment's budget or today's allowance is used up."""
    from stellis import deployment
    if deployment.exhausted():
        return deployment.MESSAGE
    allowed = limit()
    if allowed is not None and used_today() >= allowed:
        return MESSAGE
    return None


def count_tokens(usage: object) -> int:
    """Reads a response's token total from its usage field.

    Args:
        usage: The usage object from a chat completion or Responses reply.

    Returns:
        ``total_tokens`` or ``total`` when present, otherwise prompt plus
        completion tokens, otherwise input plus output tokens. Values that are
        not non-negative integers count as 0.
    """
    if not isinstance(usage, dict):
        return 0

    def number(key: str) -> int:
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else 0

    for key in ("total_tokens", "total"):
        if number(key):
            return number(key)
    return (number("prompt_tokens") + number("completion_tokens") or
            number("input") + number("output"))


def record(usage: object, model: str | None = None) -> None:
    """Adds one response's tokens to today's total and its cost to the deployment's budget."""
    from stellis import deployment
    if not runtime_profile.local_inference():  # A local model costs the deployment nothing.
        deployment.charge(deployment.cost(model, usage))
    tokens = count_tokens(usage)
    if not tokens:
        return
    with storage.connect() as conn:
        _create_table(conn)
        conn.execute(
            """
            INSERT INTO token_usage (day, tokens) VALUES (?, ?)
            ON CONFLICT(day) DO UPDATE SET tokens = tokens + excluded.tokens""",
            (_today(), tokens))
