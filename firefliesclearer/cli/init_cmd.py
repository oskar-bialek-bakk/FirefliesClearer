"""Deprecated stub — `init` was replaced by the v2 web setup wizard."""

from __future__ import annotations

import typer

from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app


@app.command()
def init() -> None:
    """[REMOVED in v2] Use `firefliesclearer serve` instead."""
    console.print(
        "[yellow]firefliesclearer init has been replaced by the web setup wizard.[/yellow]\n"
        "Run [bold]firefliesclearer serve[/bold] instead."
    )
    raise typer.Exit(code=0)
