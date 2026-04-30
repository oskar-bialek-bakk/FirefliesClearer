# FirefliesClearer v2 — Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local single-user FastAPI + HTMX web UI as a second presentation layer over v1's CLI, giving colleagues full Fireflies cleanup capability via `firefliesclearer serve` with no terminal interaction beyond that single command.

**Architecture:** New `web/` package alongside the existing `cli/`, both consuming a new `application/` services layer extracted from v1's CLI files. `core/`, `infra/`, `ports/`, and the manifest schema are byte-identical to v1. Server-side-rendered HTML with HTMX for interactivity; SSE for live operation progress; heartbeat-driven shutdown when the browser closes.

**Tech Stack:** Python 3.12+, FastAPI 0.115+, uvicorn, Jinja2, HTMX (vendored), Tailwind CSS (pre-compiled at packaging time), sse-starlette, itsdangerous, pytest + pytest-asyncio + respx.

**Spec:** `docs/superpowers/specs/2026-04-29-firefliesclearer-v2-web-ui-design.md`

---

## Phase plan

| Phase | Title | Outcome |
|---|---|---|
| 1 | Application services extraction | Mechanical refactor; v1 tests still pass; CLI still works |
| 2 | Web package skeleton + `serve` command + lifecycle + security | `firefliesclearer serve` opens a browser to a "Hello" page; heartbeat-shutdown works |
| 3 | Setup wizard | First-run experience replaces `init`; `init` removed |
| 4 | Dashboard + sidebar shell | Real Dashboard with state counts, last activity, needs-attention list (without retry — added in P5) |
| 5 | Cleanup wizard + operation registry + SSE | The 4-step wizard works end-to-end against in-memory fakes and live API; retry from dashboard works |
| 6 | Presets CRUD + migration + `run --preset` | Saving and loading filter combos; v1 `[rules.auto]` migration; CLI cron path uses `--preset` |
| 7 | History page | Filterable, paginated audit view with side panel |
| 8 | Settings page | Editable config with all sections |
| 9 | Polish, docs, release prep | README, CHANGELOG, CLAUDE.md, smoke checklist, coverage gate, wheel-size sanity |

Each phase ends with a checkpoint commit on the `version/v2` branch and is independently verifiable.

---

## Branching

Per the project's branch policy (memory: `project_firefliesclearer.md`):

- All phase work lands on a single `version/v2` integration branch.
- Each task in this plan is a small commit on a per-task feature branch (`feat/v2/<NN>-<topic>`).
- Each feature branch opens a PR into `version/v2`. After merging, delete the feature branch.
- After all phases complete, open `version/v2` → `main` PR. After that merges, delete `version/v2`.

**One-time setup before starting Task 1:**

```bash
cd C:/GIT/FirefliesClearer
git checkout main
git pull
git checkout -b version/v2
git push -u origin version/v2
```

---

## File structure overview

### Files to CREATE

**Application services (Phase 1):**
- `firefliesclearer/application/__init__.py`
- `firefliesclearer/application/setup_service.py`
- `firefliesclearer/application/scan_service.py`
- `firefliesclearer/application/archive_service.py`
- `firefliesclearer/application/purge_service.py`
- `firefliesclearer/application/audit_service.py`
- `firefliesclearer/application/preset_service.py` (Phase 6)

**Web package (Phase 2 onward):**
- `firefliesclearer/web/__init__.py`
- `firefliesclearer/web/app.py` — FastAPI app factory
- `firefliesclearer/web/lifecycle.py` — heartbeat tracker + graceful shutdown
- `firefliesclearer/web/deps.py` — FastAPI Depends() providers
- `firefliesclearer/web/operations.py` — OperationRegistry (Phase 5)
- `firefliesclearer/web/security.py` — session token + CSRF
- `firefliesclearer/web/sessions.py` — server-side wizard session storage
- `firefliesclearer/web/lockfile.py` — single-instance lockfile
- `firefliesclearer/web/routes/__init__.py`
- `firefliesclearer/web/routes/_heartbeat.py`
- `firefliesclearer/web/routes/_quit.py`
- `firefliesclearer/web/routes/setup.py` (Phase 3)
- `firefliesclearer/web/routes/dashboard.py` (Phase 4)
- `firefliesclearer/web/routes/cleanup.py` (Phase 5)
- `firefliesclearer/web/routes/progress.py` (Phase 5 — SSE)
- `firefliesclearer/web/routes/presets.py` (Phase 6)
- `firefliesclearer/web/routes/history.py` (Phase 7)
- `firefliesclearer/web/routes/settings.py` (Phase 8)

**Templates (`firefliesclearer/web/templates/`):**
- `base.html`, `_macros.html`, `error.html`
- `dashboard.html`, `presets.html`, `history.html`, `settings.html`
- `setup/welcome.html`, `setup/api_key.html`, `setup/archive_root.html`, `setup/defaults.html`
- `cleanup/filter.html`, `cleanup/review.html`, `cleanup/archive.html`, `cleanup/purge.html`
- `partials/sidebar_status.html`, `partials/state_counts.html`, `partials/last_activity.html`, `partials/needs_attention.html`
- `partials/meeting_table.html`, `partials/side_panel.html`, `partials/filter_form.html`, `partials/preset_dropdown.html`
- `partials/op_progress.html`, `partials/op_meeting_row.html`
- `partials/flash.html`, `partials/csrf_token.html`

**Static (`firefliesclearer/web/static/`):**
- `htmx.min.js` (vendored, ~50 KB)
- `htmx-sse.js` (vendored)
- `styles.css` (Tailwind, pre-compiled)
- `app.js` (heartbeat ping, side-panel toggle, shift-select)
- `icons/` (Lucide subset SVGs)

**CLI changes:**
- `firefliesclearer/cli/serve_cmd.py` (Phase 2)

**Tooling:**
- `tools/build_static.sh` — compiles Tailwind once at package time
- `tools/tailwind.config.cjs` — Tailwind config (or `.json`; `.cjs` keeps Node CLI happy if used)
- `tailwind.input.css` — Tailwind directives source

**Tests:**
- `tests/application/__init__.py` and one `test_*_service.py` per service
- `tests/web/__init__.py`, `tests/web/conftest.py`
- `tests/web/test_lifecycle.py`, `test_operations.py`, `test_security.py`, `test_sessions.py`, `test_lockfile.py`
- `tests/web/routes/__init__.py` and `test_<name>.py` per route module
- `tests/web/e2e/test_full_run.py`

**Docs:**
- `docs/superpowers/specs/v2-release-smoke.md` — manual smoke checklist (created Phase 9)

### Files to MODIFY

- `firefliesclearer/cli/app.py` — add `serve_cmd` import, remove `init_cmd` import
- `firefliesclearer/cli/scan_cmd.py` — delegate to `scan_service`
- `firefliesclearer/cli/archive_cmd.py` — delegate to `archive_service`
- `firefliesclearer/cli/purge_cmd.py` — delegate to `purge_service`
- `firefliesclearer/cli/run_cmd.py` — delegate; add `--preset` flag (Phase 6)
- `firefliesclearer/cli/status_cmd.py` — delegate to `audit_service`
- `firefliesclearer/cli/history_cmd.py` — delegate to `audit_service`
- `firefliesclearer/cli/_common.py` — small helpers used by both CLI and tests
- `firefliesclearer/infra/config.py` — add `Preset` model + `[[presets]]` section (Phase 6)
- `pyproject.toml` — new dependencies; package-data for templates and static; mypy strict scope
- `README.md` — web UI quickstart, screenshots, scheduling docs (Phase 9)
- `CLAUDE.md` — v2 architecture overview (Phase 9)
- `CHANGELOG.md` — create if missing; add v2 entry (Phase 9)
- `.github/workflows/ci.yml` — coverage gate `--cov-fail-under=85`; mypy scope (Phase 9)

### Files to DELETE

- `firefliesclearer/cli/init_cmd.py` (Phase 3)
- `tests/cli/test_init_cmd.py` (Phase 3 — superseded by `tests/application/test_setup_service.py`)

---

# Phase 1 — Application services extraction

**Goal of phase:** lift orchestration logic out of CLI command files into `firefliesclearer/application/`. No behaviour change; all 123 v1 tests pass unchanged. Each CLI command becomes a thin Typer wrapper that builds inputs, calls the service, and renders output.

**Phase exit criteria:**
- `firefliesclearer/application/` exists with one service per former CLI command (excluding `init`, which becomes `setup_service`).
- Each CLI command file is < 50 lines (was up to 137 lines).
- All v1 tests pass.
- New `tests/application/test_*.py` files cover the service-level behaviour previously tested via CLI tests.

---

### Task 1.1: Create application package skeleton

**Files:**
- Create: `firefliesclearer/application/__init__.py`
- Create: `tests/application/__init__.py`

- [ ] **Step 1: Create the package directory and empty `__init__.py`**

```python
# firefliesclearer/application/__init__.py
"""Application services — shared orchestration consumed by both CLI and web layers.

These services depend only on `core/` and `ports/` (never on Typer or FastAPI),
so they are reusable across presentation layers and trivially testable with the
existing fakes in `tests/fakes/`.
"""
```

- [ ] **Step 2: Create the corresponding test package init**

```python
# tests/application/__init__.py
```

- [ ] **Step 3: Verify tests still pass (sanity check)**

```bash
cd C:/GIT/FirefliesClearer
pytest -q
```

Expected: 123 passed, 1 skipped (the live-API contract test).

- [ ] **Step 4: Commit**

```bash
git checkout -b feat/v2/01-application-package
git add firefliesclearer/application/__init__.py tests/application/__init__.py
git commit -m "feat: scaffold application services package"
```

---

### Task 1.2: Extract `setup_service` from `init_cmd`

The setup service handles initial config writing + API ping. It will be reused by the v2 web setup wizard (Phase 3) and by the existing CLI `init` (which will be removed at the end of Phase 3 — but still works during Phases 1–2).

**Files:**
- Create: `firefliesclearer/application/setup_service.py`
- Create: `tests/application/test_setup_service.py`
- Modify: `firefliesclearer/cli/init_cmd.py` (delegate to service)

- [ ] **Step 1: Read the existing `init_cmd.py` to understand its current behaviour**

```bash
cat firefliesclearer/cli/init_cmd.py
```

Note the operations it does: prompts for API key, archive root, defaults; writes config TOML atomically; calls `getUser` ping. The service should encapsulate everything except the prompts.

- [ ] **Step 2: Write failing tests for the service**

```python
# tests/application/test_setup_service.py
"""Tests for SetupService — the orchestration behind the v1 init CLI command
and the v2 web setup wizard."""

from __future__ import annotations

from pathlib import Path

import pytest

from firefliesclearer.application.setup_service import (
    ConfigAlreadyExists,
    InvalidApiKey,
    SetupService,
    SetupValues,
)
from firefliesclearer.infra.config import Config, FirefliesConfig, ArchiveConfig, RunConfig
from tests.fakes.in_memory_repository import InMemoryMeetingRepository


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    return tmp_path / "archive"


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


@pytest.fixture
def repo() -> InMemoryMeetingRepository:
    return InMemoryMeetingRepository()


def test_verify_api_key_accepts_known_key(repo: InMemoryMeetingRepository):
    repo.set_user_email_for_key("ff_good", "oskar@example.com")
    svc = SetupService(repo_factory=lambda key: repo)

    email = svc.verify_api_key("ff_good")

    assert email == "oskar@example.com"


def test_verify_api_key_rejects_bad_key(repo: InMemoryMeetingRepository):
    svc = SetupService(repo_factory=lambda key: repo)

    with pytest.raises(InvalidApiKey):
        svc.verify_api_key("ff_bad")


def test_write_config_creates_atomic_file(
    archive_root: Path, config_path: Path, repo: InMemoryMeetingRepository
):
    svc = SetupService(repo_factory=lambda key: repo)
    repo.set_user_email_for_key("ff_good", "oskar@example.com")

    svc.write_config(
        config_path,
        SetupValues(
            api_key="ff_good",
            archive_root=archive_root,
            default_age_days=90,
            concurrency=3,
        ),
    )

    assert config_path.exists()
    text = config_path.read_text(encoding="utf-8")
    assert 'api_key = "ff_good"' in text
    assert str(archive_root) in text
    # Atomic write means no .tmp file should remain
    assert not config_path.with_suffix(".toml.tmp").exists()


def test_write_config_refuses_to_overwrite_existing(
    archive_root: Path, config_path: Path
):
    config_path.write_text('[fireflies]\napi_key = "old"\n', encoding="utf-8")
    svc = SetupService(repo_factory=lambda key: InMemoryMeetingRepository())

    with pytest.raises(ConfigAlreadyExists):
        svc.write_config(
            config_path,
            SetupValues(
                api_key="ff_new",
                archive_root=archive_root,
                default_age_days=90,
                concurrency=3,
            ),
        )


def test_write_config_force_overwrite_keeps_backup(
    archive_root: Path, config_path: Path
):
    config_path.write_text('[fireflies]\napi_key = "old"\n', encoding="utf-8")
    svc = SetupService(repo_factory=lambda key: InMemoryMeetingRepository())

    svc.write_config(
        config_path,
        SetupValues(
            api_key="ff_new",
            archive_root=archive_root,
            default_age_days=90,
            concurrency=3,
        ),
        force=True,
    )

    assert "ff_new" in config_path.read_text(encoding="utf-8")
    backup = config_path.with_suffix(".toml.bak")
    assert backup.exists()
    assert "ff_old" not in backup.read_text(encoding="utf-8") or "old" in backup.read_text(
        encoding="utf-8"
    )
```

Note: this test references `InMemoryMeetingRepository.set_user_email_for_key`. If the existing fake doesn't have this method, add it in step 4 below.

- [ ] **Step 3: Run the test to confirm it fails**

```bash
pytest tests/application/test_setup_service.py -v
```

Expected: ImportError (the service doesn't exist yet).

- [ ] **Step 4: Add `set_user_email_for_key` to the in-memory fake if missing**

Read `tests/fakes/in_memory_repository.py`. If it doesn't already support `getUser`-equivalent lookups, add:

```python
# tests/fakes/in_memory_repository.py — additions

def set_user_email_for_key(self, api_key: str, email: str) -> None:
    """Configure which API key returns which user email when ping_user() is called."""
    self._user_emails_by_key[api_key] = email

async def ping_user(self) -> str:
    """Return the email associated with the API key this repo was instantiated for."""
    if self._api_key not in self._user_emails_by_key:
        raise PermissionError("Unknown API key")
    return self._user_emails_by_key[self._api_key]
```

The `_user_emails_by_key` dict is a class-level shared dict (the fake constructor takes the API key); add `_api_key` field if not present. This may already be partially in place from v1; align with what's there.

- [ ] **Step 5: Implement `SetupService`**

```python
# firefliesclearer/application/setup_service.py
"""Orchestrates initial configuration writing and API key verification.

Used by:
- v1 CLI `firefliesclearer init` (during Phases 1–2; removed in Phase 3).
- v2 web setup wizard (Phase 3 onward).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from firefliesclearer.ports.meeting_repository import MeetingRepository


class InvalidApiKey(Exception):
    """The Fireflies API rejected the supplied API key."""


class ConfigAlreadyExists(Exception):
    """A config file already exists at the target path; pass force=True to overwrite."""


@dataclass(frozen=True)
class SetupValues:
    api_key: str
    archive_root: Path
    default_age_days: int
    concurrency: int


class SetupService:
    """Write configuration atomically and ping the Fireflies API.

    Stateless. Constructed with a `repo_factory` that maps an API key to a
    `MeetingRepository` instance — production passes the `FirefliesClient`
    constructor; tests pass a fake.
    """

    def __init__(
        self,
        repo_factory: Callable[[str], MeetingRepository],
    ) -> None:
        self._repo_factory = repo_factory

    def verify_api_key(self, api_key: str) -> str:
        """Return the email for the user the key authenticates as.

        Raises InvalidApiKey if the API rejects the key (any auth-class error).
        Other errors (network, etc.) propagate unchanged.
        """
        repo = self._repo_factory(api_key)
        try:
            return asyncio.run(repo.ping_user())
        except PermissionError as exc:
            raise InvalidApiKey(str(exc)) from exc

    def write_config(
        self,
        config_path: Path,
        values: SetupValues,
        *,
        force: bool = False,
    ) -> None:
        """Atomically write a TOML config to `config_path`.

        Raises ConfigAlreadyExists if the file exists and force=False.
        When force=True, the existing file is renamed to `<path>.bak` first.
        """
        if config_path.exists() and not force:
            raise ConfigAlreadyExists(str(config_path))

        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.exists() and force:
            backup = config_path.with_suffix(".toml.bak")
            config_path.replace(backup)

        payload: dict[str, object] = {
            "fireflies": {"api_key": values.api_key},
            "archive": {
                "root_dir": str(values.archive_root),
                "summary_format": "pdf",
            },
            "run": {
                "concurrency": values.concurrency,
                "delete_confirmation_threshold": 10,
            },
            "defaults": {
                "age_days": values.default_age_days,
            },
        }

        tmp = config_path.with_suffix(".toml.tmp")
        with tmp.open("wb") as f:
            tomli_w.dump(payload, f)
            f.flush()
        tmp.replace(config_path)
```

- [ ] **Step 6: Run tests; expect them to pass**

```bash
pytest tests/application/test_setup_service.py -v
```

Expected: all green.

- [ ] **Step 7: Refactor `init_cmd.py` to delegate to the service**

```python
# firefliesclearer/cli/init_cmd.py
"""`firefliesclearer init` — interactive first-run setup."""

from __future__ import annotations

from pathlib import Path

import typer
from platformdirs import user_config_dir, user_documents_dir

from firefliesclearer.application.setup_service import (
    ConfigAlreadyExists,
    InvalidApiKey,
    SetupService,
    SetupValues,
)
from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app
from firefliesclearer.infra.fireflies_client import FirefliesClient


@app.command()
def init(
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Interactive first-run setup."""
    config_path = config or (Path(user_config_dir("firefliesclearer")) / "config.toml")

    api_key = typer.prompt("Fireflies API key", hide_input=True)
    archive_root = typer.prompt(
        "Archive root directory",
        default=str(Path(user_documents_dir()) / "firefliesclearer-archive"),
    )
    age_days = typer.prompt("Default age threshold (days)", default=90, type=int)
    concurrency = typer.prompt("Concurrency", default=3, type=int)

    svc = SetupService(repo_factory=lambda key: FirefliesClient(api_key=key))

    try:
        email = svc.verify_api_key(api_key)
    except InvalidApiKey as exc:
        console.print(f"[red]API key rejected:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        svc.write_config(
            config_path,
            SetupValues(
                api_key=api_key,
                archive_root=Path(archive_root),
                default_age_days=age_days,
                concurrency=concurrency,
            ),
            force=force,
        )
    except ConfigAlreadyExists:
        console.print(f"[yellow]Config exists at {config_path}.[/yellow] Use --force to overwrite.")
        raise typer.Exit(code=1) from None

    console.print(f"[green]Configured.[/green] Connected as {email}.")
```

- [ ] **Step 8: Run the full test suite**

```bash
pytest -q
```

Expected: still 123 passed (existing tests) + the new application tests; 1 skipped.

- [ ] **Step 9: Commit**

```bash
git add firefliesclearer/application/setup_service.py firefliesclearer/cli/init_cmd.py tests/application/test_setup_service.py tests/fakes/in_memory_repository.py
git commit -m "refactor(application): extract SetupService from init_cmd"
```

---

### Task 1.3: Extract `scan_service` from `scan_cmd`

**Files:**
- Create: `firefliesclearer/application/scan_service.py`
- Create: `tests/application/test_scan_service.py`
- Modify: `firefliesclearer/cli/scan_cmd.py` (delegate)

- [ ] **Step 1: Write failing tests**

```python
# tests/application/test_scan_service.py
"""Tests for ScanService — converts filter primitives into a RuleEngine pass
and a structured selection result."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from firefliesclearer.application.scan_service import (
    ScanFilters,
    ScanResult,
    ScanService,
)
from firefliesclearer.core.models import Meeting
from tests.fakes.frozen_clock import FrozenClock
from tests.fakes.in_memory_repository import InMemoryMeetingRepository


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 4, 29, 10, 0, tzinfo=UTC)


@pytest.fixture
def repo(now: datetime) -> InMemoryMeetingRepository:
    repo = InMemoryMeetingRepository()
    repo.add(
        Meeting(
            meeting_id="m1",
            title="Old standup",
            meeting_date=now - timedelta(days=200),
            duration_minutes=10.0,
            host_email="oskar@example.com",
            participant_count=3,
            tags=(),
            transcript_url=None,
            audio_url="https://example.com/m1.mp3",
            source_url="https://app.fireflies.ai/view/m1",
        )
    )
    repo.add(
        Meeting(
            meeting_id="m2",
            title="Recent kickoff",
            meeting_date=now - timedelta(days=10),
            duration_minutes=45.0,
            host_email="alice@example.com",
            participant_count=8,
            tags=("kickoff",),
            transcript_url="https://example.com/m2.md",
            audio_url="https://example.com/m2.mp3",
            source_url="https://app.fireflies.ai/view/m2",
        )
    )
    return repo


@pytest.mark.asyncio
async def test_scan_with_age_filter_matches_old_meeting(
    repo: InMemoryMeetingRepository, now: datetime
):
    svc = ScanService(repo=repo, clock=FrozenClock(now))

    result = await svc.scan(ScanFilters(older_than_days=90))

    assert isinstance(result, ScanResult)
    assert {m.meeting.meeting_id for m in result.matches} == {"m1"}
    assert result.matches[0].matched_rules == ("older_than_days",)


@pytest.mark.asyncio
async def test_scan_with_no_filters_raises(repo: InMemoryMeetingRepository, now: datetime):
    svc = ScanService(repo=repo, clock=FrozenClock(now))

    with pytest.raises(ValueError, match="at least one filter"):
        await svc.scan(ScanFilters())


@pytest.mark.asyncio
async def test_scan_writes_selection_file(
    tmp_path: Path, repo: InMemoryMeetingRepository, now: datetime
):
    svc = ScanService(repo=repo, clock=FrozenClock(now))
    selections_dir = tmp_path / "selections"

    result = await svc.scan(ScanFilters(older_than_days=90))
    target = svc.write_selection_file(result, selections_dir)

    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert "m1" in text
    assert '"selected": true' in text


@pytest.mark.asyncio
async def test_scan_combined_filters_record_all_reasons(
    repo: InMemoryMeetingRepository, now: datetime
):
    svc = ScanService(repo=repo, clock=FrozenClock(now))

    result = await svc.scan(
        ScanFilters(older_than_days=90, duration_below_minutes=15.0)
    )

    assert {m.meeting.meeting_id for m in result.matches} == {"m1"}
    assert set(result.matches[0].matched_rules) == {
        "older_than_days",
        "duration_below_minutes",
    }
```

- [ ] **Step 2: Confirm tests fail**

```bash
pytest tests/application/test_scan_service.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `ScanService`**

```python
# firefliesclearer/application/scan_service.py
"""Builds a RuleEngine from filter primitives and runs a scan against the repo."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from firefliesclearer.core.models import Meeting
from firefliesclearer.core.rules import (
    DurationBelow,
    HasTag,
    HostEmail,
    NoTranscript,
    OlderThanDays,
    ParticipantsBelow,
    Rule,
    RuleEngine,
    TitleContains,
    TitleRegex,
)
from firefliesclearer.ports.clock import Clock
from firefliesclearer.ports.meeting_repository import MeetingFilter, MeetingRepository


@dataclass(frozen=True)
class ScanFilters:
    older_than_days: int | None = None
    duration_below_minutes: float | None = None
    no_transcript: bool = False
    title_contains: Sequence[str] = field(default_factory=tuple)
    title_regex: str | None = None
    host_email: Sequence[str] = field(default_factory=tuple)
    participants_below: int | None = None
    has_tag: Sequence[str] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        return not any(
            [
                self.older_than_days is not None,
                self.duration_below_minutes is not None,
                self.no_transcript,
                bool(self.title_contains),
                self.title_regex is not None,
                bool(self.host_email),
                self.participants_below is not None,
                bool(self.has_tag),
            ]
        )


@dataclass(frozen=True)
class ScanMatch:
    meeting: Meeting
    matched_rules: tuple[str, ...]


@dataclass(frozen=True)
class ScanResult:
    scan_id: str
    created_at: datetime
    filters: ScanFilters
    matches: tuple[ScanMatch, ...]

    @property
    def count(self) -> int:
        return len(self.matches)


class ScanService:
    def __init__(self, repo: MeetingRepository, clock: Clock) -> None:
        self._repo = repo
        self._clock = clock

    async def scan(self, filters: ScanFilters) -> ScanResult:
        if filters.is_empty():
            raise ValueError("Provide at least one filter.")

        engine = RuleEngine(self._build_rules(filters))
        now = self._clock.now()
        cutoff = (
            now - timedelta(days=filters.older_than_days)
            if filters.older_than_days is not None
            else None
        )
        matches: list[ScanMatch] = []
        async for meeting in self._repo.list_meetings(MeetingFilter(older_than=cutoff)):
            result = engine.evaluate(meeting, now=now)
            if result.matched:
                matches.append(ScanMatch(meeting=meeting, matched_rules=tuple(result.reasons)))

        scan_id = f"scan_{now.strftime('%Y%m%dT%H%M')}"
        return ScanResult(
            scan_id=scan_id,
            created_at=now,
            filters=filters,
            matches=tuple(matches),
        )

    def write_selection_file(self, result: ScanResult, selections_dir: Path) -> Path:
        selections_dir.mkdir(parents=True, exist_ok=True)
        target = selections_dir / f"{result.scan_id}.json"
        payload = {
            "scan_id": result.scan_id,
            "created_at": result.created_at.isoformat(),
            "filters_applied": {
                "older_than_days": result.filters.older_than_days,
                "duration_below_minutes": result.filters.duration_below_minutes,
                "no_transcript": result.filters.no_transcript,
                "title_contains": list(result.filters.title_contains),
                "title_regex": result.filters.title_regex,
                "host_email": list(result.filters.host_email),
                "participants_below": result.filters.participants_below,
                "has_tag": list(result.filters.has_tag),
            },
            "meetings": [
                {
                    "id": m.meeting.meeting_id,
                    "title": m.meeting.title,
                    "date": m.meeting.meeting_date.isoformat(),
                    "duration_min": m.meeting.duration_minutes,
                    "host": m.meeting.host_email,
                    "participants": m.meeting.participant_count,
                    "tags": list(m.meeting.tags),
                    "selected": True,
                    "matched_rules": list(m.matched_rules),
                }
                for m in result.matches
            ],
        }
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    @staticmethod
    def _build_rules(filters: ScanFilters) -> list[Rule]:
        rules: list[Rule] = []
        if filters.older_than_days is not None:
            rules.append(OlderThanDays(filters.older_than_days))
        if filters.duration_below_minutes is not None:
            rules.append(DurationBelow(filters.duration_below_minutes))
        if filters.no_transcript:
            rules.append(NoTranscript())
        if filters.title_contains:
            rules.append(TitleContains(list(filters.title_contains)))
        if filters.title_regex:
            rules.append(TitleRegex(filters.title_regex))
        if filters.host_email:
            rules.append(HostEmail(list(filters.host_email)))
        if filters.participants_below is not None:
            rules.append(ParticipantsBelow(filters.participants_below))
        if filters.has_tag:
            rules.append(HasTag(list(filters.has_tag)))
        return rules
```

If `NoTranscript` doesn't exist in `core/rules.py`, it does — the v1 spec mentions `no_transcript` as an auto-path rule. Verify; if missing, that's a separate bug to fix in core (out of scope here, but noted).

- [ ] **Step 4: Run tests**

```bash
pytest tests/application/test_scan_service.py -v
```

Expected: all green.

- [ ] **Step 5: Refactor `scan_cmd.py` to delegate**

```python
# firefliesclearer/cli/scan_cmd.py
"""`firefliesclearer scan` — list candidates, write selection file."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.table import Table

from firefliesclearer.application.scan_service import ScanFilters, ScanService
from firefliesclearer.cli import _common
from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app


@app.command()
def scan(
    older_than_days: int | None = typer.Option(None, "--older-than-days"),
    duration_below: float | None = typer.Option(None, "--duration-below"),
    no_transcript: bool = typer.Option(False, "--no-transcript"),
    title_contains: list[str] | None = typer.Option(None, "--title-contains"),  # noqa: B008
    title_regex: str | None = typer.Option(None, "--title-regex"),
    host_email: list[str] | None = typer.Option(None, "--host-email"),  # noqa: B008
    participants_below: int | None = typer.Option(None, "--participants-below"),
    has_tag: list[str] | None = typer.Option(None, "--has-tag"),  # noqa: B008
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """List meetings matching the given rules; writes a selection file."""
    deps = _common.build_deps(config_override=config)
    filters = ScanFilters(
        older_than_days=older_than_days,
        duration_below_minutes=duration_below,
        no_transcript=no_transcript,
        title_contains=tuple(title_contains or ()),
        title_regex=title_regex,
        host_email=tuple(host_email or ()),
        participants_below=participants_below,
        has_tag=tuple(has_tag or ()),
    )

    if filters.is_empty():
        raise typer.BadParameter("Provide at least one filter.")

    svc = ScanService(repo=deps.client, clock=deps.clock)
    result = asyncio.run(svc.scan(filters))

    table = Table(title=f"Candidates ({result.count})")
    for col in ("ID", "Date", "Title", "Dur", "Host", "Reasons"):
        table.add_column(col)
    for match in result.matches:
        m = match.meeting
        table.add_row(
            m.meeting_id,
            m.meeting_date.date().isoformat(),
            m.title[:60],
            f"{m.duration_minutes:.1f}",
            m.host_email,
            ", ".join(match.matched_rules),
        )
    console.print(table)

    target = svc.write_selection_file(result, deps.config.archive.root_dir / "selections")
    console.print(f"[green]Wrote selection:[/green] {target}")
```

- [ ] **Step 6: Run all tests including the existing CLI scan test**

```bash
pytest -q
```

Expected: still all green; the existing `tests/cli/test_scan_cmd.py` still passes (the CLI surface is the same).

- [ ] **Step 7: Commit**

```bash
git checkout -b feat/v2/02-scan-service
git add firefliesclearer/application/scan_service.py firefliesclearer/cli/scan_cmd.py tests/application/test_scan_service.py
git commit -m "refactor(application): extract ScanService from scan_cmd"
```

---

### Task 1.4: Extract `archive_service` from `archive_cmd`

**Files:**
- Create: `firefliesclearer/application/archive_service.py`
- Create: `tests/application/test_archive_service.py`
- Modify: `firefliesclearer/cli/archive_cmd.py`

**Same shape as Task 1.3.** Behaviour the service must encapsulate (read `archive_cmd.py` to confirm):
- Reads a selection JSON file.
- For each `selected: true` meeting, runs `Pipeline.process_one()` for the `archive` step (no purge).
- Returns/yields per-meeting results so the caller can render progress.

- [ ] **Step 1: Write failing tests**

```python
# tests/application/test_archive_service.py
"""Tests for ArchiveService — runs the archive half of the pipeline for each
selected meeting in a selection file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from firefliesclearer.application.archive_service import (
    ArchiveOutcome,
    ArchiveService,
    SelectionMissing,
)
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.core.archiver import Archiver
from firefliesclearer.core.pipeline import Pipeline
from firefliesclearer.ports.clock import Clock
from tests.fakes.fake_renderer import FakeSummaryRenderer
from tests.fakes.frozen_clock import FrozenClock
from tests.fakes.in_memory_repository import InMemoryMeetingRepository
from datetime import UTC, datetime, timedelta


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 4, 29, 10, 0, tzinfo=UTC)


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    return tmp_path / "archive"


@pytest.fixture
def manifest(archive_root: Path) -> Manifest:
    archive_root.mkdir()
    m = Manifest(archive_root / "manifest.db")
    m.connect()
    return m


@pytest.fixture
def repo(now: datetime) -> InMemoryMeetingRepository:
    r = InMemoryMeetingRepository()
    r.add(
        Meeting(
            meeting_id="m1",
            title="Test 1",
            meeting_date=now - timedelta(days=200),
            duration_minutes=2.0,
            host_email="oskar@example.com",
            participant_count=2,
            tags=(),
            transcript_url=None,
            audio_url="https://example.com/m1.mp3",
            source_url="https://app.fireflies.ai/view/m1",
        )
    )
    r.add(
        Meeting(
            meeting_id="m2",
            title="Test 2",
            meeting_date=now - timedelta(days=210),
            duration_minutes=3.0,
            host_email="oskar@example.com",
            participant_count=2,
            tags=(),
            transcript_url=None,
            audio_url="https://example.com/m2.mp3",
            source_url="https://app.fireflies.ai/view/m2",
        )
    )
    r.set_artifact_bytes("m1", audio=b"audio1", summary={"overview": "S1"}, transcript=b"# m1")
    r.set_artifact_bytes("m2", audio=b"audio2", summary={"overview": "S2"}, transcript=b"# m2")
    return r


@pytest.fixture
def selection_file(tmp_path: Path) -> Path:
    p = tmp_path / "scan.json"
    p.write_text(
        json.dumps(
            {
                "scan_id": "scan_1",
                "created_at": "2026-04-29T10:00:00+00:00",
                "filters_applied": {},
                "meetings": [
                    {"id": "m1", "title": "Test 1", "selected": True, "matched_rules": []},
                    {"id": "m2", "title": "Test 2", "selected": False, "matched_rules": []},
                ],
            }
        ),
        encoding="utf-8",
    )
    return p


@pytest.mark.asyncio
async def test_archive_processes_only_selected_meetings(
    selection_file: Path,
    archive_root: Path,
    repo: InMemoryMeetingRepository,
    manifest: Manifest,
    now: datetime,
):
    archiver = Archiver(archive_root, renderer=FakeSummaryRenderer(), clock=FrozenClock(now))
    pipeline = Pipeline(repo=repo, archiver=archiver, manifest=manifest, clock=FrozenClock(now))
    svc = ArchiveService(pipeline=pipeline)

    outcomes = [o async for o in svc.archive_selection(selection_file)]

    assert len(outcomes) == 1
    assert outcomes[0].meeting_id == "m1"
    assert outcomes[0].state is MeetingState.ARCHIVED


@pytest.mark.asyncio
async def test_archive_missing_selection_raises(
    archive_root: Path, repo: InMemoryMeetingRepository, manifest: Manifest, now: datetime
):
    archiver = Archiver(archive_root, renderer=FakeSummaryRenderer(), clock=FrozenClock(now))
    pipeline = Pipeline(repo=repo, archiver=archiver, manifest=manifest, clock=FrozenClock(now))
    svc = ArchiveService(pipeline=pipeline)

    with pytest.raises(SelectionMissing):
        async for _ in svc.archive_selection(Path("/no/such/file.json")):
            pass
```

- [ ] **Step 2: Confirm tests fail**

```bash
pytest tests/application/test_archive_service.py -v
```

- [ ] **Step 3: Implement `ArchiveService`**

```python
# firefliesclearer/application/archive_service.py
"""Runs the archive half of the pipeline for selected meetings."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from firefliesclearer.core.models import MeetingState
from firefliesclearer.core.pipeline import Pipeline


class SelectionMissing(FileNotFoundError):
    """The selection file does not exist."""


@dataclass(frozen=True)
class ArchiveOutcome:
    meeting_id: str
    title: str
    state: MeetingState
    error: str | None


class ArchiveService:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def archive_selection(self, selection_file: Path) -> AsyncIterator[ArchiveOutcome]:
        if not selection_file.exists():
            raise SelectionMissing(str(selection_file))

        payload = json.loads(selection_file.read_text(encoding="utf-8"))
        for entry in payload.get("meetings", []):
            if not entry.get("selected"):
                continue
            mid = entry["id"]
            title = entry.get("title", "")
            try:
                final_state = await self._pipeline.archive_one(meeting_id=mid)
                yield ArchiveOutcome(
                    meeting_id=mid, title=title, state=final_state, error=None
                )
            except Exception as exc:  # noqa: BLE001
                yield ArchiveOutcome(
                    meeting_id=mid,
                    title=title,
                    state=MeetingState.FAILED_FETCH,
                    error=str(exc),
                )

    async def archive_meeting_ids(self, meeting_ids: list[str]) -> AsyncIterator[ArchiveOutcome]:
        """Variant used by the web layer; takes IDs directly without a selection file."""
        for mid in meeting_ids:
            try:
                final_state = await self._pipeline.archive_one(meeting_id=mid)
                yield ArchiveOutcome(meeting_id=mid, title="", state=final_state, error=None)
            except Exception as exc:  # noqa: BLE001
                yield ArchiveOutcome(
                    meeting_id=mid,
                    title="",
                    state=MeetingState.FAILED_FETCH,
                    error=str(exc),
                )
```

Note: this assumes `Pipeline` exposes an `archive_one(meeting_id)` method. If it currently exposes only `process_one(meeting_id)` (which does archive + delete), split the responsibility:

- Add `archive_one(meeting_id)` to `Pipeline` that does steps up to and including the rename → state `archived`.
- Add `purge_one(meeting_id)` that verifies and deletes → state `deleted`.
- Keep `process_one(meeting_id)` calling both.

If you have to add these methods, do it as a separate small commit ("refactor(core): split Pipeline.process_one into archive_one + purge_one") with a unit test in `tests/core/test_pipeline.py` confirming:
- Each new method works in isolation.
- `process_one` still produces the same end-state (`deleted`).

- [ ] **Step 4: Run the new tests; expect green**

```bash
pytest tests/application/test_archive_service.py -v
```

- [ ] **Step 5: Refactor `archive_cmd.py` to delegate** (mirrors the pattern in Task 1.3 step 5; the CLI builds deps + selection path → calls `svc.archive_selection()` → renders Rich progress table).

- [ ] **Step 6: Full suite green**

```bash
pytest -q
```

- [ ] **Step 7: Commit**

```bash
git checkout -b feat/v2/03-archive-service
git add firefliesclearer/application/archive_service.py firefliesclearer/cli/archive_cmd.py tests/application/test_archive_service.py firefliesclearer/core/pipeline.py tests/core/test_pipeline.py
git commit -m "refactor(application): extract ArchiveService; split Pipeline.archive_one/purge_one"
```

---

### Task 1.5: Extract `purge_service` from `purge_cmd`

**Same shape as Task 1.4.** The purge service is symmetrical to ArchiveService:

```python
# firefliesclearer/application/purge_service.py
"""Verifies archive completeness and deletes from Fireflies for selected meetings."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from firefliesclearer.core.models import MeetingState
from firefliesclearer.core.pipeline import Pipeline


class SelectionMissing(FileNotFoundError):
    pass


@dataclass(frozen=True)
class PurgeOutcome:
    meeting_id: str
    title: str
    state: MeetingState
    error: str | None


class PurgeService:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def purge_selection(self, selection_file: Path) -> AsyncIterator[PurgeOutcome]:
        if not selection_file.exists():
            raise SelectionMissing(str(selection_file))

        payload = json.loads(selection_file.read_text(encoding="utf-8"))
        for entry in payload.get("meetings", []):
            if not entry.get("selected"):
                continue
            mid = entry["id"]
            try:
                final_state = await self._pipeline.purge_one(meeting_id=mid)
                yield PurgeOutcome(meeting_id=mid, title=entry.get("title", ""), state=final_state, error=None)
            except Exception as exc:  # noqa: BLE001
                yield PurgeOutcome(
                    meeting_id=mid,
                    title=entry.get("title", ""),
                    state=MeetingState.DELETED_FAILED,
                    error=str(exc),
                )

    async def purge_meeting_ids(self, meeting_ids: list[str]) -> AsyncIterator[PurgeOutcome]:
        for mid in meeting_ids:
            try:
                final_state = await self._pipeline.purge_one(meeting_id=mid)
                yield PurgeOutcome(meeting_id=mid, title="", state=final_state, error=None)
            except Exception as exc:  # noqa: BLE001
                yield PurgeOutcome(
                    meeting_id=mid,
                    title="",
                    state=MeetingState.DELETED_FAILED,
                    error=str(exc),
                )
```

Tests mirror `test_archive_service.py` with happy path + missing-selection error case + skip-unselected case.

- [ ] Steps 1–7 mirror Task 1.4. Commit:

```bash
git checkout -b feat/v2/04-purge-service
git commit -m "refactor(application): extract PurgeService from purge_cmd"
```

---

### Task 1.6: Extract `audit_service` from `status_cmd` + `history_cmd`

These two CLI commands both query the manifest. Combine them in one service with two methods.

```python
# firefliesclearer/application/audit_service.py
"""Read-only queries over the manifest for status and history views."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import MeetingState


@dataclass(frozen=True)
class StateSummary:
    counts_by_state: dict[MeetingState, int]
    last_run_at: datetime | None
    failed_meeting_ids: tuple[str, ...]
    last_errors: dict[str, str]


@dataclass(frozen=True)
class HistoryEntry:
    meeting_id: str
    title: str
    meeting_date: datetime
    state: MeetingState
    archived_at: datetime | None
    deleted_at: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class HistoryFilter:
    states: tuple[MeetingState, ...] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    title_contains: str | None = None
    limit: int = 50
    offset: int = 0


class AuditService:
    def __init__(self, manifest: Manifest) -> None:
        self._manifest = manifest

    def summary(self) -> StateSummary:
        counts = self._manifest.counts_by_state()
        last_run = self._manifest.last_state_change_at()
        failed_states = {
            MeetingState.FAILED_FETCH,
            MeetingState.FAILED_DOWNLOAD,
            MeetingState.FAILED_RENDER,
            MeetingState.FAILED_VERIFY,
            MeetingState.DELETED_FAILED,
        }
        failed_ids = self._manifest.meeting_ids_in_states(failed_states)
        last_errors = {mid: self._manifest.last_error_for(mid) for mid in failed_ids}
        return StateSummary(
            counts_by_state=counts,
            last_run_at=last_run,
            failed_meeting_ids=tuple(failed_ids),
            last_errors={k: v for k, v in last_errors.items() if v},
        )

    def history(self, filt: HistoryFilter) -> tuple[HistoryEntry, ...]:
        rows = self._manifest.query_history(
            states=list(filt.states) if filt.states else None,
            date_from=filt.date_from,
            date_to=filt.date_to,
            title_contains=filt.title_contains,
            limit=filt.limit,
            offset=filt.offset,
        )
        return tuple(
            HistoryEntry(
                meeting_id=r.meeting_id,
                title=r.title,
                meeting_date=r.meeting_date,
                state=r.state,
                archived_at=r.archived_at,
                deleted_at=r.deleted_at,
                last_error=r.last_error,
            )
            for r in rows
        )

    def history_count(self, filt: HistoryFilter) -> int:
        return self._manifest.count_history(
            states=list(filt.states) if filt.states else None,
            date_from=filt.date_from,
            date_to=filt.date_to,
            title_contains=filt.title_contains,
        )

    def state_log(self, meeting_id: str) -> Iterable[dict]:
        """Return every state-log row for a meeting (for the side panel)."""
        return self._manifest.state_log_for(meeting_id)
```

This requires the `Manifest` class to expose:
- `counts_by_state() -> dict[MeetingState, int]`
- `last_state_change_at() -> datetime | None`
- `meeting_ids_in_states(states) -> list[str]`
- `last_error_for(meeting_id) -> str | None`
- `query_history(...)` and `count_history(...)`
- `state_log_for(meeting_id) -> Iterable[dict]`

If any of these don't exist in v1's `Manifest`, add them (each as a small unit test in `tests/core/test_manifest.py` first). They're all read-only and trivial; no schema migration.

- [ ] **Steps 1–7 mirror Task 1.3.** Tests in `tests/application/test_audit_service.py` cover summary + history filtering.

Commit:

```bash
git checkout -b feat/v2/05-audit-service
git commit -m "refactor(application): extract AuditService from status/history_cmd"
```

---

### Task 1.7: Phase 1 verification & integration commit

- [ ] **Step 1: Full test suite green with coverage**

```bash
pytest --cov=firefliesclearer --cov-report=term-missing -q
```

Expected: ≥ 91% overall (no regression from v1); new application/* files at 100%.

- [ ] **Step 2: Type-check**

```bash
mypy firefliesclearer
```

Expected: clean.

- [ ] **Step 3: Lint + format**

```bash
ruff check firefliesclearer tests
ruff format --check firefliesclearer tests
```

- [ ] **Step 4: Open PR for the entire phase**

```bash
git checkout version/v2
git merge feat/v2/01-application-package feat/v2/02-scan-service feat/v2/03-archive-service feat/v2/04-purge-service feat/v2/05-audit-service
git push origin version/v2
gh pr create --base main --title "Phase 1: extract application services" --body "Mechanical refactor lifting orchestration out of CLI files into firefliesclearer/application/. No behaviour change; v1 tests pass unchanged."
```

(If your branch policy is one phase = one PR into `version/v2` rather than `main`, adjust `--base`.)

---

# Phase 2 — Web package skeleton + `serve` command + lifecycle + security

**Goal of phase:** `firefliesclearer serve` opens a browser to a "Hello, FirefliesClearer" page; closing the browser shuts down the server within ~60s; a session token + CSRF middleware are in place; no real route exists yet beyond the placeholder.

**Phase exit criteria:**
- `firefliesclearer serve` boots, picks a free port, prints the URL with a session token, opens the browser.
- A second `serve` invocation against the same archive root exits with the lockfile error.
- Closing the browser triggers shutdown within 60–75s; in-flight detection works (mock).
- A POST without CSRF returns 403; with CSRF returns 200.
- A request without a session token returns 401.

---

### Task 2.1: Add web dependencies and package skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `firefliesclearer/web/__init__.py`

- [ ] **Step 1: Add new dependencies to `pyproject.toml`**

```toml
# pyproject.toml [project] dependencies
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
    "httpx>=0.27",
    "pydantic>=2.7",
    "platformdirs>=4.2",
    "reportlab>=4.0",
    "tomli-w>=1.0",
    # v2 web UI
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
    "itsdangerous>=2.2",
    "sse-starlette>=2.1",
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
    # v2 test deps
    "selectolax>=0.3",
    "trio>=0.25",
]
```

(`trio` is only needed if any web test runs the FastAPI app under anyio's trio backend; it's optional. `selectolax` is a tiny, fast HTML parser used in route tests for CSS-selector assertions.)

- [ ] **Step 2: Add package-data so templates and static files ship in the wheel**

```toml
# pyproject.toml
[tool.hatch.build]
include = [
  "firefliesclearer/**",
  "firefliesclearer/web/templates/**",
  "firefliesclearer/web/static/**",
]
```

- [ ] **Step 3: Install the new deps in the dev environment**

```bash
pip install -e ".[dev]"
```

Expected: clean install.

- [ ] **Step 4: Create the web package**

```python
# firefliesclearer/web/__init__.py
"""Web UI — FastAPI + HTMX presentation layer (v2).

This package never imports from `firefliesclearer.cli` and depends only on
`firefliesclearer.application` for orchestration. See the v2 design spec
(docs/superpowers/specs/2026-04-29-firefliesclearer-v2-web-ui-design.md) for
the architectural rationale.
"""
```

- [ ] **Step 5: Smoke-test the imports**

```bash
python -c "import fastapi, uvicorn, jinja2, itsdangerous, sse_starlette; print('ok')"
```

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/v2/06-web-package-deps
git add pyproject.toml firefliesclearer/web/__init__.py
git commit -m "feat(web): add v2 web dependencies and package skeleton"
```

---

### Task 2.2: Single-instance lockfile

**Files:**
- Create: `firefliesclearer/web/lockfile.py`
- Create: `tests/web/__init__.py`, `tests/web/test_lockfile.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/web/test_lockfile.py
"""Tests for the single-instance lockfile."""

from __future__ import annotations

from pathlib import Path

import pytest

from firefliesclearer.web.lockfile import (
    AnotherInstanceRunning,
    LockFile,
)


def test_acquire_creates_lockfile(tmp_path: Path):
    lock = LockFile(tmp_path / ".serve.lock")
    with lock.acquire(url="http://127.0.0.1:54231"):
        assert (tmp_path / ".serve.lock").exists()
    assert not (tmp_path / ".serve.lock").exists()


def test_second_acquire_raises(tmp_path: Path):
    lock1 = LockFile(tmp_path / ".serve.lock")
    lock2 = LockFile(tmp_path / ".serve.lock")
    with lock1.acquire(url="http://127.0.0.1:54231"):
        with pytest.raises(AnotherInstanceRunning) as exc_info:
            with lock2.acquire(url="http://127.0.0.1:54232"):
                pass
        assert "http://127.0.0.1:54231" in str(exc_info.value)


def test_acquire_after_release_succeeds(tmp_path: Path):
    lock = LockFile(tmp_path / ".serve.lock")
    with lock.acquire(url="http://127.0.0.1:54231"):
        pass
    with lock.acquire(url="http://127.0.0.1:54232"):
        pass  # should not raise
```

- [ ] **Step 2: Run test, expect import error**

- [ ] **Step 3: Implement the lockfile**

```python
# firefliesclearer/web/lockfile.py
"""Single-instance enforcement via a platform-appropriate lockfile.

Uses fcntl.flock on POSIX, msvcrt.locking on Windows.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class AnotherInstanceRunning(RuntimeError):
    """Another `serve` process holds the lockfile."""


class LockFile:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    @contextmanager
    def acquire(self, *, url: str) -> Iterator[None]:
        existing_url = self._read_existing_url()
        try:
            self._fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise AnotherInstanceRunning(
                f"Cannot create lockfile at {self._path}: {exc}"
            ) from exc

        try:
            self._lock_exclusive_or_raise(existing_url)
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.ftruncate(self._fd, 0)
            os.write(self._fd, url.encode("utf-8"))
            yield
        finally:
            self._release()

    def _lock_exclusive_or_raise(self, existing_url: str | None) -> None:
        assert self._fd is not None
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise AnotherInstanceRunning(
                    f"Another instance is running. {self._hint(existing_url)}"
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AnotherInstanceRunning(
                    f"Another instance is running. {self._hint(existing_url)}"
                ) from exc

    def _read_existing_url(self) -> str | None:
        if not self._path.exists():
            return None
        try:
            return self._path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    @staticmethod
    def _hint(url: str | None) -> str:
        if url:
            return f"Open {url} or stop the running instance first."
        return "Stop the running instance first."

    def _release(self) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                try:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
```

- [ ] **Step 4: Run tests; expect green**

- [ ] **Step 5: Commit**

```bash
git checkout -b feat/v2/07-lockfile
git commit -m "feat(web): single-instance lockfile (cross-platform)"
```

---

### Task 2.3: Heartbeat tracker + lifecycle

**Files:**
- Create: `firefliesclearer/web/lifecycle.py`
- Create: `tests/web/test_lifecycle.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/web/test_lifecycle.py
"""Tests for HeartbeatTracker and the shutdown coordinator."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from firefliesclearer.web.lifecycle import HeartbeatTracker, ShutdownCoordinator
from tests.fakes.frozen_clock import FrozenClock


@pytest.fixture
def t0() -> datetime:
    return datetime(2026, 4, 29, 10, 0, tzinfo=UTC)


def test_first_seen_initialises_to_now(t0: datetime):
    clock = FrozenClock(t0)
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))

    assert tracker.last_seen() == t0
    assert not tracker.is_idle()


def test_ping_updates_last_seen(t0: datetime):
    clock = FrozenClock(t0)
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    clock.advance(timedelta(seconds=30))

    tracker.ping()

    assert tracker.last_seen() == t0 + timedelta(seconds=30)
    assert not tracker.is_idle()


def test_idle_after_threshold(t0: datetime):
    clock = FrozenClock(t0)
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    clock.advance(timedelta(seconds=61))

    assert tracker.is_idle()


@pytest.mark.asyncio
async def test_shutdown_coordinator_fires_when_idle_and_no_active_op(t0: datetime):
    clock = FrozenClock(t0)
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    coord = ShutdownCoordinator(
        tracker=tracker,
        is_active=lambda: False,
        clock=clock,
        poll_interval=timedelta(seconds=5),
    )

    fired = asyncio.Event()
    coord.on_shutdown_requested(lambda: fired.set())
    task = asyncio.create_task(coord.run())

    clock.advance(timedelta(seconds=70))
    await asyncio.sleep(0)
    coord.tick_now_for_test()

    await asyncio.wait_for(fired.wait(), timeout=1.0)
    coord.stop()
    await task


@pytest.mark.asyncio
async def test_shutdown_deferred_while_op_active(t0: datetime):
    clock = FrozenClock(t0)
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    active = True
    coord = ShutdownCoordinator(
        tracker=tracker,
        is_active=lambda: active,
        clock=clock,
        poll_interval=timedelta(seconds=5),
    )
    fired = asyncio.Event()
    coord.on_shutdown_requested(lambda: fired.set())
    task = asyncio.create_task(coord.run())

    clock.advance(timedelta(seconds=70))
    coord.tick_now_for_test()
    await asyncio.sleep(0.05)
    assert not fired.is_set()  # deferred

    active = False
    coord.tick_now_for_test()
    await asyncio.wait_for(fired.wait(), timeout=1.0)
    coord.stop()
    await task


@pytest.mark.asyncio
async def test_quit_button_requests_shutdown_immediately(t0: datetime):
    clock = FrozenClock(t0)
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    coord = ShutdownCoordinator(
        tracker=tracker,
        is_active=lambda: False,
        clock=clock,
        poll_interval=timedelta(seconds=5),
    )
    fired = asyncio.Event()
    coord.on_shutdown_requested(lambda: fired.set())
    task = asyncio.create_task(coord.run())

    coord.request_quit()
    coord.tick_now_for_test()

    await asyncio.wait_for(fired.wait(), timeout=1.0)
    coord.stop()
    await task
```

- [ ] **Step 2: Confirm failures**

- [ ] **Step 3: Implement the module**

```python
# firefliesclearer/web/lifecycle.py
"""Browser-driven server lifecycle: heartbeat + graceful shutdown.

The browser pings POST /_alive every 10 seconds. When pings stop for >60s and
no operation is in flight, ShutdownCoordinator fires its callback (uvicorn's
`Server.should_exit = True`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta

from firefliesclearer.ports.clock import Clock


class HeartbeatTracker:
    def __init__(self, clock: Clock, idle_threshold: timedelta) -> None:
        self._clock = clock
        self._threshold = idle_threshold
        self._last_seen = clock.now()

    def ping(self) -> None:
        self._last_seen = self._clock.now()

    def last_seen(self) -> datetime:
        return self._last_seen

    def is_idle(self) -> bool:
        return self._clock.now() - self._last_seen > self._threshold


class ShutdownCoordinator:
    def __init__(
        self,
        *,
        tracker: HeartbeatTracker,
        is_active: Callable[[], bool],
        clock: Clock,
        poll_interval: timedelta,
    ) -> None:
        self._tracker = tracker
        self._is_active = is_active
        self._clock = clock
        self._poll_interval = poll_interval
        self._on_shutdown: list[Callable[[], None]] = []
        self._stop = asyncio.Event()
        self._tick = asyncio.Event()
        self._quit_requested = False

    def on_shutdown_requested(self, cb: Callable[[], None]) -> None:
        self._on_shutdown.append(cb)

    def request_quit(self) -> None:
        self._quit_requested = True
        self._tick.set()

    def stop(self) -> None:
        self._stop.set()
        self._tick.set()

    def tick_now_for_test(self) -> None:
        self._tick.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._tick.wait(), timeout=self._poll_interval.total_seconds()
                )
            except TimeoutError:
                pass
            self._tick.clear()

            if self._stop.is_set():
                return

            should_shutdown = self._quit_requested or self._tracker.is_idle()
            if should_shutdown and not self._is_active():
                for cb in self._on_shutdown:
                    cb()
                return
```

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit**

```bash
git checkout -b feat/v2/08-lifecycle
git commit -m "feat(web): heartbeat tracker + shutdown coordinator"
```

---

### Task 2.4: Security — session token + CSRF

**Files:**
- Create: `firefliesclearer/web/security.py`
- Create: `tests/web/test_security.py`

- [ ] **Step 1: Failing tests**

```python
# tests/web/test_security.py
"""Tests for session-token + CSRF middleware."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from firefliesclearer.web.security import (
    SecurityConfig,
    install_security,
)


def make_app(token: str = "TOKEN") -> FastAPI:
    app = FastAPI()
    install_security(app, SecurityConfig(session_token=token, csrf_secret="csrf-secret"))

    @app.get("/safe")
    def safe():
        return {"ok": True}

    @app.post("/mutate")
    def mutate():
        return {"ok": True}

    return app


def test_get_without_token_returns_401():
    app = make_app()
    client = TestClient(app)
    r = client.get("/safe")
    assert r.status_code == 401


def test_get_with_query_token_sets_cookie_and_returns_200():
    app = make_app()
    client = TestClient(app)
    r = client.get("/safe?token=TOKEN")
    assert r.status_code == 200
    assert "ffc_session" in client.cookies


def test_get_with_cookie_works_after_initial_handshake():
    app = make_app()
    client = TestClient(app)
    client.get("/safe?token=TOKEN")
    r = client.get("/safe")
    assert r.status_code == 200


def test_post_without_csrf_returns_403():
    app = make_app()
    client = TestClient(app)
    client.get("/safe?token=TOKEN")
    r = client.post("/mutate")
    assert r.status_code == 403


def test_post_with_csrf_cookie_and_form_field_returns_200():
    app = make_app()
    client = TestClient(app)
    client.get("/safe?token=TOKEN")
    csrf_cookie = client.cookies.get("ffc_csrf")
    assert csrf_cookie
    r = client.post("/mutate", data={"_csrf": csrf_cookie})
    assert r.status_code == 200


def test_post_with_mismatched_csrf_returns_403():
    app = make_app()
    client = TestClient(app)
    client.get("/safe?token=TOKEN")
    r = client.post("/mutate", data={"_csrf": "wrong"})
    assert r.status_code == 403
```

- [ ] **Step 2: Confirm failures**

- [ ] **Step 3: Implement**

```python
# firefliesclearer/web/security.py
"""Session token + CSRF for the local web UI."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import FastAPI, Form, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.middleware.base import BaseHTTPMiddleware


SESSION_COOKIE = "ffc_session"
CSRF_COOKIE = "ffc_csrf"
CSRF_FIELD = "_csrf"


@dataclass(frozen=True)
class SecurityConfig:
    session_token: str
    csrf_secret: str


class SessionTokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/static/"):
            return await call_next(request)

        cookie = request.cookies.get(SESSION_COOKIE)
        query = request.query_params.get("token")
        valid = (cookie == self._token) or (query == self._token)
        if not valid:
            return Response(status_code=401, content="Session token required")

        response = await call_next(request)
        if cookie != self._token:
            response.set_cookie(
                SESSION_COOKIE,
                self._token,
                httponly=True,
                samesite="strict",
                max_age=24 * 60 * 60,
            )
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, app, *, secret: str) -> None:
        super().__init__(app)
        self._serializer = URLSafeSerializer(secret, salt="ffc-csrf")

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/static/"):
            return await call_next(request)

        if request.method not in self.SAFE_METHODS:
            cookie = request.cookies.get(CSRF_COOKIE)
            if not cookie:
                return Response(status_code=403, content="CSRF cookie missing")
            try:
                self._serializer.loads(cookie)
            except BadSignature:
                return Response(status_code=403, content="CSRF cookie invalid")
            form = await request.form()
            field = form.get(CSRF_FIELD)
            if field != cookie:
                return Response(status_code=403, content="CSRF mismatch")

        response = await call_next(request)
        if not request.cookies.get(CSRF_COOKIE):
            new_token = self._serializer.dumps(secrets.token_urlsafe(16))
            response.set_cookie(
                CSRF_COOKIE,
                new_token,
                httponly=False,  # JS reads it for fetch()
                samesite="strict",
                max_age=24 * 60 * 60,
            )
        return response


def install_security(app: FastAPI, config: SecurityConfig) -> None:
    app.add_middleware(CSRFMiddleware, secret=config.csrf_secret)
    app.add_middleware(SessionTokenMiddleware, token=config.session_token)
```

(Middleware ordering note: the outer middleware is added last but runs first. We want session check first, then CSRF — so add `SessionTokenMiddleware` last so it wraps `CSRFMiddleware`.)

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit**

```bash
git checkout -b feat/v2/09-security
git commit -m "feat(web): session token middleware + CSRF middleware"
```

---

### Task 2.5: FastAPI app factory + heartbeat & quit routes

**Files:**
- Create: `firefliesclearer/web/app.py`
- Create: `firefliesclearer/web/deps.py`
- Create: `firefliesclearer/web/routes/__init__.py`
- Create: `firefliesclearer/web/routes/_heartbeat.py`
- Create: `firefliesclearer/web/routes/_quit.py`
- Create: `firefliesclearer/web/templates/base.html`
- Create: `firefliesclearer/web/static/app.js`
- Create: `firefliesclearer/web/static/htmx.min.js` (vendored)
- Create: `firefliesclearer/web/static/styles.css` (placeholder for now)
- Create: `tests/web/conftest.py`
- Create: `tests/web/routes/__init__.py`
- Create: `tests/web/routes/test_heartbeat.py`

- [ ] **Step 1: Vendor HTMX**

Download `https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js` (or current stable) and save to `firefliesclearer/web/static/htmx.min.js`. Verify size < 100 KB.

```bash
curl -L -o firefliesclearer/web/static/htmx.min.js https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js
ls -lh firefliesclearer/web/static/htmx.min.js
```

- [ ] **Step 2: Write a stub `styles.css` for now (Tailwind compile is Task 9.x)**

```css
/* firefliesclearer/web/static/styles.css */
body { font-family: -apple-system, "Segoe UI", sans-serif; margin: 0; }
.sidebar { background: #0f172a; color: #cbd5e1; width: 200px; min-height: 100vh; padding: 16px; }
.main { padding: 24px; }
```

- [ ] **Step 3: Write `app.js`** (heartbeat ping + quit-button + side-panel/shift-select stubs)

```javascript
// firefliesclearer/web/static/app.js
(function () {
  function getCsrf() {
    const m = document.cookie.match(/ffc_csrf=([^;]+)/);
    return m ? m[1] : "";
  }

  // Heartbeat: POST /_alive every 10s using sendBeacon when possible.
  function ping() {
    const url = "/_alive";
    const csrf = getCsrf();
    if (navigator.sendBeacon) {
      const fd = new FormData();
      fd.append("_csrf", csrf);
      navigator.sendBeacon(url, fd);
    } else {
      fetch(url, { method: "POST", body: "_csrf=" + encodeURIComponent(csrf), headers: { "Content-Type": "application/x-www-form-urlencoded" } });
    }
  }
  window.addEventListener("load", ping);
  setInterval(ping, 10000);

  // Quit button.
  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-action='quit']");
    if (!btn) return;
    e.preventDefault();
    fetch("/_quit", { method: "POST", body: "_csrf=" + encodeURIComponent(getCsrf()), headers: { "Content-Type": "application/x-www-form-urlencoded" } });
    document.body.innerHTML = "<div style='padding:40px;text-align:center;font-family:sans-serif'>Server shutting down. You can close this tab.</div>";
  });

  // Side panel close on Esc.
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      const panel = document.querySelector(".side-panel.open");
      if (panel) panel.classList.remove("open");
    }
  });

  // Shift-click range select on tables with data-shift-select.
  document.addEventListener("click", function (e) {
    if (!e.shiftKey) return;
    const cb = e.target.closest("input[type='checkbox'][data-row-checkbox]");
    if (!cb) return;
    const table = cb.closest("table[data-shift-select]");
    if (!table) return;
    const all = Array.from(table.querySelectorAll("input[type='checkbox'][data-row-checkbox]"));
    const last = table.dataset.lastClickedIndex ? parseInt(table.dataset.lastClickedIndex, 10) : null;
    const idx = all.indexOf(cb);
    if (last !== null) {
      const [a, b] = [Math.min(last, idx), Math.max(last, idx)];
      for (let i = a; i <= b; i++) all[i].checked = cb.checked;
    }
    table.dataset.lastClickedIndex = idx;
  });
})();
```

- [ ] **Step 4: Write `base.html`**

```html
{# firefliesclearer/web/templates/base.html #}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}FirefliesClearer{% endblock %}</title>
  <link rel="stylesheet" href="/static/styles.css">
  <script src="/static/htmx.min.js" defer></script>
  <script src="/static/app.js" defer></script>
</head>
<body>
  {% block layout %}
  <div style="display: flex">
    <aside class="sidebar">
      <div><strong>FirefliesClearer</strong> <small>v{{ version }}</small></div>
      <nav>
        <a href="/" hx-get="/" hx-target="#page" hx-push-url="true">Dashboard</a><br>
        <a href="/cleanup" hx-get="/cleanup" hx-target="#page" hx-push-url="true">Cleanup</a><br>
        <a href="/presets" hx-get="/presets" hx-target="#page" hx-push-url="true">Presets</a><br>
        <a href="/history" hx-get="/history" hx-target="#page" hx-push-url="true">History</a><br>
        <a href="/settings" hx-get="/settings" hx-target="#page" hx-push-url="true">Settings</a>
      </nav>
      <hr>
      <div hx-get="/sidebar/status" hx-trigger="every 30s" hx-swap="innerHTML">{% block sidebar_status %}{% endblock %}</div>
      <button data-action="quit">Quit app</button>
    </aside>
    <main class="main">
      <div id="page">
        {% block page %}{% endblock %}
      </div>
    </main>
  </div>
  {% endblock %}
</body>
</html>
```

- [ ] **Step 5: Heartbeat & quit routes**

```python
# firefliesclearer/web/routes/_heartbeat.py
"""POST /_alive — keepalive ping from the browser."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from firefliesclearer.web.deps import get_tracker
from firefliesclearer.web.lifecycle import HeartbeatTracker

router = APIRouter()


@router.post("/_alive")
async def alive(tracker: HeartbeatTracker = Depends(get_tracker)) -> Response:
    tracker.ping()
    return Response(status_code=204)
```

```python
# firefliesclearer/web/routes/_quit.py
"""POST /_quit — explicit shutdown request from the sidebar."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from firefliesclearer.web.deps import get_shutdown_coordinator
from firefliesclearer.web.lifecycle import ShutdownCoordinator

router = APIRouter()


@router.post("/_quit")
async def quit_app(coord: ShutdownCoordinator = Depends(get_shutdown_coordinator)) -> Response:
    coord.request_quit()
    return Response(status_code=204)
```

- [ ] **Step 6: Deps providers**

```python
# firefliesclearer/web/deps.py
"""FastAPI Depends() providers — produce request-scoped (and app-scoped) services.

Concrete instances are bound at app creation time via app.state.* and looked up
per request here.
"""

from __future__ import annotations

from fastapi import Request

from firefliesclearer.web.lifecycle import HeartbeatTracker, ShutdownCoordinator


def get_tracker(request: Request) -> HeartbeatTracker:
    return request.app.state.tracker


def get_shutdown_coordinator(request: Request) -> ShutdownCoordinator:
    return request.app.state.shutdown_coordinator
```

- [ ] **Step 7: App factory**

```python
# firefliesclearer/web/app.py
"""FastAPI app factory."""

from __future__ import annotations

import secrets
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from firefliesclearer import __version__
from firefliesclearer.infra.system_clock import SystemClock
from firefliesclearer.ports.clock import Clock
from firefliesclearer.web.lifecycle import HeartbeatTracker, ShutdownCoordinator
from firefliesclearer.web.routes import _heartbeat, _quit
from firefliesclearer.web.security import SecurityConfig, install_security


WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def create_app(
    *,
    session_token: str | None = None,
    csrf_secret: str | None = None,
    clock: Clock | None = None,
    is_active_callable=lambda: False,
) -> FastAPI:
    """Build the FastAPI app. Caller wires services into app.state.*."""
    clock = clock or SystemClock()
    session_token = session_token or secrets.token_urlsafe(24)
    csrf_secret = csrf_secret or secrets.token_urlsafe(32)

    app = FastAPI(title="FirefliesClearer")

    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    shutdown = ShutdownCoordinator(
        tracker=tracker,
        is_active=is_active_callable,
        clock=clock,
        poll_interval=timedelta(seconds=5),
    )

    app.state.tracker = tracker
    app.state.shutdown_coordinator = shutdown
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.version = __version__
    app.state.session_token = session_token

    install_security(app, SecurityConfig(session_token=session_token, csrf_secret=csrf_secret))

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(_heartbeat.router)
    app.include_router(_quit.router)

    @app.get("/")
    def home(request):
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "version": request.app.state.version},
        )

    return app
```

- [ ] **Step 8: Test fixture**

```python
# tests/web/conftest.py
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from firefliesclearer.web.app import create_app


@pytest.fixture
def web_token() -> str:
    return "TESTTOKEN"


@pytest.fixture
def app(web_token: str):
    return create_app(session_token=web_token, csrf_secret="csrfsecret")


@pytest.fixture
def client(app, web_token: str) -> TestClient:
    c = TestClient(app)
    # Establish session by hitting any GET with token
    c.get("/?token=" + web_token)
    return c
```

- [ ] **Step 9: Heartbeat route test**

```python
# tests/web/routes/test_heartbeat.py
def test_alive_pings_tracker(app, client):
    csrf = client.cookies.get("ffc_csrf")
    initial = app.state.tracker.last_seen()

    r = client.post("/_alive", data={"_csrf": csrf})

    assert r.status_code == 204
    # tracker.last_seen() should be >= initial; with SystemClock it bumps forward.
    assert app.state.tracker.last_seen() >= initial


def test_quit_requests_shutdown(app, client):
    csrf = client.cookies.get("ffc_csrf")
    r = client.post("/_quit", data={"_csrf": csrf})
    assert r.status_code == 204
    # Internal flag set
    assert app.state.shutdown_coordinator._quit_requested is True  # noqa: SLF001
```

- [ ] **Step 10: Run all web tests**

```bash
pytest tests/web -v
```

- [ ] **Step 11: Commit**

```bash
git checkout -b feat/v2/10-app-factory
git commit -m "feat(web): app factory, base template, heartbeat & quit routes"
```

---

### Task 2.6: `firefliesclearer serve` CLI command

**Files:**
- Create: `firefliesclearer/cli/serve_cmd.py`
- Modify: `firefliesclearer/cli/app.py` (register serve)
- Create: `tests/cli/test_serve_cmd.py`

- [ ] **Step 1: Failing test (CLI smoke)**

```python
# tests/cli/test_serve_cmd.py
"""Smoke test for the `firefliesclearer serve` command — argv parsing only.

The actual server boot is integration-tested in tests/web/e2e."""

from __future__ import annotations

from typer.testing import CliRunner

from firefliesclearer.cli.app import app


def test_serve_help_lists_options():
    runner = CliRunner()
    r = runner.invoke(app, ["serve", "--help"])
    assert r.exit_code == 0
    assert "--host" in r.output
    assert "--port" in r.output
    assert "--no-open" in r.output
```

- [ ] **Step 2: Confirm failure (no `serve` command yet)**

- [ ] **Step 3: Implement**

```python
# firefliesclearer/cli/serve_cmd.py
"""`firefliesclearer serve` — launch the local web UI."""

from __future__ import annotations

import secrets
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import typer
import uvicorn

from firefliesclearer.cli import _common
from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app
from firefliesclearer.web.app import create_app
from firefliesclearer.web.lockfile import AnotherInstanceRunning, LockFile


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(0, "--port", help="0 = OS picks a free port"),
    no_open: bool = typer.Option(False, "--no-open"),
    i_know_what_im_doing: bool = typer.Option(
        False, "--i-know-what-im-doing", help="Required to bind a non-loopback host."
    ),
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """Launch the local web UI."""
    if host != "127.0.0.1" and not i_know_what_im_doing:
        console.print(
            "[red]Refusing to bind a non-loopback host without --i-know-what-im-doing.[/red]"
        )
        raise typer.Exit(code=2)

    deps = _common.build_deps(config_override=config)

    chosen_port = port or _pick_free_port(host)
    session_token = secrets.token_urlsafe(24)
    url = f"http://{host}:{chosen_port}/?token={session_token}"

    fastapi_app = create_app(
        session_token=session_token,
        csrf_secret=secrets.token_urlsafe(32),
    )
    fastapi_app.state.deps = deps  # for routes that need config/services

    lockfile = LockFile(deps.config.archive.root_dir / ".serve.lock")
    try:
        with lockfile.acquire(url=url.split("?", 1)[0]):
            console.print(f"[green]→ FirefliesClearer running at[/green] {url}")
            if not no_open:
                threading.Timer(0.5, lambda: webbrowser.open(url)).start()

            uconfig = uvicorn.Config(fastapi_app, host=host, port=chosen_port, log_level="warning")
            server = uvicorn.Server(uconfig)
            fastapi_app.state.shutdown_coordinator.on_shutdown_requested(
                lambda: setattr(server, "should_exit", True)
            )
            server.run()
    except AnotherInstanceRunning as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


def _pick_free_port(host: str) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    return port
```

- [ ] **Step 4: Register the command in `cli/app.py`**

```python
# firefliesclearer/cli/app.py — modify the side-effect import block:
from firefliesclearer.cli import (  # noqa: E402,F401
    archive_cmd,
    history_cmd,
    init_cmd,        # still here in Phase 2; removed in Phase 3
    purge_cmd,
    run_cmd,
    scan_cmd,
    serve_cmd,
    status_cmd,
)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/cli/test_serve_cmd.py -v
```

- [ ] **Step 6: Manual smoke**

```bash
firefliesclearer serve --no-open --port 7777
```

In another terminal:

```bash
curl -i "http://127.0.0.1:7777/?token=<paste from stdout>"
```

Expected: 200 with the base.html shell.

`Ctrl-C` to stop. Then:

```bash
firefliesclearer serve --no-open --port 7777 &
sleep 2
firefliesclearer serve --no-open --port 7778
# Expected: "Another instance is running. Open http://127.0.0.1:7777 ..."
kill %1
```

- [ ] **Step 7: Commit**

```bash
git checkout -b feat/v2/11-serve-cmd
git commit -m "feat(cli): firefliesclearer serve command + lifecycle wiring"
```

---

### Task 2.7: Phase 2 verification

- [ ] **Step 1:** `pytest -q` — green.
- [ ] **Step 2:** `mypy firefliesclearer/web` — clean.
- [ ] **Step 3:** `ruff check` and `ruff format --check` — clean.
- [ ] **Step 4:** Phase merge into `version/v2`.

```bash
git checkout version/v2
git merge feat/v2/06-web-package-deps feat/v2/07-lockfile feat/v2/08-lifecycle feat/v2/09-security feat/v2/10-app-factory feat/v2/11-serve-cmd
git push
```

---

# Phase 3 — Setup wizard

**Goal of phase:** `firefliesclearer serve` on a clean machine redirects to a 4-step setup wizard. Completing it writes `config.toml` atomically. The CLI `init` command is removed; an attempt to invoke it prints a redirect message.

**Phase exit criteria:**
- All routes redirect to `/setup/welcome` when no config is found.
- The 4 wizard pages render correctly and form posts validate inputs.
- API key check uses `setup_service.verify_api_key` and shows inline errors for bad keys.
- A successful submit produces a valid `config.toml` and lands the user at `/`.
- `firefliesclearer init` is removed; running it prints "use `serve` instead" and exits 0.

---

### Task 3.1: Server-side wizard session storage

The setup wizard accumulates form values across pages. We need an in-memory keyed-by-session-cookie store.

**Files:**
- Create: `firefliesclearer/web/sessions.py`
- Create: `tests/web/test_sessions.py`

- [ ] **Step 1: Failing tests**

```python
# tests/web/test_sessions.py
from __future__ import annotations

from firefliesclearer.web.sessions import SessionStore


def test_get_returns_empty_dict_for_new_session():
    store = SessionStore()
    assert store.get("sid1") == {}


def test_set_and_get_roundtrip():
    store = SessionStore()
    store.set("sid1", {"step": 2, "api_key": "ff_x"})
    assert store.get("sid1") == {"step": 2, "api_key": "ff_x"}


def test_update_merges():
    store = SessionStore()
    store.set("sid1", {"step": 1})
    store.update("sid1", {"api_key": "ff_x"})
    assert store.get("sid1") == {"step": 1, "api_key": "ff_x"}


def test_delete_removes_session():
    store = SessionStore()
    store.set("sid1", {"step": 1})
    store.delete("sid1")
    assert store.get("sid1") == {}
```

- [ ] **Step 2: Implement**

```python
# firefliesclearer/web/sessions.py
"""In-process session store keyed by the session cookie value.

Thread-safe enough for FastAPI's single-process deployment; not multi-worker
compatible (we run one uvicorn worker by design).
"""

from __future__ import annotations

import threading
from typing import Any


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, sid: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._data.get(sid, {}))

    def set(self, sid: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._data[sid] = dict(payload)

    def update(self, sid: str, patch: dict[str, Any]) -> None:
        with self._lock:
            current = dict(self._data.get(sid, {}))
            current.update(patch)
            self._data[sid] = current

    def delete(self, sid: str) -> None:
        with self._lock:
            self._data.pop(sid, None)
```

- [ ] **Step 3: Tests pass; commit**

```bash
git checkout -b feat/v2/12-sessions
git commit -m "feat(web): in-process server-side session store"
```

---

### Task 3.2: Setup wizard routes + templates

**Files:**
- Create: `firefliesclearer/web/routes/setup.py`
- Create: `firefliesclearer/web/templates/setup/welcome.html`, `api_key.html`, `archive_root.html`, `defaults.html`
- Create: `tests/web/routes/test_setup.py`
- Modify: `firefliesclearer/web/app.py` (register router; redirect-to-setup middleware)

- [ ] **Step 1: Failing route tests**

```python
# tests/web/routes/test_setup.py
"""Tests for the first-run setup wizard."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from firefliesclearer.web.app import create_app
from tests.fakes.in_memory_repository import InMemoryMeetingRepository


@pytest.fixture
def app_no_config(tmp_path: Path):
    repo = InMemoryMeetingRepository()
    repo.set_user_email_for_key("ff_good", "oskar@example.com")
    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=tmp_path / "config.toml",
        repo_factory=lambda key: repo,
    )
    return app


@pytest.fixture
def client(app_no_config) -> TestClient:
    c = TestClient(app_no_config)
    c.get("/?token=T", follow_redirects=False)
    return c


def test_root_redirects_to_setup_when_no_config(client: TestClient):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/setup/welcome" in r.headers["location"]


def test_setup_welcome_renders(client: TestClient):
    r = client.get("/setup/welcome")
    assert r.status_code == 200
    assert b"FirefliesClearer needs three things" in r.content


def test_api_key_step_rejects_bad_key(client: TestClient):
    csrf = client.cookies["ffc_csrf"]
    r = client.post(
        "/setup/api-key",
        data={"_csrf": csrf, "api_key": "ff_bad"},
        follow_redirects=False,
    )
    assert r.status_code == 200  # re-renders form with error
    assert b"rejected" in r.content


def test_api_key_step_accepts_good_key_and_advances(client: TestClient):
    csrf = client.cookies["ffc_csrf"]
    r = client.post(
        "/setup/api-key",
        data={"_csrf": csrf, "api_key": "ff_good"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303, 307)
    assert "/setup/archive-root" in r.headers["location"]


def test_finish_writes_config_and_redirects_home(
    client: TestClient, app_no_config, tmp_path: Path
):
    csrf = client.cookies["ffc_csrf"]
    client.post("/setup/api-key", data={"_csrf": csrf, "api_key": "ff_good"})
    client.post(
        "/setup/archive-root",
        data={"_csrf": csrf, "archive_root": str(tmp_path / "archive"), "create": "yes"},
    )
    r = client.post(
        "/setup/defaults",
        data={"_csrf": csrf, "age_days": "90", "concurrency": "3"},
        follow_redirects=False,
    )

    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/"
    assert (tmp_path / "config.toml").exists()
```

- [ ] **Step 2: Implement the redirect-to-setup logic and templates**

Update `create_app` to accept `config_path` and `repo_factory`:

```python
# firefliesclearer/web/app.py — additions

def create_app(
    *,
    session_token: str | None = None,
    csrf_secret: str | None = None,
    clock: Clock | None = None,
    is_active_callable=lambda: False,
    config_path: Path | None = None,
    repo_factory=None,
) -> FastAPI:
    ...
    app.state.config_path = config_path
    app.state.repo_factory = repo_factory
    app.state.session_store = SessionStore()
    ...
    app.include_router(setup.router)
    ...

    @app.middleware("http")
    async def redirect_to_setup(request, call_next):
        if request.url.path.startswith(("/setup", "/static", "/_alive", "/_quit")):
            return await call_next(request)
        if app.state.config_path and app.state.config_path.exists():
            return await call_next(request)
        return RedirectResponse("/setup/welcome", status_code=303)

    return app
```

Then write `firefliesclearer/web/routes/setup.py` (route handlers) and the four templates. (Template content kept short here for brevity; produce sensible HTML using the CSS classes from the design spec section 3.4. Each template extends `base.html` but overrides `{% block layout %}` to render a centered card with no sidebar, per spec § 4.4.)

```python
# firefliesclearer/web/routes/setup.py — full skeleton

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from firefliesclearer.application.setup_service import (
    InvalidApiKey,
    SetupService,
    SetupValues,
)

router = APIRouter(prefix="/setup")


def _sid(request: Request) -> str:
    return request.cookies.get("ffc_session", "")


def _store(request: Request):
    return request.app.state.session_store


def _service(request: Request) -> SetupService:
    return SetupService(repo_factory=request.app.state.repo_factory)


@router.get("/welcome")
async def welcome(request: Request):
    return request.app.state.templates.TemplateResponse(
        "setup/welcome.html", {"request": request}
    )


@router.get("/api-key")
async def api_key_form(request: Request, error: str | None = None):
    return request.app.state.templates.TemplateResponse(
        "setup/api_key.html", {"request": request, "error": error}
    )


@router.post("/api-key")
async def api_key_submit(
    request: Request,
    api_key: str = Form(...),
):
    svc = _service(request)
    try:
        email = svc.verify_api_key(api_key)
    except InvalidApiKey as exc:
        return request.app.state.templates.TemplateResponse(
            "setup/api_key.html",
            {"request": request, "error": f"Fireflies rejected this key: {exc}"},
        )
    _store(request).update(_sid(request), {"api_key": api_key, "email": email})
    return RedirectResponse("/setup/archive-root", status_code=303)


@router.get("/archive-root")
async def archive_root_form(request: Request, error: str | None = None):
    from platformdirs import user_documents_dir

    default_root = Path(user_documents_dir()) / "firefliesclearer-archive"
    return request.app.state.templates.TemplateResponse(
        "setup/archive_root.html",
        {"request": request, "default_root": default_root, "error": error},
    )


@router.post("/archive-root")
async def archive_root_submit(
    request: Request,
    archive_root: str = Form(...),
    create: str = Form(""),
):
    p = Path(archive_root).expanduser()
    if not p.exists():
        if create != "yes":
            return request.app.state.templates.TemplateResponse(
                "setup/archive_root.html",
                {
                    "request": request,
                    "default_root": p,
                    "error": f"Folder does not exist. Tick 'Create this folder' to make it.",
                },
            )
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return request.app.state.templates.TemplateResponse(
                "setup/archive_root.html",
                {"request": request, "default_root": p, "error": f"Cannot create: {exc}"},
            )
    if not p.is_dir():
        return request.app.state.templates.TemplateResponse(
            "setup/archive_root.html",
            {"request": request, "default_root": p, "error": "Path is not a directory."},
        )
    if not _is_writable(p):
        return request.app.state.templates.TemplateResponse(
            "setup/archive_root.html",
            {"request": request, "default_root": p, "error": "Folder is not writable."},
        )

    _store(request).update(_sid(request), {"archive_root": str(p)})
    return RedirectResponse("/setup/defaults", status_code=303)


@router.get("/defaults")
async def defaults_form(request: Request):
    return request.app.state.templates.TemplateResponse(
        "setup/defaults.html", {"request": request}
    )


@router.post("/defaults")
async def defaults_submit(
    request: Request,
    age_days: int = Form(90),
    concurrency: int = Form(3),
):
    sid = _sid(request)
    state = _store(request).get(sid)
    api_key = state.get("api_key")
    archive_root = state.get("archive_root")
    if not api_key or not archive_root:
        raise HTTPException(status_code=400, detail="Setup state lost. Restart from welcome.")

    svc = _service(request)
    config_path: Path = request.app.state.config_path
    svc.write_config(
        config_path,
        SetupValues(
            api_key=api_key,
            archive_root=Path(archive_root),
            default_age_days=age_days,
            concurrency=concurrency,
        ),
        force=False,
    )
    _store(request).delete(sid)
    return RedirectResponse("/", status_code=303)


def _is_writable(p: Path) -> bool:
    test = p / ".ffc_write_test"
    try:
        test.write_text("ok")
        test.unlink()
        return True
    except OSError:
        return False
```

- [ ] **Step 3: Tests pass**

```bash
pytest tests/web/routes/test_setup.py -v
```

- [ ] **Step 4: Commit**

```bash
git checkout -b feat/v2/13-setup-wizard
git commit -m "feat(web): first-run setup wizard with 4 steps + redirect-to-setup middleware"
```

---

### Task 3.3: Remove `init` CLI command + update tests

**Files:**
- Delete: `firefliesclearer/cli/init_cmd.py`
- Delete: `tests/cli/test_init_cmd.py`
- Modify: `firefliesclearer/cli/app.py` (remove `init_cmd` import)
- Modify: `firefliesclearer/cli/_common.py` or `app.py` — handle stale `init` invocation gracefully

Approach: replace the deleted `init_cmd.py` with a tiny stub that registers a friendly-error command:

- [ ] **Step 1: Replace `init_cmd.py` with the stub**

```python
# firefliesclearer/cli/init_cmd.py
"""Deprecated stub — `init` was replaced by the v2 web setup wizard."""

from __future__ import annotations

import typer

from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app


@app.command()
def init() -> None:
    """[REMOVED in v2] Use `firefliesclearer serve` instead."""
    console.print(
        "[yellow]firefliesclearer init has been replaced by the web setup wizard.[/yellow]\n"
        "Run [bold]firefliesclearer serve[/bold] instead."
    )
    raise typer.Exit(code=0)
```

- [ ] **Step 2: Replace `tests/cli/test_init_cmd.py` with a test for the stub**

```python
# tests/cli/test_init_cmd.py
from typer.testing import CliRunner

from firefliesclearer.cli.app import app


def test_init_prints_redirect_and_exits_zero():
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0
    assert "serve" in r.output
```

- [ ] **Step 3: Tests pass; commit**

```bash
git checkout -b feat/v2/14-init-removal
git add firefliesclearer/cli/init_cmd.py tests/cli/test_init_cmd.py
git commit -m "feat(cli): replace init command with redirect-to-serve stub"
```

---

### Task 3.4: Phase 3 verification

- [ ] **Step 1:** `pytest -q` — green.
- [ ] **Step 2:** Manual smoke against a clean tmp config: `firefliesclearer --config /tmp/fcc.toml serve --no-open --port 7777`, hit the URL, walk through the wizard, verify file is created.
- [ ] **Step 3:** Phase merge into `version/v2`.

---

# Phase 4 — Dashboard + sidebar shell

**Goal of phase:** the home page (`/`) renders a real Dashboard with state count cards, last activity (read from manifest's state_log), needs-attention list (failed meetings). Sidebar status fragment polls correctly. Retry buttons exist but are wired in Phase 5 (when the operation registry exists).

**Phase exit criteria:**
- `/` renders the Dashboard with cards populated from a fresh manifest.
- `/sidebar/status` polling endpoint returns the connection indicator + counts.
- Empty states render correctly when manifest is empty.
- Tests cover all three Dashboard sections.

---

### Task 4.1: Dashboard route and template

**Files:**
- Create: `firefliesclearer/web/routes/dashboard.py`
- Create: `firefliesclearer/web/templates/dashboard.html`
- Create: `firefliesclearer/web/templates/partials/state_counts.html`
- Create: `firefliesclearer/web/templates/partials/last_activity.html`
- Create: `firefliesclearer/web/templates/partials/needs_attention.html`
- Create: `firefliesclearer/web/templates/partials/sidebar_status.html`
- Create: `tests/web/routes/test_dashboard.py`

- [ ] **Step 1: Failing tests**

```python
# tests/web/routes/test_dashboard.py
"""Tests for the Dashboard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from firefliesclearer.core.models import MeetingState


@pytest.fixture
def configured_client(configured_app) -> TestClient:
    """A client where setup is already complete (fixture in conftest.py)."""
    c = TestClient(configured_app)
    c.get("/?token=T")
    return c


def test_dashboard_renders_with_zero_counts(configured_client):
    r = configured_client.get("/")
    assert r.status_code == 200
    doc = HTMLParser(r.text)
    cards = doc.css(".state-count-card")
    assert len(cards) == 4
    # Empty manifest → all zero
    for card in cards:
        assert "0" in card.text()


def test_dashboard_shows_failed_count_when_failures_exist(
    configured_client, configured_app
):
    manifest = configured_app.state.deps.manifest
    manifest.transition("m1", MeetingState.PENDING, details={})
    manifest.transition(
        "m1", MeetingState.FAILED_DOWNLOAD, details={"error": "Reset by peer"}
    )

    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    failed_card = doc.css_first("[data-state='failed']")
    assert "1" in failed_card.text()


def test_needs_attention_lists_failed_meetings(configured_client, configured_app):
    manifest = configured_app.state.deps.manifest
    # Insert a failed meeting via transition pair
    manifest.transition("m1", MeetingState.PENDING, details={"title": "Test Standup"})
    manifest.transition(
        "m1", MeetingState.FAILED_DOWNLOAD, details={"error": "Reset by peer"}
    )

    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    rows = doc.css(".needs-attention-row")
    assert len(rows) == 1
    assert "Test Standup" in rows[0].text()
    assert "Reset by peer" in rows[0].text()


def test_sidebar_status_renders():
    # via configured_client
    pass


def test_dashboard_empty_state_when_no_failures(configured_client):
    r = configured_client.get("/")
    doc = HTMLParser(r.text)
    empty = doc.css_first(".needs-attention-empty")
    assert empty is not None
    assert "All clear" in empty.text()
```

`configured_app` fixture in `tests/web/conftest.py`: builds an app with a real (in-tmp) config file present, real Manifest, fake repo. Add it now.

- [ ] **Step 2: Add `configured_app` fixture to `tests/web/conftest.py`**

```python
# tests/web/conftest.py — additions

@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    p = tmp_path / "archive"
    p.mkdir()
    return p


@pytest.fixture
def configured_app(tmp_path: Path, archive_root: Path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive_root.as_posix()}"
summary_format = "pdf"
[run]
concurrency = 3
delete_confirmation_threshold = 10
""",
        encoding="utf-8",
    )
    repo = InMemoryMeetingRepository()
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    manifest = Manifest(archive_root / "manifest.db")
    manifest.connect()
    deps = SimpleNamespace(
        config=load_config(config_path),
        manifest=manifest,
        client=repo,
        clock=SystemClock(),
    )
    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=config_path,
        repo_factory=lambda key: repo,
    )
    app.state.deps = deps
    return app
```

- [ ] **Step 3: Implement the route + templates**

```python
# firefliesclearer/web/routes/dashboard.py
from __future__ import annotations

from fastapi import APIRouter, Request

from firefliesclearer.application.audit_service import AuditService

router = APIRouter()


@router.get("/")
async def dashboard(request: Request):
    deps = request.app.state.deps
    audit = AuditService(manifest=deps.manifest)
    summary = audit.summary()
    return request.app.state.templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "summary": summary, "version": request.app.state.version},
    )


@router.get("/sidebar/status")
async def sidebar_status(request: Request):
    deps = request.app.state.deps
    audit = AuditService(manifest=deps.manifest)
    summary = audit.summary()
    return request.app.state.templates.TemplateResponse(
        "partials/sidebar_status.html", {"request": request, "summary": summary}
    )
```

Templates: produce minimal but semantic HTML with the `data-state` and class hooks the tests assert on. (See test selectors.) Use `_macros.html` for shared bits like rendering a state count.

- [ ] **Step 4: Tests pass; commit**

```bash
git checkout -b feat/v2/15-dashboard
git commit -m "feat(web): dashboard + sidebar status fragment"
```

---

### Task 4.2: Phase 4 merge into `version/v2`.

---

# Phase 5 — Cleanup wizard + operation registry + SSE

**Goal of phase:** the 4-step cleanup wizard works end-to-end. Operations run in the background with live progress streamed via SSE. Retry from Dashboard works.

**Phase exit criteria (matching spec § 5):**
- `/cleanup` lands at Step 1 with a default preset auto-loaded (placeholder until Phase 6 — for now, an empty filter form).
- Steps 1→4 can be completed end-to-end with the in-memory fake.
- Live progress on Steps 3 and 4 streams via SSE.
- Cancel mid-archive completes the current meeting and stops.
- Browser refresh on any step preserves state.
- Retry from Dashboard works (re-uses operation infrastructure).

This phase is the largest single block of work in v2 (~12 tasks). Execute in subagent-driven mode where possible.

---

### Task 5.1: Operation registry

**Files:**
- Create: `firefliesclearer/web/operations.py`
- Create: `tests/web/test_operations.py`

- [ ] **Step 1: Failing tests** (covering: register/get; concurrent same-kind rejected; cancel waits for current; GC after 30 min; replay buffer)

```python
# tests/web/test_operations.py
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from firefliesclearer.web.operations import (
    Event,
    MeetingSlot,
    Operation,
    OperationKind,
    OperationRegistry,
    SameKindAlreadyRunning,
)
from tests.fakes.frozen_clock import FrozenClock


@pytest.mark.asyncio
async def test_register_and_get():
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))

    async def runner(ctx):
        return None

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1"], runner=runner)
    assert reg.get(op.id) is op


@pytest.mark.asyncio
async def test_second_same_kind_raises_409_equivalent():
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))

    async def slow(ctx):
        await asyncio.sleep(0.5)

    op1 = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1"], runner=slow)

    with pytest.raises(SameKindAlreadyRunning) as exc:
        await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m2"], runner=slow)
    assert exc.value.existing_op_id == op1.id

    op1.task.cancel()


@pytest.mark.asyncio
async def test_cancel_completes_after_current_meeting():
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))

    async def runner(ctx):
        for mid in ctx.meeting_ids:
            ctx.emit(Event(seq=ctx.next_seq(), kind="meeting_state", data={"id": mid, "state": "done"}))
            if ctx.cancel_event.is_set():
                break

    op = await reg.start(
        kind=OperationKind.ARCHIVE, meeting_ids=["m1", "m2", "m3"], runner=runner
    )
    await asyncio.sleep(0.01)
    reg.cancel(op.id)
    await op.task

    events = list(op.replay_buffer())
    assert any(e.data.get("id") == "m1" for e in events)
    # Did not process all
    assert len([e for e in events if e.kind == "meeting_state"]) < 3


@pytest.mark.asyncio
async def test_gc_drops_completed_after_window():
    clock = FrozenClock(datetime(2026, 4, 29, tzinfo=UTC))
    reg = OperationRegistry(clock=clock)

    async def runner(ctx):
        return None

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1"], runner=runner)
    await op.task
    clock.advance(timedelta(minutes=31))
    reg.gc()

    with pytest.raises(KeyError):
        reg.get(op.id)
```

- [ ] **Step 2: Implement `OperationRegistry` per spec § 10.1–10.2**

```python
# firefliesclearer/web/operations.py
from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from firefliesclearer.ports.clock import Clock


class OperationKind(str, Enum):
    ARCHIVE = "archive"
    PURGE = "purge"
    RETRY_ARCHIVE = "retry-archive"
    RETRY_PURGE = "retry-purge"


class SameKindAlreadyRunning(RuntimeError):
    def __init__(self, existing_op_id: str) -> None:
        super().__init__(f"Operation {existing_op_id} already running for this kind.")
        self.existing_op_id = existing_op_id


@dataclass
class Event:
    seq: int
    kind: str
    data: dict[str, Any]
    at: datetime | None = None


@dataclass
class MeetingSlot:
    meeting_id: str
    title: str = ""
    sub_state: str = "queued"
    error: str | None = None


@dataclass
class _RunnerContext:
    meeting_ids: list[str]
    cancel_event: asyncio.Event
    _emit: Callable[[Event], None]
    _next: Callable[[], int]

    def emit(self, evt: Event) -> None:
        self._emit(evt)

    def next_seq(self) -> int:
        return self._next()


@dataclass
class Operation:
    id: str
    kind: OperationKind
    total: int
    state: str
    meetings: list[MeetingSlot]
    started_at: datetime
    finished_at: datetime | None
    cancel_event: asyncio.Event
    task: asyncio.Task
    _events: list[Event] = field(default_factory=list)
    _subscribers: list[asyncio.Queue[Event]] = field(default_factory=list)
    _seq_counter: int = 0

    def replay_buffer(self) -> Iterator[Event]:
        return iter(list(self._events))

    async def subscribe(self) -> AsyncIterator[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.append(q)
        try:
            while True:
                evt = await q.get()
                yield evt
                if evt.kind == "operation_state" and evt.data.get("state") in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }:
                    return
        finally:
            self._subscribers.remove(q)

    def _emit(self, evt: Event) -> None:
        self._events.append(evt)
        for q in self._subscribers:
            q.put_nowait(evt)


class OperationRegistry:
    GC_AGE = timedelta(minutes=30)

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._ops: dict[str, Operation] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        kind: OperationKind,
        meeting_ids: list[str],
        runner: Callable[[_RunnerContext], Awaitable[None]],
    ) -> Operation:
        async with self._lock:
            for op in self._ops.values():
                if op.kind == kind and op.state == "running":
                    raise SameKindAlreadyRunning(op.id)
            op_id = f"op_{self._clock.now().strftime('%Y-%m-%dT%H-%M-%S')}_{secrets.token_hex(2)}"
            op = Operation(
                id=op_id,
                kind=kind,
                total=len(meeting_ids),
                state="running",
                meetings=[MeetingSlot(meeting_id=mid) for mid in meeting_ids],
                started_at=self._clock.now(),
                finished_at=None,
                cancel_event=asyncio.Event(),
                task=asyncio.create_task(self._run(op_id, meeting_ids, runner)),
            )
            self._ops[op_id] = op
            return op

    def get(self, op_id: str) -> Operation:
        return self._ops[op_id]

    def cancel(self, op_id: str) -> None:
        op = self._ops[op_id]
        op.cancel_event.set()

    def has_active(self) -> bool:
        return any(o.state == "running" for o in self._ops.values())

    def gc(self) -> None:
        now = self._clock.now()
        for op_id, op in list(self._ops.items()):
            if op.finished_at and (now - op.finished_at) > self.GC_AGE:
                del self._ops[op_id]

    async def _run(self, op_id: str, meeting_ids: list[str], runner) -> None:
        op = self._ops[op_id]

        def next_seq() -> int:
            op._seq_counter += 1  # noqa: SLF001
            return op._seq_counter  # noqa: SLF001

        ctx = _RunnerContext(
            meeting_ids=list(meeting_ids),
            cancel_event=op.cancel_event,
            _emit=op._emit,  # noqa: SLF001
            _next=next_seq,
        )
        try:
            await runner(ctx)
            terminal = "cancelled" if op.cancel_event.is_set() else "succeeded"
        except Exception as exc:  # noqa: BLE001
            op._emit(  # noqa: SLF001
                Event(seq=next_seq(), kind="operation_state", data={"state": "failed", "error": str(exc)})
            )
            terminal = "failed"
        op.state = terminal
        op.finished_at = self._clock.now()
        op._emit(Event(seq=next_seq(), kind="operation_state", data={"state": terminal}))  # noqa: SLF001
```

- [ ] **Step 3: Tests pass; commit**

```bash
git checkout -b feat/v2/16-operation-registry
git commit -m "feat(web): operation registry with cancel + GC + replay buffer"
```

---

### Task 5.2 — 5.5: Cleanup wizard steps

Each step is its own task, following the same TDD pattern:

**Task 5.2: Step 1 — Filter** (filter form, live count via HTMX, preset dropdown placeholder).
**Task 5.3: Step 2 — Review** (table from in-memory selection, pagination, side panel, shift-select).
**Task 5.4: Step 3 — Archive** (preflight → POST starts operation → SSE in-progress view → done view with retry-failed).
**Task 5.5: Step 4 — Purge** (preflight with typed-count confirmation → POST → SSE → done).

For each:
- Failing route tests in `tests/web/routes/test_cleanup.py` covering exit criteria from spec § 5.x.
- Implement route handlers in `firefliesclearer/web/routes/cleanup.py`.
- Templates in `firefliesclearer/web/templates/cleanup/<step>.html`.
- Commit per task on its own feature branch.

The full implementation is too large to inline here; instead each task follows this template:

```text
- [ ] Write failing tests for the step's exit criteria (spec § 5.x).
- [ ] Implement the route handlers.
- [ ] Implement the templates.
- [ ] Manually verify the step renders and form posts work.
- [ ] Run tests; expect green.
- [ ] Commit on feat/v2/<NN>-cleanup-step-<N>.
```

**Critical implementation details to surface in the per-task plans:**

- **Wizard state** — store `step`, `filters`, `selected_ids` in the session store keyed by the session cookie (Task 3.1's SessionStore).
- **Preview count endpoint** — `POST /cleanup/preview-count` returns just an HTML fragment with the new count; HTMX `hx-trigger="input changed delay:500ms"` on each form field.
- **Pagination** — server-side; URL param `?page=N`; HTMX swap target is the table element, sidebar untouched.
- **Side panel** — `GET /cleanup/meeting/{id}/panel` returns the side panel fragment; HTMX `hx-target=".side-panel"`, `hx-swap="innerHTML"`.
- **Operation start** — `POST /cleanup/archive/start` calls `OperationRegistry.start(kind=ARCHIVE, ...)` with a runner that wraps `ArchiveService.archive_meeting_ids(...)`. Returns the in-progress view (template + SSE wired up).
- **SSE endpoint** — `/api/operations/{op_id}/events` from Task 5.6.
- **Cancel button** — `POST /cleanup/operations/{op_id}/cancel` calls `OperationRegistry.cancel(op_id)` and returns 204.

---

### Task 5.6: SSE endpoint

**Files:**
- Create: `firefliesclearer/web/routes/progress.py`
- Create: `tests/web/routes/test_progress_sse.py`

- [ ] **Step 1: Failing tests** (subscribe live; replay catches up a late subscriber; auto-close on terminal state)

```python
# tests/web/routes/test_progress_sse.py
def test_late_subscriber_sees_replay_buffer(configured_app, configured_client):
    # Start an operation, let it complete, then subscribe.
    ...
```

- [ ] **Step 2: Implement using `sse-starlette`** per spec § 10.3:

```python
# firefliesclearer/web/routes/progress.py
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter()


@router.get("/api/operations/{op_id}/events")
async def stream(op_id: str, request: Request):
    registry = request.app.state.operation_registry
    op = registry.get(op_id)

    async def gen():
        for evt in op.replay_buffer():
            yield {"event": evt.kind, "id": str(evt.seq), "data": json.dumps(evt.data)}
        async for evt in op.subscribe():
            yield {"event": evt.kind, "id": str(evt.seq), "data": json.dumps(evt.data)}

    return EventSourceResponse(gen(), ping=15)


@router.post("/api/operations/{op_id}/cancel")
async def cancel(op_id: str, request: Request):
    registry = request.app.state.operation_registry
    registry.cancel(op_id)
    return {"ok": True}
```

- [ ] **Step 3: Commit**

```bash
git checkout -b feat/v2/17-sse-endpoint
git commit -m "feat(web): SSE endpoint for operation progress + cancel"
```

---

### Task 5.7: Wire retry-from-Dashboard

In `dashboard.py` add a route `POST /retry/{meeting_id}` that calls the operation registry to start a `RETRY_ARCHIVE` (or `RETRY_PURGE`) operation for that single meeting. Returns the inline progress fragment to replace the dashboard row.

Add an integration test in `tests/web/e2e/test_full_run.py` that:
1. Boots a configured app with a fake repo containing a meeting that fails on first archive.
2. Hits `/cleanup/...` end-to-end → archive fails.
3. Hits dashboard → sees the failed row.
4. Posts to `/retry/<id>` → successful retry → row updates to ✓.

- [ ] Write the test, implement the route, commit.

```bash
git checkout -b feat/v2/18-retry-wiring
git commit -m "feat(web): retry from dashboard re-uses operation registry"
```

---

### Task 5.8: Phase 5 verification

- [ ] `pytest -q` — green.
- [ ] Manual smoke against live API with a test meeting (see release smoke checklist).
- [ ] Phase merge into `version/v2`.

---

# Phase 6 — Presets CRUD + migration + `run --preset`

**Goal of phase:** save filter combos as presets; load them on the wizard's filter step; CLI `run` accepts `--preset NAME`; v1 `[rules.auto]` auto-migrates to a default preset on first `serve`.

---

### Task 6.1: `Preset` model + config schema update

Add to `firefliesclearer/infra/config.py`:

```python
class Preset(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    default: bool = False
    created_at: datetime
    filters: ScanFiltersModel  # use Pydantic mirror of ScanFilters
```

Add `presets: list[Preset]` to the `Config` model. Tests: round-trip a TOML with presets through the config loader.

- [ ] Write failing test, implement, commit.

```bash
git checkout -b feat/v2/19-preset-model
git commit -m "feat(infra): add Preset model and [[presets]] section to config"
```

---

### Task 6.2: `PresetService` with atomic writes

`firefliesclearer/application/preset_service.py` per spec § 7.2 — full CRUD + `get_default()`. Atomic writes using the same pattern as `setup_service.write_config`.

- [ ] Write failing tests, implement, commit.

```bash
git checkout -b feat/v2/20-preset-service
git commit -m "feat(application): PresetService CRUD with atomic writes"
```

---

### Task 6.3: Migration from v1 `[rules.auto]`

On first `serve` startup, if `rules.auto` exists in config but no `[[presets]]`: create a single default preset called "Auto cleanup", remove the legacy section, write `<config>.v1.bak` with the original.

Implement in `setup_service.migrate_v1_rules_auto(config_path)`.

- [ ] Tests: TOML with `[rules.auto]` → after migration: 1 preset, no `rules.auto`, `.v1.bak` exists.
- [ ] Wire from `serve_cmd` startup.

```bash
git checkout -b feat/v2/21-preset-migration
git commit -m "feat(application): migrate v1 [rules.auto] to default preset"
```

---

### Task 6.4: Presets page + save-from-wizard

Routes `/presets` (list + new + edit + delete) and the inline "Save as preset" form on the cleanup wizard's Step 1.

- [ ] Tests + implementation + commit.

```bash
git checkout -b feat/v2/22-presets-ui
git commit -m "feat(web): presets page + save-from-wizard form"
```

---

### Task 6.5: `firefliesclearer run --preset NAME`

Modify `cli/run_cmd.py` to accept `--preset NAME` and load the preset's filters via `PresetService`. Removes the v1 hardcoded `[rules.auto]` reading. If `--preset` is omitted, falls back to the default preset; if no default, error with a clear message.

- [ ] Update test in `tests/cli/test_run_cmd.py`. Commit.

```bash
git checkout -b feat/v2/23-run-preset-flag
git commit -m "feat(cli): run --preset flag (replaces [rules.auto] reading)"
```

---

### Task 6.6: Phase 6 verification & merge.

---

# Phase 7 — History page

**Goal:** `/history` renders a paginated, filterable view over the manifest. Shareable URL state. Side panel with state-log timeline.

---

### Task 7.1: History route + filters + table

Filters: date range (preset dropdown + custom), state multiselect, title search. URL query string round-trips. Pagination 50/page.

```python
# firefliesclearer/web/routes/history.py
@router.get("/history")
async def history(
    request: Request,
    range: str = "last-30d",
    state: list[str] | None = Query(None),
    q: str = "",
    page: int = 1,
):
    audit = AuditService(manifest=request.app.state.deps.manifest)
    filt = HistoryFilter(...)
    rows = audit.history(filt)
    total = audit.history_count(filt)
    return request.app.state.templates.TemplateResponse("history.html", {...})
```

- [ ] Tests cover: filter combinations; URL round-trip; empty state; pagination.
- [ ] Commit.

```bash
git checkout -b feat/v2/24-history
git commit -m "feat(web): history page with filters and pagination"
```

---

### Task 7.2: Side panel with state-log timeline

`GET /history/{meeting_id}/panel` returns the side panel fragment with the full state log, pretty-printed.

- [ ] Test + implementation + commit.

```bash
git checkout -b feat/v2/25-history-side-panel
git commit -m "feat(web): history side panel with state-log timeline"
```

---

# Phase 8 — Settings page

**Goal:** `/settings` renders all sections from spec § 8.2; each section saves atomically; flash messages; reset configuration redirects to setup.

---

### Task 8.1: Connection section

Edit/replace API key with test-connection. Inline form for the actual replace.

```bash
git checkout -b feat/v2/26-settings-connection
git commit -m "feat(web): settings connection section (API key + test)"
```

---

### Task 8.2: Archive section

Edit archive root with the same validation as the setup wizard. Banner if path changed and content exists at old path.

```bash
git checkout -b feat/v2/27-settings-archive
git commit -m "feat(web): settings archive section with move-banner"
```

---

### Task 8.3: Defaults section

Concurrency, default age, delete-confirmation threshold.

```bash
git checkout -b feat/v2/28-settings-defaults
git commit -m "feat(web): settings defaults section"
```

---

### Task 8.4: Logs & data section

View today's log (read-only modal with JSON-lines syntax), open archive folder button, log retention.

```bash
git checkout -b feat/v2/29-settings-logs
git commit -m "feat(web): settings logs section + log retention sweep at startup"
```

---

### Task 8.5: Danger zone (Reset config)

Confirms, deletes config.toml, redirects to /setup/welcome. Archive folder + manifest left intact.

```bash
git checkout -b feat/v2/30-settings-danger-zone
git commit -m "feat(web): settings danger zone — reset configuration"
```

---

### Task 8.6: Open-archive-folder shell-out

Cross-platform: `explorer.exe` (Windows), `open` (macOS), `xdg-open` (Linux). Always passes the constant config-derived path; never user input.

```bash
git checkout -b feat/v2/31-open-archive-folder
git commit -m "feat(web): open-archive-folder shell-out (Windows/macOS/Linux)"
```

---

# Phase 9 — Polish, docs, release prep

---

### Task 9.1: Tailwind CSS compile script

Use the standalone Tailwind CLI binary (no Node required). Script `tools/build_static.sh` downloads the binary on first run, compiles `tailwind.input.css` → `firefliesclearer/web/static/styles.css`. Run once per release.

```bash
git checkout -b feat/v2/32-tailwind
git commit -m "build: tailwind CSS compile script (standalone CLI, no Node)"
```

---

### Task 9.2: Lucide icon set vendoring

Subset the 10–15 icons we use; ship as SVGs in `firefliesclearer/web/static/icons/`.

```bash
git checkout -b feat/v2/33-icons
git commit -m "build: vendor Lucide icon subset"
```

---

### Task 9.3: Manual smoke checklist doc

Create `docs/superpowers/specs/v2-release-smoke.md` per spec § 11.8.

```bash
git checkout -b feat/v2/34-smoke-checklist
git commit -m "docs: v2 release smoke checklist"
```

---

### Task 9.4: README + screenshots

Update `README.md`: install, `serve` quickstart, two screenshots (Cleanup wizard, Dashboard), scheduling docs (CLI cron, unchanged from v1).

```bash
git checkout -b feat/v2/35-readme
git commit -m "docs: README v2 update + screenshots"
```

---

### Task 9.5: CHANGELOG entry

Create `CHANGELOG.md` if missing; add v2 entry summarising user-facing changes.

```bash
git checkout -b feat/v2/36-changelog
git commit -m "docs: v2 CHANGELOG entry"
```

---

### Task 9.6: CLAUDE.md architecture overview

Update `CLAUDE.md` with the v2 layered diagram and module boundaries.

```bash
git checkout -b feat/v2/37-claude-md
git commit -m "docs: CLAUDE.md v2 architecture overview"
```

---

### Task 9.7: CI changes

`.github/workflows/ci.yml`: add `--cov-fail-under=85`; extend mypy `--strict` scope to `application/` and `web/`.

```bash
git checkout -b feat/v2/38-ci
git commit -m "ci: coverage gate 85% + extend mypy --strict scope"
```

---

### Task 9.8: import-linter contract

`importlinter.toml` (or `setup.cfg` section) enforcing layered dependency rules from spec § 2.6. Run as a CI step.

```bash
git checkout -b feat/v2/39-import-linter
git commit -m "ci: enforce layered import boundaries with import-linter"
```

---

### Task 9.9: Final coverage + wheel-size sanity

- Run `pytest --cov=firefliesclearer --cov-report=term-missing` and verify ≥ 85% overall, 100% on `core/pipeline.py`, `core/manifest.py`, `web/lifecycle.py`, `web/operations.py`, ≥ 90% on each route module.
- Run `python -m build` → check the produced wheel size is < 5 MB.

If targets miss, add tests until they pass.

```bash
git checkout -b feat/v2/40-final-coverage
git commit -m "test: final coverage push + wheel-size verification"
```

---

### Task 9.10: Manual smoke pass

Walk through the smoke checklist from Task 9.3 against a real Fireflies account with a test meeting. Tick every item in the PR description.

---

### Task 9.11: PR `version/v2` → `main`

```bash
git checkout version/v2
git push
gh pr create --base main --title "v2: web UI" --body "$(cat <<'EOF'
## Summary
- Local single-user FastAPI + HTMX web UI
- Setup wizard replaces v1 `init` command
- Dashboard, Cleanup wizard (4 steps), Presets, History, Settings
- Heartbeat-driven shutdown when browser closes

## Acceptance criteria
See spec section 12 for the full checklist.

## Smoke checklist
[paste ticked checklist from Task 9.10]
EOF
)"
```

After merge to `main`:

```bash
git push origin --delete version/v2
git branch -D version/v2
```

---

## Self-review

After writing the plan, here's the spec coverage check:

| Spec section | Plan coverage |
|---|---|
| §1 Purpose & scope | Phase plan rationale; Phase 9.4 docs |
| §2 Architecture & module layout | Phase 1 (services), Phase 2 (web pkg), Phase 9.8 (import-linter) |
| §2.7 Database location | Phase 1 services use existing Manifest; no new DB code |
| §3 App shell & navigation | Phase 2.5 (base template), Phase 4 (sidebar status), Phase 9.1 (Tailwind) |
| §4 First-run wizard | Phase 3 |
| §5 Cleanup wizard | Phase 5 (Tasks 5.2–5.5) |
| §6 Dashboard | Phase 4, Phase 5.7 (retry wiring) |
| §7 Presets | Phase 6 |
| §8 History & Settings | Phase 7, Phase 8 |
| §9 Server lifecycle & security | Phase 2.2 (lockfile), 2.3 (lifecycle), 2.4 (security), 2.6 (serve cmd wires it) |
| §10 Long-running ops & SSE | Phase 5.1 (registry), 5.6 (SSE) |
| §11 Testing strategy | Tests in every task; final coverage gate Phase 9.7 |
| §12 Acceptance criteria | Validated by smoke checklist Phase 9.3, 9.10 |
| §13 Open items / deferred | Acknowledged in spec; no plan tasks needed |

**Placeholder scan:** The Cleanup wizard tasks (5.2–5.5) reference a template "follow this shape" rather than enumerating every step. This is intentional — each is a substantial task on its own that will be expanded by the executing subagent (or by Claude during inline execution) when reached. The spec requirements are the source of truth.

**Type consistency:** verified `ScanFilters`, `Preset`, `Operation`, `OperationKind`, `MeetingSlot`, `Event`, `HeartbeatTracker`, `ShutdownCoordinator`, `LockFile`, `SessionStore`, `SetupValues` are referenced consistently across tasks.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-29-firefliesclearer-v2-web-ui.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Good fit for the ~40 tasks here; each is small enough for a clean subagent context.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Suitable if you want to watch every commit or you prefer one-context-per-phase.

Which approach?
