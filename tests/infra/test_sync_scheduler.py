"""Tests for sync_scheduler decision logic (compute_next, decide_mode)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

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
    meetings_added: int = 0,
) -> SyncRunRecord:
    return SyncRunRecord(
        id=1,
        mode=mode,
        trigger_source="scheduled",
        started_at=finished_at or datetime(2026, 5, 2, 0, 0, tzinfo=UTC),
        finished_at=finished_at,
        outcome=outcome,
        meetings_seen=0,
        meetings_added=meetings_added,
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
    # Set meetings_added=1 so the skip-empty-tick backoff doesn't apply.
    last = _run(finished_at=datetime(2026, 5, 2, 6, 0, tzinfo=UTC), meetings_added=1)
    cfg = SyncConfig(enabled=True, incremental_interval_hours=6)
    assert compute_next(last_run=last, last_full=None, config=cfg, now=now) == datetime(
        2026, 5, 2, 12, 0, tzinfo=UTC
    )


def test_compute_next_doubles_interval_after_empty_incremental_tick():
    """Phase 5: when an incremental sync yields zero new meetings, the
    schedule doubles the next interval to free up API quota for archive/
    delete on a quiet account."""
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    finished = datetime(2026, 5, 2, 6, 0, tzinfo=UTC)
    last = _run(finished_at=finished, meetings_added=0)  # empty tick
    cfg = SyncConfig(enabled=True, incremental_interval_hours=6)
    nxt = compute_next(last_run=last, last_full=None, config=cfg, now=now)
    # 6h * 2 = 12h after the previous finish.
    assert nxt == finished + timedelta(hours=12)


def test_compute_next_caps_doubled_interval_at_24h():
    """A 16h interval would double to 32h — capped at 24h so a quiet
    weekend doesn't roll into Tuesday before noticing new meetings."""
    finished = datetime(2026, 5, 2, 6, 0, tzinfo=UTC)
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    last = _run(finished_at=finished, meetings_added=0)
    cfg = SyncConfig(enabled=True, incremental_interval_hours=16)
    nxt = compute_next(last_run=last, last_full=None, config=cfg, now=now)
    assert nxt == finished + timedelta(hours=24)


def test_compute_next_does_not_double_when_last_yielded_meetings():
    """A non-empty tick uses the normal interval — the backoff resets
    as soon as new meetings show up upstream."""
    finished = datetime(2026, 5, 2, 6, 0, tzinfo=UTC)
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    last = _run(finished_at=finished, meetings_added=3)
    cfg = SyncConfig(enabled=True, incremental_interval_hours=6)
    nxt = compute_next(last_run=last, last_full=None, config=cfg, now=now)
    assert nxt == finished + timedelta(hours=6)


def test_compute_next_does_not_double_after_full_run_with_no_adds():
    """Skip-empty-tick is scoped to incremental — a full sync that walks
    every page and finds no new meetings is doing real work (it might
    still be marking rows gone) and shouldn't trigger backoff."""
    finished = datetime(2026, 5, 2, 6, 0, tzinfo=UTC)
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    last = _run(mode="full", finished_at=finished, meetings_added=0)
    cfg = SyncConfig(enabled=True, incremental_interval_hours=6)
    nxt = compute_next(last_run=last, last_full=None, config=cfg, now=now)
    assert nxt == finished + timedelta(hours=6)


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


def test_compute_next_partial_without_resume_estimate_retries_quickly():
    """5xx and transport timeouts produce partial runs with ``next_resume_at=None``
    (Fireflies never tells us when transient issues will clear, so we don't
    fabricate a timestamp to surface in the UI). The scheduler must still pick
    these up promptly — otherwise a multi-page sync that paged-out mid-stream
    stalls for the full ``incremental_interval_hours`` (default 6h), which
    leaves the user staring at "Sync paused" with no explanation of why nothing
    is happening. The scheduling decision (~1 min) is deliberately separate
    from the UI messaging (no timestamp shown)."""
    finished = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    now = datetime(2026, 5, 2, 12, 0, 30, tzinfo=UTC)  # 30s after finish
    last = _run(outcome="partial", finished_at=finished, next_resume_at=None)
    cfg = SyncConfig(enabled=True, incremental_interval_hours=6)
    nxt = compute_next(last_run=last, last_full=None, config=cfg, now=now)
    # Must retry well before the regular 6-hour incremental window.
    assert nxt < finished + timedelta(minutes=5)
    # And must be based on finished_at, not now — so a partial that finished
    # several minutes ago fires immediately rather than waiting again.
    assert nxt >= finished
    assert nxt <= finished + timedelta(minutes=2)


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


async def test_run_scheduler_acquires_lock_and_calls_lifecycle_hooks(tmp_path):
    """Regression: scheduler must take ``sync_lock`` for the duration of each
    tick and call ``on_run_started`` / ``on_run_finished`` so the manual
    /sync/now route sees the lock as held and ``/sync/status`` reports the
    in-flight scheduled run instead of ``idle``.
    """
    from firefliesclearer.infra.sync_scheduler import run_scheduler

    manifest = Manifest.open(tmp_path / "manifest.db")
    # Pre-seed cache so bootstrap path doesn't fire (which would force FULL).
    manifest.upsert_known(_scheduler_meeting("seed"), at=datetime(2026, 4, 1, tzinfo=UTC))
    repo = ControllableMeetingRepository(meetings=[_scheduler_meeting("a")], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest, clock=SystemClock())
    cfg = SyncConfig(enabled=True, incremental_interval_hours=24, full_interval_days=0)

    sync_lock = asyncio.Lock()
    started: list[tuple[str, str]] = []
    finished: list[bool] = []
    lock_state_during_run: list[bool] = []

    def on_run_started(mode: str, trigger: str, started_at: datetime) -> None:
        started.append((mode, trigger))
        lock_state_during_run.append(sync_lock.locked())

    def on_run_finished() -> None:
        finished.append(True)

    shutdown = asyncio.Event()

    async def stop_after_one_run() -> None:
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
            sync_lock=sync_lock,
            on_run_started=on_run_started,
            on_run_finished=on_run_finished,
        ),
        stop_after_one_run(),
    )

    assert len(started) == 1, "on_run_started should fire once per tick"
    assert started[0] == ("incremental", "scheduled")
    assert lock_state_during_run == [True], "sync_lock must be held when sync runs"
    assert finished == [True], "on_run_finished must fire even on the success path"
    assert not sync_lock.locked(), "sync_lock must be released after the tick"


async def test_run_scheduler_releases_lock_when_sync_raises(tmp_path):
    """The lock + on_run_finished hook must fire even if sync_service.run raises."""
    from firefliesclearer.infra.sync_scheduler import run_scheduler

    manifest = Manifest.open(tmp_path / "manifest.db")
    cfg = SyncConfig(enabled=True, incremental_interval_hours=24)

    class _ExplodingService:
        async def run(self, *, mode: object, trigger: object, resume_run_id: object = None) -> None:
            raise RuntimeError("kaboom")

    sync_lock = asyncio.Lock()
    finished: list[bool] = []
    shutdown = asyncio.Event()

    def on_run_finished() -> None:
        finished.append(True)
        shutdown.set()

    await run_scheduler(
        sync_service=_ExplodingService(),
        manifest=manifest,
        config=cfg,
        clock=SystemClock(),
        shutdown_event=shutdown,
        sync_lock=sync_lock,
        on_run_finished=on_run_finished,
    )

    assert finished == [True]
    assert not sync_lock.locked(), "sync_lock must be released after a failed tick"
