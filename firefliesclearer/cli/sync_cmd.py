"""`firefliesclearer sync` — run a one-shot sync from the CLI."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from firefliesclearer.application.sync_service import (
    SyncMode,
    SyncService,
    SyncTrigger,
)
from firefliesclearer.cli import _common
from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app


@app.command()
def sync(
    full: bool = typer.Option(False, "--full", help="Run full reconciliation."),
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip the API call; print the plan."),
) -> None:
    """Run a sync now from the command line.

    Useful for cron jobs that don't want to keep ``serve`` running. ``--full``
    triggers reconciliation mode (detects gone-from-source meetings as well
    as new/updated); the default is incremental (new meetings only).
    """
    deps = _common.build_deps(config_override=config)
    mode = SyncMode.FULL if full else SyncMode.INCREMENTAL

    if dry_run:
        # Escape brackets so Rich does not interpret "[full]" as markup.
        console.print(f"Would run \\[{mode.value}] sync.")
        return

    service = SyncService(repo=deps.client, manifest=deps.manifest, clock=deps.clock)
    outcome = asyncio.run(service.run(mode=mode, trigger=SyncTrigger.MANUAL_SETTINGS))
    console.print(
        f"Sync {outcome.outcome}: "
        f"{outcome.meetings_seen} seen, "
        f"{outcome.meetings_added} added, "
        f"{outcome.meetings_updated} updated, "
        f"{outcome.meetings_gone} gone."
    )
    if outcome.outcome != "success":
        raise typer.Exit(code=1)
