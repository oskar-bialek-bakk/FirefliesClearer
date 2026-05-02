"""Domain types: immutable, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class MeetingState(StrEnum):
    KNOWN = "known"
    PENDING = "pending"
    ARCHIVED = "archived"
    DELETED = "deleted"
    FAILED_FETCH = "failed_fetch"
    FAILED_DOWNLOAD = "failed_download"
    FAILED_RENDER = "failed_render"
    FAILED_VERIFY = "failed_verify"
    DELETED_FAILED = "deleted_failed"


@dataclass(frozen=True, slots=True)
class Meeting:
    meeting_id: str
    title: str
    meeting_date: datetime
    duration_minutes: float
    host_email: str
    participant_count: int
    tags: tuple[str, ...] = ()
    has_transcript: bool = True


@dataclass(frozen=True, slots=True)
class MatchResult:
    reasons: tuple[str, ...]

    @property
    def matched(self) -> bool:
        return len(self.reasons) > 0


@dataclass(frozen=True, slots=True)
class ArtifactBundle:
    audio_bytes: bytes | None = None
    transcript_markdown: str | None = None
    summary_payload: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
