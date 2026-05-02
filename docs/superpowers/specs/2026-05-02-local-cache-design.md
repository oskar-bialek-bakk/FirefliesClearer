# Local-cache architecture — validated design

**Status:** approved 2026-05-02 (supersedes the brainstorming draft at `docs/local-cache-plan.md`)
**Author:** brainstorming session with @oskar.bialek, conducted via the superpowers brainstorming skill on 2026-05-02

---

## Goals

The app is unusable when Fireflies rate-limits the account, because every list, filter, scan, paginate, toggle, and review-table render hits the live API. A single trip through the cleanup wizard can issue dozens of API calls before the user even confirms the selection.

This design moves the read path entirely off the API. The local SQLite database (`manifest.db`) becomes the source of truth for read concerns: list, filter, sort, paginate, select. The Fireflies API is touched only for:

- (a) periodic background sync (incremental + occasional full reconciliation),
- (b) archive artifact downloads (audio / summary / transcript) when the user archives a meeting,
- (c) `deleteTranscript` mutation when the user purges a meeting,
- (d) manual "Sync now" button on the review page when the user wants the latest data on demand.

Selected scope (from the brainstorming session):

- **Scope tier C — durable archive.** The local DB retains rows for meetings later deleted in Fireflies (out of band) so the user has a permanent searchable history. Cache rows are never DELETEd; only their state changes.
- **Cached field tier A — metadata only.** Each row carries enough to filter / sort / paginate / render the review row. Transcript text and summary HTML are not cached; archived meetings still have full artifacts on disk via the existing pipeline.
- **Sync engine tier B — incremental + periodic full reconciliation.** Cheap incremental pass (find new meetings, halt at first known ID) on every sync trigger; expensive full reconciliation pass (detect updates, mark gone-from-source rows) on a slower schedule.
- **Bootstrap tier B — soft block + partial visibility.** First-run sync runs in the background while the dashboard remains usable. A persistent banner shows progress; rate-limit pauses are visible but never block.
- **Storage layout — single DB, single table.** All meeting data lives in the existing `meetings` table extended with snapshot columns and a new `source_state` axis. Operations and sync state live side-by-side in the same row. No separate cache table, no JOINs.

## Non-goals

- Multi-device cache reconciliation. Single-machine app.
- Realtime push from Fireflies (no webhook subscription). Polling-based sync only.
- Offline mutation queueing. Archive and delete still require an online API.
- Caching transcript text or summary HTML. (Deferred — would be a future scope upgrade from tier A to tier B.)

## Edge cases

| Case | Behavior |
|------|----------|
| User archives via this app | API call (`fetch_artifacts`) succeeds → existing pipeline writes artifacts to disk → existing `manifest.transition(..., to=ARCHIVED)`. No drift. |
| User purges via this app | API call (`delete_meeting`) succeeds → `manifest.transition(..., to=DELETED)` AND `manifest.set_source_state(meeting_id, 'gone')` in the same transaction. No drift window. |
| New meeting recorded in Fireflies after last sync | Invisible until next sync. Banner displays "last synced: Nh ago"; "Sync now" button on review page triggers an immediate incremental sync. |
| Meeting deleted in Fireflies UI directly (out of band) | Detected only by full reconciliation. Row's `source_state` flips to `gone`; `op_state` unchanged. UI surfaces a "deleted in Fireflies elsewhere" badge for transparency. |
| Meeting metadata edited in Fireflies (title, tags, host) | Detected only by full reconciliation. `update_cache_fields` overwrites snapshot columns. |
| Sync hits rate limit mid-page | Per-page transactions: pages already committed are durable. Current `cursor_skip` and `next_resume_at` persisted on the `sync_runs` row with `outcome='partial'`. Scheduler resumes from cursor when `now >= next_resume_at`. |
| Crash mid-sync | Per-page transactions mean ≤ one page is lost. On next start, scheduler sees `outcome='running'` (never finalised) → marks as `failed`, restarts fresh. |
| API key invalidated | Sync `outcome='failed'`, banner shows the error; cache stays usable for read until user re-authenticates. |
| User opens cleanup wizard during sync | Wizard reads from the cache, sees whatever has been committed so far. Banner indicates sync is still running so the user knows newer rows are coming. |
| Bootstrap exhausts Fireflies daily quota | `outcome='partial'`, `next_resume_at` set to retry-after window. Banner says "Paused — resumes at HH:MM. M of approximately N cached so far." Wizard remains usable on partial data. |
| Meeting reappears in Fireflies after being marked `source_state='gone'` (e.g., user un-deleted via Fireflies UI) | Sync detects the meeting in the page response → flips `source_state` back to `live` and counts it as `meetings_added`. |

## Architecture

### Layering (extends the existing v2 ports-and-adapters topology)

```
┌────────────────────────────────────────────────────┐
│ Presentation                                       │
│  cli/  ·  web/                                     │
├────────────────────────────────────────────────────┤
│ application/                                       │
│  scan_service*    archive_service                  │
│  purge_service    audit_service                    │
│  preset_service   setup_service                    │
│  sync_service**  ◄── NEW                           │
├────────────────────────────────────────────────────┤
│ core/  (domain — pure)                             │
│  models           pipeline                         │
│  rules            archiver                         │
│  manifest*  ◄── extended schema + new transitions  │
├────────────────────────────────────────────────────┤
│ ports/                    infra/                   │
│  clock                     system_clock            │
│  meeting_repository        fireflies_client        │
│  summary_renderer          pdf_renderer            │
│                            sync_scheduler**  ◄ NEW │
│                            config · logging        │
└────────────────────────────────────────────────────┘

* modified by this design   ** new in this design
```

### New modules

- **`application/sync_service.py`** — pure orchestration. Takes a `MeetingRepository` (live API) and a `Manifest` (cache). Runs `INCREMENTAL` or `FULL` passes. Writes results into `meetings` and `sync_runs`. No HTTP, no scheduler, no UI.
- **`infra/sync_scheduler.py`** — asyncio task started by `serve_cmd`. Sleeps until next-run time, calls `sync_service.run(...)`, persists outcome. Wraps the pure service with infrastructure concerns (clock-based scheduling, error reporting).

### Data flow

- **Read paths** — `ScanService`, dashboard `AuditService`, history page, all CLI scan/run commands. All read exclusively from `Manifest`. Zero API calls during user interaction.
- **Mutate paths** — `ArchiveService.archive_meetings(...)` and `PurgeService.purge_meetings(...)` still call `FirefliesClient.fetch_artifacts` and `FirefliesClient.delete_meeting`. After mutation, they update the row's `op_state` (and `source_state` on successful delete).
- **Sync paths** — `SyncService` triggered by scheduler or manual button → calls `FirefliesClient.list_meetings` → upserts into `Manifest`.

### Three things that don't change

- `core/pipeline.py`, `core/archiver.py`, `core/rules.py`, `RuleEngine` — entirely unaffected. The existing scan-and-archive logic operates on `Meeting` value objects, which we now hand it from the cache instead of from the API.
- `ports/meeting_repository.MeetingRepository` — stays as the API-facing contract. We don't add a "read from cache" method to this port; cache reads happen directly off `Manifest`.
- The existing `MeetingState.{PENDING, ARCHIVED, DELETED, FAILED_*, DELETED_FAILED}` semantics — we add `KNOWN` as a precursor state, but the rest of the state machine is preserved.

## Schema

### Existing `meetings` table — additive changes

```sql
ALTER TABLE meetings ADD COLUMN duration_minutes  REAL;
ALTER TABLE meetings ADD COLUMN host_email        TEXT;
ALTER TABLE meetings ADD COLUMN organizer_email   TEXT;
ALTER TABLE meetings ADD COLUMN participants_json TEXT;       -- JSON array
ALTER TABLE meetings ADD COLUMN has_transcript    INTEGER;    -- 0/1
ALTER TABLE meetings ADD COLUMN audio_url         TEXT;
ALTER TABLE meetings ADD COLUMN tags_json         TEXT;       -- JSON array
ALTER TABLE meetings ADD COLUMN source_state      TEXT NOT NULL DEFAULT 'live';
ALTER TABLE meetings ADD COLUMN cached_at         TEXT;       -- ISO ts of last sync touch

CREATE INDEX IF NOT EXISTS idx_meetings_source_state ON meetings(source_state);
CREATE INDEX IF NOT EXISTS idx_meetings_meeting_date ON meetings(meeting_date);
CREATE INDEX IF NOT EXISTS idx_meetings_host_email   ON meetings(host_email);
```

The existing `state` column is renamed conceptually (not in SQL) to `op_state` in Python. The SQL column stays `state` to avoid a destructive migration; only Python-side accessors change.

### New `sync_runs` table

```sql
CREATE TABLE IF NOT EXISTS sync_runs (
  id                INTEGER PRIMARY KEY,
  mode              TEXT    NOT NULL,        -- 'incremental' | 'full'
  trigger_source    TEXT    NOT NULL,        -- 'scheduled' | 'manual_review' | 'manual_settings' | 'bootstrap'
  started_at        TEXT    NOT NULL,
  finished_at       TEXT,
  outcome           TEXT    NOT NULL,        -- 'running' | 'success' | 'partial' | 'failed'
  meetings_seen     INTEGER NOT NULL DEFAULT 0,
  meetings_added    INTEGER NOT NULL DEFAULT 0,
  meetings_updated  INTEGER NOT NULL DEFAULT 0,
  meetings_gone     INTEGER NOT NULL DEFAULT 0,
  cursor_skip       INTEGER,                 -- for partial; null otherwise
  seen_ids_json     TEXT,                    -- for resumed full sync; null otherwise
  next_resume_at    TEXT,                    -- ISO ts (rate-limit retry); null otherwise
  error_message     TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_started_at ON sync_runs(started_at DESC);
```

### State machine — additions to `MeetingState` and `LEGAL_TRANSITIONS`

```python
class MeetingState(StrEnum):
    KNOWN           = "known"           # NEW — synced from source, no operation initiated
    PENDING         = "pending"         # unchanged: queued for archive
    ARCHIVED        = "archived"        # unchanged
    FAILED_FETCH    = "failed_fetch"    # unchanged
    FAILED_DOWNLOAD = "failed_download" # unchanged
    FAILED_RENDER   = "failed_render"   # unchanged
    FAILED_VERIFY   = "failed_verify"   # unchanged
    DELETED         = "deleted"         # unchanged
    DELETED_FAILED  = "deleted_failed"  # unchanged

LEGAL_TRANSITIONS = {
    None:                   {KNOWN},                         # NEW first-touch path
    KNOWN:                  {PENDING},                       # NEW: wizard "Continue" picks
    PENDING:                {ARCHIVED, FAILED_*},            # unchanged
    ARCHIVED:               {DELETED, DELETED_FAILED},       # unchanged
    DELETED_FAILED:         {DELETED},                       # unchanged
    FAILED_*:               {PENDING},                       # unchanged (retry)
    DELETED:                set(),                           # unchanged (terminal)
}
```

The old `None → PENDING` first-touch path is removed. Today's `Manifest.register(meeting, at)` (which assumes you're starting an archive) is replaced by two distinct entry points:

- `Manifest.upsert_known(meeting, at, source_state='live')` — sync calls this. Idempotent: creates the row if absent (`op_state='known'`); if it exists, updates the snapshot fields and `cached_at` but does NOT touch `op_state`.
- `Manifest.queue_for_archive(meeting_id, at)` — wizard "Continue" calls this; transitions `KNOWN → PENDING`. The existing `pipeline.archive_one` carries on from there.

Two more new manifest methods:

- `Manifest.set_source_state(meeting_id, source_state)` — flips the `source_state` column without touching anything else.
- `Manifest.update_cache_fields(meeting, at)` — refreshes snapshot fields (title, duration, host, etc.) on an existing row. Returns `True` if any field actually changed (so `SyncService` can count `meetings_updated` accurately).
- `Manifest.list_known(*, older_than=None, include_archived=False, include_gone=False)` — yields `Meeting` objects from cached rows matching predicates. Rows lacking snapshot columns (legacy archive-only rows) are yielded with the fields we DO have and `None` placeholders elsewhere.

### Migration story for existing v2 installs

- The existing manifest has rows with `state ∈ {pending, archived, failed_*, deleted, deleted_failed}` — no `KNOWN` rows yet.
- The `ALTER TABLE … DEFAULT 'live'` clause backfills `source_state='live'` for every existing row.
- New cache columns (`duration_minutes`, `host_email`, etc.) are `NULL` until the next sync; rendering code falls back gracefully (e.g., shows "—" in the table cell).
- One-shot migration runs on `serve` startup (next to `migrate_v1_rules_auto`): adds the columns, adds the indexes, adds `sync_runs`. Idempotent — uses `pragma table_info` to detect already-applied changes before issuing `ALTER TABLE`.

## Sync engine

### `SyncService` interface

```python
class SyncMode(StrEnum):
    INCREMENTAL = "incremental"
    FULL        = "full"

class SyncTrigger(StrEnum):
    SCHEDULED        = "scheduled"
    MANUAL_REVIEW    = "manual_review"
    MANUAL_SETTINGS  = "manual_settings"
    BOOTSTRAP        = "bootstrap"

class SyncService:
    def __init__(
        self,
        *,
        repo: MeetingRepository,        # the live API
        manifest: Manifest,              # also the cache
        clock: Clock,
    ) -> None: ...

    async def run(
        self,
        *,
        mode: SyncMode,
        trigger: SyncTrigger,
        resume_run_id: int | None = None, # set when resuming a partial run
    ) -> SyncOutcome: ...
```

### Incremental algorithm

The cheap path — newly-created meetings only.

```
to_date    = None          # API returns newest-first
skip       = 0
seen_known = False

while not seen_known:
    page = repo.list_meetings(toDate=to_date, skip=skip, limit=50)
    if page is empty: break

    BEGIN TRANSACTION
      for raw in page:
        existing = manifest.get(raw.id)
        if existing is None:
          manifest.upsert_known(raw, at=now)            # adds new row
          run.meetings_added += 1
        elif existing.source_state == 'gone':
          manifest.upsert_known(raw, at=now)            # resurrected on Fireflies
          run.meetings_added += 1                       # treat as add
        else:
          seen_known = True                             # stop after this page
          break
      sync_runs.cursor_skip = skip + len(page)
    COMMIT

    skip += len(page)
```

Stop condition: first time we hit a row that we already have in `source_state='live'`. Since the API returns newest-first, everything after is older and presumably already cached. Updates to existing rows are NOT detected here — that's full mode's job.

### Full reconciliation algorithm

The expensive path — runs weekly by default, or on-demand.

```
to_date    = run.started_at     # pin pagination to start time
skip       = resume_run.cursor_skip if resuming else 0
seen_ids   = resume_run.seen_ids if resuming else set()

while True:
    page = repo.list_meetings(toDate=to_date, skip=skip, limit=50)
    if page is empty: break

    BEGIN TRANSACTION
      for raw in page:
        seen_ids.add(raw.id)
        existing = manifest.get(raw.id)
        if existing is None:
          manifest.upsert_known(raw, at=now)
          run.meetings_added += 1
        else:
          changed = manifest.update_cache_fields(raw, at=now)
          if changed: run.meetings_updated += 1
          if existing.source_state == 'gone':
            manifest.set_source_state(raw.id, 'live')
            run.meetings_added += 1
      sync_runs.cursor_skip   = skip + len(page)
      sync_runs.seen_ids_json = json.dumps(list(seen_ids))
    COMMIT

    skip += len(page)

# Reconciliation step — only when full pass completed
BEGIN TRANSACTION
  for row in manifest.live_rows():
    if row.meeting_id not in seen_ids:
      manifest.set_source_state(row.meeting_id, 'gone')
      run.meetings_gone += 1
COMMIT
```

The `to_date` pin is critical: without it, meetings added during the run shift the pagination window and we'd skip rows. With it, we get a snapshot view of "everything that existed at run start"; meetings added during the run land in the next incremental sync.

### Rate-limit handling

The existing `FirefliesClient.list_meetings` already raises `RateLimitedError(retry_after_seconds=...)`. `SyncService.run` catches it once per run (inside the page loop):

```
except RateLimitedError as e:
  sync_runs.cursor_skip    = skip
  sync_runs.next_resume_at = now + e.retry_after_seconds
  sync_runs.outcome        = 'partial'
  COMMIT
  return SyncOutcome.partial(...)
```

The scheduler checks `next_resume_at` before each tick: if `last_run.outcome == 'partial' and now < last_run.next_resume_at`, skip; if `now >= next_resume_at`, call `SyncService.run(mode=last_run.mode, resume_run_id=last_run.id)` to resume from the saved cursor.

Other failure modes:

- **Network error / 5xx** — treated like rate-limit (`retry_after_seconds=60`), partial sync persisted, scheduler retries.
- **API key invalid (401/403)** — outcome=`failed`, `error_message` populated. UI shows banner; no auto-retry; cache stays usable for read.
- **Crash mid-page** — per-page transactions mean we lose at most one page of progress; on next start, scheduler sees `outcome='running'` (never finalised) → marks as `failed`, restarts fresh.

### Concurrency

One sync at a time. `SyncService.run` acquires `app.state.sync_lock` (an `asyncio.Lock`). If a second trigger arrives while a sync is running, it returns immediately with "sync already running" — UI shows the in-progress row instead of starting a duplicate.

Progress is exposed via `app.state.current_sync` (a small dataclass mirroring `meetings_seen / meetings_added / meetings_updated / meetings_gone / outcome`). `GET /sync/status` returns this.

## Trigger surface and UI

### Three trigger paths, one entry point

```
POST /sync/now              { "mode": "incremental" | "full",
                              "trigger": "manual_review" | "manual_settings" }
GET  /sync/status           → JSON { state, mode, started_at, meetings_seen,
                                     added, updated, gone, next_resume_at,
                                     last_run_finished_at }
```

`POST /sync/now`:

- Acquires `app.state.sync_lock` non-blocking. If already held → 409 with `{ "current_run_id": ... }`.
- Spawns `asyncio.create_task(SyncService.run(...))` and returns 202 + the same payload as `/sync/status`.
- The handler returns immediately; the task runs in the event-loop background.

`GET /sync/status` is a cheap read of `app.state.current_sync` plus the latest row in `sync_runs`. Used by the polling banner.

### Trigger sources

**1. Background scheduler** (`infra/sync_scheduler.py`)

A single asyncio task started in `serve_cmd` after deps are built (gated on `[sync] enabled = true`). Loops:

```
while not shutdown:
  next_tick = compute_next(last_run, config)
  await sleep_until(next_tick)
  if last_run.outcome == 'partial' and now < last_run.next_resume_at:
    continue   # rate-limit window not over
  await SyncService.run(mode=decide_mode(now, config), trigger='scheduled')
```

Two parallel schedules; whichever is due first wins. When both are due simultaneously, full wins.

- `incremental_interval_hours = 6` — next incremental tick = `last_completed_run.finished_at + 6h` (regardless of mode; a full counts as having covered incremental too).
- `full_interval_days = 7` and `full_run_hour_local = 3` — next full tick = the first occurrence of `HH:MM = 03:00` in local time that falls at least 7 days after the previous successful full run. Default chosen so the heavy pass lands in the middle of the night and doesn't compete with daytime use.

`compute_next(last_run, config) = min(next_incremental, next_full)`. `decide_mode(now)` returns `full` if `now >= next_full`, else `incremental`. Bootstrap is treated as a full run for purposes of `next_full` computation.

All five values are configurable in the `[sync]` block of `config.toml`. Setting `full_interval_days = 0` disables full reconciliation (incremental-only mode) — see Deferrals.

**2. Review-page button** — primary manual trigger

In `_review_toolbar.html`, alongside the existing bulk-action buttons:

```html
<button hx-post="/sync/now"
        hx-vals='{"mode":"incremental","trigger":"manual_review"}'
        hx-include="closest form"
        hx-target="#sync-banner"
        hx-swap="outerHTML">
  ↻ Sync now
</button>
```

The button is always present; clicking always runs incremental (cheap).

A persistent `#sync-banner` element near the top of the review page renders one of three states:

- **idle** — small line: "Last synced: 2h ago" (or "never" pre-bootstrap), with a subtle ↻ icon.
- **running** — progress bar + "Syncing… N meetings fetched" + spinner. HTMX polls `/sync/status` every 2 s until `state ≠ 'running'`.
- **partial / paused** — "Sync paused: rate-limited until 14:32. M of N meetings synced so far." Page is still usable.

`partials/_sync_banner.html` is shared between the review page and the dashboard.

**3. Settings page — Full re-sync button**

A button on `web/templates/settings/index.html` labelled "Full re-sync now (slow, ~N API calls)". POSTs `/sync/now` with `mode=full, trigger=manual_settings`. Lower visual prominence — accuracy-tier feature, not day-to-day.

**4. Bootstrap (first run) — automatic**

The scheduler starts as soon as deps are first built — either eagerly in `serve_cmd` (when config exists at boot), or lazily in `web/deps.get_deps` (when config is written mid-serve by the setup wizard). On its first tick after start, the scheduler checks: if `meetings` has zero rows AND `sync_runs` has zero rows → run with `mode='full', trigger='bootstrap'`. Otherwise the normal `compute_next(...)` logic applies.

This means: scenario A (existing config) starts the scheduler at server boot; scenario B (post-setup-wizard) starts it when the first dashboard request triggers lazy `get_deps`. Both paths converge on the same scheduler-driven bootstrap.

The bootstrap banner is a richer variant of the running-state banner:

- "First-time sync — fetching your meetings from Fireflies."
- "1273 of approximately 2000 cached so far. You can use the cleanup wizard already; older meetings will appear as the sync continues."
- Same pause-on-rate-limit messaging if the bootstrap hits a quota.

The "approximately N" is honest — the API doesn't tell us total count up front. We update the estimate by extrapolating from the first few pages.

### Trigger summary

| Trigger source       | Mode        | Endpoint              | UI affordance                                |
|----------------------|-------------|-----------------------|----------------------------------------------|
| Scheduler tick       | incremental | (internal task)       | none — silent unless rate-limited            |
| Scheduler tick (≥7d) | full        | (internal task)       | banner shows "weekly full sync running"      |
| Review page button   | incremental | `POST /sync/now`      | banner + button                              |
| Settings button      | full        | `POST /sync/now`      | settings page section + banner appears       |
| Bootstrap            | full        | (internal task)       | bootstrap banner (richer, persistent)        |

## Read-path migration

### `ScanService` becomes the canonical user of cache

```python
# Before
class ScanService:
    def __init__(self, repo: MeetingRepository, clock: Clock) -> None: ...
    async def scan(self, filters):
        async for meeting in self._repo.list_meetings(MeetingFilter(older_than=cutoff)):
            ...

# After
class ScanService:
    def __init__(self, manifest: Manifest, clock: Clock) -> None: ...
    async def scan(self, filters):
        for meeting in self._manifest.list_known(older_than=cutoff):
            ...
```

The `scan()` method stays `async` (so callsites don't change) but the body becomes sync — `Manifest.list_known()` is a SQLite read, microseconds, not worth `async` ceremony.

### Wizard scan semantics

- The cleanup wizard (`GET /cleanup/review` and friends) reads exclusively from `Manifest.list_known()`. Zero API calls during normal flow. Filter, sort, paginate, toggle, select-all, invert — all microsecond-level cache reads.
- The "Sync now" button on the review page is the only mechanism for a user to refresh data mid-flow. After a successful incremental sync, the table re-renders with newly-cached meetings.
- The existing `?error=empty-selection` and inline-error rendering paths stay; the only difference is `_scan_or_error` no longer ever returns a `RateLimitedError` (because no API call happens). The `scan_error` variable can drop the rate-limit branch.

Side panel (`GET /cleanup/meeting/{id}/panel`) currently just looks up the in-memory match — no API call. **Stays unchanged.** It already shows only metadata; transcript text was never displayed there.

### Mutation paths — what changes, what doesn't

`ArchiveService` and `PurgeService` (in `application/`) still call `FirefliesClient.fetch_artifacts` and `delete_meeting`. The only diffs:

- After `archive_one` succeeds: existing `manifest.transition(..., to=ARCHIVED)` is unchanged. The cache row now also has snapshot fields (already populated by sync, or hydrated from the just-archived meeting object — either works).
- After `purge_one` succeeds (Fireflies API delete returns OK): existing `manifest.transition(..., to=DELETED)` plus a new `manifest.set_source_state(meeting_id, 'gone')` in the same transaction. Row stays — we only flip the flag.
- On `purge_one` failure: existing `DELETED_FAILED` state, `source_state` unchanged ('live') — accurately reflects "we tried to delete it from Fireflies but failed".

### CLI parity

`firefliesclearer scan` and `firefliesclearer run` (CLI commands) similarly flip to reading from cache. They use the same `ScanService`; the wiring change is in `cli/_common.build_deps`.

New CLI command `firefliesclearer sync [--full]` — calls `SyncService.run(...)` directly, blocks until done, prints summary. Useful for cron-style automation outside the web app. Re-uses the same service; no code duplication.

### Dashboard / history

- Dashboard summary (`AuditService.summary()`) — already reads from manifest. **Unchanged.**
- History view (`/history`) — already reads from manifest. With this design, it now naturally surfaces both `op_state=DELETED` rows (we deleted them) and `source_state=GONE` rows (Fireflies UI did) under the same "history" page, with a small badge to distinguish source. Tiny template change.

## Testing strategy

### Critical invariants — direct test coverage required

| Invariant | Test type | Location |
|-----------|-----------|----------|
| No meeting row is ever DELETEd from the table | Property test | `tests/core/test_manifest.py` |
| `op_state` and `source_state` change independently | Unit per state pair | `tests/core/test_manifest.py` |
| Sync per-page transaction: crash mid-sync loses ≤ one page | Simulated crash | `tests/application/test_sync_service.py` |
| Incremental sync stop condition: hits known live row, halts | Fake repo with mixed rows | `tests/application/test_sync_service.py` |
| Full sync reconciliation: rows missing from API → `gone` | Fake repo with deletions | `tests/application/test_sync_service.py` |
| `to_date` pin on full sync: meetings added during run land in NEXT incremental, not lost | Time-travel test with clock injection | `tests/application/test_sync_service.py` |
| Rate-limit mid-sync: cursor + `next_resume_at` persisted, scheduler resumes from cursor | Unit + integration | `tests/application/test_sync_service.py` + `tests/infra/test_sync_scheduler.py` |
| Wizard reads make zero API calls | Test repo asserting `call_count == 0` | `tests/web/routes/test_cleanup_step1.py` (extend) |
| Schema migration is idempotent (re-runnable safely) | Run twice on same DB | `tests/core/test_manifest.py` |
| `Manifest.list_known()` predicates: `include_archived`, `include_gone`, `older_than` | Table-driven | `tests/core/test_manifest.py` |
| `purge_one` success → `op_state=DELETED` AND `source_state=gone` in one transaction | Pipeline integration test | `tests/core/test_pipeline.py` |

The "test repo asserting `call_count == 0`" is the regression net for the read-path flip: every existing wizard test gets a guard that fails if `FirefliesClient.list_meetings` is invoked during the wizard flow.

### Coverage targets

Existing hard target (per `CLAUDE.md`: 100% on `core/pipeline.py` + `core/manifest.py`) extends to the new manifest methods (`upsert_known`, `set_source_state`, `update_cache_fields`, `list_known`).

New module coverage:

- `application/sync_service.py` — 100% (all algorithm; no IO).
- `infra/sync_scheduler.py` — 80%+ (cover decision logic, not actual sleep).
- New web routes (`POST /sync/now`, `GET /sync/status`) — full integration coverage in `tests/web/routes/test_sync.py` (new file).

## Phased rollout

Each phase ships as one PR, mergeable in isolation.

**Phase 1 — Schema + state machine** (no behavior change)

- Extend `meetings` table with new columns + indexes; add `sync_runs` table; add `KNOWN` state + new transitions.
- Manifest gets `upsert_known`, `set_source_state`, `update_cache_fields`, `list_known` (initially unused outside tests).
- Migration runs on `serve` startup (next to `migrate_v1_rules_auto`); idempotent.
- Existing tests must all still pass — this phase strictly ADDs.
- ~400 LOC + tests.

**Phase 2 — `SyncService` + scheduler** (behavior change: API gets called more)

- New `application/sync_service.py` (pure algorithm).
- New `infra/sync_scheduler.py` (background asyncio task).
- New `[sync]` config block: `enabled` (default `false`), intervals, `full_run_hour_local`.
- Wired up in `serve_cmd` only when `enabled=true`. Existing users default-off → no change.
- Tests cover the entire algorithm with a fake repo.
- ~500 LOC + tests.

**Phase 3 — Read-path flip** (behavior change behind same `[sync]` flag)

- `ScanService` switches to `Manifest`-based reads when flag is on.
- Wizard, dashboard, history, CLI all read from cache when flag is on.
- "Test repo with `call_count == 0`" guard added to every wizard test.
- Old `MeetingRepository`-based scan path retained behind the flag-off branch (deletable in Phase 6).
- ~250 LOC + tests.

**Phase 4 — Trigger UI** (visible UI changes for flag-on users)

- `POST /sync/now`, `GET /sync/status` endpoints.
- `partials/_sync_banner.html` rendered on review page + dashboard.
- Review-page "Sync now" button.
- Settings page "Full re-sync" button + section.
- HTMX polling (`every 2s` while running).
- ~300 LOC + tests.

**Phase 5 — Bootstrap UX** (visible UI for new flag-on users on first sync)

- Bootstrap-mode banner variant with progress estimate.
- `serve_cmd` schedules immediate `bootstrap` sync when manifest is empty AND flag is on.
- Soft-block: dashboard renders immediately with banner; wizard usable on partial data.
- ~150 LOC + tests.

**Phase 6 — Default-on flip + cleanup**

- Setup wizard writes `[sync] enabled = true`.
- Existing users get a one-time prompt: "Enable local cache for offline cleanup wizard? [Yes / Not now]". On accept: flag flipped, bootstrap kicks off.
- Old `MeetingRepository`-driven scan path deleted.
- New CLI command `firefliesclearer sync [--full]`.
- Documentation updates: `README.md`, `CHANGELOG.md`, `CLAUDE.md` architecture diagram.
- ~200 LOC + cleanup of ~150 LOC of old code.

**Total estimated scope:** ~1.8k LOC net additions (incl. tests), ~150 LOC removed in Phase 6. Six phases over six PR cycles.

The phased shape means: if we hit a real-world problem in Phase 2 (e.g., Fireflies API does something unexpected), it surfaces before we've touched the read path. If Phase 3's read flip causes a regression, only flag-on users see it. By Phase 6, the cache approach has been observed in production by an opt-in user (Oskar) for several phase cycles.

## Resolved decisions

These were open questions in the original draft (`docs/local-cache-plan.md`); the brainstorming session resolved them:

| Question | Resolution |
|----------|-----------|
| Cache-DB location — same `manifest.db` or sibling? | Same DB. Single unified `meetings` table, no separate cache table. |
| Sync interval default | Incremental: 6 h. Full reconciliation: 7 d, aligned to local 03:00. Both configurable in `[sync]` block. |
| `gone_from_source` UX | Surface as a "deleted in Fireflies elsewhere" badge. Never silently drop. |
| Cached field scope | Tier A — metadata only. Transcript / summary text NOT cached. |
| Wizard scan semantics — anything need to bust cache and hit live? | No. Wizard reads cache exclusively; the "Sync now" button is the sole escape hatch. |

## Deferrals / future work

- **Tier B (cache transcripts and summary text)** — not scoped here. If "search across all transcripts" or "view transcript without archiving" becomes a need, that's a future scope upgrade. The schema is a superset of A so it's a non-breaking extension.
- **SQL-side filtering** — currently `Manifest.list_known()` returns all rows and `RuleEngine` filters in memory. Fine up to ~tens of thousands of meetings. Beyond that, push filter clauses to SQL `WHERE`. Out of scope for v1.
- **Multi-account support** — single account assumed throughout. If multi-account is ever needed, `meetings` and `sync_runs` would need an `account_id` column.
- **Webhook ingestion** — Fireflies doesn't currently expose webhooks for meeting lifecycle events. If they do later, sync becomes push-driven and the scheduler can be retired.
- **Incremental-only mode** (skip full reconciliation entirely) — viable if user finds the weekly cost too high. Trades accuracy for budget. Configurable via `[sync] full_interval_days = 0`.
