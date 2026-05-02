"""Tests for sync_scheduler decision logic (compute_next, decide_mode)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from firefliesclearer.application.sync_service import SyncService
from firefliesclearer.core.manifest import Manifest, SyncRunRecord
from firefliesclearer.core.models import Meeting
from firefliesclearer.infra.config import SyncConfig
from firefliesclearer.infra.sync_scheduler import compute_next, decide_mode
from firefliesclearer.infra.system_clock import SystemClock
from tests.fakes.controllable_repository import ControllableMeetingRepository


def _run(
    *,
    mode: str = "incremental",
    outcome: str = "success",
    finished_at: datetime | None = None,
    next_resume_at: datetime | None = None,
) -> SyncRunRecord:
    return SyncRunRecord(
        id=1,
        mode=mode,
        trigger_source="scheduled",
        started_at=finished_at or datetime(2026, 5, 2, 0, 0, tzinfo=UTC),
        finished_at=finished_at,
        outcome=outcome,
        meetings_seen=0,
        meetings_added=0,
        meetings_updated=0,
        meetings_gone=0,
        cursor_skip=None,
        seen_ids_json=None,
        next_resume_at=next_resume_at,
        error_message=None,
    )


def test_compute_next_no_runs_returns_now():
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    cfg = SyncConfig(enabled=True)
    assert compute_next(last_run=None, last_full=None, config=cfg, now=now) == now


def test_compute_next_after_incremental_returns_finished_plus_interval():
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    last = _run(finished_at=datetime(2026, 5, 2, 6, 0, tzinfo=UTC))
    cfg = SyncConfig(enabled=True, incremental_interval_hours=6)
    assert compute_next(last_run=last, last_full=None, config=cfg, now=now) == datetime(
        2026, 5, 2, 12, 0, tzinfo=UTC
    )


def test_compute_next_picks_earlier_of_full_or_incremental():
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    last = _run(finished_at=datetime(2026, 5, 2, 11, 0, tzinfo=UTC))  # next inc at 17:00
    last_full = _run(finished_at=datetime(2026, 4, 24, 3, 0, tzinfo=UTC))  # 8 days ago
    cfg = SyncConfig(
        enabled=True,
        incremental_interval_hours=6,
        full_interval_days=7,
        full_run_hour_local=3,
    )
    # next_full lands sooner — at next 03:00 local that's >= 7 days after last_full
    nxt = compute_next(last_run=last, last_full=last_full, config=cfg, now=now)
    # 8 days have passed, next 03:00 local from "now=12:00 UTC May 2" is 03:00 UTC May 3.
    assert nxt.hour == 3


def test_compute_next_partial_run_blocks_until_resume_window():
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    resume = datetime(2026, 5, 2, 14, 0, tzinfo=UTC)
    last = _run(outcome="partial", finished_at=now, next_resume_at=resume)
    cfg = SyncConfig(enabled=True)
    # When last is partial, compute_next returns next_resume_at
    assert compute_next(last_run=last, last_full=None, config=cfg, now=now) == resume


def test_decide_mode_returns_full_when_due():
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    cfg = SyncConfig(enabled=True, full_interval_days=7, full_run_hour_local=3)
    # last_full was 8 days ago, so full is due
    last_full = _run(finished_at=datetime(2026, 4, 24, 3, 0, tzinfo=UTC))
    assert decide_mode(last_full=last_full, config=cfg, now=now) == "full"


def test_decide_mode_returns_incremental_when_full_not_due():
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    cfg = SyncConfig(enabled=True, full_interval_days=7, full_run_hour_local=3)
    last_full = _run(finished_at=datetime(2026, 5, 1, 3, 0, tzinfo=UTC))  # 1 day ago
    assert decide_mode(last_full=last_full, config=cfg, now=now) == "incremental"


def test_decide_mode_returns_incremental_when_full_disabled():
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    cfg = SyncConfig(enabled=True, full_interval_days=0)
    assert decide_mode(last_full=None, config=cfg, now=now) == "incremental"


def _scheduler_meeting(mid: str) -> Meeting:
    return Meeting(
        meeting_id=mid,
        title=mid,
        meeting_date=datetime(2026, 4, 1, tzinfo=UTC),
        duration_minutes=30.0,
        host_email="a@x",
        participant_count=2,
        tags=(),
        has_transcript=True,
    )


async def test_run_scheduler_invokes_sync_service_once_then_stops(tmp_path):
    """The scheduler runs a single sync when shutdown_event is set early."""
    from firefliesclearer.infra.sync_scheduler import run_scheduler

    manifest = Manifest.open(tmp_path / "manifest.db")
    repo = ControllableMeetingRepository(meetings=[_scheduler_meeting("a")], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest, clock=SystemClock())
    cfg = SyncConfig(enabled=True, incremental_interval_hours=24)

    shutdown = asyncio.Event()

    async def stop_after_one_run():
        # Wait for one sync to complete then signal shutdown
        for _ in range(50):
            await asyncio.sleep(0.05)
            if manifest.get_last_sync_run() is not None:
                shutdown.set()
                return
        shutdown.set()

    await asyncio.gather(
        run_scheduler(
            sync_service=svc,
            manifest=manifest,
            config=cfg,
            clock=SystemClock(),
            shutdown_event=shutdown,
        ),
        stop_after_one_run(),
    )

    last = manifest.get_last_sync_run()
    assert last is not None
    assert last.outcome == "success"
