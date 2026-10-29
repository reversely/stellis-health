"""In-process sliding-window limits for sign-in and sign-up attempts.

The account gateway is one process, so a dictionary of recent attempt times per
key covers every account. Limits reset when the process restarts.
"""

from __future__ import annotations

import collections
import math
import threading
import time
from typing import Callable


class SlidingWindow:
    """Allows at most ``limit`` recorded events per key within ``seconds``.

    Attributes:
        limit: The most events a key may record within the window.
        seconds: The window length.
    """

    def __init__(self,
                 limit: int,
                 seconds: float,
                 clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.seconds = seconds
        self._clock = clock
        self._events: dict[str, collections.deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> collections.deque[float]:
        """Drops a key's events older than the window and returns the rest."""
        events = self._events.get(key, collections.deque())
        while events and events[0] <= now - self.seconds:
            events.popleft()
        if not events:
            self._events.pop(key, None)
        return events

    def retry_after(self, key: str) -> int:
        """Returns the whole seconds until ``key`` may try again.

        Args:
            key: The username or client address being limited.

        Returns:
            0 while the key is under the limit, otherwise at least 1.
        """
        with self._lock:
            now = self._clock()
            events = self._recent(key, now)
            if len(events) < self.limit:
                return 0
            return max(1, math.ceil(events[0] + self.seconds - now))

    def record(self, key: str) -> None:
        """Records one event for ``key`` at the current time."""
        with self._lock:
            now = self._clock()
            events = self._recent(key, now)
            events.append(now)
            self._events[key] = events

    def clear(self, key: str) -> None:
        """Forgets every event for ``key``."""
        with self._lock:
            self._events.pop(key, None)

    def reset(self) -> None:
        """Forgets every event for every key."""
        with self._lock:
            self._events.clear()


# Failed sign-ins: 5 per username and 20 per client address in 15 minutes.
LOGIN_FAILURES_BY_USERNAME = SlidingWindow(5, 15 * 60)
LOGIN_FAILURES_BY_CLIENT = SlidingWindow(20, 15 * 60)
# Accounts created: 5 per client address per hour.
SIGNUPS_BY_CLIENT = SlidingWindow(5, 60 * 60)
# Demo guests (#247): 10 per client address per hour.
GUESTS_BY_CLIENT = SlidingWindow(10, 60 * 60)
