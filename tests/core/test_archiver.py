"""Tests for Archiver: atomic write, verification, drift detection."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from firefliesclearer.core.archiver import (
    ArchiveDriftError,
    Archiver,
    ArchiveVerificationError,
)
from firefliesclearer.core.models import ArtifactBundle, Meeting

NOW = datetime(2026, 4, 12, 9, 0, tzinfo=UTC)


def _meeting() -> Meeting:
    return Meeting(
        meeting_id="01HW",
        title="Kickoff Marketing Q2",
        meeting_date=NOW,
        duration_minutes=45.0,
        host_email="user@example.com",
        participant_count=8,
        tags=("eng",),
        has_transcript=True,
    )


def _bundle() -> ArtifactBundle:
    return ArtifactBundle(
        audio_bytes=b"AUDIO",
        transcript_markdown="# Transcript\n\nSpeaker A: hello",
        summary_payload={"overview": "ov"},
        metadata={"source_url": "https://x"},
    )


def test_archive_writes_all_artifacts(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    pdf_bytes = b"%PDF-FAKE"
    result = archiver.archive(meeting=_meeting(), bundle=_bundle(), summary_pdf=pdf_bytes)
    d = result.archive_dir
    assert (d / "audio.mp3").read_bytes() == b"AUDIO"
    assert (d / "summary.pdf").read_bytes() == pdf_bytes
    assert "Speaker A" in (d / "transcript.md").read_text(encoding="utf-8")
    md = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    assert md["meeting_id"] == "01HW"
    assert md["source_url"] == "https://x"


def test_archive_returns_canonical_path(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    result = archiver.archive(meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X")
    expected = tmp_path / "archive" / "2026" / "04" / ("2026-04-12_kickoff-marketing-q2_01HW")
    assert result.archive_dir == expected


def test_archive_returns_sha256s(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    result = archiver.archive(meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X")
    assert set(result.sha256s.keys()) == {"audio", "summary", "transcript"}
    assert all(len(v) == 64 for v in result.sha256s.values())


def test_archive_is_atomic_no_partial_dir_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archiver = Archiver(archive_root=tmp_path)

    def boom(*a, **kw) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(archiver, "_write_metadata", boom)
    with pytest.raises(RuntimeError):
        archiver.archive(meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X")
    canonical = tmp_path / "archive" / "2026" / "04"
    assert not canonical.exists() or list(canonical.iterdir()) == []


def test_verify_returns_true_for_complete_archive(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    result = archiver.archive(meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X")
    assert archiver.verify(result.archive_dir) is True


def test_verify_returns_false_when_file_missing(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    result = archiver.archive(meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X")
    (result.archive_dir / "audio.mp3").unlink()
    assert archiver.verify(result.archive_dir) is False


def test_verify_returns_false_when_file_zero_bytes(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    result = archiver.archive(meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X")
    (result.archive_dir / "audio.mp3").write_bytes(b"")
    assert archiver.verify(result.archive_dir) is False


def test_archive_skips_when_dir_exists_and_known(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    first = archiver.archive(meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X")
    result2 = archiver.archive(
        meeting=_meeting(),
        bundle=_bundle(),
        summary_pdf=b"%PDF-X",
        allow_known=True,
    )
    assert result2.skipped is True
    # Skipped path must still surface checksums so the manifest gets a full audit
    # trail when resuming after a crash.
    assert set(result2.sha256s.keys()) == {"audio", "summary", "transcript"}
    assert result2.sha256s == first.sha256s


def test_archive_drift_raises_on_unknown_existing_dir(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    target = tmp_path / "archive" / "2026" / "04" / "2026-04-12_kickoff-marketing-q2_01HW"
    target.mkdir(parents=True)
    (target / "stranger.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(ArchiveDriftError):
        archiver.archive(meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X")


def test_verify_raises_for_missing_dir(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    with pytest.raises(ArchiveVerificationError):
        archiver.verify(tmp_path / "nonexistent")
