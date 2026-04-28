# FirefliesClearer

Safely archive and clean up [Fireflies AI](https://fireflies.ai) meetings.

For each meeting matched by configurable rules, FirefliesClearer:

1. Lists candidates
2. Downloads artifacts to local disk: `summary.pdf` (rendered locally), `audio.mp3`, `transcript.md`, `metadata.json`
3. Verifies the archive on disk
4. Only then deletes the meeting from Fireflies

State is tracked in a local SQLite manifest for safe re-runs and audit.

## Setup

Requires Python 3.12+. PDFs are rendered via `reportlab` (pure Python, no native dependencies).

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
firefliesclearer init
```

`init` writes a config file at `%APPDATA%\firefliesclearer\config.toml` (Windows) or `~/.config/firefliesclearer/config.toml` (Linux/Mac) with your Fireflies API key, archive root, and auto-rule defaults.

## Curated cleanup (review and confirm)

```bash
firefliesclearer scan --older-than-days 90 --title-contains test
# Edit the resulting selections/scan_*.json (set selected:false on rows to keep)
firefliesclearer archive --selection selections/scan_20260428T1145.json
firefliesclearer purge   --selection selections/scan_20260428T1145.json
```

## Auto cleanup (cron / Task Scheduler)

```bash
firefliesclearer run                # dry-run
firefliesclearer run --apply --yes  # actually mutate; suitable for cron
```

The auto path applies hard rules from config: `older_than_days` and (optionally) `delete_failed_transcripts`.

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

Sample config:

```toml
[fireflies]
api_key = "ff_xxx"

[archive]
root_dir = "D:/firefliesclearer-archive"
summary_format = "pdf"

[rules.auto]
older_than_days = 180
delete_failed_transcripts = true

[run]
concurrency = 3
delete_confirmation_threshold = 10
```

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
