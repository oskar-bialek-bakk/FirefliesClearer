"""`firefliesclearer archive` — archive selected meetings."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import typer

from firefliesclearer.cli import _common
from firefliesclearer.cli.app import app
from firefliesclearer.core.models import Meeting
from firefliesclearer.core.pipeline import PipelineMode, RunReport


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
    meetings = _load_selected(selection)
    if not meetings:
        _common.console.print("[yellow]No selected meetings; nothing to do.[/yellow]")
        return
    mode = PipelineMode.DRY_RUN if dry_run else PipelineMode.ARCHIVE_ONLY
    report = asyncio.run(deps.pipeline.run(meetings, mode=mode))
    _print_report(report)


def _load_selected(selection: Path) -> list[Meeting]:
    payload = json.loads(selection.read_text(encoding="utf-8"))
    out: list[Meeting] = []
    for entry in payload["meetings"]:
        if not entry.get("selected", True):
            continue
        out.append(
            Meeting(
                meeting_id=entry["id"],
                title=entry["title"],
                meeting_date=datetime.fromisoformat(entry["date"]),
                duration_minutes=float(entry.get("duration_min", 0.0)),
                host_email=entry.get("host", ""),
                participant_count=int(entry.get("participants", 0)),
                tags=tuple(entry.get("tags", [])),
                has_transcript=True,
            )
        )
    return out


def _print_report(report: RunReport) -> None:
    _common.console.print(
        f"[green]archived={report.archived}[/green] "
        f"[red]failed={report.failed}[/red] "
        f"skipped={report.skipped} deleted={report.deleted}"
    )
    for mid, err in report.failures[:20]:
        _common.console.print(f"  [red]{mid}[/red]: {err}")
