"""Tests for SyncService — incremental + full reconciliation algorithms."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from firefliesclearer.application.sync_service import (
    SyncMode,
    SyncOutcome,
    SyncService,
    SyncTrigger,
    estimate_total,
)
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting, MeetingState
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


def test_estimate_total_with_full_pages_only():
    """While every page is full, return seen + 50 (one more page assumed)."""
    assert estimate_total(seen=50, last_page_size=50) == 100
    assert estimate_total(seen=200, last_page_size=50) == 250


def test_estimate_total_with_short_last_page():
    """A partial last page is the end. Return exactly seen."""
    assert estimate_total(seen=237, last_page_size=37) == 237


def test_estimate_total_with_zero_seen():
    """Before any page lands, fall back to a default."""
    assert estimate_total(seen=0, last_page_size=0) == 50


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


def _walk_to_archived(manifest, meeting_id: str) -> None:
    started = datetime(2026, 4, 1, tzinfo=UTC)
    manifest.transition(meeting_id, to=MeetingState.PENDING, at=started)
    manifest.transition(meeting_id, to=MeetingState.ARCHIVED, at=started)


async def test_full_sync_auto_reconciles_archived_to_deleted_when_missing_upstream(
    manifest_db,
):
    """Archived rows missing from upstream auto-promote to DELETED.

    Why this exists: Fireflies' Pro plan caps total daily GraphQL ops at 50,
    so the sustainable cleanup workflow is "archive locally, bulk-delete in
    FF web UI, let sync reconcile". Without this auto-promote, a user who
    bulk-deletes 100 meetings in FF web sees them all stuck in the
    'archived' state forever — sync only soft-marks source_state='gone'.
    """
    started = datetime(2026, 4, 1, tzinfo=UTC)
    # m0: archived locally then bulk-deleted upstream by the user.
    manifest_db.upsert_known(_meeting("m0"), at=started)
    _walk_to_archived(manifest_db, "m0")
    # m1: still live, both locally and upstream — should not be touched.
    manifest_db.upsert_known(_meeting("m1"), at=started)

    # Upstream returns only m1.
    repo = ControllableMeetingRepository(meetings=[_meeting("m1")], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    outcome = await svc.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)

    assert outcome.outcome == "success"
    rec0 = manifest_db.get("m0")
    assert rec0 is not None
    assert rec0.state is MeetingState.DELETED
    assert rec0.source_state == "gone"
    rec1 = manifest_db.get("m1")
    assert rec1 is not None
    assert rec1.state is MeetingState.KNOWN
    assert rec1.source_state == "live"


async def test_full_sync_reconciles_deleted_failed_to_deleted(manifest_db):
    """A row stuck in DELETED_FAILED (API delete tripped on rate limit) that
    the user later bulk-deleted in FF web should also auto-reconcile."""
    started = datetime(2026, 4, 1, tzinfo=UTC)
    manifest_db.upsert_known(_meeting("m0"), at=started)
    _walk_to_archived(manifest_db, "m0")
    manifest_db.transition(
        "m0",
        to=MeetingState.DELETED_FAILED,
        at=started,
        last_error="Too many requests",
    )

    repo = ControllableMeetingRepository(meetings=[], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    await svc.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)

    rec = manifest_db.get("m0")
    assert rec is not None
    assert rec.state is MeetingState.DELETED


async def test_full_sync_does_not_promote_known_to_deleted_when_missing(manifest_db):
    """Safety invariant #1: never mark a meeting as DELETED unless its
    archive is verified on disk. KNOWN-but-missing-from-upstream stays
    in KNOWN with source_state='gone' — the user might want to know
    they had it before they have a chance to archive it."""
    started = datetime(2026, 4, 1, tzinfo=UTC)
    manifest_db.upsert_known(_meeting("m0"), at=started)  # stays KNOWN

    repo = ControllableMeetingRepository(meetings=[], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    await svc.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)

    rec = manifest_db.get("m0")
    assert rec is not None
    assert rec.state is MeetingState.KNOWN
    assert rec.source_state == "gone"


async def test_full_sync_does_not_touch_archived_rows_still_present_upstream(manifest_db):
    """Archive that's still in upstream (user hasn't deleted it yet) stays
    ARCHIVED — only the *missing* archived rows promote to DELETED."""
    started = datetime(2026, 4, 1, tzinfo=UTC)
    manifest_db.upsert_known(_meeting("m0"), at=started)
    _walk_to_archived(manifest_db, "m0")

    repo = ControllableMeetingRepository(meetings=[_meeting("m0")], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    await svc.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)

    rec = manifest_db.get("m0")
    assert rec is not None
    assert rec.state is MeetingState.ARCHIVED
    assert rec.source_state == "live"


async def test_full_sync_records_reconcile_reason_in_state_log(manifest_db):
    started = datetime(2026, 4, 1, tzinfo=UTC)
    manifest_db.upsert_known(_meeting("m0"), at=started)
    _walk_to_archived(manifest_db, "m0")

    repo = ControllableMeetingRepository(meetings=[], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())
    await svc.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)

    last = manifest_db.state_log("m0")[-1]
    assert last.from_state is MeetingState.ARCHIVED
    assert last.to_state is MeetingState.DELETED
    assert last.details == {"reason": "reconciled_external_delete"}


async def test_incremental_sync_does_not_reconcile_missing_meetings(manifest_db):
    """Incremental sync stops at the first known meeting and is *not* a full
    walk of upstream — so it can't tell whether m1 is missing or just on a
    later page. The auto-reconcile only fires from full sync."""
    started = datetime(2026, 4, 1, tzinfo=UTC)
    manifest_db.upsert_known(_meeting("m0"), at=started)  # cached as KNOWN
    manifest_db.upsert_known(_meeting("m1"), at=started)
    _walk_to_archived(manifest_db, "m1")

    # Upstream still returns m0 (so incremental halts at it). m1 isn't in
    # the response, but incremental wouldn't have walked far enough to know.
    repo = ControllableMeetingRepository(meetings=[_meeting("m0")], page_size=10)
    svc = SyncService(repo=repo, manifest=manifest_db, clock=SystemClock())

    await svc.run(mode=SyncMode.INCREMENTAL, trigger=SyncTrigger.SCHEDULED)

    # m1 must still be ARCHIVED — incremental sync isn't a reconciliation.
    rec = manifest_db.get("m1")
    assert rec is not None
    assert rec.state is MeetingState.ARCHIVED


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
    for _skip, _limit, to_date in repo.list_calls:
        assert to_date is not None


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


async def test_incremental_sync_returns_partial_on_transient_5xx(manifest_db):
    """Regression: a 504 from Fireflies (after retries are exhausted in the
    HTTP client) must finalize the run as ``partial`` with ``next_resume_at``,
    not ``failed``. Otherwise a flaky upstream minute leaves the user
    looking at a sticky 'Last sync failed: server 504' banner until they
    click Retry — when the scheduler should pick it up automatically.
    """
    from collections.abc import AsyncIterator

    from firefliesclearer.infra.fireflies_client import TransientServerError

    class _FlakyRepo:
        async def list_meetings_page(
            self, *, skip: int, limit: int, to_date: object = None
        ) -> AsyncIterator[Meeting]:
            raise TransientServerError("server 504", retry_after_seconds=120.0)
            yield  # pragma: no cover — make this a real async generator

    svc = SyncService(repo=_FlakyRepo(), manifest=manifest_db, clock=SystemClock())
    outcome = await svc.run(mode=SyncMode.INCREMENTAL, trigger=SyncTrigger.SCHEDULED)

    assert outcome.outcome == "partial"
    assert outcome.next_resume_at is not None
    rec = manifest_db.get_sync_run(outcome.run_id)
    assert rec.outcome == "partial"
    assert rec.error_message is not None
    assert "504" in rec.error_message


async def test_full_sync_returns_partial_on_transient_5xx(manifest_db):
    """Same property as the incremental case, but for the full reconciliation
    path — guards against the user-reported scheduler crash."""
    from collections.abc import AsyncIterator

    from firefliesclearer.infra.fireflies_client import TransientServerError

    class _FlakyRepo:
        async def list_meetings_page(
            self, *, skip: int, limit: int, to_date: object = None
        ) -> AsyncIterator[Meeting]:
            raise TransientServerError("server 504")
            yield  # pragma: no cover

    svc = SyncService(repo=_FlakyRepo(), manifest=manifest_db, clock=SystemClock())
    outcome = await svc.run(mode=SyncMode.FULL, trigger=SyncTrigger.SCHEDULED)

    assert outcome.outcome == "partial"
    rec = manifest_db.get_sync_run(outcome.run_id)
    assert rec.outcome == "partial"


async def test_partial_run_persists_no_next_resume_at_when_no_upstream_estimate(
    manifest_db,
):
    """When the transient error carries no upstream-given retry estimate
    (the default for 5xx and transport timeouts — Fireflies never tells us
    when the issue will clear), the persisted ``next_resume_at`` must be
    ``None``. Synthesising a placeholder timestamp surfaces a fabricated
    'Next retry around HH:MM' line in the UI, which the user explicitly
    rejected: a guessed number is worse than no number. The scheduler's
    regular cadence (incremental_interval_hours) is the fallback for
    'we don't know when'."""
    from collections.abc import AsyncIterator

    from firefliesclearer.infra.fireflies_client import TransientServerError

    class _FlakyRepo:
        async def list_meetings_page(
            self, *, skip: int, limit: int, to_date: object = None
        ) -> AsyncIterator[Meeting]:
            # No retry_after_seconds — mirrors the production default.
            raise TransientServerError("Fireflies timed out...")
            yield  # pragma: no cover

    svc = SyncService(repo=_FlakyRepo(), manifest=manifest_db, clock=SystemClock())
    outcome = await svc.run(mode=SyncMode.INCREMENTAL, trigger=SyncTrigger.SCHEDULED)

    assert outcome.outcome == "partial"
    assert outcome.next_resume_at is None
    rec = manifest_db.get_sync_run(outcome.run_id)
    assert rec.next_resume_at is None
    # Error message survives intact so the banner can render it.
    assert rec.error_message is not None
    assert "Fireflies" in rec.error_message


async def test_run_finalizes_sync_run_as_failed_on_unexpected_exception(manifest_db):
    """Regression: an unexpected exception inside run() must mark the
    sync_runs row as outcome='failed' before re-raising. Otherwise the row
    stays at 'running' forever and /sync/status cannot distinguish a crashed
    sync from an active one.
    """
    from collections.abc import AsyncIterator

    class _BoomRepo:
        async def list_meetings_page(
            self, *, skip: int, limit: int, to_date: object = None
        ) -> AsyncIterator[Meeting]:
            raise RuntimeError("boom")
            yield  # pragma: no cover — make it a real async generator

    svc = SyncService(repo=_BoomRepo(), manifest=manifest_db, clock=SystemClock())

    with pytest.raises(RuntimeError, match="boom"):
        await svc.run(mode=SyncMode.INCREMENTAL, trigger=SyncTrigger.SCHEDULED)

    last = manifest_db.get_last_sync_run()
    assert last is not None
    assert last.outcome == "failed"
    assert last.finished_at is not None
    assert last.error_message is not None
    assert "boom" in last.error_message
