"""Sync scheduler — decides when and what mode to run, then drives SyncService.

The decision logic (compute_next, decide_mode) is pure and unit-tested.
The asyncio loop (run_scheduler) wraps it with sleep + lock + actual
SyncService.run invocation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

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
