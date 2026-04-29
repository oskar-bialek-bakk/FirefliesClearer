"""FakePipeline — drop-in test double for ``firefliesclearer.core.pipeline.Pipeline``.

Used by web-layer route tests (Tasks 5.4 / 5.5) that need to exercise the
``ArchiveService`` / future ``PurgeService`` runners without touching a real
``MeetingRepository`` / ``Archiver`` / ``SummaryRenderer`` stack.

Per-meeting outcomes are configured via the ``archive_outcomes`` /
``purge_outcomes`` mappings: ``{meeting_id: MeetingState}``. Unconfigured ids
default to success (ARCHIVED / DELETED respectively).
"""

from __future__ import annotations

from firefliesclearer.core.models import Meeting, MeetingState


class FakePipeline:
    """Minimal fake exposing the same per-meeting helpers as ``Pipeline``."""

    def __init__(
        self,
        *,
        archive_outcomes: dict[str, MeetingState] | None = None,
        purge_outcomes: dict[str, MeetingState] | None = None,
    ) -> None:
        self._archive_outcomes: dict[str, MeetingState] = dict(archive_outcomes or {})
        self._purge_outcomes: dict[str, MeetingState] = dict(purge_outcomes or {})
        self.archive_calls: list[str] = []
        self.purge_calls: list[str] = []

    async def archive_one(self, meeting: Meeting) -> MeetingState:
        self.archive_calls.append(meeting.meeting_id)
        return self._archive_outcomes.get(meeting.meeting_id, MeetingState.ARCHIVED)

    async def purge_one(self, meeting: Meeting) -> MeetingState:
        self.purge_calls.append(meeting.meeting_id)
        return self._purge_outcomes.get(meeting.meeting_id, MeetingState.DELETED)
