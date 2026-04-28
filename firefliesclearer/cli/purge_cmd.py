"""`firefliesclearer purge` — delete archived meetings."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from firefliesclearer.cli import _common
from firefliesclearer.cli.app import app
from firefliesclearer.cli.archive_cmd import _load_selected, _print_report
from firefliesclearer.core.pipeline import PipelineMode


@app.command()
def purge(
    selection: Path = typer.Option(  # noqa: B008
        ..., "--selection", exists=True, help="Path to selection JSON."
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt."),
    config: Path = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """Delete every `selected:true` meeting in the selection (verifies archive first)."""
    deps = _common.build_deps(config_override=config)
    meetings = _load_selected(selection)
    if not meetings:
        _common.console.print("[yellow]No selected meetings; nothing to do.[/yellow]")
        return
    threshold = deps.config.run.delete_confirmation_threshold
    if not dry_run and len(meetings) > threshold and not yes:
        confirm = typer.confirm(
            f"About to delete {len(meetings)} meetings from Fireflies. Continue?"
        )
        if not confirm:
            _common.console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(code=1)
    mode = PipelineMode.DRY_RUN if dry_run else PipelineMode.PURGE_ONLY
    report = asyncio.run(deps.pipeline.run(meetings, mode=mode))
    _print_report(report)
