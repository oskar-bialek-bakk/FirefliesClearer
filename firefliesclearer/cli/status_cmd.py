"""`firefliesclearer status` — manifest summary."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from firefliesclearer.application.audit_service import AuditService
from firefliesclearer.cli import _common
from firefliesclearer.cli.app import app


@app.command()
def status(
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """Show counts per state and recent failures."""
    deps = _common.build_deps(config_override=config)
    svc = AuditService(deps.manifest)
    summary = svc.summary()

    table = Table(title="Manifest state")
    table.add_column("State")
    table.add_column("Count", justify="right")
    for state, n in sorted(summary.counts_by_state.items(), key=lambda kv: kv[0].value):
        table.add_row(state.value, str(n))
    _common.console.print(table)

    if summary.last_run_at is not None:
        _common.console.print(f"Last activity: {summary.last_run_at.isoformat()}")

    if summary.failed_meeting_ids:
        _common.console.print(f"Failed meetings: {len(summary.failed_meeting_ids)}")
