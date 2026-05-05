"""Tests for purge_scheduler — the daily API-purge trickle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from firefliesclearer.application.purge_service import PurgeOutcome
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.infra.config import RunConfig
from firefliesclearer.infra.fireflies_client import RateLimitedError
from firefliesclearer.infra.purge_scheduler import (
    candidates_for_trickle,
    compute_next_purge,
    run_one_trickle,
    run_purge_scheduler,
)
from firefliesclearer.infra.system_clock import SystemClock

NOW = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# compute_next_purge — pure decision function
# ---------------------------------------------------------------------------


def test_compute_next_disabled_returns_none():
    cfg = RunConfig(api_purge_per_day=0)
    assert compute_next_purge(last_run_finished_at=None, config=cfg, now=NOW) is None


def test_compute_next_first_run_fires_immediately():
    cfg = RunConfig(api_purge_per_day=5)
    assert compute_next_purge(last_run_finished_at=None, config=cfg, now=NOW) == NOW


def test_compute_next_subsequent_run_waits_24h():
    cfg = RunConfig(api_purge_per_day=5)
    last = NOW - timedelta(hours=2)
    expected = last + timedelta(hours=24)
    assert compute_next_purge(last_run_finished_at=last, config=cfg, now=NOW) == expected


# ---------------------------------------------------------------------------
# candidates_for_trickle — manifest query helper
# ---------------------------------------------------------------------------


def _meeting(meeting_id: str, days_old: int = 30) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=f"Meeting {meeting_id}",
        meeting_date=NOW - timedelta(days=days_old),
        duration_minutes=30.0,
        host_email="u@x.com",
        participant_count=2,
        tags=(),
        has_transcript=True,
    )


def _walk_to_archived(manifest: Manifest, meeting_id: str, *, at: datetime = NOW) -> None:
    manifest.transition(meeting_id, to=MeetingState.PENDING, at=at)
    manifest.transition(meeting_id, to=MeetingState.ARCHIVED, at=at)


@pytest.fixture
def manifest_db(tmp_path):
    return Manifest.open(tmp_path / "manifest.db")


def test_candidates_returns_archived_oldest_first(manifest_db):
    manifest_db.upsert_known(_meeting("m_new", days_old=10), at=NOW)
    manifest_db.upsert_known(_meeting("m_old", days_old=300), at=NOW)
    _walk_to_archived(manifest_db, "m_new", at=NOW - timedelta(hours=1))
    _walk_to_archived(manifest_db, "m_old", at=NOW - timedelta(days=10))

    result = candidates_for_trickle(manifest=manifest_db, limit=5)
    assert [r.meeting_id for r in result] == ["m_old", "m_new"]


def test_candidates_includes_deleted_failed(manifest_db):
    manifest_db.upsert_known(_meeting("m1"), at=NOW)
    _walk_to_archived(manifest_db, "m1")
    manifest_db.transition(
        "m1",
        to=MeetingState.DELETED_FAILED,
        at=NOW,
        last_error="Too many requests",
    )
    result = candidates_for_trickle(manifest=manifest_db, limit=5)
    assert [r.meeting_id for r in result] == ["m1"]


def test_candidates_excludes_known_pending_failed_deleted(manifest_db):
    manifest_db.upsert_known(_meeting("m_known"), at=NOW)
    manifest_db.upsert_known(_meeting("m_pending"), at=NOW)
    manifest_db.transition("m_pending", to=MeetingState.PENDING, at=NOW)
    manifest_db.upsert_known(_meeting("m_failed"), at=NOW)
    manifest_db.transition("m_failed", to=MeetingState.PENDING, at=NOW)
    manifest_db.transition("m_failed", to=MeetingState.FAILED_DOWNLOAD, at=NOW, last_error="x")
    manifest_db.upsert_known(_meeting("m_deleted"), at=NOW)
    _walk_to_archived(manifest_db, "m_deleted")
    manifest_db.transition("m_deleted", to=MeetingState.DELETED, at=NOW)

    assert candidates_for_trickle(manifest=manifest_db, limit=5) == []


def test_candidates_excludes_gone_from_source(manifest_db):
    """Sync already learned this row is missing upstream; don't waste an API
    delete on it. The full-sync auto-reconcile (Phase 3) will close it out
    locally on the next pass."""
    manifest_db.upsert_known(_meeting("m1"), at=NOW)
    _walk_to_archived(manifest_db, "m1")
    manifest_db.set_source_state("m1", "gone")
    assert candidates_for_trickle(manifest=manifest_db, limit=5) == []


def test_candidates_respects_limit(manifest_db):
    for i in range(7):
        mid = f"m{i}"
        manifest_db.upsert_known(_meeting(mid), at=NOW)
        _walk_to_archived(manifest_db, mid, at=NOW - timedelta(days=i))
    result = candidates_for_trickle(manifest=manifest_db, limit=3)
    assert len(result) == 3


def test_candidates_zero_limit_returns_empty(manifest_db):
    manifest_db.upsert_known(_meeting("m1"), at=NOW)
    _walk_to_archived(manifest_db, "m1")
    assert candidates_for_trickle(manifest=manifest_db, limit=0) == []


# ---------------------------------------------------------------------------
# run_one_trickle — single-pass execution
# ---------------------------------------------------------------------------


class _FakePurgeService:
    """A purge service that produces canned outcomes per call.

    Each call to ``purge_meetings([meeting])`` consumes the next entry
    from ``script``. Entries: a PurgeOutcome instance OR a callable that
    raises (used to simulate RateLimitedError mid-batch).
    """

    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.calls: list[str] = []

    async def purge_meetings(self, meetings: list[Meeting]) -> AsyncIterator[PurgeOutcome]:
        assert len(meetings) == 1, "trickle calls purge_meetings one at a time"
        m = meetings[0]
        self.calls.append(m.meeting_id)
        if not self.script:
            raise AssertionError("FakePurgeService ran out of script")
        nxt = self.script.pop(0)
        if callable(nxt):
            nxt()  # raise side-effect
            return
        outcome = nxt
        if not isinstance(outcome, PurgeOutcome):
            raise TypeError(f"unexpected script entry: {type(outcome).__name__}")
        yield outcome


def _success_outcome(mid: str) -> PurgeOutcome:
    return PurgeOutcome(meeting_id=mid, title=mid, state=MeetingState.DELETED, error=None)


def _failed_outcome(mid: str, err: str) -> PurgeOutcome:
    return PurgeOutcome(meeting_id=mid, title=mid, state=MeetingState.DELETED_FAILED, error=err)


async def test_trickle_deletes_up_to_limit(manifest_db):
    for i in range(3):
        mid = f"m{i}"
        manifest_db.upsert_known(_meeting(mid), at=NOW)
        _walk_to_archived(manifest_db, mid, at=NOW - timedelta(days=i))

    svc = _FakePurgeService(
        script=[_success_outcome("m2"), _success_outcome("m1"), _success_outcome("m0")]
    )
    deleted, attempted = await run_one_trickle(purge_service=svc, manifest=manifest_db, limit=5)
    assert deleted == 3
    assert attempted == 3
    # Oldest first.
    assert svc.calls == ["m2", "m1", "m0"]


async def test_trickle_stops_on_rate_limit(manifest_db):
    """When the daily quota fires, the trickle abandons remaining
    candidates rather than burning quota on guaranteed-failing requests."""
    for i in range(5):
        mid = f"m{i}"
        manifest_db.upsert_known(_meeting(mid), at=NOW)
        _walk_to_archived(manifest_db, mid, at=NOW - timedelta(days=i))

    def _raise_rate_limit() -> None:
        raise RateLimitedError("daily quota", retry_after_seconds=60_000)

    svc = _FakePurgeService(
        script=[
            _success_outcome("m4"),
            _success_outcome("m3"),
            _raise_rate_limit,
            # The remaining script entries should never run.
            _success_outcome("never"),
            _success_outcome("never"),
        ]
    )
    deleted, attempted = await run_one_trickle(purge_service=svc, manifest=manifest_db, limit=5)
    assert deleted == 2
    assert attempted == 3
    assert svc.calls == ["m4", "m3", "m2"]


async def test_trickle_continues_after_per_meeting_failure(manifest_db):
    """A per-meeting non-rate-limit failure is logged, the meeting stays
    DELETED_FAILED, and the trickle moves on. Don't burn the rest of
    today's budget over one bad row."""
    for i in range(3):
        mid = f"m{i}"
        manifest_db.upsert_known(_meeting(mid), at=NOW)
        _walk_to_archived(manifest_db, mid, at=NOW - timedelta(days=i))

    svc = _FakePurgeService(
        script=[
            _failed_outcome("m2", "weird transient"),
            _success_outcome("m1"),
            _success_outcome("m0"),
        ]
    )
    deleted, attempted = await run_one_trickle(purge_service=svc, manifest=manifest_db, limit=5)
    assert attempted == 3
    assert deleted == 2


async def test_trickle_no_candidates_returns_zero(manifest_db):
    svc = _FakePurgeService(script=[])
    deleted, attempted = await run_one_trickle(purge_service=svc, manifest=manifest_db, limit=5)
    assert (deleted, attempted) == (0, 0)
    assert svc.calls == []


# ---------------------------------------------------------------------------
# run_purge_scheduler — async loop
# ---------------------------------------------------------------------------


async def test_scheduler_disabled_sleeps_and_exits_on_shutdown(manifest_db):
    """When the trickle is disabled, the loop should sleep and exit
    cleanly when shutdown_event fires — not spin and not run any
    purge calls."""
    cfg = RunConfig(api_purge_per_day=0)
    svc = _FakePurgeService(script=[])
    shutdown = asyncio.Event()
    task = asyncio.create_task(
        run_purge_scheduler(
            purge_service=svc,
            manifest=manifest_db,
            config=cfg,
            clock=SystemClock(),
            shutdown_event=shutdown,
        )
    )
    # Let the loop reach the disabled-recheck wait, then signal shutdown.
    await asyncio.sleep(0.05)
    shutdown.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert svc.calls == []


async def test_scheduler_fires_immediately_on_first_loop(manifest_db):
    """First-run case: ``last_run_finished_at`` is None so the next-tick
    is "now" — the loop should call run_one_trickle right away."""
    manifest_db.upsert_known(_meeting("m1"), at=NOW)
    _walk_to_archived(manifest_db, "m1")
    cfg = RunConfig(api_purge_per_day=5)
    svc = _FakePurgeService(script=[_success_outcome("m1")])
    shutdown = asyncio.Event()
    task = asyncio.create_task(
        run_purge_scheduler(
            purge_service=svc,
            manifest=manifest_db,
            config=cfg,
            clock=SystemClock(),
            shutdown_event=shutdown,
        )
    )
    # Wait long enough for the first tick to fire and the loop to enter
    # the post-tick sleep (which is 24h, so plenty of headroom).
    await asyncio.sleep(0.1)
    shutdown.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert svc.calls == ["m1"]
