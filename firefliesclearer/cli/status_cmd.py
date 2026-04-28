"""`firefliesclearer status` — manifest summary."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from firefliesclearer.cli import _common
from firefliesclearer.cli.app import app


@app.command()
def status(
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """Show counts per state and recent failures."""
    deps = _common.build_deps(config_override=config)
    counts = deps.manifest.counts_by_state()
    table = Table(title="Manifest state")
    table.add_column("State")
    table.add_column("Count", justify="right")
    for state, n in sorted(counts.items(), key=lambda kv: kv[0].value):
        table.add_row(state.value, str(n))
    _common.console.print(table)
