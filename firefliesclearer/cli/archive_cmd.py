"""`firefliesclearer archive` — archive selected meetings."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from firefliesclearer.application.archive_service import ArchiveOutcome, ArchiveService
from firefliesclearer.cli import _common
from firefliesclearer.cli.app import app
from firefliesclearer.core.models import Meeting
from firefliesclearer.core.pipeline import PipelineMode


@app.command()
def archive(
    selection: Path = typer.Option(  # noqa: B008
        ..., "--selection", exists=True, help="Path to selection JSON."
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """Archive every `selected:true` meeting in the selection file."""
    deps = _common.build_deps(config_override=config)

    if dry_run:
        # Preserve v1 dry-run behaviour exactly (no manifest/archive mutations).
        meetings = ArchiveService._meetings_from_selection(selection)
        if not meetings:
            _common.console.print("[yellow]No selected meetings; nothing to do.[/yellow]")
            return
        report = asyncio.run(deps.pipeline.run(meetings, mode=PipelineMode.DRY_RUN))
        _print_report_summary(report.archived, report.failed, report.skipped, report.deleted, [])
        return

    svc = ArchiveService(pipeline=deps.pipeline, manifest=deps.manifest)
    outcomes = asyncio.run(_collect(svc, selection))
    if not outcomes:
        _common.console.print("[yellow]No selected meetings; nothing to do.[/yellow]")
        return
    _print_outcomes(outcomes)


async def _collect(svc: ArchiveService, selection: Path) -> list[ArchiveOutcome]:
    return [o async for o in svc.archive_selection(selection)]


def _print_outcomes(outcomes: list[ArchiveOutcome]) -> None:
    archived = sum(1 for o in outcomes if o.state.value == "archived")
    failed = sum(1 for o in outcomes if o.state.value.startswith("failed_"))
    skipped = 0
    deleted = 0
    failures = [(o.meeting_id, o.error or o.state.value) for o in outcomes if o.error]
    _print_report_summary(archived, failed, skipped, deleted, failures)


def _print_report_summary(
    archived: int,
    failed: int,
    skipped: int,
    deleted: int,
    failures: list[tuple[str, str]],
) -> None:
    _common.console.print(
        f"[green]archived={archived}[/green] "
        f"[red]failed={failed}[/red] "
        f"skipped={skipped} deleted={deleted}"
    )
    for mid, err in failures[:20]:
        _common.console.print(f"  [red]{mid}[/red]: {err}")


# ---------------------------------------------------------------------------
# Backward-compat helpers (used by purge_cmd and kept for other callers)
# ---------------------------------------------------------------------------


def _load_selected(selection: Path) -> list[Meeting]:
    """Load selected meetings from *selection* JSON file (delegates to ArchiveService)."""
    return ArchiveService._meetings_from_selection(selection)


def _print_report(report: object) -> None:
    """Print a RunReport in the standard summary format."""
    _print_report_summary(
        archived=getattr(report, "archived", 0),
        failed=getattr(report, "failed", 0),
        skipped=getattr(report, "skipped", 0),
        deleted=getattr(report, "deleted", 0),
        failures=getattr(report, "failures", []),
    )
