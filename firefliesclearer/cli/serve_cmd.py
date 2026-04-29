"""`firefliesclearer serve` — launch the local web UI."""

from __future__ import annotations

import secrets
import socket
import threading
import webbrowser
from pathlib import Path

import typer
import uvicorn

from firefliesclearer.cli import _common
from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app
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

    deps = _common.build_deps(config_override=config)

    chosen_port = port or _pick_free_port(host)
    session_token = secrets.token_urlsafe(24)
    url = f"http://{host}:{chosen_port}/?token={session_token}"

    fastapi_app = create_app(
        session_token=session_token,
        csrf_secret=secrets.token_urlsafe(32),
    )
    fastapi_app.state.deps = deps  # for routes that need config/services

    lockfile = LockFile(deps.config.archive.root_dir / ".serve.lock")
    try:
        with lockfile.acquire(url=url.split("?", 1)[0]):  # do not leak token to lockfile
            console.print(f"[green]→ FirefliesClearer running at[/green] {url}")
            if not no_open:
                threading.Timer(0.5, lambda: webbrowser.open(url)).start()

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
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, 0))
    port: int = s.getsockname()[1]
    s.close()
    return port
