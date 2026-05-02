"""Tests for ManifestBackedRepository — read-only Manifest -> MeetingRepository adapter."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository
from firefliesclearer.ports.meeting_repository import MeetingFilter

NOW = datetime(2026, 5, 2, tzinfo=UTC)


def _meeting(mid: str, *, dt: datetime | None = None) -> Meeting:
    return Meeting(
        meeting_id=mid,
        title=mid,
        meeting_date=dt or NOW,
        duration_minutes=30.0,
        host_email="a@x.com",
        participant_count=2,
        tags=(),
        has_transcript=True,
    )


@pytest.fixture
def manifest(tmp_path):
    return Manifest.open(tmp_path / "manifest.db")


async def test_list_meetings_yields_cached_rows(manifest):
    manifest.upsert_known(_meeting("a"), at=NOW)
    manifest.upsert_known(_meeting("b"), at=NOW)
    repo = ManifestBackedRepository(manifest)
    ids = sorted([m.meeting_id async for m in repo.list_meetings(MeetingFilter())])
    assert ids == ["a", "b"]


async def test_list_meetings_respects_older_than_filter(manifest):
    older = datetime(2025, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 6, 1, tzinfo=UTC)
    manifest.upsert_known(_meeting("old", dt=older), at=NOW)
    manifest.upsert_known(_meeting("new", dt=newer), at=NOW)

    repo = ManifestBackedRepository(manifest)
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    ids = [m.meeting_id async for m in repo.list_meetings(MeetingFilter(older_than=cutoff))]
    assert ids == ["old"]


async def test_list_meetings_excludes_archived_and_gone_by_default(manifest):
    manifest.upsert_known(_meeting("a"), at=NOW)
    manifest.upsert_known(_meeting("b"), at=NOW)
    manifest.upsert_known(_meeting("c"), at=NOW)
    manifest.transition("b", to=MeetingState.PENDING, at=NOW)
    manifest.transition("b", to=MeetingState.ARCHIVED, at=NOW)
    manifest.set_source_state("c", "gone")

    repo = ManifestBackedRepository(manifest)
    ids = [m.meeting_id async for m in repo.list_meetings(MeetingFilter())]
    assert ids == ["a"]


async def test_fetch_artifacts_raises_not_implemented(manifest):
    repo = ManifestBackedRepository(manifest)
    with pytest.raises(NotImplementedError, match="cache adapter"):
        await repo.fetch_artifacts("a")


async def test_delete_meeting_raises_not_implemented(manifest):
    repo = ManifestBackedRepository(manifest)
    with pytest.raises(NotImplementedError, match="cache adapter"):
        await repo.delete_meeting("a")


async def test_ping_user_raises_not_implemented(manifest):
    repo = ManifestBackedRepository(manifest)
    with pytest.raises(NotImplementedError, match="cache adapter"):
        await repo.ping_user()
