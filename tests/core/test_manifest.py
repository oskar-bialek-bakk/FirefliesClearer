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


def test_meeting_state_has_known_variant():
    assert MeetingState.KNOWN.value == "known"


def test_legal_transitions_include_none_to_known():
    from firefliesclearer.core.manifest import LEGAL_TRANSITIONS

    assert MeetingState.KNOWN in LEGAL_TRANSITIONS[None]


def test_legal_transitions_include_known_to_pending():
    from firefliesclearer.core.manifest import LEGAL_TRANSITIONS

    assert MeetingState.PENDING in LEGAL_TRANSITIONS[MeetingState.KNOWN]


def test_legal_transitions_preserve_existing_none_to_pending():
    """register() still works — Phase 1 is strictly additive."""
    from firefliesclearer.core.manifest import LEGAL_TRANSITIONS

    assert MeetingState.PENDING in LEGAL_TRANSITIONS[None]


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


def test_query_history_filters_by_title_contains(manifest):
    """SQLite LIKE is case-insensitive for ASCII — 'Sync' matches 'sync'."""
    titles = {
        "a": "Daily Sync",
        "b": "Sprint Planning",
        "c": "Sync with Customer",
    }
    for mid, title in titles.items():
        meeting = Meeting(
            meeting_id=mid,
            title=title,
            meeting_date=NOW,
            duration_minutes=30.0,
            host_email="u@x.com",
            participant_count=2,
            tags=(),
            has_transcript=True,
        )
        manifest.register(meeting, at=NOW)
        manifest.transition(mid, to=MeetingState.ARCHIVED, at=NOW)
        manifest.transition(mid, to=MeetingState.DELETED, at=NOW)

    result = manifest.query_history(
        states=None,
        date_from=None,
        date_to=None,
        title_contains="Sync",
        limit=50,
        offset=0,
    )
    assert len(result) == 2
    result_ids = {r.meeting_id for r in result}
    assert result_ids == {"a", "c"}


# ---------------------------------------------------------------------------
# Phase 1: Local cache schema (snapshot columns + indexes)
# ---------------------------------------------------------------------------


def test_fresh_manifest_has_snapshot_columns(tmp_path):
    """A newly-opened DB has all Phase 1 snapshot columns + source_state."""
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(meetings)")}
    conn.close()

    expected_new = {
        "duration_minutes",
        "host_email",
        "participant_count",
        "has_transcript",
        "tags_json",
        "source_state",
        "cached_at",
    }
    assert expected_new <= cols, f"missing: {expected_new - cols}"


def test_fresh_manifest_source_state_defaults_to_live(tmp_path):
    """source_state column defaults to 'live' so existing-row migration is one-step."""
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    row = conn.execute(
        "SELECT dflt_value FROM pragma_table_info('meetings') WHERE name = 'source_state'"
    ).fetchone()
    conn.close()
    assert row is not None
    # SQLite returns the literal default expression; quoted string in our case.
    assert row[0] in ("'live'", "live")


def test_fresh_manifest_has_new_indexes(tmp_path):
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    expected = {
        "idx_meetings_source_state",
        "idx_meetings_meeting_date",
        "idx_meetings_host_email",
    }
    assert expected <= indexes, f"missing: {expected - indexes}"


def _legacy_v2_schema() -> str:
    """The pre-Phase-1 SCHEMA, used to simulate an existing manifest."""
    return """
    CREATE TABLE meetings (
      meeting_id        TEXT PRIMARY KEY,
      title             TEXT NOT NULL,
      meeting_date      TEXT NOT NULL,
      state             TEXT NOT NULL,
      archive_path      TEXT,
      audio_sha256      TEXT,
      summary_sha256    TEXT,
      transcript_sha256 TEXT,
      archived_at       TEXT,
      verified_at       TEXT,
      deleted_at        TEXT,
      last_error        TEXT
    );
    CREATE TABLE state_log (
      id            INTEGER PRIMARY KEY,
      meeting_id    TEXT NOT NULL,
      from_state    TEXT,
      to_state      TEXT NOT NULL,
      at            TEXT NOT NULL,
      details       TEXT
    );
    """


def test_migration_adds_snapshot_columns_to_existing_manifest(tmp_path):
    import sqlite3

    db_path = tmp_path / "manifest.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_legacy_v2_schema())
    conn.execute(
        "INSERT INTO meetings (meeting_id, title, meeting_date, state) VALUES (?, ?, ?, ?)",
        ("legacy-1", "Old Standup", "2026-01-01T00:00:00+00:00", "archived"),
    )
    conn.commit()
    conn.close()

    Manifest.open(db_path)  # should migrate, not raise

    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(meetings)")}
    row = conn.execute(
        "SELECT title, source_state, duration_minutes FROM meetings WHERE meeting_id = ?",
        ("legacy-1",),
    ).fetchone()
    conn.close()

    assert {"duration_minutes", "host_email", "source_state", "cached_at"} <= cols
    assert row[0] == "Old Standup"  # legacy data preserved
    assert row[1] == "live"  # source_state backfilled by DEFAULT clause
    assert row[2] is None  # legacy row has no duration


def test_migration_is_idempotent(tmp_path):
    """Running Manifest.open twice on the same DB does not error."""
    db_path = tmp_path / "manifest.db"
    Manifest.open(db_path)
    Manifest.open(db_path)  # second open must succeed without raising
    Manifest.open(db_path)  # and a third


def test_migration_preserves_existing_indexes(tmp_path):
    """The legacy idx_meetings_state index must survive migration."""
    import sqlite3

    db_path = tmp_path / "manifest.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_legacy_v2_schema())
    conn.execute("CREATE INDEX idx_meetings_state ON meetings(state)")
    conn.commit()
    conn.close()

    Manifest.open(db_path)

    conn = sqlite3.connect(str(db_path))
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_meetings_state" in indexes


def test_meeting_record_has_snapshot_fields(manifest):
    """MeetingRecord exposes snapshot + source_state fields read from the row."""
    manifest.register(_meeting(), at=NOW)
    rec = manifest.get("01HW")
    assert rec is not None
    # New attributes exist
    assert hasattr(rec, "duration_minutes")
    assert hasattr(rec, "host_email")
    assert hasattr(rec, "participant_count")
    assert hasattr(rec, "has_transcript")
    assert hasattr(rec, "tags")
    assert hasattr(rec, "source_state")
    assert hasattr(rec, "cached_at")


def test_meeting_record_legacy_register_has_null_snapshot(manifest):
    """register() still inserts only legacy columns; snapshot fields are None."""
    manifest.register(_meeting(), at=NOW)
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.duration_minutes is None
    assert rec.host_email is None
    assert rec.tags is None
    # source_state defaults to 'live' via the column DEFAULT clause
    assert rec.source_state == "live"
    assert rec.cached_at is None


def test_sync_runs_table_exists(tmp_path):
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_runs'"
    ).fetchone()
    conn.close()
    assert row is not None


def test_sync_runs_columns(tmp_path):
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_runs)")}
    conn.close()
    expected = {
        "id",
        "mode",
        "trigger_source",
        "started_at",
        "finished_at",
        "outcome",
        "meetings_seen",
        "meetings_added",
        "meetings_updated",
        "meetings_gone",
        "cursor_skip",
        "seen_ids_json",
        "next_resume_at",
        "error_message",
    }
    assert expected <= cols, f"missing: {expected - cols}"


def test_sync_runs_started_at_index_exists(tmp_path):
    import sqlite3

    Manifest.open(tmp_path / "manifest.db")
    conn = sqlite3.connect(str(tmp_path / "manifest.db"))
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_sync_runs_started_at" in indexes


def test_sync_runs_table_created_on_existing_manifest(tmp_path):
    """Migration creates sync_runs even when meetings table predates Phase 1."""
    import sqlite3

    db_path = tmp_path / "manifest.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_legacy_v2_schema())
    conn.commit()
    conn.close()

    Manifest.open(db_path)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_runs'"
    ).fetchone()
    conn.close()
    assert row is not None


def _meeting_with_full_snapshot(meeting_id: str = "01HW") -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title="Q4 Planning",
        meeting_date=NOW,
        duration_minutes=45.5,
        host_email="alice@example.com",
        participant_count=6,
        tags=("planning", "q4"),
        has_transcript=True,
    )


def test_upsert_known_inserts_new_row_in_known_state(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.state is MeetingState.KNOWN
    assert rec.title == "Q4 Planning"
    assert rec.duration_minutes == 45.5
    assert rec.host_email == "alice@example.com"
    assert rec.participant_count == 6
    assert rec.has_transcript is True
    assert rec.tags == ("planning", "q4")
    assert rec.source_state == "live"
    assert rec.cached_at == NOW


def test_upsert_known_writes_state_log_entry_for_new_row(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    log = manifest.state_log("01HW")
    assert len(log) == 1
    assert log[0].from_state is None
    assert log[0].to_state is MeetingState.KNOWN


def test_upsert_known_refreshes_snapshot_on_existing_row_without_touching_state(manifest):
    """If the row already exists in any state, snapshot fields update; state does not."""
    # Initial insert as KNOWN
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    # User then archives — moves to PENDING then ARCHIVED
    manifest.transition("01HW", to=MeetingState.PENDING, at=NOW)
    manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW, archive_path="/tmp/x")

    # Sync runs again, title was edited in Fireflies
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    edited = Meeting(
        meeting_id="01HW",
        title="Q4 Planning (rescheduled)",
        meeting_date=NOW,
        duration_minutes=60.0,
        host_email="alice@example.com",
        participant_count=8,
        tags=("planning", "q4", "rescheduled"),
        has_transcript=True,
    )
    manifest.upsert_known(edited, at=later)

    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.state is MeetingState.ARCHIVED  # state preserved
    assert rec.title == "Q4 Planning (rescheduled)"  # snapshot updated
    assert rec.duration_minutes == 60.0
    assert rec.participant_count == 8
    assert rec.tags == ("planning", "q4", "rescheduled")
    assert rec.cached_at == later  # cached_at refreshed


def test_upsert_known_does_not_log_for_existing_row(manifest):
    """No state transition happens when refreshing an existing row."""
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    manifest.upsert_known(_meeting_with_full_snapshot(), at=later)

    log = manifest.state_log("01HW")
    assert len(log) == 1  # still just the initial KNOWN log


def test_set_source_state_flips_live_to_gone(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    manifest.set_source_state("01HW", "gone")
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.source_state == "gone"


def test_set_source_state_flips_gone_to_live(manifest):
    """Resurrection edge case: a 'gone' meeting reappears in Fireflies."""
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    manifest.set_source_state("01HW", "gone")
    manifest.set_source_state("01HW", "live")
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.source_state == "live"


def test_set_source_state_leaves_op_state_unchanged(manifest):
    """source_state and op_state are independent axes."""
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    manifest.transition("01HW", to=MeetingState.PENDING, at=NOW)
    manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW, archive_path="/tmp/x")
    manifest.set_source_state("01HW", "gone")
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.state is MeetingState.ARCHIVED
    assert rec.source_state == "gone"


def test_set_source_state_unknown_id_is_noop(manifest):
    """Setting source_state on a non-existent row is silently ignored."""
    manifest.set_source_state("does-not-exist", "gone")
    assert manifest.get("does-not-exist") is None


def test_set_source_state_rejects_invalid_value(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    with pytest.raises(ValueError, match="source_state"):
        manifest.set_source_state("01HW", "bogus")


def test_update_cache_fields_returns_false_when_nothing_changed(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    changed = manifest.update_cache_fields(_meeting_with_full_snapshot(), at=later)
    assert changed is False


def test_update_cache_fields_refreshes_cached_at_even_when_no_change(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    manifest.update_cache_fields(_meeting_with_full_snapshot(), at=later)
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.cached_at == later


def test_update_cache_fields_returns_true_when_title_changed(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    edited = Meeting(
        meeting_id="01HW",
        title="Q4 Planning (rescheduled)",
        meeting_date=NOW,
        duration_minutes=45.5,
        host_email="alice@example.com",
        participant_count=6,
        tags=("planning", "q4"),
        has_transcript=True,
    )
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    changed = manifest.update_cache_fields(edited, at=later)
    assert changed is True
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.title == "Q4 Planning (rescheduled)"


def test_update_cache_fields_returns_true_when_tags_changed(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    edited = Meeting(
        meeting_id="01HW",
        title="Q4 Planning",
        meeting_date=NOW,
        duration_minutes=45.5,
        host_email="alice@example.com",
        participant_count=6,
        tags=("planning", "q4", "added-tag"),
        has_transcript=True,
    )
    later = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    assert manifest.update_cache_fields(edited, at=later) is True


def test_update_cache_fields_does_not_touch_op_state(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot(), at=NOW)
    manifest.transition("01HW", to=MeetingState.PENDING, at=NOW)
    edited = Meeting(
        meeting_id="01HW",
        title="renamed",
        meeting_date=NOW,
        duration_minutes=45.5,
        host_email="alice@example.com",
        participant_count=6,
        tags=("planning", "q4"),
        has_transcript=True,
    )
    manifest.update_cache_fields(edited, at=NOW)
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.state is MeetingState.PENDING


def test_update_cache_fields_unknown_id_returns_false(manifest):
    edited = _meeting_with_full_snapshot("not-in-db")
    assert manifest.update_cache_fields(edited, at=NOW) is False


def test_list_known_yields_known_rows(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    manifest.upsert_known(_meeting_with_full_snapshot("b"), at=NOW)

    ids = sorted(m.meeting_id for m in manifest.list_known())
    assert ids == ["a", "b"]


def test_list_known_skips_archived_by_default(manifest):
    """Archived meetings are excluded unless explicitly requested."""
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    manifest.upsert_known(_meeting_with_full_snapshot("b"), at=NOW)
    manifest.transition("b", to=MeetingState.PENDING, at=NOW)
    manifest.transition("b", to=MeetingState.ARCHIVED, at=NOW)

    ids = sorted(m.meeting_id for m in manifest.list_known())
    assert ids == ["a"]


def test_list_known_includes_archived_when_flag_true(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    manifest.upsert_known(_meeting_with_full_snapshot("b"), at=NOW)
    manifest.transition("b", to=MeetingState.PENDING, at=NOW)
    manifest.transition("b", to=MeetingState.ARCHIVED, at=NOW)

    ids = sorted(m.meeting_id for m in manifest.list_known(include_archived=True))
    assert ids == ["a", "b"]


def test_list_known_skips_gone_by_default(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    manifest.upsert_known(_meeting_with_full_snapshot("b"), at=NOW)
    manifest.set_source_state("b", "gone")

    ids = sorted(m.meeting_id for m in manifest.list_known())
    assert ids == ["a"]


def test_list_known_includes_gone_when_flag_true(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    manifest.upsert_known(_meeting_with_full_snapshot("b"), at=NOW)
    manifest.set_source_state("b", "gone")

    ids = sorted(m.meeting_id for m in manifest.list_known(include_gone=True))
    assert ids == ["a", "b"]


def test_list_known_filters_older_than(manifest):
    """older_than=cutoff yields rows whose meeting_date < cutoff."""
    older_dt = datetime(2025, 1, 1, tzinfo=UTC)
    newer_dt = datetime(2026, 6, 1, tzinfo=UTC)
    manifest.upsert_known(Meeting("old", "old", older_dt, 30.0, "a@x", 2, (), True), at=NOW)
    manifest.upsert_known(Meeting("new", "new", newer_dt, 30.0, "a@x", 2, (), True), at=NOW)

    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    ids = sorted(m.meeting_id for m in manifest.list_known(older_than=cutoff))
    assert ids == ["old"]


def test_list_known_returns_meeting_with_all_fields(manifest):
    manifest.upsert_known(_meeting_with_full_snapshot("a"), at=NOW)
    meetings = list(manifest.list_known())
    assert len(meetings) == 1
    m = meetings[0]
    assert m.meeting_id == "a"
    assert m.title == "Q4 Planning"
    assert m.duration_minutes == 45.5
    assert m.host_email == "alice@example.com"
    assert m.participant_count == 6
    assert m.has_transcript is True
    assert m.tags == ("planning", "q4")


def test_list_known_skips_legacy_rows_without_snapshot(manifest):
    """Rows from the legacy register() path lack snapshot columns.
    list_known cannot reconstruct a valid Meeting, so it skips them.
    Phase 3 deployment will rely on a full sync to populate these.
    """
    manifest.register(_meeting(), at=NOW)  # legacy entry
    manifest.upsert_known(_meeting_with_full_snapshot("synced"), at=NOW)

    ids = sorted(m.meeting_id for m in manifest.list_known())
    assert ids == ["synced"]


def test_list_known_empty_manifest_yields_nothing(manifest):
    assert list(manifest.list_known()) == []
