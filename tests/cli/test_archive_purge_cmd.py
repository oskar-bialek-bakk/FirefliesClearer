"""Tests for `archive` and `purge` curated-path commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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


def _meeting(mid: str = "01HW") -> Meeting:
    return Meeting(
        meeting_id=mid,
        title="Sync",
        meeting_date=NOW,
        duration_minutes=10.0,
        host_email="u@x.com",
        participant_count=2,
        tags=(),
        has_transcript=True,
    )


def _selection(meetings: list[Meeting], path: Path) -> Path:
    payload = {
        "scan_id": "scan_test",
        "created_at": NOW.isoformat(),
        "filters_applied": {},
        "meetings": [
            {
                "id": m.meeting_id,
                "title": m.title,
                "date": m.meeting_date.isoformat(),
                "duration_min": m.duration_minutes,
                "host": m.host_email,
                "participants": m.participant_count,
                "tags": [],
                "selected": True,
                "matched_rules": [],
            }
            for m in meetings
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def patched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from firefliesclearer.cli import _common
    from firefliesclearer.core.archiver import Archiver
    from firefliesclearer.core.manifest import Manifest
    from firefliesclearer.core.pipeline import Pipeline
    from firefliesclearer.infra.config import AppConfig
    from tests.fakes.fake_renderer import FakeSummaryRenderer
    from tests.fakes.frozen_clock import FrozenClock

    m = _meeting()
    bundle = ArtifactBundle(
        audio_bytes=b"AUDIO",
        transcript_markdown="# T",
        summary_payload={},
    )
    repo = InMemoryMeetingRepository(meetings=[m], artifacts={m.meeting_id: bundle})
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
        client=cast(FirefliesClient, repo),
    )
    monkeypatch.setattr(_common, "build_deps", lambda **kw: deps)
    return tmp_path, repo, manifest


def test_archive_then_purge_full_flow(patched, tmp_path: Path) -> None:
    _, repo, manifest = patched
    selection = _selection([_meeting()], tmp_path / "sel.json")

    result = runner.invoke(app, ["archive", "--selection", str(selection)])
    assert result.exit_code == 0, result.stdout
    assert manifest.get("01HW").state is MeetingState.ARCHIVED
    assert repo.deleted == []

    result = runner.invoke(app, ["purge", "--selection", str(selection), "--yes"])
    assert result.exit_code == 0, result.stdout
    assert manifest.get("01HW").state is MeetingState.DELETED
    assert repo.deleted == ["01HW"]


def test_archive_dry_run_makes_no_changes(patched, tmp_path: Path) -> None:
    _, repo, manifest = patched
    selection = _selection([_meeting()], tmp_path / "sel.json")
    result = runner.invoke(app, ["archive", "--selection", str(selection), "--dry-run"])
    assert result.exit_code == 0
    assert manifest.get("01HW") is None
    assert repo.deleted == []


def test_purge_skips_unselected(patched, tmp_path: Path) -> None:
    _, repo, _manifest = patched
    sel_path = _selection([_meeting()], tmp_path / "sel.json")
    payload = json.loads(sel_path.read_text(encoding="utf-8"))
    payload["meetings"][0]["selected"] = False
    sel_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    runner.invoke(app, ["archive", "--selection", str(sel_path)])
    result = runner.invoke(app, ["purge", "--selection", str(sel_path), "--yes"])
    assert result.exit_code == 0
    assert repo.deleted == []
