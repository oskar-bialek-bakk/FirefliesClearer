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
        api_key: str | None = None,
    ) -> None:
        self._meetings: dict[str, Meeting] = {m.meeting_id: m for m in (meetings or [])}
        self._artifacts: dict[str, ArtifactBundle] = artifacts or {}
        self.deleted: list[str] = []
        self.fail_fetch_for: set[str] = set()
        self.fail_delete_for: set[str] = set()
        self._api_key: str | None = api_key
        self._key_to_email: dict[str, str] = {}

    def set_user_email_for_key(self, api_key: str, email: str) -> None:
        """Register a (key → email) pair so ping_user() can verify it."""
        self._key_to_email[api_key] = email

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

    async def ping_user(self) -> str:
        """Return the email for the registered API key, or raise PermissionError."""
        if self._api_key is None or self._api_key not in self._key_to_email:
            raise PermissionError("Unknown API key")
        return self._key_to_email[self._api_key]
