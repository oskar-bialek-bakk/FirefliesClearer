# Local-cache Phase 3: Read-Path Flip — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When `[sync] enabled = true`, `ScanService` reads from the local cache (`Manifest.list_known`) instead of hitting `FirefliesClient.list_meetings`. Wizard, dashboard, history, CLI all benefit. When the flag is off, behavior is unchanged. Add regression guards asserting the API is not called during cached reads.

**Architecture:** Introduce `ManifestBackedRepository` — a thin adapter that implements `MeetingRepository.list_meetings` over `Manifest.list_known()`. `build_deps` (CLI side) and `web.deps.get_deps` (web side) construct ScanService with this adapter when the flag is on, and with `FirefliesClient` (current behavior) when it's off. Mutations (`fetch_artifacts`, `delete_meeting`) always go to the live `FirefliesClient`; the adapter raises if those methods are called on it.

**Tech Stack:** Python 3.13, asyncio, SQLite, pytest, mypy strict, ruff.

**Spec reference:** `docs/superpowers/specs/2026-05-02-local-cache-design.md` — sections "Read-path migration", "Phased rollout / Phase 3".

**Depends on:** Phases 1 + 2 must be merged.

---

## File Structure

| File | Purpose | Change type |
|------|---------|-------------|
| `firefliesclearer/infra/manifest_backed_repo.py` | New — read-only adapter implementing MeetingRepository.list_meetings via Manifest | Create (~50 LOC) |
| `firefliesclearer/cli/_common.py` | `build_deps` chooses adapter when sync.enabled | Modify (~20 LOC) |
| `firefliesclearer/web/deps.py` | `get_deps` constructs ScanService deps from cache when flag on | Modify (~15 LOC) |
| `firefliesclearer/web/routes/cleanup.py` | `_scan_service` factory now uses cache-or-live based on flag | Modify (~10 LOC) |
| `firefliesclearer/cli/scan_cmd.py`, `run_cmd.py` | Same factory choice | Modify (~6 LOC each) |
| `tests/infra/test_manifest_backed_repo.py` | New — adapter tests | Create (~80 LOC) |
| `tests/web/conftest.py` | Add a "tracking" repo fixture that fails on list_meetings | Modify (~30 LOC) |
| `tests/web/routes/test_cleanup_step1.py`, `test_cleanup_step2.py` | Wizard regression: assert call_count == 0 with flag on | Modify (~40 LOC) |

---

## Tasks

### Task 1: `ManifestBackedRepository` adapter

**Files:**
- Create: `firefliesclearer/infra/manifest_backed_repo.py`
- Test: `tests/infra/test_manifest_backed_repo.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/infra/test_manifest_backed_repo.py`:

```python
"""Tests for ManifestBackedRepository — read-only Manifest -> MeetingRepository adapter."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import Meeting
from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository
from firefliesclearer.ports.meeting_repository import MeetingFilter

NOW = datetime(2026, 5, 2, tzinfo=UTC)


def _meeting(mid: str, *, dt: datetime | None = None) -> Meeting:
    return Meeting(
        meeting_id=mid, title=mid, meeting_date=dt or NOW,
        duration_minutes=30.0, host_email="a@x.com", participant_count=2,
        tags=(), has_transcript=True,
    )


@pytest.fixture
def manifest(tmp_path):
    return Manifest.open(tmp_path / "manifest.db")


async def test_list_meetings_yields_cached_rows(manifest):
    manifest.upsert_known(_meeting("a"), at=NOW)
    manifest.upsert_known(_meeting("b"), at=NOW)
    repo = ManifestBackedRepository(manifest)
    ids = sorted([m.meeting_id async for m in repo.list_meetings(MeetingFilter())])
    assert ids == ["a", "b"]


async def test_list_meetings_respects_older_than_filter(manifest):
    older = datetime(2025, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 6, 1, tzinfo=UTC)
    manifest.upsert_known(_meeting("old", dt=older), at=NOW)
    manifest.upsert_known(_meeting("new", dt=newer), at=NOW)

    repo = ManifestBackedRepository(manifest)
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    ids = [m.meeting_id async for m in repo.list_meetings(MeetingFilter(older_than=cutoff))]
    assert ids == ["old"]


async def test_list_meetings_excludes_archived_and_gone_by_default(manifest):
    manifest.upsert_known(_meeting("a"), at=NOW)
    manifest.upsert_known(_meeting("b"), at=NOW)
    manifest.upsert_known(_meeting("c"), at=NOW)
    from firefliesclearer.core.models import MeetingState
    manifest.transition("b", to=MeetingState.PENDING, at=NOW)
    manifest.transition("b", to=MeetingState.ARCHIVED, at=NOW)
    manifest.set_source_state("c", "gone")

    repo = ManifestBackedRepository(manifest)
    ids = [m.meeting_id async for m in repo.list_meetings(MeetingFilter())]
    assert ids == ["a"]


async def test_fetch_artifacts_raises_not_implemented(manifest):
    repo = ManifestBackedRepository(manifest)
    with pytest.raises(NotImplementedError, match="cache adapter"):
        await repo.fetch_artifacts("a")


async def test_delete_meeting_raises_not_implemented(manifest):
    repo = ManifestBackedRepository(manifest)
    with pytest.raises(NotImplementedError, match="cache adapter"):
        await repo.delete_meeting("a")


async def test_ping_user_raises_not_implemented(manifest):
    repo = ManifestBackedRepository(manifest)
    with pytest.raises(NotImplementedError, match="cache adapter"):
        await repo.ping_user()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/infra/test_manifest_backed_repo.py --no-cov -v`
Expected: ImportError (module doesn't exist).

- [ ] **Step 3: Implement the adapter**

Create `firefliesclearer/infra/manifest_backed_repo.py`:

```python
"""Read-only MeetingRepository backed by the local Manifest cache.

Implements only ``list_meetings``. Mutation methods raise NotImplementedError —
ScanService is the only caller that should reach this adapter, and it only
needs reads. ArchiveService/PurgeService continue to use the live
FirefliesClient for fetch_artifacts and delete_meeting.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import ArtifactBundle, Meeting
from firefliesclearer.ports.meeting_repository import MeetingFilter


class ManifestBackedRepository:
    def __init__(self, manifest: Manifest) -> None:
        self._manifest = manifest

    async def list_meetings(self, filter: MeetingFilter) -> AsyncIterator[Meeting]:
        for m in self._manifest.list_known(older_than=filter.older_than):
            yield m

    async def fetch_artifacts(self, meeting_id: str) -> ArtifactBundle:
        raise NotImplementedError(
            "ManifestBackedRepository is a read-only cache adapter; "
            "fetch_artifacts must go to the live FirefliesClient."
        )

    async def delete_meeting(self, meeting_id: str) -> None:
        raise NotImplementedError(
            "ManifestBackedRepository is a read-only cache adapter; "
            "delete_meeting must go to the live FirefliesClient."
        )

    async def ping_user(self) -> str:
        raise NotImplementedError(
            "ManifestBackedRepository is a read-only cache adapter; "
            "ping_user must go to the live FirefliesClient."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/infra/test_manifest_backed_repo.py --no-cov -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/infra/manifest_backed_repo.py tests/infra/test_manifest_backed_repo.py
git commit -m "feat(infra): ManifestBackedRepository read adapter

Wraps Manifest.list_known into the MeetingRepository.list_meetings
contract. Mutation methods raise NotImplementedError — ScanService
is the only consumer and only reads."
```

---

### Task 2: Wire adapter into `build_deps` (CLI side)

**Files:**
- Modify: `firefliesclearer/cli/_common.py`
- Test: `tests/cli/test_serve_cmd.py` or new test file

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_serve_cmd.py` (or create `tests/cli/test_common.py` if you prefer scope-isolation):

```python
def test_build_deps_uses_cache_repo_for_scan_when_sync_enabled(tmp_path):
    """When [sync] enabled = true, build_deps assigns a scan_repo distinct from
    the live client — the cache adapter."""
    cfg_path = tmp_path / "config.toml"
    archive = tmp_path / "archive"
    archive.mkdir()
    cfg_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive.as_posix()}"
[sync]
enabled = true
""",
        encoding="utf-8",
    )
    from firefliesclearer.cli._common import build_deps
    from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository

    deps = build_deps(config_override=cfg_path)
    assert hasattr(deps, "scan_repo")
    assert isinstance(deps.scan_repo, ManifestBackedRepository)
    # The mutation client is still the live FirefliesClient
    assert not isinstance(deps.client, ManifestBackedRepository)


def test_build_deps_uses_live_repo_for_scan_when_sync_disabled(tmp_path):
    cfg_path = tmp_path / "config.toml"
    archive = tmp_path / "archive"
    archive.mkdir()
    cfg_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive.as_posix()}"
""",  # no [sync] section -> default disabled
        encoding="utf-8",
    )
    from firefliesclearer.cli._common import build_deps

    deps = build_deps(config_override=cfg_path)
    # When disabled, scan_repo equals the live client (no separate adapter)
    assert deps.scan_repo is deps.client
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/cli/test_serve_cmd.py -k "scan_repo" --no-cov -v`
Expected: 2 FAILs — `Deps` has no `scan_repo` field.

- [ ] **Step 3: Add `scan_repo` to `Deps`**

In `firefliesclearer/cli/_common.py`, change the `Deps` dataclass to include `scan_repo`:

```python
@dataclass
class Deps:
    config: AppConfig
    pipeline: Pipeline
    manifest: Manifest
    client: FirefliesClient
    clock: Clock
    scan_repo: object  # MeetingRepository — live or cache adapter
```

(`object` rather than a precise Protocol because Protocols and dataclasses don't compose cleanly here; a `# type: ignore[no-untyped-def]` may be needed at usage sites if mypy flags it.)

In the same file, change the `build_deps` body so it constructs `scan_repo`:

```python
def build_deps(*, config_override: Path | None = None) -> Deps:
    user_path = config_override or user_config_path()
    cfg = load_config(user_config=user_path)
    archive_root = cfg.archive.root_dir
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest.open(archive_root / "manifest.db")
    archiver = Archiver(archive_root=archive_root)
    renderer = ReportlabSummaryRenderer()
    client = FirefliesClient(api_key=cfg.fireflies.api_key)
    clock = SystemClock()
    pipeline = Pipeline(
        repository=client,
        manifest=manifest,
        archiver=archiver,
        renderer=renderer,
        clock=clock,
    )
    if cfg.sync.enabled:
        from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository
        scan_repo: object = ManifestBackedRepository(manifest)
    else:
        scan_repo = client
    return Deps(
        config=cfg,
        pipeline=pipeline,
        manifest=manifest,
        client=client,
        clock=clock,
        scan_repo=scan_repo,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/cli/ --no-cov -v`
Expected: all PASS, including pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/cli/_common.py tests/cli/test_serve_cmd.py
git commit -m "feat(deps): build_deps picks scan_repo based on [sync] enabled

When sync is on, scan_repo = ManifestBackedRepository(manifest);
otherwise scan_repo = the live client (preserves existing behavior).
Mutations always use the live client."
```

---

### Task 3: Update CLI commands to use `scan_repo` for ScanService

**Files:**
- Modify: `firefliesclearer/cli/scan_cmd.py:60`
- Modify: `firefliesclearer/cli/run_cmd.py:55`
- Test: existing tests (no new tests; flag-on path is covered by Task 5's regression guard)

- [ ] **Step 1: Read the current usage**

Both files currently do:

```python
svc = ScanService(repo=deps.client, clock=deps.clock)
```

- [ ] **Step 2: Update to use `scan_repo`**

In `firefliesclearer/cli/scan_cmd.py:60`, change `repo=deps.client` to `repo=deps.scan_repo`. Same in `firefliesclearer/cli/run_cmd.py:55`.

- [ ] **Step 3: Run tests**

Run: `.venv/Scripts/pytest.exe tests/cli/ --no-cov -v`
Expected: all PASS — existing tests use disabled-sync configs so `scan_repo is client` and behavior is unchanged.

- [ ] **Step 4: Commit**

```bash
git add firefliesclearer/cli/scan_cmd.py firefliesclearer/cli/run_cmd.py
git commit -m "feat(cli): scan/run commands route reads through scan_repo

When [sync] enabled = true, the ScanService reads from the local
cache instead of the live API."
```

---

### Task 4: Update web wiring (`get_deps` + cleanup `_scan_service` factory)

**Files:**
- Modify: `firefliesclearer/web/deps.py` (the lazy-build branch)
- Modify: `firefliesclearer/web/routes/cleanup.py:118` (the `_scan_service` factory)

- [ ] **Step 1: Update `web/deps.get_deps` to construct `scan_repo`**

In `firefliesclearer/web/deps.py`, locate the lazy-build branch (after `manifest = Manifest.open(...)` and before the `deps = SimpleNamespace(...)` block). Add:

```python
    if config.sync.enabled:
        from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository
        scan_repo: object = ManifestBackedRepository(manifest)
    else:
        scan_repo = client
```

Then add `scan_repo=scan_repo` to the `SimpleNamespace(...)` construction:

```python
    deps = SimpleNamespace(
        config=config,
        manifest=manifest,
        client=client,
        clock=clock,
        pipeline=pipeline,
        scan_repo=scan_repo,
    )
```

- [ ] **Step 2: Update `_scan_service` in cleanup.py**

In `firefliesclearer/web/routes/cleanup.py:118`, change:

```python
    return ScanService(repo=deps.client, clock=deps.clock)
```

To:

```python
    return ScanService(repo=getattr(deps, "scan_repo", deps.client), clock=deps.clock)
```

(`getattr` with fallback so test fixtures that don't set `scan_repo` keep working — `tests/web/conftest.py:configured_app` builds a `SimpleNamespace` without it.)

- [ ] **Step 3: Run all web tests**

Run: `.venv/Scripts/pytest.exe tests/web/ --no-cov -v`
Expected: all PASS — existing tests use the disabled default and `scan_repo` falls back to `deps.client`.

- [ ] **Step 4: Commit**

```bash
git add firefliesclearer/web/deps.py firefliesclearer/web/routes/cleanup.py
git commit -m "feat(web): wire scan_repo through deps to cleanup ScanService

Lazy deps build now picks ManifestBackedRepository when sync.enabled,
the live client otherwise. _scan_service factory uses the chosen
repo with a getattr fallback so legacy fixtures keep working."
```

---

### Task 5: Wizard regression guard — assert API not called when sync is on

**Files:**
- Modify: `tests/web/conftest.py` — add a fixture that builds a `configured_app` with sync enabled and a tracking repo
- Modify: `tests/web/routes/test_cleanup_step1.py`, `test_cleanup_step2.py` — add tests using the new fixture
- Test: those same files

This is the regression net: when sync is on, FirefliesClient.list_meetings must never be called by wizard read flows.

- [ ] **Step 1: Add the tracking-repo fixture**

In `tests/web/conftest.py`, after the existing `configured_app` fixture, add:

```python
class _TrackingRepo:
    """Wraps an InMemoryMeetingRepository and counts list_meetings calls."""

    def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
        self._inner = inner
        self.list_call_count = 0

    async def list_meetings(self, filter):  # type: ignore[no-untyped-def]
        self.list_call_count += 1
        async for m in self._inner.list_meetings(filter):
            yield m

    async def fetch_artifacts(self, mid):  # type: ignore[no-untyped-def]
        return await self._inner.fetch_artifacts(mid)

    async def delete_meeting(self, mid):  # type: ignore[no-untyped-def]
        return await self._inner.delete_meeting(mid)

    async def ping_user(self):  # type: ignore[no-untyped-def]
        return await self._inner.ping_user()


@pytest.fixture
def configured_app_sync_on(tmp_path: Path, archive_root: Path):
    """A configured_app with [sync] enabled = true and a tracking client.

    Tests using this fixture can assert that wizard reads never invoke
    client.list_meetings — proving the cache read path is wired up.
    """
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
[sync]
enabled = true
""",
        encoding="utf-8",
    )
    inner = InMemoryMeetingRepository(api_key="ff_test")
    inner.set_user_email_for_key("ff_test", "oskar@example.com")
    tracking = _TrackingRepo(inner)

    db_path = archive_root / "manifest.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    manifest = Manifest(conn)

    from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository
    deps = SimpleNamespace(
        config=load_config(user_config=config_path),
        manifest=manifest,
        client=tracking,                           # tracking, so we can assert call count
        scan_repo=ManifestBackedRepository(manifest),
        clock=SystemClock(),
        pipeline=FakePipeline(),
    )

    app = create_app(
        session_token="T",
        csrf_secret="S",
        config_path=config_path,
        repo_factory=lambda key: tracking,
    )
    app.state.deps = deps
    app.state.tracking_repo = tracking
    return app
```

- [ ] **Step 2: Add the regression test**

In `tests/web/routes/test_cleanup_step1.py` (and `test_cleanup_step2.py` if there's an existing one for step 2 — check first), append:

```python
def test_wizard_step1_filter_does_not_call_live_api_when_sync_on(configured_app_sync_on):
    """The wizard's filter step reads from cache — zero list_meetings on the
    live FirefliesClient."""
    from datetime import UTC, datetime as _dt

    # Pre-populate cache with one meeting so the filter has something to match
    manifest = configured_app_sync_on.state.deps.manifest
    from firefliesclearer.core.models import Meeting
    manifest.upsert_known(
        Meeting(
            meeting_id="m1", title="Old standup",
            meeting_date=_dt(2025, 1, 1, tzinfo=UTC),
            duration_minutes=10.0, host_email="a@x", participant_count=2,
            tags=(), has_transcript=True,
        ),
        at=_dt(2026, 5, 2, tzinfo=UTC),
    )

    client = TestClient(configured_app_sync_on)
    client.get("/?token=T", follow_redirects=False)
    csrf = client.cookies.get("ffc_csrf", "")

    # Submit a filter — older_than_days = 365
    r = client.post(
        "/cleanup/filter",
        data={"_csrf": csrf, "older_than_days": "365"},
        follow_redirects=False,
    )
    assert r.status_code in (200, 302, 303, 307), r.text

    # Then load the review page
    r = client.get("/cleanup/review")
    assert r.status_code == 200, r.text

    # The live client.list_meetings was NEVER called
    assert configured_app_sync_on.state.tracking_repo.list_call_count == 0
```

(If the actual filter form fields differ from `older_than_days`, look at `step1_filter.html` and adjust. The point of the test is the post-filter `GET /cleanup/review` — that's where the wizard would ordinarily hit the API.)

- [ ] **Step 3: Run the test**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step1.py::test_wizard_step1_filter_does_not_call_live_api_when_sync_on --no-cov -v`
Expected: PASS.

- [ ] **Step 4: Run full web test suite (regression)**

Run: `.venv/Scripts/pytest.exe tests/web/ --no-cov -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/web/conftest.py tests/web/routes/test_cleanup_step1.py tests/web/routes/test_cleanup_step2.py
git commit -m "test(web): regression guard — wizard reads zero API calls when sync on

Adds configured_app_sync_on fixture with a tracking repo and a
ManifestBackedRepository wired into deps.scan_repo. Wizard
read flows must keep tracking_repo.list_call_count == 0."
```

---

### Task 6: Final verification

- [ ] **Step 1: Full pytest suite**

Run: `.venv/Scripts/pytest.exe --no-cov -q`
Expected: all PASS.

- [ ] **Step 2: mypy + ruff**

Run: `.venv/Scripts/mypy.exe firefliesclearer && .venv/Scripts/ruff.exe check firefliesclearer tests && .venv/Scripts/ruff.exe format --check firefliesclearer tests`
Expected: all clean.

- [ ] **Step 3: Sign off**

Phase 3 complete. Default-off — no behavior change for existing users. Phase 4 plan: trigger UI (review-page Sync button + banner + status endpoint).
