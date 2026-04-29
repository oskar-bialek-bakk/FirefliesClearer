"""ScanService — shared scan orchestration for CLI and web UI."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from firefliesclearer.core.models import Meeting
from firefliesclearer.core.rules import (
    DurationBelow,
    HasTag,
    HostEmail,
    NoTranscript,
    OlderThanDays,
    ParticipantsBelow,
    Rule,
    RuleEngine,
    TitleContains,
    TitleRegex,
)
from firefliesclearer.ports.clock import Clock
from firefliesclearer.ports.meeting_repository import MeetingFilter, MeetingRepository

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScanFilters:
    """All filter criteria for a single scan run.

    All fields default to "inactive" so callers can construct a filters
    object and then check ``is_empty()`` before running.
    """

    older_than_days: int | None = None
    duration_below_minutes: float | None = None
    no_transcript: bool = False
    title_contains: Sequence[str] = field(default_factory=tuple)
    title_regex: str | None = None
    host_email: Sequence[str] = field(default_factory=tuple)
    participants_below: int | None = None
    has_tag: Sequence[str] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        """Return True when no filter criterion is active."""
        return (
            self.older_than_days is None
            and self.duration_below_minutes is None
            and not self.no_transcript
            and not self.title_contains
            and self.title_regex is None
            and not self.host_email
            and self.participants_below is None
            and not self.has_tag
        )


@dataclass(frozen=True, slots=True)
class ScanMatch:
    """A single meeting that satisfied all active filter rules."""

    meeting: Meeting
    matched_rules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScanResult:
    """The outcome of one ``ScanService.scan()`` call."""

    scan_id: str
    created_at: datetime
    filters: ScanFilters
    matches: tuple[ScanMatch, ...]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ScanService:
    """Orchestrates meeting scanning and selection-file persistence.

    Parameters
    ----------
    repo:
        The meeting repository to query.
    clock:
        Time source; injected so tests can freeze time.
    """

    def __init__(self, repo: MeetingRepository, clock: Clock) -> None:
        self._repo = repo
        self._clock = clock

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan(self, filters: ScanFilters) -> ScanResult:
        """Evaluate *filters* against every meeting in the repository.

        Parameters
        ----------
        filters:
            The filter criteria to apply.

        Returns
        -------
        ScanResult
            All meetings that matched every active rule together with
            metadata about the scan.

        Raises
        ------
        ValueError
            If *filters* is empty (no criterion active).
        """
        if filters.is_empty():
            raise ValueError("at least one filter must be active")

        now = self._clock.now()
        rules = self._build_rules(filters)
        engine = RuleEngine(cast("list[Rule]", rules))

        cutoff = (
            now - timedelta(days=filters.older_than_days)
            if filters.older_than_days is not None
            else None
        )

        matches: list[ScanMatch] = []
        async for meeting in self._repo.list_meetings(MeetingFilter(older_than=cutoff)):
            result = engine.evaluate(meeting, now=now)
            if result.matched:
                matches.append(ScanMatch(meeting=meeting, matched_rules=result.reasons))

        scan_id = f"scan_{now.strftime('%Y%m%dT%H%M')}"
        return ScanResult(
            scan_id=scan_id,
            created_at=now,
            filters=filters,
            matches=tuple(matches),
        )

    def write_selection_file(self, result: ScanResult, selections_dir: Path) -> Path:
        """Persist a selection file compatible with the v1 JSON shape.

        Parameters
        ----------
        result:
            The result of a previous ``scan()`` call.
        selections_dir:
            Directory in which to write the ``<scan_id>.json`` file.
            Created automatically if it does not exist.

        Returns
        -------
        Path
            The path of the written file.
        """
        selections_dir.mkdir(parents=True, exist_ok=True)
        f = result.filters
        payload: dict[str, object] = {
            "scan_id": result.scan_id,
            "created_at": result.created_at.isoformat(),
            "filters_applied": {
                "older_than_days": f.older_than_days,
                "duration_below_minutes": f.duration_below_minutes,
                "no_transcript": f.no_transcript,
                "title_contains": list(f.title_contains) if f.title_contains else None,
                "title_regex": f.title_regex,
                "host_email": list(f.host_email) if f.host_email else None,
                "participants_below": f.participants_below,
                "has_tag": list(f.has_tag) if f.has_tag else None,
            },
            "meetings": [
                {
                    "id": m.meeting.meeting_id,
                    "title": m.meeting.title,
                    "date": m.meeting.meeting_date.isoformat(),
                    "duration_min": m.meeting.duration_minutes,
                    "host": m.meeting.host_email,
                    "participants": m.meeting.participant_count,
                    "tags": list(m.meeting.tags),
                    "selected": True,
                    "matched_rules": list(m.matched_rules),
                }
                for m in result.matches
            ],
        }
        target = selections_dir / f"{result.scan_id}.json"
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_rules(filters: ScanFilters) -> list[object]:
        """Construct the list of Rule instances from active filter fields."""
        rules: list[object] = []
        if filters.older_than_days is not None:
            rules.append(OlderThanDays(filters.older_than_days))
        if filters.duration_below_minutes is not None:
            rules.append(DurationBelow(filters.duration_below_minutes))
        if filters.no_transcript:
            rules.append(NoTranscript())
        if filters.title_contains:
            rules.append(TitleContains(filters.title_contains))
        if filters.title_regex is not None:
            rules.append(TitleRegex(filters.title_regex))
        if filters.host_email:
            rules.append(HostEmail(filters.host_email))
        if filters.participants_below is not None:
            rules.append(ParticipantsBelow(filters.participants_below))
        if filters.has_tag:
            rules.append(HasTag(filters.has_tag))
        return rules
