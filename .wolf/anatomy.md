# anatomy.md

> Auto-maintained by OpenWolf. Last scanned: 2026-09-29T09:15:38.586Z
> Files: 221 tracked | Anatomy hits: 0 | Misses: 0

> Project structure index. Auto-maintained by OpenWolf hooks and daemon.
> Run `openwolf scan` to generate, or wait for the first Claude Code session.
> Status: Pending initial scan

## ./

- `.ffc-serve.err` (~0 tok)
- `.gitignore` — Git ignore rules (~272 tok)
- `.pre-commit-config.yaml` (~44 tok)
- `.python-version` (~2 tok)
- `AGENTS.md` — OpenWolf (~75 tok)
- `CHANGELOG.md` — Change log (~1329 tok)
- `CLAUDE.md` — OpenWolf (~1876 tok)
- `pyproject.toml` — Python project configuration (~2192 tok)
- `README.md` — Project documentation (~1625 tok)
- `tailwind.config.js` — Tailwind CSS configuration (~38 tok)

## .github/workflows/

- `ci.yml` — CI: CI (~267 tok)
- `scheduled-smoke.yml` — CI: Scheduled smoke (~731 tok)

## docs/superpowers/plans/

- `2026-04-28-firefliesclearer.md` — FirefliesClearer Implementation Plan (~37100 tok)
- `2026-04-29-firefliesclearer-v2-web-ui.md` — FirefliesClearer v2 — Web UI Implementation Plan (~32607 tok)
- `2026-05-02-local-cache-phase-1-schema.md` — Local-cache Phase 1: Schema + State Machine — Implementation Plan (~15533 tok)
- `2026-05-02-local-cache-phase-2-sync-engine.md` — Local-cache Phase 2: Sync Engine + Scheduler — Implementation Plan (~17807 tok)
- `2026-05-02-local-cache-phase-3-read-flip.md` — Local-cache Phase 3: Read-Path Flip — Implementation Plan (~5369 tok)
- `2026-05-02-local-cache-phase-4-trigger-ui.md` — Local-cache Phase 4: Trigger UI — Implementation Plan (~8055 tok)
- `2026-05-02-local-cache-phase-5-bootstrap-ux.md` — Local-cache Phase 5: Bootstrap UX — Implementation Plan (~5697 tok)
- `2026-05-02-local-cache-phase-6-default-on.md` — Local-cache Phase 6: Default-On + Cleanup — Implementation Plan (~5577 tok)
- `2026-05-06-trash-classification.md` — Trash classification — Implementation Plan (~24205 tok)

## docs/superpowers/specs/

- `2026-04-28-firefliesclearer-design.md` — FirefliesClearer — Design Spec (~5481 tok)
- `2026-04-29-firefliesclearer-v2-web-ui-design.md` — FirefliesClearer v2 — Web UI Design Spec (~14902 tok)
- `2026-05-02-local-cache-design.md` — Local-cache architecture — validated design (~8598 tok)
- `2026-05-06-trash-classification-design.md` — Trash classification — validated design (~3718 tok)
- `v2-release-smoke.md` — v2 release smoke checklist (~891 tok)

## firefliesclearer/

- `__init__.py` — FirefliesClearer — safe Fireflies AI meeting archiver and cleaner. (~28 tok)

## firefliesclearer/application/

- `__init__.py` — Application services — shared orchestration consumed by both CLI and web layers. (~81 tok)
- `archive_service.py` — ArchiveService — runs the archive half of the pipeline for selected meetings. (~1289 tok)
- `audit_service.py` — AuditService — read-only audit queries over the manifest for CLI and web UI. (~1614 tok)
- `exceptions.py` — Shared application-layer exceptions for FirefliesClearer. (~58 tok)
- `preset_service.py` — PresetService — CRUD for named presets stored in user config TOML. (~2554 tok)
- `purge_service.py` — PurgeService — runs the purge half of the pipeline for selected meetings. (~969 tok)
- `scan_service.py` — ScanService — shared scan orchestration for CLI and web UI. (~2205 tok)
- `setup_service.py` — SetupService — shared first-run configuration logic for CLI and web UI. (~2952 tok)
- `sync_service.py` — SyncService — pulls meetings from the live API into the local cache. (~5228 tok)

## firefliesclearer/cli/

- `__init__.py` (~0 tok)
- `_common.py` — Shared CLI helpers: load config, build dependencies. (~724 tok)
- `app.py` — Top-level Typer app. (~384 tok)
- `archive_cmd.py` — `firefliesclearer archive` — archive selected meetings. (~974 tok)
- `history_cmd.py` — `firefliesclearer history` — audit query. (~530 tok)
- `init_cmd.py` — Deprecated stub — `init` was replaced by the v2 web setup wizard. (~149 tok)
- `purge_cmd.py` — `firefliesclearer purge` — delete archived meetings. (~729 tok)
- `run_cmd.py` — `firefliesclearer run` — preset-driven auto path. (~876 tok)
- `scan_cmd.py` — `firefliesclearer scan` — list candidates, write selection file. (~862 tok)
- `serve_cmd.py` — `firefliesclearer serve` — launch the local web UI. (~2100 tok)
- `status_cmd.py` — `firefliesclearer status` — manifest summary. (~324 tok)
- `sync_cmd.py` — `firefliesclearer sync` — run a one-shot sync from the CLI. (~491 tok)

## firefliesclearer/core/

- `__init__.py` (~0 tok)
- `archiver.py` — Archiver: atomic per-meeting writes, verification, drift detection. (~1353 tok)
- `manifest.py` — SQLite-backed state machine and audit log. (~9175 tok)
- `models.py` — Domain types: immutable, no I/O. (~336 tok)
- `pipeline.py` — Per-meeting transactional pipeline: list -> archive -> verify -> delete. (~3131 tok)
- `rules.py` — Selection rule predicates and engine. Pure functions, no I/O. (~1022 tok)
- `sync_types.py` — Pure value types shared between the sync engine and its scheduler. (~161 tok)

## firefliesclearer/infra/

- `__init__.py` (~0 tok)
- `atomic_toml.py` — Atomic TOML write helper: temp-file + fsync + rename pattern. (~371 tok)
- `config.py` — Config: TOML loader, precedence chain, Pydantic validation. (~2051 tok)
- `fireflies_client.py` — Async GraphQL client for Fireflies AI. (~7972 tok)
- `fs.py` — Filesystem helpers: slug, canonical paths, atomic writes, hashing. (~622 tok)
- `log_retention.py` — Log retention sweep: delete *.log files older than N days. (~318 tok)
- `logging.py` — Structured JSON-lines logging with daily rotation and key redaction. (~831 tok)
- `manifest_backed_repo.py` — Read-only MeetingRepository backed by the local Manifest cache. (~465 tok)
- `open_folder.py` — Cross-platform helper to open a folder in the system file explorer. (~253 tok)
- `pdf_renderer.py` — reportlab-based PDF renderer for meeting summaries. (~2746 tok)
- `purge_scheduler.py` — Background API-purge trickle scheduler. (~2816 tok)
- `sync_scheduler.py` — Sync scheduler — decides when and what mode to run, then drives SyncService. (~2981 tok)
- `system_clock.py` — Production Clock implementation. (~57 tok)

## firefliesclearer/ports/

- `__init__.py` (~0 tok)
- `clock.py` — Clock port: testable time source. (~56 tok)
- `meeting_repository.py` — Meeting repository port: read meetings, fetch artifacts, delete. (~289 tok)
- `summary_renderer.py` — Summary renderer port: produces PDF bytes from a summary payload. (~125 tok)

## firefliesclearer/web/

- `__init__.py` — Web UI — FastAPI + HTMX presentation layer (v2). (~89 tok)
- `app.py` — FastAPI app factory. (~1251 tok)
- `deps.py` — FastAPI Depends() providers — request-scoped lookups for app.state services. (~2405 tok)
- `lifecycle.py` — Browser-driven server lifecycle: heartbeat + graceful shutdown. (~755 tok)
- `lockfile.py` — Single-instance enforcement via a platform-appropriate lockfile. (~1187 tok)
- `operations.py` — In-memory registry for long-running archive/purge operations. (~2401 tok)
- `proactor_fix.py` — Windows ProactorEventLoop cosmetic-error suppression. (~644 tok)
- `security.py` — Session token + CSRF protection for the local web UI. (~2658 tok)
- `sessions.py` — In-process session store keyed by the session cookie value. (~282 tok)
- `tailwind.input.css` — Styles: 4 rules, 1 layers (~136 tok)
- `wizard_session.py` — Cleanup-wizard session-state helpers. (~3955 tok)

## firefliesclearer/web/routes/

- `__init__.py` (~0 tok)
- `_heartbeat.py` — POST /_alive — keepalive ping from the browser. (~128 tok)
- `_quit.py` — POST /_quit — explicit shutdown request from the sidebar. (~144 tok)
- `cleanup.py` — Cleanup wizard routes. (~22117 tok)
- `dashboard.py` — Dashboard route + sidebar status fragment + single-meeting retry. (~7948 tok)
- `history.py` — History route — audit log with filters and pagination. (~2382 tok)
- `presets.py` — Presets CRUD routes — /presets (list, new, edit, delete). (~2023 tok)
- `progress.py` — SSE progress endpoint for long-running operations. (~612 tok)
- `settings.py` — Settings page — /settings with 6 collapsible sections. (~4168 tok)
- `setup.py` — Python package setup (~1872 tok)
- `sync.py` — Sync routes — manual trigger endpoint and status polling endpoint. (~3975 tok)

## firefliesclearer/web/static/

- `app.js` — firefliesclearer/web/static/app.js (~1415 tok)
- `styles.css` — Styles: 22 rules, 69 vars (~16800 tok)

## firefliesclearer/web/templates/

- `_log_viewer.html` (~95 tok)
- `base.html` — {% block title %}FirefliesClearer{% endblock %} (~1033 tok)
- `dashboard.html` (~135 tok)
- `history_panel.html` (~340 tok)
- `history.html` (~1592 tok)
- `settings.html` (~2419 tok)

## firefliesclearer/web/templates/cleanup/

- `_archive_meeting_row.html` (~366 tok)
- `_preview_count.html` (~47 tok)
- `_progress_sse.html` — values: updateRow, updateProgress (~1376 tok)
- `_purge_meeting_row.html` (~353 tok)
- `_review_continue.html` (~320 tok)
- `_review_row.html` (~780 tok)
- `_review_side_panel.html` (~405 tok)
- `_review_table.html` (~834 tok)
- `_review_toolbar.html` (~939 tok)
- `_stepper.html` (~200 tok)
- `step1_filter.html` (~2212 tok)
- `step2_review.html` (~311 tok)
- `step3_archive_done.html` (~552 tok)
- `step3_archive_in_progress.html` (~518 tok)
- `step3_archive_preflight.html` (~301 tok)
- `step3a_trash_confirm.html` (~543 tok)
- `step4_mark_deleted_done.html` (~402 tok)
- `step4_purge_done.html` (~370 tok)
- `step4_purge_in_progress.html` (~452 tok)
- `step4_purge_preflight.html` (~1133 tok)

## firefliesclearer/web/templates/partials/

- `_dashboard_main.html` (~191 tok)
- `_retry_all_progress.html` (~365 tok)
- `_retry_history_row.html` (~374 tok)
- `_retry_progress.html` (~410 tok)
- `_sync_banner.html` (~1166 tok)
- `_sync_opt_in_banner.html` (~238 tok)
- `awaiting_external_deletion.html` (~791 tok)
- `last_activity.html` (~98 tok)
- `needs_attention.html` (~1088 tok)
- `sidebar_status.html` (~159 tok)
- `state_counts.html` (~721 tok)

## firefliesclearer/web/templates/presets/

- `_filter_fieldsets.html` (~1392 tok)
- `edit.html` (~532 tok)
- `list.html` (~767 tok)
- `new.html` (~534 tok)

## firefliesclearer/web/templates/setup/

- `api_key.html` (~272 tok)
- `archive_root.html` (~274 tok)
- `defaults.html` (~256 tok)
- `welcome.html` (~158 tok)

## scripts/

- `probe_fireflies.py` — One-shot Fireflies API probe. (~1008 tok)
- `scheduled_smoke.py` — Weekly destructive smoke test against the live Fireflies account. (~3963 tok)

## tests/

- `__init__.py` (~0 tok)
- `conftest.py` — Shared pytest fixtures. (~69 tok)

## tests/application/

- `__init__.py` (~0 tok)
- `test_archive_service.py` — Tests for ArchiveService — selection-file and direct-meeting-iterable paths. (~2491 tok)
- `test_audit_service.py` — Tests for AuditService — read-only audit queries over the manifest. (~2712 tok)
- `test_preset_service.py` — Tests for PresetService — CRUD with atomic writes, TDD order. (~4274 tok)
- `test_purge_service.py` — Tests for PurgeService — selection-file and direct-meeting-iterable paths. (~2562 tok)
- `test_scan_service.py` — Tests for ScanService — scan() and write_selection_file(). (~2296 tok)
- `test_setup_service.py` — Tests for SetupService — verify_api_key + atomic write_config + migrate_v1_rules_auto. (~4617 tok)
- `test_sync_service.py` — Tests for SyncService — incremental + full reconciliation algorithms. (~7304 tok)

## tests/cli/

- `__init__.py` (~0 tok)
- `test_app.py` — Tests for top-level Typer app. (~210 tok)
- `test_archive_purge_cmd.py` — Tests for `archive` and `purge` curated-path commands. (~1345 tok)
- `test_init_cmd.py` — Tests for the deprecated `init` command stub. (~94 tok)
- `test_run_cmd.py` — Tests for `firefliesclearer run` — preset-driven auto path. (~3201 tok)
- `test_scan_cmd.py` — Tests for `firefliesclearer scan`. (~952 tok)
- `test_serve_cmd.py` — Smoke test for `firefliesclearer serve` — argv parsing only. (~1745 tok)
- `test_status_history_cmd.py` — Tests for `status` and `history`. (~824 tok)
- `test_sync_cmd.py` — Tests for `firefliesclearer sync` CLI command. (~367 tok)

## tests/core/

- `__init__.py` (~0 tok)
- `test_archiver.py` — Tests for Archiver: atomic write, verification, drift detection. (~1431 tok)
- `test_manifest_fsm_trash.py` — FSM tests for the trash flow's KNOWN -> DELETED short-circuit. (~634 tok)
- `test_manifest.py` — Tests for the SQLite-backed manifest. (~10069 tok)
- `test_models.py` — Tests for domain models. (~490 tok)
- `test_pipeline.py` — Tests for Pipeline: per-meeting transaction, failure modes, idempotency. (~7060 tok)
- `test_rules.py` — Tests for selection rules. (~1230 tok)

## tests/fakes/

- `__init__.py` (~0 tok)
- `controllable_repository.py` — Test fake — paginated repository with rate-limit injection. (~621 tok)
- `fake_pipeline.py` — FakePipeline — drop-in test double for ``firefliesclearer.core.pipeline.Pipeline``. (~960 tok)
- `fake_renderer.py` — FakeSummaryRenderer returns deterministic bytes. (~227 tok)
- `frozen_clock.py` — FrozenClock for deterministic tests. (~202 tok)
- `in_memory_repository.py` — In-memory MeetingRepository for tests. (~653 tok)

## tests/infra/

- `__init__.py` (~0 tok)
- `test_config.py` — Tests for config: schema validation and precedence chain. (~2850 tok)
- `test_fireflies_client.py` — Tests for the Fireflies GraphQL client (using respx to mock httpx). (~11847 tok)
- `test_fs.py` — Tests for filesystem helpers (slug, paths, atomic write, hashing). (~593 tok)
- `test_log_retention.py` — Tests for infra/log_retention.py. (~644 tok)
- `test_logging.py` — Tests for structured JSON logging with API-key redaction. (~719 tok)
- `test_manifest_backed_repo.py` — Tests for ManifestBackedRepository — read-only Manifest -> MeetingRepository adapter. (~824 tok)
- `test_open_folder.py` — Tests for infra/open_folder.py. (~540 tok)
- `test_pdf_renderer.py` — Tests for the reportlab-based PDF renderer. (~416 tok)
- `test_purge_scheduler.py` — Tests for purge_scheduler — the daily API-purge trickle. (~3194 tok)
- `test_sync_scheduler.py` — Tests for sync_scheduler decision logic (compute_next, decide_mode). (~3652 tok)

## tests/tools/

- `__init__.py` (~0 tok)
- `test_build_static_script.py` — Tests for build tooling: Tailwind compile script and Lucide icon vendoring. (~1674 tok)

## tests/web/

- `__init__.py` (~0 tok)
- `conftest.py` — _TrackingRepo: web_token, app, client, archive_root + 6 more (~1660 tok)
- `test_app.py` — Tests for app-level state initialisation. (~138 tok)
- `test_deps.py` — Tests for ``firefliesclearer.web.deps.get_deps`` — lazy build + error branches. (~2355 tok)
- `test_lifecycle.py` — Tests for HeartbeatTracker and the shutdown coordinator. (~1487 tok)
- `test_lockfile.py` — Tests for the single-instance lockfile. (~579 tok)
- `test_operations.py` — Tests for the in-memory OperationRegistry. (~2471 tok)
- `test_proactor_fix.py` — Tests for the Windows ProactorEventLoop cosmetic-error filter. (~866 tok)
- `test_security.py` — Tests for session-token + CSRF middleware. (~2556 tok)
- `test_sessions.py` — Tests for the in-process server-side session store. (~298 tok)
- `test_wizard_session.py` — Unit tests for ``firefliesclearer.web.wizard_session``. (~3690 tok)

## tests/web/e2e/

- `__init__.py` (~0 tok)
- `test_full_run.py` — End-to-end full-run test for the v2 web UI. (~2680 tok)

## tests/web/routes/

- `__init__.py` (~0 tok)
- `test_cleanup_step1_presets.py` — Tests for preset integration in cleanup wizard Step 1 (Task 6.4). (~1604 tok)
- `test_cleanup_step1_trash_preset.py` — Step 1 — second preset picker for trash classification. (~1100 tok)
- `test_cleanup_step1.py` — Tests for cleanup wizard Step 1 — filter form, live preview-count, submit. (~3113 tok)
- `test_cleanup_step2_archive_toggle.py` — Step 2 — Archive toggle, trash classifier auto-fill, bulk actions. (~3356 tok)
- `test_cleanup_step2.py` — Tests for cleanup wizard Step 2 — Review (table + selection + side panel). (~10567 tok)
- `test_cleanup_step3.py` — Tests for cleanup wizard Step 3 — Archive (preflight, start, in-progress, done, cancel). (~9704 tok)
- `test_cleanup_step3a.py` — Step 3a — Trash confirmation (typed-count gate, demote, non-host auto-mark). (~3534 tok)
- `test_cleanup_step4.py` — Tests for cleanup wizard Step 4 — Purge (preflight, start, in-progress, done). (~12853 tok)
- `test_dashboard.py` — Tests for the Dashboard route + sidebar status fragment. (~6968 tok)
- `test_heartbeat.py` — Tests for POST /_alive and POST /_quit routes. (~448 tok)
- `test_history_trash_filter.py` — History page — Archived / Trash / All filter chip + no-archive badge. (~2481 tok)
- `test_history.py` — Tests for GET /history — filters, pagination, URL round-trip. (~5895 tok)
- `test_presets.py` — Tests for the /presets CRUD UI (Task 6.4). (~2248 tok)
- `test_progress_sse.py` — Tests for the SSE progress endpoint at GET /api/operations/{op_id}/events. (~3857 tok)
- `test_retry.py` — Tests for POST /retry/{meeting_id} — single-meeting retry from the Dashboard. (~8690 tok)
- `test_settings.py` — Tests for the /settings page (Phase 8 — Task 31). (~6262 tok)
- `test_setup.py` — Tests for the first-run setup wizard. (~3232 tok)
- `test_sync.py` — Tests for /sync/now and /sync/status endpoints. (~4838 tok)

## tools/

- `build_static.sh` (~368 tok)
