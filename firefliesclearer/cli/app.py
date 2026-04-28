"""Top-level Typer app."""

from __future__ import annotations

import typer

from firefliesclearer import __version__

app = typer.Typer(
    name="firefliesclearer",
    help="Safely archive and clean up Fireflies AI meetings.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"firefliesclearer {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """FirefliesClearer."""


# Side-effect imports register commands when each is implemented.
from firefliesclearer.cli import (  # noqa: E402,F401
    archive_cmd,
    history_cmd,
    init_cmd,
    purge_cmd,
    run_cmd,
    scan_cmd,
    status_cmd,
)
