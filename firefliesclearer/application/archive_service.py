"""ArchiveService — runs the archive half of the pipeline for selected meetings."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from firefliesclearer.application.exceptions import SelectionMissing
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.core.pipeline import Pipeline

__all__ = ["ArchiveOutcome", "ArchiveService", "SelectionMissing"]


@dataclass(frozen=True, slots=True)
class ArchiveOutcome:
    meeting_id: str
    title: str
    state: MeetingState
    error: str | None


class ArchiveService:
    """Orchestrates archiving of selected meetings for CLI and web UI.

    Parameters
    ----------
    pipeline:
        The configured Pipeline instance (repository, manifest, archiver, etc.).
    manifest:
        The shared Manifest used to read error details after a failed archive.
    """

    def __init__(self, *, pipeline: Pipeline, manifest: Manifest) -> None:
        self._pipeline = pipeline
        self._manifest = manifest

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def archive_selection(self, selection_file: Path) -> AsyncIterator[ArchiveOutcome]:
        """Archive every ``selected:true`` meeting listed in *selection_file*.

        Parameters
        ----------
        selection_file:
            Path to the JSON selection file produced by ScanService (or v1
            scan_cmd).  The file must exist.

        Yields
        ------
        ArchiveOutcome
            One outcome per selected meeting.

        Raises
        ------
        SelectionMissing
            If *selection_file* does not exist.
        """
        if not selection_file.exists():
            raise SelectionMissing(str(selection_file))
        meetings = self._meetings_from_selection(selection_file)
        async for outcome in self.archive_meetings(meetings):
            yield outcome

    async def archive_meetings(self, meetings: Iterable[Meeting]) -> AsyncIterator[ArchiveOutcome]:
        """Archive an arbitrary iterable of Meeting objects.

        Used by the web layer which already holds Meeting objects in its
        operation registry.

        Yields
        ------
        ArchiveOutcome
            One outcome per meeting.
        """
        for meeting in meetings:
            try:
                state = await self._pipeline.archive_one(meeting)
                error: str | None = None
                if state.value.startswith("failed_"):
                    rec = self._manifest.get(meeting.meeting_id)
                    error = rec.last_error if rec else None
                yield ArchiveOutcome(
                    meeting_id=meeting.meeting_id,
                    title=meeting.title,
                    state=state,
                    error=error,
                )
            except Exception as exc:  # defensive boundary
                yield ArchiveOutcome(
                    meeting_id=meeting.meeting_id,
                    title=meeting.title,
                    state=MeetingState.FAILED_FETCH,
                    error=str(exc),
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _meetings_from_selection(selection_file: Path) -> list[Meeting]:
        """Parse a selection JSON file and return selected Meeting objects."""
        payload = json.loads(selection_file.read_text(encoding="utf-8"))
        out: list[Meeting] = []
        for entry in payload.get("meetings", []):
            if not entry.get("selected", True):
                continue
            out.append(
                Meeting(
                    meeting_id=entry["id"],
                    title=entry["title"],
                    meeting_date=datetime.fromisoformat(entry["date"]),
                    duration_minutes=float(entry.get("duration_min", 0.0)),
                    host_email=entry.get("host", ""),
                    participant_count=int(entry.get("participants", 0)),
                    tags=tuple(entry.get("tags", [])),
                    has_transcript=True,
                )
            )
        return out
