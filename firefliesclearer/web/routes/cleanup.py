"""Cleanup wizard routes.

Phase 5 of the v2 web UI builds a 4-step wizard:

1. Filter   (this task)
2. Review
3. Archive
4. Purge

Step 1 owns:

- ``GET  /cleanup``                — render the filter form, pre-populated
  from any saved ``wizard.filters`` slice (refresh-safe).
- ``POST /cleanup/preview-count``  — HTMX endpoint, returns an HTML
  fragment with the live "N meetings would match" counter.
- ``POST /cleanup``                — submit the form; on success persists
  the wizard state and redirects to ``/cleanup/review`` (Task 5.3).

Subsequent tasks (5.3-5.7) attach more handlers to the same router.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from firefliesclearer.application.scan_service import ScanFilters, ScanService
from firefliesclearer.web import wizard_session
from firefliesclearer.web.deps import get_deps
from firefliesclearer.web.sessions import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Local helpers (mirroring the pattern used in routes/setup.py).
# These are duplicated locally for now; consolidation into a shared helper
# module is a Phase 5.8 cleanup.
# ---------------------------------------------------------------------------


def _sid(request: Request) -> str:
    return request.cookies.get("ffc_session", "")


def _store(request: Request) -> SessionStore:
    store: SessionStore = request.app.state.session_store
    return store


def _templates(request: Request) -> Jinja2Templates:
    t: Jinja2Templates = request.app.state.templates
    return t


def _service(deps: SimpleNamespace) -> ScanService:
    return ScanService(repo=deps.client, clock=deps.clock)


def _validate_regex(filters: ScanFilters) -> str | None:
    """Eagerly validate ``title_regex`` so bad patterns surface inline.

    Returns ``None`` when the pattern is absent or compiles successfully,
    otherwise an inline error message suitable for the form/fragment.
    """
    if filters.title_regex is None:
        return None
    try:
        re.compile(filters.title_regex)
    except re.error as exc:
        return f"Invalid regex: {exc}"
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/cleanup")
async def step1_form(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the Step 1 filter form, pre-populated from session state."""
    state = wizard_session.get_state(_store(request), _sid(request))
    filters = wizard_session.filters_from_dict(state.get("filters", {}))
    return _templates(request).TemplateResponse(
        request,
        "cleanup/step1_filter.html",
        {
            "filters": filters,
            "presets": [],
            "step": "filter",
            "error": None,
        },
    )


@router.post("/cleanup/preview-count")
async def preview_count(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """HTMX endpoint: return an HTML fragment with the live match count."""
    form = await request.form()
    filters = wizard_session.parse_filter_form(dict(form))
    if filters.is_empty():
        return _templates(request).TemplateResponse(
            request,
            "cleanup/_preview_count.html",
            {"count": None, "message": "Add at least one filter to see a count."},
        )

    err = _validate_regex(filters)
    if err:
        return _templates(request).TemplateResponse(
            request,
            "cleanup/_preview_count.html",
            {"count": None, "message": err},
        )

    svc = _service(deps)
    try:
        result = await svc.scan(filters)
    except Exception as exc:  # API error path: fragment, never 5xx.
        logger.warning("preview-count failed: %s", exc, exc_info=True)
        return _templates(request).TemplateResponse(
            request,
            "cleanup/_preview_count.html",
            {"count": None, "message": f"Could not preview count: {exc}."},
        )
    return _templates(request).TemplateResponse(
        request,
        "cleanup/_preview_count.html",
        {"count": len(result.matches), "message": None},
    )


@router.post("/cleanup")
async def step1_submit(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Submit the filter form. On success, redirect to Step 2 (Review)."""
    form = await request.form()
    filters = wizard_session.parse_filter_form(dict(form))
    if filters.is_empty():
        return _templates(request).TemplateResponse(
            request,
            "cleanup/step1_filter.html",
            {
                "filters": filters,
                "presets": [],
                "step": "filter",
                "error": "Add at least one filter before continuing.",
            },
            status_code=422,
        )
    err = _validate_regex(filters)
    if err:
        return _templates(request).TemplateResponse(
            request,
            "cleanup/step1_filter.html",
            {
                "filters": filters,
                "presets": [],
                "step": "filter",
                "error": err,
            },
            status_code=422,
        )
    state = wizard_session.WizardState(
        step="review",
        filters=wizard_session.filters_to_dict(filters),
        selected_ids=[],
        operation_id=None,
    )
    wizard_session.set_state(_store(request), _sid(request), state)
    return _redirect("/cleanup/review")


def _redirect(location: str) -> RedirectResponse:
    """RedirectResponse helper with 303 (See Other) for POST -> GET."""
    return RedirectResponse(location, status_code=303)


# Re-export ScanFilters so future wizard steps can import from one place.
__all__ = ["ScanFilters", "router"]
