"""Sync scheduler — decides when and what mode to run, then drives SyncService.

The decision logic (compute_next, decide_mode) is pure and unit-tested.
The asyncio loop (run_scheduler) wraps it with sleep + lock + actual
SyncService.run invocation.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from firefliesclearer.core.manifest import SyncRunRecord
from firefliesclearer.core.sync_types import SyncMode, SyncTrigger
from firefliesclearer.infra.config import SyncConfig

logger = logging.getLogger(__name__)

OnRunStarted = Callable[[str, str, datetime], None]
"""(mode, trigger, started_at) -> None — fired before each scheduler tick runs."""

OnRunFinished = Callable[[], None]
"""Fired after each tick, success or failure."""


def compute_next(
    *,
    last_run: SyncRunRecord | None,
    last_full: SyncRunRecord | None,
    config: SyncConfig,
    now: datetime,
) -> datetime:
    """Return the timestamp at which the scheduler should next fire.

    Rules:
      - Never run if config.enabled is False (caller checks separately).
      - If no runs yet -> fire now.
      - If last_run is partial -> fire at last_run.next_resume_at.
      - Else: min(next_incremental, next_full).
        next_incremental = last_run.finished_at + incremental_interval_hours.
        next_full = first 03:00-local-time occurrence >= last_full.finished_at +
                    full_interval_days. Skipped when full_interval_days == 0.
    """
    if last_run is None:
        return now
    if last_run.outcome == "partial" and last_run.next_resume_at is not None:
        return last_run.next_resume_at

    next_incremental = (last_run.finished_at or now) + timedelta(
        hours=config.incremental_interval_hours
    )

    if config.full_interval_days == 0 or last_full is None:
        return next_incremental

    full_threshold = (last_full.finished_at or now) + timedelta(days=config.full_interval_days)
    next_full = _next_local_hour_at_or_after(
        threshold=full_threshold, hour_local=config.full_run_hour_local
    )
    return min(next_incremental, next_full)


def decide_mode(
    *,
    last_full: SyncRunRecord | None,
    config: SyncConfig,
    now: datetime,
) -> str:
    """Return 'full' or 'incremental' for the tick that is firing now."""
    if config.full_interval_days == 0:
        return "incremental"
    if last_full is None:
        return "full"
    threshold = (last_full.finished_at or now) + timedelta(days=config.full_interval_days)
    if now >= threshold:
        return "full"
    return "incremental"


def _next_local_hour_at_or_after(*, threshold: datetime, hour_local: int) -> datetime:
    """Return the first datetime at or after *threshold* whose local hour == hour_local.

    Implementation note: we use the threshold's tz to keep the math timezone-aware.
    The 'local' hour is interpreted in that tz; for production this is UTC unless
    the system clock is configured otherwise.
    """
    candidate = threshold.replace(hour=hour_local, minute=0, second=0, microsecond=0)
    if candidate < threshold:
        candidate += timedelta(days=1)
    return candidate


async def run_scheduler(
    *,
    sync_service: Any,  # SyncService
    manifest: Any,  # Manifest
    config: SyncConfig,
    clock: Any,  # Clock
    shutdown_event: asyncio.Event,
    sync_lock: asyncio.Lock | None = None,
    on_run_started: OnRunStarted | None = None,
    on_run_finished: OnRunFinished | None = None,
) -> None:
    """Drive sync_service.run() at the scheduler's chosen cadence.

    Loops until shutdown_event is set. On every iteration:
      1. Read last_run + last_full from manifest.
      2. Compute next_tick. If now < next_tick, sleep until next_tick
         (with shutdown event check).
      3. Decide mode (full vs incremental) based on last_full.
      4. Bootstrap detection: if no runs yet AND meetings table is empty,
         override mode to 'full' and trigger to 'bootstrap'.
      5. Acquire ``sync_lock`` (if provided) so manual /sync/now triggers
         see the lock as held and 409 instead of starting a concurrent run.
      6. Fire ``on_run_started`` so the web layer can mirror the in-flight
         run on ``app.state.current_sync`` for /sync/status.
      7. Call sync_service.run(...). Catch and log exceptions; failure
         marks the run failed but does not stop the scheduler.
      8. Always fire ``on_run_finished`` and release ``sync_lock``.
    """
    while not shutdown_event.is_set():
        try:
            last_run = manifest.get_last_sync_run()
            last_full = _find_last_completed_full(manifest)

            now = clock.now()
            next_tick = compute_next(last_run=last_run, last_full=last_full, config=config, now=now)
            if next_tick > now:
                wait_seconds = (next_tick - now).total_seconds()
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=wait_seconds)
                    return  # shutdown signaled during sleep
                except TimeoutError:
                    pass

            # Bootstrap: no runs ever and cache empty -> force full sync
            is_bootstrap = last_run is None and _cache_is_empty(manifest)
            if is_bootstrap:
                mode = SyncMode.FULL
                trigger = SyncTrigger.BOOTSTRAP
            else:
                mode_str = decide_mode(last_full=last_full, config=config, now=clock.now())
                mode = SyncMode.FULL if mode_str == "full" else SyncMode.INCREMENTAL
                trigger = SyncTrigger.SCHEDULED

            resume_id = (
                last_run.id if last_run is not None and last_run.outcome == "partial" else None
            )

            if sync_lock is not None:
                await sync_lock.acquire()
            try:
                if on_run_started is not None:
                    # Defensive: a buggy hook must not block the actual sync.
                    with contextlib.suppress(Exception):
                        on_run_started(mode.value, trigger.value, clock.now())
                try:
                    await sync_service.run(mode=mode, trigger=trigger, resume_run_id=resume_id)
                except Exception as exc:
                    # Top-level loop guard: any sync error is logged but does
                    # not stop the scheduler.
                    logger.exception("Scheduler tick failed: %s", exc)
            finally:
                if on_run_finished is not None:
                    with contextlib.suppress(Exception):
                        on_run_finished()
                if sync_lock is not None:
                    sync_lock.release()
        except Exception as exc:
            # Outer guard: never let the scheduler loop die. Sleep a minute
            # before retrying so we don't spin on persistent failures.
            logger.exception("Scheduler loop error: %s", exc)
            await asyncio.sleep(60)


def _find_last_completed_full(manifest: Any) -> SyncRunRecord | None:
    """Return the most recent successful full sync run, or None.

    Delegates to ``Manifest.get_last_full_sync_run`` so a full followed by
    incrementals still finds the full — querying ``get_last_sync_run`` alone
    promoted every tick to a full once an incremental landed.
    """
    last_full: SyncRunRecord | None = manifest.get_last_full_sync_run()
    return last_full


def _cache_is_empty(manifest: Any) -> bool:
    """Return True if there are no meetings cached at all."""
    for _ in manifest.list_known(include_archived=True, include_gone=True):
        return False
    return True
