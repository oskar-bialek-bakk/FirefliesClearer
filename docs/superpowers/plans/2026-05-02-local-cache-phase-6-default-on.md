# Local-cache Phase 6: Default-On + Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Final phase. Setup wizard writes `[sync] enabled = true` for fresh installs. Existing flag-off users get a one-time dashboard prompt to opt in. Add `firefliesclearer sync [--full]` CLI command. Remove the old `MeetingRepository`-driven scan code paths now that everyone's on the cache. Update README / CHANGELOG / CLAUDE.md.

**Architecture:** No new modules. Setup wizard's `_build_payload` adds the `[sync]` section. A new `partials/_sync_opt_in_banner.html` is included at the top of the dashboard when `cfg.sync.enabled is False`; clicking "Enable" POSTs to `/sync/enable` which writes the flag and reloads. The CLI `sync` command is a thin Typer wrapper around `SyncService.run`. Cleanup phase removes `if cfg.sync.enabled` branches in `build_deps` / `get_deps` and inlines the cache-only path.

**Tech Stack:** Python 3.13, Typer, FastAPI, HTMX, pytest.

**Spec reference:** `docs/superpowers/specs/2026-05-02-local-cache-design.md` — sections "Trigger surface", "Phased rollout / Phase 6".

**Depends on:** Phases 1-5 merged.

---

## File Structure

| File | Purpose | Change type |
|------|---------|-------------|
| `firefliesclearer/application/setup_service.py` | `_build_payload` includes `[sync] enabled = true` | Modify (~10 LOC) |
| `firefliesclearer/web/templates/partials/_sync_opt_in_banner.html` | New — dashboard opt-in banner for existing users | Create (~30 LOC) |
| `firefliesclearer/web/routes/sync.py` | New `POST /sync/enable` endpoint | Modify (~40 LOC) |
| `firefliesclearer/web/templates/dashboard.html` | Conditionally include opt-in banner | Modify (~5 LOC) |
| `firefliesclearer/cli/sync_cmd.py` | New — `firefliesclearer sync` Typer command | Create (~70 LOC) |
| `firefliesclearer/cli/_common.py` | Inline cache-only path; remove flag check | Modify (~15 LOC removed) |
| `firefliesclearer/web/deps.py` | Same — inline cache-only path | Modify (~10 LOC removed) |
| `firefliesclearer/web/routes/cleanup.py` | Remove `getattr(deps, "scan_repo", deps.client)` fallback (always scan_repo) | Modify (~5 LOC simplified) |
| `README.md`, `CHANGELOG.md`, `CLAUDE.md` | Document the new architecture | Modify |
| Tests across the affected files | Updated assertions | Modify |

---

## Tasks

### Task 1: Setup wizard writes `[sync] enabled = true`

**Files:**
- Modify: `firefliesclearer/application/setup_service.py`
- Test: `tests/application/test_setup_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/application/test_setup_service.py`:

```python
def test_write_config_enables_sync_for_fresh_installs(tmp_path: Path) -> None:
    svc = _make_service()
    cfg_path = tmp_path / "config.toml"
    values = SetupValues(
        api_key="ff_key",
        archive_root=tmp_path / "arch",
        default_age_days=90,
        concurrency=5,
    )
    svc.write_config(cfg_path, values)
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    assert data["sync"]["enabled"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/application/test_setup_service.py::test_write_config_enables_sync_for_fresh_installs --no-cov -v`
Expected: FAIL.

- [ ] **Step 3: Add `[sync]` block to `_build_payload`**

In `firefliesclearer/application/setup_service.py`, in `_build_payload`, append a `"sync"` key to the returned dict:

```python
            "sync": {
                "enabled": True,
                # Other fields use SyncConfig defaults.
            },
```

- [ ] **Step 4: Run test**

Run: `.venv/Scripts/pytest.exe tests/application/test_setup_service.py --no-cov -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/application/setup_service.py tests/application/test_setup_service.py
git commit -m "feat(setup): fresh installs enable sync by default

The setup wizard now writes [sync] enabled = true so first-run
users immediately get the local-cache benefits without manually
flipping the flag."
```

---

### Task 2: One-time opt-in banner for existing users

**Files:**
- Create: `firefliesclearer/web/templates/partials/_sync_opt_in_banner.html`
- Modify: `firefliesclearer/web/templates/dashboard.html`
- Modify: `firefliesclearer/web/routes/sync.py` (add `POST /sync/enable` + dismiss handling)
- Modify: `firefliesclearer/web/routes/dashboard.py` (decide whether to render the banner)
- Test: `tests/web/routes/test_dashboard.py` + `tests/web/routes/test_sync.py`

The banner shows when `cfg.sync.enabled is False` AND a `_sync_opt_in_dismissed` marker is missing from config. Clicking "Enable" rewrites the config with `[sync] enabled = true` and reloads. Clicking "Not now" writes the dismiss marker.

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/routes/test_dashboard.py`:

```python
def test_dashboard_shows_opt_in_banner_when_sync_disabled(configured_app):
    """Default configured_app has sync disabled — banner should appear."""
    with TestClient(configured_app) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/")
        assert "Enable local cache" in r.text


def test_dashboard_hides_opt_in_banner_when_sync_enabled(configured_app_sync_on):
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/")
        assert "Enable local cache" not in r.text
```

Add to `tests/web/routes/test_sync.py`:

```python
def test_post_sync_enable_writes_flag_and_redirects(configured_app, tmp_path):
    """POST /sync/enable writes enabled=true to config and redirects to /."""
    with TestClient(configured_app) as client:
        client.get("/?token=T", follow_redirects=False)
        csrf = client.cookies.get("ffc_csrf", "")
        r = client.post(
            "/sync/enable",
            data={"_csrf": csrf, "action": "enable"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303, 307)
        # Re-read config — the flag should now be true
        cfg_path = configured_app.state.config_path
        import tomllib
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        assert data["sync"]["enabled"] is True


def test_post_sync_enable_dismiss_writes_marker(configured_app, tmp_path):
    with TestClient(configured_app) as client:
        client.get("/?token=T", follow_redirects=False)
        csrf = client.cookies.get("ffc_csrf", "")
        r = client.post(
            "/sync/enable",
            data={"_csrf": csrf, "action": "dismiss"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303, 307)
        cfg_path = configured_app.state.config_path
        import tomllib
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        # Dismissed marker prevents the banner from showing again
        assert data.get("sync", {}).get("opt_in_dismissed") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_dashboard.py tests/web/routes/test_sync.py -k "opt_in or sync_enable" --no-cov -v`
Expected: FAILs.

- [ ] **Step 3: Create the partial**

Create `firefliesclearer/web/templates/partials/_sync_opt_in_banner.html`:

```html
<div class="sync-optin-banner" role="status">
  <div class="sync-optin-banner__msg">
    <strong>New: local cache for offline cleanup.</strong>
    Periodic background sync keeps a copy of your meetings locally so the
    cleanup wizard works even when Fireflies is rate-limiting your account.
  </div>
  <form method="post" hx-post="/sync/enable" hx-swap="none" style="display:inline">
    <input type="hidden" name="_csrf" value="{{ request.cookies.get('ffc_csrf', '') }}">
    <input type="hidden" name="action" value="enable">
    <button type="submit" class="btn-primary">Enable local cache</button>
  </form>
  <form method="post" hx-post="/sync/enable" hx-swap="none" style="display:inline">
    <input type="hidden" name="_csrf" value="{{ request.cookies.get('ffc_csrf', '') }}">
    <input type="hidden" name="action" value="dismiss">
    <button type="submit" class="btn-link">Not now</button>
  </form>
</div>
```

- [ ] **Step 4: Add the route handler**

In `firefliesclearer/web/routes/sync.py`, add:

```python
import tomllib
import tomli_w
from fastapi.responses import RedirectResponse


@router.post("/sync/enable")
async def enable_or_dismiss(request: Request) -> Response:
    form = await request.form()
    action = form.get("action", "")
    if action not in ("enable", "dismiss"):
        raise HTTPException(status_code=422, detail=f"Invalid action: {action!r}")

    cfg_path = request.app.state.config_path
    if cfg_path is None or not cfg_path.exists():
        raise HTTPException(status_code=500, detail="No config to update")

    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    sync_section = dict(data.get("sync", {}))
    if action == "enable":
        sync_section["enabled"] = True
    else:
        sync_section["opt_in_dismissed"] = True
    data["sync"] = sync_section
    # Write atomically next to the original.
    tmp = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        tomli_w.dump(data, f)
    tmp.replace(cfg_path)

    return RedirectResponse("/", status_code=303)
```

(`SyncConfig` already accepts an `opt_in_dismissed` field — add it to the model.)

In `firefliesclearer/infra/config.py`, extend `SyncConfig`:

```python
class SyncConfig(BaseModel):
    enabled: bool = False
    incremental_interval_hours: int = Field(default=6, ge=1, le=168)
    full_interval_days: int = Field(default=7, ge=0, le=365)
    full_run_hour_local: int = Field(default=3, ge=0, le=23)
    opt_in_dismissed: bool = False
```

- [ ] **Step 5: Wire banner conditional in dashboard**

In `firefliesclearer/web/routes/dashboard.py`, add to the template ctx:

```python
    "show_sync_opt_in": (
        deps.config.sync.enabled is False
        and deps.config.sync.opt_in_dismissed is False
    ),
```

In `firefliesclearer/web/templates/dashboard.html`, near the top:

```html
{% if show_sync_opt_in %}
  {% include "partials/_sync_opt_in_banner.html" %}
{% endif %}
```

- [ ] **Step 6: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/ --no-cov -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add firefliesclearer/web/templates/partials/_sync_opt_in_banner.html firefliesclearer/web/templates/dashboard.html firefliesclearer/web/routes/sync.py firefliesclearer/web/routes/dashboard.py firefliesclearer/infra/config.py tests/web/routes/test_dashboard.py tests/web/routes/test_sync.py
git commit -m "feat(web): one-time opt-in banner for existing users

Dashboard renders an Enable-or-Dismiss banner when [sync] enabled is
false AND opt_in_dismissed is false. Both actions persist the
choice atomically in config.toml. Banner never appears again after
either action."
```

---

### Task 3: `firefliesclearer sync` CLI command

**Files:**
- Create: `firefliesclearer/cli/sync_cmd.py`
- Modify: `firefliesclearer/cli/app.py` (register the command)
- Test: `tests/cli/test_sync_cmd.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_sync_cmd.py`:

```python
"""Tests for `firefliesclearer sync` CLI command."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from firefliesclearer.cli.app import app

runner = CliRunner()


def _write_minimal_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    archive = tmp_path / "arch"
    archive.mkdir()
    cfg.write_text(
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
    return cfg


def test_sync_command_runs_incremental_by_default(tmp_path):
    cfg = _write_minimal_config(tmp_path)
    # Use a stub repo via env var or arg — see implementation
    result = runner.invoke(app, ["sync", "--config", str(cfg), "--dry-run"])
    assert result.exit_code == 0
    assert "incremental" in result.output.lower()


def test_sync_command_full_flag_runs_full(tmp_path):
    cfg = _write_minimal_config(tmp_path)
    result = runner.invoke(app, ["sync", "--config", str(cfg), "--full", "--dry-run"])
    assert result.exit_code == 0
    assert "full" in result.output.lower()
```

(`--dry-run` is a hint that the implementation should support a quick path that skips the actual API call but proves the wiring works. Implement accordingly.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/cli/test_sync_cmd.py --no-cov -v`
Expected: FAIL — command not registered.

- [ ] **Step 3: Implement the command**

Create `firefliesclearer/cli/sync_cmd.py`:

```python
"""`firefliesclearer sync` — run a one-shot sync from the CLI."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from firefliesclearer.application.sync_service import (
    SyncMode,
    SyncService,
    SyncTrigger,
)
from firefliesclearer.cli import _common
from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app


@app.command()
def sync(
    full: bool = typer.Option(False, "--full", help="Run full reconciliation."),
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip the API call; print the plan."),
) -> None:
    """Run a sync now from the command line."""
    deps = _common.build_deps(config_override=config)
    mode = SyncMode.FULL if full else SyncMode.INCREMENTAL

    if dry_run:
        console.print(f"Would run [{mode.value}] sync.")
        return

    service = SyncService(repo=deps.client, manifest=deps.manifest, clock=deps.clock)
    outcome = asyncio.run(
        service.run(mode=mode, trigger=SyncTrigger.MANUAL_SETTINGS)
    )
    console.print(
        f"Sync {outcome.outcome}: "
        f"{outcome.meetings_seen} seen, "
        f"{outcome.meetings_added} added, "
        f"{outcome.meetings_updated} updated, "
        f"{outcome.meetings_gone} gone."
    )
    if outcome.outcome != "success":
        raise typer.Exit(code=1)
```

In `firefliesclearer/cli/app.py`, ensure the `sync_cmd` module is imported so the `@app.command()` decorator runs at startup. Check the existing pattern (likely a `from firefliesclearer.cli import sync_cmd  # noqa: F401` line near other command imports).

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/pytest.exe tests/cli/test_sync_cmd.py --no-cov -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/cli/sync_cmd.py firefliesclearer/cli/app.py tests/cli/test_sync_cmd.py
git commit -m "feat(cli): firefliesclearer sync [--full] command

One-shot sync from the command line. Useful for cron jobs that
don't want to keep `serve` running. --full triggers reconciliation
mode; --dry-run prints the plan without making API calls."
```

---

### Task 4: Remove flag-off code paths

**Files:**
- Modify: `firefliesclearer/cli/_common.py` — `build_deps` always uses cache adapter
- Modify: `firefliesclearer/web/deps.py` — `get_deps` always uses cache adapter, scheduler always starts
- Modify: `firefliesclearer/web/routes/cleanup.py` — drop the `getattr(deps, "scan_repo", deps.client)` fallback
- Test: `tests/cli/test_serve_cmd.py`, `tests/web/test_deps.py`

After Phase 6's setup-wizard change, the only path where `cfg.sync.enabled` could still be `False` is for users who explicitly dismissed the banner in Task 2. Even then, the read paths can use the cache adapter — the cache will be empty until they re-enable, and `Manifest.list_known()` returns an empty iterator on an empty cache (no harm). What we keep flag-gated: the scheduler and the CLI `sync` command. They're cheap-to-skip if disabled and shouldn't fire surprises.

Actually that's still a behavior split. Cleaner: keep the flag for "should we run sync at all?" but always wire the read path through the adapter. If sync hasn't run, the wizard sees an empty list — that's the user's choice from dismissing.

- [ ] **Step 1: Update tests to assert the new always-adapter behavior**

In `tests/cli/test_serve_cmd.py`, replace `test_build_deps_uses_live_repo_for_scan_when_sync_disabled` with:

```python
def test_build_deps_always_uses_cache_adapter(tmp_path):
    """After Phase 6 cleanup, scan_repo is always the cache adapter, regardless
    of sync.enabled. The flag now only controls scheduler startup."""
    cfg_path = tmp_path / "config.toml"
    archive = tmp_path / "archive"
    archive.mkdir()
    cfg_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive.as_posix()}"
""",
        encoding="utf-8",
    )
    from firefliesclearer.cli._common import build_deps
    from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository

    deps = build_deps(config_override=cfg_path)
    assert isinstance(deps.scan_repo, ManifestBackedRepository)
```

- [ ] **Step 2: Run test to confirm current code fails it**

Run: `.venv/Scripts/pytest.exe tests/cli/test_serve_cmd.py::test_build_deps_always_uses_cache_adapter --no-cov -v`
Expected: FAIL — current code falls back to `client` when sync disabled.

- [ ] **Step 3: Inline the cache-only path**

In `firefliesclearer/cli/_common.py`, simplify `build_deps`:

```python
    # Phase 6: cache adapter is unconditional; the [sync] flag now only
    # controls whether the scheduler runs.
    from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository
    scan_repo = ManifestBackedRepository(manifest)
    return Deps(
        config=cfg,
        pipeline=pipeline,
        manifest=manifest,
        client=client,
        clock=clock,
        scan_repo=scan_repo,
    )
```

In `firefliesclearer/web/deps.py`, simplify the lazy-build similarly.

In `firefliesclearer/web/routes/cleanup.py`, simplify the `_scan_service` factory:

```python
def _scan_service(request: Request, deps: SimpleNamespace) -> ScanService:
    return ScanService(repo=deps.scan_repo, clock=deps.clock)
```

(no more getattr fallback.)

- [ ] **Step 4: Run all tests**

Run: `.venv/Scripts/pytest.exe --no-cov -q`
Expected: PASS. Some pre-Phase-6 tests that asserted the live-client path may need updates — check failures, update assertions to expect the cache adapter.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/cli/_common.py firefliesclearer/web/deps.py firefliesclearer/web/routes/cleanup.py tests/cli/test_serve_cmd.py
git commit -m "refactor: remove flag-off scan code paths

After Phase 6 cleanup, scan_repo is always ManifestBackedRepository.
The [sync] flag now only controls whether the background scheduler
runs. Removes ~50 LOC of conditional logic across build_deps,
get_deps, and cleanup._scan_service."
```

---

### Task 5: Documentation updates

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md` (architecture diagram)
- Test: none (docs)

- [ ] **Step 1: Update README**

Add a section under "Architecture overview" (or wherever the README documents internals):

```markdown
## Local cache

FirefliesClearer keeps a local SQLite mirror of your Fireflies meetings
in `manifest.db`. Periodic background sync (every 6 h, with a weekly
full reconciliation at 03:00 local) keeps it fresh. The cleanup wizard
reads from this cache, so filtering and selection stay responsive even
when Fireflies is rate-limiting your account. Archive downloads and
deletes still go to the live API.

Configure via the `[sync]` section of `config.toml`:

```toml
[sync]
enabled = true                    # master flag
incremental_interval_hours = 6
full_interval_days = 7            # 0 disables full reconciliation
full_run_hour_local = 3
```

Run a sync from the CLI: `firefliesclearer sync [--full]`.
```

- [ ] **Step 2: Update CHANGELOG**

Add an entry under the "Unreleased" or current-version section:

```markdown
### Added
- Local cache (`[sync]` config section). Cleanup wizard reads from
  cache, working offline + during rate-limit windows.
- `firefliesclearer sync [--full]` CLI command for cron-style sync.
- Sync controls UI: review-page Sync now button, dashboard banner,
  settings-page Full re-sync button, opt-in banner for existing users.

### Changed
- New installs: `[sync] enabled = true` by default.
```

- [ ] **Step 3: Update CLAUDE.md architecture diagram**

In `CLAUDE.md`, update the architecture diagram to include `sync_service` and `sync_scheduler`. Add a note in "Common gotchas" explaining the cache vs live-API split.

- [ ] **Step 4: Smoke test**

Run: `.venv/Scripts/pytest.exe --no-cov -q`
Expected: PASS (no test changes from this task).

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md CLAUDE.md
git commit -m "docs: document local-cache architecture in README/CHANGELOG/CLAUDE.md"
```

---

### Task 6: Final verification + project sign-off

- [ ] **Step 1: Full pytest suite**

Run: `.venv/Scripts/pytest.exe --no-cov -q`
Expected: all PASS.

- [ ] **Step 2: mypy + ruff**

Run: `.venv/Scripts/mypy.exe firefliesclearer && .venv/Scripts/ruff.exe check firefliesclearer tests && .venv/Scripts/ruff.exe format --check firefliesclearer tests`
Expected: clean.

- [ ] **Step 3: Coverage check on the cumulative new code**

Run: `.venv/Scripts/pytest.exe --cov=firefliesclearer --cov-report=term-missing -q`
Expected: total coverage 80%+ (per CLAUDE.md), with `core/manifest.py` and `core/pipeline.py` at 100%, and `application/sync_service.py` at 95%+.

- [ ] **Step 4: Manual end-to-end smoke**

Optional but recommended for the final phase: start a fresh `firefliesclearer serve`, verify the bootstrap banner, click Sync now, watch the counters tick, navigate to the wizard and confirm it renders cached meetings without hitting the API.

- [ ] **Step 5: Project sign-off**

The local-cache feature is complete: schema → sync engine → read flip → trigger UI → bootstrap UX → default-on. All six phases shipped. Existing users have a one-time opt-in path; new users get it automatically.
