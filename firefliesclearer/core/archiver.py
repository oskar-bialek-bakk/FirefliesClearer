"""Archiver: atomic per-meeting writes, verification, drift detection."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from firefliesclearer.core.models import ArtifactBundle, Meeting
from firefliesclearer.infra.fs import canonical_meeting_dir, sha256_file

REQUIRED_FILES = ("audio.mp3", "summary.pdf", "transcript.md", "metadata.json")


class ArchiveDriftError(Exception):
    """Existing canonical directory present but not produced by us."""


class ArchiveVerificationError(Exception):
    """Raised when a verification target is invalid (e.g., missing dir)."""


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    archive_dir: Path
    sha256s: dict[str, str]
    skipped: bool = False


class Archiver:
    """Writes meeting artifacts to a canonical archive directory atomically.

    Helper writers are regular instance methods (not staticmethods) so tests
    can patch them per-instance via ``monkeypatch.setattr(archiver, ...)``.
    """

    def __init__(self, *, archive_root: Path) -> None:
        self._root = archive_root

    def archive(
        self,
        *,
        meeting: Meeting,
        bundle: ArtifactBundle,
        summary_pdf: bytes,
        allow_known: bool = False,
    ) -> ArchiveResult:
        target = canonical_meeting_dir(
            archive_root=self._root,
            meeting_id=meeting.meeting_id,
            title=meeting.title,
            meeting_date=meeting.meeting_date,
        )
        if target.exists():
            if allow_known and self.verify(target):
                return ArchiveResult(archive_dir=target, sha256s={}, skipped=True)
            raise ArchiveDriftError(f"Canonical dir already exists: {target}")

        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"{meeting.meeting_id}.", dir=target.parent
        ) as tmpdir:
            tmp_path = Path(tmpdir)
            self._write_audio(tmp_path, bundle)
            self._write_summary(tmp_path, summary_pdf)
            self._write_transcript(tmp_path, bundle)
            self._write_metadata(tmp_path, meeting, bundle)
            shas = {
                "audio": sha256_file(tmp_path / "audio.mp3"),
                "summary": sha256_file(tmp_path / "summary.pdf"),
                "transcript": sha256_file(tmp_path / "transcript.md"),
            }
            shutil.move(str(tmp_path), str(target))
        return ArchiveResult(archive_dir=target, sha256s=shas)

    def verify(self, archive_dir: Path) -> bool:
        if not archive_dir.exists():
            raise ArchiveVerificationError(f"Archive dir missing: {archive_dir}")
        for name in REQUIRED_FILES:
            f = archive_dir / name
            if not f.exists() or f.stat().st_size == 0:
                return False
        return True

    def _write_audio(self, tmp: Path, bundle: ArtifactBundle) -> None:
        if bundle.audio_bytes is None:
            raise ValueError("audio_bytes required")
        (tmp / "audio.mp3").write_bytes(bundle.audio_bytes)

    def _write_summary(self, tmp: Path, summary_pdf: bytes) -> None:
        if not summary_pdf:
            raise ValueError("summary_pdf required")
        (tmp / "summary.pdf").write_bytes(summary_pdf)

    def _write_transcript(self, tmp: Path, bundle: ArtifactBundle) -> None:
        if bundle.transcript_markdown is None:
            raise ValueError("transcript_markdown required")
        (tmp / "transcript.md").write_text(bundle.transcript_markdown, encoding="utf-8")

    def _write_metadata(self, tmp: Path, meeting: Meeting, bundle: ArtifactBundle) -> None:
        meta = {
            "meeting_id": meeting.meeting_id,
            "title": meeting.title,
            "meeting_date": meeting.meeting_date.isoformat(),
            "duration_minutes": meeting.duration_minutes,
            "host_email": meeting.host_email,
            "participant_count": meeting.participant_count,
            "tags": list(meeting.tags),
            **bundle.metadata,
        }
        (tmp / "metadata.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
