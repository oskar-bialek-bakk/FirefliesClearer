"""Tests for `firefliesclearer scan`."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from firefliesclearer.cli.app import app
from firefliesclearer.core.models import Meeting
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

runner = CliRunner()
NOW = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


def _meetings() -> list[Meeting]:
    return [
        Meeting(
            meeting_id="old1",
            title="Test old",
            meeting_date=NOW - timedelta(days=200),
            duration_minutes=20.0,
            host_email="u@x.com",
            participant_count=3,
            tags=(),
            has_transcript=True,
        ),
        Meeting(
            meeting_id="new1",
            title="Recent",
            meeting_date=NOW - timedelta(days=10),
            duration_minutes=30.0,
            host_email="u@x.com",
            participant_count=4,
            tags=(),
            has_transcript=True,
        ),
    ]


@pytest.fixture
def patched_deps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from firefliesclearer.cli import _common
    from firefliesclearer.core.archiver import Archiver
    from firefliesclearer.core.manifest import Manifest
    from firefliesclearer.core.pipeline import Pipeline
    from firefliesclearer.infra.config import AppConfig
    from firefliesclearer.infra.fireflies_client import FirefliesClient
    from tests.fakes.fake_renderer import FakeSummaryRenderer
    from tests.fakes.frozen_clock import FrozenClock

    repo = InMemoryMeetingRepository(meetings=_meetings())
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
        client=cast(FirefliesClient, repo),  # duck-typed; runtime compatible
        clock=FrozenClock(NOW),
    )
    monkeypatch.setattr(_common, "build_deps", lambda **kw: deps)
    return tmp_path, archive_root


def test_scan_with_older_than_writes_selection_file(patched_deps) -> None:
    _, archive_root = patched_deps
    result = runner.invoke(app, ["scan", "--older-than-days", "180"])
    assert result.exit_code == 0, result.stdout
    selections = list((archive_root / "selections").glob("scan_*.json"))
    assert len(selections) == 1
    payload = json.loads(selections[0].read_text(encoding="utf-8"))
    ids = [m["id"] for m in payload["meetings"]]
    assert ids == ["old1"]
    assert payload["meetings"][0]["selected"] is True
