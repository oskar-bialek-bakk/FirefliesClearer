"""FastAPI Depends() providers — request-scoped lookups for app.state services."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException, Request

from firefliesclearer.core.archiver import Archiver
from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.pipeline import Pipeline
from firefliesclearer.infra.config import load_config
from firefliesclearer.infra.pdf_renderer import ReportlabSummaryRenderer
from firefliesclearer.infra.system_clock import SystemClock
from firefliesclearer.web.lifecycle import HeartbeatTracker, ShutdownCoordinator


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
    pipeline = Pipeline(
        repository=client,
        manifest=manifest,
        archiver=archiver,
        renderer=renderer,
        clock=clock,
    )
    if config.sync.enabled:
        from firefliesclearer.infra.manifest_backed_repo import ManifestBackedRepository

        scan_repo: object = ManifestBackedRepository(manifest)
    else:
        scan_repo = client
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

        sync_service = SyncService(repo=client, manifest=manifest, clock=clock)
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
            )
        )
    return deps
