"""FrozenClock for deterministic tests."""

from __future__ import annotations

from datetime import datetime


class FrozenClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def set(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now
