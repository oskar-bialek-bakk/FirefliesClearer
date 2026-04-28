"""Tests for Pipeline: per-meeting transaction, failure modes, idempotency."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from firefliesclearer.core.archiver import Archiver
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import ArtifactBundle, Meeting, MeetingState
from firefliesclearer.core.pipeline import Pipeline, PipelineMode
from tests.fakes.fake_renderer import FakeSummaryRenderer
from tests.fakes.frozen_clock import FrozenClock
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


def _meeting(mid: str = "01HW") -> Meeting:
    return Meeting(
        meeting_id=mid,
        title=f"Meeting {mid}",
        meeting_date=NOW,
        duration_minutes=10.0,
        host_email="u@x.com",
        participant_count=2,
        tags=(),
        has_transcript=True,
    )


def _bundle() -> ArtifactBundle:
    return ArtifactBundle(
        audio_bytes=b"AUDIO",
        transcript_markdown="# T",
        summary_payload={"overview": "ov"},
    )


def _build(
    tmp_path: Path,
    meetings: list[Meeting],
    **kw: Any,
) -> tuple[Pipeline, InMemoryMeetingRepository, Manifest, Archiver, FakeSummaryRenderer]:
    repo = InMemoryMeetingRepository(
        meetings=list(meetings),
        artifacts={m.meeting_id: _bundle() for m in meetings},
    )
    repo.fail_fetch_for = set(kw.get("fail_fetch_for", []))
    repo.fail_delete_for = set(kw.get("fail_delete_for", []))
    manifest = Manifest.open(tmp_path / "manifest.db")
    archiver = Archiver(archive_root=tmp_path)
    renderer = FakeSummaryRenderer()
    clock = FrozenClock(NOW)
    pipeline = Pipeline(
        repository=repo,
        manifest=manifest,
        archiver=archiver,
        renderer=renderer,
        clock=clock,
    )
    return pipeline, repo, manifest, archiver, renderer


async def test_happy_path_archives_and_deletes(tmp_path: Path) -> None:
    """Apply mode: meeting goes pending -> archived -> deleted in one run."""
    m = _meeting()
    pipeline, repo, manifest, _, _ = _build(tmp_path, [m])
    report = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report.archived == 1
    assert report.deleted == 1
    rec = manifest.get(m.meeting_id)
    assert rec is not None
    assert rec.state is MeetingState.DELETED
    assert repo.deleted == [m.meeting_id]


async def test_dry_run_makes_no_mutations(tmp_path: Path) -> None:
    """Dry-run mode does not touch manifest, archive, or repo."""
    m = _meeting()
    pipeline, repo, manifest, _, _ = _build(tmp_path, [m])
    report = await pipeline.run([m], mode=PipelineMode.DRY_RUN)
    assert report.archived == 0
    assert report.deleted == 0
    assert manifest.get(m.meeting_id) is None
    assert repo.deleted == []


async def test_fetch_failure_records_state_no_delete(tmp_path: Path) -> None:
    """Fetch failure -> FAILED_FETCH; no delete attempted."""
    m = _meeting()
    pipeline, repo, manifest, _, _ = _build(tmp_path, [m], fail_fetch_for=[m.meeting_id])
    report = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report.failed == 1
    rec = manifest.get(m.meeting_id)
    assert rec is not None
    assert rec.state is MeetingState.FAILED_FETCH
    assert repo.deleted == []


async def test_delete_failure_keeps_archive(tmp_path: Path) -> None:
    """Delete failure after successful archive -> DELETED_FAILED, archive intact."""
    m = _meeting()
    pipeline, _repo, manifest, _, _ = _build(tmp_path, [m], fail_delete_for=[m.meeting_id])
    report = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report.deleted == 0
    rec = manifest.get(m.meeting_id)
    assert rec is not None
    assert rec.state is MeetingState.DELETED_FAILED
    assert rec.archive_path is not None
    assert Path(rec.archive_path).exists()


async def test_idempotent_second_run_skips_already_deleted(tmp_path: Path) -> None:
    """Running again on a DELETED meeting bumps skipped, no state change."""
    m = _meeting()
    pipeline, _, manifest, _, _ = _build(tmp_path, [m])
    await pipeline.run([m], mode=PipelineMode.APPLY)
    report2 = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report2.skipped == 1
    rec = manifest.get(m.meeting_id)
    assert rec is not None
    assert rec.state is MeetingState.DELETED


async def test_one_failure_does_not_abort_run(tmp_path: Path) -> None:
    """A single meeting failure does not stop processing of others."""
    m1, m2, m3 = _meeting("a"), _meeting("b"), _meeting("c")
    pipeline, _, manifest, _, _ = _build(tmp_path, [m1, m2, m3], fail_fetch_for=["b"])
    report = await pipeline.run([m1, m2, m3], mode=PipelineMode.APPLY)
    assert report.archived == 2
    assert report.deleted == 2
    assert report.failed == 1
    rb = manifest.get("b")
    ra = manifest.get("a")
    rc = manifest.get("c")
    assert rb is not None and rb.state is MeetingState.FAILED_FETCH
    assert ra is not None and ra.state is MeetingState.DELETED
    assert rc is not None and rc.state is MeetingState.DELETED


async def test_archive_only_mode_does_not_delete(tmp_path: Path) -> None:
    """Archive-only mode: archive but never call repo.delete."""
    m = _meeting()
    pipeline, repo, manifest, _, _ = _build(tmp_path, [m])
    report = await pipeline.run([m], mode=PipelineMode.ARCHIVE_ONLY)
    assert report.archived == 1
    assert report.deleted == 0
    rec = manifest.get(m.meeting_id)
    assert rec is not None
    assert rec.state is MeetingState.ARCHIVED
    assert repo.deleted == []


async def test_purge_only_requires_archived_state(tmp_path: Path) -> None:
    """Purge-only deletes only meetings already in ARCHIVED state."""
    m = _meeting()
    pipeline, _, manifest, _, _ = _build(tmp_path, [m])
    await pipeline.run([m], mode=PipelineMode.ARCHIVE_ONLY)
    rec_before = manifest.get(m.meeting_id)
    assert rec_before is not None and rec_before.state is MeetingState.ARCHIVED
    report = await pipeline.run([m], mode=PipelineMode.PURGE_ONLY)
    assert report.deleted == 1
    rec_after = manifest.get(m.meeting_id)
    assert rec_after is not None
    assert rec_after.state is MeetingState.DELETED


async def test_purge_only_skips_pending_meetings(tmp_path: Path) -> None:
    """Purge-only on never-archived meeting: no delete, increments skipped."""
    m = _meeting()
    pipeline, repo, _, _, _ = _build(tmp_path, [m])
    report = await pipeline.run([m], mode=PipelineMode.PURGE_ONLY)
    assert report.deleted == 0
    assert report.skipped == 1
    assert repo.deleted == []


async def test_failed_fetch_then_retry_succeeds(tmp_path: Path) -> None:
    """Requeue path: FAILED_FETCH -> PENDING -> ARCHIVED -> DELETED on retry.

    Covers ``existing.state.value.startswith("failed_")`` branch.
    """
    m = _meeting()
    pipeline, repo, manifest, _, _ = _build(tmp_path, [m], fail_fetch_for=[m.meeting_id])
    report1 = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report1.failed == 1
    rec1 = manifest.get(m.meeting_id)
    assert rec1 is not None and rec1.state is MeetingState.FAILED_FETCH

    # Clear the failure flag and rerun.
    repo.fail_fetch_for = set()
    report2 = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report2.archived == 1
    assert report2.deleted == 1
    rec2 = manifest.get(m.meeting_id)
    assert rec2 is not None and rec2.state is MeetingState.DELETED


async def test_archive_drift_records_failed_verify(tmp_path: Path) -> None:
    """ArchiveDriftError path: stranger directory at canonical path.

    Pre-create the canonical directory empty so ``verify`` returns False and
    ``archive`` raises ``ArchiveDriftError``.
    """
    from firefliesclearer.infra.fs import canonical_meeting_dir

    m = _meeting()
    target = canonical_meeting_dir(
        archive_root=tmp_path,
        meeting_id=m.meeting_id,
        title=m.title,
        meeting_date=m.meeting_date,
    )
    target.mkdir(parents=True, exist_ok=True)
    (target / "stranger.txt").write_text("not ours", encoding="utf-8")

    pipeline, repo, manifest, _, _ = _build(tmp_path, [m])
    report = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report.failed == 1
    assert report.archived == 0
    rec = manifest.get(m.meeting_id)
    assert rec is not None and rec.state is MeetingState.FAILED_VERIFY
    assert rec.last_error is not None
    assert repo.deleted == []


async def test_render_failure_records_failed_render(tmp_path: Path) -> None:
    """Render exception -> FAILED_RENDER state."""
    m = _meeting()
    pipeline, repo, manifest, _, renderer = _build(tmp_path, [m])

    def boom(payload: dict[str, Any], *, meeting_title: str) -> bytes:
        raise RuntimeError("render kaboom")

    renderer.render = boom  # type: ignore[method-assign]
    report = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report.failed == 1
    rec = manifest.get(m.meeting_id)
    assert rec is not None and rec.state is MeetingState.FAILED_RENDER
    assert rec.last_error is not None and "render kaboom" in rec.last_error
    assert repo.deleted == []


async def test_archive_write_failure_records_failed_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generic exception from archiver.archive -> FAILED_DOWNLOAD state."""
    m = _meeting()
    pipeline, repo, manifest, archiver, _ = _build(tmp_path, [m])

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(archiver, "archive", boom)
    report = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report.failed == 1
    rec = manifest.get(m.meeting_id)
    assert rec is not None and rec.state is MeetingState.FAILED_DOWNLOAD
    assert rec.last_error is not None and "disk full" in rec.last_error
    assert repo.deleted == []


async def test_verify_returns_false_records_failed_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """archiver.verify returning False after archive -> FAILED_VERIFY."""
    m = _meeting()
    pipeline, repo, manifest, archiver, _ = _build(tmp_path, [m])

    def always_false(_archive_dir: Path) -> bool:
        return False

    monkeypatch.setattr(archiver, "verify", always_false)
    report = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report.failed == 1
    rec = manifest.get(m.meeting_id)
    assert rec is not None and rec.state is MeetingState.FAILED_VERIFY
    assert rec.last_error == "verify failed"
    assert repo.deleted == []


async def test_failed_then_retry_fails_again_short_circuits(tmp_path: Path) -> None:
    """Requeue path where retry also fails: covers the early-return after _archive.

    Covers the ``if not ok: return`` after the failed_* requeue branch.
    """
    m = _meeting()
    pipeline, repo, manifest, _, _ = _build(tmp_path, [m], fail_fetch_for=[m.meeting_id])
    # First run: fails fetch.
    await pipeline.run([m], mode=PipelineMode.APPLY)
    rec1 = manifest.get(m.meeting_id)
    assert rec1 is not None and rec1.state is MeetingState.FAILED_FETCH

    # Second run: fetch still fails (fail_fetch_for retained), so requeue
    # path runs PENDING -> _archive() -> False -> early return without delete.
    report2 = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report2.failed == 1
    assert report2.deleted == 0
    rec2 = manifest.get(m.meeting_id)
    assert rec2 is not None and rec2.state is MeetingState.FAILED_FETCH
    assert repo.deleted == []


async def test_delete_guard_when_not_archived(tmp_path: Path) -> None:
    """Defensive guard in _delete: returns silently if record not ARCHIVED.

    Exercises ``_delete`` directly with a meeting that has no manifest record,
    covering the ``rec is None or rec.state is not ARCHIVED`` early return.
    """
    from firefliesclearer.core.pipeline import RunReport

    m = _meeting()
    pipeline, repo, _, _, _ = _build(tmp_path, [m])
    r = RunReport()
    await pipeline._delete(m, r)  # no manifest entry -> early return
    assert r.deleted == 0
    assert r.failed == 0
    assert repo.deleted == []


async def test_archive_only_idempotent_skips_archived(tmp_path: Path) -> None:
    """Re-running ARCHIVE_ONLY on an ARCHIVED meeting must be a no-op.

    The meeting's state is not PENDING and not failed_*, so the archive branch
    is skipped entirely. The mode also skips the delete step.
    """
    m = _meeting()
    pipeline, repo, manifest, _, _ = _build(tmp_path, [m])
    await pipeline.run([m], mode=PipelineMode.ARCHIVE_ONLY)
    rec1 = manifest.get(m.meeting_id)
    assert rec1 is not None and rec1.state is MeetingState.ARCHIVED

    report = await pipeline.run([m], mode=PipelineMode.ARCHIVE_ONLY)
    assert report.archived == 0
    assert report.deleted == 0
    rec2 = manifest.get(m.meeting_id)
    assert rec2 is not None and rec2.state is MeetingState.ARCHIVED
    assert repo.deleted == []
