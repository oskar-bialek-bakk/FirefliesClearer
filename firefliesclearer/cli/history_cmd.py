"""`firefliesclearer history` — audit query."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.table import Table

from firefliesclearer.application.audit_service import AuditService, HistoryFilter
from firefliesclearer.cli import _common
from firefliesclearer.cli.app import app
from firefliesclearer.core.models import MeetingState


@app.command()
def history(
    month: str = typer.Option(..., "--month", help="Year-Month, e.g. 2026-04."),
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """List meetings deleted in the given month (audit)."""
    deps = _common.build_deps(config_override=config)

    try:
        year_str, month_str = month.split("-")
        year, mnum = int(year_str), int(month_str)
    except ValueError as e:
        raise typer.BadParameter(f"Invalid --month '{month}'. Use YYYY-MM.") from e

    date_from = datetime(year, mnum, 1, tzinfo=UTC)
    next_year, next_month = (year, mnum + 1) if mnum < 12 else (year + 1, 1)
    date_to = datetime(next_year, next_month, 1, tzinfo=UTC)

    filt = HistoryFilter(
        states=(MeetingState.DELETED,),
        date_from=date_from,
        date_to=date_to,
        limit=1000,
        offset=0,
    )
    svc = AuditService(manifest=deps.manifest)
    entries = svc.history(filt)

    table = Table(title=f"Deleted in {year:04d}-{mnum:02d}")
    for col in ("ID", "Title", "Meeting date", "Deleted at", "Archive path"):
        table.add_column(col)
    for entry in entries:
        table.add_row(
            entry.meeting_id,
            entry.title[:60],
            entry.meeting_date.date().isoformat(),
            entry.deleted_at.isoformat() if entry.deleted_at else "",
            entry.archive_path or "",
        )
    _common.console.print(table)
