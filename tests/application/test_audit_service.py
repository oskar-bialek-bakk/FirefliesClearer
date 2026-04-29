"""Tests for AuditService — read-only audit queries over the manifest."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from firefliesclearer.application.audit_service import (
    AuditService,
    HistoryEntry,
    HistoryFilter,
    StateSummary,
)
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting, MeetingState

NOW = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meeting(mid: str = "01HW", title: str = "Standup") -> Meeting:
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


def _open_manifest(tmp_path: Path) -> Manifest:
    return Manifest.open(tmp_path / "manifest.db")


def _build(tmp_path: Path) -> tuple[AuditService, Manifest]:
    manifest = _open_manifest(tmp_path)
    svc = AuditService(manifest)
    return svc, manifest


# ---------------------------------------------------------------------------
# StateSummary frozen
# ---------------------------------------------------------------------------


def test_state_summary_is_frozen() -> None:
    summary = StateSummary(
        counts_by_state={},
        last_run_at=None,
        failed_meeting_ids=(),
        last_errors={},
    )
    with pytest.raises((AttributeError, TypeError)):
        summary.last_run_at = NOW  # type: ignore[misc]


def test_history_entry_is_frozen() -> None:
    entry = HistoryEntry(
        meeting_id="x",
        title="T",
        meeting_date=NOW,
        state=MeetingState.DELETED,
        archive_path=None,
        archived_at=None,
        deleted_at=None,
        last_error=None,
    )
    with pytest.raises((AttributeError, TypeError)):
        entry.meeting_id = "y"  # type: ignore[misc]


def test_history_filter_is_frozen() -> None:
    filt = HistoryFilter()
    with pytest.raises((AttributeError, TypeError)):
        filt.limit = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# summary()
# ---------------------------------------------------------------------------


def test_summary_returns_zero_counts_for_empty_manifest(tmp_path: Path) -> None:
    svc, _ = _build(tmp_path)
    summary = svc.summary()
    assert summary.counts_by_state == {}
    assert summary.last_run_at is None
    assert summary.failed_meeting_ids == ()
    assert summary.last_errors == {}


def test_summary_counts_each_state(tmp_path: Path) -> None:
    svc, manifest = _build(tmp_path)
    manifest.register(_meeting("a"), at=NOW)
    manifest.register(_meeting("b"), at=NOW)
    manifest.transition("a", to=MeetingState.ARCHIVED, at=NOW)
    manifest.transition("a", to=MeetingState.DELETED, at=NOW)

    summary = svc.summary()
    assert summary.counts_by_state.get(MeetingState.PENDING, 0) == 1
    assert summary.counts_by_state.get(MeetingState.DELETED, 0) == 1


def test_summary_last_run_at_is_max_state_log_at(tmp_path: Path) -> None:
    t1 = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 4, 5, 15, 0, tzinfo=UTC)
    svc, manifest = _build(tmp_path)
    manifest.register(_meeting("a"), at=t1)
    manifest.register(_meeting("b"), at=t2)

    summary = svc.summary()
    assert summary.last_run_at == t2


def test_summary_collects_failed_ids_and_last_errors(tmp_path: Path) -> None:
    svc, manifest = _build(tmp_path)
    manifest.register(_meeting("a"), at=NOW)
    manifest.register(_meeting("b"), at=NOW)
    manifest.transition("a", to=MeetingState.FAILED_FETCH, at=NOW, last_error="fetch failed")
    manifest.transition("b", to=MeetingState.FAILED_DOWNLOAD, at=NOW, last_error="download err")

    summary = svc.summary()
    assert set(summary.failed_meeting_ids) == {"a", "b"}
    assert summary.last_errors["a"] == "fetch failed"
    assert summary.last_errors["b"] == "download err"


def test_summary_excludes_meetings_with_no_last_error_from_last_errors(tmp_path: Path) -> None:
    svc, manifest = _build(tmp_path)
    manifest.register(_meeting("a"), at=NOW)
    # transition without a last_error
    manifest.transition("a", to=MeetingState.FAILED_VERIFY, at=NOW)

    summary = svc.summary()
    assert "a" in summary.failed_meeting_ids
    assert "a" not in summary.last_errors


# ---------------------------------------------------------------------------
# history()
# ---------------------------------------------------------------------------


def test_history_returns_filtered_entries_with_correct_fields_mapped(tmp_path: Path) -> None:
    svc, manifest = _build(tmp_path)
    manifest.register(_meeting("a", "My Meeting"), at=NOW)
    manifest.transition("a", to=MeetingState.ARCHIVED, at=NOW)
    manifest.transition("a", to=MeetingState.DELETED, at=NOW)

    filt = HistoryFilter(states=(MeetingState.DELETED,))
    entries = svc.history(filt)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.meeting_id == "a"
    assert entry.title == "My Meeting"
    assert entry.state is MeetingState.DELETED
    assert entry.deleted_at is not None
    assert entry.meeting_date == NOW


def test_history_empty_when_no_match(tmp_path: Path) -> None:
    svc, manifest = _build(tmp_path)
    manifest.register(_meeting("a"), at=NOW)  # pending, not deleted

    filt = HistoryFilter(states=(MeetingState.DELETED,))
    entries = svc.history(filt)

    assert entries == ()


def test_history_count_matches_full_query_total_ignoring_pagination(tmp_path: Path) -> None:
    svc, manifest = _build(tmp_path)
    for mid in ("a", "b", "c"):
        manifest.register(_meeting(mid), at=NOW)
        manifest.transition(mid, to=MeetingState.ARCHIVED, at=NOW)
        manifest.transition(mid, to=MeetingState.DELETED, at=NOW)

    filt = HistoryFilter(states=(MeetingState.DELETED,), limit=1, offset=0)
    paged_entries = svc.history(filt)
    total_count = svc.history_count(filt)

    assert len(paged_entries) == 1
    assert total_count == 3


# ---------------------------------------------------------------------------
# state_log()
# ---------------------------------------------------------------------------


def test_state_log_returns_entries_for_meeting_in_chronological_order(tmp_path: Path) -> None:
    t1 = datetime(2026, 4, 1, tzinfo=UTC)
    t2 = datetime(2026, 4, 2, tzinfo=UTC)
    svc, manifest = _build(tmp_path)
    manifest.register(_meeting("a"), at=t1)
    manifest.transition("a", to=MeetingState.ARCHIVED, at=t2)

    log = list(svc.state_log("a"))

    assert len(log) == 2
    assert log[0].to_state is MeetingState.PENDING
    assert log[1].to_state is MeetingState.ARCHIVED
