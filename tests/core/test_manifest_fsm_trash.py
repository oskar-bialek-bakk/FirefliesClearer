"""FSM tests for the trash flow's KNOWN -> DELETED short-circuit."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from firefliesclearer.core.manifest import SCHEMA, IllegalStateTransition, Manifest
from firefliesclearer.core.models import Meeting, MeetingState

NOW = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)


def _manifest() -> Manifest:
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return Manifest(conn)


def _meeting(mid: str = "m1") -> Meeting:
    return Meeting(
        meeting_id=mid,
        title="t",
        meeting_date=NOW,
        duration_minutes=10.0,
        host_email="oskar@example.com",
        participant_count=2,
    )


def test_known_to_deleted_is_legal_for_trash_flow() -> None:
    m = _manifest()
    m.upsert_known(_meeting(), at=NOW)
    m.transition(
        "m1",
        to=MeetingState.DELETED,
        at=NOW,
        details={"reason": "manual_trash_via_wizard"},
    )
    rec = m.get("m1")
    assert rec is not None
    assert rec.state is MeetingState.DELETED
    assert rec.archive_path is None
    last = m.state_log("m1")[-1]
    assert last.from_state is MeetingState.KNOWN
    assert last.to_state is MeetingState.DELETED
    assert last.details == {"reason": "manual_trash_via_wizard"}


def test_known_to_pending_still_legal() -> None:
    m = _manifest()
    m.upsert_known(_meeting(), at=NOW)
    m.transition("m1", to=MeetingState.PENDING, at=NOW)
    rec = m.get("m1")
    assert rec is not None
    assert rec.state is MeetingState.PENDING


def test_known_to_archived_still_illegal() -> None:
    m = _manifest()
    m.upsert_known(_meeting(), at=NOW)
    with pytest.raises(IllegalStateTransition):
        m.transition("m1", to=MeetingState.ARCHIVED, at=NOW)


def test_pending_to_deleted_still_illegal() -> None:
    m = _manifest()
    m.upsert_known(_meeting(), at=NOW)
    m.transition("m1", to=MeetingState.PENDING, at=NOW)
    with pytest.raises(IllegalStateTransition):
        m.transition("m1", to=MeetingState.DELETED, at=NOW)
