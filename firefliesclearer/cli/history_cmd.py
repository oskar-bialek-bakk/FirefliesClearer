"""`firefliesclearer history` — audit query."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from firefliesclearer.cli import _common
from firefliesclearer.cli.app import app


@app.command()
def history(
    month: str = typer.Option(
        ..., "--month", help="Year-Month, e.g. 2026-04."
    ),
    config: Path = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """List meetings deleted in the given month (audit)."""
    deps = _common.build_deps(config_override=config)
    try:
        year_str, month_str = month.split("-")
        year, mnum = int(year_str), int(month_str)
    except ValueError as e:
        raise typer.BadParameter(
            f"Invalid --month '{month}'. Use YYYY-MM."
        ) from e

    records = deps.manifest.history(year=year, month=mnum)
    table = Table(title=f"Deleted in {year:04d}-{mnum:02d}")
    for col in ("ID", "Title", "Meeting date", "Deleted at", "Archive path"):
        table.add_column(col)
    for r in records:
        table.add_row(
            r.meeting_id,
            r.title[:60],
            r.meeting_date.date().isoformat(),
            r.deleted_at.isoformat() if r.deleted_at else "",
            r.archive_path or "",
        )
    _common.console.print(table)
