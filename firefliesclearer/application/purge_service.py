"""PurgeService — runs the purge half of the pipeline for selected meetings."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path

from firefliesclearer.application.archive_service import ArchiveService
from firefliesclearer.application.exceptions import SelectionMissing
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.core.pipeline import Pipeline

__all__ = ["PurgeOutcome", "PurgeService", "SelectionMissing"]


@dataclass(frozen=True, slots=True)
class PurgeOutcome:
    meeting_id: str
    title: str
    state: MeetingState
    error: str | None


class PurgeService:
    """Orchestrates purging of selected meetings for CLI and web UI.

    Parameters
    ----------
    pipeline:
        The configured Pipeline instance (repository, manifest, archiver, etc.).
    manifest:
        The shared Manifest used to read error details after a failed purge.
    """

    def __init__(self, *, pipeline: Pipeline, manifest: Manifest) -> None:
        self._pipeline = pipeline
        self._manifest = manifest

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def purge_selection(self, selection_file: Path) -> AsyncIterator[PurgeOutcome]:
        """Purge every ``selected:true`` meeting listed in *selection_file*.

        Parameters
        ----------
        selection_file:
            Path to the JSON selection file produced by ScanService (or v1
            scan_cmd).  The file must exist.

        Yields
        ------
        PurgeOutcome
            One outcome per selected meeting.

        Raises
        ------
        SelectionMissing
            If *selection_file* does not exist.
        """
        if not selection_file.exists():
            raise SelectionMissing(str(selection_file))
        meetings = ArchiveService._meetings_from_selection(selection_file)
        async for outcome in self.purge_meetings(meetings):
            yield outcome

    async def purge_meetings(self, meetings: Iterable[Meeting]) -> AsyncIterator[PurgeOutcome]:
        """Purge an arbitrary iterable of Meeting objects.

        Used by the web layer which already holds Meeting objects in its
        operation registry.

        Yields
        ------
        PurgeOutcome
            One outcome per meeting.
        """
        for meeting in meetings:
            try:
                state = await self._pipeline.purge_one(meeting)
                error: str | None = None
                if state is MeetingState.DELETED_FAILED:
                    rec = self._manifest.get(meeting.meeting_id)
                    error = rec.last_error if rec else None
                yield PurgeOutcome(
                    meeting_id=meeting.meeting_id,
                    title=meeting.title,
                    state=state,
                    error=error,
                )
            except Exception as exc:  # defensive boundary
                yield PurgeOutcome(
                    meeting_id=meeting.meeting_id,
                    title=meeting.title,
                    state=MeetingState.DELETED_FAILED,
                    error=str(exc),
                )
