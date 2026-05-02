# Local-cache Phase 5: Bootstrap UX — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** First-run experience for the cache. When `[sync] enabled = true` AND the manifest is empty, the scheduler triggers an immediate full sync with `trigger=bootstrap`. The banner displays a richer "first-time sync" variant with an estimated total ("1273 of approximately 2000 cached so far"). The dashboard and wizard remain usable on partial data while bootstrap continues in the background. Rate-limit pauses surface in the same banner.

**Architecture:** Phase 2 already implemented bootstrap detection in `infra/sync_scheduler.run_scheduler` (no runs + empty cache → forces FULL + BOOTSTRAP). Phase 5 (a) ensures the scheduler also starts on lazy `get_deps` (post-setup-wizard scenario), (b) adds the bootstrap-aware banner variant, and (c) implements the "approximately N" estimator.

**Tech Stack:** Python 3.13, FastAPI, HTMX, Jinja2, asyncio, pytest.

**Spec reference:** `docs/superpowers/specs/2026-05-02-local-cache-design.md` — sections "Bootstrap (first run) — automatic", "Phased rollout / Phase 5".

**Depends on:** Phases 1-4 merged.

---

## File Structure

| File | Purpose | Change type |
|------|---------|-------------|
| `firefliesclearer/web/deps.py` | Start scheduler on lazy deps build when sync enabled | Modify (~25 LOC) |
| `firefliesclearer/web/templates/partials/_sync_banner.html` | Add bootstrap state branch | Modify (~25 LOC) |
| `firefliesclearer/application/sync_service.py` | Estimator helper for "approximately N" | Modify (~30 LOC) |
| `firefliesclearer/web/routes/sync.py` | Surface estimate + bootstrap flag in status dict | Modify (~10 LOC) |
| `tests/web/test_deps.py` | Bootstrap-trigger integration test | Modify (~50 LOC) |
| `tests/application/test_sync_service.py` | Estimator unit tests | Modify (~50 LOC) |
| `tests/web/routes/test_sync.py` | Bootstrap status surfacing test | Modify (~30 LOC) |

---

## Tasks

### Task 1: "Approximately N" estimator on `SyncOutcome` snapshot

**Files:**
- Modify: `firefliesclearer/application/sync_service.py`
- Test: `tests/application/test_sync_service.py`

The full-sync algorithm walks pages of 50. After page 1, we have 50 meetings; the API doesn't tell us total count up front. Estimate: when we see a partial page (< 50 meetings), the total is `previous_full_pages * 50 + len(last_page)`. Until then, project linearly: `pages_so_far * 50` is a lower bound; `pages_so_far * 50 + 50` is an "at least one more page" upper bound. Display as "approximately N" rounded to a friendly increment.

- [ ] **Step 1: Write the failing tests**

Add to `tests/application/test_sync_service.py`:

```python
from firefliesclearer.application.sync_service import estimate_total


def test_estimate_total_with_full_pages_only():
    """While every page is full, return seen + 50 (one more page assumed)."""
    assert estimate_total(seen=50, last_page_size=50) == 100
    assert estimate_total(seen=200, last_page_size=50) == 250


def test_estimate_total_with_short_last_page():
    """A partial last page is the end. Return exactly seen."""
    assert estimate_total(seen=237, last_page_size=37) == 237


def test_estimate_total_with_zero_seen():
    """Before any page lands, fall back to a default."""
    assert estimate_total(seen=0, last_page_size=0) == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py -k estimate --no-cov -v`
Expected: ImportError.

- [ ] **Step 3: Add `estimate_total` to `application/sync_service.py`**

```python
def estimate_total(*, seen: int, last_page_size: int) -> int:
    """Estimate the total number of meetings in the source.

    seen: number of meetings yielded so far.
    last_page_size: number of items in the most recent page.

    Heuristic:
    - If last_page_size < PAGE_SIZE → that was the final page → total == seen.
    - If last_page_size == PAGE_SIZE → at least one more page exists → total >= seen + PAGE_SIZE.
    - If seen == 0 → default to PAGE_SIZE (just a placeholder).
    """
    if seen == 0:
        return PAGE_SIZE
    if 0 < last_page_size < PAGE_SIZE:
        return seen
    return seen + PAGE_SIZE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/application/test_sync_service.py -k estimate --no-cov -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/application/sync_service.py tests/application/test_sync_service.py
git commit -m "feat(sync): estimate_total helper for bootstrap progress"
```

---

### Task 2: Track `last_page_size` on `CurrentSyncSnapshot` + surface estimate

**Files:**
- Modify: `firefliesclearer/web/routes/sync.py` (extend snapshot + status dict)
- Modify: `firefliesclearer/application/sync_service.py` (update snapshot during run)
- Test: `tests/web/routes/test_sync.py`

For the banner to show "1273 of approximately 2000", the running snapshot needs `last_page_size`. SyncService updates it after every page; `_build_status_dict` derives `estimated_total`.

- [ ] **Step 1: Write the failing test**

Add to `tests/web/routes/test_sync.py`:

```python
def test_status_includes_estimated_total_during_running(configured_app_sync_on):
    from datetime import UTC, datetime
    from firefliesclearer.web.routes.sync import CurrentSyncSnapshot

    configured_app_sync_on.state.current_sync = CurrentSyncSnapshot(
        run_id=1, mode="full", trigger_source="bootstrap",
        started_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        meetings_seen=100, meetings_added=100,
        last_page_size=50,
    )
    with TestClient(configured_app_sync_on) as client:
        client.get("/?token=T", follow_redirects=False)
        body = client.get("/sync/status").json()
        assert body["estimated_total"] == 150
        assert body["is_bootstrap"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/web/routes/test_sync.py::test_status_includes_estimated_total_during_running --no-cov -v`
Expected: FAIL — `last_page_size` not on snapshot.

- [ ] **Step 3: Extend `CurrentSyncSnapshot` and `_build_status_dict`**

In `firefliesclearer/web/routes/sync.py`, change `CurrentSyncSnapshot`:

```python
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
    last_page_size: int = 0
```

In `_build_status_dict`, when `current is not None`, add to the returned dict:

```python
        from firefliesclearer.application.sync_service import estimate_total
        estimated_total = estimate_total(
            seen=current.meetings_seen, last_page_size=current.last_page_size,
        )
```

And include in the response payload:

```python
            "estimated_total": estimated_total,
            "is_bootstrap": current.trigger_source == "bootstrap",
```

- [ ] **Step 4: Update `SyncService` to write `last_page_size`**

In `firefliesclearer/application/sync_service.py`, the run methods need a way to publish snapshot updates. Add a parameter to `SyncService.__init__`:

```python
class SyncService:
    def __init__(
        self,
        *,
        repo: object,
        manifest: Manifest,
        clock: Clock,
        snapshot_callback: Callable[[int, int, int, int, int, int], None] | None = None,
    ) -> None:
        ...
        self._snapshot_callback = snapshot_callback
```

(Signature: `(seen, added, updated, gone, last_page_size, meetings_seen) -> None`. The web layer passes a callback that updates `app.state.current_sync`.)

After each `record_sync_progress` call inside `_run_incremental` and `_run_full`, also call:

```python
            if self._snapshot_callback is not None:
                self._snapshot_callback(
                    seen, added, updated, 0,  # gone tallied at end of full
                    len(page),  # last_page_size
                )
```

In the trigger handler in `firefliesclearer/web/routes/sync.py:trigger_sync`, pass a callback when constructing the service:

```python
    def _update_snapshot(seen, added, updated, gone, last_page_size, _meetings_seen):
        if request.app.state.current_sync is not None:
            snap = request.app.state.current_sync
            snap.meetings_seen = seen
            snap.meetings_added = added
            snap.meetings_updated = updated
            snap.meetings_gone = gone
            snap.last_page_size = last_page_size

    service = SyncService(
        repo=deps.client, manifest=deps.manifest, clock=deps.clock,
        snapshot_callback=_update_snapshot,
    )
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/pytest.exe --no-cov -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add firefliesclearer/application/sync_service.py firefliesclearer/web/routes/sync.py tests/web/routes/test_sync.py
git commit -m "feat(sync): track last_page_size for progress estimate

CurrentSyncSnapshot now carries last_page_size. _build_status_dict
derives estimated_total via estimate_total() and surfaces is_bootstrap
when trigger_source == 'bootstrap'. SyncService gets an optional
snapshot_callback so the web layer can keep the snapshot fresh
without coupling the pure algorithm to FastAPI app state."
```

---

### Task 3: Bootstrap banner variant

**Files:**
- Modify: `firefliesclearer/web/templates/partials/_sync_banner.html`
- Modify: `firefliesclearer/web/static/styles.css`

When `sync_status.is_bootstrap == True` and `state == 'running'`, render the richer copy.

- [ ] **Step 1: Update the partial**

In `firefliesclearer/web/templates/partials/_sync_banner.html`, find the `{% elif state == "running" %}` branch and split it into bootstrap vs ordinary running:

```html
  {% elif state == "running" and sync_status.is_bootstrap %}
    <span class="sync-banner__spinner" aria-hidden="true"></span>
    <span class="sync-banner__msg">
      First-time sync — fetching your meetings from Fireflies.
      <strong>{{ sync_status.meetings_seen }}</strong> of approximately
      <strong>{{ sync_status.estimated_total }}</strong> cached so far.
      You can use the cleanup wizard already; older meetings will appear as
      the sync continues.
    </span>

  {% elif state == "running" %}
    <span class="sync-banner__spinner" aria-hidden="true"></span>
    <span class="sync-banner__msg">
      Syncing… <strong>{{ sync_status.meetings_seen }}</strong> seen,
      <strong>{{ sync_status.meetings_added }}</strong> added,
      <strong>{{ sync_status.meetings_updated }}</strong> updated.
    </span>
```

- [ ] **Step 2: Add bootstrap variant style (optional polish)**

Append to `firefliesclearer/web/static/styles.css`:

```css
.sync-banner--running.sync-banner--bootstrap { background: #cfe2ff; color: #052c65; }
```

And in the partial, change the wrapper class to include `sync-banner--bootstrap` when `is_bootstrap`:

```html
<div id="sync-banner"
     class="sync-banner sync-banner--{{ state }}{% if sync_status and sync_status.is_bootstrap %} sync-banner--bootstrap{% endif %}"
     ...
```

- [ ] **Step 3: No tests at this layer (template-only)**

- [ ] **Step 4: Smoke test**

Run: `.venv/Scripts/pytest.exe --no-cov -q`
Expected: still all green.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/templates/partials/_sync_banner.html firefliesclearer/web/static/styles.css
git commit -m "feat(web): bootstrap banner variant with progress estimate"
```

---

### Task 4: Start scheduler on lazy `get_deps`

**Files:**
- Modify: `firefliesclearer/web/deps.py`
- Test: `tests/web/test_deps.py`

In Phase 2 we wired the scheduler into `serve_cmd`'s eager-deps path. Lazy deps (post-setup-wizard scenario) didn't get the scheduler. Phase 5 closes that gap so the bootstrap fires when the user finishes setup.

- [ ] **Step 1: Write the failing test**

Add to `tests/web/test_deps.py`:

```python
async def test_get_deps_starts_scheduler_when_sync_enabled(tmp_path: Path) -> None:
    """Lazy deps build with [sync] enabled creates a scheduler task."""
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive_root.as_posix()}"
[sync]
enabled = true
""",
        encoding="utf-8",
    )
    repo = InMemoryMeetingRepository(api_key="ff_test")
    app = create_app(
        session_token="T", csrf_secret="S",
        config_path=config_path, repo_factory=lambda _key: repo,
    )
    app.state.deps = None

    request = _make_request(app)
    await get_deps(request)

    # After lazy build, the scheduler task is on app.state and not done
    assert hasattr(app.state, "sync_scheduler_task")
    assert app.state.sync_scheduler_task is not None
    # Cleanup: signal shutdown so the task ends
    app.state.sync_shutdown_event.set()
    await app.state.sync_scheduler_task


async def test_get_deps_does_not_start_scheduler_when_sync_disabled(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive_root.as_posix()}"
""",
        encoding="utf-8",
    )
    repo = InMemoryMeetingRepository(api_key="ff_test")
    app = create_app(
        session_token="T", csrf_secret="S",
        config_path=config_path, repo_factory=lambda _key: repo,
    )
    app.state.deps = None

    request = _make_request(app)
    await get_deps(request)

    # Scheduler not started when flag is off
    assert getattr(app.state, "sync_scheduler_task", None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest.exe tests/web/test_deps.py -k scheduler --no-cov -v`
Expected: 2 FAILs.

- [ ] **Step 3: Update `get_deps`**

In `firefliesclearer/web/deps.py`, after the `request.app.state.deps = deps` assignment in the lazy-build branch, add:

```python
    if config.sync.enabled and getattr(request.app.state, "sync_scheduler_task", None) is None:
        import asyncio
        from firefliesclearer.application.sync_service import SyncService
        from firefliesclearer.infra.sync_scheduler import run_scheduler

        sync_service = SyncService(repo=client, manifest=manifest, clock=clock)
        request.app.state.sync_service = sync_service
        request.app.state.sync_shutdown_event = asyncio.Event()
        request.app.state.sync_scheduler_task = asyncio.create_task(
            run_scheduler(
                sync_service=sync_service, manifest=manifest,
                config=config.sync, clock=clock,
                shutdown_event=request.app.state.sync_shutdown_event,
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest.exe tests/web/test_deps.py --no-cov -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/web/deps.py tests/web/test_deps.py
git commit -m "feat(web): start sync scheduler on lazy get_deps when enabled

Closes the post-setup-wizard gap: when the user completes setup
with [sync] enabled = true, the scheduler kicks off on the first
dashboard request and detects the empty cache → bootstrap full sync."
```

---

### Task 5: Bootstrap end-to-end test

**Files:**
- Test: `tests/web/test_deps.py` (extend) or new `tests/web/routes/test_bootstrap.py`

Cover the path: empty cache + sync enabled + first request → scheduler fires → bootstrap sync → banner shows is_bootstrap=true.

- [ ] **Step 1: Write the test**

Add to `tests/web/test_deps.py`:

```python
async def test_lazy_deps_with_empty_cache_triggers_bootstrap_sync(tmp_path: Path) -> None:
    """End-to-end: empty cache + sync enabled → scheduler runs bootstrap."""
    from firefliesclearer.core.models import Meeting
    from datetime import UTC, datetime as _dt
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[fireflies]
api_key = "ff_test"
[archive]
root_dir = "{archive_root.as_posix()}"
[sync]
enabled = true
incremental_interval_hours = 24
""",
        encoding="utf-8",
    )
    repo = InMemoryMeetingRepository(
        meetings=[
            Meeting(
                meeting_id="m0", title="t", meeting_date=_dt(2026, 4, 1, tzinfo=UTC),
                duration_minutes=30.0, host_email="a@x", participant_count=2,
                tags=(), has_transcript=True,
            )
        ],
        api_key="ff_test",
    )
    repo.set_user_email_for_key("ff_test", "oskar@example.com")

    # InMemoryMeetingRepository's list_meetings doesn't match the
    # ControllableMeetingRepository.list_meetings_page contract that
    # SyncService uses. For this test, monkey-patch a thin shim:
    async def _list_page(skip, limit, to_date=None):
        for i, m in enumerate(repo._meetings.values()):
            if i < skip:
                continue
            if i >= skip + limit:
                break
            yield m
    repo.list_meetings_page = _list_page  # type: ignore[attr-defined]

    app = create_app(
        session_token="T", csrf_secret="S",
        config_path=config_path, repo_factory=lambda _key: repo,
    )
    app.state.deps = None

    request = _make_request(app)
    await get_deps(request)

    # Wait up to a few seconds for the scheduler to fire bootstrap
    import asyncio
    for _ in range(50):
        await asyncio.sleep(0.05)
        last = app.state.deps.manifest.get_last_sync_run()
        if last is not None and last.outcome == "success":
            break
    last = app.state.deps.manifest.get_last_sync_run()
    assert last is not None
    assert last.trigger_source == "bootstrap"
    assert last.outcome == "success"

    # Cleanup
    app.state.sync_shutdown_event.set()
    await app.state.sync_scheduler_task
```

(`InMemoryMeetingRepository.list_meetings_page` is monkey-patched here for test simplicity. The real production repo is `FirefliesClient` whose `list_meetings_page` will be added in a small production-side adapter task — see Task 6 below.)

- [ ] **Step 2: Run test**

Run: `.venv/Scripts/pytest.exe tests/web/test_deps.py::test_lazy_deps_with_empty_cache_triggers_bootstrap_sync --no-cov -v`
Expected: probably FAILs because `FirefliesClient` doesn't expose `list_meetings_page`. That's fixed in Task 6.

- [ ] **Step 3: Skip and proceed to Task 6** (or implement Task 6 first then re-run this test).

---

### Task 6: `FirefliesClient.list_meetings_page` for `SyncService` consumption

**Files:**
- Modify: `firefliesclearer/infra/fireflies_client.py`
- Test: `tests/infra/test_fireflies_client.py`

`SyncService` calls `repo.list_meetings_page(skip=, limit=, to_date=)` — the contract `ControllableMeetingRepository` exposes. `FirefliesClient` only has `list_meetings(filter)` today. Add a thin `list_meetings_page` that maps to the same underlying GraphQL query.

- [ ] **Step 1: Write the failing test**

Add to `tests/infra/test_fireflies_client.py`:

```python
async def test_list_meetings_page_with_skip_limit_to_date(monkeypatch):
    """list_meetings_page sends the GraphQL query with the right variables."""
    from firefliesclearer.infra.fireflies_client import FirefliesClient
    captured: list[dict] = []

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"data": {"transcripts": []}}

    class FakeClient:
        async def post(self, url, json, headers):
            captured.append(json)
            return FakeResp()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None

    monkeypatch.setattr(
        "firefliesclearer.infra.fireflies_client.httpx.AsyncClient",
        lambda **kw: FakeClient(),
    )
    client = FirefliesClient(api_key="ff_test")
    from datetime import UTC, datetime
    to_date = datetime(2026, 5, 2, tzinfo=UTC)

    page = [m async for m in client.list_meetings_page(skip=100, limit=25, to_date=to_date)]
    assert page == []
    assert len(captured) == 1
    assert captured[0]["variables"]["skip"] == 100
    assert captured[0]["variables"]["limit"] == 25
    assert captured[0]["variables"]["toDate"] == "2026-05-02T00:00:00+00:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest.exe tests/infra/test_fireflies_client.py -k list_meetings_page --no-cov -v`
Expected: FAIL.

- [ ] **Step 3: Implement `list_meetings_page`**

In `firefliesclearer/infra/fireflies_client.py`, add to the `FirefliesClient` class:

```python
async def list_meetings_page(
    self,
    *,
    skip: int,
    limit: int,
    to_date: datetime | None = None,
) -> AsyncIterator[Meeting]:
    """Single-page list — used by SyncService for explicit pagination control.

    Differs from list_meetings (which auto-paginates internally): the caller
    drives skip and limit, so SyncService can persist a cursor and resume.
    """
    async with self._http() as client:
        variables: dict[str, Any] = {
            "limit": limit,
            "skip": skip,
            "toDate": to_date.isoformat() if to_date is not None else None,
        }
        payload = await self._request(
            client, LIST_QUERY, variables, op="list_meetings_page"
        )
        for raw in payload.get("data", {}).get("transcripts", []) or []:
            yield _meeting_from_raw(raw)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/pytest.exe tests/infra/test_fireflies_client.py tests/web/test_deps.py --no-cov -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add firefliesclearer/infra/fireflies_client.py tests/infra/test_fireflies_client.py
git commit -m "feat(fireflies): list_meetings_page for explicit pagination

Wraps the existing LIST_QUERY with caller-controlled skip+limit+toDate
so SyncService can persist a cursor and resume after rate-limit
interruption."
```

---

### Task 7: Final verification

- [ ] **Step 1: Full pytest suite**

Run: `.venv/Scripts/pytest.exe --no-cov -q`
Expected: all PASS.

- [ ] **Step 2: mypy + ruff**

Run: `.venv/Scripts/mypy.exe firefliesclearer && .venv/Scripts/ruff.exe check firefliesclearer tests && .venv/Scripts/ruff.exe format --check firefliesclearer tests`
Expected: clean.

- [ ] **Step 3: Sign off**

Phase 5 complete. The cache + sync + UI all work end-to-end with `[sync] enabled = true` set manually. Phase 6 flips the default-on switch and cleans up legacy code paths.
