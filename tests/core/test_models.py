"""Tests for domain models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from firefliesclearer.core.models import (
    ArtifactBundle,
    MatchResult,
    Meeting,
    MeetingState,
)


def _meeting(**overrides):
    base = dict(
        meeting_id="01HW123",
        title="Weekly Standup",
        meeting_date=datetime(2026, 4, 1, 9, 0, tzinfo=UTC),
        duration_minutes=30.0,
        host_email="user@example.com",
        participant_count=5,
        tags=("eng",),
        has_transcript=True,
    )
    base.update(overrides)
    return Meeting(**base)


def test_meeting_is_frozen():
    m = _meeting()
    with pytest.raises(AttributeError):
        m.title = "Changed"  # type: ignore[misc]


def test_meeting_equality_by_value():
    a = _meeting()
    b = _meeting()
    assert a == b


def test_meeting_tags_are_tuple():
    m = _meeting(tags=("a", "b"))
    assert isinstance(m.tags, tuple)


def test_match_result_matched_when_at_least_one_reason():
    r = MatchResult(reasons=("older_than_days",))
    assert r.matched is True


def test_match_result_not_matched_when_no_reasons():
    r = MatchResult(reasons=())
    assert r.matched is False


def test_artifact_bundle_default_empty():
    b = ArtifactBundle()
    assert b.audio_bytes is None
    assert b.transcript_markdown is None
    assert b.summary_payload is None
    assert b.metadata == {}


def test_meeting_state_values():
    assert {s.value for s in MeetingState} == {
        "known",
        "pending",
        "archived",
        "deleted",
        "failed_fetch",
        "failed_download",
        "failed_render",
        "failed_verify",
        "deleted_failed",
    }
