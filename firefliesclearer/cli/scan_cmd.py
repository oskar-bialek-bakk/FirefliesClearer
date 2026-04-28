"""`firefliesclearer scan` — list candidates, write selection file."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import typer
from rich.table import Table

from firefliesclearer.cli import _common
from firefliesclearer.cli._common import console
from firefliesclearer.cli.app import app
from firefliesclearer.core.models import Meeting
from firefliesclearer.core.rules import (
    DurationBelow,
    HasTag,
    HostEmail,
    OlderThanDays,
    ParticipantsBelow,
    Rule,
    RuleEngine,
    TitleContains,
    TitleRegex,
)
from firefliesclearer.ports.meeting_repository import MeetingFilter


@app.command()
def scan(
    older_than_days: int = typer.Option(
        None, "--older-than-days", help="Match meetings older than N days."
    ),
    duration_below: float = typer.Option(
        None, "--duration-below", help="Match duration < N minutes."
    ),
    title_contains: list[str] = typer.Option(  # noqa: B008
        None, "--title-contains", help="Substring match on title."
    ),
    title_regex: str = typer.Option(None, "--title-regex", help="Regex match on title."),
    host_email: list[str] = typer.Option(  # noqa: B008
        None, "--host-email", help="Match host email (repeatable)."
    ),
    participants_below: int = typer.Option(
        None, "--participants-below", help="Match if participants < N."
    ),
    has_tag: list[str] = typer.Option(  # noqa: B008
        None, "--has-tag", help="Match if any of these tags present."
    ),
    config: Path = typer.Option(None, "--config"),  # noqa: B008
) -> None:
    """List meetings matching the given rules; writes a selection file."""
    deps = _common.build_deps(config_override=config)
    # Mypy treats ClassVar `name` on rule dataclasses as incompatible with the
    # Rule Protocol's instance attribute; cast at the boundary.
    rules: list[object] = []
    if older_than_days is not None:
        rules.append(OlderThanDays(older_than_days))
    if duration_below is not None:
        rules.append(DurationBelow(duration_below))
    if title_contains:
        rules.append(TitleContains(title_contains))
    if title_regex:
        rules.append(TitleRegex(title_regex))
    if host_email:
        rules.append(HostEmail(host_email))
    if participants_below is not None:
        rules.append(ParticipantsBelow(participants_below))
    if has_tag:
        rules.append(HasTag(has_tag))
    if not rules:
        raise typer.BadParameter("Provide at least one filter.")

    engine = RuleEngine(cast("list[Rule]", rules))
    now = datetime.now(tz=UTC)
    cutoff = now - timedelta(days=older_than_days) if older_than_days is not None else None

    matched: list[tuple[Meeting, tuple[str, ...]]] = []

    async def _collect() -> None:
        async for m in deps.client.list_meetings(MeetingFilter(older_than=cutoff)):
            result = engine.evaluate(m, now=now)
            if result.matched:
                matched.append((m, result.reasons))

    asyncio.run(_collect())

    table = Table(title=f"Candidates ({len(matched)})")
    for col in ("ID", "Date", "Title", "Dur", "Host", "Reasons"):
        table.add_column(col)
    for m, reasons in matched:
        table.add_row(
            m.meeting_id,
            m.meeting_date.date().isoformat(),
            m.title[:60],
            f"{m.duration_minutes:.1f}",
            m.host_email,
            ", ".join(reasons),
        )
    console.print(table)

    selections_dir = deps.config.archive.root_dir / "selections"
    selections_dir.mkdir(parents=True, exist_ok=True)
    scan_id = f"scan_{now.strftime('%Y%m%dT%H%M')}"
    payload = {
        "scan_id": scan_id,
        "created_at": now.isoformat(),
        "filters_applied": {
            "older_than_days": older_than_days,
            "duration_below_minutes": duration_below,
            "title_contains": title_contains,
            "title_regex": title_regex,
            "host_email": host_email,
            "participants_below": participants_below,
            "has_tag": has_tag,
        },
        "meetings": [
            {
                "id": m.meeting_id,
                "title": m.title,
                "date": m.meeting_date.isoformat(),
                "duration_min": m.duration_minutes,
                "host": m.host_email,
                "participants": m.participant_count,
                "tags": list(m.tags),
                "selected": True,
                "matched_rules": list(reasons),
            }
            for m, reasons in matched
        ],
    }
    target = selections_dir / f"{scan_id}.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[green]Wrote selection:[/green] {target}")
