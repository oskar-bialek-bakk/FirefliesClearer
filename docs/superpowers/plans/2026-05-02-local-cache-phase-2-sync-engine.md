# Local-cache Phase 2: Sync Engine + Scheduler — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the `SyncService` algorithm and `sync_scheduler` background task. Behind a `[sync] enabled = false` flag (default off), so existing users see no behavior change. Phase 1's manifest primitives are now consumed by a real sync.

**Architecture:** `application/sync_service.py` is a pure async algorithm (incremental + full reconciliation). `infra/sync_scheduler.py` is an asyncio background task wrapping it with clock-based scheduling. `serve_cmd` starts the scheduler when `[sync] enabled = true`. Tests use a controllable fake repo to exercise every code path. No web routes, no UI — Phase 4 builds those.

**Tech Stack:** Python 3.13, asyncio, SQLite (via Phase 1's Manifest), pytest, pytest-asyncio (auto mode), mypy strict, ruff.

**Spec reference:** `docs/superpowers/specs/2026-05-02-local-cache-design.md` — sections "Sync engine", "Trigger surface" (scheduler subsection), "Phased rollout / Phase 2".

**Depends on:** Phase 1 (Manifest with `upsert_known`, `set_source_state`, `update_cache_fields`, `list_known`, and `sync_runs` table) must be merged.

---

## File Structure

| File | Purpose | Change type |
|------|---------|-------------|
| `firefliesclearer/infra/config.py` | Add `SyncConfig` Pydantic model + integrate into `AppConfig` | Modify (~30 LOC) |
| `firefliesclearer/core/manifest.py` | Add `sync_runs` helper methods (start_run, finalize_run, mark_partial, get_last_run, get_run_by_id) | Modify (~120 LOC) |
| `firefliesclearer/application/sync_service.py` | New — pure algorithm (incremental, full, rate-limit, resume) | Create (~250 LOC) |
| `firefliesclearer/infra/sync_scheduler.py` | New — asyncio task wrapper around SyncService | Create (~120 LOC) |
| `firefliesclearer/cli/serve_cmd.py` | Wire scheduler start into serve lifecycle when flag enabled | Modify (~20 LOC) |
| `tests/fakes/controllable_repository.py` | New — test fake with controllable pagination + rate-limit injection | Create (~80 LOC) |
| `tests/core/test_manifest.py` | Add tests for new sync_runs helpers | Modify (~120 LOC) |
| `tests/application/test_sync_service.py` | New — full algorithm coverage | Create (~400 LOC) |
| `tests/infra/test_sync_scheduler.py` | New — decision logic | Create (~150 LOC) |
| `tests/infra/test_config.py` | Add SyncConfig tests | Modify (~50 LOC) |

---

## Tasks

### Task 1: Add `SyncConfig` to `AppConfig`

**Files:**
- Modify: `firefliesclearer/infra/config.py`
- Test: `tests/infra/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/infra/test_config.py`:

```python
def test_sync_config_defaults_when_section_missing(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "/tmp/x"
""",
        encoding="utf-8",
    )
    cfg = load_config(user_config=cfg_path)
    assert cfg.sync.enabled is False
    assert cfg.sync.incremental_interval_hours == 6
    assert cfg.sync.full_interval_days == 7
    assert cfg.sync.full_run_hour_local == 3


def test_sync_config_explicit_values(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "/tmp/x"
[sync]
enabled = true
incremental_interval_hours = 12
full_interval_days = 30
full_run_hour_local = 4
""",
        encoding="utf-8",
    )
    cfg = load_config(user_config=cfg_path)
    assert cfg.sync.enabled is True
    assert cfg.sync.incremental_interval_hours == 12
    assert cfg.sync.full_interval_days == 30
    assert cfg.sync.full_run_hour_local == 4


def test_sync_config_full_interval_days_zero_disables_full(tmp_path):
    """0 means 'never run full reconciliation' — incremental-only mode."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "/tmp/x"
[sync]
full_interval_days = 0
""",
        encoding="utf-8",
    )
    cfg = load_config(user_config=cfg_path)
    assert cfg.sync.full_interval_days == 0


def test_sync_config_validates_hour_range(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "/tmp/x"
[sync]
full_run_hour_local = 25
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(user_config=cfg_path)
```

(If imports `pytest`, `load_config`, `ConfigError` aren't already present at the top of the test file, add them.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/infra/test_config.py::test_sync_config_defaults_when_section_missing tests/infra/test_config.py::test_sync_config_explicit_values tests/infra/test_config.py::test_sync_config_full_interval_days_zero_disables_full tests/infra/test_config.py::test_sync_config_validates_hour_range --no-cov -v`
Expected: 4 FAILs — `cfg.sync` doesn't exist yet.

- [ ] **Step 3: Add `SyncConfig` and integrate**

In `firefliesclearer/infra/config.py`, after the `RunConfig` class and before `AppConfig`, add:

```python
class SyncConfig(BaseModel):
    """Configuration for the local-cache sync engine.

    enabled: master flag — when False (default), the scheduler does not
        start, and read paths fall back to the live repo. Phase 6 flips
        the default to True.
    incremental_interval_hours: cadence for cheap "find new meetings" passes.
    full_interval_days: cadence for full reconciliation. 0 disables it
        (incremental-only mode).
    full_run_hour_local: local hour-of-day to align full reconciliations
        to so they don't compete with daytime use.
    """

    enabled: bool = False
    incremental_interval_hours: int = Field(default=6, ge=1, le=168)
    full_interval_days: int = Field(default=7, ge=0, le=365)
    full_run_hour_local: int = Field(default=3, ge=0, le=23)
```

In the same file, change `AppConfig`:

```python
class AppConfig(BaseModel):
    fireflies: FirefliesConfig
    archive: ArchiveConfig
    rules: dict[str, Any] = Field(default_factory=dict)
    run: RunConfig = Field(default_factory=RunConfig)
    presets: list[Preset] = Field(default_factory=list)
```

To:

```python
class AppConfig(BaseModel):
    fireflies: FirefliesConfig
    archive: ArchiveConfig
    rules: dict[str, Any] = Field(default_factory=dict)
    run: RunConfig = Field(default_factory=RunConfig)
    presets: list[Preset] = Field(default_factory=list)
    sync: SyncConfig = Field(default_factory=SyncConfig)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/infra/test_config.py --no-cov -v`
Expected: all tests PASS, including all pre-existing config tests (regression check).

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/infra/config.py tests/infra/test_config.py
git commit -m "feat(config): add [sync] section with defaults

enabled defaults to false so Phase 2 lands silently. Intervals
configurable; full_interval_days = 0 disables full reconciliation
entirely (incremental-only mode for users who want minimum API budget).
Hour validated to 0-23."
```

---

### Task 2: Add `sync_runs` helpers to Manifest

**Files:**
- Modify: `firefliesclearer/core/manifest.py`
- Test: `tests/core/test_manifest.py`

The Phase 1 `sync_runs` table is empty plumbing. This task adds the read/write methods that `SyncService` will use.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_manifest.py`:

```python
def test_start_sync_run_inserts_running_row(manifest):
    run_id = manifest.start_sync_run(mode="incremental", trigger="manual_review", at=NOW)
    assert isinstance(run_id, int)
    rec = manifest.get_sync_run(run_id)
    assert rec is not None
    assert rec.mode == "incremental"
    assert rec.trigger_source == "manual_review"
    assert rec.outcome == "running"
    assert rec.started_at == NOW
    assert rec.finished_at is None


def test_record_sync_progress_updates_counters(manifest):
    run_id = manifest.start_sync_run(mode="full", trigger="bootstrap", at=NOW)
    manifest.record_sync_progress(
        run_id, seen=50, added=45, updated=3, gone=0, cursor_skip=50, seen_ids=["a", "b"]
    )
    rec = manifest.get_sync_run(run_id)
    assert rec.meetings_seen == 50
    assert rec.meetings_added == 45
    assert rec.meetings_updated == 3
    assert rec.meetings_gone == 0
    assert rec.cursor_skip == 50
    assert rec.seen_ids_json is not None  # JSON array


def test_finalize_sync_run_success(manifest):
    later = datetime(2026, 5, 2, 13, 0, tzinfo=UTC)
    run_id = manifest.start_sync_run(mode="incremental", trigger="scheduled", at=NOW)
    manifest.finalize_sync_run(run_id, outcome="success", at=later)
    rec = manifest.get_sync_run(run_id)
    assert rec.outcome == "success"
    assert rec.finished_at == later
    assert rec.error_message is None


def test_finalize_sync_run_failed_with_error(manifest):
    run_id = manifest.start_sync_run(mode="full", trigger="scheduled", at=NOW)
    manifest.finalize_sync_run(
        run_id, outcome="failed", at=NOW, error_message="API key invalid"
    )
    rec = manifest.get_sync_run(run_id)
    assert rec.outcome == "failed"
    assert rec.error_message == "API key invalid"


def test_mark_sync_run_partial(manifest):
    later = datetime(2026, 5, 2, 13, 0, tzinfo=UTC)
    resume = datetime(2026, 5, 2, 14, 0, tzinfo=UTC)
    run_id = manifest.start_sync_run(mode="full", trigger="scheduled", at=NOW)
    manifest.mark_sync_run_partial(
        run_id, at=later, next_resume_at=resume, error_message="rate-limited"
    )
    rec = manifest.get_sync_run(run_id)
    assert rec.outcome == "partial"
    assert rec.finished_at == later
    assert rec.next_resume_at == resume
    assert rec.error_message == "rate-limited"


def test_get_last_sync_run_returns_most_recent(manifest):
    earlier = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    later = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    manifest.start_sync_run(mode="incremental", trigger="scheduled", at=earlier)
    rid2 = manifest.start_sync_run(mode="full", trigger="scheduled", at=later)
    last = manifest.get_last_sync_run()
    assert last is not None
    assert last.id == rid2


def test_get_last_sync_run_none_when_empty(manifest):
    assert manifest.get_last_sync_run() is None


def test_get_sync_run_unknown_id_returns_none(manifest):
    assert manifest.get_sync_run(999_999) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py -k "sync_run or sync_progress" --no-cov -v`
Expected: 8 FAILs.

- [ ] **Step 3: Implement helpers + read shape**

In `firefliesclearer/core/manifest.py`, after the existing `StateLogEntry` dataclass (around line 90), add:

```python
@dataclass(frozen=True, slots=True)
class SyncRunRecord:
    id: int
    mode: str
    trigger_source: str
    started_at: datetime
    finished_at: datetime | None
    outcome: str
    meetings_seen: int
    meetings_added: int
    meetings_updated: int
    meetings_gone: int
    cursor_skip: int | None
    seen_ids_json: str | None
    next_resume_at: datetime | None
    error_message: str | None
```

In the same file, add these methods on the `Manifest` class (after `list_known` from Phase 1):

```python
def start_sync_run(self, *, mode: str, trigger: str, at: datetime) -> int:
    cur = self._conn.execute(
        "INSERT INTO sync_runs (mode, trigger_source, started_at, outcome) "
        "VALUES (?, ?, ?, 'running')",
        (mode, trigger, _iso(at)),
    )
    row = self._conn.execute("SELECT last_insert_rowid()").fetchone()
    return int(row[0])

def record_sync_progress(
    self,
    run_id: int,
    *,
    seen: int,
    added: int,
    updated: int,
    gone: int,
    cursor_skip: int | None,
    seen_ids: list[str] | None = None,
) -> None:
    seen_ids_json = json.dumps(seen_ids) if seen_ids is not None else None
    self._conn.execute(
        "UPDATE sync_runs SET "
        "  meetings_seen = ?, meetings_added = ?, meetings_updated = ?, "
        "  meetings_gone = ?, cursor_skip = ?, seen_ids_json = ? "
        "WHERE id = ?",
        (seen, added, updated, gone, cursor_skip, seen_ids_json, run_id),
    )

def finalize_sync_run(
    self,
    run_id: int,
    *,
    outcome: str,
    at: datetime,
    error_message: str | None = None,
) -> None:
    self._conn.execute(
        "UPDATE sync_runs SET outcome = ?, finished_at = ?, error_message = ? "
        "WHERE id = ?",
        (outcome, _iso(at), error_message, run_id),
    )

def mark_sync_run_partial(
    self,
    run_id: int,
    *,
    at: datetime,
    next_resume_at: datetime,
    error_message: str | None = None,
) -> None:
    self._conn.execute(
        "UPDATE sync_runs SET outcome = 'partial', finished_at = ?, "
        "  next_resume_at = ?, error_message = ? "
        "WHERE id = ?",
        (_iso(at), _iso(next_resume_at), error_message, run_id),
    )

def get_sync_run(self, run_id: int) -> SyncRunRecord | None:
    row = self._conn.execute(
        "SELECT id, mode, trigger_source, started_at, finished_at, outcome, "
        "  meetings_seen, meetings_added, meetings_updated, meetings_gone, "
        "  cursor_skip, seen_ids_json, next_resume_at, error_message "
        "FROM sync_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_sync_run_record(row)

def get_last_sync_run(self) -> SyncRunRecord | None:
    row = self._conn.execute(
        "SELECT id, mode, trigger_source, started_at, finished_at, outcome, "
        "  meetings_seen, meetings_added, meetings_updated, meetings_gone, "
        "  cursor_skip, seen_ids_json, next_resume_at, error_message "
        "FROM sync_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return _row_to_sync_run_record(row)
```

Add the row-to-record helper near the other module-level helpers (`_iso`, `_parse_iso`):

```python
def _row_to_sync_run_record(row: tuple) -> SyncRunRecord:
    started_at = _parse_iso(row[3])
    assert started_at is not None
    return SyncRunRecord(
        id=int(row[0]),
        mode=row[1],
        trigger_source=row[2],
        started_at=started_at,
        finished_at=_parse_iso(row[4]),
        outcome=row[5],
        meetings_seen=int(row[6]),
        meetings_added=int(row[7]),
        meetings_updated=int(row[8]),
        meetings_gone=int(row[9]),
        cursor_skip=row[10],
        seen_ids_json=row[11],
        next_resume_at=_parse_iso(row[12]),
        error_message=row[13],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/core/test_manifest.py --no-cov -v`
Expected: all tests PASS, including the existing 40+ tests (regression check).

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/manifest.py tests/core/test_manifest.py
git commit -m "feat(manifest): sync_runs helpers (start/progress/finalize/partial)

Adds SyncRunRecord read shape and five Manifest methods used by
SyncService: start_sync_run, record_sync_progress, finalize_sync_run,
mark_sync_run_partial, get_sync_run, get_last_sync_run."
```

---

### Task 3: Define `SyncMode`, `SyncTrigger`, `SyncOutcome` types

**Files:**
- Create: `firefliesclearer/application/sync_service.py`
- Test: `tests/application/test_sync_service.py`

Skeleton for the service module. Just types this task; algorithm follows.

- [ ] **Step 1: Write the failing tests**

Create `tests/application/test_sync_service.py`:

```python
"""Tests for SyncService — incremental + full reconciliation algorithms."""

from __future__ import annotations

from datetime import UTC, datetime

from firefliesclearer.application.sync_service import (
    SyncMode,
    SyncOutcome,
    SyncTrigger,
)


def test_sync_mode_values():
    assert SyncMode.INCREMENTAL.value == "incremental"
    assert SyncMode.FULL.value == "full"


def test_sync_trigger_values():
    assert SyncTrigger.SCHEDULED.value == "scheduled"
    assert SyncTrigger.MANUAL_REVIEW.value == "manual_review"
    assert SyncTrigger.MANUAL_SETTINGS.value == "manual_settings"
    assert SyncTrigger.BOOTSTRAP.value == "bootstrap"


def test_sync_outcome_success_factory():
    out = SyncOutcome.success(
        run_id=1, meetings_seen=10, meetings_added=5, meetings_updated=2, meetings_gone=0
    )
    assert out.run_id == 1
    assert out.outcome == "success"
    assert out.meetings_seen == 10
    assert out.meetings_added == 5
    assert out.meetings_updated == 2
    assert out.meetings_gone == 0


def test_sync_outcome_partial_factory():
    resume = datetime(2026, 5, 2, 14, 0, tzinfo=UTC)
    out = SyncOutcome.partial(
        run_id=2, meetings_seen=20, meetings_added=10, next_resume_at=resume
    )
    assert out.outcome == "partial"
    assert out.next_resume_at == resume


def test_sync_outcome_failed_factory():
    out = SyncOutcome.failed(run_id=3, error_message="API key invalid")
    assert out.outcome == "failed"
    assert out.error_message == "API key invalid"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py --no-cov -v`
Expected: ImportError or 5 FAILs because the module doesn't exist.

- [ ] **Step 3: Create the module skeleton**

Create `firefliesclearer/application/sync_service.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py --no-cov -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/application/sync_service.py tests/application/test_sync_service.py
git commit -m "feat(sync): SyncMode/SyncTrigger enums + SyncOutcome dataclass

Skeleton for the sync service. Algorithms land in subsequent tasks."
```

---

### Task 4: Add controllable test repository

**Files:**
- Create: `tests/fakes/controllable_repository.py`

`InMemoryMeetingRepository` doesn't support deterministic pagination or rate-limit injection, both required for sync tests.

- [ ] **Step 1: Write the failing test**

Add to `tests/application/test_sync_service.py`:

```python
from tests.fakes.controllable_repository import ControllableMeetingRepository
from firefliesclearer.core.models import Meeting


def _meeting(meeting_id: str) -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title=f"Meeting {meeting_id}",
        meeting_date=datetime(2026, 4, 1, tzinfo=UTC),
        duration_minutes=30.0,
        host_email="a@x.com",
        participant_count=3,
        tags=(),
        has_transcript=True,
    )


async def test_controllable_repo_paginates_by_skip():
    repo = ControllableMeetingRepository(
        meetings=[_meeting(f"m{i}") for i in range(5)],
        page_size=2,
    )
    # skip=0, limit=2 → first two
    page = []
    async for m in repo.list_meetings_page(skip=0, limit=2):
        page.append(m.meeting_id)
    assert page == ["m0", "m1"]


async def test_controllable_repo_raises_rate_limit_at_configured_skip():
    from firefliesclearer.infra.fireflies_client import RateLimitedError

    repo = ControllableMeetingRepository(
        meetings=[_meeting(f"m{i}") for i in range(10)],
        page_size=5,
        raise_rate_limit_after_skip=5,
    )
    # First page (skip=0) succeeds
    page1 = [m async for m in repo.list_meetings_page(skip=0, limit=5)]
    assert len(page1) == 5
    # Second page (skip=5) raises
    import pytest
    with pytest.raises(RateLimitedError):
        async for _ in repo.list_meetings_page(skip=5, limit=5):
            pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py -k controllable --no-cov -v`
Expected: ImportError on `ControllableMeetingRepository`.

- [ ] **Step 3: Create the fake**

Create `tests/fakes/controllable_repository.py`:

```python
"""Test fake — paginated repository with rate-limit injection.

Unlike InMemoryMeetingRepository, this fake exposes a page-by-page
list_meetings_page method whose pagination matches the FirefliesClient
contract (skip + limit + toDate). It can also be configured to raise
RateLimitedError at a specific skip value, simulating Fireflies'
quota responses for sync-engine tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from firefliesclearer.core.models import Meeting
from firefliesclearer.infra.fireflies_client import RateLimitedError


class ControllableMeetingRepository:
    def __init__(
        self,
        *,
        meetings: list[Meeting],
        page_size: int = 50,
        raise_rate_limit_after_skip: int | None = None,
        raise_rate_limit_retry_after_seconds: float = 60.0,
    ) -> None:
        self._meetings = list(meetings)
        self._page_size = page_size
        self._raise_at = raise_rate_limit_after_skip
        self._retry_after = raise_rate_limit_retry_after_seconds
        self.list_calls: list[tuple[int, int, datetime | None]] = []  # (skip, limit, to_date)

    async def list_meetings_page(
        self,
        *,
        skip: int,
        limit: int,
        to_date: datetime | None = None,
    ) -> AsyncIterator[Meeting]:
        self.list_calls.append((skip, limit, to_date))
        if self._raise_at is not None and skip >= self._raise_at:
            raise RateLimitedError(
                "controllable fake: rate-limited",
                retry_after_seconds=self._retry_after,
            )
        # Filter by to_date (upper bound on meeting_date) to mirror real API
        candidates = (
            [m for m in self._meetings if m.meeting_date < to_date]
            if to_date is not None
            else list(self._meetings)
        )
        # Pagination
        page = candidates[skip : skip + limit]
        for m in page:
            yield m
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py -k controllable --no-cov -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fakes/controllable_repository.py tests/application/test_sync_service.py
git commit -m "test: ControllableMeetingRepository for sync engine tests

Supports deterministic skip/limit pagination + RateLimitedError
injection at a configured skip threshold. Records every list call
so tests can assert on pagination behavior."
```

---

### Task 5: Implement `SyncService.run()` happy-path incremental

**Files:**
- Modify: `firefliesclearer/application/sync_service.py`
- Test: `tests/application/test_sync_service.py`

Just the happy path: walk pages, upsert each meeting, halt on first known live row. No rate-limit handling, no resume, no full mode yet.

- [ ] **Step 1: Write the failing tests**

Add to `tests/application/test_sync_service.py`:

```python
from firefliesclearer.application.sync_service import SyncService
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.infra.system_clock import SystemClock


@pytest.fixture
def manifest_db(tmp_path):
    return Manifest.open(tmp_path / "manifest.db")


async def test_incremental_sync_inserts_all_meetings_when_cache_empty(manifest_db):
    repo = ControllableMeetingRepository(
        meetings=[_meeting(f"m{i}") for i in range(3)],
        page_size=2,
    )
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    outcome = await svc.run(mode=SyncMode.INCREMENTAL, trigger=SyncTrigger.SCHEDULED)

    assert outcome.outcome == "success"
    assert outcome.meetings_added == 3
    assert outcome.meetings_seen == 3
    # All three rows are now in the cache
    assert {m.meeting_id for m in manifest_db.list_known()} == {"m0", "m1", "m2"}


async def test_incremental_sync_halts_at_first_known_live_meeting(manifest_db):
    """If m0 is already cached, sync stops after seeing it (page boundary)."""
    # Pre-populate cache with m0
    manifest_db.upsert_known(_meeting("m0"), at=datetime(2026, 4, 1, tzinfo=UTC))

    # Repo has m99 (newest), m0 (already cached), m1, m2
    repo = ControllableMeetingRepository(
        meetings=[_meeting("m99"), _meeting("m0"), _meeting("m1"), _meeting("m2")],
        page_size=2,
    )
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    outcome = await svc.run(mode=SyncMode.INCREMENTAL, trigger=SyncTrigger.SCHEDULED)

    # m99 was added; m0 stops the loop; m1 and m2 not seen (older than m0)
    assert outcome.meetings_added == 1
    cached = {m.meeting_id for m in manifest_db.list_known()}
    assert cached == {"m0", "m99"}
    # Repo was called twice: skip=0 (got m99, m0; m0 stops) — actually only once
    assert len(repo.list_calls) == 1


async def test_incremental_sync_records_run_in_sync_runs_table(manifest_db):
    repo = ControllableMeetingRepository(meetings=[_meeting("m0")], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    outcome = await svc.run(mode=SyncMode.INCREMENTAL, trigger=SyncTrigger.MANUAL_REVIEW)

    rec = manifest_db.get_sync_run(outcome.run_id)
    assert rec is not None
    assert rec.mode == "incremental"
    assert rec.trigger_source == "manual_review"
    assert rec.outcome == "success"
    assert rec.meetings_added == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py -k incremental --no-cov -v`
Expected: 3 FAILs — `SyncService` class doesn't exist.

- [ ] **Step 3: Implement `SyncService.run()` for incremental happy-path**

Append to `firefliesclearer/application/sync_service.py`:

```python
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting
from firefliesclearer.ports.clock import Clock

PAGE_SIZE = 50


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
        run_id = self._manifest.start_sync_run(
            mode=mode.value, trigger=trigger.value, at=now
        )

        if mode == SyncMode.INCREMENTAL:
            return await self._run_incremental(run_id=run_id, started_at=now)
        # FULL mode added in a later task
        raise NotImplementedError("Full sync added in Task 7")

    async def _run_incremental(self, *, run_id: int, started_at: datetime) -> SyncOutcome:
        skip = 0
        seen = added = 0
        seen_known = False

        while not seen_known:
            page: list[Meeting] = []
            async for m in self._repo.list_meetings_page(
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
                else:
                    seen_known = True
                    break  # stop processing this page

            self._manifest.record_sync_progress(
                run_id, seen=seen, added=added, updated=0, gone=0,
                cursor_skip=skip + len(page),
            )

            if seen_known:
                break
            skip += len(page)

        self._manifest.finalize_sync_run(
            run_id, outcome="success", at=self._clock.now()
        )
        return SyncOutcome.success(
            run_id=run_id,
            meetings_seen=seen,
            meetings_added=added,
            meetings_updated=0,
            meetings_gone=0,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py -k incremental --no-cov -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/application/sync_service.py tests/application/test_sync_service.py
git commit -m "feat(sync): SyncService.run incremental happy-path

Walks pages, upserts new rows, halts at first cached live meeting.
Records the run in sync_runs with success outcome. No rate-limit
handling or resume yet — those land in subsequent tasks."
```

---

### Task 6: Add resurrection handling (gone → live on incremental)

**Files:**
- Modify: `firefliesclearer/application/sync_service.py`
- Test: `tests/application/test_sync_service.py`

When an incremental sync sees a meeting that's `source_state='gone'` in the cache, treat it as "added back" — flip to live and count as added.

- [ ] **Step 1: Write the failing test**

Add to `tests/application/test_sync_service.py`:

```python
async def test_incremental_sync_resurrects_gone_meeting(manifest_db):
    """A meeting marked 'gone' that reappears in API → flip to 'live'."""
    manifest_db.upsert_known(_meeting("m0"), at=datetime(2026, 4, 1, tzinfo=UTC))
    manifest_db.set_source_state("m0", "gone")

    # API now returns m0 again (resurrection)
    repo = ControllableMeetingRepository(meetings=[_meeting("m0")], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    outcome = await svc.run(mode=SyncMode.INCREMENTAL, trigger=SyncTrigger.SCHEDULED)

    assert outcome.meetings_added == 1
    rec = manifest_db.get("m0")
    assert rec.source_state == "live"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py::test_incremental_sync_resurrects_gone_meeting --no-cov -v`
Expected: FAIL — current logic stops at any existing row, including gone ones.

- [ ] **Step 3: Update `_run_incremental`**

In `firefliesclearer/application/sync_service.py`, replace the per-meeting block inside the `while not seen_known` loop:

```python
            for raw in page:
                seen += 1
                existing = self._manifest.get(raw.meeting_id)
                if existing is None:
                    self._manifest.upsert_known(raw, at=started_at)
                    added += 1
                else:
                    seen_known = True
                    break  # stop processing this page
```

With:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py --no-cov -v`
Expected: all PASS, including all earlier sync tests (regression check).

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/application/sync_service.py tests/application/test_sync_service.py
git commit -m "feat(sync): incremental resurrects gone-then-back meetings

Edge case: if a row was reconciled to source_state='gone' but is now
back in the API response, refresh + flip to 'live' and count as added."
```

---

### Task 7: Implement full reconciliation mode

**Files:**
- Modify: `firefliesclearer/application/sync_service.py`
- Test: `tests/application/test_sync_service.py`

Full mode walks every page, builds a seen-id set, then in a reconciliation step marks any cached row not in the set as `source_state='gone'`. Detects updates via `update_cache_fields` (returns True on real change).

- [ ] **Step 1: Write the failing tests**

Add to `tests/application/test_sync_service.py`:

```python
async def test_full_sync_walks_all_pages_and_marks_missing_as_gone(manifest_db):
    # Cache has m0, m1, m2 (all live)
    started = datetime(2026, 4, 1, tzinfo=UTC)
    for mid in ["m0", "m1", "m2"]:
        manifest_db.upsert_known(_meeting(mid), at=started)

    # API only returns m0 and m1 (m2 deleted in Fireflies UI)
    repo = ControllableMeetingRepository(
        meetings=[_meeting("m0"), _meeting("m1")],
        page_size=10,
    )
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    outcome = await svc.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)

    assert outcome.outcome == "success"
    assert outcome.meetings_seen == 2
    assert outcome.meetings_gone == 1
    assert manifest_db.get("m2").source_state == "gone"
    assert manifest_db.get("m0").source_state == "live"
    assert manifest_db.get("m1").source_state == "live"


async def test_full_sync_counts_updates_separately_from_seen(manifest_db):
    started = datetime(2026, 4, 1, tzinfo=UTC)
    manifest_db.upsert_known(_meeting("m0"), at=started)

    # API returns m0 with edited title — counts as updated
    edited = Meeting(
        meeting_id="m0",
        title="Edited Title",
        meeting_date=datetime(2026, 4, 1, tzinfo=UTC),
        duration_minutes=30.0,
        host_email="a@x.com",
        participant_count=3,
        tags=(),
        has_transcript=True,
    )
    repo = ControllableMeetingRepository(meetings=[edited], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    outcome = await svc.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)

    assert outcome.meetings_seen == 1
    assert outcome.meetings_updated == 1
    assert outcome.meetings_added == 0
    assert manifest_db.get("m0").title == "Edited Title"


async def test_full_sync_with_empty_repo_marks_all_cached_as_gone(manifest_db):
    started = datetime(2026, 4, 1, tzinfo=UTC)
    manifest_db.upsert_known(_meeting("m0"), at=started)
    manifest_db.upsert_known(_meeting("m1"), at=started)

    repo = ControllableMeetingRepository(meetings=[], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    outcome = await svc.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)

    assert outcome.meetings_gone == 2
    assert manifest_db.get("m0").source_state == "gone"
    assert manifest_db.get("m1").source_state == "gone"


async def test_full_sync_to_date_pinned_to_run_start(manifest_db):
    """Full sync passes started_at as to_date so pagination is stable."""
    repo = ControllableMeetingRepository(meetings=[_meeting("m0")], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    await svc.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)

    # Every list call should have to_date set (not None)
    assert repo.list_calls
    for skip, limit, to_date in repo.list_calls:
        assert to_date is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py -k full_sync --no-cov -v`
Expected: 4 FAILs — full mode raises `NotImplementedError`.

- [ ] **Step 3: Implement `_run_full`**

In `firefliesclearer/application/sync_service.py`, replace the `run()` method body:

```python
    async def run(
        self,
        *,
        mode: SyncMode,
        trigger: SyncTrigger,
        resume_run_id: int | None = None,
    ) -> SyncOutcome:
        now = self._clock.now()
        run_id = self._manifest.start_sync_run(
            mode=mode.value, trigger=trigger.value, at=now
        )

        if mode == SyncMode.INCREMENTAL:
            return await self._run_incremental(run_id=run_id, started_at=now)
        if mode == SyncMode.FULL:
            return await self._run_full(run_id=run_id, started_at=now)
        raise ValueError(f"Unsupported mode: {mode}")
```

Append the `_run_full` method to the `SyncService` class:

```python
    async def _run_full(self, *, run_id: int, started_at: datetime) -> SyncOutcome:
        # Pin pagination to started_at so meetings created during the run
        # don't shift the window.
        to_date = started_at
        skip = 0
        seen = added = updated = 0
        seen_ids: list[str] = []

        while True:
            page: list[Meeting] = []
            async for m in self._repo.list_meetings_page(
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
                run_id, seen=seen, added=added, updated=updated, gone=0,
                cursor_skip=skip + len(page), seen_ids=seen_ids,
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
            run_id, seen=seen, added=added, updated=updated, gone=gone,
            cursor_skip=skip, seen_ids=seen_ids,
        )
        self._manifest.finalize_sync_run(run_id, outcome="success", at=self._clock.now())
        return SyncOutcome.success(
            run_id=run_id,
            meetings_seen=seen,
            meetings_added=added,
            meetings_updated=updated,
            meetings_gone=gone,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py --no-cov -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/application/sync_service.py tests/application/test_sync_service.py
git commit -m "feat(sync): full reconciliation mode

Walks every page with to_date pinned to run start, builds seen-id set,
then marks cached rows missing from the set as source_state='gone'.
Counts updates separately from added (returns True from
update_cache_fields when a tracked field actually changed)."
```

---

### Task 8: Rate-limit handling + resume support

**Files:**
- Modify: `firefliesclearer/application/sync_service.py`
- Test: `tests/application/test_sync_service.py`

Catch `RateLimitedError` once per run, persist `cursor_skip` + `next_resume_at` on `sync_runs`, return `SyncOutcome.partial`. Support resuming a partial run from its saved cursor.

- [ ] **Step 1: Write the failing tests**

Add to `tests/application/test_sync_service.py`:

```python
from datetime import timedelta


async def test_incremental_sync_returns_partial_when_rate_limited(manifest_db):
    repo = ControllableMeetingRepository(
        meetings=[_meeting(f"m{i}") for i in range(10)],
        page_size=5,
        raise_rate_limit_after_skip=5,
        raise_rate_limit_retry_after_seconds=120.0,
    )
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    outcome = await svc.run(mode=SyncMode.INCREMENTAL, trigger=SyncTrigger.SCHEDULED)

    assert outcome.outcome == "partial"
    assert outcome.meetings_added == 5  # first page made it
    assert outcome.next_resume_at is not None

    rec = manifest_db.get_sync_run(outcome.run_id)
    assert rec.outcome == "partial"
    assert rec.cursor_skip == 5
    assert rec.next_resume_at is not None


async def test_full_sync_returns_partial_when_rate_limited(manifest_db):
    repo = ControllableMeetingRepository(
        meetings=[_meeting(f"m{i}") for i in range(10)],
        page_size=5,
        raise_rate_limit_after_skip=5,
    )
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    outcome = await svc.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)

    assert outcome.outcome == "partial"
    rec = manifest_db.get_sync_run(outcome.run_id)
    assert rec.cursor_skip == 5
    assert rec.seen_ids_json is not None  # so resume can rebuild seen_ids


async def test_full_sync_resume_continues_from_cursor(manifest_db):
    """Calling run() with resume_run_id resumes from the saved cursor."""
    # First run: rate-limited at skip=5
    repo1 = ControllableMeetingRepository(
        meetings=[_meeting(f"m{i}") for i in range(10)],
        page_size=5,
        raise_rate_limit_after_skip=5,
    )
    svc1 = SyncService(repo=repo1, manifest=manifest_db, clock=SystemClock())
    out1 = await svc1.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)
    assert out1.outcome == "partial"

    # Second run: rate limit no longer applies; resume from cursor_skip=5
    repo2 = ControllableMeetingRepository(
        meetings=[_meeting(f"m{i}") for i in range(10)],
        page_size=5,
    )
    svc2 = SyncService(repo=repo2, manifest=manifest_db, clock=SystemClock())
    out2 = await svc2.run(
        mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED, resume_run_id=out1.run_id
    )

    assert out2.outcome == "success"
    # Repo2 must have been called starting at skip=5
    assert any(skip == 5 for skip, _, _ in repo2.list_calls)
    # All 10 meetings now in cache
    assert {m.meeting_id for m in manifest_db.list_known()} == {f"m{i}" for i in range(10)}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py -k "rate_limited or resume" --no-cov -v`
Expected: 3 FAILs — rate-limit handling not implemented.

- [ ] **Step 3: Implement rate-limit handling + resume**

In `firefliesclearer/application/sync_service.py`, add the import at the top:

```python
from firefliesclearer.infra.fireflies_client import RateLimitedError
```

Then replace the entire `_run_incremental` method:

```python
    async def _run_incremental(self, *, run_id: int, started_at: datetime) -> SyncOutcome:
        skip = 0
        seen = added = 0
        seen_known = False

        try:
            while not seen_known:
                page: list[Meeting] = []
                async for m in self._repo.list_meetings_page(
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
                    run_id, seen=seen, added=added, updated=0, gone=0,
                    cursor_skip=skip + len(page),
                )

                if seen_known:
                    break
                skip += len(page)
        except RateLimitedError as e:
            return self._record_rate_limited(
                run_id=run_id, cursor_skip=skip, retry_after=e.retry_after_seconds,
                seen=seen, added=added, error=str(e),
            )

        self._manifest.finalize_sync_run(run_id, outcome="success", at=self._clock.now())
        return SyncOutcome.success(
            run_id=run_id,
            meetings_seen=seen,
            meetings_added=added,
            meetings_updated=0,
            meetings_gone=0,
        )
```

Replace `_run_full` to support resume + rate-limit:

```python
    async def _run_full(
        self, *, run_id: int, started_at: datetime, resume_run_id: int | None = None
    ) -> SyncOutcome:
        # Resume support: if resume_run_id given, recover cursor + seen_ids
        # from the prior partial run.
        if resume_run_id is not None:
            prior = self._manifest.get_sync_run(resume_run_id)
            if prior is None or prior.outcome != "partial":
                raise ValueError(
                    f"resume_run_id={resume_run_id} not found or not partial"
                )
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
                async for m in self._repo.list_meetings_page(
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
                    run_id, seen=seen, added=added, updated=updated, gone=0,
                    cursor_skip=skip + len(page), seen_ids=seen_ids,
                )
                skip += len(page)
        except RateLimitedError as e:
            return self._record_rate_limited(
                run_id=run_id, cursor_skip=skip, retry_after=e.retry_after_seconds,
                seen=seen, added=added, updated=updated,
                seen_ids=seen_ids, error=str(e),
            )

        # Reconciliation
        gone = 0
        seen_set = set(seen_ids)
        for cached in self._manifest.list_known(include_archived=True, include_gone=False):
            if cached.meeting_id not in seen_set:
                self._manifest.set_source_state(cached.meeting_id, "gone")
                gone += 1

        self._manifest.record_sync_progress(
            run_id, seen=seen, added=added, updated=updated, gone=gone,
            cursor_skip=skip, seen_ids=seen_ids,
        )
        self._manifest.finalize_sync_run(run_id, outcome="success", at=self._clock.now())
        return SyncOutcome.success(
            run_id=run_id,
            meetings_seen=seen,
            meetings_added=added,
            meetings_updated=updated,
            meetings_gone=gone,
        )
```

Add the helper method:

```python
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
            run_id, seen=seen, added=added, updated=updated, gone=0,
            cursor_skip=cursor_skip, seen_ids=seen_ids,
        )
        self._manifest.mark_sync_run_partial(
            run_id, at=self._clock.now(), next_resume_at=next_resume_at,
            error_message=error,
        )
        return SyncOutcome.partial(
            run_id=run_id,
            meetings_seen=seen,
            meetings_added=added,
            meetings_updated=updated,
            next_resume_at=next_resume_at,
        )
```

Update the `run` method to forward `resume_run_id` to `_run_full`:

```python
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
            run_id = self._manifest.start_sync_run(
                mode=mode.value, trigger=trigger.value, at=now
            )

        if mode == SyncMode.INCREMENTAL:
            return await self._run_incremental(run_id=run_id, started_at=now)
        if mode == SyncMode.FULL:
            return await self._run_full(
                run_id=run_id, started_at=now, resume_run_id=resume_run_id
            )
        raise ValueError(f"Unsupported mode: {mode}")
```

Add the imports needed at the top of the file (these are likely already present after the prior tasks; add what's missing):

```python
import json
from datetime import datetime, timedelta
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py --no-cov -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/application/sync_service.py tests/application/test_sync_service.py
git commit -m "feat(sync): rate-limit handling + resume from cursor

Catches RateLimitedError once per run, persists cursor_skip and
next_resume_at on the sync_runs row, returns SyncOutcome.partial.
A subsequent run() call with resume_run_id continues from the
saved cursor with the same to_date pin and seen_ids set."
```

---

### Task 9: Implement `sync_scheduler` decision logic

**Files:**
- Create: `firefliesclearer/infra/sync_scheduler.py`
- Test: `tests/infra/test_sync_scheduler.py`

The scheduler computes `next_tick` and `decide_mode(now)` from config + last run state. Pure functions; the asyncio loop wraps them in the next task.

- [ ] **Step 1: Write the failing tests**

Create `tests/infra/test_sync_scheduler.py`:

```python
"""Tests for sync_scheduler decision logic (compute_next, decide_mode)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from firefliesclearer.core.manifest import SyncRunRecord
from firefliesclearer.infra.config import SyncConfig
from firefliesclearer.infra.sync_scheduler import compute_next, decide_mode


def _run(
    *,
    mode: str = "incremental",
    outcome: str = "success",
    finished_at: datetime | None = None,
    next_resume_at: datetime | None = None,
) -> SyncRunRecord:
    return SyncRunRecord(
        id=1, mode=mode, trigger_source="scheduled",
        started_at=finished_at or datetime(2026, 5, 2, 0, 0, tzinfo=UTC),
        finished_at=finished_at, outcome=outcome,
        meetings_seen=0, meetings_added=0, meetings_updated=0, meetings_gone=0,
        cursor_skip=None, seen_ids_json=None,
        next_resume_at=next_resume_at, error_message=None,
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
    cfg = SyncConfig(enabled=True, incremental_interval_hours=6, full_interval_days=7,
                     full_run_hour_local=3)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/infra/test_sync_scheduler.py --no-cov -v`
Expected: ImportError (module doesn't exist).

- [ ] **Step 3: Implement scheduler decision logic**

Create `firefliesclearer/infra/sync_scheduler.py`:

```python
"""Sync scheduler — decides when and what mode to run, then drives SyncService.

The decision logic (compute_next, decide_mode) is pure and unit-tested.
The asyncio loop (run_scheduler) wraps it with sleep + lock + actual
SyncService.run invocation.
"""

from __future__ import annotations

import asyncio
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
      - If no runs yet → fire now.
      - If last_run is partial → fire at last_run.next_resume_at.
      - Else: min(next_incremental, next_full).
        next_incremental = last_run.finished_at + incremental_interval_hours.
        next_full = first 03:00-local-time occurrence ≥ last_full.finished_at +
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

    full_threshold = (last_full.finished_at or now) + timedelta(
        days=config.full_interval_days
    )
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/infra/test_sync_scheduler.py --no-cov -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/infra/sync_scheduler.py tests/infra/test_sync_scheduler.py
git commit -m "feat(scheduler): pure decision logic (compute_next + decide_mode)

Picks the earlier of next_incremental and next_full ticks, with
partial-run windows blocking until next_resume_at. Hour alignment
for full reconciliations is interpreted in the threshold's tz."
```

---

### Task 10: Implement scheduler asyncio loop + serve_cmd wiring

**Files:**
- Modify: `firefliesclearer/infra/sync_scheduler.py` (add `run_scheduler` coroutine)
- Modify: `firefliesclearer/cli/serve_cmd.py` (start scheduler when enabled)
- Test: `tests/infra/test_sync_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/infra/test_sync_scheduler.py`:

```python
import asyncio
import pytest

from firefliesclearer.application.sync_service import (
    SyncMode,
    SyncOutcome,
    SyncService,
    SyncTrigger,
)
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.infra.system_clock import SystemClock
from tests.fakes.controllable_repository import ControllableMeetingRepository
from firefliesclearer.core.models import Meeting


def _meeting(mid: str) -> Meeting:
    return Meeting(
        meeting_id=mid, title=mid, meeting_date=datetime(2026, 4, 1, tzinfo=UTC),
        duration_minutes=30.0, host_email="a@x", participant_count=2, tags=(), has_transcript=True,
    )


async def test_run_scheduler_invokes_sync_service_once_then_stops(tmp_path):
    """The scheduler runs a single sync when shutdown_event is set early."""
    from firefliesclearer.infra.sync_scheduler import run_scheduler

    manifest = Manifest.open(tmp_path / "manifest.db")
    repo = ControllableMeetingRepository(meetings=[_meeting("a")], page_size=10)
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
            sync_service=svc, manifest=manifest, config=cfg,
            clock=SystemClock(), shutdown_event=shutdown,
        ),
        stop_after_one_run(),
    )

    last = manifest.get_last_sync_run()
    assert last is not None
    assert last.outcome == "success"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/infra/test_sync_scheduler.py::test_run_scheduler_invokes_sync_service_once_then_stops --no-cov -v`
Expected: ImportError on `run_scheduler`.

- [ ] **Step 3: Implement `run_scheduler`**

Append to `firefliesclearer/infra/sync_scheduler.py`:

```python
async def run_scheduler(
    *,
    sync_service: object,  # SyncService
    manifest: object,       # Manifest
    config: SyncConfig,
    clock: object,          # Clock
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
            next_tick = compute_next(
                last_run=last_run, last_full=last_full, config=config, now=now
            )
            if next_tick > now:
                wait_seconds = (next_tick - now).total_seconds()
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=wait_seconds)
                    return  # shutdown signaled during sleep
                except asyncio.TimeoutError:
                    pass

            # Bootstrap: no runs ever and cache empty → force full sync
            is_bootstrap = last_run is None and _cache_is_empty(manifest)
            if is_bootstrap:
                mode = SyncMode.FULL
                trigger = SyncTrigger.BOOTSTRAP
            else:
                mode_str = decide_mode(last_full=last_full, config=config, now=clock.now())
                mode = SyncMode.FULL if mode_str == "full" else SyncMode.INCREMENTAL
                trigger = SyncTrigger.SCHEDULED

            resume_id = (
                last_run.id
                if last_run is not None and last_run.outcome == "partial"
                else None
            )
            try:
                await sync_service.run(mode=mode, trigger=trigger, resume_run_id=resume_id)
            except Exception as exc:  # noqa: BLE001 — top-level loop guard
                logger.exception("Scheduler tick failed: %s", exc)
        except Exception as exc:  # noqa: BLE001 — never let the loop die
            logger.exception("Scheduler loop error: %s", exc)
            await asyncio.sleep(60)


def _find_last_completed_full(manifest) -> SyncRunRecord | None:  # type: ignore[no-untyped-def]
    """Walk recent sync_runs for the most recent full+success record."""
    last = manifest.get_last_sync_run()
    if last is None:
        return None
    if last.mode == "full" and last.outcome == "success":
        return last
    # In Phase 2 we only have get_last_sync_run; querying by mode is a
    # Phase 4 concern when the UI wants it. For now, accept that
    # "no recent full" means "treat as never ran a full".
    return None


def _cache_is_empty(manifest) -> bool:  # type: ignore[no-untyped-def]
    """Return True if there are no meetings cached at all."""
    for _ in manifest.list_known(include_archived=True, include_gone=True):
        return False
    return True
```

In `firefliesclearer/cli/serve_cmd.py`, find the eager-deps build branch (the `if config_path.exists():` block). After `fastapi_app.state.deps = deps` add scheduler startup. The full block becomes:

```python
    if config_path.exists():
        migrate_v1_rules_auto(config_path)
        from firefliesclearer.infra.config import load_config

        _cfg = load_config(user_config=config_path)
        sweep_old_logs(_cfg.archive.root_dir, _cfg.run.log_retention_days)
        deps = _common.build_deps(config_override=config_path)
        fastapi_app.state.deps = deps

        # Phase 2: start sync scheduler when enabled (default false)
        if _cfg.sync.enabled:
            import asyncio
            from firefliesclearer.application.sync_service import SyncService
            from firefliesclearer.infra.sync_scheduler import run_scheduler

            sync_service = SyncService(
                repo=deps.client, manifest=deps.manifest, clock=deps.clock,
            )
            shutdown_event = asyncio.Event()
            fastapi_app.state.sync_shutdown_event = shutdown_event
            fastapi_app.state.sync_service = sync_service

            @fastapi_app.on_event("startup")
            async def _start_sync_scheduler() -> None:
                asyncio.create_task(
                    run_scheduler(
                        sync_service=sync_service, manifest=deps.manifest,
                        config=_cfg.sync, clock=deps.clock,
                        shutdown_event=shutdown_event,
                    )
                )

            @fastapi_app.on_event("shutdown")
            async def _stop_sync_scheduler() -> None:
                shutdown_event.set()
    else:
        fastapi_app.state.deps = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/infra/test_sync_scheduler.py --no-cov -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/infra/sync_scheduler.py firefliesclearer/cli/serve_cmd.py tests/infra/test_sync_scheduler.py
git commit -m "feat(scheduler): asyncio loop + serve_cmd wiring

Background task driven by shutdown_event. Detects bootstrap (no
runs + empty cache) and forces a full sync with trigger=bootstrap.
Resume support for partial runs. Wired into serve_cmd lifecycle
when [sync] enabled = true; default-off remains untouched."
```

---

### Task 11: Final verification

**Files:** none (read-only commands)

- [ ] **Step 1: Full pytest suite (regression check)**

Run: `.venv/Scripts/pytest.exe --no-cov -q`
Expected: all tests pass. Phase 2 adds ~50-60 new tests; coverage of `application/sync_service.py` and `infra/sync_scheduler.py` should be 95%+.

- [ ] **Step 2: mypy strict**

Run: `.venv/Scripts/mypy.exe firefliesclearer`
Expected: clean.

- [ ] **Step 3: ruff lint + format**

Run: `.venv/Scripts/ruff.exe check firefliesclearer tests && .venv/Scripts/ruff.exe format --check firefliesclearer tests`
Expected: both clean.

- [ ] **Step 4: Coverage check on new modules**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py tests/infra/test_sync_scheduler.py tests/core/test_manifest.py --cov=firefliesclearer.application.sync_service --cov=firefliesclearer.infra.sync_scheduler --cov=firefliesclearer.core.manifest --cov-report=term-missing -q`
Expected: 95%+ on `sync_service.py` and `sync_scheduler.py`; 100% on `manifest.py` (existing hard target preserved).

- [ ] **Step 5: Sign off**

After all checks green, Phase 2 is complete. Default-off — no users see any change. Phase 3 plan: read-path flip (ScanService reads from Manifest when flag is on).
