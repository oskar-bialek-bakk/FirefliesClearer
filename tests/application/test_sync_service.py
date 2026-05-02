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
    out = SyncOutcome.partial(run_id=2, meetings_seen=20, meetings_added=10, next_resume_at=resume)
    assert out.outcome == "partial"
    assert out.next_resume_at == resume


def test_sync_outcome_failed_factory():
    out = SyncOutcome.failed(run_id=3, error_message="API key invalid")
    assert out.outcome == "failed"
    assert out.error_message == "API key invalid"
