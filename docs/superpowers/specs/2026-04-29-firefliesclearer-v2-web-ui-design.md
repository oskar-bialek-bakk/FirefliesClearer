# FirefliesClearer v2 — Web UI Design Spec

**Date:** 2026-04-29
**Status:** Approved — ready for implementation planning
**Author:** Oskar Białek (with Claude as collaborator)
**Supersedes (in part):** [`2026-04-28-firefliesclearer-design.md`](2026-04-28-firefliesclearer-design.md) — v1 architecture spec; v2 adds a new presentation layer without modifying v1's core, ports, or infra.

---

## 1. Purpose & scope

v2 ships a **local single-user web UI** that gives a colleague the full FirefliesClearer feature set without ever touching a terminal beyond `firefliesclearer serve`. It is an additional presentation layer on top of v1's existing application/domain/infra; v1's CLI commands continue to work unchanged for power users and for cron.

### In scope (v2)

- A FastAPI + HTMX web app launched by a new CLI command `firefliesclearer serve`.
- Setup wizard that replaces the v1 `init` command (which is removed).
- Dashboard with manifest state counts, last-activity rows, and recent failures (with retry).
- Cleanup wizard — 4 steps: Filter → Review → Archive → Purge.
- Presets — saved filter combos (CRUD) replace v1's `[rules.auto]` config block.
- History — searchable, filterable view over the manifest's audit trail.
- Settings — edit API key, archive root, defaults; manage presets.
- Browser auto-launch on `serve`; heartbeat-driven graceful shutdown when the browser is closed (deferred while operations are in flight).
- Live progress for archive/purge via Server-Sent Events.

### Out of scope (v2 — explicitly)

- **Multi-user / authentication.** Bound to `127.0.0.1`, single-user assumption.
- **Native binary / installer.** Pip-only; binary deferred to v3.
- **Triggering recurring runs from the UI.** CLI cron remains the recurring-run path.
- **In-app OS scheduler integration.** Same — deferred to v3.
- **Charts / data visualizations on dashboard.** Counts and tables only.
- **CSV export.**
- **Editing transcripts or summaries.**
- **Anything that would require changes to `core/` or `infra/`.** All v1 contracts stay intact.

### Removed from v1 in v2

- `firefliesclearer init` CLI command — superseded by the web setup wizard. Auto-detected first-run flow when `serve` finds no config.

### Non-goals (carried over from v1)

- Re-uploading or restoring meetings.
- Real-time monitoring or webhooks.
- Editing/transforming archived content.

---

## 2. Architecture & module layout

**Guiding principle:** v2 adds a second presentation adapter alongside `cli/`. `core/`, `ports/`, and `infra/` stay untouched. The application logic currently inlined in CLI command files (filter→Rule conversion, scan/archive/purge wiring, status/history queries) gets extracted into a thin **application services layer** that both CLI and web consume — so the web UI doesn't reinvent any business logic and the CLI doesn't drift.

### 2.1 Refactor before adding (mechanical, no behaviour change)

CLI command files in v1 contain a mix of (a) Typer plumbing, (b) Rich output, and (c) actual orchestration. Items (c) get lifted into a new `application/` package; (a) and (b) stay in `cli/`. Mechanical refactor, fully covered by the existing 123-test suite.

```
firefliesclearer/
├── application/                ← NEW: shared by cli/ and web/
│   ├── __init__.py
│   ├── setup_service.py        # initial config write + API ping (was init_cmd)
│   ├── scan_service.py         # filters → Rule list → selection (was scan_cmd)
│   ├── archive_service.py      # archive a selection (was archive_cmd)
│   ├── purge_service.py        # purge a selection (was purge_cmd)
│   ├── audit_service.py        # status + history queries (was status_cmd, history_cmd)
│   └── preset_service.py       # NEW: CRUD for saved filter combos
```

These are plain async classes/functions taking the existing ports (`MeetingRepository`, `Manifest`, `Archiver`, etc.) as constructor args. No FastAPI dependency, no Typer dependency. Pure.

### 2.2 New `web/` package

```
firefliesclearer/web/
├── __init__.py
├── app.py                      # FastAPI app factory: create_app(config, services)
├── lifecycle.py                # heartbeat tracker + graceful shutdown
├── deps.py                     # FastAPI Depends() providers (config, services, csrf)
├── operations.py               # background-task registry for archive/purge runs
├── routes/
│   ├── __init__.py
│   ├── setup.py                # GET/POST first-run wizard
│   ├── dashboard.py            # GET /
│   ├── cleanup.py              # the 4-step wizard
│   ├── presets.py              # CRUD
│   ├── history.py              # paginated audit query
│   ├── settings.py             # config editor
│   ├── progress.py             # SSE endpoint per running operation
│   └── _heartbeat.py           # POST /_alive (keepalive ping)
├── templates/
│   ├── base.html               # sidebar shell
│   ├── _macros.html
│   ├── partials/               # HTMX fragments
│   ├── setup/
│   ├── cleanup/
│   ├── dashboard.html
│   ├── presets.html
│   ├── history.html
│   └── settings.html
└── static/
    ├── htmx.min.js
    ├── htmx-sse.js
    ├── styles.css              # Tailwind, pre-built at packaging time
    └── app.js                  # heartbeat ping, side-panel toggle, shift-select
```

### 2.3 New CLI command

```python
# firefliesclearer/cli/serve_cmd.py
@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 0,                    # 0 = OS picks free port; we print the URL
    open_browser: bool = True,
):
    """Launch the local web UI."""
```

`firefliesclearer init` is **deleted** (the web wizard replaces it). All other v1 CLI commands remain.

### 2.4 New dependencies (added to `pyproject.toml`)

| Package | Purpose | Notes |
|---|---|---|
| `fastapi>=0.115` | web framework | |
| `uvicorn[standard]>=0.30` | ASGI server | |
| `jinja2>=3.1` | server-side templates | |
| `python-multipart>=0.0.9` | form posts | required by FastAPI for `Form()` |
| `itsdangerous>=2.2` | signed CSRF cookies | |
| `sse-starlette>=2.1` | SSE helpers | |

HTMX itself is shipped as a vendored static file (`web/static/htmx.min.js`) — no Node, no CDN dependency at runtime.

### 2.5 What does NOT change

- `core/` — every file untouched.
- `infra/fireflies_client.py`, `infra/pdf_renderer.py`, `infra/fs.py`, `infra/config.py` — untouched.
- `ports/*` — untouched.
- Manifest schema — **no migrations needed for v2**. Presets live in user config TOML, not in the manifest DB.
- Existing v1 tests — continue to pass without modification.

### 2.6 Layered dependency rules

```
web/  →  application/  →  ports/   ←  infra/
cli/  →  application/  →  ports/   ←  infra/
                  ↓
                core/  (pure domain, no I/O)
```

Web never imports from `cli/`. CLI never imports from `web/`. Both go through `application/`. `core/` depends on nothing in this project except `ports/`.

### 2.7 Where the database lives

The SQLite manifest stays exactly where v1 put it: in `core/manifest.py` (the `Manifest` class) writing to `<archive_root>/manifest.db` on disk.

```
web/routes/dashboard.py     ──┐
web/routes/cleanup.py        ──┼──>  application/*_service.py  ──>  core/manifest.Manifest  ──>  sqlite3
web/routes/history.py        ──┤                                                                     │
web/routes/setup.py           ──┘                                                                     ▼
                                                                                       <archive_root>/manifest.db
```

Properties preserved from v1:

1. **Single writer per process.** One `Manifest` instance per `serve` process, shared by all concurrent HTTP requests.
2. **Cross-process safety.** Web + cron may run concurrently against the same DB; SQLite WAL mode handles it; pipeline transactions are per-meeting and idempotent on retry.

Persistent layout (unchanged from v1):

```
<archive_root>/
├── archive/        ← downloaded artefacts
├── selections/     ← v1 selection JSON files (still written by CLI scan; web wizard works in-memory)
├── logs/           ← daily JSON-lines logs
└── manifest.db     ← SQLite, WAL mode, accessed only via core/manifest.Manifest
```

Presets are the one new persistent thing in v2 and they do **not** go in the DB — they live under `[[presets]]` in user config TOML.

---

## 3. App shell & navigation

### 3.1 Layout regions

```
┌──────────────────────────────────────────────────────────────────────┐
│ Sidebar (200px)              │  Main content area                    │
│ ┌────────────────────────┐   │                                       │
│ │ FirefliesClearer       │   │  ┌─────────────────────────────────┐  │
│ │ <build version>        │   │  │ Page header                     │  │
│ ├────────────────────────┤   │  │ Title · breadcrumb · actions    │  │
│ │ ▦  Dashboard           │   │  └─────────────────────────────────┘  │
│ │ ✂  Cleanup             │   │                                       │
│ │ ★  Presets             │   │  Page body                            │
│ │ ⌚ History             │   │  (HTMX swap target: id="page")        │
│ │ ⚙  Settings            │   │                                       │
│ │                        │   │                                       │
│ │ ──── status ────       │   │                                       │
│ │ ● connected            │   │                                       │
│ │ archive: 1,204         │   │                                       │
│ │ failed: 1              │   │                                       │
│ │                        │   │                                       │
│ │ [Quit app]             │   │                                       │
│ └────────────────────────┘   │                                       │
└──────────────────────────────┴───────────────────────────────────────┘
```

### 3.2 Sidebar (top to bottom)

- **Brand header** — "FirefliesClearer" + version string.
- **Primary nav (5 items)** — Dashboard / Cleanup / Presets / History / Settings. Active item highlighted; uses `<a hx-get hx-target="#page" hx-push-url="true">` for SPA-feeling navigation without an SPA framework.
- **Status footer** — API connection indicator (green/red dot — based on last successful Fireflies call), counts (archived total + failed if > 0). Refreshed every ~30s via HTMX polling on a dedicated fragment endpoint (`GET /sidebar/status`).
- **Quit-app button** — explicit way to shut down the server (POST `/_quit`); useful when the user wants to stop without closing the tab.

### 3.3 Page conventions

Every page renders into a single `#page` div:

```html
<header class="page-header">
  <h1>Page title</h1>
  <div class="page-actions"><!-- e.g. "+ New preset" --></div>
</header>

<section class="page-body">
  <!-- main content -->
</section>
```

### 3.4 HTMX patterns used everywhere

- **Page navigation:** `hx-get` + `hx-target="#page"` + `hx-push-url="true"`.
- **Form submit returns a fragment:** filters/forms POST and return only the swapped section.
- **Polling:** sidebar status footer (`hx-trigger="every 30s"`).
- **SSE:** archive/purge progress (`hx-ext="sse" sse-connect="/api/operations/{id}/events"`).
- **Confirm dialogs:** `hx-confirm="…"` on destructive buttons.

### 3.5 Visual style

- **Tailwind CSS** vendored as a pre-built static file (~30 KB compiled CSS). Tailwind CLI compiles `styles.css` once at packaging time; no Node at runtime or install time.
- Slate gray sidebar (`#0f172a`), white content area, blue accent for active/primary actions, red for destructive, amber for warnings.
- System font stack (`-apple-system, Segoe UI, sans-serif`). No web fonts.
- Vendored Lucide icon set (~50 KB, only those used).

### 3.6 Browser support

Chrome / Edge / Firefox / Safari current versions. No legacy IE. Targeted at laptop/desktop. Layout collapses sidebar to icons-only under 1000px wide; not optimised for phones.

### 3.7 Flash messages, errors, empty states

- **Flash messages** — toast component (top-right), `hx-trigger="load"` on a `<div>` returned in response to a successful mutation. Auto-dismiss after 4s.
- **Error pages** — FastAPI exception handler returns either a full error page (for normal navigation) or an HTMX-targetable error fragment (for in-place errors). 404/500 styled consistently.
- **Empty states** — every list view (history, presets) has a designed empty state with a primary action.

---

## 4. First-run wizard (Setup)

Triggered automatically when `firefliesclearer serve` starts and no valid user config is found. Three short steps; the user can never end up with a half-written config (write happens atomically at the end).

### 4.1 Trigger logic

On `serve` startup:

```
load_config() →
  ├─ valid config + API key present  → app starts normally on Dashboard
  ├─ no config file at all           → redirect every request to /setup/welcome
  └─ config file but missing/empty API key → redirect to /setup/api-key
                                              (skip earlier steps; preserve archive_root if set)
```

The wizard is the only thing reachable while config is incomplete. Sidebar nav is hidden during setup.

### 4.2 Steps

**Step 1 — Welcome.** Single screen explaining the three things needed. Single "Let's go →" button.

**Step 2 — API key.**
- Password input (`type="password"`, with show/hide toggle).
- Helper text linking to Fireflies' API key page (exact URL confirmed during implementation).
- On submit: `POST /setup/api-key` →
  - Calls `setup_service.verify_api_key(key)` which runs the v1 `getUser` ping.
  - **Pass:** stores key in session (not yet on disk), shows "Connected as `<email>`", advances.
  - **Fail (401/403):** inline "Fireflies rejected this key. Double-check and try again."
  - **Fail (network):** "Can't reach Fireflies — check your internet connection. Retry / Skip verification."

**Step 3 — Archive folder.**
- Pre-filled with `<user-documents>/firefliesclearer-archive` (resolved via `platformdirs`).
- Plain text input with a help link explaining how to copy a path from File Explorer / Finder (browsers can't render arbitrary folder pickers from a text input).
- Validation on submit: exists+writable → accept; doesn't exist → "Create this folder?" inline confirm; not writable / is a file → error.

**Step 4 — Defaults & finish.**
- Default age threshold for cleanup — number input, default `90` days.
- Concurrency — slider 1–10, default `3`. Tooltip: "How many meetings to download in parallel."
- Submit: build full config (Pydantic-validated) → atomic write (`config.toml.tmp` → fsync → rename) → seed empty preset list → redirect to `/`.

### 4.3 Edge cases

- **Browser closes mid-wizard** — heartbeat stops, server shuts down (no in-flight op). Next `serve` re-enters wizard at the appropriate step.
- **Refresh mid-wizard** — server stores progress in a signed cookie (step + already-validated values).
- **Redo setup later** — Settings → "Reset configuration" clears `config.toml`, redirects to `/setup/welcome`. Archive folder and `manifest.db` are NOT touched.
- **Stale `init` invocation** — prints `firefliesclearer init has been replaced by the web setup wizard. Run 'firefliesclearer serve' instead.` and exits 0.

### 4.4 Visual style

Wizard pages center a card on the screen (max-width ~520px), no sidebar visible, minimal chrome — signals "you're configuring the app, not using it yet."

---

## 5. Cleanup wizard

Four steps with a sticky stepper at the top. **Back** always available except step 1, **Continue** disabled until the step's exit criteria are met. Wizard state lives in a server-side session keyed by an opaque cookie — no hidden form fields needed across steps; refresh-safe and browser-back-safe.

```
[1. Filter] → [2. Review] → [3. Archive] → [4. Purge]
```

### 5.1 Step 1 — Filter

**Goal:** build a `RuleSet` (the same object the v1 `scan` command builds) and count matching meetings.

- **Preset dropdown** at top: `Load preset ▾`. Loading a preset replaces all filter values; the form stays editable afterwards. Default preset (if any) is loaded on page entry.
- **Filter form** — one row per filter type, mirroring v1's full rule set:
  - Older than `[ N ]` days `[☐ enabled]`
  - Duration below `[ N ]` minutes `[☐ enabled]`
  - `[☐] No transcript only`
  - Title contains `[ comma-separated ]` (substring match)
  - Title regex `[ pattern ]` *(advanced — collapsible)*
  - Host email `[ comma-separated emails ]`
  - Has tag `[ comma-separated tags ]`
  - Participants below `[ N ]`
- Live "**N meetings would match**" counter, updates ~500ms after the user stops typing (HTMX `hx-trigger="input changed delay:500ms"`). Hits `POST /cleanup/preview-count`; runs the matching pass against the API's metadata-only listing.
- **Continue →** enabled when matched count > 0.

**Edge cases:**
- Zero matches: "No meetings match. Adjust filters or load a different preset."
- API unreachable: counter fails inline; user can still hit Continue and the next step retries the fetch.

### 5.2 Step 2 — Review

**Goal:** show the full matched list, let the user deselect specific rows, finalize the selection.

- **Toolbar:** "**N of M selected**" counter; Select all / Deselect all / Invert; client-side search (title, host, tag); sort dropdown (Date / Duration / Title).
- **Table** — server-paginated at 100 rows per page; columns: checkbox (single-click toggle, **shift-click selects range**), Title (clickable → side panel), Date (relative + absolute on hover), Duration (min), Host email, Participants count, Matched rules (chips), 🔗 Open in Fireflies.
- **Side panel** (slides in from right when a row is clicked): full title, date, host, participants list, duration, tags, summary preview (~500 chars), source URL. Close button + Esc dismisses. Does NOT pause the table — user can keep toggling rows while panel is open.
- **Continue →** enabled if ≥ 1 row selected.

**Edge cases:**
- > 100 matches with "Select all": only current page selects by default; banner "100 of 247 selected on this page. **Select all 247?**" (Gmail/Linear pattern).
- Going Back to Step 1 and changing filters: confirm dialog "Changing filters will clear your current selection of N meetings. Continue?".
- Refresh: server-side session restores everything; meeting metadata re-fetched if cache age > 5 min.

### 5.3 Step 3 — Archive

**Goal:** download artefacts, write to canonical paths, mark meetings `archived` in the manifest.

**Pre-flight panel:**
- "About to archive **N meetings** (~ X MB estimated)."
- Estimated size = sum of (duration × audio bitrate); rough but useful.
- **Start archive** button (no confirmation modal — the next step has the destructive action).

**In-progress view:**
- Top: progress bar (`completed / total`) + elapsed + estimated remaining.
- Per-meeting list rows show meeting title + sub-state (`queued / fetching / downloading audio / rendering pdf / verifying / archived ✓ / failed ✗`).
- SSE stream from `/api/operations/{op_id}/events` updates rows in place via HTMX.
- **Cancel** button: requests graceful stop after current meeting completes (no half-done meetings).

**Done view:**
- Summary: "**X archived, Y failed.**" Failed rows expanded with the error message.
- **Retry failed** re-archives just the failures.
- **Continue to Purge →** disabled if no successes.
- **View archive folder** opens the file manager (Windows: `explorer.exe`, macOS: `open`, Linux: `xdg-open`).

**Edge cases:**
- All meetings fail: stop the wizard, show error summary, link to History for context.
- Browser closes mid-archive: heartbeat-shutdown is deferred until completion (per § 9.2). User reopens → wizard resumes on Step 3 with the completed state.
- Cancel: completed meetings keep `archived` state; rest stay untouched in Fireflies; user can continue to Purge with just the successes.

### 5.4 Step 4 — Purge

**Goal:** verify each meeting's archive is complete, then call Fireflies' delete mutation.

**Pre-flight panel — destructive:**
- "About to **permanently delete N meetings** from Fireflies."
- Numbered list of titles (collapsible if > 10).
- Yellow notice: "This cannot be undone. Archived files on disk are kept; only Fireflies' copies are removed."
- **Confirmation:** the user must type the count `N` into a text input before the button enables.
- **Purge N meetings** — red, disabled until confirmation typed.

**In-progress view:** identical pattern to Archive — progress bar, per-meeting state (`verifying / deleting / deleted ✓ / failed ✗`), SSE streaming.

**Done view:**
- "**N deleted from Fireflies.**" (or "X deleted, Y failed" with detail).
- **Done** → returns to Dashboard.
- **Cleanup another batch** → restarts wizard at Step 1 (filters preserved as a starting point).

**Safety guardrails (re-applied here, matching v1):**

1. Each meeting is re-verified (`verified_at` set; all required files exist on disk; sizes non-zero) **immediately before** the delete mutation. If verification fails, that meeting is skipped, kept in Fireflies, marked `failed_verify`.
2. On Fireflies API error during delete: meeting stays `archived`, recorded as `deleted_failed`. Re-runnable via the Dashboard's failures list.

### 5.5 What state lives where

| State | Where |
|---|---|
| Current step (1/2/3/4) | Server-side session, signed cookie |
| Filter form values | Server-side session |
| Selected meeting IDs | Server-side session |
| Operation progress | In-memory `OperationRegistry` (per-process) |
| Manifest writes (archived/deleted/failed) | SQLite via `core/manifest.Manifest` |
| Heartbeat state | In-memory `HeartbeatTracker` |

Wizard state is intentionally not persisted to disk — if `serve` is killed, the wizard is gone, but the manifest knows what was done. The user resumes by starting the wizard from Step 1 (and can use "state = pending" filters from the dashboard's failures list to find anything that didn't complete).

---

## 6. Dashboard

Home page when the app is correctly configured. Read-only, refreshes automatically, never mutates anything itself.

### 6.1 Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ Dashboard                                  [✂ Start cleanup ▸]  │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ Archived │  │ Pending  │  │ Failed   │  │ Deleted  │         │
│  │  1,204   │  │    3     │  │    1     │  │  1,189   │         │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
│                                                                 │
│  Last activity                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 2026-04-29 09:14 · Archived 14 · Deleted 14 · 0 failed   │   │
│  │ 2026-04-22 10:01 · Archived 8  · Deleted 8  · 0 failed   │   │
│  │ 2026-04-15 09:55 · Archived 12 · Deleted 11 · 1 failed   │   │
│  │                                          [View history →]│   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  Needs attention                                                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ ⚠ Test Standup (2025-12-01) — failed_download             │   │
│  │   "Connection reset by peer" · [Retry] [Open in Fireflies]│   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 Components

- **Top bar action `[✂ Start cleanup ▸]`** — primary CTA, jumps to Step 1 of the Cleanup wizard.
- **State count cards** — counts from the manifest grouped by state. Click navigates to the relevant filtered view (History or Needs attention).
- **Last activity** — last ~5 "runs" derived from the `state_log` table. A "run" is a contiguous window of state transitions within ~1 hour grouped by date+hour. Each row links to History with a date filter pre-applied.
- **Needs attention** — meetings in any `*_failed` state with title, date, error code + `last_error` text, **Retry** button (re-runs `Pipeline.process_one` for that meeting; SSE-streamed inline within the dashboard row), **Open in Fireflies** button. Empty state: "All clear ✓ — nothing needs your attention."

### 6.3 Refresh behaviour

- Dashboard polls every 30s via HTMX (`hx-trigger="every 30s"` on the four-card fragment + the "Needs attention" fragment).
- During an active retry (SSE in flight on a row), polling for that fragment pauses to avoid stomping the in-place updates.

### 6.4 Performance

- All counts come from indexed columns (`idx_meetings_state` already exists in v1's schema).
- "Last activity" query is bounded (last 5 buckets), runs in single-digit ms even on a 10K-meeting manifest.
- "Needs attention" list capped at 50 displayed; "Show all" link to History if more.

### 6.5 Out of scope on Dashboard for v2

Charts, CSV export, meeting search/preview from the dashboard, manual "trigger auto run" button (there is no auto path inside the UI). All cut per Q9 in brainstorm.

---

## 7. Presets

A preset is a named, reusable filter configuration. Replaces v1's `[rules.auto]` config block (a single hard-coded rule set used only by `run --apply`).

### 7.1 Data model

Stored in user config TOML, **not** in the manifest DB:

```toml
[[presets]]
name = "Auto cleanup"
description = "Old meetings (>180d) and anything without a transcript"
default = true                       # at most one preset has default = true
created_at = "2026-04-29T10:14:00+02:00"

[presets.filters]
older_than_days = 180
no_transcript = true

[[presets]]
name = "Drafts and tests"
description = "Short meetings whose title contains test/draft/temp"
default = false
created_at = "2026-04-29T10:18:00+02:00"

[presets.filters]
duration_below_minutes = 5
title_contains = ["test", "draft", "temp"]
```

**Schema rules:**
- `name` — required, unique (case-insensitive), 1–60 chars.
- `description` — optional, 0–200 chars.
- `default` — at most one preset has `default = true`. Loaded automatically when the Cleanup wizard opens.
- `filters` — same Pydantic model as v1 `RuleSet`.
- `created_at` — set by the server, never user-editable.

**Migration from v1:** if a user upgrades and has `[rules.auto]` populated, the setup-detection logic on first `serve` startup auto-creates a single preset called "Auto cleanup" from those rules and marks it default. The legacy section is removed; a backup `config.toml.v1.bak` is written next to the new file before rewriting. One-shot migration.

### 7.2 `PresetService` (in `application/preset_service.py`)

```python
class PresetService:
    def list(self) -> list[Preset]: ...
    def get(self, name: str) -> Preset: ...                       # raises PresetNotFound
    def create(self, name: str, description: str, filters: RuleSet, *, default: bool = False) -> Preset: ...
    def update(self, name: str, *, description: str | None = None, filters: RuleSet | None = None, default: bool | None = None) -> Preset: ...
    def delete(self, name: str) -> None: ...
    def get_default(self) -> Preset | None: ...
```

All methods read/write through the same atomic-write helper as v1's config. Used by both web (Settings → Presets) and the Cleanup wizard.

### 7.3 UI

The Presets nav item opens a list page:

```
┌────────────────────────────────────────────────────────────┐
│ Presets                                  [+ New preset]    │
├────────────────────────────────────────────────────────────┤
│  ★ Auto cleanup            (default)        [Edit] [Delete]│
│    Old meetings (>180d) and anything without a transcript  │
│    older_than_days=180 · no_transcript                     │
├────────────────────────────────────────────────────────────┤
│    Drafts and tests                          [Edit] [Delete]│
│    Short meetings whose title contains test/draft/temp     │
│    duration_below=5 · title_contains=test,draft,temp       │
└────────────────────────────────────────────────────────────┘
```

- Star marker on the default.
- **Run cleanup with this preset** action — shortcut to start the wizard at Step 1 with this preset pre-loaded.
- Edit opens a modal with the same form as the wizard's Filter step plus name/description/default.
- Delete asks confirm; deleting the default preset just unsets default (no auto-promotion).
- Empty state: "Create your first preset" CTA. (Shouldn't occur after migration — every upgraded user has at least the auto-cleanup preset.)

### 7.4 Save-from-wizard flow

In the Cleanup wizard's Filter step, after a useful filter combination is built:
- Top-right of the filter form: `[☆ Save as preset…]`
- Inline form (HTMX swap): Name (required), description (optional), `[☐] Set as default`.
- Pre-validation: name collision → "Overwrite existing?" / "Use a different name".
- "Save and continue" does both at once.

### 7.5 Out of scope for v2

- Schedule attached to a preset (no cron expression in UI). Power users continue to wire `firefliesclearer run --apply --yes --preset "Auto cleanup"` into their OS scheduler. **This means `run` gains a `--preset` flag in v2**, replacing v1's hard-coded `[rules.auto]` reading.
- Sharing presets between users (local file only).
- Folders / categories (flat list).
- Run history per preset (could be derived from manifest log; not worth building until requested).

---

## 8. History & Settings

### 8.1 History

**Purpose:** answer "what happened to my Fireflies meetings?" — browseable, searchable view over the manifest's `meetings` table joined with `state_log`.

**URL:** `/history` with query-string filters so links are shareable.

**Layout:**

```
┌──────────────────────────────────────────────────────────────────┐
│ History                                                          │
├──────────────────────────────────────────────────────────────────┤
│ Date range [last 30 days ▾]  State [all ▾]  Search [_________]   │
├──────────────────────────────────────────────────────────────────┤
│ Date         Title                          State        Actions │
│ 2026-04-29   Test Standup                   ✓ deleted    🔗      │
│ 2026-04-29   Draft sync — Q4                ✓ deleted    🔗      │
│ 2026-04-22   Marketing kickoff Q2           ✓ archived   🔗 📁   │
│ 2026-04-15   Old planning call              ✗ failed     🔗 ↻    │
│              [← Prev]   Page 1 of 17   [Next →]                  │
└──────────────────────────────────────────────────────────────────┘
```

**Filters:**
- **Date range** — preset dropdown: today / last 7d / last 30d / last 90d / this month / last month / all time / custom.
- **State** — multi-select: archived / deleted / pending / failed (groups all `*_failed` states; side panel shows the specific subtype).
- **Search** — substring match against meeting title (case-insensitive). Server-side.

Filters AND together. URL updates on change so `/history?range=last-30d&state=failed` is shareable.

**Table:**
- Server-paginated, 50 per page.
- Default sort: meeting_date DESC. Toggleable per column.
- Row click → side panel with full metadata + state log timeline (every transition with timestamp + details JSON pretty-printed).
- Action icons: 🔗 Open in Fireflies (only if state ≠ deleted); 📁 Open archive folder (for `archived` and `deleted`); ↻ Retry (for `*_failed`).

**Empty states:** filter-no-match → "No meetings match these filters." with clear-filters link; manifest empty → "Nothing here yet — try the Cleanup wizard."

**Performance:** queries hit existing v1 indexes. Title search via `LIKE '%term%'` — fine for typical 10K-row manifests; FTS5 deferred until needed.

### 8.2 Settings

**URL:** `/settings`. Single page with collapsible sections.

#### Connection
- API key — masked input, never re-displayed in plaintext. "Test connection" calls the `getUser` ping.
- "Replace key" — inline form (paste new → test → save). Old key never appears.

#### Archive
- Archive root — edit with the same "exists / writable / create?" validation as setup.
- If user changes the root and content exists at the old path: banner "Existing archive at `<old>` is NOT moved automatically. Move it manually before continuing if you want to preserve history." We don't auto-move (cross-drive moves can take hours).

#### Defaults
- Concurrency — slider 1–10.
- Default age threshold — number input (suggested value when creating a new preset).
- Delete confirmation threshold — count above which Purge requires the typed-count confirmation. Default 10.

#### Presets
- Link to the Presets page (`★ Manage presets →`). No editing here — Presets has its own page.

#### Logs & data
- "View today's log" — opens `<archive_root>/logs/YYYY-MM-DD.log` in a read-only modal viewer (JSON-lines syntax).
- "Open archive folder" — file explorer at `<archive_root>`.
- Retention — log files older than N days auto-deleted on each `serve` startup (default 30, configurable). Manifest entries are never auto-deleted.

#### Danger zone
- "Reset configuration" — confirms, deletes `config.toml`, redirects to setup wizard. Archive folder + manifest left intact.
- "Delete entire archive" — *not in v2*; user can `rm -rf` themselves.

**Save behaviour:** each section has its own Save button (submits only that section); flash message on success; validation errors inline; atomic write (`.tmp` → fsync → rename) on every save.

**Out of scope for v2:** backup/restore configuration (manual TOML copy), themes / dark mode, keyboard-shortcut customisation (we ship a fixed small set: `/` focus search, `Esc` close side panel, `?` show help), multi-account.

---

## 9. Server lifecycle & security

### 9.1 Startup (`firefliesclearer serve`)

```
firefliesclearer serve [--host 127.0.0.1] [--port 0] [--no-open]
```

**Sequence:**
1. Load config (or detect missing → flag for setup-wizard mode).
2. Initialise services: `Manifest`, `FirefliesClient`, `Archiver`, `OperationRegistry`, `HeartbeatTracker`, `PresetService`.
3. Build FastAPI app via `create_app(config, services)`.
4. Bind to `host:port`. If `port=0` (default), the OS picks a free port; we read it back via `socket.getsockname()`.
5. Print the URL once, **including the session token** so `--no-open` users (and copy-pasted URLs in general) work without an out-of-band auth handoff: `→ FirefliesClearer running at http://127.0.0.1:54231/?token=<session-token>`.
6. Unless `--no-open`: `webbrowser.open(<that same URL>)`.
7. Start uvicorn event loop. Block until shutdown.

**Defaults are deliberate:** `host=127.0.0.1` (loopback only), `port=0` (no conflicts), browser auto-opens.

**Single-instance lockfile:** before binding, attempt to acquire `<archive_root>/.serve.lock` (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows). If held → exit with: "Another instance is running. Open `http://127.0.0.1:<port>` (URL read from the lockfile contents) or stop it first."

### 9.2 Heartbeat & graceful shutdown

The "browser closes → app closes" mechanism.

**Components:**
- **Client** — every page in `base.html` runs a tiny script that POSTs `/_alive` every 10 seconds via `navigator.sendBeacon`.
- **`HeartbeatTracker`** (in `web/lifecycle.py`):
  - Records `last_seen: datetime` on every `/_alive` POST.
  - Background asyncio task wakes every 5 seconds and checks `now() - last_seen`.
  - If `> 60s` AND `OperationRegistry.has_active() == False` → initiate shutdown.
  - If `> 60s` AND active operation exists → defer; log "Browser gone, but operation in progress; will exit after completion." Re-checks every 5 seconds.

**Why 60s grace?** Tab refreshes drop the heartbeat for ~1s; user navigation drops it briefly; brief network blips. 60s comfortably covers all of these without making "I really did close the tab" feel laggy.

**Multi-tab:** any open tab keeps the heartbeat going. Closing one of two tabs leaves the other pinging — server stays alive. Closing the last tab → 60s grace → shutdown.

**Other shutdown triggers:**
- **Ctrl-C in terminal** — uvicorn handles SIGINT; lifespan hook cancels new operations, waits up to 30s for active ones to finish per-meeting transactions, then exits. Manifest is consistent at any per-meeting boundary.
- **Quit-app button in sidebar** — POST `/_quit` sets a `shutdown_requested` flag. The heartbeat task picks it up on its next 5-second tick and initiates shutdown immediately when no operation is active. The in-flight-op deferral still applies: clicking Quit while an archive runs schedules shutdown for after the operation completes (the UI surfaces this as "Will exit after current operation").

**Graceful shutdown sequence:**
1. Refuse new HTTP requests (uvicorn standard behaviour).
2. Wait for in-flight requests to finish.
3. Stop the heartbeat task.
4. Close the `Manifest` (commits any open WAL, releases the file).
5. Release the lockfile.
6. Close the `FirefliesClient` httpx pool.
7. Exit 0.

### 9.3 Background operation safety

The `OperationRegistry` (in `web/operations.py`):
- Holds `op_id → Operation(state, progress, asyncio.Task, cancel_event)`.
- `register(op_id, task, total_steps)`, `get(op_id)`, `cancel(op_id)` (sets cancel_event; the operation checks it **between meetings, never during**), `has_active()`.
- Completed operations kept in memory for 30 minutes for "Done page" navigation; then GC'd.

Shutdown logic consults `has_active()` to decide whether to wait.

### 9.4 Security

Threat model: **other software running on the same machine should not be able to drive the FirefliesClearer API just because they can hit `127.0.0.1`** (malicious npm postinstall, browser extensions scanning local ports, CSRF from public websites making fetch requests to localhost).

**Defences:**

1. **Bind loopback-only.** `host=127.0.0.1`, never `0.0.0.0`. Non-loopback requires an explicit `--i-know-what-im-doing` flag.

2. **Session token in URL on browser auto-open.** On `serve` start, generate a random 32-char token. Browser auto-open URL includes it as `?token=<...>`. Server stores the token and sets a session cookie on first hit. Subsequent requests check the cookie (or the query param if no cookie yet — covers bookmarks). Other apps on localhost can't guess the token. Same model `jupyter notebook` uses.

3. **CSRF token on mutating requests.** Every form (POST/PUT/DELETE) carries a CSRF token rendered into the template. Implementation: signed `itsdangerous` cookie + matching hidden field (double-submit). FastAPI dependency `csrf_protected` runs on every mutating route. GET/HEAD exempt.

4. **CORS strict.** `Access-Control-Allow-Origin: http://127.0.0.1:<port>`; reject everything else.

5. **No file uploads in v2.**

6. **API key never leaves the server.** Settings → Connection masks input; key stored in user config TOML on disk; only ever sent outbound to Fireflies in `Authorization` headers; never echoed in any HTTP response (including masked — the field shows `********` or "Set" / "Not set").

7. **Logging redaction (already in v1).** Custom logging filter strips `Authorization: Bearer …` patterns; web logs run through the same filter.

8. **No subprocess execution from user input.** "Open archive folder" is the only shell-out (`explorer.exe` / `open` / `xdg-open`); always passes a constant config-derived path.

9. **Path traversal hardening.** Endpoints accepting a meeting ID re-resolve via `Manifest`; endpoints accepting a folder name validate via `Path.resolve()` and reject paths containing `..` after resolution.

### 9.5 Out of scope security-wise

- Authentication / multi-user.
- Encryption of config TOML at rest.
- Rate limiting on the local API.
- Audit logging of UI actions beyond what the manifest's `state_log` already captures.

---

## 10. Long-running operations & SSE

### 10.1 Shape of an operation

```python
# web/operations.py

@dataclass
class Operation:
    id: str                              # e.g. "op_2026-04-29T10-14-23_a8f2"
    kind: Literal["archive", "purge", "retry-archive", "retry-purge"]
    total: int
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    meetings: list[MeetingSlot]
    started_at: datetime
    finished_at: datetime | None
    cancel_event: asyncio.Event
    task: asyncio.Task
    event_queue: asyncio.Queue[Event]

@dataclass
class MeetingSlot:
    meeting_id: str
    title: str
    sub_state: Literal[
        "queued", "fetching", "downloading_audio",
        "rendering_pdf", "verifying", "deleting",
        "done", "failed",
    ]
    error: str | None

@dataclass
class Event:
    op_id: str
    seq: int                             # monotonic per operation
    kind: Literal["meeting_state", "operation_state", "summary"]
    data: dict[str, Any]
    at: datetime
```

`OperationRegistry` (also in `web/operations.py`):
```python
class OperationRegistry:
    def start(self, kind, meeting_ids, services) -> Operation: ...
    def get(self, op_id) -> Operation: ...           # raises OperationNotFound
    def cancel(self, op_id) -> None: ...
    def has_active(self) -> bool: ...
    def gc(self) -> None: ...                        # drop completed > 30min
```

### 10.2 Concurrency

- **Across operations:** at most ONE concurrent operation per kind (no two simultaneous archives). A second start request returns `409 Conflict` with the existing op_id; UI shows banner "An archive is already running, switch to it →." Avoids two processes both racing the manifest from the same `serve` process.
- **Within an operation:** concurrent per-meeting workers, capacity from `config.run.concurrency` (default 3). Coordinator uses an `asyncio.Semaphore`.
- Each per-meeting worker runs the v1 `Pipeline.process_one(meeting_id)` flow unchanged — that's where all safety guarantees live.

### 10.3 SSE endpoint

```
GET /api/operations/{op_id}/events       (Content-Type: text/event-stream)
```

Implementation via `sse-starlette`:

```python
@router.get("/api/operations/{op_id}/events")
async def stream(op_id: str, registry: OperationRegistry = Depends()):
    op = registry.get(op_id)

    async def gen():
        # Replay events that already happened (so a late subscriber catches up)
        for evt in op.replay_buffer():
            yield {"event": evt.kind, "id": str(evt.seq), "data": json.dumps(evt.data)}

        # Stream live events
        async for evt in op.subscribe():
            yield {"event": evt.kind, "id": str(evt.seq), "data": json.dumps(evt.data)}
            if evt.kind == "operation_state" and evt.data["state"] in {"succeeded", "failed", "cancelled"}:
                return

    return EventSourceResponse(gen(), ping=15)
```

**Properties:**
- **Replay buffer.** Operation keeps full event list in memory. Late subscriber (or refresh) sees full history before live events. Buffer dropped 30 min after operation completion.
- **Last-Event-ID support.** Reconnecting clients with `Last-Event-ID` header get only events after that seq. HTMX's SSE extension handles this automatically.
- **Auto-close on terminal state.** Generator exits; browser sees `EventSource.readyState == CLOSED` and stops trying to reconnect.

### 10.4 Browser side (HTMX SSE extension)

```html
<div hx-ext="sse" sse-connect="/api/operations/{{op.id}}/events">
  <div hx-trigger="sse:operation_state" hx-target="#progress-summary" hx-swap="innerHTML"
       hx-get="/cleanup/archive/{{op.id}}/summary"></div>

  <ul id="meeting-list">
    {% for slot in op.meetings %}
      <li id="m-{{slot.meeting_id}}"
          hx-trigger="sse:meeting_state-{{slot.meeting_id}}"
          hx-target="this" hx-swap="outerHTML"
          hx-get="/cleanup/archive/{{op.id}}/meeting/{{slot.meeting_id}}">
        {{ render_slot(slot) }}
      </li>
    {% endfor %}
  </ul>
</div>
```

Each meeting row listens for an SSE event named `meeting_state-<id>` and refreshes itself via HTMX `GET`. Top summary listens for `operation_state` events and refreshes the count/progress bar. Cancellation is `<button hx-post="/api/operations/{{op.id}}/cancel">` → operation finishes current meeting and stops; UI sees final `operation_state=cancelled`.

### 10.5 Why SSE and not WebSockets

| | SSE | WebSockets |
|---|---|---|
| Direction | server→client only ✓ (we never need client→server over the same channel) | bidirectional |
| Reconnection | automatic, with Last-Event-ID | manual |
| HTTP semantics | yes (proxies, dev tools, curl all work) | no |
| HTMX support | first-class extension | requires custom JS |
| Server complexity | one async generator | connection manager |

Our use case is strictly server→client; WebSockets would be over-engineering.

### 10.6 Retry-from-Dashboard flow

The Dashboard's "Needs attention" list and History's per-row Retry button both go through the same operation infrastructure:
- POST `/operations/retry-archive` with `{meeting_ids: [...]}` → creates operation with `kind="retry-archive"`, returns `op_id`.
- Dashboard row swaps to an inline progress bar (HTMX `hx-target="closest .row"`) subscribing to that operation's SSE stream filtered to one meeting.
- On terminal state, the row re-renders: ✓ or new error message + retry button.

Exactly one path through the system for "do work on a meeting" — `Pipeline.process_one` — whether invoked by wizard, retry, CLI, or cron.

### 10.7 Failure modes

| Failure | Handling |
|---|---|
| Server crashes mid-operation | OperationRegistry is in-memory — gone. Affected meetings stay in their last manifest state. Re-running the wizard or hitting Retry resumes via the manifest's idempotent state machine. |
| Browser closes mid-operation | Heartbeat-shutdown is deferred (per § 9.2). Operation continues; server exits cleanly when it's done. User reopens later → operation gone from registry but outcome visible in History/Dashboard. |
| User clicks Cancel | `cancel_event` set; current per-meeting work finishes (never abort mid-transaction); loop exits; final `operation_state=cancelled` event fires. |
| SSE connection drops | HTMX reconnects automatically; replay buffer fills the gap. |
| Operation taking forever | No timeout — large archives can legitimately take > 1 hour. Per-HTTP-call timeouts in v1's Fireflies client still apply to individual API calls. |
| Two browser tabs viewing the same operation | Both subscribe to the same SSE stream independently; both update simultaneously. |

---

## 11. Testing strategy

v1 set the bar: 123 tests, 91% overall coverage, 100% on `core/pipeline.py` and `core/manifest.py`. v2 adds web-layer tests without lowering any of those numbers.

### 11.1 What stays unchanged

- All 123 existing v1 tests continue to pass without modification.
- The fakes (`InMemoryMeetingRepository`, `FakeSummaryRenderer`, `FrozenClock`) remain the substrate for domain and application tests.
- pytest + pytest-asyncio + respx + pytest-cov stay as the toolchain.

### 11.2 What's added

```
tests/
├── core/                    ← unchanged
├── infra/                   ← unchanged
├── application/             ← NEW (small, mostly extracted from cli/ tests)
│   ├── test_setup_service.py
│   ├── test_scan_service.py
│   ├── test_archive_service.py
│   ├── test_purge_service.py
│   ├── test_audit_service.py
│   └── test_preset_service.py
├── web/                     ← NEW (the bulk of v2's new tests)
│   ├── conftest.py          # FastAPI test client fixtures, fakes, signed cookies
│   ├── test_lifecycle.py
│   ├── test_operations.py
│   ├── test_csrf.py
│   ├── test_session.py
│   ├── routes/
│   │   ├── test_setup.py
│   │   ├── test_dashboard.py
│   │   ├── test_cleanup.py
│   │   ├── test_presets.py
│   │   ├── test_history.py
│   │   ├── test_settings.py
│   │   ├── test_progress_sse.py
│   │   └── test_heartbeat.py
│   └── e2e/
│       └── test_full_run.py
└── cli/                     ← updated to test the post-extraction CLI shells
```

### 11.3 Test types in the web layer

**Route-level tests:** FastAPI's `TestClient` against an app built with `create_app(config=..., services={...fakes...})`. No real Fireflies calls (`InMemoryMeetingRepository` fake), no real disk writes (tmpdir + fresh SQLite per test). Assertions:
- HTTP status + content type.
- HTMX endpoints: `text/html` fragment containing expected elements (use `selectolax` for cheap CSS-selector-based assertions; avoids regex-on-HTML brittleness).
- Cookies set/expected (session, CSRF).
- Form posts: redirect chain or HTMX `HX-Redirect` header.

**SSE tests:** subscribe via `TestClient.stream("GET", ...)`, drain N events, assert ordering and final-state. Verify replay buffer by connecting a second client mid-operation.

**Operation lifecycle tests:** start a fake operation (dummy coroutine yielding events on a controllable schedule), assert that:
- Concurrent same-kind operations are rejected with 409.
- Cancellation completes after the current per-meeting boundary.
- Completed operations GC'd after 30 minutes (controllable clock).

**Heartbeat tests:** `FrozenClock` to advance time, assert that:
- After 60s of no `/_alive`, shutdown is requested when no operation is active.
- During an active operation, shutdown is deferred.
- Multi-tab simulation (two heartbeat sources, only one stops) keeps the server alive.

**CSRF tests:** POST without token → 403; mismatched token → 403; matching cookie + form field → 200.

### 11.4 What we deliberately do NOT test

- **Browser-rendered visual layout.** No Playwright/Selenium in v2's CI. High flake cost; rendered HTML is well-tested at the fragment level. Visual regressions caught by manual review during PR.
- **HTMX itself.** Vendored library; test the responses it consumes and the requests it sends, not the swap mechanics.
- **Real Fireflies API behaviour at the web layer.** v1's contract test already covers infra↔Fireflies; web tests use the in-memory fake.

### 11.5 Critical-path test cases (MUST exist)

**Setup wizard:**
- Fresh install → `serve` → all routes redirect to `/setup/welcome` until config is written.
- Bad API key on step 2 → inline error, advance blocked.
- Successful setup writes a valid TOML, atomically.
- Re-running setup never overwrites the manifest.

**Cleanup wizard:**
- Step 1 → Step 4 happy path with a 5-meeting selection produces the expected manifest transitions.
- Going Back from Step 2 to Step 1 preserves filter values.
- Changing filters after a selection prompts confirmation and clears selections.
- Browser refresh at every step restores wizard state.

**Per-meeting transactions (re-asserted at the web layer):**
- Wizard archive: each meeting transitions `pending → archived` with manifest log entries.
- Wizard purge: each verified meeting transitions `archived → deleted`.
- Failure injection (fake repository raises mid-download): meeting state is `failed_download`, archive rolled back, no delete attempted.
- Cancel mid-archive: completed meetings stay archived, remaining stay untouched.

**Operation registry & SSE:**
- Two POSTs to start an archive yield 201 then 409 with the original op_id.
- Subscriber connecting after the operation is half-done sees full replay buffer.
- Subscriber that disconnects and reconnects with `Last-Event-ID` resumes correctly.

**Lifecycle:**
- Heartbeat-shutdown fires exactly once after threshold.
- Heartbeat-shutdown defers while operation runs.
- Quit-app endpoint triggers shutdown immediately when no op is active.
- Single-instance lockfile blocks a second `serve`.

**Presets:**
- Auto-migration from v1 `[rules.auto]` produces a single default preset and a `.v1.bak` backup.
- Name collision on create returns a clear error.
- Deleting the default preset doesn't auto-promote another.

**History:**
- Date-range + state filter combinations produce expected row counts.
- URL query string round-trips.

**Security:**
- Non-loopback bind without `--i-know-what-im-doing` fails with a clear message.
- Request without session token → 401 (or redirect to a "session required" page).
- POST without CSRF → 403.
- Cross-origin POST → blocked by CORS pre-flight.

### 11.6 Coverage targets

- ≥ 85% overall (v1's 91% target dilutes as web layer grows; absolute lines covered grow significantly).
- 100% on `core/pipeline.py`, `core/manifest.py` (already true in v1; protect from regressions).
- 100% on `web/lifecycle.py` (heartbeat + shutdown).
- 100% on `web/operations.py` (concurrency-bounded job runner; race conditions are unforgiving).
- 90% on every route module.

### 11.7 CI

- Existing GitHub Actions workflow gets one new step: `pytest tests/web/` (already covered by the existing `pytest` invocation; called out separately so failures are obvious in the run summary).
- mypy `--strict` extended to `application/` and `web/` (currently only on `core/`).
- Coverage gate enforced at CI (`--cov-fail-under=85`).

### 11.8 Manual smoke checklist (before each release)

A short markdown checklist at `docs/superpowers/specs/v2-release-smoke.md` (created during implementation), to be ticked off in the PR. Covers things automated tests can't:
- Run `firefliesclearer serve` on a clean machine — browser opens, setup wizard appears.
- Walk through setup with a real API key.
- Run the Cleanup wizard with a small (< 5 meeting) real selection. Watch progress; verify files on disk; verify deletion in fireflies.ai.
- Close the browser; confirm server exits within 60s.
- Re-open `serve`; verify dashboard reflects the previous run.
- Test "Open in Fireflies" link, "Open archive folder," side panel, retry from dashboard.

---

## 12. Acceptance criteria (definition of done for v2)

A v2 PR can be merged to `main` iff every item below is true.

### 12.1 Functional

1. **`firefliesclearer serve`** binds to `127.0.0.1` on a free port, prints the URL, and (without `--no-open`) opens the default browser to that URL with a session token.
2. **First-run wizard** appears automatically when no valid config exists. Completing all steps writes a valid `config.toml` atomically and lands the user on the Dashboard.
3. **`firefliesclearer init` is removed.** Invoking it prints a one-line redirect message to `serve` and exits 0.
4. **Dashboard** shows accurate state counts (archived / pending / failed / deleted), last-activity rows, and a Needs-attention list. All counts and lists refresh every 30s.
5. **Cleanup wizard** completes the four steps end-to-end against a real Fireflies account, producing the same on-disk artefacts and the same manifest transitions as the v1 CLI's curated path. Specifically:
   - Step 1 (Filter) — every v1 rule predicate is offered and the live count matches what v1's `scan` would produce.
   - Step 2 (Review) — selection respects deselections; shift-click range-select works; row click opens the side panel without losing selection state.
   - Step 3 (Archive) — produces the exact same canonical paths as v1; manifest entries land in `archived` state.
   - Step 4 (Purge) — verifies before deletion; manifest entries transition to `deleted`; archive folders untouched on disk.
6. **Presets** can be created from the wizard and from the Presets page. The default preset auto-loads on the Cleanup wizard's Step 1. v1 `[rules.auto]` is migrated to a single default preset with a `.v1.bak` backup created.
7. **History** page filters by date range and state; URL query strings round-trip; the side panel shows the full state-log timeline for any meeting.
8. **Settings** page edits API key (with test-connection), archive root, defaults, and concurrency. Each section saves atomically and shows a flash message.
9. **Retry from Dashboard** and **Retry from History** both successfully re-process a meeting in any `*_failed` state via the same `Pipeline.process_one` flow as the wizard.
10. **`firefliesclearer run --apply --yes [--preset NAME]`** still works and is unchanged in semantics from v1; `--preset` is the new flag that replaces v1's hard-coded `[rules.auto]` block reading.

### 12.2 Lifecycle & UX

11. **Heartbeat-shutdown** fires within 60–75 seconds of the last browser tab being closed, when no operation is active.
12. **In-flight-op safety** — if any operation is running, shutdown is deferred until it completes.
13. **Single-instance lockfile** prevents two `serve` processes against the same archive root; the second exits with a clear message naming the first instance's URL.
14. **Quit-app button** in the sidebar shuts the server down within 5 seconds when no operation is running.
15. **Browser refresh** at any wizard step preserves the user's progress; closing and re-opening the browser within 60s does too.

### 12.3 Security

16. **Loopback-only by default.** Non-`127.0.0.1` host requires `--i-know-what-im-doing`.
17. **Session token** required for any non-static request; missing/invalid → 401.
18. **CSRF token** required for any POST/PUT/DELETE; missing/mismatched → 403.
19. **CORS** rejects cross-origin requests except from the bound origin.
20. **API key** is never present in any HTTP response body or in any log line; asserted by an explicit test.

### 12.4 Architecture & code quality

21. **Layering rules** enforced: `web/` → `application/`; `application/` → `core/` and `ports/`; `infra/` implements `ports/`. No circular imports; no `web ↔ cli` cross-imports. Verified by an `import-linter` config (or a pytest that walks the import graph).
22. **`core/`, `infra/`, `ports/` are byte-identical to v1** apart from any bug fixes incidentally caught (each in an isolated commit + test).
23. **Manifest schema unchanged.** No migrations introduced in v2.
24. **All new code has type hints** and passes `mypy --strict` on `core/`, `application/`, and `web/`.
25. **Ruff** clean on the whole repo.

### 12.5 Tests & coverage

26. **All v1 tests pass unchanged.**
27. **New tests added** matching § 11.5's "MUST exist" list.
28. **Coverage** ≥ 85% overall, 100% on `core/pipeline.py`, `core/manifest.py`, `web/lifecycle.py`, `web/operations.py`, and ≥ 90% on each route module.
29. **CI green** on push and on PR.
30. **Manual smoke checklist** ticked off in the PR description.

### 12.6 Documentation

31. **README** updated with: install instructions, `serve` quickstart, screenshot of the Cleanup wizard, screenshot of the Dashboard, scheduling the auto path on Windows / macOS / Linux (CLI + cron, unchanged from v1).
32. **CHANGELOG** entry for v2 listing user-facing changes (web UI, removed `init`, added presets, `run --preset` flag).
33. **Design spec** (this document) committed before any implementation work begins.
34. **`CLAUDE.md`** updated with the v2 architecture overview (layered diagram + module boundaries).

### 12.7 Distribution

35. **`pip install firefliesclearer`** on a clean Python 3.12+ environment installs all v2 dependencies and exposes the `firefliesclearer serve` command.
36. **No Node.js or external build tools required at install time.** Tailwind CSS and HTMX are pre-built and shipped as static files in the wheel.
37. **Wheel size** stays under 5 MB (sanity check — v1 was ~1 MB; the additions should fit comfortably).

---

## 13. Open items / deferred to implementation or v3

- **Native binary / installer** (PyInstaller / Nuitka) — v3.
- **In-app OS scheduler integration** — v3, paired with the binary work.
- **Charts on Dashboard, CSV export** — deferred until requested.
- **FTS5 for history search** — defer until 100K+ row manifests appear.
- **Move-archive-on-root-change** — manual; not building automation around long file moves.
- **Issue #2 from v1** (`host_email` field returns empty in live data; suggested fix is fallback to `organizer_email`) — orthogonal to v2 scope; can be picked up in either CLI or web work as a separate commit if it blocks any wizard test.
