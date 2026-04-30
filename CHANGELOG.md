# Changelog

All notable changes to FirefliesClearer will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - Unreleased

### Added
- Local web UI via `firefliesclearer serve` (FastAPI + HTMX, no JavaScript framework).
- First-run setup wizard replaces `firefliesclearer init` (4-step: welcome / API key / archive root / defaults).
- Dashboard with state counts, last-activity rows, and a needs-attention list (auto-refreshes every 30s).
- Cleanup wizard (4 steps: filter / review / archive / purge) with live count, side panel, SSE progress streaming, and retry from dashboard.
- Presets — saved filter combinations replace v1's `[rules.auto]` block. CRUD via `/presets` page; "Save as preset" inline form on the cleanup wizard's Step 1; star-marked default preset auto-loads.
- History page (`/history`) with date-range presets + custom range, state multiselect, title search, paginated 50/page; shareable URL query string.
- History side panel (`/history/{meeting_id}/panel`) renders the full state-log timeline with details JSON.
- Settings page (`/settings`) — Connection (API key + test), Archive (root + move-warning banner), Defaults (concurrency / age / threshold), Logs (today's log viewer, open archive folder, retention), Danger zone (typed-string RESET).
- `firefliesclearer run --preset NAME` — CLI flag uses a named preset's filters. Falls back to the default preset when omitted; errors when no default and no flag.
- v1 `[rules.auto]` migration on first `serve` startup creates an "Auto cleanup" default preset and writes `<config>.v1.bak`.
- Heartbeat-driven shutdown — server exits within ~60s of the last browser tab closing.
- Single-instance lockfile prevents two `serve` processes against the same archive root.
- Quit-app sidebar button.
- Cross-platform "Open archive folder" shell-out (Windows / macOS / Linux).
- Log retention sweep on `serve` startup (default 30 days, configurable).
- Tailwind CSS standalone CLI build script (`tools/build_static.sh`); 16 vendored Lucide icons under `firefliesclearer/web/static/icons/`.

### Changed
- `firefliesclearer init` is now a one-line redirect to `serve`. The setup wizard handles all first-run config.
- `firefliesclearer run` no longer reads v1's hard-coded `[rules.auto]` block. It loads the default preset (or the named one via `--preset`).

### Migration notes
- **OR → AND filter semantics**: v1 `run` matched meetings that were `older_than_days` **OR** had `no_transcript`. v2 presets use **AND** — all active filters in a single preset must match. The v1 → v2 migration produces a single "Auto cleanup" preset with both filters set, which under AND matches **strictly fewer** meetings than v1 OR. To preserve v1 OR behavior, split the migrated preset into two separate presets via the Presets UI:
  - "Old meetings" — `older_than_days = N`
  - "Failed transcripts" — `no_transcript = true`
  Then run `firefliesclearer run --preset "Old meetings"` and `firefliesclearer run --preset "Failed transcripts"` separately, or keep one as default for the most common case.

### Security
- Loopback-only by default (`127.0.0.1`); non-loopback host requires `--i-know-what-im-doing`.
- Session token in URL on browser auto-open; required for non-static requests.
- CSRF token (signed itsdangerous cookie + matching hidden field) on all POST/PUT/DELETE.

## [1.0.0] - 2026-04-28

### Added
- Initial CLI release: `init`, `scan`, `archive`, `purge`, `run`, `audit`, `status`, `history`.
- Local SQLite manifest tracks state for safe re-runs.
- ReportLab PDF rendering for meeting summaries (no native deps).
- Cross-platform support (Windows, macOS, Linux).
