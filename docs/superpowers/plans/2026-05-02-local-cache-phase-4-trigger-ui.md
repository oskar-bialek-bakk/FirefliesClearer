# Local-cache Phase 4: Trigger UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** User-visible sync controls. `POST /sync/now` and `GET /sync/status` endpoints; a `_sync_banner.html` partial rendered on the review page and dashboard; a "Sync now" button on the review-page toolbar; a "Full re-sync" button in settings. HTMX polls `/sync/status` every 2 s while a sync is running. All gated behind `[sync] enabled = true`; flag-off users see nothing.

**Architecture:** A small dataclass `app.state.current_sync` mirrors the in-flight run. `POST /sync/now` acquires `app.state.sync_lock` (asyncio.Lock), spawns `asyncio.create_task(SyncService.run(...))`, returns 202 + the same shape as `/sync/status`. `GET /sync/status` reads `current_sync` plus the latest `sync_runs` row. The banner partial decides idle / running / partial / failed presentation. HTMX `hx-trigger="every 2s"` on the running banner polls until `state != 'running'`.

**Tech Stack:** Python 3.13, FastAPI, HTMX, Jinja2, asyncio, pytest.

**Spec reference:** `docs/superpowers/specs/2026-05-02-local-cache-design.md` — sections "Trigger surface and UI", "Phased rollout / Phase 4".

**Depends on:** Phases 1, 2, 3 merged.

---

## File Structure

| File | Purpose | Change type |
|------|---------|-------------|
| `firefliesclearer/web/routes/sync.py` | New — POST /sync/now + GET /sync/status | Create (~150 LOC) |
| `firefliesclearer/web/app.py` | Register the new router | Modify (~3 LOC) |
| `firefliesclearer/web/templates/partials/_sync_banner.html` | New — banner with idle/running/partial/failed states | Create (~80 LOC) |
| `firefliesclearer/web/templates/cleanup/_review_toolbar.html` | Add "Sync now" button + banner | Modify (~15 LOC) |
| `firefliesclearer/web/templates/dashboard.html` | Add banner near the top | Modify (~3 LOC) |
| `firefliesclearer/web/templates/settings/index.html` (or wherever the settings page lives — find via `Glob` `**/settings/*.html`) | Add "Full re-sync" button + section | Modify (~25 LOC) |
| `firefliesclearer/web/static/styles.css` | Banner styles + spinner | Modify (~30 LOC) |
| `tests/web/routes/test_sync.py` | New — endpoint tests | Create (~250 LOC) |
| `tests/web/conftest.py` | Extend `configured_app_sync_on` to pre-create a SyncService | Modify (~10 LOC) |

---

## Tasks

### Task 1: `app.state.current_sync` + `app.state.sync_lock`

**Files:**
- Modify: `firefliesclearer/web/app.py`
- Test: `tests/web/test_app.py` (create if needed) or extend an existing app-level test

The state holders live on the FastAPI `app.state`. They're created in `create_app()` so all routes see them.

- [ ] **Step 1: Write the failing test**

Add to `tests/web/test_app.py` (create the file if it doesn't exist; use the standard imports):

```python
"""Tests for app-level state initialisation."""

from __future__ import annotations

from firefliesclearer.web.app import create_app


def test_create_app_initialises_sync_state_holders():
    app = create_app(session_token="T", csrf_secret="S")
    # asyncio.Lock isn't trivially comparable; check existence + type
    import asyncio
    assert hasattr(app.state, "sync_lock")
    assert isinstance(app.state.sync_lock, asyncio.Lock)
    assert hasattr(app.state, "current_sync")
    assert app.state.current_sync is None  # nothing running yet
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/test_app.py::test_create_app_initialises_sync_state_holders --no-cov -v`
Expected: FAIL.

- [ ] **Step 3: Add to `create_app`**

In `firefliesclearer/web/app.py`, inside `create_app()` after the existing `app.state.*` assignments, add:

```python
    import asyncio
    app.state.sync_lock = asyncio.Lock()
    app.state.current_sync = None  # set to a CurrentSyncSnapshot when a run is in flight
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/pytest.exe tests/web/test_app.py --no-cov -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/app.py tests/web/test_app.py
git commit -m "feat(web): app.state holders for sync lock + current run"
```

---

### Task 2: `CurrentSyncSnapshot` dataclass + `/sync/status` endpoint

**Files:**
- Create: `firefliesclearer/web/routes/sync.py`
- Modify: `firefliesclearer/web/app.py` (register router)
- Test: `tests/web/routes/test_sync.py`

The status endpoint returns idle when `current_sync is None` and the last sync_runs row otherwise. It's a JSON endpoint (HTMX poll uses it for partial template rendering, but the JSON shape is the source of truth).

- [ ] **Step 1: Write the failing tests**

Create `tests/web/routes/test_sync.py`:

```python
"""Tests for /sync/now and /sync/status endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_status_endpoint_idle_when_no_runs(configured_app_sync_on):
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/sync/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "idle"
        assert body["last_run"] is None


def test_status_endpoint_returns_running_state_when_in_flight(configured_app_sync_on):
    """When a sync is in flight, status returns state=running with progress."""
    from datetime import UTC, datetime
    from firefliesclearer.web.routes.sync import CurrentSyncSnapshot

    configured_app_sync_on.state.current_sync = CurrentSyncSnapshot(
        run_id=1, mode="incremental", trigger_source="manual_review",
        started_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        meetings_seen=10, meetings_added=5, meetings_updated=0, meetings_gone=0,
    )

    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/sync/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "running"
        assert body["mode"] == "incremental"
        assert body["meetings_added"] == 5


def test_status_endpoint_returns_last_completed_when_idle(configured_app_sync_on):
    from datetime import UTC, datetime
    manifest = configured_app_sync_on.state.deps.manifest
    rid = manifest.start_sync_run(
        mode="incremental", trigger="scheduled", at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
    )
    manifest.finalize_sync_run(
        rid, outcome="success", at=datetime(2026, 5, 2, 12, 5, tzinfo=UTC),
    )

    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/sync/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "idle"
        assert body["last_run"] is not None
        assert body["last_run"]["outcome"] == "success"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_sync.py --no-cov -v`
Expected: ImportError on `CurrentSyncSnapshot`.

- [ ] **Step 3: Create the route module + register**

Create `firefliesclearer/web/routes/sync.py`:

```python
"""Sync routes — manual trigger endpoint and status polling endpoint."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from firefliesclearer.application.sync_service import (
    SyncMode,
    SyncService,
    SyncTrigger,
)
from firefliesclearer.web.deps import get_deps

logger = logging.getLogger(__name__)
router = APIRouter()


@dataclass(slots=True)
class CurrentSyncSnapshot:
    run_id: int
    mode: str
    trigger_source: str
    started_at: datetime
    meetings_seen: int = 0
    meetings_added: int = 0
    meetings_updated: int = 0
    meetings_gone: int = 0


@router.get("/sync/status")
async def status_endpoint(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> JSONResponse:
    current = getattr(request.app.state, "current_sync", None)
    last_run = deps.manifest.get_last_sync_run()
    last_run_dict = (
        {
            "id": last_run.id,
            "mode": last_run.mode,
            "outcome": last_run.outcome,
            "started_at": last_run.started_at.isoformat(),
            "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
            "meetings_seen": last_run.meetings_seen,
            "meetings_added": last_run.meetings_added,
            "meetings_updated": last_run.meetings_updated,
            "meetings_gone": last_run.meetings_gone,
            "next_resume_at": (
                last_run.next_resume_at.isoformat() if last_run.next_resume_at else None
            ),
            "error_message": last_run.error_message,
        }
        if last_run is not None
        else None
    )
    if current is not None:
        return JSONResponse(
            {
                "state": "running",
                "run_id": current.run_id,
                "mode": current.mode,
                "trigger_source": current.trigger_source,
                "started_at": current.started_at.isoformat(),
                "meetings_seen": current.meetings_seen,
                "meetings_added": current.meetings_added,
                "meetings_updated": current.meetings_updated,
                "meetings_gone": current.meetings_gone,
                "last_run": last_run_dict,
            }
        )
    return JSONResponse({"state": "idle", "last_run": last_run_dict})
```

In `firefliesclearer/web/app.py`, import + register the router:

```python
from firefliesclearer.web.routes import sync as sync_routes
...
app.include_router(sync_routes.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_sync.py --no-cov -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/routes/sync.py firefliesclearer/web/app.py tests/web/routes/test_sync.py
git commit -m "feat(sync): GET /sync/status endpoint + CurrentSyncSnapshot

Returns 'idle' when no sync is in flight (with the last completed
run's summary), 'running' with live counters when a sync is active.
Used by the polling banner on the review page + dashboard."
```

---

### Task 3: `POST /sync/now` endpoint

**Files:**
- Modify: `firefliesclearer/web/routes/sync.py`
- Test: `tests/web/routes/test_sync.py`

Acquires `sync_lock` non-blocking; on success, spawns the run task and returns 202. On lock-held, returns 409 with the in-flight run id.

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/routes/test_sync.py`:

```python
def test_post_sync_now_returns_202_and_starts_a_run(configured_app_sync_on):
    """Happy path: POST /sync/now returns 202 with mode + trigger."""
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        csrf = client.cookies.get("ffc_csrf", "")
        r = client.post(
            "/sync/now",
            data={"_csrf": csrf, "mode": "incremental", "trigger": "manual_review"},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["state"] == "running"
        assert body["mode"] == "incremental"


def test_post_sync_now_returns_409_when_already_running(configured_app_sync_on):
    """If sync_lock is already held, return 409."""
    from datetime import UTC, datetime
    from firefliesclearer.web.routes.sync import CurrentSyncSnapshot

    configured_app_sync_on.state.current_sync = CurrentSyncSnapshot(
        run_id=99, mode="full", trigger_source="scheduled",
        started_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
    )
    # Manually lock the sync_lock to simulate in-flight
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        configured_app_sync_on.state.sync_lock.acquire()
    )

    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        csrf = client.cookies.get("ffc_csrf", "")
        r = client.post(
            "/sync/now",
            data={"_csrf": csrf, "mode": "incremental", "trigger": "manual_review"},
        )
        assert r.status_code == 409
        body = r.json()
        assert body["current_run_id"] == 99


def test_post_sync_now_rejects_invalid_mode(configured_app_sync_on):
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        csrf = client.cookies.get("ffc_csrf", "")
        r = client.post(
            "/sync/now",
            data={"_csrf": csrf, "mode": "bogus", "trigger": "manual_review"},
        )
        assert r.status_code == 422


def test_post_sync_now_rejects_invalid_trigger(configured_app_sync_on):
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        csrf = client.cookies.get("ffc_csrf", "")
        r = client.post(
            "/sync/now",
            data={"_csrf": csrf, "mode": "incremental", "trigger": "scheduled"},
        )
        # 'scheduled' is for scheduler-internal use; manual triggers must be
        # 'manual_review' or 'manual_settings'.
        assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_sync.py -k "post_sync_now" --no-cov -v`
Expected: 4 FAILs.

- [ ] **Step 3: Add the POST handler**

In `firefliesclearer/web/routes/sync.py`, add at the bottom:

```python
@router.post("/sync/now")
async def trigger_sync(
    request: Request,
    mode: str = "incremental",
    trigger: str = "manual_review",
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> JSONResponse:
    # Validate mode
    try:
        sync_mode = SyncMode(mode)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid mode: {mode!r}")
    # Validate trigger — only manual_* allowed from this endpoint
    if trigger not in ("manual_review", "manual_settings"):
        raise HTTPException(status_code=422, detail=f"Invalid trigger: {trigger!r}")
    sync_trigger = SyncTrigger(trigger)

    sync_lock: asyncio.Lock = request.app.state.sync_lock
    if sync_lock.locked():
        current = getattr(request.app.state, "current_sync", None)
        return JSONResponse(
            {"current_run_id": current.run_id if current else None},
            status_code=409,
        )

    # Build a SyncService — prefer one already on app.state, else construct
    service = getattr(request.app.state, "sync_service", None)
    if service is None:
        service = SyncService(repo=deps.client, manifest=deps.manifest, clock=deps.clock)

    # Acquire the lock and spawn the task
    await sync_lock.acquire()
    snapshot = CurrentSyncSnapshot(
        run_id=0,  # set below after start_sync_run returns
        mode=mode, trigger_source=trigger,
        started_at=deps.clock.now(),
    )
    request.app.state.current_sync = snapshot

    async def _runner() -> None:
        try:
            outcome = await service.run(mode=sync_mode, trigger=sync_trigger)
            snapshot.run_id = outcome.run_id
            snapshot.meetings_seen = outcome.meetings_seen
            snapshot.meetings_added = outcome.meetings_added
            snapshot.meetings_updated = outcome.meetings_updated
            snapshot.meetings_gone = outcome.meetings_gone
        except Exception as exc:  # noqa: BLE001 — log and release
            logger.exception("Sync task failed: %s", exc)
        finally:
            request.app.state.current_sync = None
            sync_lock.release()

    asyncio.create_task(_runner())
    return JSONResponse(
        {
            "state": "running",
            "mode": mode,
            "trigger_source": trigger,
            "started_at": snapshot.started_at.isoformat(),
        },
        status_code=202,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_sync.py --no-cov -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/routes/sync.py tests/web/routes/test_sync.py
git commit -m "feat(sync): POST /sync/now endpoint

Acquires sync_lock; on success spawns SyncService.run as a task and
returns 202. Returns 409 with current_run_id when another sync is
already in flight. Rejects invalid mode/trigger with 422."
```

---

### Task 4: `_sync_banner.html` partial

**Files:**
- Create: `firefliesclearer/web/templates/partials/_sync_banner.html`
- Modify: `firefliesclearer/web/static/styles.css` (banner + spinner styles)

The banner has four visual states: idle, running, partial, failed. It includes the HTMX poll trigger when running; otherwise no trigger. The polling element re-renders the banner partial.

- [ ] **Step 1: Render the partial**

Create `firefliesclearer/web/templates/partials/_sync_banner.html`:

```html
{# Sync banner — included on review page + dashboard. The route handler
   passes a `sync_status` context dict matching GET /sync/status's JSON shape. #}
{% set state = sync_status.state if sync_status else "idle" %}

<div id="sync-banner" class="sync-banner sync-banner--{{ state }}"
     {% if state == "running" %}
     hx-get="/sync/status/banner"
     hx-trigger="every 2s"
     hx-target="this"
     hx-swap="outerHTML"
     {% endif %}>

  {% if state == "idle" %}
    {% if sync_status and sync_status.last_run %}
      {% set lr = sync_status.last_run %}
      <span class="sync-banner__msg">
        Last synced: <strong>{{ lr.finished_at | default("—") }}</strong>
        ({{ lr.mode }}, {{ lr.outcome }})
      </span>
    {% else %}
      <span class="sync-banner__msg">No sync run yet.</span>
    {% endif %}
    <form method="post" hx-post="/sync/now"
          hx-vals='{"mode":"incremental","trigger":"manual_review"}'
          hx-target="#sync-banner" hx-swap="outerHTML"
          style="display:inline">
      <input type="hidden" name="_csrf" value="{{ request.cookies.get('ffc_csrf', '') }}">
      <button type="submit" class="sync-banner__btn">↻ Sync now</button>
    </form>

  {% elif state == "running" %}
    <span class="sync-banner__spinner" aria-hidden="true"></span>
    <span class="sync-banner__msg">
      Syncing… <strong>{{ sync_status.meetings_seen }}</strong> seen,
      <strong>{{ sync_status.meetings_added }}</strong> added,
      <strong>{{ sync_status.meetings_updated }}</strong> updated.
    </span>

  {% elif sync_status.last_run and sync_status.last_run.outcome == "partial" %}
    {% set lr = sync_status.last_run %}
    <span class="sync-banner__msg">
      Sync paused: rate-limited until <strong>{{ lr.next_resume_at }}</strong>.
      {{ lr.meetings_added }} added so far.
    </span>

  {% elif sync_status.last_run and sync_status.last_run.outcome == "failed" %}
    {% set lr = sync_status.last_run %}
    <span class="sync-banner__msg">
      Last sync failed: {{ lr.error_message or "unknown error" }}
    </span>
    <form method="post" hx-post="/sync/now"
          hx-vals='{"mode":"incremental","trigger":"manual_review"}'
          hx-target="#sync-banner" hx-swap="outerHTML">
      <input type="hidden" name="_csrf" value="{{ request.cookies.get('ffc_csrf', '') }}">
      <button type="submit" class="sync-banner__btn">↻ Retry</button>
    </form>
  {% endif %}
</div>
```

- [ ] **Step 2: Add styles**

Append to `firefliesclearer/web/static/styles.css`:

```css
.sync-banner { display:flex; align-items:center; gap:0.5rem; padding:0.5rem 0.75rem;
  margin: 0.5rem 0; border-radius: 4px; font-size: 0.9rem; }
.sync-banner--idle    { background: #f6f8fa; color: #444; }
.sync-banner--running { background: #fff3cd; color: #663c00; }
.sync-banner__msg     { flex: 1; }
.sync-banner__btn     { padding: 2px 8px; background: transparent; border: 1px solid #888;
  border-radius: 3px; cursor: pointer; font-size: 0.85rem; }
.sync-banner__btn:hover { background: #eef; }
.sync-banner__spinner { display:inline-block; width: 1em; height: 1em;
  border: 2px solid #663c00; border-top-color: transparent; border-radius: 50%;
  animation: sync-spin 0.8s linear infinite; }
@keyframes sync-spin { to { transform: rotate(360deg); } }
```

- [ ] **Step 3: No tests for templates directly — covered indirectly via Task 5/6 integration**

- [ ] **Step 4: Smoke check**

Run: `.venv/Scripts/pytest.exe --no-cov -q`
Expected: still all green (no test changes, just template + CSS additions).

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/templates/partials/_sync_banner.html firefliesclearer/web/static/styles.css
git commit -m "feat(web): _sync_banner.html partial + styles

Four states: idle (last-synced-at + Sync now button), running
(spinner + live counters with hx-trigger every 2s), partial
(rate-limit countdown), failed (error + retry). Used on review
page + dashboard via include."
```

---

### Task 5: `GET /sync/status/banner` (HTML render of the partial)

**Files:**
- Modify: `firefliesclearer/web/routes/sync.py`
- Test: `tests/web/routes/test_sync.py`

The polling banner re-renders itself by GETting an HTML version of `/sync/status`. The JSON `/sync/status` endpoint stays for non-HTMX clients (e.g., the future CLI sync status command); the new `/sync/status/banner` returns the rendered partial.

- [ ] **Step 1: Write the failing test**

Add to `tests/web/routes/test_sync.py`:

```python
def test_status_banner_renders_idle_html(configured_app_sync_on):
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/sync/status/banner")
        assert r.status_code == 200
        assert "sync-banner--idle" in r.text
        assert "Sync now" in r.text or "No sync run yet" in r.text


def test_status_banner_renders_running_html(configured_app_sync_on):
    from datetime import UTC, datetime
    from firefliesclearer.web.routes.sync import CurrentSyncSnapshot
    configured_app_sync_on.state.current_sync = CurrentSyncSnapshot(
        run_id=1, mode="incremental", trigger_source="manual_review",
        started_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        meetings_seen=10, meetings_added=5,
    )

    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/sync/status/banner")
        assert r.status_code == 200
        assert "sync-banner--running" in r.text
        assert "10" in r.text  # meetings_seen rendered
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_sync.py -k "status_banner" --no-cov -v`
Expected: 2 FAILs.

- [ ] **Step 3: Add the HTML status route**

In `firefliesclearer/web/routes/sync.py`, add (next to the JSON one):

```python
from fastapi.templating import Jinja2Templates
from starlette.responses import Response


@router.get("/sync/status/banner")
async def status_banner(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    templates: Jinja2Templates = request.app.state.templates
    sync_status = _build_status_dict(request, deps)
    return templates.TemplateResponse(
        request, "partials/_sync_banner.html", {"sync_status": sync_status}
    )


def _build_status_dict(request: Request, deps: SimpleNamespace) -> dict:  # type: ignore[type-arg]
    current = getattr(request.app.state, "current_sync", None)
    last_run = deps.manifest.get_last_sync_run()
    last_run_dict = None
    if last_run is not None:
        last_run_dict = {
            "id": last_run.id,
            "mode": last_run.mode,
            "outcome": last_run.outcome,
            "started_at": last_run.started_at.isoformat(),
            "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
            "meetings_seen": last_run.meetings_seen,
            "meetings_added": last_run.meetings_added,
            "meetings_updated": last_run.meetings_updated,
            "meetings_gone": last_run.meetings_gone,
            "next_resume_at": (
                last_run.next_resume_at.isoformat() if last_run.next_resume_at else None
            ),
            "error_message": last_run.error_message,
        }
    if current is not None:
        return {
            "state": "running",
            "run_id": current.run_id,
            "mode": current.mode,
            "trigger_source": current.trigger_source,
            "started_at": current.started_at.isoformat(),
            "meetings_seen": current.meetings_seen,
            "meetings_added": current.meetings_added,
            "meetings_updated": current.meetings_updated,
            "meetings_gone": current.meetings_gone,
            "last_run": last_run_dict,
        }
    return {"state": "idle", "last_run": last_run_dict}
```

Refactor the JSON endpoint to call `_build_status_dict` for DRY:

```python
@router.get("/sync/status")
async def status_endpoint(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> JSONResponse:
    return JSONResponse(_build_status_dict(request, deps))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_sync.py --no-cov -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/routes/sync.py tests/web/routes/test_sync.py
git commit -m "feat(sync): GET /sync/status/banner renders HTML partial

HTMX-polled endpoint. Shares _build_status_dict with the JSON
endpoint so payload shapes never drift."
```

---

### Task 6: Wire banner into review page + dashboard

**Files:**
- Modify: `firefliesclearer/web/templates/cleanup/_review_toolbar.html`
- Modify: `firefliesclearer/web/templates/dashboard.html`
- Modify: `firefliesclearer/web/routes/cleanup.py` (pass `sync_status` to template ctx)
- Modify: `firefliesclearer/web/routes/dashboard.py` (same)
- Test: `tests/web/routes/test_cleanup_step2.py` + `tests/web/routes/test_dashboard.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/routes/test_cleanup_step2.py`:

```python
def test_review_page_includes_sync_banner(configured_app_sync_on):
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        # populate session with filters so the wizard can reach the review step
        # (existing helpers in this test file should set up filters; reuse them)
        # ... reuse whatever path the existing tests use ...
        r = client.get("/cleanup/review")
        assert "sync-banner" in r.text
```

Add to `tests/web/routes/test_dashboard.py`:

```python
def test_dashboard_includes_sync_banner(configured_app_sync_on):
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/")
        assert "sync-banner" in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_cleanup_step2.py::test_review_page_includes_sync_banner tests/web/routes/test_dashboard.py::test_dashboard_includes_sync_banner --no-cov -v`
Expected: 2 FAILs (banner not yet in templates).

- [ ] **Step 3: Include banner in templates**

In `firefliesclearer/web/templates/cleanup/_review_toolbar.html`, near the top (above the existing toolbar div), add:

```html
{% include "partials/_sync_banner.html" %}
```

In `firefliesclearer/web/templates/dashboard.html`, find a sensible place near the top (after the page heading but before the main content) and add the same `{% include %}`.

- [ ] **Step 4: Pass `sync_status` from routes**

In `firefliesclearer/web/routes/cleanup.py`'s `_review_context`, add:

```python
    "sync_status": _sync_status_for_template(request),
```

(Define `_sync_status_for_template` at module level — it's a thin wrapper over `_build_status_dict` from sync.py to avoid an import cycle. Just inline the same logic, or import from sync.py if no cycle exists.)

In `firefliesclearer/web/routes/dashboard.py`'s `dashboard()` template ctx, add the same key.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/web/ --no-cov -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/web/templates/cleanup/_review_toolbar.html firefliesclearer/web/templates/dashboard.html firefliesclearer/web/routes/cleanup.py firefliesclearer/web/routes/dashboard.py tests/web/routes/test_cleanup_step2.py tests/web/routes/test_dashboard.py
git commit -m "feat(web): include _sync_banner on review page + dashboard"
```

---

### Task 7: Settings-page "Full re-sync" button

**Files:**
- Modify: settings template (find via `Glob` `**/settings/*.html`; expect `firefliesclearer/web/templates/settings/index.html` or similar)
- Test: `tests/web/routes/test_settings.py` (extend or create)

- [ ] **Step 1: Find the settings page template**

Run via Glob in your tool: `firefliesclearer/web/templates/settings/*.html`. Open the main settings page template. Note the section structure (likely each section is a fieldset or a `<section>`).

- [ ] **Step 2: Write the failing test**

Add to `tests/web/routes/test_settings.py`:

```python
def test_settings_page_includes_full_resync_button(configured_app_sync_on):
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        r = client.get("/settings")
        assert r.status_code == 200
        assert "Full re-sync" in r.text
        assert 'hx-vals=\'{"mode":"full","trigger":"manual_settings"}\'' in r.text
```

(Adjust the URL `/settings` to match the actual settings route — check `firefliesclearer/web/routes/settings.py` if needed.)

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_settings.py::test_settings_page_includes_full_resync_button --no-cov -v`
Expected: FAIL.

- [ ] **Step 4: Add the section**

In the main settings template, add a new section:

```html
<section class="settings-section">
  <h2>Local cache sync</h2>
  <p class="muted">
    Periodic background sync keeps your local cache up to date. The
    cleanup wizard reads from this cache so it stays usable even when
    Fireflies is rate-limiting your account.
  </p>
  <p>
    <strong>Full re-sync</strong> walks every meeting in Fireflies and
    reconciles updates + deletions. Slow (~N API calls); run when you
    want to detect title edits or meetings deleted in Fireflies UI.
  </p>
  <form method="post" hx-post="/sync/now"
        hx-vals='{"mode":"full","trigger":"manual_settings"}'
        hx-target="#sync-banner" hx-swap="outerHTML">
    <input type="hidden" name="_csrf" value="{{ request.cookies.get('ffc_csrf', '') }}">
    <button type="submit" class="btn-secondary">Full re-sync now</button>
  </form>
  {% include "partials/_sync_banner.html" %}
</section>
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/pytest.exe tests/web/ --no-cov -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/web/templates/settings/ tests/web/routes/test_settings.py
git commit -m "feat(web): settings page Full re-sync section + button"
```

---

### Task 8: Final verification

- [ ] **Step 1: Full pytest suite**

Run: `.venv/Scripts/pytest.exe --no-cov -q`
Expected: all PASS.

- [ ] **Step 2: mypy + ruff**

Run: `.venv/Scripts/mypy.exe firefliesclearer && .venv/Scripts/ruff.exe check firefliesclearer tests && .venv/Scripts/ruff.exe format --check firefliesclearer tests`
Expected: clean.

- [ ] **Step 3: Manual smoke (optional but recommended)**

If feasible, start the dev server (`.venv/Scripts/firefliesclearer.exe serve`) with a flag-on config and visit `/`. The banner should render. Click "Sync now" — the banner flips to running, then back to idle once the run completes.

- [ ] **Step 4: Sign off**

Phase 4 complete. Phase 5: bootstrap UX (the richer banner variant + immediate full-sync trigger when manifest is empty).
