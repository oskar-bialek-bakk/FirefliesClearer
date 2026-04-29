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


# ---------------------------------------------------------------------------
# New read-only helpers (for AuditService)
# ---------------------------------------------------------------------------


def test_last_state_change_at_returns_none_for_empty_manifest(manifest):
    assert manifest.last_state_change_at() is None


def test_last_state_change_at_returns_max_of_state_log(manifest):
    t1 = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 4, 5, 15, 0, tzinfo=UTC)
    manifest.register(_meeting("a"), at=t1)
    manifest.register(_meeting("b"), at=t2)
    result = manifest.last_state_change_at()
    assert result == t2


def test_meeting_ids_in_states_returns_matching_ids(manifest):
    manifest.register(_meeting("a"), at=NOW)
    manifest.register(_meeting("b"), at=NOW)
    manifest.transition("a", to=MeetingState.FAILED_FETCH, at=NOW, last_error="x")
    ids = manifest.meeting_ids_in_states([MeetingState.FAILED_FETCH])
    assert ids == ["a"]
    pending_ids = manifest.meeting_ids_in_states([MeetingState.PENDING])
    assert pending_ids == ["b"]


def test_meeting_ids_in_states_with_empty_set_returns_empty(manifest):
    manifest.register(_meeting(), at=NOW)
    assert manifest.meeting_ids_in_states([]) == []


def test_query_history_filters_by_state_and_date_range_and_title(manifest):
    t_march = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
    t_april = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    manifest.register(_meeting("a"), at=NOW)
    manifest.register(_meeting("b"), at=NOW)
    manifest.register(_meeting("c"), at=NOW)
    # transition a -> ARCHIVED -> DELETED in march
    manifest.transition("a", to=MeetingState.ARCHIVED, at=t_march)
    manifest.transition("a", to=MeetingState.DELETED, at=t_march)
    # transition b -> ARCHIVED -> DELETED in april
    manifest.transition("b", to=MeetingState.ARCHIVED, at=t_april)
    manifest.transition("b", to=MeetingState.DELETED, at=t_april)
    # c stays pending

    date_from = datetime(2026, 4, 1, tzinfo=UTC)
    date_to = datetime(2026, 5, 1, tzinfo=UTC)
    result = manifest.query_history(
        states=[MeetingState.DELETED],
        date_from=date_from,
        date_to=date_to,
        title_contains=None,
        limit=50,
        offset=0,
    )
    assert [r.meeting_id for r in result] == ["b"]


def test_query_history_respects_limit_and_offset_and_orders_by_deleted_at_desc(manifest):
    t1 = datetime(2026, 4, 1, tzinfo=UTC)
    t2 = datetime(2026, 4, 2, tzinfo=UTC)
    t3 = datetime(2026, 4, 3, tzinfo=UTC)
    for mid, t in [("a", t1), ("b", t2), ("c", t3)]:
        manifest.register(_meeting(mid), at=NOW)
        manifest.transition(mid, to=MeetingState.ARCHIVED, at=t)
        manifest.transition(mid, to=MeetingState.DELETED, at=t)

    # All, ordered desc: c, b, a
    all_result = manifest.query_history(
        states=None, date_from=None, date_to=None, title_contains=None, limit=50, offset=0
    )
    assert [r.meeting_id for r in all_result] == ["c", "b", "a"]

    # limit=2 offset=1 -> skip c, take b, a
    paged = manifest.query_history(
        states=None, date_from=None, date_to=None, title_contains=None, limit=2, offset=1
    )
    assert [r.meeting_id for r in paged] == ["b", "a"]


def test_count_history_returns_total_ignoring_limit_offset(manifest):
    for mid in ("a", "b", "c"):
        manifest.register(_meeting(mid), at=NOW)
        manifest.transition(mid, to=MeetingState.ARCHIVED, at=NOW)
        manifest.transition(mid, to=MeetingState.DELETED, at=NOW)

    total = manifest.count_history(
        states=[MeetingState.DELETED],
        date_from=None,
        date_to=None,
        title_contains=None,
    )
    assert total == 3
