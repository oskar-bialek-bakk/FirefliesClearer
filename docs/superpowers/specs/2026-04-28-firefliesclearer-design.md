# FirefliesClearer — Design Spec

**Date:** 2026-04-28
**Status:** Approved — ready for implementation planning
**Author:** Oskar Białek (with Claude as collaborator)

---

## 1. Purpose & scope

A Python CLI that archives and cleans up [Fireflies AI](https://fireflies.ai) meetings. For each meeting matched by configurable rules, the tool downloads artifacts to local disk, verifies the archive, and only then deletes the meeting from Fireflies. State is tracked in a local SQLite manifest for audit and safe re-runs.

**Out of scope (v1):**
- Re-uploading or restoring meetings.
- Any UI other than CLI (web UI is a deliberate future extension; the architecture leaves room for it).
- Cross-account / multi-tenant operation (each user runs the tool against their own Fireflies account).

**Non-goals:**
- Real-time monitoring or webhooks.
- Editing/transforming transcripts beyond a light cleanup pass for the `transcript.md` output.

---

## 2. User stories

- **U1 — Recurring hygiene (auto path).** As a power user, I want to run `firefliesclearer run --apply` from a scheduler (cron / Task Scheduler) so old meetings (> N days) and meetings where transcription failed are archived locally and removed from Fireflies, with no interactive prompts.
- **U2 — Curated cleanup (review path).** As a careful user, I want to scan my account with rich filters (age, duration, title, host, participants, tags), review the candidate list, optionally edit it to deselect specific meetings, and then archive + delete only the ones I confirmed.
- **U3 — Audit.** As an operator, I want to query "what was deleted last month, and where are its artifacts on disk?" without re-hitting the Fireflies API.
- **U4 — Resume after failure.** As a user whose download crashed midway, I want to re-run the same command and have only the unfinished/failed meetings retried — already-archived ones skipped, already-deleted ones not re-deleted.
- **U5 — Multi-user distribution.** As a colleague receiving this tool, I want to run `firefliesclearer init`, paste my own API key, choose my own archive directory, and start using it without sharing tokens or editing source.

---

## 3. Architecture

Layered, with domain depending on ports (interfaces) rather than concrete adapters. The CLI is a thin shell over the application layer; a future web UI replaces only the presentation layer.

```
┌─────────────────────────────────────────────────────────────────┐
│  Presentation layer                                             │
│  - CLI (Typer + Rich)               ← v1                        │
│  - Web UI (FastAPI + small SPA)     ← future                    │
└─────────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────────┐
│  Application / orchestration layer                              │
│  - Commands: init, scan, archive, purge, run, status, history   │
│  - Pipeline coordinator (list → archive → verify → delete)      │
└─────────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────────┐
│  Domain / core layer (pure, fully testable without I/O)         │
│  - Selection engine (rules → predicates over Meeting)           │
│  - Archive writer (atomic writes to canonical layout)           │
│  - Manifest (SQLite, state machine per meeting)                 │
│  - Pipeline (per-meeting transactional orchestration)           │
└─────────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────────┐
│  Infrastructure layer                                           │
│  - Fireflies GraphQL client (httpx, async)                      │
│  - Filesystem adapter                                           │
│  - PDF renderer (markdown → templated HTML → PDF via WeasyPrint)│
│  - Config loader (TOML, pydantic-validated)                     │
└─────────────────────────────────────────────────────────────────┘
```

### 3.1 Modules

**`firefliesclearer/core/`** — domain core, no I/O.

| Module | Responsibility |
|---|---|
| `models.py` | Frozen dataclasses: `Meeting`, `MeetingArtifact`, `Rule`, `MatchResult`. |
| `rules.py` | Rule predicates and a `RuleEngine` that returns `MatchResult(matched, reasons[])`. |
| `manifest.py` | SQLite-backed state machine per meeting. Audit log of every transition. |
| `archiver.py` | Coordinates artifact writing in canonical layout. Atomic per-meeting (writes to tmp dir, renames on success). |
| `pipeline.py` | Per-meeting transactional orchestrator: list → match → archive → verify → delete. |

**`firefliesclearer/ports/`** — interfaces the core depends on; concrete impls live in infra.

| Module | Responsibility |
|---|---|
| `meeting_repository.py` | `list_meetings(filter) -> AsyncIterator[Meeting]`, `fetch_artifacts(id) -> ArtifactBundle`, `delete_meeting(id) -> None`. |
| `summary_renderer.py` | `render(summary_data) -> bytes` (the PDF). |
| `clock.py` | `now() -> datetime` (injected for testable age rules). |

**`firefliesclearer/infra/`** — concrete adapters.

| Module | Responsibility |
|---|---|
| `fireflies_client.py` | GraphQL client over `httpx.AsyncClient`. Implements `MeetingRepository`. Pagination, retry on 5xx/429, rate-limit aware. |
| `pdf_renderer.py` | Implements `SummaryRenderer`. Markdown → HTML (Jinja2 template + CSS) → PDF (WeasyPrint). |
| `fs.py` | Atomic write helpers, slug+date path builder. |
| `config.py` | Loads/merges TOML config from precedence chain. Validated with pydantic. |

**`firefliesclearer/cli/`** — Typer commands.

| Command | Purpose |
|---|---|
| `init` | Interactive first-run; writes user config (API key, archive root, defaults). |
| `scan [filters...]` | Curated path step 1: lists candidates as Rich table; writes `selections/scan_<ts>.json`. No state changes. |
| `archive --selection <file> [--dry-run]` | Curated path step 2: archives every selected meeting. Updates manifest. No deletions. |
| `purge --selection <file> [--dry-run] [--yes]` | Curated path step 3: verifies archive completeness, then deletes. Updates manifest. Confirms above threshold unless `--yes`. |
| `run [--apply] [--yes]` | Auto path: applies hard rules from config, full pipeline. Default = dry-run; `--apply` mutates. |
| `status` | Manifest summary: counts per state, last run, recent failures. |
| `history [--month YYYY-MM]` | Audit query against the manifest. |

### 3.2 Tech stack

- **Language:** Python 3.12+.
- **CLI:** Typer + Rich.
- **HTTP:** `httpx` (async).
- **Config & models:** Pydantic v2.
- **Cross-platform paths:** `platformdirs`.
- **PDF:** WeasyPrint (primary). `reportlab` is the documented fallback if WeasyPrint's GTK dependency proves painful for Windows colleagues; final choice confirmed during implementation by an install test on a clean Windows machine.
- **Persistence:** SQLite (stdlib).
- **Tests:** pytest, respx, pytest-asyncio.
- **Tooling:** ruff, mypy (`--strict` on `core/`).

---

## 4. Selection rules

### 4.1 Available rule predicates (v1)

All rules surface as filters in the curated path; only `older_than_days` and `no_transcript` run in the auto path.

| Rule | Auto path | Curated path |
|---|---|---|
| `older_than_days: int` | yes | yes |
| `no_transcript: bool` | yes | yes |
| `duration_below_minutes: float` | no | yes |
| `title_contains: list[str]` (case-insensitive substrings) | no | yes |
| `title_regex: str` | no | yes |
| `host_email: list[str]` | no | yes |
| `participants_below: int` | no | yes |
| `has_tag: list[str]` | no | yes |

Rules combine with AND. (OR-of-AND groups are explicitly out of scope for v1; users can run multiple `scan` invocations and concatenate selection files if needed.)

### 4.2 Match reasons

`MatchResult.reasons` carries the names of every rule that matched. These appear in the selection file's `matched_rules` column so the user understands why each meeting was flagged.

---

## 5. Pipeline

### 5.1 Curated path (multi-step)

1. `scan` — fetch metadata (no audio/PDF), apply rules, write `selections/scan_<ts>.json`.
2. User optionally edits the file (sets `selected: false` on rows to keep).
3. `archive --selection <file>` — for each selected meeting, fetch artifacts, render PDF, write atomically to canonical path, mark `archived` in manifest.
4. User optionally inspects the archive on disk.
5. `purge --selection <file>` — verify archive completeness, delete from Fireflies, mark `deleted`.

### 5.2 Auto path (single command)

`run [--apply] [--yes]` does steps 1–5 in one process using only auto-path rules. No selection file written. `--apply` required for any mutation; `--yes` skips the count-threshold confirmation.

### 5.3 Per-meeting transaction (safety core)

```
state: pending
  ├─ fetch artifacts                  fail → failed_fetch
  ├─ download audio.mp3 → tmp/<id>/   fail → failed_download
  ├─ render summary.pdf → tmp/<id>/   fail → failed_render
  ├─ write transcript.md, metadata.json → tmp/<id>/
  ├─ verify (existence + non-zero + checksums) fail → failed_verify
  ├─ rename tmp/<id>/ → canonical path
  │     state: archived
  ├─ call delete mutation             fail → deleted_failed
  └─ state: deleted
```

**Invariant:** `state == deleted` ⇒ archive directory exists, all required files present, `verified_at` is set. Any other state means the meeting still exists in Fireflies (or the local state hasn't caught up — in which case re-running converges).

---

## 6. On-disk layout

```
<archive_root>/
├── archive/
│   └── 2026/
│       └── 04/
│           └── 2026-04-12_kickoff-marketing-q2_<meetingId>/
│               ├── metadata.json     # title, date, host, participants, duration, tags, source URL
│               ├── transcript.md     # speaker-attributed; one paragraph per speaker turn; no content edits
│               ├── summary.pdf       # rendered locally
│               └── audio.mp3
├── selections/
│   └── scan_20260428T1145.json
├── logs/
│   └── 2026-04-28.log                # daily-rotated JSON lines
└── manifest.db
```

**Slug rules:**
- Base: title (or "untitled" if missing).
- ASCII-fold (Polish/diacritics), lowercase.
- Replace runs of non-alphanumeric chars with `-`.
- Trim leading/trailing `-`.
- Truncate to 60 characters.

**Idempotency:** the canonical path encodes the meeting ID, so re-runs never collide. If a directory exists and matches a manifest entry in `archived`/`deleted`, skip. If it exists but no manifest entry (drift), bail with a warning — never overwrite.

---

## 7. Selection file format

`selections/scan_<ts>.json`:

```jsonc
{
  "scan_id": "scan_20260428T1145",
  "created_at": "2026-04-28T11:45:00+02:00",
  "filters_applied": {
    "older_than_days": 90,
    "title_contains": ["test", "draft"],
    "duration_below_minutes": 2
  },
  "meetings": [
    {
      "id": "01HW...",
      "title": "Test Standup",
      "date": "2025-12-01T09:00:00Z",
      "duration_min": 1.5,
      "host": "oskar.bialek@bakk.com",
      "participants": 1,
      "tags": [],
      "selected": true,
      "matched_rules": ["older_than_days", "duration_below_minutes"]
    }
  ]
}
```

User-editable: setting `selected: false` skips that meeting in subsequent `archive` / `purge` invocations. The file is the single source of truth between commands; subsequent commands do not re-fetch from Fireflies for selection purposes (they re-fetch only to download artifacts at archive time).

---

## 8. Manifest schema

```sql
CREATE TABLE meetings (
  meeting_id        TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  meeting_date      TEXT NOT NULL,         -- ISO-8601
  state             TEXT NOT NULL,         -- pending|archived|deleted|failed_fetch|failed_download|failed_render|failed_verify|deleted_failed
  archive_path      TEXT,
  audio_sha256      TEXT,
  summary_sha256    TEXT,
  transcript_sha256 TEXT,
  archived_at       TEXT,
  verified_at       TEXT,
  deleted_at        TEXT,
  last_error        TEXT
);

CREATE TABLE state_log (
  id            INTEGER PRIMARY KEY,
  meeting_id    TEXT NOT NULL,
  from_state    TEXT,
  to_state      TEXT NOT NULL,
  at            TEXT NOT NULL,
  details       TEXT,                       -- JSON blob (error text, file sizes, etc.)
  FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);

CREATE INDEX idx_meetings_state ON meetings(state);
CREATE INDEX idx_state_log_meeting ON state_log(meeting_id);
```

SQLite runs in WAL mode. Writes are serialized through a single `Manifest` instance; the application layer never opens raw connections.

### 8.1 State machine

Legal transitions:
- `pending → archived | failed_fetch | failed_download | failed_render | failed_verify`
- `archived → deleted | deleted_failed`
- `failed_* → pending` (re-queue on retry)
- `deleted_failed → deleted` (retry succeeds)

Any other transition raises `IllegalStateTransition`.

---

## 9. Configuration

### 9.1 Locations & precedence

Highest precedence wins.

1. CLI flag (`--archive-root`, `--api-key`, `--config`).
2. `FIREFLIES_API_KEY` environment variable.
3. Project-local `./firefliesclearer.toml` (optional; useful for cron in a fixed working directory).
4. User config: cross-platform via `platformdirs`.
   - Windows: `%APPDATA%\firefliesclearer\config.toml`
   - macOS: `~/Library/Application Support/firefliesclearer/config.toml`
   - Linux: `~/.config/firefliesclearer/config.toml`

### 9.2 Schema (TOML)

```toml
[fireflies]
api_key = "ff_xxx"

[archive]
root_dir = "D:/firefliesclearer-archive"
summary_format = "pdf"   # v1 supports "pdf" only; reserved for future "html" / "html+pdf"

[rules.auto]
older_than_days = 180
delete_failed_transcripts = true

[run]
concurrency = 3
delete_confirmation_threshold = 10
```

### 9.3 First-run flow

`firefliesclearer init` interactively:
1. Prompts for API key (stored in user config; never echoed back to terminal).
2. Prompts for archive root (default: `<user-documents>/firefliesclearer-archive`).
3. Prompts for auto-path rule defaults (sensible suggestions).
4. Writes config to user-config location.
5. Performs a sanity ping (`getUser` query) to verify the API key works.

Validation is via Pydantic. Missing API key in any non-init command yields a friendly error pointing at `firefliesclearer init`.

---

## 10. Error handling, retries, observability

### 10.1 Retries & backoff

- Network calls (Fireflies GraphQL, audio download): 3 retries, exponential backoff 1s/4s/16s with ±25% jitter. Retry only on connection errors and HTTP 429/5xx.
- 429 with `Retry-After` header: honored exactly, capped at 60s.
- 4xx (non-429): no retry; surface error.
- PDF render & filesystem I/O: no retries — fail fast and log.
- Delete mutation: 1 retry max; otherwise re-running `purge` is the proper retry path (idempotent).

### 10.2 Concurrency

- Within a meeting: sequential.
- Across meetings: async pool, `concurrency = 3` by default (configurable).
- Audio downloads stream chunked to disk; never load the full file into memory.

### 10.3 Observability

- **stdout:** Rich progress bars per stage; final summary table.
- **`logs/YYYY-MM-DD.log`:** structured JSON-lines. One entry per state transition + one per outbound API call (method, status, latency, redacted token). Daily rotation, 30-day retention.
- **manifest `state_log`:** authoritative audit trail, queryable by `history`.
- **`status` command:** prints counts per state, last run timestamp, count of meetings in failed states with the latest error message.

### 10.4 Safety guardrails (non-negotiable)

1. **No delete without verified archive.** Asserted in `pipeline.py`; re-checked as a precondition inside `purge`.
2. **No silent overwrites.** Pre-existing canonical dir + matching manifest entry → skip; pre-existing dir + no manifest entry → bail with a warning.
3. **API key never logged.** Custom logging filter; an explicit unit test asserts redaction.
4. **Confirmation prompt.** `purge` and `run --apply` prompt when count > `delete_confirmation_threshold` (default 10). `--yes` bypasses (for cron). `archive` does not prompt (it never deletes).
5. **Soft kill safe.** Ctrl-C between meetings: clean exit, tmp dir cleaned. Ctrl-C during a delete API call: state remains `archived`, `purge` retries; Fireflies' delete is idempotent on already-deleted IDs.

---

## 11. Testing strategy

### 11.1 Pyramid

| Layer | Scope | Tooling |
|---|---|---|
| Unit (domain) | rules, manifest state machine, slug, pipeline transactions w/ fakes | pytest |
| Integration (infra) | Fireflies client w/ respx, PDF renderer end-to-end, config loader | pytest, respx |
| Contract (live API) | opt-in, gated by env var | `pytest -m contract` |
| End-to-end CLI | each command happy path + dry-run no-op | Typer's `CliRunner` |

Coverage target: ≥80% overall, 100% on `core/pipeline.py` and `core/manifest.py`.

### 11.2 Fakes over mocks

- `InMemoryMeetingRepository` implements the port with an in-memory artifact store. Used by all domain and CLI tests — no `unittest.mock`.
- `FakeSummaryRenderer` returns deterministic bytes.
- `FrozenClock` makes age rules deterministic without `freezegun`.

### 11.3 Critical-path test cases

These MUST exist before declaring the feature complete:

**Rules:**
- Each predicate: table-driven match / no-match / boundary.
- Combined rules: `matched_rules` reasons preserved.

**Pipeline transactions:**
- Happy path → state log shows `pending → archived → deleted`.
- Each failure mode → expected `failed_*` state, no archive corruption, no delete.
- Crash mid-flight (`KeyboardInterrupt`) between archive and delete → state stays `archived`.
- Idempotency: second run is no-op for `deleted` meetings, retries `failed_*`.

**Manifest:**
- Every legal transition succeeds; every illegal one raises.
- Concurrent writes don't corrupt state (WAL + serialized writes).
- `history` audit query returns expected rows for a date range.

**Archiver:**
- Slug: Polish/diacritics, special chars, very long titles → ASCII-folded, 60-char-truncated, idempotent.
- Atomic rename: simulated mid-write failure → tmp dir cleaned, no partial canonical path.
- Drift detection: pre-existing canonical dir without manifest entry → warn-and-skip.

**Fireflies client (respx):**
- Cursor-based pagination across multiple pages.
- 429 with `Retry-After` honored.
- 5xx retried with correct backoff timing.
- 4xx (non-429) not retried.
- API key redaction in logs.

**PDF renderer:**
- Output is a valid PDF (magic bytes `%PDF`).
- Polish characters render without raising.
- Empty/missing summary fields produce sensible placeholders, not crashes.

**Config:**
- Precedence chain (CLI → env → project → user) verified at every level.
- Missing API key → actionable error message naming `firefliesclearer init`.

**CLI smoke:**
- Each command exits 0 on happy path.
- `run` (no `--apply`) is a true no-op: zero API mutations and zero writes outside `logs/` (the daily run log is still written so dry-runs are auditable).
- Confirmation threshold fires; `--yes` bypasses.

### 11.4 Contract tests (opt-in)

Marked `@pytest.mark.contract`. Skipped unless `FIREFLIES_TEST_API_KEY` is set. Verify GraphQL schema fields we depend on still exist. Never call delete against real data — only against a fixture meeting in a dedicated test workspace, gated further by an explicit `--live-delete` flag invoked manually before releases.

### 11.5 CI

GitHub Actions: ruff + mypy (`--strict` on `core/`) + unit + integration tests on every push. Contract tests off by default; toggleable via `workflow_dispatch`.

### 11.6 TDD

Per the user's global workflow rules: failing test first → minimal implementation → refactor → coverage gate. Build order: `rules` → `manifest` → `archiver` → `pipeline` → infra adapters → CLI.

---

## 12. Open items / deferred to implementation

- **PDF library final choice.** WeasyPrint vs. reportlab decided by an install test on a clean Windows machine during early implementation. The renderer port hides the choice from the rest of the system.
- **Exact Fireflies GraphQL field names.** The spec assumes the documented shape (meetings list with metadata; `summary` object with overview/action_items/keywords; audio URL; delete mutation). The contract test will catch any drift from current API.
- **OR-of-AND rule composition.** Out of v1; revisit if user stories demand it.
- **Web UI.** Out of v1 by design. The application/domain layers are built so the UI is a second adapter, not a rewrite.

---

## 13. Acceptance criteria (definition of done for v1)

1. `firefliesclearer init` writes a valid config, sanity-pings the API, and reports success.
2. `firefliesclearer scan --older-than 90` lists matching meetings and writes a selection file.
3. Editing the selection file (flipping `selected` to `false`) is honored by `archive` and `purge`.
4. `firefliesclearer archive --selection ...` produces a complete artifact set on disk for every selected meeting and updates the manifest to `archived`.
5. `firefliesclearer purge --selection ...` deletes only meetings whose archive is verified and updates the manifest to `deleted`.
6. `firefliesclearer run` (no `--apply`) is a true no-op (no API mutations, no filesystem writes outside `logs/`); `run --apply --yes` performs the full pipeline non-interactively.
7. Killing the process at any moment leaves the manifest and the filesystem in a consistent state; re-running converges.
8. `firefliesclearer status` reports current manifest counts; `history --month 2026-04` returns the audit trail.
9. Test coverage ≥80% overall, 100% on `core/pipeline.py` and `core/manifest.py`. CI green.
10. README documents installation, `init`, the two paths, and how to schedule the auto path on Windows Task Scheduler / Linux cron.
