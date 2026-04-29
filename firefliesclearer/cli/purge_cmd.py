"""`firefliesclearer purge` — delete archived meetings."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from firefliesclearer.application.archive_service import ArchiveService
from firefliesclearer.application.purge_service import PurgeOutcome, PurgeService
from firefliesclearer.cli import _common
from firefliesclearer.cli.app import app
from firefliesclearer.core.pipeline import PipelineMode

from .archive_cmd import _print_report_summary


@app.command()
def purge(
    selection: Path = typer.Option(  # noqa: B008
        ..., "--selection", exists=True, help="Path to selection JSON."
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt."),
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """Delete every `selected:true` meeting in the selection (verifies archive first)."""
    deps = _common.build_deps(config_override=config)

    meetings = ArchiveService._meetings_from_selection(selection)
    if not meetings:
        _common.console.print("[yellow]No selected meetings; nothing to do.[/yellow]")
        return

    if dry_run:
        report = asyncio.run(deps.pipeline.run(meetings, mode=PipelineMode.DRY_RUN))
        _print_report_summary(report.archived, report.failed, report.skipped, report.deleted, [])
        return

    threshold = deps.config.run.delete_confirmation_threshold
    if len(meetings) > threshold and not yes:
        confirm = typer.confirm(
            f"About to delete {len(meetings)} meetings from Fireflies. Continue?"
        )
        if not confirm:
            _common.console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(code=1)

    svc = PurgeService(pipeline=deps.pipeline, manifest=deps.manifest)
    outcomes = asyncio.run(_collect(svc, selection))
    if not outcomes:
        _common.console.print("[yellow]No selected meetings; nothing to do.[/yellow]")
        return
    _print_purge_outcomes(outcomes)


async def _collect(svc: PurgeService, selection: Path) -> list[PurgeOutcome]:
    return [o async for o in svc.purge_selection(selection)]


def _print_purge_outcomes(outcomes: list[PurgeOutcome]) -> None:
    deleted = sum(1 for o in outcomes if o.state.value == "deleted")
    failed = sum(1 for o in outcomes if o.state.value == "deleted_failed")
    skipped = sum(1 for o in outcomes if o.state.value not in ("deleted", "deleted_failed"))
    failures = [(o.meeting_id, o.error or o.state.value) for o in outcomes if o.error]
    _print_report_summary(0, failed, skipped, deleted, failures)
