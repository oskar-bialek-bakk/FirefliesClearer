# CLAUDE.md — FirefliesClearer

Project-specific guidance for Claude Code sessions in this repo.

## Architecture overview (v2)

Layered, ports-and-adapters style:

```
┌────────────────────────────────────────────────────┐
│ Presentation                                       │
│  ┌──────────────┐    ┌──────────────────────────┐  │
│  │ cli/         │    │ web/                     │  │
│  │ Typer cmds   │    │ FastAPI + HTMX templates │  │
│  └──────────────┘    └──────────────────────────┘  │
├────────────────────────────────────────────────────┤
│ application/                                       │
│  setup_service · scan_service · archive_service    │
│  purge_service · audit_service · preset_service    │
├────────────────────────────────────────────────────┤
│ core/  (domain — pure)                             │
│  models · pipeline · rules · manifest · archiver   │
├────────────────────────────────────────────────────┤
│ ports/  (protocols)        infra/  (adapters)      │
│  clock                      system_clock           │
│  meeting_repository         fireflies_client       │
│  summary_renderer           pdf_renderer           │
│                             config · logging       │
│                             atomic_toml · fs       │
│                             log_retention          │
│                             open_folder            │
└────────────────────────────────────────────────────┘
```

## Boundaries

- `web/` and `cli/` may import from `application/` and `core/`. They MUST NOT import from each other.
- `application/` may import from `core/` and `ports/`. It MUST NOT import from `infra/` directly — use `ports/` protocols.
- `core/` is pure: no IO, no async, no framework imports. Depends only on stdlib + Pydantic.
- `infra/` implements `ports/` protocols and contains all IO. May import from `core/` for domain types only.
- `ports/` defines Protocols. No implementation, no IO.

These boundaries are enforced by an `import-linter` contract in CI (Phase 9.8).

## Running tests

- Tests via `.venv/Scripts/pytest.exe` (Windows) or `.venv/bin/pytest` (Unix).
- mypy strict on `firefliesclearer/`: `.venv/Scripts/mypy.exe firefliesclearer`.
- Ruff: `.venv/Scripts/ruff.exe check firefliesclearer tests` and `.venv/Scripts/ruff.exe format --check firefliesclearer tests`.
- Pyproject sets `asyncio_mode = "auto"` — never add `@pytest.mark.asyncio` decorators.

## Web testing patterns

- `tests/web/conftest.py:configured_app` is the canonical fixture builder.
- `Manifest.open(path)` thread-pins; web tests use raw `sqlite3.connect(check_same_thread=False)` in the fixture.
- `TestClient` context-manager + `client.portal.call(...)` pattern for async ops (see `tests/web/routes/test_cleanup_step3.py`).
- HTMX POSTs are urlencoded (CSRF middleware rejects multipart). All POST forms include `<input type="hidden" name="_csrf" value="{{ request.cookies.get('ffc_csrf', '') }}">`.

## Common gotchas

- Pipeline.archive_one(meeting) and purge_one(meeting) take a `Meeting` object, not a meeting_id.
- `Manifest.open(path)`, NOT `Manifest(path)`. `Manifest.transition(mid, *, to=, at=, ...)` is keyword-only on `to`/`at`.
- Modern Starlette `TemplateResponse(request, "name.html", {ctx})` — the older dict-with-request form raises `TypeError`.
- Ruff selects `["E","F","W","I","N","UP","B","SIM","RUF"]` but NOT `SLF001`/`BLE001`. Don't add those noqa directives — they trip RUF100.
- Mypy class-scope name resolution gotcha: `class Foo: def list(self) -> list[Bar]: ...` fails mypy strict even with `from __future__ import annotations`. Workaround: alias `_List = builtins.list` (see `application/preset_service.py`).
- `tomli_w` cannot serialize Python `None`; use `model_dump(mode="json", exclude_none=True)` per Pydantic model before passing to `tomli_w.dump`.

## Phase 5 deferrals (not yet done as of v2 release)

- `web/routes/cleanup.py` is ~1230 lines — natural break is per-step submodules.
- 4x `_make_*_runner` helpers spread across files; could extract a kind-driven factory.
- `_sid` / `_store` / `_templates` / `_redirect` helpers duplicated across `setup.py`, `cleanup.py`, `dashboard.py`, `presets.py`, `settings.py` — could extract to `web/_helpers.py`.
- `step1_filter.html` filter fieldsets duplicate `presets/_filter_fieldsets.html` — could DRY into a single shared partial.

These don't block v2 release; flagged for v2.x maintenance work.

## Safety invariants (non-negotiable)

1. Never delete a Fireflies meeting unless its archive is verified on disk (file existence + non-zero + checksum recorded).
2. Per-meeting transactional: one failure never aborts the run, only that meeting.
3. API key is redacted in all logs.
4. `run` defaults to dry-run; `--apply` required for mutations.
5. Crash mid-flight is safe: state stays at `archived`, never an inconsistent intermediate.

## Workflow rules

- TDD: tests first (RED → GREEN → REFACTOR), 80% coverage minimum, 100% on `core/pipeline.py` + `core/manifest.py`.
- Files <=400 lines, single responsibility.
- Immutable domain types (frozen dataclasses).
- No mutation of inputs; return new values.
- Conventional commits.
- Never hardcode secrets; API key from config file (precedence: CLI flag → env var → project config → user config).
