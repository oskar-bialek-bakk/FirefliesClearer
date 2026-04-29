"""Dashboard route + sidebar status fragment.

Owns ``GET /`` (full dashboard page) and ``GET /sidebar/status`` (HTMX
poll fragment for the left-rail health summary). Both are read-only
views over the manifest via ``AuditService.summary()``.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from firefliesclearer.application.audit_service import AuditService, StateSummary
from firefliesclearer.core.manifest import Manifest, MeetingRecord
from firefliesclearer.core.models import MeetingState
from firefliesclearer.web.deps import get_deps

router = APIRouter()


def _templates(request: Request) -> Jinja2Templates:
    t: Jinja2Templates = request.app.state.templates
    return t


def _needs_attention_rows(manifest: Manifest, summary: StateSummary) -> list[MeetingRecord]:
    """Resolve failed meeting IDs into MeetingRecord rows for the template."""
    rows: list[MeetingRecord] = []
    for mid in summary.failed_meeting_ids:
        rec = manifest.get(mid)
        if rec is not None:
            rows.append(rec)
    return rows


@router.get("/")
async def dashboard(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    audit = AuditService(manifest=deps.manifest)
    summary = audit.summary()
    rows = _needs_attention_rows(deps.manifest, summary)
    return _templates(request).TemplateResponse(
        request,
        "dashboard.html",
        {
            "summary": summary,
            "needs_attention": rows,
            "version": request.app.state.version,
            "MeetingState": MeetingState,
        },
    )


@router.get("/sidebar/status")
async def sidebar_status(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    audit = AuditService(manifest=deps.manifest)
    summary = audit.summary()
    return _templates(request).TemplateResponse(
        request,
        "partials/sidebar_status.html",
        {"summary": summary, "MeetingState": MeetingState},
    )
