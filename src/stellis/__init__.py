"""Stellis local interview workspace."""

# Demo workspaces read "today" from their story date (#252); this must run before any other import.
from stellis import story_clock

story_clock.install()

from stellis.cli import main  # noqa: E402

__all__ = ["main"]
