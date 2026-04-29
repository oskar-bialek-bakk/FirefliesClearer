"""FakePipeline — drop-in test double for ``firefliesclearer.core.pipeline.Pipeline``.

Used by web-layer route tests (Tasks 5.4 / 5.5 / 5.7) that need to exercise the
``ArchiveService`` / ``PurgeService`` runners without touching a real
``MeetingRepository`` / ``Archiver`` / ``SummaryRenderer`` stack.

Per-meeting outcomes are configured via the ``archive_outcomes`` /
``purge_outcomes`` mappings: ``{meeting_id: MeetingState | list[MeetingState]}``.
A list value is consumed call-by-call from the front; once exhausted the entry
falls back to the success default. A scalar value is reused for every call.
Unconfigured ids default to success (ARCHIVED / DELETED respectively).

The list form is what the retry-from-dashboard tests use to model "first call
fails, retry succeeds": ``archive_outcomes={"m1": [FAILED_DOWNLOAD, ARCHIVED]}``.
"""

from __future__ import annotations

from firefliesclearer.core.models import Meeting, MeetingState

# Type alias for readability: a per-meeting outcome can be a single state
# (reused for every call) or a list consumed call-by-call from the front.
_OutcomeValue = MeetingState | list[MeetingState]


def _next_outcome(
    table: dict[str, _OutcomeValue],
    meeting_id: str,
    default: MeetingState,
) -> MeetingState:
    """Pop the next configured outcome for *meeting_id*, falling back to *default*.

    Mutates list-valued entries in place (popping from the front). Scalar
    entries are returned without mutation. Missing entries return *default*.
    Once a list is fully consumed, subsequent calls also fall back to *default*.
    """
    value = table.get(meeting_id)
    if value is None:
        return default
    if isinstance(value, list):
        if not value:
            return default
        return value.pop(0)
    return value


class FakePipeline:
    """Minimal fake exposing the same per-meeting helpers as ``Pipeline``."""

    def __init__(
        self,
        *,
        archive_outcomes: dict[str, _OutcomeValue] | None = None,
        purge_outcomes: dict[str, _OutcomeValue] | None = None,
    ) -> None:
        self._archive_outcomes: dict[str, _OutcomeValue] = dict(archive_outcomes or {})
        self._purge_outcomes: dict[str, _OutcomeValue] = dict(purge_outcomes or {})
        self.archive_calls: list[str] = []
        self.purge_calls: list[str] = []

    def set_archive_outcome(self, meeting_id: str, outcome: _OutcomeValue) -> None:
        """Replace the configured archive outcome(s) for *meeting_id*.

        Convenience for tests that need to reconfigure the pipeline mid-flight
        (e.g. "first archive fails, second archive — the retry — succeeds").
        """
        self._archive_outcomes[meeting_id] = outcome

    def set_purge_outcome(self, meeting_id: str, outcome: _OutcomeValue) -> None:
        """Replace the configured purge outcome(s) for *meeting_id*."""
        self._purge_outcomes[meeting_id] = outcome

    async def archive_one(self, meeting: Meeting) -> MeetingState:
        self.archive_calls.append(meeting.meeting_id)
        return _next_outcome(self._archive_outcomes, meeting.meeting_id, MeetingState.ARCHIVED)

    async def purge_one(self, meeting: Meeting) -> MeetingState:
        self.purge_calls.append(meeting.meeting_id)
        return _next_outcome(self._purge_outcomes, meeting.meeting_id, MeetingState.DELETED)
