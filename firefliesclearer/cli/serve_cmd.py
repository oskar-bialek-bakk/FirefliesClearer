"""`firefliesclearer serve` — launch the local web UI."""

from __future__ import annotations

import secrets
import socket
import threading
import webbrowser
from pathlib import Path

import typer
import uvicorn

from firefliesclearer.application.setup_service import migrate_v1_rules_auto
from firefliesclearer.cli import _common
from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app
from firefliesclearer.infra.config import user_config_path
from firefliesclearer.infra.fireflies_client import FirefliesClient
from firefliesclearer.infra.log_retention import sweep_old_logs
from firefliesclearer.web.app import create_app
from firefliesclearer.web.lockfile import AnotherInstanceRunningError, LockFile


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(0, "--port", help="0 = OS picks a free port"),
    no_open: bool = typer.Option(False, "--no-open"),
    i_know_what_im_doing: bool = typer.Option(
        False, "--i-know-what-im-doing", help="Required to bind a non-loopback host."
    ),
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """Launch the local web UI."""
    if host != "127.0.0.1" and not i_know_what_im_doing:
        console.print(
            "[red]Refusing to bind a non-loopback host without --i-know-what-im-doing.[/red]"
        )
        raise typer.Exit(code=2)

    # Resolve config path (override or default platform-specific user_config_path).
    config_path = config or user_config_path()

    # Always pass config_path + repo_factory; deps are loaded only if config exists.
    chosen_port = port or _pick_free_port(host)
    session_token = secrets.token_urlsafe(24)
    url = f"http://{host}:{chosen_port}/?token={session_token}"

    fastapi_app = create_app(
        session_token=session_token,
        csrf_secret=secrets.token_urlsafe(32),
        config_path=config_path,
        repo_factory=lambda key: FirefliesClient(api_key=key),
    )

    # If config exists, run the one-shot v1→v2 migration (no-op when already
    # migrated), sweep old logs, then load deps so they reflect the
    # post-migration state.
    if config_path.exists():
        migrate_v1_rules_auto(config_path)
        from firefliesclearer.infra.config import load_config

        _cfg = load_config(user_config=config_path)
        sweep_old_logs(_cfg.archive.root_dir, _cfg.run.log_retention_days)
        deps = _common.build_deps(config_override=config_path)
        fastapi_app.state.deps = deps

        # Phase 2: start sync scheduler when enabled (default false)
        if _cfg.sync.enabled:
            import asyncio

            from firefliesclearer.application.sync_service import SyncService
            from firefliesclearer.infra.sync_scheduler import run_scheduler
            from firefliesclearer.web.routes.sync import make_scheduler_hooks

            snapshot_callback, on_run_started, on_run_finished = make_scheduler_hooks(
                fastapi_app.state
            )
            sync_service = SyncService(
                repo=deps.client,
                manifest=deps.manifest,
                clock=deps.clock,
                snapshot_callback=snapshot_callback,
            )
            shutdown_event = asyncio.Event()
            fastapi_app.state.sync_shutdown_event = shutdown_event
            fastapi_app.state.sync_service = sync_service

            @fastapi_app.on_event("startup")
            async def _start_sync_scheduler() -> None:
                # Reference held on app.state so the task is not garbage-
                # collected mid-run (RUF006).
                fastapi_app.state.sync_scheduler_task = asyncio.create_task(
                    run_scheduler(
                        sync_service=sync_service,
                        manifest=deps.manifest,
                        config=_cfg.sync,
                        clock=deps.clock,
                        shutdown_event=shutdown_event,
                        sync_lock=fastapi_app.state.sync_lock,
                        on_run_started=on_run_started,
                        on_run_finished=on_run_finished,
                    )
                )

            @fastapi_app.on_event("shutdown")
            async def _stop_sync_scheduler() -> None:
                shutdown_event.set()

        # Phase 4: start the API-purge trickle scheduler.
        # Disabled by default for upgrades (api_purge_per_day defaults to 5
        # which IS enabled — but if the user has set it to 0 they opted out
        # entirely). This runs independently of the sync scheduler so users
        # who keep [sync] disabled still get the trickle benefit.
        if _cfg.run.api_purge_per_day > 0:
            import asyncio as _asyncio_purge

            from firefliesclearer.application.purge_service import PurgeService
            from firefliesclearer.infra.purge_scheduler import run_purge_scheduler

            purge_service = PurgeService(
                pipeline=deps.pipeline,
                manifest=deps.manifest,
            )
            purge_shutdown = _asyncio_purge.Event()
            fastapi_app.state.purge_shutdown_event = purge_shutdown

            @fastapi_app.on_event("startup")
            async def _start_purge_scheduler() -> None:
                fastapi_app.state.purge_scheduler_task = _asyncio_purge.create_task(
                    run_purge_scheduler(
                        purge_service=purge_service,
                        manifest=deps.manifest,
                        config=_cfg.run,
                        clock=deps.clock,
                        shutdown_event=purge_shutdown,
                    )
                )

            @fastapi_app.on_event("shutdown")
            async def _stop_purge_scheduler() -> None:
                purge_shutdown.set()
    else:
        fastapi_app.state.deps = None

    # Lockfile lives next to the config — independent of whether deps loaded.
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lockfile = LockFile(config_path.parent / ".serve.lock")
    try:
        with lockfile.acquire(url=url.split("?", 1)[0]):  # do not leak token to lockfile
            # Plain print() is unconditional; Rich's Console() can autodetect
            # the wrapper exe context as a non-tty and emit nothing, leaving
            # the user with no URL to navigate to.
            print(f"-> FirefliesClearer running at {url}", flush=True)
            if not no_open:
                timer = threading.Timer(0.5, lambda: webbrowser.open(url))
                timer.daemon = True
                timer.start()

            uconfig = uvicorn.Config(fastapi_app, host=host, port=chosen_port, log_level="warning")
            server = uvicorn.Server(uconfig)
            fastapi_app.state.shutdown_coordinator.on_shutdown_requested(
                lambda: setattr(server, "should_exit", True)
            )
            server.run()
    except AnotherInstanceRunningError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


def _pick_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        port: int = s.getsockname()[1]
        return port
