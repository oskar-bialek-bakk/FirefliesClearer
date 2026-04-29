"""Tests for `firefliesclearer run` — auto path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from firefliesclearer.cli.app import app
from firefliesclearer.core.models import ArtifactBundle, Meeting, MeetingState
from firefliesclearer.infra.fireflies_client import FirefliesClient
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

runner = CliRunner()
NOW = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


def _meeting(mid: str, days_old: int, *, has_transcript: bool = True) -> Meeting:
    return Meeting(
        meeting_id=mid,
        title=f"M-{mid}",
        meeting_date=NOW - timedelta(days=days_old),
        duration_minutes=10.0,
        host_email="u@x.com",
        participant_count=2,
        tags=(),
        has_transcript=has_transcript,
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

    old = _meeting("old", days_old=200)
    no_t = _meeting("nt", days_old=10, has_transcript=False)
    fresh = _meeting("fresh", days_old=10)
    repo = InMemoryMeetingRepository(
        meetings=[old, no_t, fresh],
        artifacts={
            "old": ArtifactBundle(audio_bytes=b"A", transcript_markdown="# T", summary_payload={}),
            "nt": ArtifactBundle(audio_bytes=b"A", transcript_markdown="# T", summary_payload={}),
            "fresh": ArtifactBundle(),
        },
    )
    archive_root = tmp_path / "arch"
    archive_root.mkdir()
    manifest = Manifest.open(archive_root / "manifest.db")
    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": "x"},
            "archive": {
                "root_dir": str(archive_root),
                "summary_format": "pdf",
            },
            "rules": {
                "auto": {
                    "older_than_days": 180,
                    "delete_failed_transcripts": True,
                }
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
    return repo, manifest


def test_run_dry_run_makes_no_mutations(patched) -> None:
    repo, manifest = patched
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 0, result.stdout
    assert manifest.get("old") is None
    assert repo.deleted == []


def test_run_apply_deletes_matching(patched) -> None:
    repo, manifest = patched
    result = runner.invoke(app, ["run", "--apply", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert manifest.get("old").state is MeetingState.DELETED
    assert manifest.get("nt").state is MeetingState.DELETED
    assert manifest.get("fresh") is None
    assert set(repo.deleted) == {"old", "nt"}
