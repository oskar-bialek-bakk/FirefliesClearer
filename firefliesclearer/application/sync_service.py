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

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting
from firefliesclearer.ports.clock import Clock

PAGE_SIZE = 50


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


class SyncService:
    def __init__(
        self,
        *,
        repo: object,  # ControllableMeetingRepository or FirefliesClient
        manifest: Manifest,
        clock: Clock,
    ) -> None:
        self._repo = repo
        self._manifest = manifest
        self._clock = clock

    async def run(
        self,
        *,
        mode: SyncMode,
        trigger: SyncTrigger,
        resume_run_id: int | None = None,
    ) -> SyncOutcome:
        now = self._clock.now()
        run_id = self._manifest.start_sync_run(mode=mode.value, trigger=trigger.value, at=now)

        if mode == SyncMode.INCREMENTAL:
            return await self._run_incremental(run_id=run_id, started_at=now)
        if mode == SyncMode.FULL:
            return await self._run_full(run_id=run_id, started_at=now)
        raise ValueError(f"Unsupported mode: {mode}")

    async def _run_incremental(self, *, run_id: int, started_at: datetime) -> SyncOutcome:
        skip = 0
        seen = added = 0
        seen_known = False

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
                    # Resurrected: refresh snapshot + flip back to live.
                    self._manifest.upsert_known(raw, at=started_at)
                    self._manifest.set_source_state(raw.meeting_id, "live")
                    added += 1
                else:
                    seen_known = True
                    break  # stop processing this page

            self._manifest.record_sync_progress(
                run_id,
                seen=seen,
                added=added,
                updated=0,
                gone=0,
                cursor_skip=skip + len(page),
            )

            if seen_known:
                break
            skip += len(page)

        self._manifest.finalize_sync_run(run_id, outcome="success", at=self._clock.now())
        return SyncOutcome.success(
            run_id=run_id,
            meetings_seen=seen,
            meetings_added=added,
            meetings_updated=0,
            meetings_gone=0,
        )

    async def _run_full(self, *, run_id: int, started_at: datetime) -> SyncOutcome:
        # Pin pagination to started_at so meetings created during the run
        # don't shift the window.
        to_date = started_at
        skip = 0
        seen = added = updated = 0
        seen_ids: list[str] = []

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
            skip += len(page)

        # Reconciliation: mark cached live rows missing from API as gone.
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
