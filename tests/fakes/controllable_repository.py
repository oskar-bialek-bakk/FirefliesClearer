"""Test fake — paginated repository with rate-limit injection.

Unlike InMemoryMeetingRepository, this fake exposes a page-by-page
list_meetings_page method whose pagination matches the FirefliesClient
contract (skip + limit + toDate). It can also be configured to raise
RateLimitedError at a specific skip value, simulating Fireflies'
quota responses for sync-engine tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from firefliesclearer.core.models import Meeting
from firefliesclearer.infra.fireflies_client import RateLimitedError


class ControllableMeetingRepository:
    def __init__(
        self,
        *,
        meetings: list[Meeting],
        page_size: int = 50,
        raise_rate_limit_after_skip: int | None = None,
        raise_rate_limit_retry_after_seconds: float = 60.0,
    ) -> None:
        self._meetings = list(meetings)
        self._page_size = page_size
        self._raise_at = raise_rate_limit_after_skip
        self._retry_after = raise_rate_limit_retry_after_seconds
        self.list_calls: list[tuple[int, int, datetime | None]] = []  # (skip, limit, to_date)

    async def list_meetings_page(
        self,
        *,
        skip: int,
        limit: int,
        to_date: datetime | None = None,
    ) -> AsyncIterator[Meeting]:
        self.list_calls.append((skip, limit, to_date))
        if self._raise_at is not None and skip >= self._raise_at:
            raise RateLimitedError(
                "controllable fake: rate-limited",
                retry_after_seconds=self._retry_after,
            )
        # Filter by to_date (upper bound on meeting_date) to mirror real API
        candidates = (
            [m for m in self._meetings if m.meeting_date < to_date]
            if to_date is not None
            else list(self._meetings)
        )
        # Pagination — cap by configured page_size so tests can control
        # how many rows the caller sees regardless of its requested limit.
        effective_limit = min(limit, self._page_size)
        page = candidates[skip : skip + effective_limit]
        for m in page:
            yield m
