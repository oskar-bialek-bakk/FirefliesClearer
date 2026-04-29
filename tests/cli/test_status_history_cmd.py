"""Tests for `status` and `history`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from firefliesclearer.cli.app import app
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.infra.fireflies_client import FirefliesClient
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

runner = CliRunner()
NOW = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)


def _meeting(mid: str) -> Meeting:
    return Meeting(
        meeting_id=mid,
        title=f"M-{mid}",
        meeting_date=NOW,
        duration_minutes=1.0,
        host_email="u@x.com",
        participant_count=1,
        tags=(),
        has_transcript=True,
    )


@pytest.fixture
def patched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from firefliesclearer.cli import _common
    from firefliesclearer.core.archiver import Archiver
    from firefliesclearer.core.manifest import Manifest
    from firefliesclearer.core.pipeline import Pipeline
    from firefliesclearer.infra.config import AppConfig
    from tests.fakes.fake_renderer import FakeSummaryRenderer
    from tests.fakes.frozen_clock import FrozenClock

    archive_root = tmp_path / "arch"
    archive_root.mkdir()
    manifest = Manifest.open(archive_root / "manifest.db")
    manifest.register(_meeting("a"), at=NOW)
    manifest.transition("a", to=MeetingState.ARCHIVED, at=NOW)
    manifest.transition("a", to=MeetingState.DELETED, at=NOW)
    manifest.register(_meeting("b"), at=NOW)

    repo = InMemoryMeetingRepository(meetings=[])
    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": "x"},
            "archive": {
                "root_dir": str(archive_root),
                "summary_format": "pdf",
            },
        }
    )
    pipeline = Pipeline(
        repository=repo,
        manifest=manifest,
        archiver=Archiver(archive_root=archive_root),
        renderer=FakeSummaryRenderer(),
        clock=FrozenClock(NOW),
    )
    deps = _common.Deps(
        config=cfg,
        pipeline=pipeline,
        manifest=manifest,
        client=cast(FirefliesClient, repo),
        clock=FrozenClock(NOW),
    )
    monkeypatch.setattr(_common, "build_deps", lambda **kw: deps)


def test_status_shows_counts(patched) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stdout
    assert "deleted" in result.stdout.lower()
    assert "pending" in result.stdout.lower()


def test_history_for_known_month_lists_deleted(patched) -> None:
    result = runner.invoke(app, ["history", "--month", "2026-04"])
    assert result.exit_code == 0
    assert "M-a" in result.stdout or " a " in result.stdout
    # Pending meeting "b" should not appear
    assert "M-b" not in result.stdout
