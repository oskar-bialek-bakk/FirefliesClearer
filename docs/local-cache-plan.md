# Local-cache architecture — planning doc

**Status:** draft for review (2026-05-02)
**Author:** working session with @oskar.bialek

## Problem

The app is unusable when Fireflies rate-limits the account, because every list, filter, scan, paginate, toggle, and review-table render hits `MeetingRepository.list_meetings()` against the live API. A single trip through the cleanup wizard can issue dozens of API calls before the user even confirms the selection.

The user's proposal: treat the API as the source of truth only for **mutation** (archive download + delete) and **explicit pulls** (scheduled sync + a "Sync now" button on the review page), and treat a local DB as the source of truth for **read** (list / filter / sort / paginate / select). Refresh the local cache on a schedule (or on demand), not on every UI interaction.

**API used for:**
- (a) periodic background sync,
- (b) archive artifact downloads (audio / summary / transcript),
- (c) delete on user-confirmed purge,
- (d) manual "Sync now" button on the review page — fetches new meetings on demand when the user wants the latest state without waiting for the scheduler.

## Goals

- UI stays responsive when rate-limited: filtering, scrolling, selection all work offline against the cached data.
- API budget shrinks to: (a) periodic sync, (b) actual archive downloads, (c) actual deletes.
- The cleanup wizard never lists the API mid-flow. Scans evaluate filters against the cache.
- Cache freshness is visible to the user (last sync timestamp + manual "refresh now" button).

## Non-goals

- Multi-device cache reconciliation. Single-machine app.
- Realtime push from Fireflies (no webhook subscription). Polling-based sync only.
- Offline mutation queueing. Archive/delete still require an online API.

## Edge cases — actual list

User correctly pointed out that the obvious "stale cache after delete" concern is a non-issue — our deletes update the cache transactionally on API success. The real edge cases are:

| Case | Behavior |
|------|----------|
| User runs archive/delete from this app | API call succeeds → mark cached row state in `manifest.db` (already happens). No drift. |
| New meeting recorded in Fireflies after last sync | Invisible until next sync. Surface "last sync N min ago" in the UI; "refresh now" button triggers an immediate sync. |
| Meeting deleted in Fireflies UI directly (out of band) | Next sync detects ID missing from API response → either (a) flag row as `gone_from_source`, or (b) silently drop. Recommend (a) so the user notices. |
| Meeting metadata edited in Fireflies (title, tags, host) | Next sync overwrites cached row by `meeting_id`. |
| User runs scan during sync | Sync is a single transaction; reads see the pre-sync snapshot until commit. Standard SQLite WAL behavior. |
| Sync hits rate limit mid-page | Partial sync persisted; mark sync as `partial` with a `cursor` so the next attempt resumes from where it stopped. |
| API key invalidated | Sync fails fast → surface a banner; cache stays usable for read until user re-authenticates. |

## Architecture sketch

### New modules

```
firefliesclearer/
├── application/
│   └── sync_service.py           # NEW — orchestrates pull-from-API → upsert-to-cache
├── infra/
│   ├── meeting_cache.py          # NEW — sqlite-backed CachedMeetingRepository
│   └── sync_scheduler.py         # NEW — async loop running sync at configured interval
└── ports/
    └── meeting_cache.py          # NEW — CachedMeetingRepository protocol (read-only)
```

### Repository topology

Today there is one port — `MeetingRepository` — with one production adapter (`FirefliesClient`).

After:

```
                       ┌─────────────────────┐
read paths ──read───►  │ CachedMeetingRepo   │  ◄─── upsert ─── SyncService
(scan, list, page)     │  (sqlite-backed)    │                       │
                       └─────────────────────┘                       │
                                                                     ▼
                       ┌─────────────────────┐                ┌──────────────┐
mutate paths ──call──► │ MeetingRepository   │ ──API trip───► │  Fireflies   │
(archive download,     │  (FirefliesClient)  │                │     API      │
 delete)               └─────────────────────┘                └──────────────┘
```

`SyncService` reads from `MeetingRepository.list_meetings(MeetingFilter())`, transforms each `Meeting` into a cache row, and upserts. It also reconciles deletions (rows in cache but not in latest API response).

### Cache schema

Add to `manifest.db` (existing) rather than a sibling DB — operations that already touch `manifest` rows can stay transactional. New columns:

```sql
CREATE TABLE IF NOT EXISTS meeting_cache (
  meeting_id        TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  meeting_date      TEXT NOT NULL,
  duration_minutes  REAL NOT NULL,
  host_email        TEXT NOT NULL,
  participant_count INTEGER NOT NULL,
  has_transcript    INTEGER NOT NULL,    -- 0/1
  tags_json         TEXT NOT NULL,        -- JSON array
  audio_url         TEXT,                 -- nullable; only present in v1 meetings
  -- sync bookkeeping
  cached_at         TEXT NOT NULL,        -- ISO timestamp of last sync that wrote this row
  source_state      TEXT NOT NULL DEFAULT 'live'  -- 'live' | 'gone_from_source'
);

CREATE INDEX IF NOT EXISTS idx_meeting_cache_date     ON meeting_cache(meeting_date);
CREATE INDEX IF NOT EXISTS idx_meeting_cache_host     ON meeting_cache(host_email);
CREATE INDEX IF NOT EXISTS idx_meeting_cache_state    ON meeting_cache(source_state);

CREATE TABLE IF NOT EXISTS sync_runs (
  id              INTEGER PRIMARY KEY,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  outcome         TEXT NOT NULL,         -- 'running' | 'success' | 'partial' | 'failed'
  meetings_seen   INTEGER NOT NULL DEFAULT 0,
  meetings_added  INTEGER NOT NULL DEFAULT 0,
  meetings_updated INTEGER NOT NULL DEFAULT 0,
  meetings_gone   INTEGER NOT NULL DEFAULT 0,
  error_message   TEXT
);
```

### Read-path migration

`ScanService.scan(filters)` currently calls `self._repo.list_meetings(...)`. Flip this to use `CachedMeetingRepository`. The rule engine already works over plain `Meeting` objects — no changes needed.

`_scan_or_none()` and `_scan_or_error()` in `web/routes/cleanup.py` become trivially fast (sqlite reads, microseconds) — selection toggles, paginates, and bulk actions all become snappy with no API trips.

### Mutation paths (unchanged)

- `Pipeline.archive_one(meeting)` — fetch artifacts, render, verify, mark `manifest.archived` — exactly as today. After successful archive, also stamp `meeting_cache.source_state = 'archived_locally'` if we want the UI to grey those rows out (optional polish).
- `Pipeline.purge_one(meeting)` — calls `repo.delete_meeting(meeting_id)`. On success: (a) update `meetings.state = deleted`, (b) delete the `meeting_cache` row in the same transaction. No drift window.

### Sync trigger surface

Three trigger points, all plumbing the same `SyncService.run()`:

1. **Background scheduler** — `infra/sync_scheduler.py` runs an asyncio task on `serve` startup that wakes every N hours (config: `[sync] interval_hours = 12`). Conservative default.
2. **Manual refresh** — `POST /sync/now` route, with two button affordances:
   - **Review page (primary)** — "Sync now" button next to the existing select-all / invert / deselect-all toolbar. Most useful here because that's where the user is when they think "is the list current?".
   - **Dashboard sidebar (secondary)** — same endpoint, exposed for users who want to refresh before entering the wizard.

   Both return 202 + a progress fragment that polls `/sync/status` until the run finishes, then re-renders the review table fragment so newly synced meetings appear without a manual page refresh. Reuses the existing `OperationRegistry` machinery (Phase 5).
3. **Empty-cache bootstrap** — first time `serve` starts after upgrade, the cache table is empty. Block the dashboard with a "First-time sync — fetching meetings…" interstitial that fires `SyncService.run()` once before unlocking the UI. Or: let the empty cache through, with a banner saying "No meetings synced yet — click Refresh." (My preference: the latter; less risk if first sync is slow.)

### Rate-limit handling

`SyncService.run()` walks `MeetingRepository.list_meetings(MeetingFilter())` page by page. On `FirefliesError` with `too_many_requests`:
- Persist current cursor + position into `sync_runs` row with outcome `partial`.
- Schedule a retry at `now + retry_after` (parse from response if available, else default 1h).
- UI shows "Sync paused: rate-limited until HH:MM. M of N meetings synced so far."

The cache stays consistent: rows already upserted are valid. Reconciliation (mark-gone) only runs on **complete** sync (full pass), not partial.

## Phased migration

Splitting this so we don't ship one giant PR:

### Phase A — Cache infrastructure (no behavior change)
- Add `meeting_cache` and `sync_runs` tables; migration in `core/manifest.py`.
- Implement `infra/meeting_cache.SqliteMeetingCache` with read API matching `MeetingRepository.list_meetings` shape (returns `AsyncIterator[Meeting]`).
- Implement `application/sync_service.SyncService.run(repo, cache, clock)` — pure logic, takes both repos as deps.
- Tests: schema migration idempotent; sync with mocked repo populates cache; reconciliation marks gone rows.

### Phase B — Read-path flip (behavior change behind a flag)
- Add `[sync] enabled = false` config flag (default off so existing users are unaffected).
- When `enabled = true`, `web/deps.get_deps` builds `ScanService` with the cache repo instead of the API client.
- Manual `/sync/now` route + button.
- Tests: flag off → existing behavior; flag on → scans hit cache.

### Phase C — Background scheduler + empty-cache UX
- `infra/sync_scheduler.py` runs scheduled syncs.
- Empty-cache banner with "Refresh now" CTA.
- Last-synced-at indicator on the dashboard.

### Phase D — Default-on flip
- Default `[sync] enabled = true` in setup wizard.
- Migration: existing users keep their flag; new installs get sync by default.
- Update README + CHANGELOG.

## Open questions for you

1. **Cache table location** — same `manifest.db` (proposed) or a sibling `cache.db`? Same DB lets us join cache + state in one query but couples migrations.
2. **Sync interval default** — 12h? 6h? Daily at 03:00 local time? Daily during off-peak suits "nightly process" your message described.
3. ~~**`gone_from_source` UX** — surface as "deleted in Fireflies elsewhere" badge, or silently drop on next reconcile?~~ **Resolved 2026-05-02:** surface as a badge for transparency.
4. **Scope of cached fields** — the schema above caches enough for filtering and rendering. Do you want to also cache transcript text / summary HTML so the side-panel preview works offline? Adds size; removes another API trip.
5. **Wizard scan semantics** — once cache is the source, the existing `?scan` re-runs need to be retired. Are there any current flows that explicitly want to bust the cache and hit live? (Probably no; raise it for confirmation.)

## Estimated scope

- Phase A: ~400 LOC + tests.
- Phase B: ~150 LOC (mostly wiring).
- Phase C: ~200 LOC + UI.
- Phase D: cleanup + docs.
- Total: ~1k LOC across ~3 PRs over a couple of sessions.

Not committed until we agree on the open questions above.
