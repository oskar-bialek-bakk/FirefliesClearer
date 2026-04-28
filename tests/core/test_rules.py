"""Tests for selection rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from firefliesclearer.core.models import Meeting
from firefliesclearer.core.rules import (
    DurationBelow,
    HasTag,
    HostEmail,
    NoTranscript,
    OlderThanDays,
    ParticipantsBelow,
    RuleEngine,
    TitleContains,
    TitleRegex,
)

NOW = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)


def _meeting(**overrides: Any) -> Meeting:
    base: dict[str, Any] = dict(
        meeting_id="01HW",
        title="Weekly Standup",
        meeting_date=NOW - timedelta(days=10),
        duration_minutes=30.0,
        host_email="user@example.com",
        participant_count=5,
        tags=("eng",),
        has_transcript=True,
    )
    base.update(overrides)
    return Meeting(**base)


@pytest.mark.parametrize(
    "age_days,threshold,expect",
    [(5, 7, False), (7, 7, False), (8, 7, True), (365, 30, True)],
)
def test_older_than_days(age_days: int, threshold: int, expect: bool) -> None:
    rule = OlderThanDays(threshold)
    m = _meeting(meeting_date=NOW - timedelta(days=age_days))
    assert rule.matches(m, now=NOW) is expect


def test_no_transcript_matches_only_when_missing() -> None:
    rule = NoTranscript()
    assert rule.matches(_meeting(has_transcript=False), now=NOW) is True
    assert rule.matches(_meeting(has_transcript=True), now=NOW) is False


@pytest.mark.parametrize(
    "duration,threshold,expect",
    [(0.5, 2.0, True), (2.0, 2.0, False), (3.0, 2.0, False)],
)
def test_duration_below(duration: float, threshold: float, expect: bool) -> None:
    rule = DurationBelow(threshold)
    m = _meeting(duration_minutes=duration)
    assert rule.matches(m, now=NOW) is expect


@pytest.mark.parametrize(
    "title,patterns,expect",
    [
        ("Test Standup", ["test"], True),
        ("WEEKLY", ["weekly", "draft"], True),
        ("Kickoff", ["test"], False),
        ("", ["test"], False),
    ],
)
def test_title_contains_case_insensitive(
    title: str, patterns: list[str], expect: bool
) -> None:
    rule = TitleContains(patterns)
    m = _meeting(title=title)
    assert rule.matches(m, now=NOW) is expect


def test_title_regex() -> None:
    rule = TitleRegex(r"^\[draft\].*")
    assert rule.matches(_meeting(title="[draft] Sync"), now=NOW) is True
    assert rule.matches(_meeting(title="Draft Sync"), now=NOW) is False


def test_host_email_matches_any_in_list() -> None:
    rule = HostEmail(["a@x.com", "b@x.com"])
    assert rule.matches(_meeting(host_email="a@x.com"), now=NOW) is True
    assert rule.matches(_meeting(host_email="c@x.com"), now=NOW) is False


def test_host_email_is_case_insensitive() -> None:
    rule = HostEmail(["User@Example.com"])
    assert rule.matches(_meeting(host_email="user@example.com"), now=NOW) is True


@pytest.mark.parametrize(
    "count,threshold,expect",
    [(1, 2, True), (2, 2, False), (5, 2, False)],
)
def test_participants_below(count: int, threshold: int, expect: bool) -> None:
    rule = ParticipantsBelow(threshold)
    m = _meeting(participant_count=count)
    assert rule.matches(m, now=NOW) is expect


def test_has_tag_matches_any() -> None:
    rule = HasTag(["archive", "draft"])
    assert rule.matches(_meeting(tags=("draft",)), now=NOW) is True
    assert rule.matches(_meeting(tags=("eng",)), now=NOW) is False
    assert rule.matches(_meeting(tags=()), now=NOW) is False


def test_engine_returns_match_with_reasons() -> None:
    engine = RuleEngine([OlderThanDays(7), TitleContains(["standup"])])
    m = _meeting(meeting_date=NOW - timedelta(days=30), title="Weekly Standup")
    result = engine.evaluate(m, now=NOW)
    assert result.matched is True
    assert set(result.reasons) == {"older_than_days", "title_contains"}


def test_engine_uses_and_semantics_returns_no_match_if_any_fails() -> None:
    engine = RuleEngine([OlderThanDays(7), TitleContains(["nope"])])
    m = _meeting(meeting_date=NOW - timedelta(days=30), title="Weekly Standup")
    result = engine.evaluate(m, now=NOW)
    assert result.matched is False
    assert result.reasons == ()


def test_engine_with_no_rules_never_matches() -> None:
    engine = RuleEngine([])
    result = engine.evaluate(_meeting(), now=NOW)
    assert result.matched is False
