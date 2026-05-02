"""Sync scheduler — decides when and what mode to run, then drives SyncService.

The decision logic (compute_next, decide_mode) is pure and unit-tested.
The asyncio loop (run_scheduler) wraps it with sleep + lock + actual
SyncService.run invocation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from firefliesclearer.core.manifest import SyncRunRecord
from firefliesclearer.infra.config import SyncConfig

logger = logging.getLogger(__name__)


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
) -> None:
    """Drive sync_service.run() at the scheduler's chosen cadence.

    Loops until shutdown_event is set. On every iteration:
      1. Read last_run + last_full from manifest.
      2. Compute next_tick. If now < next_tick, sleep until next_tick
         (with shutdown event check).
      3. Decide mode (full vs incremental) based on last_full.
      4. Bootstrap detection: if no runs yet AND meetings table is empty,
         override mode to 'full' and trigger to 'bootstrap'.
      5. Call sync_service.run(...). Catch and log exceptions; failure
         marks the run failed but does not stop the scheduler.
    """
    from firefliesclearer.application.sync_service import SyncMode, SyncTrigger

    while not shutdown_event.is_set():
        try:
            last_run = manifest.get_last_sync_run()
            # last_full is the most recent finished full run; manifest doesn't
            # have a dedicated lookup, so we filter in Python (small data).
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
            try:
                await sync_service.run(mode=mode, trigger=trigger, resume_run_id=resume_id)
            except Exception as exc:
                # Top-level loop guard: any sync error is logged but does
                # not stop the scheduler.
                logger.exception("Scheduler tick failed: %s", exc)
        except Exception as exc:
            # Outer guard: never let the scheduler loop die. Sleep a minute
            # before retrying so we don't spin on persistent failures.
            logger.exception("Scheduler loop error: %s", exc)
            await asyncio.sleep(60)


def _find_last_completed_full(manifest: Any) -> SyncRunRecord | None:
    """Walk recent sync_runs for the most recent full+success record."""
    last: SyncRunRecord | None = manifest.get_last_sync_run()
    if last is None:
        return None
    if last.mode == "full" and last.outcome == "success":
        return last
    # In Phase 2 we only have get_last_sync_run; querying by mode is a
    # Phase 4 concern when the UI wants it. For now, accept that
    # "no recent full" means "treat as never ran a full".
    return None


def _cache_is_empty(manifest: Any) -> bool:
    """Return True if there are no meetings cached at all."""
    for _ in manifest.list_known(include_archived=True, include_gone=True):
        return False
    return True
