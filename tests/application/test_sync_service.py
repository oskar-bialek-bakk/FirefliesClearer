"""Tests for SyncService — incremental + full reconciliation algorithms."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from firefliesclearer.application.sync_service import (
    SyncMode,
    SyncOutcome,
    SyncTrigger,
)
from firefliesclearer.core.models import Meeting
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
