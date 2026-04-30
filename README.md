# FirefliesClearer

Safely archive and clean up [Fireflies AI](https://fireflies.ai) meetings.

For each meeting matched by configurable rules, FirefliesClearer:

1. Lists candidates
2. Downloads artifacts to local disk: `summary.pdf` (rendered locally), `audio.mp3`, `transcript.md`, `metadata.json`
3. Verifies the archive on disk
4. Only then deletes the meeting from Fireflies

State is tracked in a local SQLite manifest for safe re-runs and audit.

See [CHANGELOG.md](CHANGELOG.md) for what changed in v2.

## v2 Web UI

v2 adds a local web UI — no cloud, no account beyond your existing Fireflies API key.

**30-second quickstart:**

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"   # Windows
# .venv/bin/pip install -e ".[dev]"     # macOS / Linux
firefliesclearer serve
```

Your browser opens automatically to `http://127.0.0.1:<port>/?token=<...>`. A 4-step setup wizard walks you through API key, archive root, and defaults on first run.

[screenshot placeholder]

**Web UI features:**

- **Dashboard** — state-count cards, last-activity rows, needs-attention list (auto-refreshes every 30s).
- **Cleanup wizard** — 4 steps: filter meetings with a live count, review + select, archive to disk, then purge from Fireflies. SSE progress streaming; retry failed meetings from the dashboard.
- **Presets** — save filter combinations as named presets; star one as default. Replaces the v1 `[rules.auto]` config block.
- **History** (`/history`) — paginated view of all archived/deleted meetings with date-range, state, and title filters. Shareable URLs; click any row to see the full state-log timeline.
- **Settings** (`/settings`) — API key, archive root, concurrency defaults, log viewer, and a danger-zone RESET.

> **Note:** `firefliesclearer init` is removed in v2. Use `firefliesclearer serve` — the setup wizard handles first-run configuration.

See [docs/superpowers/specs/v2-release-smoke.md](docs/superpowers/specs/v2-release-smoke.md) for the manual pre-release smoke checklist.

## Setup

Requires Python 3.12+. PDFs are rendered via `reportlab` (pure Python, no native dependencies).

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
firefliesclearer serve    # web UI — recommended for first-run setup
```

On first run, `serve` opens a 4-step setup wizard in your browser and writes a config file at `%APPDATA%\firefliesclearer\config.toml` (Windows) or `~/.config/firefliesclearer/config.toml` (Linux/macOS).

> **v1 users:** `firefliesclearer init` is replaced by `serve`. Running `init` now prints a redirect message. Your existing `[rules.auto]` config is automatically migrated to a default preset on the first `serve` startup, and a `<config>.v1.bak` backup is written.

## Curated cleanup (review and confirm)

```bash
firefliesclearer scan --older-than-days 90 --title-contains test
# Edit the resulting selections/scan_*.json (set selected:false on rows to keep)
firefliesclearer archive --selection selections/scan_20260428T1145.json
firefliesclearer purge   --selection selections/scan_20260428T1145.json
```

## Auto cleanup (cron / Task Scheduler)

```bash
firefliesclearer run                         # dry-run using the default preset
firefliesclearer run --apply --yes           # actually mutate; suitable for cron
firefliesclearer run --preset "Old meetings" # use a named preset
```

`run` loads the **default preset** (configured via `/presets` in the web UI) and applies its filters. Pass `--preset NAME` to use a specific preset. If no default is set and `--preset` is omitted, the command exits with a clear error.

> **v1:** the hard-coded `[rules.auto]` block is no longer read by `run`. Create equivalent presets via `firefliesclearer serve` → `/presets`. See [CHANGELOG.md](CHANGELOG.md) for the OR → AND filter-semantics change.

## Audit

```bash
firefliesclearer status                       # counts per state
firefliesclearer history --month 2026-04      # what was deleted in April 2026
```

## Configuration

Override precedence (highest wins):

1. CLI flags (`--config`, etc.)
2. `FIREFLIES_API_KEY` environment variable
3. `./firefliesclearer.toml` (project-local)
4. User config (see paths above)

Sample config (v2 — presets replace the old `[rules.auto]` block):

```toml
[fireflies]
api_key = "ff_xxx"

[archive]
root_dir = "D:/firefliesclearer-archive"
summary_format = "pdf"

[run]
concurrency = 3
delete_confirmation_threshold = 10
default_age_days = 180
log_retention_days = 30
```

Presets (saved filter combinations) are managed via `firefliesclearer serve` → `/presets`, not in the TOML file.

## Safety guarantees

- Never deletes a meeting unless its archive is verified on disk (existence + non-zero size for each expected file). The sha256 of every file is recorded in the manifest so the audit trail can detect tampering or drift after the fact.
- Per-meeting transactions: one failure never aborts a run.
- API key is redacted in all logs.
- `run` defaults to dry-run; `--apply` required for any mutation.
- Crash mid-flight is safe: state stays at `archived`, never an inconsistent intermediate.

## Development

```bash
.venv\Scripts\ruff check .
.venv\Scripts\mypy firefliesclearer
.venv\Scripts\pytest -q
```

Coverage is 100% on `core/pipeline.py` and `core/manifest.py` (the safety-critical core), >=80% overall.

The contract test (live API) is opt-in:

```bash
$env:FIREFLIES_TEST_API_KEY = "<your-test-key>"
.venv\Scripts\pytest -m contract -q
```

## License

MIT.
