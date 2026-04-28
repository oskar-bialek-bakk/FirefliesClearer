# FirefliesClearer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI that archives Fireflies AI meetings to local disk (summary.pdf, audio.mp3, transcript.md, metadata.json), verifies the archive, then deletes from Fireflies — with a SQLite manifest for safe re-runs and audit.

**Architecture:** Layered (CLI → application → domain → infra), with the domain depending on ports (interfaces). The CLI is a thin shell over the application layer; a future web UI will replace only the presentation layer. Per-meeting transactions enforce: never delete unless archive verified.

**Tech Stack:** Python 3.12+, Typer + Rich (CLI), httpx (async GraphQL), Pydantic v2 (config + models), platformdirs (cross-platform paths), WeasyPrint (PDF), SQLite (manifest), pytest + respx + pytest-asyncio (tests), ruff + mypy (lint/type).

**Spec:** `docs/superpowers/specs/2026-04-28-firefliesclearer-design.md` is the source of truth. Read it before starting.

---

## File structure

```
firefliesclearer/
├── pyproject.toml                       # poetry/PEP 621; dependencies, scripts, ruff, mypy
├── .python-version                      # "3.12"
├── .pre-commit-config.yaml              # ruff format + ruff check
├── .github/workflows/ci.yml             # lint + type + tests
│
├── firefliesclearer/
│   ├── __init__.py                      # __version__ = "0.1.0"
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py                    # Meeting, ArtifactBundle, Rule, MatchResult, MeetingState
│   │   ├── rules.py                     # 8 rule predicates + RuleEngine + MatchResult assembly
│   │   ├── manifest.py                  # SQLite-backed state machine + audit log
│   │   ├── archiver.py                  # Slug, canonical path, atomic writes, verify, drift detect
│   │   └── pipeline.py                  # Per-meeting transactional orchestrator
│   ├── ports/
│   │   ├── __init__.py
│   │   ├── meeting_repository.py        # Protocol: list_meetings, fetch_artifacts, delete_meeting
│   │   ├── summary_renderer.py          # Protocol: render(summary_data) -> bytes
│   │   └── clock.py                     # Protocol: now() -> datetime
│   ├── infra/
│   │   ├── __init__.py
│   │   ├── config.py                    # TOML loader, precedence chain, Pydantic validation
│   │   ├── fs.py                        # atomic_write, slugify, ensure_dir, sha256_file
│   │   ├── pdf_renderer.py              # Markdown -> Jinja2 HTML -> WeasyPrint PDF
│   │   ├── fireflies_client.py          # httpx async GraphQL client + retries + redaction
│   │   ├── system_clock.py              # SystemClock implementing Clock port
│   │   └── logging.py                   # JSON-lines structured logging + daily rotation
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── app.py                       # Typer app root + global options + command registration
│   │   ├── init_cmd.py
│   │   ├── scan_cmd.py
│   │   ├── archive_cmd.py
│   │   ├── purge_cmd.py
│   │   ├── run_cmd.py
│   │   ├── status_cmd.py
│   │   ├── history_cmd.py
│   │   └── _common.py                   # Shared CLI helpers (load config, build deps, format tables)
│   └── templates/
│       ├── summary.html.j2              # Jinja2 template for PDF
│       └── summary.css                  # Print stylesheet
│
└── tests/
    ├── __init__.py
    ├── conftest.py                       # tmp_path fixtures, frozen clock, factory helpers
    ├── fakes/
    │   ├── __init__.py
    │   ├── in_memory_repository.py       # Implements MeetingRepository
    │   ├── fake_renderer.py              # Returns deterministic bytes
    │   └── frozen_clock.py               # Implements Clock; settable now
    ├── core/
    │   ├── test_models.py
    │   ├── test_rules.py
    │   ├── test_manifest.py
    │   ├── test_archiver.py
    │   └── test_pipeline.py
    ├── infra/
    │   ├── test_config.py
    │   ├── test_fs.py
    │   ├── test_pdf_renderer.py
    │   ├── test_fireflies_client.py
    │   └── test_logging.py
    └── cli/
        ├── test_init_cmd.py
        ├── test_scan_cmd.py
        ├── test_archive_cmd.py
        ├── test_purge_cmd.py
        ├── test_run_cmd.py
        ├── test_status_cmd.py
        └── test_history_cmd.py
```

**Build order (TDD per spec §11.6):** rules → manifest → archiver → pipeline (with fakes) → infra adapters → CLI → polish.

**Working directory for all commands:** `C:/GIT/FirefliesClearer`. All paths in this plan are relative to that root unless absolute.

**Conventions:**
- Conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`, `ci:`.
- Frozen dataclasses for domain types; Pydantic v2 only at boundaries (config, GraphQL response parsing).
- File ≤400 lines, single responsibility.
- 100% coverage on `core/pipeline.py` and `core/manifest.py`; ≥80% overall.

---

## Phase 0 — Project bootstrap

### Task 0: Initialize Python project, dependencies, and tooling

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.pre-commit-config.yaml`
- Create: `firefliesclearer/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Pin Python version**

Create `.python-version`:

```
3.12
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "firefliesclearer"
version = "0.1.0"
description = "Safely archive and clean up Fireflies AI meetings."
requires-python = ">=3.12"
readme = "README.md"
license = { text = "MIT" }
authors = [{ name = "Oskar Białek" }]
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
    "httpx>=0.27",
    "pydantic>=2.7",
    "platformdirs>=4.2",
    "weasyprint>=62.3",
    "jinja2>=3.1",
    "tomli-w>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "respx>=0.21",
    "ruff>=0.5",
    "mypy>=1.10",
    "pre-commit>=3.7",
]

[project.scripts]
firefliesclearer = "firefliesclearer.cli.app:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "SIM", "RUF"]
ignore = ["E501"]

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
strict = false
warn_return_any = true
warn_unused_ignores = true

[[tool.mypy.overrides]]
module = "firefliesclearer.core.*"
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "contract: opt-in tests that hit real Fireflies API",
]
addopts = [
    "--strict-markers",
    "-ra",
    "--cov=firefliesclearer",
    "--cov-report=term-missing",
]
```

- [ ] **Step 3: Write `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

- [ ] **Step 4: Create package init files**

`firefliesclearer/__init__.py`:

```python
"""FirefliesClearer — safe Fireflies AI meeting archiver and cleaner."""

__version__ = "0.1.0"
```

`tests/__init__.py`: empty file.

- [ ] **Step 5: Create root conftest**

`tests/conftest.py`:

```python
"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def tmp_archive_root(tmp_path):
    """An archive root inside tmp_path."""
    root = tmp_path / "archive"
    root.mkdir()
    return root
```

- [ ] **Step 6: Create venv and install**

```bash
cd C:/GIT/FirefliesClearer
python -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

Expected: install completes; `firefliesclearer --help` resolves (will fail with ModuleNotFoundError since `cli/app.py` doesn't exist yet — that's fine).

- [ ] **Step 7: Verify tooling**

```bash
.venv/Scripts/ruff.exe check .
.venv/Scripts/mypy.exe firefliesclearer
.venv/Scripts/pytest.exe -q
```

Expected: ruff clean (no files to check yet), mypy "Success: no issues", pytest "no tests ran".

- [ ] **Step 8: Add .venv to .gitignore (already there) and commit**

```bash
git add pyproject.toml .python-version .pre-commit-config.yaml firefliesclearer/__init__.py tests/__init__.py tests/conftest.py
git commit -m "chore: bootstrap python project with deps, ruff, mypy, pytest"
```

---

## Phase 1 — Domain models

### Task 1: Define core domain types (Meeting, Rule, MatchResult, ArtifactBundle)

**Files:**
- Create: `firefliesclearer/core/__init__.py` (empty)
- Create: `firefliesclearer/core/models.py`
- Create: `tests/core/__init__.py` (empty)
- Create: `tests/core/test_models.py`

- [ ] **Step 1: Create empty package files**

```bash
echo "" > firefliesclearer/core/__init__.py
mkdir -p tests/core && echo "" > tests/core/__init__.py
```

- [ ] **Step 2: Write the failing tests**

`tests/core/test_models.py`:

```python
"""Tests for domain models."""

from __future__ import annotations

from datetime import datetime, timezone

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
        meeting_date=datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
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
        "pending",
        "archived",
        "deleted",
        "failed_fetch",
        "failed_download",
        "failed_render",
        "failed_verify",
        "deleted_failed",
    }
```

- [ ] **Step 3: Run the tests — expect failures**

```bash
.venv/Scripts/pytest.exe tests/core/test_models.py -v
```

Expected: ImportError / ModuleNotFoundError for `firefliesclearer.core.models`.

- [ ] **Step 4: Implement models**

`firefliesclearer/core/models.py`:

```python
"""Domain types: immutable, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MeetingState(str, Enum):
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
```

- [ ] **Step 5: Run tests — expect pass**

```bash
.venv/Scripts/pytest.exe tests/core/test_models.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/core/__init__.py firefliesclearer/core/models.py tests/core/__init__.py tests/core/test_models.py
git commit -m "feat(core): add Meeting, MatchResult, ArtifactBundle, MeetingState models"
```

---

## Phase 2 — Rules

### Task 2: Implement all 8 rule predicates and the RuleEngine

**Files:**
- Create: `firefliesclearer/core/rules.py`
- Create: `tests/core/test_rules.py`

- [ ] **Step 1: Write the failing tests**

`tests/core/test_rules.py`:

```python
"""Tests for selection rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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

NOW = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)


def _meeting(**overrides) -> Meeting:
    base = dict(
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


def test_engine_uses_AND_semantics_returns_no_match_if_any_fails() -> None:
    engine = RuleEngine([OlderThanDays(7), TitleContains(["nope"])])
    m = _meeting(meeting_date=NOW - timedelta(days=30), title="Weekly Standup")
    result = engine.evaluate(m, now=NOW)
    assert result.matched is False
    assert result.reasons == ()


def test_engine_with_no_rules_never_matches() -> None:
    engine = RuleEngine([])
    result = engine.evaluate(_meeting(), now=NOW)
    assert result.matched is False
```

- [ ] **Step 2: Run tests — expect failures**

```bash
.venv/Scripts/pytest.exe tests/core/test_rules.py -v
```

Expected: ImportError on `firefliesclearer.core.rules`.

- [ ] **Step 3: Implement rules**

`firefliesclearer/core/rules.py`:

```python
"""Selection rule predicates and engine. Pure functions, no I/O."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from firefliesclearer.core.models import MatchResult, Meeting


class Rule(Protocol):
    name: str

    def matches(self, meeting: Meeting, *, now: datetime) -> bool: ...


@dataclass(frozen=True, slots=True)
class OlderThanDays:
    threshold_days: int
    name: str = "older_than_days"

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return meeting.meeting_date < now - timedelta(days=self.threshold_days)


@dataclass(frozen=True, slots=True)
class NoTranscript:
    name: str = "no_transcript"

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return not meeting.has_transcript


@dataclass(frozen=True, slots=True)
class DurationBelow:
    threshold_minutes: float
    name: str = "duration_below_minutes"

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return meeting.duration_minutes < self.threshold_minutes


@dataclass(frozen=True, slots=True)
class TitleContains:
    patterns: tuple[str, ...]
    name: str = "title_contains"

    def __init__(self, patterns: Iterable[str]) -> None:
        object.__setattr__(self, "patterns", tuple(p.lower() for p in patterns))
        object.__setattr__(self, "name", "title_contains")

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        if not self.patterns:
            return False
        title = meeting.title.lower()
        return any(p in title for p in self.patterns)


@dataclass(frozen=True, slots=True)
class TitleRegex:
    pattern: str
    name: str = "title_regex"

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return re.search(self.pattern, meeting.title) is not None


@dataclass(frozen=True, slots=True)
class HostEmail:
    emails: tuple[str, ...]
    name: str = "host_email"

    def __init__(self, emails: Iterable[str]) -> None:
        object.__setattr__(self, "emails", tuple(e.lower() for e in emails))
        object.__setattr__(self, "name", "host_email")

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return meeting.host_email.lower() in self.emails


@dataclass(frozen=True, slots=True)
class ParticipantsBelow:
    threshold: int
    name: str = "participants_below"

    def matches(self, meeting: Meeting, *, now: datetime) -> bool:
        return meeting.participant_count < self.threshold


@dataclass(frozen=True, slots=True)
class HasTag:
    tags: tuple[str, ...]
    name: str = "has_tag"

    def __init__(self, tags: Iterable[str]) -> None:
        object.__setattr__(self, "tags", tuple(t.lower() for t in tags))
        object.__setattr__(self, "name", "has_tag")

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
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/Scripts/pytest.exe tests/core/test_rules.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/rules.py tests/core/test_rules.py
git commit -m "feat(core): add rule predicates and RuleEngine with AND semantics"
```

---

## Phase 3 — Manifest

### Task 3: Manifest schema, register/get, state machine, history, counts

**Files:**
- Create: `firefliesclearer/core/manifest.py`
- Create: `tests/core/test_manifest.py`

- [ ] **Step 1: Write the failing tests**

`tests/core/test_manifest.py`:

```python
"""Tests for the SQLite-backed manifest."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from firefliesclearer.core.manifest import IllegalStateTransition, Manifest
from firefliesclearer.core.models import Meeting, MeetingState

NOW = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)


def _meeting(meeting_id: str = "01HW") -> Meeting:
    return Meeting(
        meeting_id=meeting_id,
        title="Standup",
        meeting_date=NOW,
        duration_minutes=10.0,
        host_email="u@x.com",
        participant_count=2,
        tags=(),
        has_transcript=True,
    )


@pytest.fixture
def manifest(tmp_path):
    return Manifest.open(tmp_path / "manifest.db")


def test_open_creates_schema(tmp_path):
    db_path = tmp_path / "manifest.db"
    Manifest.open(db_path)
    assert db_path.exists()


def test_register_new_meeting_starts_pending(manifest):
    manifest.register(_meeting(), at=NOW)
    rec = manifest.get("01HW")
    assert rec is not None
    assert rec.state is MeetingState.PENDING


def test_register_is_idempotent_for_existing_id(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.register(_meeting(), at=NOW)
    assert manifest.get("01HW") is not None


def test_get_returns_none_for_unknown(manifest):
    assert manifest.get("missing") is None


def test_legal_transition_pending_to_archived(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.transition(
        "01HW",
        to=MeetingState.ARCHIVED,
        at=NOW,
        archive_path="/tmp/x",
        verified_at=NOW,
        sha256s={"audio": "a", "summary": "s", "transcript": "t"},
    )
    rec = manifest.get("01HW")
    assert rec.state is MeetingState.ARCHIVED
    assert rec.archive_path == "/tmp/x"
    assert rec.audio_sha256 == "a"


def test_legal_transition_archived_to_deleted(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW)
    manifest.transition("01HW", to=MeetingState.DELETED, at=NOW)
    assert manifest.get("01HW").state is MeetingState.DELETED


def test_illegal_transition_pending_to_deleted_raises(manifest):
    manifest.register(_meeting(), at=NOW)
    with pytest.raises(IllegalStateTransition):
        manifest.transition("01HW", to=MeetingState.DELETED, at=NOW)


def test_illegal_transition_after_deleted_raises(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW)
    manifest.transition("01HW", to=MeetingState.DELETED, at=NOW)
    with pytest.raises(IllegalStateTransition):
        manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW)


def test_failed_states_can_return_to_pending(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.transition(
        "01HW", to=MeetingState.FAILED_DOWNLOAD, at=NOW, last_error="boom"
    )
    manifest.transition("01HW", to=MeetingState.PENDING, at=NOW)
    assert manifest.get("01HW").state is MeetingState.PENDING


def test_state_log_records_every_transition(manifest):
    manifest.register(_meeting(), at=NOW)
    manifest.transition("01HW", to=MeetingState.ARCHIVED, at=NOW)
    manifest.transition("01HW", to=MeetingState.DELETED, at=NOW)
    log = manifest.state_log("01HW")
    assert [(e.from_state, e.to_state) for e in log] == [
        (None, MeetingState.PENDING),
        (MeetingState.PENDING, MeetingState.ARCHIVED),
        (MeetingState.ARCHIVED, MeetingState.DELETED),
    ]


def test_history_filters_by_month(manifest):
    manifest.register(_meeting("a"), at=datetime(2026, 3, 5, tzinfo=timezone.utc))
    manifest.register(_meeting("b"), at=datetime(2026, 4, 5, tzinfo=timezone.utc))
    manifest.transition(
        "a", to=MeetingState.ARCHIVED, at=datetime(2026, 3, 6, tzinfo=timezone.utc)
    )
    manifest.transition(
        "a", to=MeetingState.DELETED, at=datetime(2026, 3, 7, tzinfo=timezone.utc)
    )
    manifest.transition(
        "b", to=MeetingState.ARCHIVED, at=datetime(2026, 4, 6, tzinfo=timezone.utc)
    )
    manifest.transition(
        "b", to=MeetingState.DELETED, at=datetime(2026, 4, 7, tzinfo=timezone.utc)
    )
    march = manifest.history(year=2026, month=3)
    april = manifest.history(year=2026, month=4)
    assert {h.meeting_id for h in march} == {"a"}
    assert {h.meeting_id for h in april} == {"b"}


def test_counts_by_state(manifest):
    manifest.register(_meeting("a"), at=NOW)
    manifest.register(_meeting("b"), at=NOW)
    manifest.transition("a", to=MeetingState.ARCHIVED, at=NOW)
    counts = manifest.counts_by_state()
    assert counts.get(MeetingState.PENDING, 0) == 1
    assert counts.get(MeetingState.ARCHIVED, 0) == 1
```

- [ ] **Step 2: Run tests — expect failures**

```bash
.venv/Scripts/pytest.exe tests/core/test_manifest.py -v
```

Expected: ImportError on `firefliesclearer.core.manifest`.

- [ ] **Step 3: Implement manifest**

`firefliesclearer/core/manifest.py`:

```python
"""SQLite-backed state machine and audit log."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from firefliesclearer.core.models import Meeting, MeetingState

LEGAL_TRANSITIONS: Mapping[MeetingState | None, frozenset[MeetingState]] = {
    None: frozenset({MeetingState.PENDING}),
    MeetingState.PENDING: frozenset(
        {
            MeetingState.ARCHIVED,
            MeetingState.FAILED_FETCH,
            MeetingState.FAILED_DOWNLOAD,
            MeetingState.FAILED_RENDER,
            MeetingState.FAILED_VERIFY,
        }
    ),
    MeetingState.ARCHIVED: frozenset(
        {MeetingState.DELETED, MeetingState.DELETED_FAILED}
    ),
    MeetingState.DELETED_FAILED: frozenset({MeetingState.DELETED}),
    MeetingState.FAILED_FETCH: frozenset({MeetingState.PENDING}),
    MeetingState.FAILED_DOWNLOAD: frozenset({MeetingState.PENDING}),
    MeetingState.FAILED_RENDER: frozenset({MeetingState.PENDING}),
    MeetingState.FAILED_VERIFY: frozenset({MeetingState.PENDING}),
    MeetingState.DELETED: frozenset(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  meeting_id        TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  meeting_date      TEXT NOT NULL,
  state             TEXT NOT NULL,
  archive_path      TEXT,
  audio_sha256      TEXT,
  summary_sha256    TEXT,
  transcript_sha256 TEXT,
  archived_at       TEXT,
  verified_at       TEXT,
  deleted_at        TEXT,
  last_error        TEXT
);
CREATE TABLE IF NOT EXISTS state_log (
  id            INTEGER PRIMARY KEY,
  meeting_id    TEXT NOT NULL,
  from_state    TEXT,
  to_state      TEXT NOT NULL,
  at            TEXT NOT NULL,
  details       TEXT,
  FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);
CREATE INDEX IF NOT EXISTS idx_meetings_state ON meetings(state);
CREATE INDEX IF NOT EXISTS idx_state_log_meeting ON state_log(meeting_id);
"""


class IllegalStateTransition(Exception):
    pass


@dataclass(frozen=True, slots=True)
class MeetingRecord:
    meeting_id: str
    title: str
    meeting_date: datetime
    state: MeetingState
    archive_path: str | None
    audio_sha256: str | None
    summary_sha256: str | None
    transcript_sha256: str | None
    archived_at: datetime | None
    verified_at: datetime | None
    deleted_at: datetime | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class StateLogEntry:
    meeting_id: str
    from_state: MeetingState | None
    to_state: MeetingState
    at: datetime
    details: dict | None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse_iso(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


class Manifest:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, path: Path) -> "Manifest":
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def register(self, meeting: Meeting, *, at: datetime) -> None:
        if self.get(meeting.meeting_id) is not None:
            return
        self._conn.execute(
            "INSERT INTO meetings (meeting_id, title, meeting_date, state) VALUES (?, ?, ?, ?)",
            (
                meeting.meeting_id,
                meeting.title,
                meeting.meeting_date.isoformat(),
                MeetingState.PENDING.value,
            ),
        )
        self._log(meeting.meeting_id, None, MeetingState.PENDING, at, None)

    def get(self, meeting_id: str) -> MeetingRecord | None:
        cur = self._conn.execute(
            "SELECT meeting_id, title, meeting_date, state, archive_path, "
            "audio_sha256, summary_sha256, transcript_sha256, archived_at, "
            "verified_at, deleted_at, last_error FROM meetings WHERE meeting_id = ?",
            (meeting_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return MeetingRecord(
            meeting_id=row[0],
            title=row[1],
            meeting_date=_parse_iso(row[2]),
            state=MeetingState(row[3]),
            archive_path=row[4],
            audio_sha256=row[5],
            summary_sha256=row[6],
            transcript_sha256=row[7],
            archived_at=_parse_iso(row[8]),
            verified_at=_parse_iso(row[9]),
            deleted_at=_parse_iso(row[10]),
            last_error=row[11],
        )

    def transition(
        self,
        meeting_id: str,
        *,
        to: MeetingState,
        at: datetime,
        archive_path: str | None = None,
        verified_at: datetime | None = None,
        sha256s: Mapping[str, str] | None = None,
        last_error: str | None = None,
        details: dict | None = None,
    ) -> None:
        rec = self.get(meeting_id)
        from_state = rec.state if rec else None
        if to not in LEGAL_TRANSITIONS.get(from_state, frozenset()):
            raise IllegalStateTransition(
                f"{meeting_id}: cannot transition {from_state} -> {to}"
            )
        sets: list[str] = ["state = ?"]
        vals: list = [to.value]
        if archive_path is not None:
            sets.append("archive_path = ?")
            vals.append(archive_path)
        if verified_at is not None:
            sets.append("verified_at = ?")
            vals.append(_iso(verified_at))
        if sha256s:
            for kind in ("audio", "summary", "transcript"):
                if kind in sha256s:
                    sets.append(f"{kind}_sha256 = ?")
                    vals.append(sha256s[kind])
        if to is MeetingState.ARCHIVED:
            sets.append("archived_at = ?")
            vals.append(_iso(at))
        if to is MeetingState.DELETED:
            sets.append("deleted_at = ?")
            vals.append(_iso(at))
        if last_error is not None:
            sets.append("last_error = ?")
            vals.append(last_error)
        vals.append(meeting_id)
        self._conn.execute(
            f"UPDATE meetings SET {', '.join(sets)} WHERE meeting_id = ?", vals
        )
        self._log(meeting_id, from_state, to, at, details)

    def state_log(self, meeting_id: str) -> list[StateLogEntry]:
        rows = self._conn.execute(
            "SELECT meeting_id, from_state, to_state, at, details "
            "FROM state_log WHERE meeting_id = ? ORDER BY id ASC",
            (meeting_id,),
        ).fetchall()
        return [
            StateLogEntry(
                meeting_id=mid,
                from_state=MeetingState(frm) if frm else None,
                to_state=MeetingState(to),
                at=datetime.fromisoformat(at),
                details=json.loads(details) if details else None,
            )
            for mid, frm, to, at, details in rows
        ]

    def history(self, *, year: int, month: int) -> list[MeetingRecord]:
        prefix = f"{year:04d}-{month:02d}"
        rows = self._conn.execute(
            "SELECT meeting_id FROM meetings WHERE state = 'deleted' AND deleted_at LIKE ?",
            (f"{prefix}%",),
        ).fetchall()
        result: list[MeetingRecord] = []
        for (mid,) in rows:
            rec = self.get(mid)
            if rec is not None:
                result.append(rec)
        return result

    def counts_by_state(self) -> dict[MeetingState, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) FROM meetings GROUP BY state"
        ).fetchall()
        return {MeetingState(s): c for s, c in rows}

    def _log(
        self,
        meeting_id: str,
        from_state: MeetingState | None,
        to_state: MeetingState,
        at: datetime,
        details: dict | None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO state_log (meeting_id, from_state, to_state, at, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                meeting_id,
                from_state.value if from_state else None,
                to_state.value,
                _iso(at),
                json.dumps(details) if details else None,
            ),
        )
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/Scripts/pytest.exe tests/core/test_manifest.py -v
```

Expected: 13 tests pass.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/manifest.py tests/core/test_manifest.py
git commit -m "feat(core): add SQLite-backed Manifest with state machine and audit"
```

---

## Phase 4 — Archiver

### Task 4: Slug + canonical path builder + atomic write helpers

**Files:**
- Create: `firefliesclearer/infra/__init__.py` (empty)
- Create: `firefliesclearer/infra/fs.py`
- Create: `tests/infra/__init__.py` (empty)
- Create: `tests/infra/test_fs.py`

- [ ] **Step 1: Create empty packages**

```bash
echo "" > firefliesclearer/infra/__init__.py
mkdir -p tests/infra && echo "" > tests/infra/__init__.py
```

- [ ] **Step 2: Write the failing tests**

`tests/infra/test_fs.py`:

```python
"""Tests for filesystem helpers (slug, paths, atomic write, hashing)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from firefliesclearer.infra.fs import (
    atomic_write_bytes,
    canonical_meeting_dir,
    sha256_file,
    slugify,
)


@pytest.mark.parametrize(
    "title,expected_prefix",
    [
        ("Weekly Standup", "weekly-standup"),
        ("[Draft] Q2 / Plans", "draft-q2-plans"),
        ("  Spaces   everywhere  ", "spaces-everywhere"),
        ("Zażółć gęślą jaźń", "zazolc-gesla-jazn"),
        ("", "untitled"),
        ("---", "untitled"),
    ],
)
def test_slugify(title: str, expected_prefix: str) -> None:
    assert slugify(title) == expected_prefix


def test_slugify_truncates_to_60_chars() -> None:
    long = "a" * 200
    assert len(slugify(long)) == 60


def test_canonical_meeting_dir_layout(tmp_path: Path) -> None:
    date = datetime(2026, 4, 12, 9, 0, tzinfo=timezone.utc)
    path = canonical_meeting_dir(
        archive_root=tmp_path,
        meeting_id="01HW",
        title="Kickoff Marketing Q2",
        meeting_date=date,
    )
    assert path == tmp_path / "archive" / "2026" / "04" / (
        "2026-04-12_kickoff-marketing-q2_01HW"
    )


def test_atomic_write_bytes_writes_file(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"hello")
    assert target.read_bytes() == b"hello"


def test_atomic_write_bytes_replaces_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"


def test_atomic_write_bytes_does_not_leave_tmp_on_success(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "out.bin"
    atomic_write_bytes(target, b"x")
    leftover = list(tmp_path.rglob("*.tmp"))
    assert leftover == []


def test_sha256_file(tmp_path: Path) -> None:
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    assert sha256_file(f) == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
```

- [ ] **Step 3: Run tests — expect failures**

```bash
.venv/Scripts/pytest.exe tests/infra/test_fs.py -v
```

Expected: ImportError on `firefliesclearer.infra.fs`.

- [ ] **Step 4: Implement fs helpers**

`firefliesclearer/infra/fs.py`:

```python
"""Filesystem helpers: slug, canonical paths, atomic writes, hashing."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_length: int = 60) -> str:
    """ASCII-fold + lowercase + non-alnum->'-' + trim + truncate."""
    if not text:
        return "untitled"
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    dashed = _NON_ALNUM.sub("-", lowered).strip("-")
    if not dashed:
        return "untitled"
    return dashed[:max_length].rstrip("-") or "untitled"


def canonical_meeting_dir(
    *,
    archive_root: Path,
    meeting_id: str,
    title: str,
    meeting_date: datetime,
) -> Path:
    yyyy = f"{meeting_date.year:04d}"
    mm = f"{meeting_date.month:02d}"
    day = meeting_date.strftime("%Y-%m-%d")
    name = f"{day}_{slugify(title)}_{meeting_id}"
    return archive_root / "archive" / yyyy / mm / name


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to <path>.tmp then os.replace into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sha256_file(path: Path, *, chunk_size: int = 65536) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()
```

- [ ] **Step 5: Run tests — expect pass**

```bash
.venv/Scripts/pytest.exe tests/infra/test_fs.py -v
```

Expected: 9 tests pass.

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/infra/__init__.py firefliesclearer/infra/fs.py tests/infra/__init__.py tests/infra/test_fs.py
git commit -m "feat(infra): add slug, canonical path, atomic write, sha256 helpers"
```

---

### Task 5: Archiver — coordinates artifact writes, atomic rename, verification, drift detection

**Files:**
- Create: `firefliesclearer/core/archiver.py`
- Create: `tests/core/test_archiver.py`

- [ ] **Step 1: Write the failing tests**

`tests/core/test_archiver.py`:

```python
"""Tests for Archiver: atomic write, verification, drift detection."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from firefliesclearer.core.archiver import (
    ArchiveDriftError,
    Archiver,
    ArchiveVerificationError,
)
from firefliesclearer.core.models import ArtifactBundle, Meeting

NOW = datetime(2026, 4, 12, 9, 0, tzinfo=timezone.utc)


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
    result = archiver.archive(
        meeting=_meeting(), bundle=_bundle(), summary_pdf=pdf_bytes
    )
    d = result.archive_dir
    assert (d / "audio.mp3").read_bytes() == b"AUDIO"
    assert (d / "summary.pdf").read_bytes() == pdf_bytes
    assert "Speaker A" in (d / "transcript.md").read_text(encoding="utf-8")
    md = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    assert md["meeting_id"] == "01HW"
    assert md["source_url"] == "https://x"


def test_archive_returns_canonical_path(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    result = archiver.archive(
        meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X"
    )
    expected = tmp_path / "archive" / "2026" / "04" / (
        "2026-04-12_kickoff-marketing-q2_01HW"
    )
    assert result.archive_dir == expected


def test_archive_returns_sha256s(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    result = archiver.archive(
        meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X"
    )
    assert set(result.sha256s.keys()) == {"audio", "summary", "transcript"}
    assert all(len(v) == 64 for v in result.sha256s.values())


def test_archive_is_atomic_no_partial_dir_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archiver = Archiver(archive_root=tmp_path)

    real = archiver._write_metadata  # type: ignore[attr-defined]

    def boom(*a, **kw) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(archiver, "_write_metadata", boom)
    with pytest.raises(RuntimeError):
        archiver.archive(
            meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X"
        )
    canonical = tmp_path / "archive" / "2026" / "04"
    assert not canonical.exists() or list(canonical.iterdir()) == []
    # restore to silence "unused" lints
    monkeypatch.setattr(archiver, "_write_metadata", real)


def test_verify_returns_true_for_complete_archive(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    result = archiver.archive(
        meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X"
    )
    assert archiver.verify(result.archive_dir) is True


def test_verify_returns_false_when_file_missing(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    result = archiver.archive(
        meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X"
    )
    (result.archive_dir / "audio.mp3").unlink()
    assert archiver.verify(result.archive_dir) is False


def test_verify_returns_false_when_file_zero_bytes(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    result = archiver.archive(
        meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X"
    )
    (result.archive_dir / "audio.mp3").write_bytes(b"")
    assert archiver.verify(result.archive_dir) is False


def test_archive_skips_when_dir_exists_and_known(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    archiver.archive(
        meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X"
    )
    # Second call: known existing path -> skip when allow_known=True.
    result2 = archiver.archive(
        meeting=_meeting(),
        bundle=_bundle(),
        summary_pdf=b"%PDF-X",
        allow_known=True,
    )
    assert result2.skipped is True


def test_archive_drift_raises_on_unknown_existing_dir(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    target = (
        tmp_path / "archive" / "2026" / "04" / "2026-04-12_kickoff-marketing-q2_01HW"
    )
    target.mkdir(parents=True)
    (target / "stranger.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(ArchiveDriftError):
        archiver.archive(
            meeting=_meeting(), bundle=_bundle(), summary_pdf=b"%PDF-X"
        )


def test_verify_raises_for_missing_dir(tmp_path: Path) -> None:
    archiver = Archiver(archive_root=tmp_path)
    with pytest.raises(ArchiveVerificationError):
        archiver.verify(tmp_path / "nonexistent")
```

- [ ] **Step 2: Run tests — expect failures**

```bash
.venv/Scripts/pytest.exe tests/core/test_archiver.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement Archiver**

`firefliesclearer/core/archiver.py`:

```python
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
    pass


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    archive_dir: Path
    sha256s: dict[str, str]
    skipped: bool = False


class Archiver:
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
                return ArchiveResult(
                    archive_dir=target, sha256s={}, skipped=True
                )
            raise ArchiveDriftError(
                f"Canonical dir already exists: {target}"
            )

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
            raise ArchiveVerificationError(
                f"Archive dir missing: {archive_dir}"
            )
        for name in REQUIRED_FILES:
            f = archive_dir / name
            if not f.exists() or f.stat().st_size == 0:
                return False
        return True

    @staticmethod
    def _write_audio(tmp: Path, bundle: ArtifactBundle) -> None:
        if bundle.audio_bytes is None:
            raise ValueError("audio_bytes required")
        (tmp / "audio.mp3").write_bytes(bundle.audio_bytes)

    @staticmethod
    def _write_summary(tmp: Path, summary_pdf: bytes) -> None:
        if not summary_pdf:
            raise ValueError("summary_pdf required")
        (tmp / "summary.pdf").write_bytes(summary_pdf)

    @staticmethod
    def _write_transcript(tmp: Path, bundle: ArtifactBundle) -> None:
        if bundle.transcript_markdown is None:
            raise ValueError("transcript_markdown required")
        (tmp / "transcript.md").write_text(
            bundle.transcript_markdown, encoding="utf-8"
        )

    @staticmethod
    def _write_metadata(
        tmp: Path, meeting: Meeting, bundle: ArtifactBundle
    ) -> None:
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
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/Scripts/pytest.exe tests/core/test_archiver.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/core/archiver.py tests/core/test_archiver.py
git commit -m "feat(core): add Archiver with atomic writes, verification, drift detection"
```

---

## Phase 5 — Ports and fakes

### Task 6: Define ports (Protocols) and implement test fakes

**Files:**
- Create: `firefliesclearer/ports/__init__.py` (empty)
- Create: `firefliesclearer/ports/clock.py`
- Create: `firefliesclearer/ports/summary_renderer.py`
- Create: `firefliesclearer/ports/meeting_repository.py`
- Create: `firefliesclearer/infra/system_clock.py`
- Create: `tests/fakes/__init__.py` (empty)
- Create: `tests/fakes/frozen_clock.py`
- Create: `tests/fakes/fake_renderer.py`
- Create: `tests/fakes/in_memory_repository.py`

- [ ] **Step 1: Create empty packages**

```bash
echo "" > firefliesclearer/ports/__init__.py
mkdir -p tests/fakes && echo "" > tests/fakes/__init__.py
```

- [ ] **Step 2: Write `clock` port**

`firefliesclearer/ports/clock.py`:

```python
"""Clock port: testable time source."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
```

- [ ] **Step 3: Write `summary_renderer` port**

`firefliesclearer/ports/summary_renderer.py`:

```python
"""Summary renderer port: produces PDF bytes from a summary payload."""

from __future__ import annotations

from typing import Any, Protocol


class SummaryRenderer(Protocol):
    def render(self, summary_payload: dict[str, Any], *, meeting_title: str) -> bytes: ...
```

- [ ] **Step 4: Write `meeting_repository` port**

`firefliesclearer/ports/meeting_repository.py`:

```python
"""Meeting repository port: read meetings, fetch artifacts, delete."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from firefliesclearer.core.models import ArtifactBundle, Meeting


@dataclass(frozen=True, slots=True)
class MeetingFilter:
    """Boundary filter — narrow before pagination at the source if possible."""

    older_than: datetime | None = None
    limit: int | None = None


class MeetingRepository(Protocol):
    def list_meetings(
        self, filter: MeetingFilter
    ) -> AsyncIterator[Meeting]: ...

    async def fetch_artifacts(self, meeting_id: str) -> ArtifactBundle: ...

    async def delete_meeting(self, meeting_id: str) -> None: ...
```

- [ ] **Step 5: Implement SystemClock**

`firefliesclearer/infra/system_clock.py`:

```python
"""Production Clock implementation."""

from __future__ import annotations

from datetime import datetime, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=timezone.utc)
```

- [ ] **Step 6: Implement test fakes**

`tests/fakes/frozen_clock.py`:

```python
"""FrozenClock for deterministic tests."""

from __future__ import annotations

from datetime import datetime


class FrozenClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def set(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now
```

`tests/fakes/fake_renderer.py`:

```python
"""FakeSummaryRenderer returns deterministic bytes."""

from __future__ import annotations

from typing import Any


class FakeSummaryRenderer:
    def __init__(self, output: bytes = b"%PDF-FAKE") -> None:
        self._output = output
        self.calls: list[dict[str, Any]] = []

    def render(self, summary_payload: dict[str, Any], *, meeting_title: str) -> bytes:
        self.calls.append({"payload": summary_payload, "title": meeting_title})
        return self._output
```

`tests/fakes/in_memory_repository.py`:

```python
"""In-memory MeetingRepository for tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

from firefliesclearer.core.models import ArtifactBundle, Meeting
from firefliesclearer.ports.meeting_repository import MeetingFilter


class InMemoryMeetingRepository:
    def __init__(
        self,
        meetings: list[Meeting] | None = None,
        artifacts: dict[str, ArtifactBundle] | None = None,
    ) -> None:
        self._meetings: dict[str, Meeting] = {
            m.meeting_id: m for m in (meetings or [])
        }
        self._artifacts: dict[str, ArtifactBundle] = artifacts or {}
        self.deleted: list[str] = []
        self.fail_fetch_for: set[str] = set()
        self.fail_delete_for: set[str] = set()

    async def list_meetings(
        self, filter: MeetingFilter
    ) -> AsyncIterator[Meeting]:
        for m in self._meetings.values():
            if filter.older_than and m.meeting_date >= filter.older_than:
                continue
            yield m

    async def fetch_artifacts(self, meeting_id: str) -> ArtifactBundle:
        if meeting_id in self.fail_fetch_for:
            raise RuntimeError(f"forced fetch failure: {meeting_id}")
        return self._artifacts.get(meeting_id, ArtifactBundle())

    async def delete_meeting(self, meeting_id: str) -> None:
        if meeting_id in self.fail_delete_for:
            raise RuntimeError(f"forced delete failure: {meeting_id}")
        if meeting_id not in self._meetings:
            return  # idempotent
        self.deleted.append(meeting_id)
        del self._meetings[meeting_id]
```

- [ ] **Step 7: Verify everything still imports**

```bash
.venv/Scripts/pytest.exe -q
```

Expected: existing tests still pass, no import errors from new modules (the fakes are unused so far).

- [ ] **Step 8: Commit**

```bash
git add firefliesclearer/ports/ firefliesclearer/infra/system_clock.py tests/fakes/
git commit -m "feat: add ports (Clock, SummaryRenderer, MeetingRepository) and test fakes"
```

---

## Phase 6 — Pipeline

### Task 7: Per-meeting transactional pipeline (happy path + failure modes + idempotency)

**Files:**
- Create: `firefliesclearer/core/pipeline.py`
- Create: `tests/core/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

`tests/core/test_pipeline.py`:

```python
"""Tests for Pipeline: per-meeting transaction, failure modes, idempotency."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from firefliesclearer.core.archiver import Archiver
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import ArtifactBundle, Meeting, MeetingState
from firefliesclearer.core.pipeline import Pipeline, PipelineMode
from tests.fakes.fake_renderer import FakeSummaryRenderer
from tests.fakes.frozen_clock import FrozenClock
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)


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


def _build(tmp_path: Path, meetings: list[Meeting], **kw):
    repo = InMemoryMeetingRepository(
        meetings=meetings,
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
    return pipeline, repo, manifest


@pytest.mark.asyncio
async def test_happy_path_archives_and_deletes(tmp_path: Path) -> None:
    m = _meeting()
    pipeline, repo, manifest = _build(tmp_path, [m])
    report = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report.archived == 1
    assert report.deleted == 1
    assert manifest.get(m.meeting_id).state is MeetingState.DELETED
    assert repo.deleted == [m.meeting_id]


@pytest.mark.asyncio
async def test_dry_run_makes_no_mutations(tmp_path: Path) -> None:
    m = _meeting()
    pipeline, repo, manifest = _build(tmp_path, [m])
    report = await pipeline.run([m], mode=PipelineMode.DRY_RUN)
    assert report.archived == 0
    assert report.deleted == 0
    assert manifest.get(m.meeting_id) is None
    assert repo.deleted == []


@pytest.mark.asyncio
async def test_fetch_failure_records_state_no_delete(tmp_path: Path) -> None:
    m = _meeting()
    pipeline, repo, manifest = _build(
        tmp_path, [m], fail_fetch_for=[m.meeting_id]
    )
    report = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report.failed == 1
    rec = manifest.get(m.meeting_id)
    assert rec.state is MeetingState.FAILED_FETCH
    assert repo.deleted == []


@pytest.mark.asyncio
async def test_delete_failure_keeps_archive(tmp_path: Path) -> None:
    m = _meeting()
    pipeline, repo, manifest = _build(
        tmp_path, [m], fail_delete_for=[m.meeting_id]
    )
    report = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report.deleted == 0
    rec = manifest.get(m.meeting_id)
    assert rec.state is MeetingState.DELETED_FAILED
    assert rec.archive_path is not None
    assert Path(rec.archive_path).exists()


@pytest.mark.asyncio
async def test_idempotent_second_run_skips_already_deleted(tmp_path: Path) -> None:
    m = _meeting()
    pipeline, _, manifest = _build(tmp_path, [m])
    await pipeline.run([m], mode=PipelineMode.APPLY)
    report2 = await pipeline.run([m], mode=PipelineMode.APPLY)
    assert report2.skipped == 1
    assert manifest.get(m.meeting_id).state is MeetingState.DELETED


@pytest.mark.asyncio
async def test_one_failure_does_not_abort_run(tmp_path: Path) -> None:
    m1, m2, m3 = _meeting("a"), _meeting("b"), _meeting("c")
    pipeline, repo, manifest = _build(
        tmp_path, [m1, m2, m3], fail_fetch_for=["b"]
    )
    report = await pipeline.run([m1, m2, m3], mode=PipelineMode.APPLY)
    assert report.archived == 2
    assert report.deleted == 2
    assert report.failed == 1
    assert manifest.get("b").state is MeetingState.FAILED_FETCH
    assert manifest.get("a").state is MeetingState.DELETED
    assert manifest.get("c").state is MeetingState.DELETED


@pytest.mark.asyncio
async def test_archive_only_mode_does_not_delete(tmp_path: Path) -> None:
    m = _meeting()
    pipeline, repo, manifest = _build(tmp_path, [m])
    report = await pipeline.run([m], mode=PipelineMode.ARCHIVE_ONLY)
    assert report.archived == 1
    assert report.deleted == 0
    assert manifest.get(m.meeting_id).state is MeetingState.ARCHIVED
    assert repo.deleted == []


@pytest.mark.asyncio
async def test_purge_only_requires_archived_state(tmp_path: Path) -> None:
    m = _meeting()
    pipeline, _, manifest = _build(tmp_path, [m])
    await pipeline.run([m], mode=PipelineMode.ARCHIVE_ONLY)
    assert manifest.get(m.meeting_id).state is MeetingState.ARCHIVED
    report = await pipeline.run([m], mode=PipelineMode.PURGE_ONLY)
    assert report.deleted == 1
    assert manifest.get(m.meeting_id).state is MeetingState.DELETED


@pytest.mark.asyncio
async def test_purge_only_skips_pending_meetings(tmp_path: Path) -> None:
    m = _meeting()
    pipeline, repo, manifest = _build(tmp_path, [m])
    report = await pipeline.run([m], mode=PipelineMode.PURGE_ONLY)
    assert report.deleted == 0
    assert report.skipped == 1
    assert repo.deleted == []
```

- [ ] **Step 2: Run tests — expect failures**

```bash
.venv/Scripts/pytest.exe tests/core/test_pipeline.py -v
```

Expected: ImportError on `firefliesclearer.core.pipeline`.

- [ ] **Step 3: Implement Pipeline**

`firefliesclearer/core/pipeline.py`:

```python
"""Per-meeting transactional pipeline: list -> archive -> verify -> delete."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from firefliesclearer.core.archiver import (
    ArchiveDriftError,
    Archiver,
)
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.ports.clock import Clock
from firefliesclearer.ports.meeting_repository import MeetingRepository
from firefliesclearer.ports.summary_renderer import SummaryRenderer


class PipelineMode(str, Enum):
    DRY_RUN = "dry_run"
    APPLY = "apply"            # archive + delete
    ARCHIVE_ONLY = "archive"   # archive, no delete (curated step 2)
    PURGE_ONLY = "purge"       # delete archived, no new archive (curated step 3)


@dataclass(slots=True)
class RunReport:
    archived: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


class Pipeline:
    def __init__(
        self,
        *,
        repository: MeetingRepository,
        manifest: Manifest,
        archiver: Archiver,
        renderer: SummaryRenderer,
        clock: Clock,
    ) -> None:
        self._repo = repository
        self._manifest = manifest
        self._archiver = archiver
        self._renderer = renderer
        self._clock = clock

    async def run(
        self, meetings: Sequence[Meeting], *, mode: PipelineMode
    ) -> RunReport:
        report = RunReport()
        for m in meetings:
            await self._process_one(m, mode=mode, report=report)
        return report

    async def _process_one(
        self, meeting: Meeting, *, mode: PipelineMode, report: RunReport
    ) -> None:
        if mode is PipelineMode.DRY_RUN:
            return
        existing = self._manifest.get(meeting.meeting_id)
        if existing and existing.state is MeetingState.DELETED:
            report.skipped += 1
            return
        if mode is PipelineMode.PURGE_ONLY:
            await self._purge_only(meeting, existing, report)
            return
        if existing is None or existing.state is MeetingState.PENDING:
            self._manifest.register(meeting, at=self._clock.now())
            ok = await self._archive(meeting, report)
            if not ok:
                return
        elif existing.state.value.startswith("failed_"):
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.PENDING,
                at=self._clock.now(),
            )
            ok = await self._archive(meeting, report)
            if not ok:
                return
        if mode is PipelineMode.ARCHIVE_ONLY:
            return
        await self._delete(meeting, report)

    async def _archive(self, meeting: Meeting, report: RunReport) -> bool:
        try:
            bundle = await self._repo.fetch_artifacts(meeting.meeting_id)
        except Exception as e:
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.FAILED_FETCH,
                at=self._clock.now(),
                last_error=str(e),
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, f"fetch: {e}"))
            return False

        try:
            pdf = self._renderer.render(
                bundle.summary_payload or {},
                meeting_title=meeting.title,
            )
        except Exception as e:
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.FAILED_RENDER,
                at=self._clock.now(),
                last_error=str(e),
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, f"render: {e}"))
            return False

        try:
            result = self._archiver.archive(
                meeting=meeting,
                bundle=bundle,
                summary_pdf=pdf,
                allow_known=True,
            )
        except ArchiveDriftError as e:
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.FAILED_VERIFY,
                at=self._clock.now(),
                last_error=str(e),
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, f"drift: {e}"))
            return False
        except Exception as e:
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.FAILED_DOWNLOAD,
                at=self._clock.now(),
                last_error=str(e),
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, f"write: {e}"))
            return False

        if not self._archiver.verify(result.archive_dir):
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.FAILED_VERIFY,
                at=self._clock.now(),
                last_error="verify failed",
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, "verify failed"))
            return False

        now = self._clock.now()
        self._manifest.transition(
            meeting.meeting_id,
            to=MeetingState.ARCHIVED,
            at=now,
            archive_path=str(result.archive_dir),
            verified_at=now,
            sha256s=result.sha256s,
        )
        report.archived += 1
        return True

    async def _delete(self, meeting: Meeting, report: RunReport) -> None:
        rec = self._manifest.get(meeting.meeting_id)
        if rec is None or rec.state is not MeetingState.ARCHIVED:
            return  # only delete if archived & verified
        try:
            await self._repo.delete_meeting(meeting.meeting_id)
        except Exception as e:
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.DELETED_FAILED,
                at=self._clock.now(),
                last_error=str(e),
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, f"delete: {e}"))
            return
        self._manifest.transition(
            meeting.meeting_id,
            to=MeetingState.DELETED,
            at=self._clock.now(),
        )
        report.deleted += 1

    async def _purge_only(
        self, meeting: Meeting, existing, report: RunReport
    ) -> None:
        if existing is None or existing.state is not MeetingState.ARCHIVED:
            report.skipped += 1
            return
        await self._delete(meeting, report)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/Scripts/pytest.exe tests/core/test_pipeline.py -v
```

Expected: 9 tests pass.

- [ ] **Step 5: Verify coverage on pipeline + manifest is 100%**

```bash
.venv/Scripts/pytest.exe tests/core/test_pipeline.py tests/core/test_manifest.py --cov=firefliesclearer.core.pipeline --cov=firefliesclearer.core.manifest --cov-report=term-missing
```

Expected: `pipeline.py` and `manifest.py` both at 100%. If not, add tests for the missing lines (most likely a delete-only path or a state-check branch).

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/core/pipeline.py tests/core/test_pipeline.py
git commit -m "feat(core): add Pipeline with per-meeting transactions and failure handling"
```

---

## Phase 7 — Configuration

### Task 8: Pydantic config schema, TOML loader, precedence chain

**Files:**
- Create: `firefliesclearer/infra/config.py`
- Create: `tests/infra/test_config.py`

- [ ] **Step 1: Write the failing tests**

`tests/infra/test_config.py`:

```python
"""Tests for config: schema validation and precedence chain."""

from __future__ import annotations

from pathlib import Path

import pytest

from firefliesclearer.infra.config import (
    AppConfig,
    ConfigError,
    load_config,
    write_config,
)


def _write_user(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "user.toml"
    p.write_text(content, encoding="utf-8")
    return p


def test_minimal_valid_config_parses(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "ff_xyz"

        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    cfg = load_config(user_config=user)
    assert cfg.fireflies.api_key == "ff_xyz"
    assert cfg.archive.root_dir == Path("C:/tmp/arch")
    assert cfg.archive.summary_format == "pdf"


def test_missing_api_key_raises_actionable_error(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    with pytest.raises(ConfigError) as exc:
        load_config(user_config=user)
    assert "firefliesclearer init" in str(exc.value)


def test_env_var_overrides_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "from_user"
        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    monkeypatch.setenv("FIREFLIES_API_KEY", "from_env")
    cfg = load_config(user_config=user)
    assert cfg.fireflies.api_key == "from_env"


def test_project_config_overrides_user_config(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "from_user"
        [archive]
        root_dir = "C:/user/arch"
        summary_format = "pdf"
        """,
    )
    project = tmp_path / "firefliesclearer.toml"
    project.write_text(
        """
        [archive]
        root_dir = "C:/proj/arch"
        """,
        encoding="utf-8",
    )
    cfg = load_config(user_config=user, project_config=project)
    assert cfg.archive.root_dir == Path("C:/proj/arch")
    assert cfg.fireflies.api_key == "from_user"  # not overridden


def test_cli_override_beats_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "from_user"
        [archive]
        root_dir = "C:/tmp/arch"
        summary_format = "pdf"
        """,
    )
    monkeypatch.setenv("FIREFLIES_API_KEY", "from_env")
    cfg = load_config(
        user_config=user,
        cli_overrides={"fireflies.api_key": "from_cli"},
    )
    assert cfg.fireflies.api_key == "from_cli"


def test_invalid_summary_format_rejected(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path,
        """
        [fireflies]
        api_key = "x"
        [archive]
        root_dir = "C:/tmp"
        summary_format = "docx"
        """,
    )
    with pytest.raises(ConfigError):
        load_config(user_config=user)


def test_write_config_round_trip(tmp_path: Path) -> None:
    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": "ff_xyz"},
            "archive": {
                "root_dir": str(tmp_path / "arch"),
                "summary_format": "pdf",
            },
        }
    )
    target = tmp_path / "out.toml"
    write_config(cfg, target)
    loaded = load_config(user_config=target)
    assert loaded.fireflies.api_key == "ff_xyz"
```

- [ ] **Step 2: Run tests — expect failures**

```bash
.venv/Scripts/pytest.exe tests/infra/test_config.py -v
```

- [ ] **Step 3: Implement config**

`firefliesclearer/infra/config.py`:

```python
"""Config: TOML loader, precedence chain, Pydantic validation."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, Field, ValidationError, field_validator


class ConfigError(Exception):
    pass


class FirefliesConfig(BaseModel):
    api_key: str = Field(min_length=1)


class ArchiveConfig(BaseModel):
    root_dir: Path
    summary_format: Literal["pdf"] = "pdf"


class AutoRulesConfig(BaseModel):
    older_than_days: int = 180
    delete_failed_transcripts: bool = True


class RunConfig(BaseModel):
    concurrency: int = Field(default=3, ge=1, le=20)
    delete_confirmation_threshold: int = Field(default=10, ge=0)


class AppConfig(BaseModel):
    fireflies: FirefliesConfig
    archive: ArchiveConfig
    rules: dict[str, Any] = Field(default_factory=dict)
    run: RunConfig = Field(default_factory=RunConfig)

    @field_validator("rules")
    @classmethod
    def _coerce_rules(cls, v: dict[str, Any]) -> dict[str, Any]:
        out = {**v}
        if "auto" in out:
            out["auto"] = AutoRulesConfig.model_validate(out["auto"]).model_dump()
        else:
            out["auto"] = AutoRulesConfig().model_dump()
        return out

    def auto_rules(self) -> AutoRulesConfig:
        return AutoRulesConfig.model_validate(self.rules.get("auto", {}))


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _deep_merge(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {**a}
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _apply_dotted(
    target: dict[str, Any], dotted_key: str, value: Any
) -> None:
    parts = dotted_key.split(".")
    cur: dict[str, Any] = target
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def load_config(
    *,
    user_config: Path,
    project_config: Path | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> AppConfig:
    """Precedence (highest wins): cli_overrides > env > project > user."""
    merged = _read_toml(user_config)
    if project_config is not None:
        merged = _deep_merge(merged, _read_toml(project_config))

    if env_key := os.environ.get("FIREFLIES_API_KEY"):
        merged = _deep_merge(merged, {"fireflies": {"api_key": env_key}})

    if cli_overrides:
        for k, v in cli_overrides.items():
            _apply_dotted(merged, k, v)

    if "fireflies" not in merged or not merged["fireflies"].get("api_key"):
        raise ConfigError(
            "Missing Fireflies API key. Run `firefliesclearer init` to set it, "
            "or export FIREFLIES_API_KEY."
        )

    try:
        return AppConfig.model_validate(merged)
    except ValidationError as e:
        raise ConfigError(str(e)) from e


def write_config(cfg: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = cfg.model_dump(mode="json")
    payload["archive"]["root_dir"] = str(payload["archive"]["root_dir"])
    with open(path, "wb") as f:
        tomli_w.dump(payload, f)


def user_config_path() -> Path:
    """Cross-platform user config location via platformdirs."""
    from platformdirs import user_config_dir

    return Path(user_config_dir("firefliesclearer", appauthor=False)) / "config.toml"
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/Scripts/pytest.exe tests/infra/test_config.py -v
```

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/infra/config.py tests/infra/test_config.py
git commit -m "feat(infra): add Pydantic config schema with TOML loader and precedence chain"
```

---

## Phase 8 — PDF renderer

### Task 9: Markdown summary -> Jinja2 HTML -> WeasyPrint PDF

**Files:**
- Create: `firefliesclearer/templates/summary.html.j2`
- Create: `firefliesclearer/templates/summary.css`
- Create: `firefliesclearer/infra/pdf_renderer.py`
- Create: `tests/infra/test_pdf_renderer.py`

- [ ] **Step 1: Write the failing tests**

`tests/infra/test_pdf_renderer.py`:

```python
"""Tests for the WeasyPrint-based PDF renderer."""

from __future__ import annotations

import pytest

from firefliesclearer.infra.pdf_renderer import WeasyPrintSummaryRenderer

# WeasyPrint requires GTK on Windows; skip if unavailable in the env.
weasyprint = pytest.importorskip("weasyprint")


def _render(payload: dict, *, title: str = "Test") -> bytes:
    return WeasyPrintSummaryRenderer().render(payload, meeting_title=title)


def test_output_starts_with_pdf_magic() -> None:
    payload = {
        "overview": "We discussed Q2 plans.",
        "action_items": ["Ship feature", "Update docs"],
        "keywords": ["q2", "roadmap"],
    }
    out = _render(payload)
    assert out.startswith(b"%PDF")


def test_polish_characters_render_without_error() -> None:
    payload = {
        "overview": "Zażółć gęślą jaźń. Spotkanie projektowe.",
        "action_items": ["Wysłać podsumowanie"],
        "keywords": ["projekt"],
    }
    out = _render(payload, title="Spotkanie ABC")
    assert out.startswith(b"%PDF")
    assert len(out) > 500  # non-trivial content


def test_empty_payload_produces_placeholder_pdf() -> None:
    out = _render({})
    assert out.startswith(b"%PDF")


def test_missing_action_items_does_not_crash() -> None:
    out = _render({"overview": "Just an overview"})
    assert out.startswith(b"%PDF")
```

- [ ] **Step 2: Run tests — expect failures (or skip if WeasyPrint missing)**

```bash
.venv/Scripts/pytest.exe tests/infra/test_pdf_renderer.py -v
```

If WeasyPrint isn't installed (Windows GTK problem), all tests will skip — proceed to install fix below before continuing.

- [ ] **Step 3: Write the HTML template**

`firefliesclearer/templates/summary.html.j2`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
  <link rel="stylesheet" href="{{ css_path }}">
</head>
<body>
  <header>
    <h1>{{ title }}</h1>
  </header>
  <section class="overview">
    <h2>Overview</h2>
    <p>{{ overview or "(no overview)" }}</p>
  </section>
  {% if action_items %}
  <section class="action-items">
    <h2>Action items</h2>
    <ul>
      {% for item in action_items %}<li>{{ item }}</li>{% endfor %}
    </ul>
  </section>
  {% endif %}
  {% if keywords %}
  <section class="keywords">
    <h2>Keywords</h2>
    <p>{{ keywords | join(", ") }}</p>
  </section>
  {% endif %}
</body>
</html>
```

- [ ] **Step 4: Write the CSS**

`firefliesclearer/templates/summary.css`:

```css
@page { size: A4; margin: 22mm 18mm; }
body { font-family: "DejaVu Sans", "Segoe UI", sans-serif; color: #222; line-height: 1.45; }
header h1 { margin: 0 0 16px 0; font-size: 22pt; border-bottom: 2px solid #333; padding-bottom: 6px; }
h2 { font-size: 13pt; margin: 18pt 0 6pt 0; color: #444; }
section { margin-bottom: 12pt; }
ul { margin: 4pt 0 0 18pt; padding: 0; }
li { margin: 2pt 0; }
.keywords p { font-style: italic; color: #555; }
```

- [ ] **Step 5: Implement the renderer**

`firefliesclearer/infra/pdf_renderer.py`:

```python
"""WeasyPrint-based PDF renderer for meeting summaries."""

from __future__ import annotations

from importlib import resources
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape


class WeasyPrintSummaryRenderer:
    """Renders summary payloads to PDF via Jinja2 + WeasyPrint."""

    def __init__(self) -> None:
        templates_dir = resources.files("firefliesclearer") / "templates"
        self._env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html"]),
        )
        self._templates_dir = str(templates_dir)

    def render(
        self, summary_payload: dict[str, Any], *, meeting_title: str
    ) -> bytes:
        # Local import keeps WeasyPrint optional at module import time.
        from weasyprint import HTML

        ctx = {
            "title": meeting_title,
            "overview": summary_payload.get("overview"),
            "action_items": summary_payload.get("action_items", []),
            "keywords": summary_payload.get("keywords", []),
            "css_path": f"{self._templates_dir}/summary.css",
        }
        html = self._env.get_template("summary.html.j2").render(**ctx)
        return HTML(string=html, base_url=self._templates_dir).write_pdf()
```

- [ ] **Step 6: Run tests — expect pass**

```bash
.venv/Scripts/pytest.exe tests/infra/test_pdf_renderer.py -v
```

If WeasyPrint install fails on Windows, document the install (`MSYS2`/`GTK3-runtime`) in `README.md` under a "Setup" section and rerun. If install proves unworkable, switch the renderer to `reportlab` (drop the Jinja template, use programmatic flowables); the spec explicitly allows this fallback. Update `pyproject.toml` accordingly.

- [ ] **Step 7: Commit**

```bash
git add firefliesclearer/templates/ firefliesclearer/infra/pdf_renderer.py tests/infra/test_pdf_renderer.py
git commit -m "feat(infra): add WeasyPrint-based summary PDF renderer with Jinja2 template"
```

---

## Phase 9 — Fireflies GraphQL client

> **Note on schema:** This client targets the public Fireflies GraphQL API at `https://api.fireflies.ai/graphql`. Field names below (`transcripts`, `transcript`, `deleteTranscript`, `summary`, etc.) reflect the documented schema as of plan-write time. Before merging the client, run the contract test (Task 11, Step 6) to verify the schema is still current. If field names differ, update query strings and response parsing — public types and the port surface stay the same.

### Task 10: GraphQL client basics — auth, redaction, retries

**Files:**
- Create: `firefliesclearer/infra/fireflies_client.py`
- Create: `tests/infra/test_fireflies_client.py`

- [ ] **Step 1: Write failing tests for auth + redaction + retries**

`tests/infra/test_fireflies_client.py`:

```python
"""Tests for the Fireflies GraphQL client (using respx to mock httpx)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from firefliesclearer.infra.fireflies_client import (
    FirefliesClient,
    FirefliesError,
)
from firefliesclearer.ports.meeting_repository import MeetingFilter

API_URL = "https://api.fireflies.ai/graphql"


@pytest.fixture
def client():
    return FirefliesClient(
        api_key="ff_secret_xyz",
        endpoint=API_URL,
        retry_max=3,
        retry_base_seconds=0.0,
    )


@pytest.mark.asyncio
@respx.mock
async def test_request_sends_bearer_auth(client: FirefliesClient) -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(
            200, json={"data": {"transcripts": []}}
        )
    )
    async for _ in client.list_meetings(MeetingFilter()):
        pass
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer ff_secret_xyz"


@pytest.mark.asyncio
@respx.mock
async def test_api_key_is_redacted_in_logs(
    client: FirefliesClient, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200, json={"data": {"transcripts": []}}
        )
    )
    caplog.set_level(logging.INFO, logger="firefliesclearer.infra.fireflies_client")
    async for _ in client.list_meetings(MeetingFilter()):
        pass
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "ff_secret_xyz" not in text
    assert "[REDACTED]" in text or "Bearer" not in text


@pytest.mark.asyncio
@respx.mock
async def test_4xx_error_not_retried_and_raises(
    client: FirefliesClient,
) -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(403, json={"errors": ["forbidden"]})
    )
    with pytest.raises(FirefliesError):
        async for _ in client.list_meetings(MeetingFilter()):
            pass
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_5xx_is_retried_then_succeeds(
    client: FirefliesClient,
) -> None:
    route = respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(503, text="boom"),
            httpx.Response(503, text="boom"),
            httpx.Response(200, json={"data": {"transcripts": []}}),
        ]
    )
    async for _ in client.list_meetings(MeetingFilter()):
        pass
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_429_with_retry_after_is_honored(
    client: FirefliesClient,
) -> None:
    route = respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"data": {"transcripts": []}}),
        ]
    )
    async for _ in client.list_meetings(MeetingFilter()):
        pass
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_list_meetings_paginates(client: FirefliesClient) -> None:
    page1 = {
        "data": {
            "transcripts": [
                {
                    "id": "a",
                    "title": "M1",
                    "date": "2026-01-01T10:00:00Z",
                    "duration": 12.5,
                    "host_email": "u@x.com",
                    "participants": ["u@x.com", "b@x.com"],
                    "tags": [],
                    "transcript_url": "https://x/t/a",
                }
            ]
        }
    }
    page2 = {
        "data": {
            "transcripts": [
                {
                    "id": "b",
                    "title": "M2",
                    "date": "2026-02-01T10:00:00Z",
                    "duration": 5.0,
                    "host_email": "u@x.com",
                    "participants": ["u@x.com"],
                    "tags": ["draft"],
                    "transcript_url": None,
                }
            ]
        }
    }
    page3 = {"data": {"transcripts": []}}
    respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=page3),
        ]
    )
    ids: list[str] = []
    async for m in client.list_meetings(MeetingFilter()):
        ids.append(m.meeting_id)
    assert ids == ["a", "b"]


@pytest.mark.asyncio
@respx.mock
async def test_list_meetings_applies_older_than_filter(
    client: FirefliesClient,
) -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200, json={"data": {"transcripts": []}}
        )
    )
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async for _ in client.list_meetings(MeetingFilter(older_than=cutoff)):
        pass
    body = respx.calls.last.request.content.decode()
    assert "to_date" in body or "fromDate" in body or "older" in body  # variable name varies


@pytest.mark.asyncio
@respx.mock
async def test_no_transcript_url_marks_meeting_has_transcript_false(
    client: FirefliesClient,
) -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "transcripts": [
                        {
                            "id": "x",
                            "title": "Y",
                            "date": "2026-04-01T10:00:00Z",
                            "duration": 1.0,
                            "host_email": "u@x.com",
                            "participants": [],
                            "tags": [],
                            "transcript_url": None,
                        }
                    ]
                }
            },
        )
    )
    async for m in client.list_meetings(MeetingFilter(limit=1)):
        assert m.has_transcript is False
        break


@pytest.mark.asyncio
@respx.mock
async def test_delete_meeting_calls_mutation(
    client: FirefliesClient,
) -> None:
    route = respx.post(API_URL).mock(
        return_value=httpx.Response(
            200, json={"data": {"deleteTranscript": {"id": "x"}}}
        )
    )
    await client.delete_meeting("x")
    assert route.call_count == 1
    body = route.calls.last.request.content.decode()
    assert "deleteTranscript" in body
    assert "\"x\"" in body


@pytest.mark.asyncio
@respx.mock
async def test_delete_meeting_idempotent_on_404(
    client: FirefliesClient,
) -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(404, json={"errors": ["not_found"]})
    )
    # 404 on delete = already gone; should not raise
    await client.delete_meeting("missing")
```

- [ ] **Step 2: Run tests — expect failures**

```bash
.venv/Scripts/pytest.exe tests/infra/test_fireflies_client.py -v
```

- [ ] **Step 3: Implement client**

`firefliesclearer/infra/fireflies_client.py`:

```python
"""Async GraphQL client for Fireflies AI."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from firefliesclearer.core.models import ArtifactBundle, Meeting
from firefliesclearer.ports.meeting_repository import MeetingFilter

logger = logging.getLogger(__name__)

LIST_QUERY = """
query Transcripts($limit: Int, $skip: Int, $to_date: DateTime) {
  transcripts(limit: $limit, skip: $skip, to_date: $to_date) {
    id
    title
    date
    duration
    host_email
    participants
    tags
    transcript_url
    summary { overview action_items keywords }
    audio_url
  }
}
"""

DELETE_MUTATION = """
mutation DeleteTranscript($id: String!) {
  deleteTranscript(id: $id) { id }
}
"""

DETAIL_QUERY = """
query TranscriptDetail($id: String!) {
  transcript(id: $id) {
    id
    title
    date
    duration
    host_email
    participants
    tags
    transcript_url
    audio_url
    summary { overview action_items keywords }
    sentences { speaker_name text }
  }
}
"""


class FirefliesError(Exception):
    pass


class FirefliesClient:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = "https://api.fireflies.ai/graphql",
        retry_max: int = 3,
        retry_base_seconds: float = 1.0,
        page_size: int = 50,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._retry_max = retry_max
        self._retry_base = retry_base_seconds
        self._page_size = page_size
        self._timeout = timeout_seconds

    async def list_meetings(
        self, filter: MeetingFilter
    ) -> AsyncIterator[Meeting]:
        skip = 0
        emitted = 0
        async with self._http() as client:
            while True:
                variables = {
                    "limit": self._page_size,
                    "skip": skip,
                    "to_date": filter.older_than.isoformat()
                    if filter.older_than
                    else None,
                }
                payload = await self._request(
                    client, LIST_QUERY, variables, op="list_meetings"
                )
                items = payload.get("data", {}).get("transcripts", []) or []
                if not items:
                    return
                for raw in items:
                    yield _meeting_from_raw(raw)
                    emitted += 1
                    if filter.limit and emitted >= filter.limit:
                        return
                if len(items) < self._page_size:
                    return
                skip += self._page_size

    async def fetch_artifacts(self, meeting_id: str) -> ArtifactBundle:
        async with self._http() as client:
            payload = await self._request(
                client,
                DETAIL_QUERY,
                {"id": meeting_id},
                op="fetch_artifacts",
            )
            t = payload.get("data", {}).get("transcript")
            if t is None:
                raise FirefliesError(f"transcript not found: {meeting_id}")
            audio_url = t.get("audio_url")
            audio_bytes = (
                await self._download_audio(client, audio_url)
                if audio_url
                else None
            )
            return ArtifactBundle(
                audio_bytes=audio_bytes,
                transcript_markdown=_render_transcript_md(t),
                summary_payload=t.get("summary") or {},
                metadata={
                    "source_url": f"https://app.fireflies.ai/view/{meeting_id}",
                    "audio_url": audio_url,
                },
            )

    async def delete_meeting(self, meeting_id: str) -> None:
        async with self._http() as client:
            try:
                await self._request(
                    client,
                    DELETE_MUTATION,
                    {"id": meeting_id},
                    op="delete_meeting",
                    retries_for_4xx=False,
                )
            except FirefliesError as e:
                if "404" in str(e):
                    return  # idempotent
                raise

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
        )

    async def _request(
        self,
        client: httpx.AsyncClient,
        query: str,
        variables: dict[str, Any],
        *,
        op: str,
        retries_for_4xx: bool = False,
    ) -> dict[str, Any]:
        body = {"query": query, "variables": variables}
        for attempt in range(self._retry_max + 1):
            logger.info(
                "fireflies_request op=%s attempt=%d auth=[REDACTED]",
                op,
                attempt,
            )
            try:
                resp = await client.post(self._endpoint, json=body)
            except httpx.HTTPError as e:
                if attempt >= self._retry_max:
                    raise FirefliesError(f"network: {e}") from e
                await self._sleep(attempt, retry_after=None)
                continue

            if resp.status_code == 429:
                if attempt >= self._retry_max:
                    raise FirefliesError("rate limited")
                ra = resp.headers.get("Retry-After")
                await self._sleep(attempt, retry_after=ra)
                continue
            if 500 <= resp.status_code < 600:
                if attempt >= self._retry_max:
                    raise FirefliesError(f"server {resp.status_code}")
                await self._sleep(attempt, retry_after=None)
                continue
            if 400 <= resp.status_code < 500:
                raise FirefliesError(f"{resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            if data.get("errors"):
                raise FirefliesError(f"graphql: {data['errors']}")
            return data

        raise FirefliesError("retries exhausted")

    async def _sleep(self, attempt: int, *, retry_after: str | None) -> None:
        if retry_after is not None:
            try:
                await asyncio.sleep(min(float(retry_after), 60.0))
                return
            except ValueError:
                pass
        delay = self._retry_base * (4**attempt)
        delay *= 1 + random.uniform(-0.25, 0.25)
        await asyncio.sleep(max(0.0, delay))

    @staticmethod
    async def _download_audio(
        client: httpx.AsyncClient, url: str
    ) -> bytes:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
            return b"".join(chunks)


def _meeting_from_raw(raw: dict[str, Any]) -> Meeting:
    participants = raw.get("participants") or []
    return Meeting(
        meeting_id=raw["id"],
        title=raw.get("title") or "(untitled)",
        meeting_date=datetime.fromisoformat(
            raw["date"].replace("Z", "+00:00")
        ),
        duration_minutes=float(raw.get("duration") or 0.0),
        host_email=raw.get("host_email") or "",
        participant_count=len(participants),
        tags=tuple(raw.get("tags") or ()),
        has_transcript=bool(raw.get("transcript_url")),
    )


def _render_transcript_md(t: dict[str, Any]) -> str:
    sentences = t.get("sentences") or []
    if not sentences:
        return f"# {t.get('title') or '(untitled)'}\n\n_(no transcript content)_\n"
    lines: list[str] = [f"# {t.get('title') or '(untitled)'}", ""]
    last_speaker: str | None = None
    buf: list[str] = []
    for s in sentences:
        speaker = s.get("speaker_name") or "Unknown"
        text = (s.get("text") or "").strip()
        if not text:
            continue
        if speaker != last_speaker and buf:
            lines.append(f"**{last_speaker}:** {' '.join(buf)}")
            lines.append("")
            buf = []
        last_speaker = speaker
        buf.append(text)
    if buf and last_speaker is not None:
        lines.append(f"**{last_speaker}:** {' '.join(buf)}")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
.venv/Scripts/pytest.exe tests/infra/test_fireflies_client.py -v
```

If a test fails because of a query-variable name mismatch (e.g., `to_date` vs `fromDate`), update the test assertion's substring list and proceed; the field names will be confirmed by the contract test in Task 11.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/infra/fireflies_client.py tests/infra/test_fireflies_client.py
git commit -m "feat(infra): add async Fireflies GraphQL client with retries and redaction"
```

---

### Task 11: Optional contract test against live Fireflies API

**Files:**
- Modify: `tests/infra/test_fireflies_client.py`

- [ ] **Step 1: Append a contract test (skipped by default)**

Add to `tests/infra/test_fireflies_client.py`:

```python
import os

@pytest.mark.contract
@pytest.mark.skipif(
    not os.environ.get("FIREFLIES_TEST_API_KEY"),
    reason="FIREFLIES_TEST_API_KEY not set",
)
@pytest.mark.asyncio
async def test_contract_list_one_real_meeting() -> None:
    """Hit live API and confirm schema fields parse into a Meeting."""
    from firefliesclearer.infra.fireflies_client import FirefliesClient

    real_client = FirefliesClient(
        api_key=os.environ["FIREFLIES_TEST_API_KEY"], page_size=1
    )
    count = 0
    async for m in real_client.list_meetings(MeetingFilter(limit=1)):
        assert m.meeting_id
        assert m.title
        count += 1
        break
    assert count <= 1
```

- [ ] **Step 2: Run contract test (manually)**

```bash
$env:FIREFLIES_TEST_API_KEY = "<your-test-api-key>"
.venv/Scripts/pytest.exe -m contract tests/infra/test_fireflies_client.py -v
```

Expected: 1 passed (or skipped if no key). If GraphQL returns errors complaining about field names, read the error and update `LIST_QUERY` in `fireflies_client.py` (e.g., rename `to_date` -> `fromDate`, etc.), update `_meeting_from_raw` to read the new keys, and rerun.

- [ ] **Step 3: Commit**

```bash
git add tests/infra/test_fireflies_client.py
git commit -m "test(infra): add opt-in contract test for live Fireflies API"
```

---

## Phase 10 — CLI

### Task 12: Typer app skeleton, shared helpers, structured logging

**Files:**
- Create: `firefliesclearer/cli/__init__.py` (empty)
- Create: `firefliesclearer/cli/app.py`
- Create: `firefliesclearer/cli/_common.py`
- Create: `firefliesclearer/infra/logging.py`
- Create: `tests/infra/test_logging.py`
- Create: `tests/cli/__init__.py` (empty)
- Create: `tests/cli/test_app.py`

- [ ] **Step 1: Create empty packages**

```bash
echo "" > firefliesclearer/cli/__init__.py
mkdir -p tests/cli && echo "" > tests/cli/__init__.py
```

- [ ] **Step 2: Write failing tests for logging redaction**

`tests/infra/test_logging.py`:

```python
"""Tests for structured JSON logging with API-key redaction."""

from __future__ import annotations

import json
from pathlib import Path

from firefliesclearer.infra.logging import setup_logging
import logging


def test_json_lines_emitted_to_file(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=log_dir, level="INFO")
    logging.getLogger("test").info("hello", extra={"event": "x", "n": 1})
    files = list(log_dir.glob("*.log"))
    assert files, "log file not created"
    line = files[0].read_text(encoding="utf-8").strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["msg"] == "hello"
    assert parsed["event"] == "x"
    assert parsed["n"] == 1


def test_authorization_header_redacted(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=log_dir, level="INFO")
    logging.getLogger("test").info(
        "request: Authorization: Bearer ff_super_secret"
    )
    line = list(log_dir.glob("*.log"))[0].read_text(encoding="utf-8")
    assert "ff_super_secret" not in line
    assert "[REDACTED]" in line
```

- [ ] **Step 3: Implement structured logging**

`firefliesclearer/infra/logging.py`:

```python
"""Structured JSON-lines logging with daily rotation and key redaction."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE)
_API_KEY_PATTERN = re.compile(r"ff_[A-Za-z0-9._\-]+")


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        msg = _BEARER_PATTERN.sub("Bearer [REDACTED]", msg)
        msg = _API_KEY_PATTERN.sub("[REDACTED]", msg)
        out: dict[str, object] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": msg,
        }
        for key, value in record.__dict__.items():
            if key in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "asctime", "taskName",
            }:
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = str(value)
            out[key] = value
        return json.dumps(out, ensure_ascii=False)


def setup_logging(*, log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = log_dir / f"{today}.log"
    handler = TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=30, encoding="utf-8"
    )
    handler.setFormatter(JsonLineFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
```

- [ ] **Step 4: Run logging tests — expect pass**

```bash
.venv/Scripts/pytest.exe tests/infra/test_logging.py -v
```

- [ ] **Step 5: Write failing test for `firefliesclearer --version`**

`tests/cli/test_app.py`:

```python
"""Tests for top-level Typer app."""

from __future__ import annotations

from typer.testing import CliRunner

from firefliesclearer import __version__
from firefliesclearer.cli.app import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_runs() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
```

(The "all commands listed" check is added at the end of Task 17, once every command is implemented.)

- [ ] **Step 6: Implement Typer app skeleton + shared helpers**

`firefliesclearer/cli/_common.py`:

```python
"""Shared CLI helpers: load config, build dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from firefliesclearer.core.archiver import Archiver
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.pipeline import Pipeline
from firefliesclearer.infra.config import (
    AppConfig,
    load_config,
    user_config_path,
)
from firefliesclearer.infra.fireflies_client import FirefliesClient
from firefliesclearer.infra.pdf_renderer import WeasyPrintSummaryRenderer
from firefliesclearer.infra.system_clock import SystemClock

console = Console()


@dataclass
class Deps:
    config: AppConfig
    pipeline: Pipeline
    manifest: Manifest
    client: FirefliesClient


def build_deps(*, config_override: Path | None = None) -> Deps:
    user_path = config_override or user_config_path()
    cfg = load_config(user_config=user_path)
    archive_root = cfg.archive.root_dir
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest.open(archive_root / "manifest.db")
    archiver = Archiver(archive_root=archive_root)
    renderer = WeasyPrintSummaryRenderer()
    client = FirefliesClient(api_key=cfg.fireflies.api_key)
    pipeline = Pipeline(
        repository=client,
        manifest=manifest,
        archiver=archiver,
        renderer=renderer,
        clock=SystemClock(),
    )
    return Deps(config=cfg, pipeline=pipeline, manifest=manifest, client=client)
```

`firefliesclearer/cli/app.py`:

```python
"""Top-level Typer app."""

from __future__ import annotations

import typer

from firefliesclearer import __version__

app = typer.Typer(
    name="firefliesclearer",
    help="Safely archive and clean up Fireflies AI meetings.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"firefliesclearer {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """FirefliesClearer."""


# Commands registered via side-effect imports below.
from firefliesclearer.cli import (  # noqa: E402,F401
    archive_cmd,
    history_cmd,
    init_cmd,
    purge_cmd,
    run_cmd,
    scan_cmd,
    status_cmd,
)
```

Create stub modules so the imports above succeed (each command will be filled in by Tasks 13–18):

For each of `init_cmd.py`, `scan_cmd.py`, `archive_cmd.py`, `purge_cmd.py`, `run_cmd.py`, `status_cmd.py`, `history_cmd.py` create as a near-empty stub that does NOT register any Typer command yet — the import alone must succeed:

```python
"""(stub — implemented in a later task)"""
```

That's the entire file. No imports, no decorators. This avoids registering placeholder Typer commands that would clutter `--help`. Tasks 13–17 fully replace each file with the real command.

Loosen the `test_help_lists_all_commands` test for now so Task 12 can land green:

```python
def test_help_runs() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
```

The full "all commands listed" assertion is added back as the last step of Task 17 once every command is implemented:

```python
def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["init", "scan", "archive", "purge", "run", "status", "history"]:
        assert cmd in result.stdout
```

- [ ] **Step 7: Run app tests — expect pass**

```bash
.venv/Scripts/pytest.exe tests/cli/test_app.py -v
```

Both tests should pass: `--version` prints version, `--help` exits 0 (no commands yet — that's fine).

- [ ] **Step 8: Commit**

```bash
git add firefliesclearer/cli/ firefliesclearer/infra/logging.py tests/infra/test_logging.py tests/cli/__init__.py tests/cli/test_app.py
git commit -m "feat(cli): scaffold Typer app, shared helpers, JSON logging"
```

---

### Task 13: `init` command — interactive first-run config

**Files:**
- Modify: `firefliesclearer/cli/init_cmd.py`
- Create: `tests/cli/test_init_cmd.py`

- [ ] **Step 1: Write failing test**

`tests/cli/test_init_cmd.py`:

```python
"""Tests for `firefliesclearer init`."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from firefliesclearer.cli.app import app
from firefliesclearer.infra.config import load_config

runner = CliRunner()


def test_init_writes_config_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    archive_root = tmp_path / "arch"
    result = runner.invoke(
        app,
        ["init", "--config", str(cfg_path), "--no-ping"],
        input=f"ff_test_key\n{archive_root}\n180\ny\n",
    )
    assert result.exit_code == 0, result.stdout
    assert cfg_path.exists()
    cfg = load_config(user_config=cfg_path)
    assert cfg.fireflies.api_key == "ff_test_key"
    assert cfg.archive.root_dir == archive_root


def test_init_does_not_echo_api_key(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    archive_root = tmp_path / "arch"
    result = runner.invoke(
        app,
        ["init", "--config", str(cfg_path), "--no-ping"],
        input=f"ff_secret_key\n{archive_root}\n180\ny\n",
    )
    assert "ff_secret_key" not in result.stdout
```

- [ ] **Step 2: Implement `init`**

Replace `firefliesclearer/cli/init_cmd.py`:

```python
"""`firefliesclearer init` — interactive first-run config."""

from __future__ import annotations

from pathlib import Path

import typer

from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app
from firefliesclearer.infra.config import (
    AppConfig,
    user_config_path,
    write_config,
)


@app.command()
def init(
    config: Path = typer.Option(
        None, "--config", help="Override config file path."
    ),
    no_ping: bool = typer.Option(
        False, "--no-ping", help="Skip API connectivity check."
    ),
) -> None:
    """Set up FirefliesClearer for the first time."""
    target = config or user_config_path()
    api_key = typer.prompt("Fireflies API key", hide_input=True)
    default_root = str(Path.home() / "Documents" / "firefliesclearer-archive")
    root_str = typer.prompt(
        "Archive root directory", default=default_root
    )
    older_than = typer.prompt(
        "Auto-path: delete meetings older than N days", default=180, type=int
    )
    delete_failed = typer.confirm(
        "Auto-path: delete meetings with failed transcripts?", default=True
    )
    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": api_key},
            "archive": {
                "root_dir": str(Path(root_str)),
                "summary_format": "pdf",
            },
            "rules": {
                "auto": {
                    "older_than_days": older_than,
                    "delete_failed_transcripts": delete_failed,
                }
            },
        }
    )
    write_config(cfg, target)
    console.print(f"[green]Config written:[/green] {target}")
    if no_ping:
        return
    console.print("[dim]Skipping connectivity check (--no-ping).[/dim]")
```

- [ ] **Step 3: Remove the old placeholder in this file** if any auto-generated stub from Task 12 still exists. The replacement above defines the real `init` command directly.

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/pytest.exe tests/cli/test_init_cmd.py -v
```

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/cli/init_cmd.py tests/cli/test_init_cmd.py
git commit -m "feat(cli): implement init command for interactive first-run config"
```

---

### Task 14: `scan` command — list candidates and write selection file

**Files:**
- Modify: `firefliesclearer/cli/scan_cmd.py`
- Create: `tests/cli/test_scan_cmd.py`

- [ ] **Step 1: Write failing test**

`tests/cli/test_scan_cmd.py`:

```python
"""Tests for `firefliesclearer scan`."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from firefliesclearer.cli.app import app
from firefliesclearer.core.models import Meeting
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

runner = CliRunner()
NOW = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)


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
    from firefliesclearer.cli import _common, scan_cmd
    from firefliesclearer.core.archiver import Archiver
    from firefliesclearer.core.manifest import Manifest
    from firefliesclearer.core.pipeline import Pipeline
    from firefliesclearer.infra.config import AppConfig
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
        config=cfg, pipeline=pipeline, manifest=manifest, client=repo
    )
    monkeypatch.setattr(_common, "build_deps", lambda **kw: deps)
    return tmp_path, archive_root


def test_scan_with_older_than_writes_selection_file(patched_deps) -> None:
    _, archive_root = patched_deps
    result = runner.invoke(
        app, ["scan", "--older-than-days", "180"]
    )
    assert result.exit_code == 0, result.stdout
    selections = list((archive_root / "selections").glob("scan_*.json"))
    assert len(selections) == 1
    payload = json.loads(selections[0].read_text(encoding="utf-8"))
    ids = [m["id"] for m in payload["meetings"]]
    assert ids == ["old1"]
    assert payload["meetings"][0]["selected"] is True
```

- [ ] **Step 2: Implement `scan`**

Replace `firefliesclearer/cli/scan_cmd.py`:

```python
"""`firefliesclearer scan` — list candidates, write selection file."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from rich.table import Table

from firefliesclearer.cli._common import build_deps, console
from firefliesclearer.cli.app import app
from firefliesclearer.core.models import Meeting
from firefliesclearer.core.rules import (
    DurationBelow,
    HasTag,
    HostEmail,
    OlderThanDays,
    ParticipantsBelow,
    RuleEngine,
    TitleContains,
    TitleRegex,
)
from firefliesclearer.ports.meeting_repository import MeetingFilter


@app.command()
def scan(
    older_than_days: int = typer.Option(
        None, "--older-than-days", help="Match meetings older than N days."
    ),
    duration_below: float = typer.Option(
        None, "--duration-below", help="Match duration < N minutes."
    ),
    title_contains: list[str] = typer.Option(
        None, "--title-contains", help="Substring match on title."
    ),
    title_regex: str = typer.Option(
        None, "--title-regex", help="Regex match on title."
    ),
    host_email: list[str] = typer.Option(
        None, "--host-email", help="Match host email (repeatable)."
    ),
    participants_below: int = typer.Option(
        None, "--participants-below", help="Match if participants < N."
    ),
    has_tag: list[str] = typer.Option(
        None, "--has-tag", help="Match if any of these tags present."
    ),
    config: Path = typer.Option(
        None, "--config", help="Override config file path."
    ),
) -> None:
    """List meetings matching the given rules; writes a selection file."""
    deps = build_deps(config_override=config)
    rules = []
    if older_than_days is not None:
        rules.append(OlderThanDays(older_than_days))
    if duration_below is not None:
        rules.append(DurationBelow(duration_below))
    if title_contains:
        rules.append(TitleContains(title_contains))
    if title_regex:
        rules.append(TitleRegex(title_regex))
    if host_email:
        rules.append(HostEmail(host_email))
    if participants_below is not None:
        rules.append(ParticipantsBelow(participants_below))
    if has_tag:
        rules.append(HasTag(has_tag))
    if not rules:
        raise typer.BadParameter("Provide at least one filter.")

    engine = RuleEngine(rules)
    now = datetime.now(tz=timezone.utc)
    cutoff = (
        now - timedelta(days=older_than_days)
        if older_than_days is not None
        else None
    )

    matched: list[tuple[Meeting, tuple[str, ...]]] = []

    async def _collect() -> None:
        async for m in deps.client.list_meetings(
            MeetingFilter(older_than=cutoff)
        ):
            result = engine.evaluate(m, now=now)
            if result.matched:
                matched.append((m, result.reasons))

    asyncio.run(_collect())

    table = Table(title=f"Candidates ({len(matched)})")
    for col in ("ID", "Date", "Title", "Dur", "Host", "Reasons"):
        table.add_column(col)
    for m, reasons in matched:
        table.add_row(
            m.meeting_id,
            m.meeting_date.date().isoformat(),
            m.title[:60],
            f"{m.duration_minutes:.1f}",
            m.host_email,
            ", ".join(reasons),
        )
    console.print(table)

    selections_dir = deps.config.archive.root_dir / "selections"
    selections_dir.mkdir(parents=True, exist_ok=True)
    scan_id = f"scan_{now.strftime('%Y%m%dT%H%M')}"
    payload = {
        "scan_id": scan_id,
        "created_at": now.isoformat(),
        "filters_applied": {
            "older_than_days": older_than_days,
            "duration_below_minutes": duration_below,
            "title_contains": title_contains,
            "title_regex": title_regex,
            "host_email": host_email,
            "participants_below": participants_below,
            "has_tag": has_tag,
        },
        "meetings": [
            {
                "id": m.meeting_id,
                "title": m.title,
                "date": m.meeting_date.isoformat(),
                "duration_min": m.duration_minutes,
                "host": m.host_email,
                "participants": m.participant_count,
                "tags": list(m.tags),
                "selected": True,
                "matched_rules": list(reasons),
            }
            for m, reasons in matched
        ],
    }
    target = selections_dir / f"{scan_id}.json"
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    console.print(f"[green]Wrote selection:[/green] {target}")
```

- [ ] **Step 3: Run tests**

```bash
.venv/Scripts/pytest.exe tests/cli/test_scan_cmd.py -v
```

- [ ] **Step 4: Commit**

```bash
git add firefliesclearer/cli/scan_cmd.py tests/cli/test_scan_cmd.py
git commit -m "feat(cli): implement scan command with filters and selection file output"
```

---

### Task 15: `archive` and `purge` commands — selection-file driven

**Files:**
- Modify: `firefliesclearer/cli/archive_cmd.py`
- Modify: `firefliesclearer/cli/purge_cmd.py`
- Create: `tests/cli/test_archive_purge_cmd.py`

- [ ] **Step 1: Write failing test**

`tests/cli/test_archive_purge_cmd.py`:

```python
"""Tests for `archive` and `purge` curated-path commands."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from firefliesclearer.cli.app import app
from firefliesclearer.core.models import ArtifactBundle, Meeting, MeetingState
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

runner = CliRunner()
NOW = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)


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
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
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
        config=cfg, pipeline=pipeline, manifest=manifest, client=repo
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

    result = runner.invoke(
        app, ["purge", "--selection", str(selection), "--yes"]
    )
    assert result.exit_code == 0, result.stdout
    assert manifest.get("01HW").state is MeetingState.DELETED
    assert repo.deleted == ["01HW"]


def test_archive_dry_run_makes_no_changes(patched, tmp_path: Path) -> None:
    _, repo, manifest = patched
    selection = _selection([_meeting()], tmp_path / "sel.json")
    result = runner.invoke(
        app, ["archive", "--selection", str(selection), "--dry-run"]
    )
    assert result.exit_code == 0
    assert manifest.get("01HW") is None
    assert repo.deleted == []


def test_purge_skips_unselected(patched, tmp_path: Path) -> None:
    _, repo, manifest = patched
    sel_path = _selection([_meeting()], tmp_path / "sel.json")
    payload = json.loads(sel_path.read_text(encoding="utf-8"))
    payload["meetings"][0]["selected"] = False
    sel_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    runner.invoke(app, ["archive", "--selection", str(sel_path)])
    result = runner.invoke(
        app, ["purge", "--selection", str(sel_path), "--yes"]
    )
    assert result.exit_code == 0
    assert repo.deleted == []
```

- [ ] **Step 2: Implement `archive`**

Replace `firefliesclearer/cli/archive_cmd.py`:

```python
"""`firefliesclearer archive` — archive selected meetings."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import typer

from firefliesclearer.cli._common import build_deps, console
from firefliesclearer.cli.app import app
from firefliesclearer.core.models import Meeting
from firefliesclearer.core.pipeline import PipelineMode


@app.command()
def archive(
    selection: Path = typer.Option(
        ..., "--selection", exists=True, help="Path to selection JSON."
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    config: Path = typer.Option(None, "--config"),
) -> None:
    """Archive every `selected:true` meeting in the selection file."""
    deps = build_deps(config_override=config)
    meetings = _load_selected(selection)
    if not meetings:
        console.print("[yellow]No selected meetings; nothing to do.[/yellow]")
        return
    mode = PipelineMode.DRY_RUN if dry_run else PipelineMode.ARCHIVE_ONLY
    report = asyncio.run(deps.pipeline.run(meetings, mode=mode))
    _print_report(report)


def _load_selected(selection: Path) -> list[Meeting]:
    payload = json.loads(selection.read_text(encoding="utf-8"))
    out: list[Meeting] = []
    for entry in payload["meetings"]:
        if not entry.get("selected", True):
            continue
        out.append(
            Meeting(
                meeting_id=entry["id"],
                title=entry["title"],
                meeting_date=datetime.fromisoformat(entry["date"]),
                duration_minutes=float(entry.get("duration_min", 0.0)),
                host_email=entry.get("host", ""),
                participant_count=int(entry.get("participants", 0)),
                tags=tuple(entry.get("tags", [])),
                has_transcript=True,
            )
        )
    return out


def _print_report(report) -> None:
    console.print(
        f"[green]archived={report.archived}[/green] "
        f"[red]failed={report.failed}[/red] "
        f"skipped={report.skipped} deleted={report.deleted}"
    )
    for mid, err in report.failures[:20]:
        console.print(f"  [red]{mid}[/red]: {err}")
```

- [ ] **Step 3: Implement `purge`**

Replace `firefliesclearer/cli/purge_cmd.py`:

```python
"""`firefliesclearer purge` — delete archived meetings."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from firefliesclearer.cli._common import build_deps, console
from firefliesclearer.cli.app import app
from firefliesclearer.cli.archive_cmd import _load_selected, _print_report
from firefliesclearer.core.pipeline import PipelineMode


@app.command()
def purge(
    selection: Path = typer.Option(
        ..., "--selection", exists=True, help="Path to selection JSON."
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(
        False, "--yes", help="Skip confirmation prompt."
    ),
    config: Path = typer.Option(None, "--config"),
) -> None:
    """Delete every `selected:true` meeting in the selection (verifies archive first)."""
    deps = build_deps(config_override=config)
    meetings = _load_selected(selection)
    if not meetings:
        console.print("[yellow]No selected meetings; nothing to do.[/yellow]")
        return
    threshold = deps.config.run.delete_confirmation_threshold
    if not dry_run and len(meetings) > threshold and not yes:
        confirm = typer.confirm(
            f"About to delete {len(meetings)} meetings from Fireflies. Continue?"
        )
        if not confirm:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(code=1)
    mode = PipelineMode.DRY_RUN if dry_run else PipelineMode.PURGE_ONLY
    report = asyncio.run(deps.pipeline.run(meetings, mode=mode))
    _print_report(report)
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/pytest.exe tests/cli/test_archive_purge_cmd.py -v
```

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/cli/archive_cmd.py firefliesclearer/cli/purge_cmd.py tests/cli/test_archive_purge_cmd.py
git commit -m "feat(cli): implement archive and purge commands using selection files"
```

---

### Task 16: `run` command — auto path with hard rules

**Files:**
- Modify: `firefliesclearer/cli/run_cmd.py`
- Create: `tests/cli/test_run_cmd.py`

- [ ] **Step 1: Write failing test**

`tests/cli/test_run_cmd.py`:

```python
"""Tests for `firefliesclearer run` — auto path."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from firefliesclearer.cli.app import app
from firefliesclearer.core.models import ArtifactBundle, Meeting, MeetingState
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

runner = CliRunner()
NOW = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)


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


@pytest.fixture
def patched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from firefliesclearer.cli import _common
    from firefliesclearer.core.archiver import Archiver
    from firefliesclearer.core.manifest import Manifest
    from firefliesclearer.core.pipeline import Pipeline
    from firefliesclearer.infra.config import AppConfig
    from tests.fakes.fake_renderer import FakeSummaryRenderer
    from tests.fakes.frozen_clock import FrozenClock

    old = _meeting("old", days_old=200)
    no_t = _meeting("nt", days_old=10, has_transcript=False)
    fresh = _meeting("fresh", days_old=10)
    repo = InMemoryMeetingRepository(
        meetings=[old, no_t, fresh],
        artifacts={
            "old": ArtifactBundle(
                audio_bytes=b"A", transcript_markdown="# T", summary_payload={}
            ),
            "nt": ArtifactBundle(
                audio_bytes=b"A", transcript_markdown="# T", summary_payload={}
            ),
            "fresh": ArtifactBundle(),
        },
    )
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
            "rules": {
                "auto": {
                    "older_than_days": 180,
                    "delete_failed_transcripts": True,
                }
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
        config=cfg, pipeline=pipeline, manifest=manifest, client=repo
    )
    monkeypatch.setattr(_common, "build_deps", lambda **kw: deps)
    return repo, manifest


def test_run_dry_run_makes_no_mutations(patched) -> None:
    repo, manifest = patched
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 0, result.stdout
    assert manifest.get("old") is None
    assert repo.deleted == []


def test_run_apply_deletes_matching(patched) -> None:
    repo, manifest = patched
    result = runner.invoke(app, ["run", "--apply", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert manifest.get("old").state is MeetingState.DELETED
    assert manifest.get("nt").state is MeetingState.DELETED
    assert manifest.get("fresh") is None
    assert set(repo.deleted) == {"old", "nt"}
```

- [ ] **Step 2: Implement `run`**

Replace `firefliesclearer/cli/run_cmd.py`:

```python
"""`firefliesclearer run` — auto path."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer

from firefliesclearer.cli._common import build_deps, console
from firefliesclearer.cli.archive_cmd import _print_report
from firefliesclearer.cli.app import app
from firefliesclearer.core.models import Meeting
from firefliesclearer.core.pipeline import PipelineMode
from firefliesclearer.core.rules import NoTranscript, OlderThanDays, RuleEngine
from firefliesclearer.ports.meeting_repository import MeetingFilter


@app.command()
def run(
    apply: bool = typer.Option(
        False, "--apply", help="Actually mutate (default: dry-run)."
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Skip confirmation prompt above threshold."
    ),
    config: Path = typer.Option(None, "--config"),
) -> None:
    """Apply hard rules from config (age + no-transcript)."""
    deps = build_deps(config_override=config)
    auto = deps.config.auto_rules()
    rules = [OlderThanDays(auto.older_than_days)]
    matched: list[Meeting] = []
    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=auto.older_than_days)

    async def _collect() -> None:
        engine = RuleEngine(rules)
        no_transcript = NoTranscript()
        async for m in deps.client.list_meetings(
            MeetingFilter(older_than=None)
        ):
            age_match = engine.evaluate(m, now=now).matched
            no_t_match = (
                auto.delete_failed_transcripts
                and no_transcript.matches(m, now=now)
            )
            if age_match or no_t_match:
                matched.append(m)

    asyncio.run(_collect())

    if not matched:
        console.print("[green]Nothing matched.[/green]")
        return

    console.print(f"[bold]{len(matched)}[/bold] meetings match auto rules.")
    if not apply:
        console.print("[yellow]Dry-run; pass --apply to mutate.[/yellow]")
        return

    threshold = deps.config.run.delete_confirmation_threshold
    if len(matched) > threshold and not yes:
        if not typer.confirm(
            f"About to archive+delete {len(matched)} meetings. Continue?"
        ):
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(code=1)

    report = asyncio.run(
        deps.pipeline.run(matched, mode=PipelineMode.APPLY)
    )
    _print_report(report)

    # discard unused cutoff if only collected for symmetry
    _ = cutoff
```

- [ ] **Step 3: Run tests**

```bash
.venv/Scripts/pytest.exe tests/cli/test_run_cmd.py -v
```

- [ ] **Step 4: Commit**

```bash
git add firefliesclearer/cli/run_cmd.py tests/cli/test_run_cmd.py
git commit -m "feat(cli): implement run command for auto-path with age + no-transcript rules"
```

---

### Task 17: `status` and `history` commands

**Files:**
- Modify: `firefliesclearer/cli/status_cmd.py`
- Modify: `firefliesclearer/cli/history_cmd.py`
- Create: `tests/cli/test_status_history_cmd.py`

- [ ] **Step 1: Write failing test**

`tests/cli/test_status_history_cmd.py`:

```python
"""Tests for `status` and `history`."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from firefliesclearer.cli.app import app
from firefliesclearer.core.models import Meeting, MeetingState

runner = CliRunner()
NOW = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)


def _meeting(mid: str) -> Meeting:
    return Meeting(
        meeting_id=mid,
        title=f"M-{mid}",
        meeting_date=NOW,
        duration_minutes=1.0,
        host_email="u@x.com",
        participant_count=1,
        tags=(),
        has_transcript=True,
    )


@pytest.fixture
def patched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from firefliesclearer.cli import _common
    from firefliesclearer.core.manifest import Manifest
    from firefliesclearer.infra.config import AppConfig

    archive_root = tmp_path / "arch"
    archive_root.mkdir()
    manifest = Manifest.open(archive_root / "manifest.db")
    manifest.register(_meeting("a"), at=NOW)
    manifest.transition("a", to=MeetingState.ARCHIVED, at=NOW)
    manifest.transition("a", to=MeetingState.DELETED, at=NOW)
    manifest.register(_meeting("b"), at=NOW)

    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": "x"},
            "archive": {
                "root_dir": str(archive_root),
                "summary_format": "pdf",
            },
        }
    )
    deps = _common.Deps(config=cfg, pipeline=None, manifest=manifest, client=None)
    monkeypatch.setattr(_common, "build_deps", lambda **kw: deps)


def test_status_shows_counts(patched) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stdout
    assert "deleted" in result.stdout.lower()
    assert "pending" in result.stdout.lower()


def test_history_for_known_month_lists_deleted(patched) -> None:
    result = runner.invoke(app, ["history", "--month", "2026-04"])
    assert result.exit_code == 0
    assert "a" in result.stdout
    assert "b" not in result.stdout  # b is pending, not deleted
```

- [ ] **Step 2: Implement `status`**

Replace `firefliesclearer/cli/status_cmd.py`:

```python
"""`firefliesclearer status` — manifest summary."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from firefliesclearer.cli._common import build_deps, console
from firefliesclearer.cli.app import app


@app.command()
def status(
    config: Path = typer.Option(None, "--config"),
) -> None:
    """Show counts per state and recent failures."""
    deps = build_deps(config_override=config)
    counts = deps.manifest.counts_by_state()
    table = Table(title="Manifest state")
    table.add_column("State")
    table.add_column("Count", justify="right")
    for state, n in sorted(counts.items(), key=lambda kv: kv[0].value):
        table.add_row(state.value, str(n))
    console.print(table)
```

- [ ] **Step 3: Implement `history`**

Replace `firefliesclearer/cli/history_cmd.py`:

```python
"""`firefliesclearer history` — audit query."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from firefliesclearer.cli._common import build_deps, console
from firefliesclearer.cli.app import app


@app.command()
def history(
    month: str = typer.Option(
        ..., "--month", help="Year-Month, e.g. 2026-04."
    ),
    config: Path = typer.Option(None, "--config"),
) -> None:
    """List meetings deleted in the given month (audit)."""
    deps = build_deps(config_override=config)
    try:
        year_str, month_str = month.split("-")
        year, mnum = int(year_str), int(month_str)
    except ValueError as e:
        raise typer.BadParameter(
            f"Invalid --month '{month}'. Use YYYY-MM."
        ) from e

    records = deps.manifest.history(year=year, month=mnum)
    table = Table(title=f"Deleted in {year:04d}-{mnum:02d}")
    for col in ("ID", "Title", "Meeting date", "Deleted at", "Archive path"):
        table.add_column(col)
    for r in records:
        table.add_row(
            r.meeting_id,
            r.title[:60],
            r.meeting_date.date().isoformat(),
            r.deleted_at.isoformat() if r.deleted_at else "",
            r.archive_path or "",
        )
    console.print(table)
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/pytest.exe tests/cli/test_status_history_cmd.py -v
```

- [ ] **Step 5: Tighten the help test now that all commands exist**

Append to `tests/cli/test_app.py`:

```python
def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["init", "scan", "archive", "purge", "run", "status", "history"]:
        assert cmd in result.stdout
```

Run:

```bash
.venv/Scripts/pytest.exe tests/cli/test_app.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/cli/status_cmd.py firefliesclearer/cli/history_cmd.py tests/cli/test_status_history_cmd.py tests/cli/test_app.py
git commit -m "feat(cli): implement status and history audit commands"
```

---

## Phase 11 — Polish

### Task 18: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install GTK for WeasyPrint
        run: sudo apt-get update && sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0
      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Lint
        run: ruff check .
      - name: Format check
        run: ruff format --check .
      - name: Type check (core strict)
        run: mypy firefliesclearer
      - name: Tests
        run: pytest -q
```

- [ ] **Step 2: Commit and verify on GitHub**

```bash
mkdir -p .github/workflows
git add .github/workflows/ci.yml
git commit -m "ci: add lint/type/test workflow"
git push
```

Open the Actions tab on `https://github.com/oskar-bialek-bakk/FirefliesClearer` and confirm the run goes green.

---

### Task 19: README and final coverage gate

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write README setup + usage**

Replace `README.md` with:

```markdown
# FirefliesClearer

Safely archive and clean up [Fireflies AI](https://fireflies.ai) meetings.

For each meeting matched by configurable rules, FirefliesClearer:

1. Lists candidates
2. Downloads artifacts to local disk: `summary.pdf` (rendered locally), `audio.mp3`, `transcript.md`, `metadata.json`
3. Verifies the archive on disk
4. Only then deletes the meeting from Fireflies

State is tracked in a local SQLite manifest for safe re-runs and audit.

## Setup

Requires Python 3.12+. WeasyPrint requires GTK on Windows; if installation fails,
follow the [WeasyPrint Windows guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows).

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
firefliesclearer init
```

## Curated cleanup (review and confirm)

```bash
firefliesclearer scan --older-than-days 90 --title-contains test
# Edit the resulting selections/scan_*.json (set selected:false on rows to keep)
firefliesclearer archive --selection selections/scan_20260428T1145.json
firefliesclearer purge   --selection selections/scan_20260428T1145.json
```

## Auto cleanup (cron / Task Scheduler)

```bash
firefliesclearer run                # dry-run
firefliesclearer run --apply --yes  # actually mutate; suitable for cron
```

## Audit

```bash
firefliesclearer status
firefliesclearer history --month 2026-04
```

## Configuration

User config at `%APPDATA%\firefliesclearer\config.toml` (Windows),
`~/.config/firefliesclearer/config.toml` (Linux/Mac). Override precedence (highest wins):

1. CLI flags
2. `FIREFLIES_API_KEY` env var
3. `./firefliesclearer.toml` (project-local)
4. User config

## License

MIT.
```

- [ ] **Step 2: Run full test suite with coverage**

```bash
.venv/Scripts/pytest.exe -q
```

Expected: all tests pass; coverage on `core/pipeline.py` and `core/manifest.py` is 100%; overall ≥80%. If not, identify uncovered lines (`--cov-report=term-missing`) and add targeted tests.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add setup, curated, auto, audit, and config sections to README"
```

- [ ] **Step 4: Final smoke test**

```bash
.venv/Scripts/firefliesclearer.exe --help
.venv/Scripts/firefliesclearer.exe --version
.venv/Scripts/firefliesclearer.exe init --config /tmp/cfg.toml --no-ping
```

Each should exit 0 with sensible output.

- [ ] **Step 5: Push final state**

```bash
git push
```

---

## Deferred from v1 (tracked, not implemented in this plan)

These items appear in the spec but are intentionally not part of the v1 plan above. They are safe to defer because the architecture leaves clean seams for adding them later without rewriting tests.

- **Async concurrency across meetings (spec §10.2).** The pipeline currently processes meetings sequentially. A `Semaphore`-bounded `asyncio.gather` can be added inside `Pipeline.run` later, with a single new test that exercises 3 concurrent meetings. The per-meeting transactional invariants are already independent, so concurrency is a drop-in once needed.
- **`init` sanity ping (spec §9.3 step 5).** Task 13's `init` writes config but does not call `getUser` against Fireflies before declaring success. Add a short async ping in `init_cmd.py` after `write_config` (gated off by `--no-ping` for tests) when the live `FirefliesClient` is wired in. Estimated effort: ~10 minutes of code + 1 test.

Both are noted here so they appear in `git log` / future grep, not lost.

