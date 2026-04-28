"""In-memory MeetingRepository for tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

from firefliesclearer.core.models import ArtifactBundle, Meeting
from firefliesclearer.ports.meeting_repository import MeetingFilter


class InMemoryMeetingRepository:
    def __init__(
        self,
        meetings: list[Meeting] | None = None,
        artifacts: dict[str, ArtifactBundle] | None = None,
    ) -> None:
        self._meetings: dict[str, Meeting] = {
            m.meeting_id: m for m in (meetings or [])
        }
        self._artifacts: dict[str, ArtifactBundle] = artifacts or {}
        self.deleted: list[str] = []
        self.fail_fetch_for: set[str] = set()
        self.fail_delete_for: set[str] = set()

    async def list_meetings(self, filter: MeetingFilter) -> AsyncIterator[Meeting]:
        for m in self._meetings.values():
            if filter.older_than and m.meeting_date >= filter.older_than:
                continue
            yield m

    async def fetch_artifacts(self, meeting_id: str) -> ArtifactBundle:
        if meeting_id in self.fail_fetch_for:
            raise RuntimeError(f"forced fetch failure: {meeting_id}")
        return self._artifacts.get(meeting_id, ArtifactBundle())

    async def delete_meeting(self, meeting_id: str) -> None:
        if meeting_id in self.fail_delete_for:
            raise RuntimeError(f"forced delete failure: {meeting_id}")
        if meeting_id not in self._meetings:
            return
        self.deleted.append(meeting_id)
        del self._meetings[meeting_id]
