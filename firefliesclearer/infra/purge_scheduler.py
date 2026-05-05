"""Background API-purge trickle scheduler.

Why this exists: Fireflies' Pro plan caps total daily GraphQL ops at 50,
across all operations (list, fetch, delete). A user with 200+ archived
meetings to clean up cannot do it in one session — the daily quota will
trip mid-batch and lock the API until UTC midnight. This scheduler
trickles a small number of API deletes per day in the background, so a
"set and forget" cleanup runs to completion over weeks without burning
the budget needed for sync and ad-hoc archives.

Architecture mirrors :mod:`firefliesclearer.infra.sync_scheduler`:
pure decision functions (``compute_next_purge``) that are unit-tested in
isolation, plus an async loop (``run_purge_scheduler``) that wraps them
with sleep, locking, and error handling.

Trickle behaviour:
- Picks the oldest ARCHIVED / DELETED_FAILED rows up to ``api_purge_per_day``.
- Calls :meth:`PurgeService.purge_meetings` for them in sequence.
- On the first :class:`RateLimitedError`, stops the batch (further deletes
  this UTC day are guaranteed to fail; better to surrender today's budget
  to whatever else needs it than burn requests on locked-out calls).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from typing import Any, Protocol

from firefliesclearer.core.manifest import Manifest, MeetingRecord
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.infra.config import RunConfig
from firefliesclearer.infra.fireflies_client import RateLimitedError
from firefliesclearer.ports.clock import Clock

logger = logging.getLogger(__name__)

# Trickle interval between successive runs. Hard-coded at 24h because
# Fireflies' daily quota resets at UTC midnight; firing more often than
# that would burn budget for no benefit (we're already at the daily cap).
_TRICKLE_INTERVAL = timedelta(hours=24)

# How long to sleep when the trickle is disabled (api_purge_per_day == 0)
# before re-checking the config. Long enough not to spin, short enough
# that flipping the toggle takes effect within an hour.
_DISABLED_RECHECK = timedelta(hours=1)


class _PurgeServiceProto(Protocol):
    """Minimum surface of :class:`PurgeService` used by the trickle.

    Declared as a Protocol so the scheduler module doesn't import
    application/* (preserves the layered architecture contract).
    """

    def purge_meetings(self, meetings: list[Meeting]) -> Any:  # AsyncIterator[PurgeOutcome]
        ...


def compute_next_purge(
    *,
    last_run_finished_at: datetime | None,
    config: RunConfig,
    now: datetime,
) -> datetime | None:
    """Return when the trickle should next fire, or None if disabled.

    - ``api_purge_per_day == 0`` → disabled, returns None.
    - First run (``last_run_finished_at is None``) → fires immediately.
    - Otherwise → ``last_run_finished_at + 24h``.
    """
    if config.api_purge_per_day <= 0:
        return None
    if last_run_finished_at is None:
        return now
    return last_run_finished_at + _TRICKLE_INTERVAL


def candidates_for_trickle(*, manifest: Manifest, limit: int) -> list[MeetingRecord]:
    """Return up to *limit* MeetingRecord rows ready for API delete.

    Order: oldest archived_at first (so a multi-day cleanup drains the
    oldest backlog first). Rows missing ``archived_at`` (legacy data
    that pre-dated that column) sort *after* every row that has a real
    archived_at — the tuple key's first element makes ``None`` sort as
    1 vs 0 for present timestamps. Within the legacy bucket they're
    ordered by ``meeting_date``.

    States: ARCHIVED and DELETED_FAILED only — the same eligibility set
    used by the manual mark-deleted action. Rows in
    ``source_state='gone'`` are excluded (sync already learned they're
    not in upstream; the API delete would 404 anyway and the
    auto-reconcile in full sync handles them locally).
    """
    if limit <= 0:
        return []
    eligible_states = (MeetingState.ARCHIVED, MeetingState.DELETED_FAILED)
    candidates: list[MeetingRecord] = []
    for state in eligible_states:
        for mid in manifest.meeting_ids_in_states((state,)):
            rec = manifest.get(mid)
            if rec is None or rec.source_state == "gone":
                continue
            candidates.append(rec)
    candidates.sort(key=lambda r: (r.archived_at is None, r.archived_at or r.meeting_date))
    return candidates[:limit]


def _record_to_meeting(rec: MeetingRecord) -> Meeting:
    """Project a MeetingRecord back to the immutable Meeting needed by
    PurgeService. Mirrors ``_fetch_meeting_from_cache`` in dashboard.py
    but keeps that helper's web-layer dependencies out of infra/."""
    return Meeting(
        meeting_id=rec.meeting_id,
        title=rec.title,
        meeting_date=rec.meeting_date,
        duration_minutes=float(rec.duration_minutes) if rec.duration_minutes is not None else 0.0,
        host_email=rec.host_email or "",
        participant_count=int(rec.participant_count) if rec.participant_count is not None else 0,
        tags=rec.tags or (),
        has_transcript=bool(rec.has_transcript) if rec.has_transcript is not None else True,
    )


async def run_one_trickle(
    *,
    purge_service: _PurgeServiceProto,
    manifest: Manifest,
    limit: int,
) -> tuple[int, int]:
    """Execute a single trickle pass; return ``(deleted, attempted)``.

    Stops early on :class:`RateLimitedError` (the daily quota tripped) so
    the rest of today's request budget is preserved for whatever else
    might run (sync, archive). Per-meeting failures other than rate-limit
    are logged but do not abort the loop.
    """
    candidates = candidates_for_trickle(manifest=manifest, limit=limit)
    if not candidates:
        return (0, 0)
    deleted = 0
    attempted = 0
    for rec in candidates:
        attempted += 1
        meeting = _record_to_meeting(rec)
        try:
            async for outcome in purge_service.purge_meetings([meeting]):
                if outcome.state is MeetingState.DELETED:
                    deleted += 1
                else:
                    logger.info(
                        "purge_trickle: %s did not delete (state=%s, error=%s)",
                        rec.meeting_id,
                        outcome.state.value,
                        outcome.error,
                    )
        except RateLimitedError as e:
            logger.info(
                "purge_trickle: stopping after %d attempt(s) — rate-limited "
                "(retry_after_seconds=%s)",
                attempted,
                e.retry_after_seconds,
            )
            break
        except Exception:
            logger.exception("purge_trickle: unexpected error on %s", rec.meeting_id)
            # Continue with the next candidate — one bad row shouldn't
            # halt the trickle. The row stays in DELETED_FAILED for the
            # next pass.
            continue
    return (deleted, attempted)


async def run_purge_scheduler(
    *,
    purge_service: _PurgeServiceProto,
    manifest: Manifest,
    config: RunConfig,
    clock: Clock,
    shutdown_event: asyncio.Event,
    purge_lock: asyncio.Lock | None = None,
) -> None:
    """Drive ``run_one_trickle`` at the daily cadence.

    Loops until ``shutdown_event`` is set. Same shape as
    :func:`run_scheduler` in sync_scheduler — kept similar so the
    operational story is the same: park the task on
    ``app.state.purge_scheduler_task``, set ``shutdown_event`` on app
    shutdown, optional lock to serialize manual triggers later.
    """
    last_run_finished_at: datetime | None = None
    while not shutdown_event.is_set():
        try:
            now = clock.now()
            next_tick = compute_next_purge(
                last_run_finished_at=last_run_finished_at,
                config=config,
                now=now,
            )
            if next_tick is None:
                # Disabled — sleep then re-check (operator may flip the
                # config without restarting the app).
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=_DISABLED_RECHECK.total_seconds(),
                    )
                if shutdown_event.is_set():
                    return
                continue
            wait_seconds = (next_tick - now).total_seconds()
            if wait_seconds > 0:
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=wait_seconds)
                    return  # shutdown signaled mid-sleep
                except TimeoutError:
                    pass
            if purge_lock is not None:
                await purge_lock.acquire()
            try:
                deleted, attempted = await run_one_trickle(
                    purge_service=purge_service,
                    manifest=manifest,
                    limit=config.api_purge_per_day,
                )
                if attempted:
                    logger.info(
                        "purge_trickle: deleted %d/%d (cap %d/day)",
                        deleted,
                        attempted,
                        config.api_purge_per_day,
                    )
            except Exception:
                logger.exception("purge_trickle tick failed")
            finally:
                if purge_lock is not None:
                    purge_lock.release()
                last_run_finished_at = clock.now()
        except Exception:
            logger.exception("purge_scheduler outer loop error")
            # Sleep a minute so we don't spin on persistent failures.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=60)
