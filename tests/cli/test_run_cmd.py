"""Tests for `firefliesclearer run` — preset-driven auto path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from firefliesclearer.cli.app import app
from firefliesclearer.core.models import ArtifactBundle, Meeting, MeetingState
from firefliesclearer.infra.config import AppConfig, Preset, ScanFiltersModel, write_config
from firefliesclearer.infra.fireflies_client import FirefliesClient
from tests.fakes.frozen_clock import FrozenClock
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


def _write_config_with_presets(
    tmp_path: Path,
    archive_root: Path,
    presets: list[Preset],
) -> Path:
    """Write a config TOML with the given presets and return the path."""
    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": "x"},
            "archive": {
                "root_dir": str(archive_root),
                "summary_format": "pdf",
            },
        }
    )
    # Build a new config with presets (immutable pattern).
    cfg_with_presets = cfg.model_copy(update={"presets": presets})
    config_path = tmp_path / "config.toml"
    write_config(cfg_with_presets, config_path)
    return config_path


@pytest.fixture
def patched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fixture: write real config TOML, monkeypatch build_deps, return (repo, manifest, config_path)."""
    from firefliesclearer.cli import _common
    from firefliesclearer.core.archiver import Archiver
    from firefliesclearer.core.manifest import Manifest
    from firefliesclearer.core.pipeline import Pipeline
    from tests.fakes.fake_renderer import FakeSummaryRenderer

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

    # Default preset: older_than_days=180
    default_preset = Preset(
        name="Default",
        description="",
        default=True,
        created_at=NOW,
        filters=ScanFiltersModel(older_than_days=180),
    )
    config_path = _write_config_with_presets(tmp_path, archive_root, [default_preset])

    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": "x"},
            "archive": {
                "root_dir": str(archive_root),
                "summary_format": "pdf",
            },
        }
    )
    clock = FrozenClock(NOW)
    pipeline = Pipeline(
        repository=repo,
        manifest=manifest,
        archiver=Archiver(archive_root=archive_root),
        renderer=FakeSummaryRenderer(),
        clock=clock,
    )
    deps = _common.Deps(
        config=cfg,
        pipeline=pipeline,
        manifest=manifest,
        client=cast(FirefliesClient, repo),
        clock=clock,
    )
    monkeypatch.setattr(_common, "build_deps", lambda **kw: deps)
    return repo, manifest, config_path


def test_run_dry_run_with_default_preset(patched) -> None:
    """Default preset (older_than_days=180): dry-run reports matches, no mutations."""
    repo, manifest, config_path = patched
    result = runner.invoke(app, ["run", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "Default" in result.output
    assert manifest.get("old") is None
    assert repo.deleted == []


def test_run_apply_with_default_preset_deletes_matching(patched) -> None:
    """`--apply --yes` with default preset archives+deletes the old meeting."""
    repo, manifest, config_path = patched
    result = runner.invoke(app, ["run", "--apply", "--yes", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert manifest.get("old").state is MeetingState.DELETED
    assert manifest.get("fresh") is None
    assert "old" in repo.deleted


def test_run_with_named_preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--preset Foo` with older_than_days=300 only matches the 400-day-old meeting."""
    from firefliesclearer.cli import _common
    from firefliesclearer.core.archiver import Archiver
    from firefliesclearer.core.manifest import Manifest
    from firefliesclearer.core.pipeline import Pipeline
    from tests.fakes.fake_renderer import FakeSummaryRenderer

    very_old = _meeting("very_old", days_old=400)
    old = _meeting("old", days_old=200)
    fresh = _meeting("fresh", days_old=10)
    repo = InMemoryMeetingRepository(
        meetings=[very_old, old, fresh],
        artifacts={
            "very_old": ArtifactBundle(
                audio_bytes=b"A", transcript_markdown="# T", summary_payload={}
            ),
            "old": ArtifactBundle(audio_bytes=b"A", transcript_markdown="# T", summary_payload={}),
            "fresh": ArtifactBundle(),
        },
    )
    archive_root = tmp_path / "arch"
    archive_root.mkdir()
    manifest = Manifest.open(archive_root / "manifest.db")

    foo_preset = Preset(
        name="Foo",
        description="",
        default=False,
        created_at=NOW,
        filters=ScanFiltersModel(older_than_days=300),
    )
    config_path = _write_config_with_presets(tmp_path, archive_root, [foo_preset])

    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": "x"},
            "archive": {
                "root_dir": str(archive_root),
                "summary_format": "pdf",
            },
        }
    )
    clock = FrozenClock(NOW)
    pipeline = Pipeline(
        repository=repo,
        manifest=manifest,
        archiver=Archiver(archive_root=archive_root),
        renderer=FakeSummaryRenderer(),
        clock=clock,
    )
    deps = _common.Deps(
        config=cfg,
        pipeline=pipeline,
        manifest=manifest,
        client=cast(FirefliesClient, repo),
        clock=clock,
    )
    monkeypatch.setattr(_common, "build_deps", lambda **kw: deps)

    result = runner.invoke(app, ["run", "--preset", "Foo", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "Foo" in result.output
    assert "1" in result.output  # only very_old (400d) matches, not old (200d)
    # dry-run: no mutations
    assert manifest.get("very_old") is None
    assert repo.deleted == []


def test_run_with_unknown_preset_errors(patched) -> None:
    """`--preset Nope` exits 1 with a 'not found' message."""
    _repo, _manifest, config_path = patched
    result = runner.invoke(app, ["run", "--preset", "Nope", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "Nope" in result.output


def test_run_no_default_preset_and_no_flag_errors(tmp_path: Path) -> None:
    """No presets configured, no --preset flag → exit 1 with clear message."""
    archive_root = tmp_path / "arch"
    archive_root.mkdir()
    # Config with no presets at all.
    config_path = _write_config_with_presets(tmp_path, archive_root, [])

    result = runner.invoke(app, ["run", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "No default preset" in result.output


def test_run_preset_with_no_transcript_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preset with no_transcript=True matches the nt meeting."""
    from firefliesclearer.cli import _common
    from firefliesclearer.core.archiver import Archiver
    from firefliesclearer.core.manifest import Manifest
    from firefliesclearer.core.pipeline import Pipeline
    from tests.fakes.fake_renderer import FakeSummaryRenderer

    no_t = _meeting("nt", days_old=10, has_transcript=False)
    fresh = _meeting("fresh", days_old=10)
    repo = InMemoryMeetingRepository(
        meetings=[no_t, fresh],
        artifacts={
            "nt": ArtifactBundle(audio_bytes=b"A", transcript_markdown="# T", summary_payload={}),
            "fresh": ArtifactBundle(),
        },
    )
    archive_root = tmp_path / "arch"
    archive_root.mkdir()
    manifest = Manifest.open(archive_root / "manifest.db")

    nt_preset = Preset(
        name="NoTranscript",
        description="",
        default=True,
        created_at=NOW,
        filters=ScanFiltersModel(no_transcript=True),
    )
    config_path = _write_config_with_presets(tmp_path, archive_root, [nt_preset])

    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": "x"},
            "archive": {
                "root_dir": str(archive_root),
                "summary_format": "pdf",
            },
        }
    )
    clock = FrozenClock(NOW)
    pipeline = Pipeline(
        repository=repo,
        manifest=manifest,
        archiver=Archiver(archive_root=archive_root),
        renderer=FakeSummaryRenderer(),
        clock=clock,
    )
    deps = _common.Deps(
        config=cfg,
        pipeline=pipeline,
        manifest=manifest,
        client=cast(FirefliesClient, repo),
        clock=clock,
    )
    monkeypatch.setattr(_common, "build_deps", lambda **kw: deps)

    result = runner.invoke(app, ["run", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "NoTranscript" in result.output
    # dry-run: no mutations but nt was matched
    assert manifest.get("nt") is None
    assert repo.deleted == []
    assert "1" in result.output  # 1 meeting matched
