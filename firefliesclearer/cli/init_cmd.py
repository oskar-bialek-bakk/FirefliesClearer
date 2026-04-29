"""`firefliesclearer init` — interactive first-run config."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from firefliesclearer.application.setup_service import InvalidApiKey, SetupService, SetupValues
from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app
from firefliesclearer.infra.config import user_config_path
from firefliesclearer.infra.fireflies_client import FirefliesClient


def _make_fireflies_factory(
    endpoint: str = "https://api.fireflies.ai/graphql",
) -> object:
    """Return a repo_factory that constructs a real FirefliesClient per key."""

    def factory(api_key: str) -> FirefliesClient:
        return FirefliesClient(api_key=api_key, endpoint=endpoint)

    return factory


@app.command()
def init(
    config: Path | None = typer.Option(  # noqa: B008
        None, "--config", help="Override config file path."
    ),
    no_ping: bool = typer.Option(False, "--no-ping", help="Skip API connectivity check."),
) -> None:
    """Set up FirefliesClearer for the first time."""
    target = config or user_config_path()
    api_key = typer.prompt("Fireflies API key", hide_input=True)
    default_root = str(Path.home() / "Documents" / "firefliesclearer-archive")
    root_str = typer.prompt("Archive root directory", default=default_root)
    older_than = typer.prompt("Auto-path: delete meetings older than N days", default=180, type=int)
    # delete_failed_transcripts is currently always True; v1 prompt is vestigial — Phase 3 removes init entirely.
    delete_failed = typer.confirm(  # noqa: F841
        "Auto-path: delete meetings with failed transcripts?", default=True
    )

    values = SetupValues(
        api_key=api_key,
        archive_root=Path(root_str),
        default_age_days=older_than,  # v1's prompt becomes default_age_days
        concurrency=3,  # v1 didn't ask; default
    )

    svc = SetupService(repo_factory=_make_fireflies_factory())  # type: ignore[arg-type]

    if not no_ping:
        try:
            email = asyncio.run(svc.verify_api_key(api_key))
            console.print(f"[green]API key verified:[/green] {email}")
        except InvalidApiKey as exc:
            console.print(f"[red]Invalid API key:[/red] {exc}")
            raise typer.Exit(code=1) from exc
    else:
        console.print("[dim]Skipping connectivity check (--no-ping).[/dim]")

    svc.write_config(target, values)
    console.print(f"[green]Config written:[/green] {target}")
