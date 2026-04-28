"""Tests for the SQLite-backed manifest."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from firefliesclearer.core.manifest import IllegalStateTransition, Manifest
from firefliesclearer.core.models import Meeting, MeetingState

NOW = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


def _meeting(meeting_id: str = "01HW") -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title="Standup",
        meeting_date=NOW,
        duration_minutes=10.0,
        host_email="u@x.com",
        participant_count=2,
        tags=(),
        has_transcript=True,
    )


@pytest.fixture
def manifest(tmp_path):
    return Manifest.open(tmp_path / "manifest.db")


def test_open_creates_schema(tmp_path):
    db_path = tmp_path / "manifest.db"
    Manifest.open(db_path)
    assert db_path.exists()


def test_register_new_meeting_starts_pending(manifest):
    manifest.register(_meeting(), at=NOW)
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.state is MeetingState.PENDING


def test_register_is_idempotent_for_existing_id(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.register(_meeting(), at=NOW)
    assert manifest.get("01HW") is not None


def test_get_returns_none_for_unknown(manifest):
    assert manifest.get("missing") is None


def test_legal_transition_pending_to_archived(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.transition(
        "01HW",
        to=MeetingState.ARCHIVED,
        at=NOW,
        archive_path="/tmp/x",
        verified_at=NOW,
        sha256s={"audio": "a", "summary": "s", "transcript": "t"},
    )
    rec = manifest.get("01HW")
    assert rec.state is MeetingState.ARCHIVED
    assert rec.archive_path == "/tmp/x"
    assert rec.audio_sha256 == "a"


def test_legal_transition_archived_to_deleted(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW)
    manifest.transition("01HW", to=MeetingState.DELETED, at=NOW)
    assert manifest.get("01HW").state is MeetingState.DELETED


def test_illegal_transition_pending_to_deleted_raises(manifest):
    manifest.register(_meeting(), at=NOW)
    with pytest.raises(IllegalStateTransition):
        manifest.transition("01HW", to=MeetingState.DELETED, at=NOW)


def test_illegal_transition_after_deleted_raises(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW)
    manifest.transition("01HW", to=MeetingState.DELETED, at=NOW)
    with pytest.raises(IllegalStateTransition):
        manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW)


def test_failed_states_can_return_to_pending(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.transition("01HW", to=MeetingState.FAILED_DOWNLOAD, at=NOW, last_error="boom")
    manifest.transition("01HW", to=MeetingState.PENDING, at=NOW)
    assert manifest.get("01HW").state is MeetingState.PENDING


def test_state_log_records_every_transition(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW)
    manifest.transition("01HW", to=MeetingState.DELETED, at=NOW)
    log = manifest.state_log("01HW")
    assert [(e.from_state, e.to_state) for e in log] == [
        (None, MeetingState.PENDING),
        (MeetingState.PENDING, MeetingState.ARCHIVED),
        (MeetingState.ARCHIVED, MeetingState.DELETED),
    ]


def test_history_filters_by_month(manifest):
    manifest.register(_meeting("a"), at=datetime(2026, 3, 5, tzinfo=UTC))
    manifest.register(_meeting("b"), at=datetime(2026, 4, 5, tzinfo=UTC))
    manifest.transition("a", to=MeetingState.ARCHIVED, at=datetime(2026, 3, 6, tzinfo=UTC))
    manifest.transition("a", to=MeetingState.DELETED, at=datetime(2026, 3, 7, tzinfo=UTC))
    manifest.transition("b", to=MeetingState.ARCHIVED, at=datetime(2026, 4, 6, tzinfo=UTC))
    manifest.transition("b", to=MeetingState.DELETED, at=datetime(2026, 4, 7, tzinfo=UTC))
    march = manifest.history(year=2026, month=3)
    april = manifest.history(year=2026, month=4)
    assert {h.meeting_id for h in march} == {"a"}
    assert {h.meeting_id for h in april} == {"b"}


def test_counts_by_state(manifest):
    manifest.register(_meeting("a"), at=NOW)
    manifest.register(_meeting("b"), at=NOW)
    manifest.transition("a", to=MeetingState.ARCHIVED, at=NOW)
    counts = manifest.counts_by_state()
    assert counts.get(MeetingState.PENDING, 0) == 1
    assert counts.get(MeetingState.ARCHIVED, 0) == 1


def test_close_releases_connection(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.close()


def test_transition_records_details_in_state_log(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.transition(
        "01HW",
        to=MeetingState.FAILED_DOWNLOAD,
        at=NOW,
        last_error="boom",
        details={"err": "x"},
    )
    log = manifest.state_log("01HW")
    assert log[-1].details == {"err": "x"}
    assert log[0].details is None
