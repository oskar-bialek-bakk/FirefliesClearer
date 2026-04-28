# FirefliesClearer

A Python CLI for safely archiving and cleaning up Fireflies AI meetings: list → download artifacts → delete, tracked in a local SQLite manifest.

## Status
Pre-implementation. Design spec at `docs/superpowers/specs/2026-04-28-firefliesclearer-design.md`.

## Architecture (one-line)
Layered: CLI (Typer) → application (commands + pipeline) → domain (rules, manifest, archiver) → infrastructure (Fireflies GraphQL client, PDF renderer, filesystem). Domain depends on ports, not concrete adapters, so a future web UI replaces only the CLI layer.

## Stack
- Python 3.12+
- `httpx` (async GraphQL), `typer` + `rich` (CLI), `pydantic` (config + models), `weasyprint` (PDF), `platformdirs` (cross-platform config paths), SQLite (manifest)
- `pytest`, `respx`, `mypy --strict` (on `core/`), `ruff`

## Repository layout (planned)
```
firefliesclearer/
  core/        # domain — pure, no I/O
  ports/       # interfaces the domain depends on
  infra/       # concrete adapters (Fireflies, FS, PDF, config)
  cli/         # Typer commands, thin shell over application layer
  templates/   # PDF/HTML templates
tests/
docs/superpowers/specs/
```

## Workflow rules
- TDD: tests first (RED → GREEN → REFACTOR), 80% coverage minimum, 100% on `core/pipeline.py` + `core/manifest.py`.
- Files ≤400 lines, single responsibility.
- Immutable domain types (frozen dataclasses).
- No mutation of inputs; return new values.
- Conventional commits.
- Never hardcode secrets; API key from config file (precedence: CLI flag → env var → project config → user config).

## Safety invariants (non-negotiable)
1. Never delete a Fireflies meeting unless its archive is verified on disk (file existence + non-zero + checksum recorded).
2. Per-meeting transactional: one failure never aborts the run, only that meeting.
3. API key is redacted in all logs.
4. `run` defaults to dry-run; `--apply` required for mutations.
5. Crash mid-flight is safe: state stays at `archived`, never an inconsistent intermediate.
