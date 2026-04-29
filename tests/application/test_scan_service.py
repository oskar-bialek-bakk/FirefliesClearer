"""Tests for ScanService — scan() and write_selection_file()."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from firefliesclearer.application.scan_service import (
    ScanFilters,
    ScanService,
)
from firefliesclearer.core.models import Meeting
from tests.fakes.frozen_clock import FrozenClock
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


def _make_meeting(
    mid: str,
    *,
    days_old: int = 10,
    duration_minutes: float = 30.0,
    host_email: str = "host@example.com",
    participant_count: int = 3,
    tags: tuple[str, ...] = (),
    has_transcript: bool = True,
    title: str = "Regular Meeting",
) -> Meeting:
    return Meeting(
        meeting_id=mid,
        title=title,
        meeting_date=NOW - timedelta(days=days_old),
        duration_minutes=duration_minutes,
        host_email=host_email,
        participant_count=participant_count,
        tags=tags,
        has_transcript=has_transcript,
    )


def _make_service(meetings: list[Meeting]) -> ScanService:
    repo = InMemoryMeetingRepository(meetings=meetings)
    clock = FrozenClock(NOW)
    return ScanService(repo=repo, clock=clock)


# ---------------------------------------------------------------------------
# scan() — filter behaviour
# ---------------------------------------------------------------------------


async def test_scan_older_than_days_matches_old_meeting_only() -> None:
    old = _make_meeting("old", days_old=200)
    recent = _make_meeting("recent", days_old=10)
    svc = _make_service([old, recent])

    result = await svc.scan(ScanFilters(older_than_days=180))

    assert len(result.matches) == 1
    assert result.matches[0].meeting.meeting_id == "old"
    assert "older_than_days" in result.matches[0].matched_rules


async def test_scan_empty_filters_raises_value_error() -> None:
    svc = _make_service([])

    with pytest.raises(ValueError, match="at least one filter"):
        await svc.scan(ScanFilters())


async def test_scan_combined_filters_records_all_matched_rules() -> None:
    """A meeting that satisfies both older_than_days AND duration_below_minutes
    should have both rule names in matched_rules."""
    target = _make_meeting("m1", days_old=200, duration_minutes=5.0)
    svc = _make_service([target])

    filters = ScanFilters(older_than_days=180, duration_below_minutes=10.0)
    result = await svc.scan(filters)

    assert len(result.matches) == 1
    match = result.matches[0]
    assert "older_than_days" in match.matched_rules
    assert "duration_below_minutes" in match.matched_rules


async def test_scan_no_transcript_flag_matches_meetings_without_transcript() -> None:
    with_transcript = _make_meeting("has", has_transcript=True, days_old=1)
    without_transcript = _make_meeting("no", has_transcript=False, days_old=1)
    svc = _make_service([with_transcript, without_transcript])

    result = await svc.scan(ScanFilters(no_transcript=True))

    ids = [m.meeting.meeting_id for m in result.matches]
    assert "no" in ids
    assert "has" not in ids


async def test_scan_title_contains_filter() -> None:
    match = _make_meeting("m1", title="Daily Standup", days_old=1)
    no_match = _make_meeting("m2", title="Sprint Review", days_old=1)
    svc = _make_service([match, no_match])

    result = await svc.scan(ScanFilters(title_contains=["standup"]))

    assert len(result.matches) == 1
    assert result.matches[0].meeting.meeting_id == "m1"


async def test_scan_host_email_filter() -> None:
    target = _make_meeting("m1", host_email="boss@corp.com", days_old=1)
    other = _make_meeting("m2", host_email="other@corp.com", days_old=1)
    svc = _make_service([target, other])

    result = await svc.scan(ScanFilters(host_email=["boss@corp.com"]))

    assert len(result.matches) == 1
    assert result.matches[0].meeting.meeting_id == "m1"


async def test_scan_has_tag_filter() -> None:
    tagged = _make_meeting("m1", tags=("sales", "followup"), days_old=1)
    untagged = _make_meeting("m2", tags=(), days_old=1)
    svc = _make_service([tagged, untagged])

    result = await svc.scan(ScanFilters(has_tag=["sales"]))

    assert len(result.matches) == 1
    assert result.matches[0].meeting.meeting_id == "m1"


async def test_scan_result_metadata() -> None:
    meeting = _make_meeting("m1", days_old=200)
    svc = _make_service([meeting])

    result = await svc.scan(ScanFilters(older_than_days=180))

    assert result.scan_id.startswith("scan_")
    assert result.created_at == NOW
    assert result.filters.older_than_days == 180


# ---------------------------------------------------------------------------
# write_selection_file()
# ---------------------------------------------------------------------------


async def test_write_selection_file_creates_json_at_correct_path(tmp_path: Path) -> None:
    meeting = _make_meeting("m1", days_old=200)
    svc = _make_service([meeting])

    result = await svc.scan(ScanFilters(older_than_days=180))
    sel_dir = tmp_path / "selections"
    written = svc.write_selection_file(result, sel_dir)

    assert written.exists()
    assert written.name == f"{result.scan_id}.json"
    assert written.parent == sel_dir


async def test_write_selection_file_shape_matches_v1(tmp_path: Path) -> None:
    """JSON shape must be byte-compatible with the v1 scan_cmd output."""
    meeting = _make_meeting(
        "abc123",
        days_old=200,
        duration_minutes=25.5,
        host_email="h@example.com",
        participant_count=4,
        tags=("tag1",),
    )
    svc = _make_service([meeting])

    result = await svc.scan(ScanFilters(older_than_days=180))
    sel_dir = tmp_path / "selections"
    written = svc.write_selection_file(result, sel_dir)

    payload = json.loads(written.read_text(encoding="utf-8"))

    # Top-level keys
    assert "scan_id" in payload
    assert "created_at" in payload
    assert "filters_applied" in payload
    assert "meetings" in payload

    # filters_applied shape
    filters = payload["filters_applied"]
    assert "older_than_days" in filters
    assert "duration_below_minutes" in filters
    assert "no_transcript" in filters
    assert "title_contains" in filters
    assert "title_regex" in filters
    assert "host_email" in filters
    assert "participants_below" in filters
    assert "has_tag" in filters

    # Meeting entry shape
    entry = payload["meetings"][0]
    assert entry["id"] == "abc123"
    assert entry["title"] == meeting.title
    assert entry["selected"] is True
    assert isinstance(entry["matched_rules"], list)
    assert "older_than_days" in entry["matched_rules"]
    assert entry["host"] == "h@example.com"
    assert entry["participants"] == 4
    assert entry["tags"] == ["tag1"]
    assert entry["duration_min"] == 25.5


async def test_write_selection_file_all_matched_meetings_selected(tmp_path: Path) -> None:
    m1 = _make_meeting("m1", days_old=200)
    m2 = _make_meeting("m2", days_old=250)
    svc = _make_service([m1, m2])

    result = await svc.scan(ScanFilters(older_than_days=180))
    sel_dir = tmp_path / "selections"
    svc.write_selection_file(result, sel_dir)

    written = next(sel_dir.glob("*.json"))
    payload = json.loads(written.read_text(encoding="utf-8"))

    assert len(payload["meetings"]) == 2
    assert all(m["selected"] is True for m in payload["meetings"])


async def test_write_selection_file_creates_parent_dirs(tmp_path: Path) -> None:
    meeting = _make_meeting("m1", days_old=200)
    svc = _make_service([meeting])

    result = await svc.scan(ScanFilters(older_than_days=180))
    deep_dir = tmp_path / "a" / "b" / "selections"
    written = svc.write_selection_file(result, deep_dir)

    assert written.exists()
