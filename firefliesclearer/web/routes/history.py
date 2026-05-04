"""History route — audit log with filters and pagination."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from firefliesclearer.application.audit_service import (
    FAILED_STATES,
    AuditService,
    HistoryFilter,
)
from firefliesclearer.core.models import MeetingState
from firefliesclearer.web.deps import get_deps

router = APIRouter()

_PAGE_SIZE = 50


def _templates(request: Request) -> Jinja2Templates:
    t: Jinja2Templates = request.app.state.templates
    return t


# Maps preset name → timedelta offset from now (None = no lower bound).
_RANGE_DELTAS: dict[str, timedelta | None] = {
    "last-7d": timedelta(days=7),
    "last-30d": timedelta(days=30),
    "last-90d": timedelta(days=90),
    "all-time": None,
    "custom": None,  # handled separately
}


def _parse_date_param(value: str | None) -> datetime | None:
    """Parse ISO date string (YYYY-MM-DD) to UTC midnight datetime, or None."""
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value)
        # If no timezone info, assume UTC midnight
        if d.tzinfo is None:
            return d.replace(tzinfo=UTC)
        return d
    except ValueError:
        return None


def _resolve_date_range(
    range_preset: str,
    from_param: str | None,
    to_param: str | None,
    now: datetime,
) -> tuple[datetime | None, datetime | None]:
    """Return (date_from, date_to) for the given preset / custom params."""
    if range_preset == "custom":
        return _parse_date_param(from_param), _parse_date_param(to_param)

    delta = _RANGE_DELTAS.get(range_preset, timedelta(days=30))
    if delta is None:
        return None, None
    return now - delta, None


def _parse_states(raw: list[str] | None) -> tuple[MeetingState, ...] | None:
    """Convert list of state strings to a tuple of MeetingState, silently skipping unknowns."""
    if not raw:
        return None
    valid: list[MeetingState] = []
    valid_values = {s.value for s in MeetingState}
    for s in raw:
        if s in valid_values:
            valid.append(MeetingState(s))
    return tuple(valid) if valid else None


@router.get("/history")
async def history(
    request: Request,
    range: str = "last-30d",
    state: list[str] | None = Query(default=None),  # noqa: B008
    q: str = "",
    page: int = 1,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the history page with filters and pagination."""
    # Clamp page to >= 1
    page = max(1, page)

    # Read the current time from the injected clock so tests can pin "now"
    # instead of racing the wall clock. The previous implementation called
    # ``datetime.now(tz=UTC)`` directly, which drifted past the
    # ``last-7d`` boundary in the test suite once enough days passed since
    # the test's hardcoded ``NOW`` constant.
    now = deps.clock.now()
    date_from, date_to = _resolve_date_range(range, from_param=from_, to_param=to, now=now)

    states = _parse_states(state)

    audit = AuditService(manifest=deps.manifest)

    # Count first so we can clamp page before querying rows.
    count_filt = HistoryFilter(
        states=states,
        date_from=date_from,
        date_to=date_to,
        title_contains=q or None,
    )
    total = audit.history_count(count_filt)
    total_pages = max(1, math.ceil(total / _PAGE_SIZE))

    # Clamp page to [1, total_pages] before computing offset.
    page = max(1, min(page, total_pages))

    filt = HistoryFilter(
        states=states,
        date_from=date_from,
        date_to=date_to,
        title_contains=q or None,
        limit=_PAGE_SIZE,
        offset=(page - 1) * _PAGE_SIZE,
    )

    rows = audit.history(filt)

    all_states = list(MeetingState)

    # Drive the per-row Retry button off the canonical FAILED_STATES tuple
    # so adding a new failed state automatically grows the action surface
    # without a template edit. Projected to .value to make the check a
    # cheap string-equality in Jinja.
    retryable_state_values = tuple(s.value for s in FAILED_STATES)
    ctx = {
        "request": request,
        "rows": rows,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "range": range,
        "state": state or [],
        "q": q,
        "all_states": all_states,
        "retryable_state_values": retryable_state_values,
        "date_from": from_,
        "date_to": to,
        "version": str(request.app.state.version),
    }
    return _templates(request).TemplateResponse(request, "history.html", ctx)


@router.get("/history/{meeting_id}/panel")
async def history_panel(
    request: Request,
    meeting_id: str,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the side-panel fragment for the given meeting."""
    audit = AuditService(manifest=deps.manifest)
    record = deps.manifest.get(meeting_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    log_entries = audit.state_log(meeting_id)
    return _templates(request).TemplateResponse(
        request,
        "history_panel.html",
        {"record": record, "log_entries": log_entries},
    )
