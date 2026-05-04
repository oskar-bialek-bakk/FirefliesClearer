"""AuditService — read-only audit queries over the manifest for CLI and web UI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from firefliesclearer.core.manifest import Manifest, StateLogEntry
from firefliesclearer.core.models import MeetingState

__all__ = [
    "FAILED_STATES",
    "AuditService",
    "HistoryEntry",
    "HistoryFilter",
    "StateSummary",
]

# Single source of truth for "needs attention" / "retry-able" states.
# Anywhere a UI or route enumerates failed states, it should pull from here
# (or its ``.value`` projection ``FAILED_STATE_VALUES``) so adding a new
# failed state never silently desyncs the dashboard count, the retry-all
# eligibility check, the history filter, and the template's "show Retry
# button" predicate.
FAILED_STATES: tuple[MeetingState, ...] = (
    MeetingState.FAILED_FETCH,
    MeetingState.FAILED_DOWNLOAD,
    MeetingState.FAILED_RENDER,
    MeetingState.FAILED_VERIFY,
    MeetingState.DELETED_FAILED,
)

# Backward-compat alias for any downstream code still importing the
# private name (kept for one release cycle; safe to remove afterwards).
_FAILED_STATES = FAILED_STATES


@dataclass(frozen=True, slots=True)
class StateSummary:
    """Snapshot of manifest health for the status view."""

    counts_by_state: Mapping[MeetingState, int]
    last_run_at: datetime | None
    failed_meeting_ids: tuple[str, ...]
    last_errors: Mapping[str, str]

    @property
    def failed_count(self) -> int:
        """Total count of meetings in any failed state (across _FAILED_STATES)."""
        return sum(self.counts_by_state.get(s, 0) for s in _FAILED_STATES)

    @property
    def total_count(self) -> int:
        """Total meetings in the manifest across every state.

        Includes freshly-synced ``KNOWN`` rows that don't surface in any of
        the action-oriented cards (Archived / Pending / Failed / Deleted) —
        those are the bulk of what shows up after a sync, and the user
        otherwise has no single number telling them how much they've got
        cached.
        """
        return sum(self.counts_by_state.values())


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One row in the history view — a subset of MeetingRecord fields."""

    meeting_id: str
    title: str
    meeting_date: datetime
    state: MeetingState
    archive_path: str | None
    archived_at: datetime | None
    deleted_at: datetime | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class HistoryFilter:
    """Filter parameters for history / history_count queries."""

    states: tuple[MeetingState, ...] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    title_contains: str | None = None
    limit: int = 50
    offset: int = 0


class AuditService:
    """Read-only audit queries over the manifest.

    Parameters
    ----------
    manifest:
        The SQLite-backed manifest; the single data source for all audit reads.
    """

    def __init__(self, *, manifest: Manifest) -> None:
        self._manifest = manifest

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def summary(self) -> StateSummary:
        """Return a StateSummary snapshot of the current manifest state."""
        counts = self._manifest.counts_by_state()
        last_run_at = self._manifest.last_state_change_at()

        failed_ids_list = self._manifest.meeting_ids_in_states(_FAILED_STATES)
        failed_meeting_ids = tuple(failed_ids_list)

        last_errors: dict[str, str] = {}
        for mid in failed_meeting_ids:
            rec = self._manifest.get(mid)
            if rec is not None and rec.last_error:
                last_errors[mid] = rec.last_error

        return StateSummary(
            counts_by_state=MappingProxyType(counts),
            last_run_at=last_run_at,
            failed_meeting_ids=failed_meeting_ids,
            last_errors=MappingProxyType(last_errors),
        )

    def history(self, filt: HistoryFilter) -> tuple[HistoryEntry, ...]:
        """Return HistoryEntry objects matching *filt*, in deleted_at DESC order."""
        records = self._manifest.query_history(
            states=list(filt.states) if filt.states is not None else None,
            date_from=filt.date_from,
            date_to=filt.date_to,
            title_contains=filt.title_contains,
            limit=filt.limit,
            offset=filt.offset,
        )
        return tuple(
            HistoryEntry(
                meeting_id=r.meeting_id,
                title=r.title,
                meeting_date=r.meeting_date,
                state=r.state,
                archive_path=r.archive_path,
                archived_at=r.archived_at,
                deleted_at=r.deleted_at,
                last_error=r.last_error,
            )
            for r in records
        )

    def history_count(self, filt: HistoryFilter) -> int:
        """Return total count matching *filt*, ignoring limit and offset."""
        return self._manifest.count_history(
            states=list(filt.states) if filt.states is not None else None,
            date_from=filt.date_from,
            date_to=filt.date_to,
            title_contains=filt.title_contains,
        )

    def state_log(self, meeting_id: str) -> list[StateLogEntry]:
        """Return state log entries for *meeting_id* in chronological order."""
        return self._manifest.state_log(meeting_id)
