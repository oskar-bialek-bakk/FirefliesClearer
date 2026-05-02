"""`firefliesclearer scan` — list candidates, write selection file."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import typer
from rich.table import Table

from firefliesclearer.application.scan_service import ScanFilters, ScanService
from firefliesclearer.cli import _common
from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app
from firefliesclearer.ports.meeting_repository import MeetingRepository


@app.command()
def scan(
    older_than_days: int | None = typer.Option(
        None, "--older-than-days", help="Match meetings older than N days."
    ),
    duration_below: float | None = typer.Option(
        None, "--duration-below", help="Match duration < N minutes."
    ),
    no_transcript: bool = typer.Option(
        False, "--no-transcript", help="Match meetings with no transcript."
    ),
    title_contains: list[str] | None = typer.Option(  # noqa: B008
        None, "--title-contains", help="Substring match on title."
    ),
    title_regex: str | None = typer.Option(None, "--title-regex", help="Regex match on title."),
    host_email: list[str] | None = typer.Option(  # noqa: B008
        None, "--host-email", help="Match host email (repeatable)."
    ),
    participants_below: int | None = typer.Option(
        None, "--participants-below", help="Match if participants < N."
    ),
    has_tag: list[str] | None = typer.Option(  # noqa: B008
        None, "--has-tag", help="Match if any of these tags present."
    ),
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """List meetings matching the given rules; writes a selection file."""
    deps = _common.build_deps(config_override=config)

    filters = ScanFilters(
        older_than_days=older_than_days,
        duration_below_minutes=duration_below,
        no_transcript=no_transcript,
        title_contains=title_contains or (),
        title_regex=title_regex,
        host_email=host_email or (),
        participants_below=participants_below,
        has_tag=has_tag or (),
    )

    if filters.is_empty():
        raise typer.BadParameter("Provide at least one filter.")

    svc = ScanService(repo=cast(MeetingRepository, deps.scan_repo), clock=deps.clock)

    result = asyncio.run(svc.scan(filters))

    table = Table(title=f"Candidates ({len(result.matches)})")
    for col in ("ID", "Date", "Title", "Dur", "Host", "Reasons"):
        table.add_column(col)
    for match in result.matches:
        m = match.meeting
        table.add_row(
            m.meeting_id,
            m.meeting_date.date().isoformat(),
            m.title[:60],
            f"{m.duration_minutes:.1f}",
            m.host_email,
            ", ".join(match.matched_rules),
        )
    console.print(table)

    selections_dir = deps.config.archive.root_dir / "selections"
    target = svc.write_selection_file(result, selections_dir)
    console.print(f"[green]Wrote selection:[/green] {target}")
