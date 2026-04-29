"""Cleanup wizard routes.

Phase 5 of the v2 web UI builds a 4-step wizard:

1. Filter
2. Review   (Task 5.3)
3. Archive
4. Purge

Step 1 (Task 5.2) owns:

- ``GET  /cleanup``                — render the filter form, pre-populated
  from any saved ``wizard.filters`` slice (refresh-safe).
- ``POST /cleanup/preview-count``  — HTMX endpoint, returns an HTML
  fragment with the live "N meetings would match" counter.
- ``POST /cleanup``                — submit the form; on success persists
  the wizard state and redirects to ``/cleanup/review``.

Step 2 (Task 5.3) owns:

- ``GET  /cleanup/review``                       — render the matches table.
- ``POST /cleanup/review/toggle/{meeting_id}``   — toggle one row.
- ``POST /cleanup/review/select-all``            — page-scoped or all-matches.
- ``POST /cleanup/review/deselect-all``          — page-scoped or clear all.
- ``POST /cleanup/review/invert``                — invert current page.
- ``GET  /cleanup/meeting/{id}/panel``           — side-panel fragment.
- ``POST /cleanup/review``                       — Continue → /cleanup/archive.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from firefliesclearer.application.scan_service import (
    ScanFilters,
    ScanMatch,
    ScanResult,
    ScanService,
)
from firefliesclearer.web import wizard_session
from firefliesclearer.web.deps import get_deps
from firefliesclearer.web.sessions import SessionStore

PAGE_SIZE = 100

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


# ---------------------------------------------------------------------------
# Step 2 — Review (Task 5.3)
# ---------------------------------------------------------------------------


def _filters_from_session(request: Request) -> ScanFilters | None:
    """Read filters from the wizard session slice. Returns None if absent/empty."""
    state = wizard_session.get_state(_store(request), _sid(request))
    raw = state.get("filters", {})
    if not raw:
        return None
    filters = wizard_session.filters_from_dict(raw)
    if filters.is_empty():
        return None
    return filters


async def _scan_or_none(deps: SimpleNamespace, filters: ScanFilters) -> ScanResult | None:
    """Run the scan, returning None on any repository error.

    Errors are logged. Callers decide how to surface "no scan available" — for
    Step 2 today that means rendering an empty table.
    """
    svc = _service(deps)
    try:
        return await svc.scan(filters)
    except Exception as exc:  # API error path: degrade gracefully.
        logger.warning("review scan failed: %s", exc, exc_info=True)
        return None


def _page_slice(
    matches: tuple[ScanMatch, ...], page: int
) -> tuple[tuple[ScanMatch, ...], int, int]:
    """Return (slice, total, pages) for the requested page (1-indexed)."""
    total = len(matches)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    return matches[start:end], total, pages


def _review_context(
    request: Request,
    *,
    matches_page: tuple[ScanMatch, ...],
    total: int,
    page: int,
    pages: int,
    selected_ids: set[str],
    error: str | None = None,
) -> dict[str, object]:
    """Shared template context for full-page + table-fragment renders."""
    page_ids = {m.meeting.meeting_id for m in matches_page}
    selected_on_page = len(selected_ids & page_ids)
    return {
        "matches": matches_page,
        "total": total,
        "page": page,
        "pages": pages,
        "page_size": PAGE_SIZE,
        "selected_ids": selected_ids,
        "selected_count": len(selected_ids),
        "selected_on_page": selected_on_page,
        "step": "review",
        "error": error,
    }


@router.get("/cleanup/review")
async def step2_review(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the Step 2 review table (or just the table fragment for HTMX)."""
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    page = _safe_page(request.query_params.get("page"))
    result = await _scan_or_none(deps, filters)
    matches = result.matches if result is not None else ()
    page_matches, total, pages = _page_slice(matches, page)

    selected_ids = wizard_session.get_selected(_store(request), _sid(request))

    ctx = _review_context(
        request,
        matches_page=page_matches,
        total=total,
        page=page,
        pages=pages,
        selected_ids=selected_ids,
    )
    template = (
        "cleanup/_review_table.html"
        if request.headers.get("HX-Request") == "true"
        else "cleanup/step2_review.html"
    )
    return _templates(request).TemplateResponse(request, template, ctx)


@router.post("/cleanup/review/toggle/{meeting_id}")
async def review_toggle(
    request: Request,
    meeting_id: str,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Toggle a single meeting's selection. Returns the updated table fragment."""
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    wizard_session.toggle_in_selection(_store(request), _sid(request), meeting_id)
    return await _render_table_fragment(request, deps, filters)


@router.post("/cleanup/review/select-all")
async def review_select_all(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Add the current page (or all matches when ?all=true) to the selection."""
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    form = await request.form()
    use_all = _is_true(form.get("all"))
    page = _safe_page(form.get("page"))

    result = await _scan_or_none(deps, filters)
    matches = result.matches if result is not None else ()

    if use_all:
        ids = [m.meeting.meeting_id for m in matches]
        wizard_session.replace_selection(_store(request), _sid(request), ids)
    else:
        page_matches, _total, _pages = _page_slice(matches, page)
        wizard_session.add_to_selection(
            _store(request),
            _sid(request),
            [m.meeting.meeting_id for m in page_matches],
        )

    return await _render_table_fragment(request, deps, filters, page=page)


@router.post("/cleanup/review/deselect-all")
async def review_deselect_all(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Remove the current page (or clear entirely when ?all=true) from selection."""
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    form = await request.form()
    use_all = _is_true(form.get("all"))
    page = _safe_page(form.get("page"))

    if use_all:
        wizard_session.replace_selection(_store(request), _sid(request), [])
    else:
        result = await _scan_or_none(deps, filters)
        matches = result.matches if result is not None else ()
        page_matches, _total, _pages = _page_slice(matches, page)
        wizard_session.remove_from_selection(
            _store(request),
            _sid(request),
            [m.meeting.meeting_id for m in page_matches],
        )

    return await _render_table_fragment(request, deps, filters, page=page)


@router.post("/cleanup/review/invert")
async def review_invert(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Invert selection on the current page only (off-page selection untouched)."""
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    form = await request.form()
    page = _safe_page(form.get("page"))

    result = await _scan_or_none(deps, filters)
    matches = result.matches if result is not None else ()
    page_matches, _total, _pages = _page_slice(matches, page)

    page_ids = {m.meeting.meeting_id for m in page_matches}
    selected = wizard_session.get_selected(_store(request), _sid(request))
    inverted = (selected - page_ids) | (page_ids - selected)
    # Preserve the original ordering: on-page IDs come from page_matches, then
    # any off-page IDs that survived from the previous selection.
    off_page = [mid for mid in selected if mid not in page_ids]
    on_page = [m.meeting.meeting_id for m in page_matches if m.meeting.meeting_id in inverted]
    wizard_session.replace_selection(_store(request), _sid(request), off_page + on_page)

    return await _render_table_fragment(request, deps, filters, page=page)


@router.get("/cleanup/meeting/{meeting_id}/panel")
async def meeting_side_panel(
    request: Request,
    meeting_id: str,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the side-panel fragment for the given meeting (must be in matches)."""
    filters = _filters_from_session(request)
    if filters is None:
        raise HTTPException(status_code=404, detail="No active scan in session")

    result = await _scan_or_none(deps, filters)
    if result is None:
        raise HTTPException(status_code=404, detail="Scan unavailable")

    for match in result.matches:
        if match.meeting.meeting_id == meeting_id:
            return _templates(request).TemplateResponse(
                request,
                "cleanup/_review_side_panel.html",
                {"meeting": match.meeting, "matched_rules": match.matched_rules},
            )
    raise HTTPException(status_code=404, detail="Meeting not in current matches")


@router.post("/cleanup/review")
async def step2_submit(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Continue button: advance wizard step to ``archive`` and redirect."""
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    form = await request.form()
    page = _safe_page(form.get("page"))

    selected_ids = wizard_session.get_selected(_store(request), _sid(request))
    if not selected_ids:
        result = await _scan_or_none(deps, filters)
        matches = result.matches if result is not None else ()
        page_matches, total, pages = _page_slice(matches, page)
        ctx = _review_context(
            request,
            matches_page=page_matches,
            total=total,
            page=page,
            pages=pages,
            selected_ids=set(),
            error="Please select at least one meeting before continuing.",
        )
        return _templates(request).TemplateResponse(
            request,
            "cleanup/step2_review.html",
            ctx,
            status_code=422,
        )

    state = wizard_session.get_state(_store(request), _sid(request))
    new_state = wizard_session.WizardState(
        step="archive",
        filters=state.get("filters", {}),
        selected_ids=list(selected_ids),
        operation_id=state.get("operation_id"),
    )
    wizard_session.set_state(_store(request), _sid(request), new_state)
    return _redirect("/cleanup/archive")


# ---------------------------------------------------------------------------
# Internal helpers — Step 2
# ---------------------------------------------------------------------------


async def _render_table_fragment(
    request: Request,
    deps: SimpleNamespace,
    filters: ScanFilters,
    *,
    page: int | None = None,
) -> Response:
    """Re-run the scan and render the table + OOB toolbar fragment.

    Returns the concatenation of ``_review_table.html`` (HTMX primary swap
    target) and ``_review_toolbar.html`` (out-of-band swap target). This keeps
    the toolbar's selected-count and "select all N" banner in sync after every
    selection mutation (toggle / select-all / deselect-all / invert).

    CSRF for these POST handlers is enforced by the global ``CSRFMiddleware``
    in ``firefliesclearer.web.security``; no per-route check is needed.
    """
    page = page if page is not None else _safe_page(request.query_params.get("page"))
    result = await _scan_or_none(deps, filters)
    matches = result.matches if result is not None else ()
    page_matches, total, pages = _page_slice(matches, page)
    selected_ids = wizard_session.get_selected(_store(request), _sid(request))
    ctx = _review_context(
        request,
        matches_page=page_matches,
        total=total,
        page=page,
        pages=pages,
        selected_ids=selected_ids,
    )
    templates = _templates(request)
    table_html = templates.get_template("cleanup/_review_table.html").render(
        {"request": request, **ctx}
    )
    toolbar_html = templates.get_template("cleanup/_review_toolbar.html").render(
        {"request": request, **ctx}
    )
    return Response(table_html + toolbar_html, media_type="text/html")


def _safe_page(raw: object) -> int:
    """Coerce a raw ``?page=N`` query/form value into a positive int (default 1)."""
    if raw is None:
        return 1
    try:
        n = int(str(raw))
    except (TypeError, ValueError):
        return 1
    return max(1, n)


def _is_true(raw: object) -> bool:
    """Treat ``"true"`` / ``"on"`` / ``"1"`` as truthy form values."""
    if raw is None:
        return False
    return str(raw).strip().lower() in {"true", "on", "1", "yes"}


# Re-export ScanFilters so future wizard steps can import from one place.
__all__ = ["ScanFilters", "router"]
