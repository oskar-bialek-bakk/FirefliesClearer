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
