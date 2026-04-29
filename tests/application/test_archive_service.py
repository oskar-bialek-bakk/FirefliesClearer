"""Tests for ArchiveService — selection-file and direct-meeting-iterable paths."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from firefliesclearer.application.archive_service import (
    ArchiveOutcome,
    ArchiveService,
    SelectionMissing,
)
from firefliesclearer.core.archiver import Archiver
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import ArtifactBundle, Meeting, MeetingState
from firefliesclearer.core.pipeline import Pipeline
from tests.fakes.fake_renderer import FakeSummaryRenderer
from tests.fakes.frozen_clock import FrozenClock
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meeting(mid: str = "01HW", title: str = "Sync") -> Meeting:
    return Meeting(
        meeting_id=mid,
        title=title,
        meeting_date=NOW,
        duration_minutes=10.0,
        host_email="u@x.com",
        participant_count=2,
        tags=(),
        has_transcript=True,
    )


def _bundle() -> ArtifactBundle:
    return ArtifactBundle(
        audio_bytes=b"AUDIO",
        transcript_markdown="# T",
        summary_payload={"overview": "ov"},
    )


def _build(
    tmp_path: Path,
    meetings: list[Meeting],
    **kw: Any,
) -> tuple[ArchiveService, Pipeline, InMemoryMeetingRepository, Manifest]:
    repo = InMemoryMeetingRepository(
        meetings=list(meetings),
        artifacts={m.meeting_id: _bundle() for m in meetings},
    )
    repo.fail_fetch_for = set(kw.get("fail_fetch_for", []))
    manifest = Manifest.open(tmp_path / "manifest.db")
    archiver = Archiver(archive_root=tmp_path)
    renderer = FakeSummaryRenderer()
    clock = FrozenClock(NOW)
    pipeline = Pipeline(
        repository=repo,
        manifest=manifest,
        archiver=archiver,
        renderer=renderer,
        clock=clock,
    )
    svc = ArchiveService(pipeline=pipeline, manifest=manifest)
    return svc, pipeline, repo, manifest


def _write_selection(
    path: Path,
    meetings: list[Meeting],
    *,
    select_all: bool = True,
    deselect_ids: set[str] | None = None,
) -> Path:
    deselect_ids = deselect_ids or set()
    payload = {
        "scan_id": "scan_test",
        "created_at": NOW.isoformat(),
        "filters_applied": {},
        "meetings": [
            {
                "id": m.meeting_id,
                "title": m.title,
                "date": m.meeting_date.isoformat(),
                "duration_min": m.duration_minutes,
                "host": m.host_email,
                "participants": m.participant_count,
                "tags": [],
                "selected": select_all and m.meeting_id not in deselect_ids,
                "matched_rules": [],
            }
            for m in meetings
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# SelectionMissing
# ---------------------------------------------------------------------------


def test_selection_missing_is_file_not_found_error() -> None:
    """SelectionMissing must be a subclass of FileNotFoundError."""
    assert issubclass(SelectionMissing, FileNotFoundError)


async def test_archive_selection_raises_selection_missing_for_nonexistent_file(
    tmp_path: Path,
) -> None:
    """archive_selection raises SelectionMissing for a missing selection file."""
    m = _meeting()
    svc, _, _, _ = _build(tmp_path, [m])
    missing = tmp_path / "nope.json"

    with pytest.raises(SelectionMissing):
        async for _ in svc.archive_selection(missing):
            pass


# ---------------------------------------------------------------------------
# archive_selection (selection file path)
# ---------------------------------------------------------------------------


async def test_archive_selection_yields_one_outcome_per_selected_meeting(
    tmp_path: Path,
) -> None:
    """archive_selection yields one ArchiveOutcome per `selected:true` meeting."""
    m1, m2 = _meeting("a", "Alpha"), _meeting("b", "Beta")
    svc, _, _, _ = _build(tmp_path, [m1, m2])
    sel = _write_selection(tmp_path / "sel.json", [m1, m2])

    outcomes = [o async for o in svc.archive_selection(sel)]

    assert len(outcomes) == 2
    ids = {o.meeting_id for o in outcomes}
    assert ids == {"a", "b"}
    assert all(o.state is MeetingState.ARCHIVED for o in outcomes)
    assert all(o.error is None for o in outcomes)


async def test_archive_selection_skips_unselected_meetings(tmp_path: Path) -> None:
    """archive_selection skips meetings with `selected:false`."""
    m1, m2 = _meeting("a"), _meeting("b")
    svc, _, _, _ = _build(tmp_path, [m1, m2])
    sel = _write_selection(tmp_path / "sel.json", [m1, m2], deselect_ids={"b"})

    outcomes = [o async for o in svc.archive_selection(sel)]

    assert len(outcomes) == 1
    assert outcomes[0].meeting_id == "a"


async def test_archive_selection_empty_selection_yields_nothing(tmp_path: Path) -> None:
    """archive_selection with all meetings deselected yields no outcomes."""
    m = _meeting()
    svc, _, _, _ = _build(tmp_path, [m])
    sel = _write_selection(tmp_path / "sel.json", [m], select_all=False)

    outcomes = [o async for o in svc.archive_selection(sel)]

    assert outcomes == []


async def test_archive_selection_outcome_carries_failed_state_and_error(
    tmp_path: Path,
) -> None:
    """When archive_one returns a failed state, outcome carries the error."""
    m = _meeting()
    svc, _, _, _ = _build(tmp_path, [m], fail_fetch_for=[m.meeting_id])
    sel = _write_selection(tmp_path / "sel.json", [m])

    outcomes = [o async for o in svc.archive_selection(sel)]

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.state is MeetingState.FAILED_FETCH
    assert outcome.error is not None
    assert len(outcome.error) > 0


# ---------------------------------------------------------------------------
# archive_meetings (direct Meeting iterable — web path)
# ---------------------------------------------------------------------------


async def test_archive_meetings_yields_one_outcome_per_meeting(tmp_path: Path) -> None:
    """archive_meetings yields one ArchiveOutcome per Meeting passed in."""
    m1, m2 = _meeting("a"), _meeting("b")
    svc, _, _, _ = _build(tmp_path, [m1, m2])

    outcomes = [o async for o in svc.archive_meetings([m1, m2])]

    assert len(outcomes) == 2
    assert all(o.state is MeetingState.ARCHIVED for o in outcomes)


async def test_archive_meetings_empty_list_yields_nothing(tmp_path: Path) -> None:
    """archive_meetings on empty iterable yields no outcomes."""
    svc, _, _, _ = _build(tmp_path, [])

    outcomes = [o async for o in svc.archive_meetings([])]

    assert outcomes == []


async def test_archive_meetings_carries_failure_on_repo_error(tmp_path: Path) -> None:
    """archive_meetings wraps pipeline failure into ArchiveOutcome with error."""
    m = _meeting()
    svc, _, _repo, _ = _build(tmp_path, [m], fail_fetch_for=[m.meeting_id])

    outcomes = [o async for o in svc.archive_meetings([m])]

    assert len(outcomes) == 1
    assert outcomes[0].state is MeetingState.FAILED_FETCH
    assert outcomes[0].error is not None


async def test_archive_meetings_captures_unexpected_exception(tmp_path: Path) -> None:
    """archive_meetings catches any unexpected exception and returns FAILED_FETCH outcome."""
    m = _meeting()
    svc, pipeline, _, _ = _build(tmp_path, [m])

    async def boom(_meeting: Meeting) -> MeetingState:
        raise RuntimeError("unexpected boom")

    pipeline.archive_one = boom  # type: ignore[method-assign]

    outcomes = [o async for o in svc.archive_meetings([m])]

    assert len(outcomes) == 1
    assert outcomes[0].state is MeetingState.FAILED_FETCH
    assert "unexpected boom" in (outcomes[0].error or "")


# ---------------------------------------------------------------------------
# ArchiveOutcome value object
# ---------------------------------------------------------------------------


def test_archive_outcome_is_frozen() -> None:
    """ArchiveOutcome is a frozen dataclass."""
    outcome = ArchiveOutcome(
        meeting_id="x",
        title="T",
        state=MeetingState.ARCHIVED,
        error=None,
    )
    with pytest.raises((AttributeError, TypeError)):
        outcome.meeting_id = "y"  # type: ignore[misc]
