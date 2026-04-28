"""Meeting repository port: read meetings, fetch artifacts, delete."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from firefliesclearer.core.models import ArtifactBundle, Meeting


@dataclass(frozen=True, slots=True)
class MeetingFilter:
    """Boundary filter — narrow before pagination at the source if possible."""

    older_than: datetime | None = None
    limit: int | None = None


class MeetingRepository(Protocol):
    def list_meetings(self, filter: MeetingFilter) -> AsyncIterator[Meeting]: ...

    async def fetch_artifacts(self, meeting_id: str) -> ArtifactBundle: ...

    async def delete_meeting(self, meeting_id: str) -> None: ...
