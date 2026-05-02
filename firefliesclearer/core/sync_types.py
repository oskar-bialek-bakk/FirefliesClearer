"""Pure value types shared between the sync engine and its scheduler.

Lives in ``core/`` so ``infra.sync_scheduler`` can construct mode/trigger
values without importing from ``application/`` (which import-linter
forbids — see Contract 4 in pyproject.toml).
"""

from __future__ import annotations

from enum import StrEnum


class SyncMode(StrEnum):
    INCREMENTAL = "incremental"
    FULL = "full"


class SyncTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL_REVIEW = "manual_review"
    MANUAL_SETTINGS = "manual_settings"
    BOOTSTRAP = "bootstrap"
