"""`firefliesclearer run` — auto path."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import typer

from firefliesclearer.cli import _common
from firefliesclearer.cli.app import app
from firefliesclearer.cli.archive_cmd import _print_report
from firefliesclearer.core.models import Meeting
from firefliesclearer.core.pipeline import PipelineMode
from firefliesclearer.core.rules import NoTranscript, OlderThanDays, Rule, RuleEngine
from firefliesclearer.ports.meeting_repository import MeetingFilter


@app.command()
def run(
    apply: bool = typer.Option(False, "--apply", help="Actually mutate (default: dry-run)."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt above threshold."),
    config: Path | None = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """Apply hard rules from config (age + no-transcript)."""
    deps = _common.build_deps(config_override=config)
    auto = deps.config.auto_rules()
    matched: list[Meeting] = []
    now = datetime.now(tz=UTC)

    async def _collect() -> None:
        engine = RuleEngine(cast("list[Rule]", [OlderThanDays(auto.older_than_days)]))
        no_transcript = NoTranscript()
        async for m in deps.client.list_meetings(MeetingFilter()):
            age_match = engine.evaluate(m, now=now).matched
            no_t_match = auto.delete_failed_transcripts and no_transcript.matches(m, now=now)
            if age_match or no_t_match:
                matched.append(m)

    asyncio.run(_collect())

    if not matched:
        _common.console.print("[green]Nothing matched.[/green]")
        return

    _common.console.print(f"[bold]{len(matched)}[/bold] meetings match auto rules.")
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
