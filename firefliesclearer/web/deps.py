"""FastAPI Depends() providers — request-scoped lookups for app.state services."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, Request

from firefliesclearer.core.archiver import Archiver
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.pipeline import Pipeline
from firefliesclearer.infra.atomic_toml import write_atomic_toml
from firefliesclearer.infra.config import load_config
from firefliesclearer.infra.pdf_renderer import ReportlabSummaryRenderer
from firefliesclearer.infra.system_clock import SystemClock
from firefliesclearer.web.lifecycle import HeartbeatTracker, ShutdownCoordinator

logger = logging.getLogger(__name__)


async def _resolve_and_persist_user_email(client: Any, config_path: Path) -> str | None:
    """Resolve the authenticated user's email and persist it to config.

    Used by :func:`get_deps` to bridge pre-Phase-6 configs that lack
    ``[fireflies] user_email`` — calls Fireflies' ``user`` query once and
    writes the answer back atomically so future deps builds skip the API
    hop. Returns ``None`` (with a logged warning) on any failure: the
    pipeline will then fall back to "always attempt API delete", which is
    the safe pre-Phase-6 behaviour.
    """
    try:
        email_raw: Any = await client.ping_user()
    except Exception:
        logger.warning(
            "Could not resolve user_email from Fireflies (will skip non-host "
            "auto-mark until next deps build).",
            exc_info=True,
        )
        return None
    email: str | None = email_raw if isinstance(email_raw, str) and email_raw else None
    if email is None:
        return None
    try:
        with open(config_path, "rb") as f:
            payload: dict[str, Any] = tomllib.load(f)
        payload.setdefault("fireflies", {})["user_email"] = email
        write_atomic_toml(config_path, payload)
    except Exception:
        # Persistence is a nice-to-have; if it fails (read-only mount,
        # malformed TOML), we still hand the resolved email back so this
        # process uses it for non-host auto-mark.
        logger.warning(
            "Resolved user_email=%s but failed to persist to %s",
            email,
            config_path,
            exc_info=True,
        )
    return email


def get_tracker(request: Request) -> HeartbeatTracker:
    tracker: HeartbeatTracker = request.app.state.tracker
    return tracker


def get_shutdown_coordinator(request: Request) -> ShutdownCoordinator:
    coord: ShutdownCoordinator = request.app.state.shutdown_coordinator
    return coord


async def get_deps(request: Request) -> SimpleNamespace:
    """Return application deps, building them lazily after first-run setup.

    The serve command attaches deps eagerly when the config exists at boot.
    When the wizard writes config mid-process, ``app.state.deps`` starts out
    ``None``; this provider builds the deps the first time a route asks for
    them and caches them on ``app.state.deps`` for subsequent calls.

    Declared ``async`` so the lazy build runs on the event-loop thread,
    matching every async route handler that consumes the result. A sync
    provider would dispatch to anyio's threadpool, pinning the manifest's
    SQLite connection to a worker thread and breaking subsequent route calls.
    """
    deps = getattr(request.app.state, "deps", None)
    if deps is not None:
        return deps  # type: ignore[no-any-return]

    config_path = request.app.state.config_path
    if config_path is None or not config_path.exists():
        raise HTTPException(
            status_code=500,
            detail="Application is not configured; cannot build deps.",
        )
    repo_factory = request.app.state.repo_factory
    if repo_factory is None:
        raise HTTPException(
            status_code=500,
            detail="Server is missing a repo_factory; cannot build deps.",
        )

    config = load_config(user_config=config_path)
    archive_root = config.archive.root_dir
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest.open(archive_root / "manifest.db")
    client = repo_factory(config.fireflies.api_key)
    clock = SystemClock()
    archiver = Archiver(archive_root=archive_root)
    renderer = ReportlabSummaryRenderer()
    # user_email lazy-resolve: existing configs (pre-Phase-6) don't have
    # ``[fireflies] user_email`` set; rather than force the user to re-run
    # setup, ping Fireflies once on first deps build and persist the result
    # to config. After that, every subsequent ``get_deps`` reads from
    # config without burning an API call.
    user_email = config.fireflies.user_email
    if user_email is None:
        user_email = await _resolve_and_persist_user_email(client, config_path)
    pipeline = Pipeline(
        repository=client,
        manifest=manifest,
        archiver=archiver,
        renderer=renderer,
        clock=clock,
        user_email=user_email,
    )
    # Phase 6: cache adapter is unconditional. The [sync] flag now only
    # controls whether the scheduler runs (see below). The read path is
    # always served by ManifestBackedRepository — when sync is disabled and
    # the cache is empty, the wizard sees an empty list, which is the user's
    # choice from dismissing the opt-in banner.
    from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository

    scan_repo = ManifestBackedRepository(manifest)
    deps = SimpleNamespace(
        config=config,
        manifest=manifest,
        client=client,
        clock=clock,
        pipeline=pipeline,
        scan_repo=scan_repo,
    )
    request.app.state.deps = deps

    # Post-setup-wizard scenario: when [sync] enabled = true and the
    # scheduler hasn't been kicked off yet (eager-deps path in serve_cmd
    # already starts it), start it now. The scheduler detects the empty
    # cache and triggers a bootstrap full sync on its first tick.
    if config.sync.enabled and getattr(request.app.state, "sync_scheduler_task", None) is None:
        import asyncio

        from firefliesclearer.application.sync_service import SyncService
        from firefliesclearer.infra.sync_scheduler import run_scheduler
        from firefliesclearer.web.routes.sync import make_scheduler_hooks

        snapshot_callback, on_run_started, on_run_finished = make_scheduler_hooks(request.app.state)
        sync_service = SyncService(
            repo=client,
            manifest=manifest,
            clock=clock,
            snapshot_callback=snapshot_callback,
        )
        request.app.state.sync_service = sync_service
        request.app.state.sync_shutdown_event = asyncio.Event()
        # Park task on app.state to keep it alive (avoid GC + RUF006).
        request.app.state.sync_scheduler_task = asyncio.create_task(
            run_scheduler(
                sync_service=sync_service,
                manifest=manifest,
                config=config.sync,
                clock=clock,
                shutdown_event=request.app.state.sync_shutdown_event,
                sync_lock=request.app.state.sync_lock,
                on_run_started=on_run_started,
                on_run_finished=on_run_finished,
            )
        )

    # Phase 4 — start purge trickle scheduler post-wizard if not yet running.
    # Same lazy-init pattern as sync above: serve_cmd kicks it off eagerly
    # when config exists at boot; this branch covers the wizard-finish path.
    if (
        config.run.api_purge_per_day > 0
        and getattr(request.app.state, "purge_scheduler_task", None) is None
    ):
        import asyncio

        from firefliesclearer.application.purge_service import PurgeService
        from firefliesclearer.infra.purge_scheduler import run_purge_scheduler

        purge_service = PurgeService(pipeline=pipeline, manifest=manifest)
        request.app.state.purge_shutdown_event = asyncio.Event()
        request.app.state.purge_scheduler_task = asyncio.create_task(
            run_purge_scheduler(
                purge_service=purge_service,
                manifest=manifest,
                config=config.run,
                clock=clock,
                shutdown_event=request.app.state.purge_shutdown_event,
            )
        )
    return deps
