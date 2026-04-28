"""`firefliesclearer init` — interactive first-run config."""

from __future__ import annotations

from pathlib import Path

import typer

from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app
from firefliesclearer.infra.config import (
    AppConfig,
    user_config_path,
    write_config,
)


@app.command()
def init(
    config: Path = typer.Option(  # noqa: B008
        None, "--config", help="Override config file path."
    ),
    no_ping: bool = typer.Option(
        False, "--no-ping", help="Skip API connectivity check."
    ),
) -> None:
    """Set up FirefliesClearer for the first time."""
    target = config or user_config_path()
    api_key = typer.prompt("Fireflies API key", hide_input=True)
    default_root = str(Path.home() / "Documents" / "firefliesclearer-archive")
    root_str = typer.prompt(
        "Archive root directory", default=default_root
    )
    older_than = typer.prompt(
        "Auto-path: delete meetings older than N days", default=180, type=int
    )
    delete_failed = typer.confirm(
        "Auto-path: delete meetings with failed transcripts?", default=True
    )
    cfg = AppConfig.model_validate(
        {
            "fireflies": {"api_key": api_key},
            "archive": {
                "root_dir": str(Path(root_str)),
                "summary_format": "pdf",
            },
            "rules": {
                "auto": {
                    "older_than_days": older_than,
                    "delete_failed_transcripts": delete_failed,
                }
            },
        }
    )
    write_config(cfg, target)
    console.print(f"[green]Config written:[/green] {target}")
    if no_ping:
        return
    console.print("[dim]Skipping connectivity check (--no-ping).[/dim]")
