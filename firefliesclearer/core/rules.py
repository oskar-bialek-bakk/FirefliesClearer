"""Selection rule predicates and engine. Pure functions, no I/O."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar, Protocol

from firefliesclearer.core.models import MatchResult, Meeting


class Rule(Protocol):
    name: str

    def matches(self, meeting: Meeting, *, now: datetime) -> bool: ...


@dataclass(frozen=True, slots=True)
class OlderThanDays:
    threshold_days: int
    name: ClassVar[str] = "older_than_days"

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return meeting.meeting_date < now - timedelta(days=self.threshold_days)


@dataclass(frozen=True, slots=True)
class NoTranscript:
    name: ClassVar[str] = "no_transcript"

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return not meeting.has_transcript


@dataclass(frozen=True, slots=True)
class DurationBelow:
    threshold_minutes: float
    name: ClassVar[str] = "duration_below_minutes"

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return meeting.duration_minutes < self.threshold_minutes


@dataclass(frozen=True, slots=True)
class TitleContains:
    patterns: tuple[str, ...]
    name: ClassVar[str] = "title_contains"

    def __init__(self, patterns: Iterable[str]) -> None:
        object.__setattr__(self, "patterns", tuple(p.lower() for p in patterns))

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        if not self.patterns:
            return False
        title = meeting.title.lower()
        return any(p in title for p in self.patterns)


@dataclass(frozen=True, slots=True)
class TitleRegex:
    pattern: str
    name: ClassVar[str] = "title_regex"

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return re.search(self.pattern, meeting.title) is not None


@dataclass(frozen=True, slots=True)
class HostEmail:
    emails: tuple[str, ...]
    name: ClassVar[str] = "host_email"

    def __init__(self, emails: Iterable[str]) -> None:
        object.__setattr__(self, "emails", tuple(e.lower() for e in emails))

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return meeting.host_email.lower() in self.emails


@dataclass(frozen=True, slots=True)
class ParticipantsBelow:
    threshold: int
    name: ClassVar[str] = "participants_below"

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return meeting.participant_count < self.threshold


@dataclass(frozen=True, slots=True)
class HasTag:
    tags: tuple[str, ...]
    name: ClassVar[str] = "has_tag"

    def __init__(self, tags: Iterable[str]) -> None:
        object.__setattr__(self, "tags", tuple(t.lower() for t in tags))

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        meeting_tags = {t.lower() for t in meeting.tags}
        return any(t in meeting_tags for t in self.tags)


class RuleEngine:
    """Evaluates rules with AND semantics; reports matched rule names."""

    def __init__(self, rules: Sequence[Rule]) -> None:
        self._rules = tuple(rules)

    def evaluate(self, meeting: Meeting, *, now: datetime) -> MatchResult:
        if not self._rules:
            return MatchResult(reasons=())
        names: list[str] = []
        for rule in self._rules:
            if not rule.matches(meeting, now=now):
                return MatchResult(reasons=())
            names.append(rule.name)
        return MatchResult(reasons=tuple(names))
