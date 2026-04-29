"""FrozenClock for deterministic tests."""

from __future__ import annotations

from datetime import datetime, timedelta


class FrozenClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def set(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        """Move the frozen time forward by *delta*.

        Negative deltas are allowed (e.g. to simulate clock drift in tests),
        but the typical use is positive forward motion. Callers should pass
        timedelta values explicitly to avoid sign-confusion.
        """
        self._now = self._now + delta
