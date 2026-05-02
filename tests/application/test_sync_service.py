"""Tests for SyncService — incremental + full reconciliation algorithms."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from firefliesclearer.application.sync_service import (
    SyncMode,
    SyncOutcome,
    SyncService,
    SyncTrigger,
)
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting
from firefliesclearer.infra.system_clock import SystemClock
from tests.fakes.controllable_repository import ControllableMeetingRepository


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
    out = SyncOutcome.partial(run_id=2, meetings_seen=20, meetings_added=10, next_resume_at=resume)
    assert out.outcome == "partial"
    assert out.next_resume_at == resume


def test_sync_outcome_failed_factory():
    out = SyncOutcome.failed(run_id=3, error_message="API key invalid")
    assert out.outcome == "failed"
    assert out.error_message == "API key invalid"


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
    with pytest.raises(RateLimitedError):
        async for _ in repo.list_meetings_page(skip=5, limit=5):
            pass


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
