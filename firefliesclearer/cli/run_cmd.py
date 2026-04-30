"""`firefliesclearer run` — preset-driven auto path."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from firefliesclearer.application.preset_service import PresetNotFoundError, PresetService
from firefliesclearer.application.scan_service import ScanService
from firefliesclearer.cli import _common
from firefliesclearer.cli.app import app
from firefliesclearer.cli.archive_cmd import _print_report
from firefliesclearer.core.pipeline import PipelineMode
from firefliesclearer.infra.config import user_config_path
from firefliesclearer.web.wizard_session import filters_from_dict


@app.command()
def run(
    apply: bool = typer.Option(False, "--apply", help="Actually mutate (default: dry-run)."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt above threshold."),
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
    preset_name: str | None = typer.Option(
        None, "--preset", help="Named preset to use (omit to use the default preset)."
    ),
) -> None:
    """Apply a preset's filters (age, transcript, etc.)."""
    user_path = config or user_config_path()
    svc = PresetService(user_path)

    if preset_name is not None:
        try:
            preset = svc.get(preset_name)
        except PresetNotFoundError:
            _common.console.print(
                f"[red]Preset {preset_name!r} not found.[/red] "
                "Use `firefliesclearer presets` to list available presets."
            )
            raise typer.Exit(code=1) from None
    else:
        maybe_preset = svc.get_default()
        if maybe_preset is None:
            _common.console.print(
                "[red]No default preset configured.[/red] "
                "Pass --preset NAME or create one in the UI."
            )
            raise typer.Exit(code=1)
        preset = maybe_preset

    filters = filters_from_dict(preset.filters.model_dump())

    deps = _common.build_deps(config_override=config)
    scan_svc = ScanService(repo=deps.client, clock=deps.clock)

    scan_result = asyncio.run(scan_svc.scan(filters))
    matched = [m.meeting for m in scan_result.matches]

    if not matched:
        _common.console.print("[green]Nothing matched.[/green]")
        return

    _common.console.print(
        f"[bold]{len(matched)}[/bold] meetings match preset [bold]{preset.name!r}[/bold]."
    )
    if not apply:
        _common.console.print("[yellow]Dry-run; pass --apply to mutate.[/yellow]")
        return

    threshold = deps.config.run.delete_confirmation_threshold
    if (
        len(matched) > threshold
        and not yes
        and not typer.confirm(f"About to archive+delete {len(matched)} meetings. Continue?")
    ):
        _common.console.print("[yellow]Aborted.[/yellow]")
        raise typer.Exit(code=1)

    report = asyncio.run(deps.pipeline.run(matched, mode=PipelineMode.APPLY))
    _print_report(report)
