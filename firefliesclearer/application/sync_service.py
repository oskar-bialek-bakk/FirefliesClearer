"""SyncService — pulls meetings from the live API into the local cache.

Two modes, both async:

- INCREMENTAL: cheap; walks pages newest-first until it hits a meeting
  already cached as live, then halts. Detects new meetings only.
- FULL: expensive; walks every page, builds a seen-id set, then in a
  reconciliation step marks any cached row not in the set as
  source_state='gone'. Detects new + updated + gone-from-source.

Rate-limit handling: catches RateLimitedError once per run, persists
cursor + next_resume_at on the sync_runs row, returns SyncOutcome.partial.
The scheduler resumes from the cursor when the retry window expires.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting
from firefliesclearer.infra.fireflies_client import RateLimitedError
from firefliesclearer.ports.clock import Clock

PAGE_SIZE = 50


def estimate_total(*, seen: int, last_page_size: int) -> int:
    """Estimate the total number of meetings in the source.

    seen: number of meetings yielded so far.
    last_page_size: number of items in the most recent page.

    Heuristic:
    - If last_page_size < PAGE_SIZE → that was the final page → total == seen.
    - If last_page_size == PAGE_SIZE → at least one more page exists → total >= seen + PAGE_SIZE.
    - If seen == 0 → default to PAGE_SIZE (just a placeholder).
    """
    if seen == 0:
        return PAGE_SIZE
    if 0 < last_page_size < PAGE_SIZE:
        return seen
    return seen + PAGE_SIZE


class SyncMode(StrEnum):
    INCREMENTAL = "incremental"
    FULL = "full"


class SyncTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL_REVIEW = "manual_review"
    MANUAL_SETTINGS = "manual_settings"
    BOOTSTRAP = "bootstrap"


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """Summary of one sync run, returned by SyncService.run()."""

    run_id: int
    outcome: str  # 'success' | 'partial' | 'failed'
    meetings_seen: int = 0
    meetings_added: int = 0
    meetings_updated: int = 0
    meetings_gone: int = 0
    next_resume_at: datetime | None = None
    error_message: str | None = None

    @classmethod
    def success(
        cls,
        *,
        run_id: int,
        meetings_seen: int,
        meetings_added: int,
        meetings_updated: int,
        meetings_gone: int,
    ) -> Self:
        return cls(
            run_id=run_id,
            outcome="success",
            meetings_seen=meetings_seen,
            meetings_added=meetings_added,
            meetings_updated=meetings_updated,
            meetings_gone=meetings_gone,
        )

    @classmethod
    def partial(
        cls,
        *,
        run_id: int,
        meetings_seen: int,
        meetings_added: int,
        next_resume_at: datetime,
        meetings_updated: int = 0,
        meetings_gone: int = 0,
    ) -> Self:
        return cls(
            run_id=run_id,
            outcome="partial",
            meetings_seen=meetings_seen,
            meetings_added=meetings_added,
            meetings_updated=meetings_updated,
            meetings_gone=meetings_gone,
            next_resume_at=next_resume_at,
        )

    @classmethod
    def failed(cls, *, run_id: int, error_message: str) -> Self:
        return cls(
            run_id=run_id,
            outcome="failed",
            error_message=error_message,
        )


SnapshotCallback = Callable[[int, int, int, int, int], None]
"""Signature: (seen, added, updated, gone, last_page_size) -> None."""


class SyncService:
    def __init__(
        self,
        *,
        repo: object,  # ControllableMeetingRepository or FirefliesClient
        manifest: Manifest,
        clock: Clock,
        snapshot_callback: SnapshotCallback | None = None,
    ) -> None:
        self._repo = repo
        self._manifest = manifest
        self._clock = clock
        self._snapshot_callback = snapshot_callback

    async def run(
        self,
        *,
        mode: SyncMode,
        trigger: SyncTrigger,
        resume_run_id: int | None = None,
    ) -> SyncOutcome:
        now = self._clock.now()
        if resume_run_id is not None:
            run_id = resume_run_id  # reuse the prior partial row
        else:
            run_id = self._manifest.start_sync_run(mode=mode.value, trigger=trigger.value, at=now)

        try:
            if mode == SyncMode.INCREMENTAL:
                return await self._run_incremental(run_id=run_id, started_at=now)
            if mode == SyncMode.FULL:
                return await self._run_full(
                    run_id=run_id, started_at=now, resume_run_id=resume_run_id
                )
            raise ValueError(f"Unsupported mode: {mode}")
        except Exception as exc:
            # An unexpected error must finalize the sync_runs row so /sync/status
            # and the scheduler can distinguish a crashed run from an active one.
            # RateLimitedError is caught inside _run_incremental / _run_full and
            # never reaches here; this branch handles only unexpected failures.
            with contextlib.suppress(Exception):
                self._manifest.finalize_sync_run(
                    run_id,
                    outcome="failed",
                    at=self._clock.now(),
                    error_message=str(exc),
                )
            raise

    async def _run_incremental(self, *, run_id: int, started_at: datetime) -> SyncOutcome:
        skip = 0
        seen = added = 0
        seen_known = False

        try:
            while not seen_known:
                page: list[Meeting] = []
                async for m in self._repo.list_meetings_page(  # type: ignore[attr-defined]
                    skip=skip, limit=PAGE_SIZE, to_date=None
                ):
                    page.append(m)
                if not page:
                    break

                for raw in page:
                    seen += 1
                    existing = self._manifest.get(raw.meeting_id)
                    if existing is None:
                        self._manifest.upsert_known(raw, at=started_at)
                        added += 1
                    elif existing.source_state == "gone":
                        self._manifest.upsert_known(raw, at=started_at)
                        self._manifest.set_source_state(raw.meeting_id, "live")
                        added += 1
                    else:
                        seen_known = True
                        break

                self._manifest.record_sync_progress(
                    run_id,
                    seen=seen,
                    added=added,
                    updated=0,
                    gone=0,
                    cursor_skip=skip + len(page),
                )
                self._publish_snapshot(
                    seen=seen,
                    added=added,
                    updated=0,
                    gone=0,
                    last_page_size=len(page),
                )

                if seen_known:
                    break
                skip += len(page)
        except RateLimitedError as e:
            return self._record_rate_limited(
                run_id=run_id,
                cursor_skip=skip,
                retry_after=e.retry_after_seconds,
                seen=seen,
                added=added,
                error=str(e),
            )

        self._manifest.finalize_sync_run(run_id, outcome="success", at=self._clock.now())
        return SyncOutcome.success(
            run_id=run_id,
            meetings_seen=seen,
            meetings_added=added,
            meetings_updated=0,
            meetings_gone=0,
        )

    async def _run_full(
        self,
        *,
        run_id: int,
        started_at: datetime,
        resume_run_id: int | None = None,
    ) -> SyncOutcome:
        # Resume support: if resume_run_id given, recover cursor + seen_ids
        # from the prior partial run.
        if resume_run_id is not None:
            prior = self._manifest.get_sync_run(resume_run_id)
            if prior is None or prior.outcome != "partial":
                raise ValueError(f"resume_run_id={resume_run_id} not found or not partial")
            to_date = prior.started_at
            skip = prior.cursor_skip or 0
            seen_ids: list[str] = (
                list(json.loads(prior.seen_ids_json)) if prior.seen_ids_json else []
            )
            seen = prior.meetings_seen
            added = prior.meetings_added
            updated = prior.meetings_updated
        else:
            to_date = started_at
            skip = 0
            seen_ids = []
            seen = added = updated = 0

        try:
            while True:
                page: list[Meeting] = []
                async for m in self._repo.list_meetings_page(  # type: ignore[attr-defined]
                    skip=skip, limit=PAGE_SIZE, to_date=to_date
                ):
                    page.append(m)
                if not page:
                    break

                for raw in page:
                    seen += 1
                    seen_ids.append(raw.meeting_id)
                    existing = self._manifest.get(raw.meeting_id)
                    if existing is None:
                        self._manifest.upsert_known(raw, at=started_at)
                        added += 1
                    else:
                        if self._manifest.update_cache_fields(raw, at=started_at):
                            updated += 1
                        if existing.source_state == "gone":
                            self._manifest.set_source_state(raw.meeting_id, "live")
                            added += 1

                self._manifest.record_sync_progress(
                    run_id,
                    seen=seen,
                    added=added,
                    updated=updated,
                    gone=0,
                    cursor_skip=skip + len(page),
                    seen_ids=seen_ids,
                )
                self._publish_snapshot(
                    seen=seen,
                    added=added,
                    updated=updated,
                    gone=0,
                    last_page_size=len(page),
                )
                skip += len(page)
        except RateLimitedError as e:
            return self._record_rate_limited(
                run_id=run_id,
                cursor_skip=skip,
                retry_after=e.retry_after_seconds,
                seen=seen,
                added=added,
                updated=updated,
                seen_ids=seen_ids,
                error=str(e),
            )

        # Reconciliation
        gone = 0
        seen_set = set(seen_ids)
        for cached in self._manifest.list_known(include_archived=True, include_gone=False):
            if cached.meeting_id not in seen_set:
                self._manifest.set_source_state(cached.meeting_id, "gone")
                gone += 1

        self._manifest.record_sync_progress(
            run_id,
            seen=seen,
            added=added,
            updated=updated,
            gone=gone,
            cursor_skip=skip,
            seen_ids=seen_ids,
        )
        self._manifest.finalize_sync_run(run_id, outcome="success", at=self._clock.now())
        return SyncOutcome.success(
            run_id=run_id,
            meetings_seen=seen,
            meetings_added=added,
            meetings_updated=updated,
            meetings_gone=gone,
        )

    def _publish_snapshot(
        self,
        *,
        seen: int,
        added: int,
        updated: int,
        gone: int,
        last_page_size: int,
    ) -> None:
        """Notify the optional snapshot callback after each progress tick.

        The web layer wires this to update ``app.state.current_sync`` so the
        live banner reflects ``meetings_seen`` / ``last_page_size`` without
        coupling the pure algorithm to FastAPI internals.
        """
        if self._snapshot_callback is None:
            return
        # Defensive: snapshot publish is purely cosmetic for the progress
        # banner; a callback raising must never abort an in-flight sync.
        with contextlib.suppress(Exception):
            self._snapshot_callback(seen, added, updated, gone, last_page_size)

    def _record_rate_limited(
        self,
        *,
        run_id: int,
        cursor_skip: int,
        retry_after: float | None,
        seen: int,
        added: int,
        updated: int = 0,
        seen_ids: list[str] | None = None,
        error: str,
    ) -> SyncOutcome:
        retry_seconds = retry_after if retry_after is not None else 60.0
        next_resume_at = self._clock.now() + timedelta(seconds=retry_seconds)
        # record final progress before flagging partial
        self._manifest.record_sync_progress(
            run_id,
            seen=seen,
            added=added,
            updated=updated,
            gone=0,
            cursor_skip=cursor_skip,
            seen_ids=seen_ids,
        )
        self._manifest.mark_sync_run_partial(
            run_id,
            at=self._clock.now(),
            next_resume_at=next_resume_at,
            error_message=error,
        )
        return SyncOutcome.partial(
            run_id=run_id,
            meetings_seen=seen,
            meetings_added=added,
            meetings_updated=updated,
            next_resume_at=next_resume_at,
        )
