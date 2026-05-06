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
from collections.abc import Awaitable, Callable
from datetime import datetime
from types import SimpleNamespace
from typing import Final, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from firefliesclearer.application.archive_service import ArchiveService
from firefliesclearer.application.preset_service import (
    PresetAlreadyExistsError,
    PresetNotFoundError,
    PresetService,
)
from firefliesclearer.application.scan_service import (
    ScanFilters,
    ScanMatch,
    ScanResult,
    ScanService,
)
from firefliesclearer.core.manifest import IllegalStateTransition
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.core.rules import Rule
from firefliesclearer.infra.config import ScanFiltersModel
from firefliesclearer.web import wizard_session
from firefliesclearer.web.deps import get_deps
from firefliesclearer.web.operations import (
    Event,
    Operation,
    OperationKind,
    OperationRegistry,
    SameKindAlreadyRunning,
    _RunnerContext,
)
from firefliesclearer.web.sessions import SessionStore

# Estimated audio bitrate (kbps) for the preflight size estimate.
# Fireflies' default download is low-bitrate mono audio; 64 kbps is the
# advertised "voice" tier and gives a usable order-of-magnitude estimate.
AUDIO_KBPS = 64

PAGE_SIZE = 100

# Sort axes available on the review table (Step 2). The default — date desc —
# matches the implicit ordering produced by ``ScanService.scan`` so existing
# tests / users see no behavior change without a query param.
SORT_FIELDS: Final[frozenset[str]] = frozenset({"date", "duration", "title"})
SORT_DIRECTIONS: Final[frozenset[str]] = frozenset({"asc", "desc"})
DEFAULT_SORT: Final[str] = "date"
DEFAULT_DIR: Final[str] = "desc"

# Sentinel for "leave this field unchanged" in ``_set_wizard``. Distinct from
# ``None``, which is a legitimate value for ``operation_id`` (it clears the id
# when transitioning to the next step).
_UNSET: Final[object] = object()

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
    # Phase 6: deps.scan_repo is always the cache adapter; no fallback needed.
    return ScanService(repo=deps.scan_repo, clock=deps.clock)


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
    """Render the Step 1 filter form, pre-populated from session state.

    If ``?preset=NAME`` is supplied, the preset's filters override the session
    state for this render (they are not persisted to the session until the user
    submits the form).
    """
    state = wizard_session.get_state(_store(request), _sid(request))
    filters = wizard_session.filters_from_dict(state.get("filters", {}))
    loaded_trash_preset = state.get("trash_classifier_preset")

    config_path = request.app.state.config_path
    preset_svc = PresetService(config_path) if config_path else None
    presets = preset_svc.list() if preset_svc else []

    preset_name_param = request.query_params.get("preset")
    preset_error: str | None = None
    loaded_preset: str | None = None

    if preset_name_param and preset_svc:
        try:
            preset = preset_svc.get(preset_name_param)
            filters = wizard_session.filters_from_dict(preset.filters.model_dump())
            loaded_preset = preset.name
        except PresetNotFoundError:
            preset_error = f'Preset "{preset_name_param}" not found.'

    return _templates(request).TemplateResponse(
        request,
        "cleanup/step1_filter.html",
        {
            "filters": filters,
            "presets": presets,
            "loaded_preset": loaded_preset,
            "loaded_trash_preset": loaded_trash_preset,
            "step": "filter",
            "error": preset_error,
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

    result, scan_error = await _scan_or_error(deps, filters)
    if scan_error is not None:
        return _templates(request).TemplateResponse(
            request,
            "cleanup/_preview_count.html",
            {"count": None, "message": scan_error},
        )
    assert result is not None  # mypy: scan_error None implies result non-None
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

    trash_preset_raw = form.get("trash_preset")
    trash_preset = str(trash_preset_raw).strip() if trash_preset_raw is not None else ""
    trash_classifier_preset: str | None = trash_preset or None  # collapse empty string -> None

    config_path = request.app.state.config_path
    presets = PresetService(config_path).list() if config_path else []

    if filters.is_empty():
        return _templates(request).TemplateResponse(
            request,
            "cleanup/step1_filter.html",
            {
                "filters": filters,
                "presets": presets,
                "loaded_preset": None,
                "loaded_trash_preset": trash_classifier_preset,
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
                "presets": presets,
                "loaded_preset": None,
                "loaded_trash_preset": trash_classifier_preset,
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
        trash_ids=[],
        trash_classifier_preset=trash_classifier_preset,
        trash_candidate_ids=[],
    )
    wizard_session.set_state(_store(request), _sid(request), state)
    return _redirect("/cleanup/review")


def _redirect(location: str) -> RedirectResponse:
    """RedirectResponse helper with 303 (See Other) for POST -> GET."""
    return RedirectResponse(location, status_code=303)


# ---------------------------------------------------------------------------
# Save-as-preset (inline from Step 1)
# ---------------------------------------------------------------------------


@router.post("/cleanup/save-as-preset")
async def save_as_preset(
    request: Request,
) -> Response:
    """Save the current wizard session filters as a named preset.

    Re-renders Step 1 with a success or error banner.
    """
    form_data = await request.form()
    form = dict(form_data)

    preset_name = str(form.get("preset_name", "")).strip()
    preset_description = str(form.get("preset_description", "")).strip()
    preset_default = str(form.get("preset_default", "")) in {"on", "true", "1", "yes"}

    config_path = request.app.state.config_path
    preset_svc = PresetService(config_path) if config_path else None
    presets = preset_svc.list() if preset_svc else []

    # Read current session filters
    filters = _filters_from_session(request)
    if filters is None:
        filters = wizard_session.filters_from_dict({})

    save_preset_error: str | None = None
    save_preset_success: str | None = None

    if preset_name and preset_svc:
        filters_model = ScanFiltersModel.model_validate(wizard_session.filters_to_dict(filters))
        try:
            preset_svc.create(
                preset_name, preset_description, filters_model, default=preset_default
            )
            save_preset_success = f"Saved preset: {preset_name}"
        except PresetAlreadyExistsError as exc:  # surface errors as banners
            save_preset_error = str(exc)
    elif not preset_name:
        save_preset_error = "Preset name is required."

    loaded_trash_preset_sap = wizard_session.get_state(_store(request), _sid(request)).get(
        "trash_classifier_preset"
    )
    return _templates(request).TemplateResponse(
        request,
        "cleanup/step1_filter.html",
        {
            "filters": filters,
            "presets": presets,
            "loaded_preset": None,
            "loaded_trash_preset": loaded_trash_preset_sap,
            "step": "filter",
            "error": None,
            "save_preset_error": save_preset_error,
            "save_preset_success": save_preset_success,
        },
    )


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
    result, _err = await _scan_or_error(deps, filters)
    return result


async def _scan_or_error(
    deps: SimpleNamespace, filters: ScanFilters
) -> tuple[ScanResult | None, str | None]:
    """Run the scan, returning (result, error_message).

    On rate limit: error_message describes the retry window.
    On other API errors: error_message is the exception text (truncated).
    On success: error_message is None.
    """
    from firefliesclearer.infra.fireflies_client import FirefliesError, RateLimitedError

    svc = _service(deps)
    try:
        return await svc.scan(filters), None
    except RateLimitedError as exc:
        logger.warning("scan rate-limited: %s", exc)
        if exc.retry_after_seconds is not None and exc.retry_after_seconds > 60:
            mins = int(exc.retry_after_seconds // 60) + 1
            msg = (
                f"Fireflies is rate-limiting this account. Retry after about {mins} "
                f"minute{'s' if mins != 1 else ''} (typically resets at next UTC midnight)."
            )
        else:
            msg = "Fireflies is rate-limiting this account. Please retry in a moment."
        return None, msg
    except FirefliesError as exc:
        logger.warning("scan failed (FirefliesError): %s", exc)
        return None, f"Fireflies API error: {exc}"
    except Exception as exc:
        logger.warning("scan failed: %s", exc, exc_info=True)
        return None, f"Could not reach Fireflies: {exc}"


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


def _normalize_sort(raw: object) -> str:
    """Coerce a raw ``?sort=...`` value to a known axis (default: date)."""
    if raw is None:
        return DEFAULT_SORT
    candidate = str(raw).strip().lower()
    return candidate if candidate in SORT_FIELDS else DEFAULT_SORT


def _normalize_dir(raw: object) -> str:
    """Coerce a raw ``?dir=...`` value to ``asc`` or ``desc`` (default: desc)."""
    if raw is None:
        return DEFAULT_DIR
    candidate = str(raw).strip().lower()
    return candidate if candidate in SORT_DIRECTIONS else DEFAULT_DIR


def _sort_matches(
    matches: tuple[ScanMatch, ...], *, sort: str, direction: str
) -> tuple[ScanMatch, ...]:
    """Return ``matches`` reordered by the chosen axis.

    Unknown ``sort`` / ``direction`` values fall back to date / desc so a stray
    URL never produces a 500. Sort is stable; ties retain the input ordering.
    """
    axis = _normalize_sort(sort)
    descending = _normalize_dir(direction) == "desc"

    if axis == "title":
        return tuple(sorted(matches, key=_title_key, reverse=descending))
    if axis == "duration":
        return tuple(sorted(matches, key=_duration_key, reverse=descending))
    return tuple(sorted(matches, key=_date_key, reverse=descending))


def _title_key(match: ScanMatch) -> str:
    return match.meeting.title.casefold()


def _duration_key(match: ScanMatch) -> float:
    return match.meeting.duration_minutes


def _date_key(match: ScanMatch) -> datetime:
    return match.meeting.meeting_date


def _review_context(
    request: Request,
    *,
    matches_page: tuple[ScanMatch, ...],
    total: int,
    page: int,
    pages: int,
    selected_ids: set[str],
    trash_ids: set[str],
    error: str | None = None,
    sort: str = DEFAULT_SORT,
    direction: str = DEFAULT_DIR,
    sync_status: dict[str, object] | None = None,
) -> dict[str, object]:
    """Shared template context for full-page + table-fragment renders."""
    page_ids = {m.meeting.meeting_id for m in matches_page}
    selected_on_page = len(selected_ids & page_ids)
    ctx: dict[str, object] = {
        "matches": matches_page,
        "total": total,
        "page": page,
        "pages": pages,
        "page_size": PAGE_SIZE,
        "selected_ids": selected_ids,
        "trash_ids": trash_ids,
        "selected_count": len(selected_ids),
        "selected_on_page": selected_on_page,
        "step": "review",
        "error": error,
        "sort": sort,
        "dir": direction,
    }
    if sync_status is not None:
        ctx["sync_status"] = sync_status
    return ctx


@router.get("/cleanup/review")
async def step2_review(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the Step 2 review table (or just the table fragment for HTMX).

    Accepts an optional ``?error=empty-selection`` query param emitted by the
    archive / purge guards when they bounce the user back here.  The param is
    forwarded to the template so the error banner is visible without requiring a
    server-side flash mechanism.
    """
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    page = _safe_page(request.query_params.get("page"))
    sort = _normalize_sort(request.query_params.get("sort"))
    direction = _normalize_dir(request.query_params.get("dir"))
    result, scan_error = await _scan_or_error(deps, filters)
    matches = result.matches if result is not None else ()
    sorted_matches = _sort_matches(matches, sort=sort, direction=direction)
    page_matches, total, pages = _page_slice(sorted_matches, page)

    # Trash-classifier: evaluate the preset against the current scan result and
    # persist the candidate set so toggle handlers can consult it cheaply.
    trash_preset_name = wizard_session.get_state(_store(request), _sid(request)).get(
        "trash_classifier_preset"
    )
    trash_candidates: list[str] = []
    if trash_preset_name and result is not None:
        config_path = request.app.state.config_path
        preset_svc = PresetService(config_path) if config_path else None
        if preset_svc is not None:
            try:
                trash_preset = preset_svc.get(trash_preset_name)
            except PresetNotFoundError:
                trash_preset = None
            if trash_preset is not None:
                trash_filters = wizard_session.filters_from_dict(trash_preset.filters.model_dump())
                trash_candidates = _classify_trash(
                    [m.meeting for m in result.matches],
                    trash_filters,
                    now=deps.clock.now(),
                )

    # Persist for toggle handlers — updated every time Step 2 renders so
    # filter changes propagate automatically.
    _persist_trash_candidates(request, trash_candidates)

    selected_ids = wizard_session.get_selected(_store(request), _sid(request))

    # Surface the error param forwarded by the empty-selection guards.
    inline_error: str | None = None
    if request.query_params.get("error") == "empty-selection":
        inline_error = "Select at least one meeting before continuing."
    elif scan_error is not None:
        # Bubble scan failures (rate limit, network, etc.) so the user sees
        # WHY the table is empty instead of a confusing zero-results page.
        inline_error = scan_error

    from firefliesclearer.web.routes.sync import maybe_status_for_template

    ctx = _review_context(
        request,
        matches_page=page_matches,
        total=total,
        page=page,
        pages=pages,
        selected_ids=selected_ids,
        trash_ids=wizard_session.get_trash_ids(_store(request), _sid(request)),
        error=inline_error,
        sort=sort,
        direction=direction,
        sync_status=maybe_status_for_template(request, deps),
    )
    # HTMX request (column-header sort, pagination): emit the same triplet
    # the POST mutation handlers emit — table + OOB toolbar + OOB continue
    # form. Returning only the table left the toolbar's hidden ``sort``/``dir``
    # inputs stale after a sort change, so the next "Deselect all on page"
    # click sent the *previous* sort to the server and operated on the wrong
    # rows (user-reported 2026-05-03).
    if request.headers.get("HX-Request") == "true":
        templates = _templates(request)
        render_ctx = {"request": request, **ctx}
        table_html = templates.get_template("cleanup/_review_table.html").render(render_ctx)
        toolbar_html = templates.get_template("cleanup/_review_toolbar.html").render(render_ctx)
        continue_html = templates.get_template("cleanup/_review_continue.html").render(render_ctx)
        return Response(table_html + toolbar_html + continue_html, media_type="text/html")
    return _templates(request).TemplateResponse(request, "cleanup/step2_review.html", ctx)


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

    form = await request.form()
    page = _safe_page(form.get("page"))
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    added = wizard_session.toggle_in_selection(_store(request), _sid(request), meeting_id)
    if added:
        candidates = set(
            wizard_session.get_state(_store(request), _sid(request)).get("trash_candidate_ids", [])
            or []
        )
        if meeting_id in candidates:
            wizard_session.add_to_trash(_store(request), _sid(request), [meeting_id])
    return await _render_table_fragment(
        request, deps, filters, page=page, sort=sort, direction=direction
    )


@router.post("/cleanup/review/archive-toggle/{meeting_id}")
async def review_archive_toggle(
    request: Request,
    meeting_id: str,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Toggle a meeting's trash-classification (Archive checkbox).

    Only effective when the meeting is in ``selected_ids`` — otherwise a
    silent no-op (``set_trash_ids`` enforces ``trash_ids ⊆ selected_ids``).
    Returns the same table fragment as the per-row select toggle so the
    HTMX swap renders consistently.
    """
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    form = await request.form()
    page = _safe_page(form.get("page"))
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    wizard_session.toggle_in_trash(_store(request), _sid(request), meeting_id)
    return await _render_table_fragment(
        request, deps, filters, page=page, sort=sort, direction=direction
    )


@router.post("/cleanup/review/mark-trash")
async def review_mark_trash(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Bulk: classify all selected rows as trash (copy ``selected_ids`` -> ``trash_ids``)."""
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    form = await request.form()
    page = _safe_page(form.get("page"))
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    state = wizard_session.get_state(_store(request), _sid(request))
    selected = list(state.get("selected_ids", []) or [])
    wizard_session.set_trash_ids(_store(request), _sid(request), selected)
    return await _render_table_fragment(
        request, deps, filters, page=page, sort=sort, direction=direction
    )


@router.post("/cleanup/review/mark-archive")
async def review_mark_archive(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Bulk: classify all selected rows as archive (clear ``trash_ids``)."""
    filters = _filters_from_session(request)
    if filters is None:
        return _redirect("/cleanup")

    form = await request.form()
    page = _safe_page(form.get("page"))
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    wizard_session.set_trash_ids(_store(request), _sid(request), [])
    return await _render_table_fragment(
        request, deps, filters, page=page, sort=sort, direction=direction
    )


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
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    result = await _scan_or_none(deps, filters)
    matches = result.matches if result is not None else ()
    sorted_matches = _sort_matches(matches, sort=sort, direction=direction)

    if use_all:
        ids = [m.meeting.meeting_id for m in sorted_matches]
        wizard_session.replace_selection(_store(request), _sid(request), ids)
        newly_selected_ids = ids
    else:
        page_matches, _total, _pages = _page_slice(sorted_matches, page)
        wizard_session.add_to_selection(
            _store(request),
            _sid(request),
            [m.meeting.meeting_id for m in page_matches],
        )
        newly_selected_ids = [m.meeting.meeting_id for m in page_matches]

    candidates = set(
        wizard_session.get_state(_store(request), _sid(request)).get("trash_candidate_ids", [])
        or []
    )
    auto_trash = [mid for mid in newly_selected_ids if mid in candidates]
    if auto_trash:
        wizard_session.add_to_trash(_store(request), _sid(request), auto_trash)

    return await _render_table_fragment(
        request, deps, filters, page=page, sort=sort, direction=direction
    )


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
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    if use_all:
        wizard_session.replace_selection(_store(request), _sid(request), [])
    else:
        result = await _scan_or_none(deps, filters)
        matches = result.matches if result is not None else ()
        sorted_matches = _sort_matches(matches, sort=sort, direction=direction)
        page_matches, _total, _pages = _page_slice(sorted_matches, page)
        wizard_session.remove_from_selection(
            _store(request),
            _sid(request),
            [m.meeting.meeting_id for m in page_matches],
        )

    return await _render_table_fragment(
        request, deps, filters, page=page, sort=sort, direction=direction
    )


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
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    result = await _scan_or_none(deps, filters)
    matches = result.matches if result is not None else ()
    sorted_matches = _sort_matches(matches, sort=sort, direction=direction)
    page_matches, _total, _pages = _page_slice(sorted_matches, page)

    page_ids = {m.meeting.meeting_id for m in page_matches}
    selected = wizard_session.get_selected(_store(request), _sid(request))
    inverted = (selected - page_ids) | (page_ids - selected)
    # Preserve the original ordering: on-page IDs come from page_matches, then
    # any off-page IDs that survived from the previous selection.
    off_page = [mid for mid in selected if mid not in page_ids]
    on_page = [m.meeting.meeting_id for m in page_matches if m.meeting.meeting_id in inverted]
    wizard_session.replace_selection(_store(request), _sid(request), off_page + on_page)

    return await _render_table_fragment(
        request, deps, filters, page=page, sort=sort, direction=direction
    )


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
    sort = _normalize_sort(form.get("sort"))
    direction = _normalize_dir(form.get("dir"))

    selected_ids = wizard_session.get_selected(_store(request), _sid(request))
    if not selected_ids:
        result = await _scan_or_none(deps, filters)
        matches = result.matches if result is not None else ()
        sorted_matches = _sort_matches(matches, sort=sort, direction=direction)
        page_matches, total, pages = _page_slice(sorted_matches, page)
        ctx = _review_context(
            request,
            matches_page=page_matches,
            total=total,
            page=page,
            pages=pages,
            selected_ids=set(),
            trash_ids=wizard_session.get_trash_ids(_store(request), _sid(request)),
            error="Please select at least one meeting before continuing.",
            sort=sort,
            direction=direction,
        )
        return _templates(request).TemplateResponse(
            request,
            "cleanup/step2_review.html",
            ctx,
            status_code=422,
        )

    state = wizard_session.get_state(_store(request), _sid(request))
    trash_ids = list(state.get("trash_ids", []) or [])
    new_state = wizard_session.WizardState(
        step="archive",
        filters=state.get("filters", {}),
        selected_ids=list(selected_ids),
        operation_id=state.get("operation_id"),
        trash_ids=trash_ids,
        trash_classifier_preset=state.get("trash_classifier_preset"),
        trash_candidate_ids=list(state.get("trash_candidate_ids", []) or []),
    )
    wizard_session.set_state(_store(request), _sid(request), new_state)
    if trash_ids:
        return _redirect("/cleanup/trash-confirm")
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
    sort: str | None = None,
    direction: str | None = None,
) -> Response:
    """Re-run the scan and render the table + OOB toolbar + OOB continue form.

    Returns the concatenation of:
      - ``_review_table.html`` (HTMX primary swap target — ``.review-table``)
      - ``_review_toolbar.html`` (out-of-band swap — ``#review-toolbar``)
      - ``_review_continue.html`` (out-of-band swap — ``#continue-form``)

    All three share state derived from the same selection set, so re-rendering
    them together avoids drift. In particular, the Continue button's
    ``disabled`` attribute is computed from ``selected_count``; without the
    OOB swap it stuck at the initial-render value and prevented the user
    from advancing even after picking rows (user-reported 2026-05-03).

    CSRF for these POST handlers is enforced by the global ``CSRFMiddleware``
    in ``firefliesclearer.web.security``; no per-route check is needed.
    """
    page = page if page is not None else _safe_page(request.query_params.get("page"))
    sort = sort if sort is not None else _normalize_sort(request.query_params.get("sort"))
    direction = (
        direction if direction is not None else _normalize_dir(request.query_params.get("dir"))
    )
    result = await _scan_or_none(deps, filters)
    matches = result.matches if result is not None else ()
    sorted_matches = _sort_matches(matches, sort=sort, direction=direction)
    page_matches, total, pages = _page_slice(sorted_matches, page)
    selected_ids = wizard_session.get_selected(_store(request), _sid(request))
    ctx = _review_context(
        request,
        matches_page=page_matches,
        total=total,
        page=page,
        pages=pages,
        selected_ids=selected_ids,
        trash_ids=wizard_session.get_trash_ids(_store(request), _sid(request)),
        sort=sort,
        direction=direction,
    )
    templates = _templates(request)
    render_ctx = {"request": request, **ctx}
    table_html = templates.get_template("cleanup/_review_table.html").render(render_ctx)
    toolbar_html = templates.get_template("cleanup/_review_toolbar.html").render(render_ctx)
    continue_html = templates.get_template("cleanup/_review_continue.html").render(render_ctx)
    return Response(table_html + toolbar_html + continue_html, media_type="text/html")


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


# ---------------------------------------------------------------------------
# Step 3 — Archive (Task 5.4)
# ---------------------------------------------------------------------------


def _registry(request: Request) -> OperationRegistry:
    reg: OperationRegistry = request.app.state.operation_registry
    return reg


def _estimate_size_mb(meetings: list[Meeting]) -> int:
    """Return the rough total audio size in MB for *meetings*.

    Estimate: ``sum(duration_minutes * 60 * AUDIO_KBPS / 8 / 1024 / 1024)``.
    Rounds to the nearest MB; a zero-meeting input returns 0.
    """
    total_bytes = sum(m.duration_minutes * 60.0 * AUDIO_KBPS * 1000.0 / 8.0 for m in meetings)
    total_mb = total_bytes / (1024.0 * 1024.0)
    return round(total_mb)


# TODO(perf): re-fetches the full Fireflies metadata listing on every archive
# request. Acceptable for personal-scale use; worth caching once we've shipped
# the 5-min metadata cache referenced in spec § 5.2.
async def _selected_meetings(deps: SimpleNamespace, selected_ids: list[str]) -> list[Meeting]:
    """Re-resolve selected ids back to Meeting objects from the local cache.

    Order is preserved from ``selected_ids`` so events stream in the order the
    user expects to see them. Missing ids (e.g. meeting gone-from-source
    between Step 2 and Step 3) are silently dropped.

    Uses ``Manifest.list_known_by_ids`` — a single SQL query for the
    targeted set, no full-table scan. The previous implementation walked
    ``manifest.list_known()`` and filtered in Python, which was O(N_total)
    per step transition; the version before that walked
    ``deps.client.list_meetings(...)`` (live API) which compounded the
    problem with network latency. The targeted helper keeps Step 3 / 4
    start latency bounded by ``len(selected_ids)`` even on large
    manifests (Copilot review on PR #19).
    """
    if not selected_ids:
        return []
    by_id = {m.meeting_id: m for m in deps.manifest.list_known_by_ids(selected_ids)}
    return [by_id[mid] for mid in selected_ids if mid in by_id]


def _classify_trash(meetings: list[Meeting], filters: ScanFilters, *, now: datetime) -> list[str]:
    """Return the subset of meeting ids that satisfy ``filters``.

    Uses ``ScanService._build_rules`` so the trash classifier gets the
    same predicate semantics as the cleanup-filter scan (conjunctive: a
    meeting matches iff every active rule matches). Empty filter set →
    no candidates.
    """
    rules = cast("list[Rule]", ScanService._build_rules(filters))
    if not rules:
        return []
    return [m.meeting_id for m in meetings if all(r.matches(m, now=now) for r in rules)]


def _persist_trash_candidates(request: Request, ids: list[str]) -> None:
    """Replace ``trash_candidate_ids`` while preserving the rest of the wizard slice."""
    state = wizard_session.get_state(_store(request), _sid(request))
    new_state = wizard_session.WizardState(
        step=state.get("step", "review"),
        filters=state.get("filters", {}),
        selected_ids=list(state.get("selected_ids", []) or []),
        operation_id=state.get("operation_id"),
        trash_ids=list(state.get("trash_ids", []) or []),
        trash_classifier_preset=state.get("trash_classifier_preset"),
        trash_candidate_ids=list(ids),
    )
    wizard_session.set_state(_store(request), _sid(request), new_state)


def _meetings_for_step4(meetings: list[Meeting], user_email: str | None) -> list[Meeting]:
    """Restrict + order the Step 4 (Delete tab) handoff list.

    Two transformations:

    * Drop non-host meetings. ``Pipeline._archive`` auto-marks any meeting
      whose ``host_email`` differs from the configured ``user_email`` as
      DELETED with reason ``non_host_no_api_delete`` immediately after a
      successful archive, because Fireflies' deleteTranscript mutation
      rejects non-host calls. Listing those rows on the handoff page is
      noise — the user can't bulk-delete them in Fireflies' UI either, and
      they're already in DELETED state in our manifest.

    * Sort ascending by ``meeting_date`` (oldest first) so the user can
      walk the Fireflies UI's day-grouped list top-to-bottom and check off
      titles in order.

    When ``user_email`` is unset (lazy-resolve race or pre-Phase-6
    config), the host filter is a no-op — we keep showing every selected
    row rather than hiding work the user just archived.
    """
    if user_email:
        ue_lc = user_email.lower()
        meetings = [m for m in meetings if not m.host_email or m.host_email.lower() == ue_lc]
    return sorted(meetings, key=lambda m: m.meeting_date)


def _make_archive_runner(
    *, deps: SimpleNamespace, meetings: list[Meeting]
) -> Callable[[_RunnerContext], Awaitable[None]]:
    """Build the runner closure handed to ``OperationRegistry.start``.

    Pre-emits one ``meeting_state="fetching"`` event per meeting before the
    iteration step, then emits one ``done`` / ``failed`` event per outcome
    yielded by ``ArchiveService.archive_meetings``. Cancellation is checked
    between yields — the in-flight meeting is allowed to complete (matches
    the spec's "no half-done meetings" guarantee).
    """
    titles = {m.meeting_id: m.title for m in meetings}

    async def archive_runner(ctx: _RunnerContext) -> None:
        if not meetings:
            return
        # Pre-seed every queued row with its title so the in-progress view
        # shows _which_ meeting is at position N from the moment the page
        # loads — not just an opaque "queued" status. Without this, only the
        # currently-processing meeting got a title (via its `fetching` event)
        # and the rest of the list rendered title-less placeholders, which
        # the user reported as "next are presenting only statuses like
        # queued, pending - without meeting names" (2026-05-03).
        for m in meetings:
            ctx.emit(
                Event(
                    seq=ctx.next_seq(),
                    kind="meeting_state",
                    data={
                        "id": m.meeting_id,
                        "title": m.title,
                        "sub_state": "queued",
                    },
                )
            )
        svc = ArchiveService(pipeline=deps.pipeline, manifest=deps.manifest)
        # Iterate one meeting at a time so cancellation can interrupt cleanly
        # and so per-meeting events bracket each pipeline step.
        for meeting in meetings:
            if ctx.cancel_event.is_set():
                break
            ctx.emit(
                Event(
                    seq=ctx.next_seq(),
                    kind="meeting_state",
                    data={
                        "id": meeting.meeting_id,
                        "title": meeting.title,
                        "sub_state": "fetching",
                    },
                )
            )
            async for outcome in svc.archive_meetings([meeting]):
                sub_state = "failed" if outcome.state.value.startswith("failed_") else "done"
                ctx.emit(
                    Event(
                        seq=ctx.next_seq(),
                        kind="meeting_state",
                        data={
                            "id": outcome.meeting_id,
                            "title": outcome.title or titles.get(outcome.meeting_id, ""),
                            "sub_state": sub_state,
                            "error": outcome.error,
                        },
                    )
                )

    return archive_runner


def _replay_meeting_states(op: Operation) -> tuple[list[dict[str, object]], int, int]:
    """Walk ``op.replay_buffer()`` and produce a per-meeting current-state list.

    Returns ``(rows, archived_count, failed_count)`` where ``rows`` preserves
    the order set by the operation's initial ``meetings`` slot list. Used by
    the in-progress and done templates.
    """
    state_by_id: dict[str, dict[str, object]] = {}
    for slot in op.meetings:
        state_by_id[slot.meeting_id] = {
            "id": slot.meeting_id,
            "title": slot.title,
            "sub_state": slot.sub_state,
            "error": slot.error,
        }
    for evt in op.replay_buffer():
        if evt.kind != "meeting_state":
            continue
        mid = str(evt.data.get("id", ""))
        if not mid:
            continue
        row = state_by_id.setdefault(
            mid,
            {"id": mid, "title": "", "sub_state": "queued", "error": None},
        )
        row["title"] = evt.data.get("title") or row.get("title") or ""
        row["sub_state"] = evt.data.get("sub_state") or row.get("sub_state") or "queued"
        if "error" in evt.data:
            row["error"] = evt.data.get("error")
    rows = list(state_by_id.values())
    archived = sum(1 for r in rows if r["sub_state"] == "done")
    failed = sum(1 for r in rows if r["sub_state"] == "failed")
    return rows, archived, failed


def _set_wizard(
    request: Request,
    *,
    step: str,
    selected_ids: list[str] | None = None,
    operation_id: str | None | object = _UNSET,
) -> None:
    """Update the wizard slice atomically while preserving the rest.

    ``operation_id`` defaults to ``_UNSET`` (a private sentinel) so callers can
    distinguish "leave the existing id alone" from "clear the id" (``None``).
    """
    state = wizard_session.get_state(_store(request), _sid(request))
    if operation_id is _UNSET:
        new_op_id: str | None = state.get("operation_id")
    elif operation_id is None or isinstance(operation_id, str):
        # Narrow ``operation_id`` away from ``object`` to ``str | None``.
        new_op_id = operation_id
    else:
        # Unreachable: ``operation_id``'s declared union is ``str | None | object``
        # but the only ``object`` value we accept is the ``_UNSET`` sentinel.
        raise TypeError(f"unexpected operation_id: {operation_id!r}")
    new_state = wizard_session.WizardState(
        step=step,
        filters=state.get("filters", {}),
        selected_ids=(
            list(selected_ids)
            if selected_ids is not None
            else list(state.get("selected_ids") or [])
        ),
        operation_id=new_op_id,
    )
    wizard_session.set_state(_store(request), _sid(request), new_state)


@router.get("/cleanup/archive")
async def step3_preflight(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the archive preflight: count + size estimate + Start button."""
    state = wizard_session.get_state(_store(request), _sid(request))
    selected_ids = list(state.get("selected_ids") or [])
    if not selected_ids:
        return _redirect("/cleanup/review?error=empty-selection")

    meetings = await _selected_meetings(deps, selected_ids)
    return _templates(request).TemplateResponse(
        request,
        "cleanup/step3_archive_preflight.html",
        {
            "step": "archive",
            "count": len(meetings),
            "size_mb": _estimate_size_mb(meetings),
            "error": None,
        },
    )


@router.post("/cleanup/archive/start")
async def step3_start(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Kick off the archive operation; redirect to the in-progress view."""
    state = wizard_session.get_state(_store(request), _sid(request))
    selected_ids = list(state.get("selected_ids") or [])
    if not selected_ids:
        return _redirect("/cleanup/review?error=empty-selection")

    meetings = await _selected_meetings(deps, selected_ids)
    runner = _make_archive_runner(deps=deps, meetings=meetings)
    try:
        op = await _registry(request).start(
            kind=OperationKind.ARCHIVE,
            meeting_ids=[m.meeting_id for m in meetings],
            runner=runner,
        )
    except SameKindAlreadyRunning:
        size_mb = _estimate_size_mb(meetings)
        return _templates(request).TemplateResponse(
            request,
            "cleanup/step3_archive_preflight.html",
            {
                "step": "archive",
                "count": len(meetings),
                "size_mb": size_mb,
                "error": ("Another archive operation is already running. Wait for it to complete."),
            },
            status_code=409,
        )

    _set_wizard(request, step="archive", operation_id=op.id)
    return _redirect("/cleanup/archive/in-progress")


@router.get("/cleanup/archive/in-progress")
async def step3_in_progress(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the live progress view; redirect when state is missing/terminal."""
    state = wizard_session.get_state(_store(request), _sid(request))
    op_id = state.get("operation_id")
    if not op_id:
        return _redirect("/cleanup/archive")
    try:
        op = _registry(request).get(op_id)
    except KeyError:
        return _redirect("/cleanup/archive")
    if op.state in {"succeeded", "failed", "cancelled"}:
        return _redirect("/cleanup/archive/done")

    rows, archived, failed = _replay_meeting_states(op)
    # Progress bar reads as "meetings processed", so failures advance it too.
    # Mirrors the purge in-progress handler for consistency (spec § 5.3).
    completed = archived + failed
    total = max(op.total, len(rows))
    progress_pct = round(100.0 * completed / total) if total else 0
    return _templates(request).TemplateResponse(
        request,
        "cleanup/step3_archive_in_progress.html",
        {
            "step": "archive",
            "op_id": op.id,
            "meetings": rows,
            "total": total,
            "completed": completed,
            "progress_pct": progress_pct,
        },
    )


@router.get("/cleanup/archive/done")
async def step3_done(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the post-archive summary; redirect when running or missing."""
    state = wizard_session.get_state(_store(request), _sid(request))
    op_id = state.get("operation_id")
    if not op_id:
        return _redirect("/cleanup/archive")
    try:
        op = _registry(request).get(op_id)
    except KeyError:
        return _redirect("/cleanup/archive")
    if op.state == "running":
        return _redirect("/cleanup/archive/in-progress")

    rows, archived, failed = _replay_meeting_states(op)
    return _templates(request).TemplateResponse(
        request,
        "cleanup/step3_archive_done.html",
        {
            "step": "archive",
            "op_id": op.id,
            "meetings": rows,
            "archived_count": archived,
            "failed_count": failed,
        },
    )


@router.post("/cleanup/operations/{op_id}/cancel")
async def cancel_operation(request: Request, op_id: str) -> Response:
    """Set the cooperative cancel flag on the named operation."""
    try:
        _registry(request).get(op_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="operation not found") from exc
    _registry(request).cancel(op_id)
    return Response(status_code=204)


@router.post("/cleanup/archive/continue")
async def step3_continue(request: Request) -> Response:
    """Confirm transition to the Purge step. Requires a terminal op + ≥1 success.

    Filters ``wizard.selected_ids`` down to only the meetings whose archive
    succeeded (per spec § 5.3 "user can continue to Purge with just the
    successes"). The archive op's replay buffer is the source of truth — any
    id with a ``meeting_state`` event of ``sub_state="done"`` is a success.
    """
    state = wizard_session.get_state(_store(request), _sid(request))
    op_id = state.get("operation_id")
    if not op_id:
        return _redirect("/cleanup/archive")
    try:
        op = _registry(request).get(op_id)
    except KeyError:
        return _redirect("/cleanup/archive")
    if op.state == "running":
        return _redirect("/cleanup/archive/in-progress")
    rows, archived, _failed = _replay_meeting_states(op)
    if archived == 0:
        return _redirect("/cleanup/archive/done")
    success_ids = [
        str(row["id"]) for row in rows if row.get("sub_state") == "done" and row.get("id")
    ]
    if not success_ids:
        return _redirect("/cleanup/archive/done")
    _set_wizard(request, step="purge", selected_ids=success_ids, operation_id=None)
    return _redirect("/cleanup/purge")


# ---------------------------------------------------------------------------
# Step 3a — Trash confirmation (typed-count gate, demote, non-host auto-mark)
# ---------------------------------------------------------------------------


@router.get("/cleanup/trash-confirm")
async def step3a_preflight(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the trash-confirmation preflight: warning banner, sorted list,
    per-row demote checkboxes, typed-count gate."""
    state = wizard_session.get_state(_store(request), _sid(request))
    trash_ids = list(state.get("trash_ids", []) or [])
    if not trash_ids:
        return _redirect("/cleanup/archive")

    meetings = await _selected_meetings(deps, trash_ids)
    if not meetings:
        return _redirect("/cleanup/review?error=empty-selection")
    meetings = sorted(meetings, key=lambda m: m.meeting_date)

    return _templates(request).TemplateResponse(
        request,
        "cleanup/step3a_trash_confirm.html",
        {
            "step": "trash",
            "count": len(meetings),
            "meetings": meetings,
            "error": None,
        },
    )


# ---------------------------------------------------------------------------
# Step 4 — Purge (Task 5.5)
# ---------------------------------------------------------------------------


def _make_purge_runner(
    *, deps: SimpleNamespace, meetings: list[Meeting]
) -> Callable[[_RunnerContext], Awaitable[None]]:
    """Build the runner closure handed to ``OperationRegistry.start`` for purge.

    Pre-emits one ``meeting_state="deleting"`` event per meeting before the
    iteration step, then emits one ``done`` / ``failed`` event per outcome
    yielded by ``PurgeService.purge_meetings``. Cancellation is checked
    between yields — the in-flight meeting is allowed to complete.
    """
    from firefliesclearer.application.purge_service import PurgeService

    titles = {m.meeting_id: m.title for m in meetings}

    async def purge_runner(ctx: _RunnerContext) -> None:
        if not meetings:
            return
        # Pre-seed every queued row with its title — same fix as the archive
        # runner. Without this the in-progress purge view shows only "queued"
        # next to placeholder rows for every meeting that hasn't reached its
        # turn yet (user-reported 2026-05-03).
        for m in meetings:
            ctx.emit(
                Event(
                    seq=ctx.next_seq(),
                    kind="meeting_state",
                    data={
                        "id": m.meeting_id,
                        "title": m.title,
                        "sub_state": "queued",
                    },
                )
            )
        svc = PurgeService(pipeline=deps.pipeline, manifest=deps.manifest)
        for meeting in meetings:
            if ctx.cancel_event.is_set():
                break
            # TODO(phase-1): Pipeline.purge_one currently does not re-verify the
            # archive before issuing the delete mutation (spec § 5.4 mandates
            # verify-before-delete). When that lands, change this pre-emit
            # sub_state to "verifying" and emit "deleting" between the verify
            # pass and the delete call.
            ctx.emit(
                Event(
                    seq=ctx.next_seq(),
                    kind="meeting_state",
                    data={
                        "id": meeting.meeting_id,
                        "title": meeting.title,
                        "sub_state": "deleting",
                    },
                )
            )
            async for outcome in svc.purge_meetings([meeting]):
                sub_state = "done" if outcome.state.value == "deleted" else "failed"
                ctx.emit(
                    Event(
                        seq=ctx.next_seq(),
                        kind="meeting_state",
                        data={
                            "id": outcome.meeting_id,
                            "title": outcome.title or titles.get(outcome.meeting_id, ""),
                            "sub_state": sub_state,
                            "error": outcome.error,
                        },
                    )
                )

    return purge_runner


def _replay_purge_states(op: Operation) -> tuple[list[dict[str, object]], int, int]:
    """Walk ``op.replay_buffer()`` and produce per-meeting purge state rows.

    Returns ``(rows, deleted_count, failed_count)`` where ``rows`` preserves
    the operation's slot ordering. Mirrors ``_replay_meeting_states`` but for
    the purge sub-state vocabulary (``verifying / deleting / done / failed``).
    """
    state_by_id: dict[str, dict[str, object]] = {}
    for slot in op.meetings:
        state_by_id[slot.meeting_id] = {
            "id": slot.meeting_id,
            "title": slot.title,
            "sub_state": slot.sub_state,
            "error": slot.error,
        }
    for evt in op.replay_buffer():
        if evt.kind != "meeting_state":
            continue
        mid = str(evt.data.get("id", ""))
        if not mid:
            continue
        row = state_by_id.setdefault(
            mid,
            {"id": mid, "title": "", "sub_state": "queued", "error": None},
        )
        row["title"] = evt.data.get("title") or row.get("title") or ""
        row["sub_state"] = evt.data.get("sub_state") or row.get("sub_state") or "queued"
        if "error" in evt.data:
            row["error"] = evt.data.get("error")
    rows = list(state_by_id.values())
    deleted = sum(1 for r in rows if r["sub_state"] == "done")
    failed = sum(1 for r in rows if r["sub_state"] == "failed")
    return rows, deleted, failed


@router.get("/cleanup/purge")
async def step4_preflight(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the purge preflight: count + numbered list + typed-count gate."""
    state = wizard_session.get_state(_store(request), _sid(request))
    selected_ids = list(state.get("selected_ids") or [])
    if not selected_ids:
        return _redirect("/cleanup/archive/done")

    meetings = await _selected_meetings(deps, selected_ids)
    if not meetings:
        # All previously-selected meetings have vanished (e.g. deleted directly
        # in Fireflies between the archive step and now).  Bounce back to review
        # so the user can re-filter rather than being shown a "0 meetings" purge
        # confirmation that would be a no-op.
        return _redirect("/cleanup/review?error=empty-selection")
    meetings = _meetings_for_step4(meetings, deps.config.fireflies.user_email)
    return _templates(request).TemplateResponse(
        request,
        "cleanup/step4_purge_preflight.html",
        {
            "step": "purge",
            "count": len(meetings),
            "meetings": meetings,
            "error": None,
        },
    )


@router.post("/cleanup/purge/start")
async def step4_start(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Kick off the purge operation; redirect to the in-progress view.

    Server-side double-checks the typed-count confirmation: the form value
    must equal the selection size as a string-int comparison. Mismatch or
    absence re-renders the preflight with status 422 and a banner.
    """
    state = wizard_session.get_state(_store(request), _sid(request))
    selected_ids = list(state.get("selected_ids") or [])
    if not selected_ids:
        return _redirect("/cleanup/archive/done")

    meetings = await _selected_meetings(deps, selected_ids)
    if not meetings:
        # All previously-selected meetings have vanished between the archive step
        # and now.  Bounce back to review rather than launching a no-op purge.
        return _redirect("/cleanup/review?error=empty-selection")
    count = len(meetings)

    form = await request.form()
    confirmed_raw = form.get("confirmed_count")
    confirmed = str(confirmed_raw).strip() if confirmed_raw is not None else ""
    if confirmed != str(count):
        return _templates(request).TemplateResponse(
            request,
            "cleanup/step4_purge_preflight.html",
            {
                "step": "purge",
                "count": count,
                "meetings": meetings,
                "error": "Type the count to confirm.",
            },
            status_code=422,
        )

    runner = _make_purge_runner(deps=deps, meetings=meetings)
    try:
        op = await _registry(request).start(
            kind=OperationKind.PURGE,
            meeting_ids=[m.meeting_id for m in meetings],
            runner=runner,
        )
    except SameKindAlreadyRunning:
        return _templates(request).TemplateResponse(
            request,
            "cleanup/step4_purge_preflight.html",
            {
                "step": "purge",
                "count": count,
                "meetings": meetings,
                "error": "Another purge operation is already running. Wait for it to complete.",
            },
            status_code=409,
        )

    _set_wizard(request, step="purge", operation_id=op.id)
    return _redirect("/cleanup/purge/in-progress")


@router.get("/cleanup/purge/in-progress")
async def step4_in_progress(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the live purge progress view; redirect when missing/terminal."""
    state = wizard_session.get_state(_store(request), _sid(request))
    op_id = state.get("operation_id")
    if not op_id:
        return _redirect("/cleanup/purge")
    try:
        op = _registry(request).get(op_id)
    except KeyError:
        return _redirect("/cleanup/purge")
    if op.state in {"succeeded", "failed", "cancelled"}:
        return _redirect("/cleanup/purge/done")

    rows, completed_done, completed_failed = _replay_purge_states(op)
    completed = completed_done + completed_failed
    total = max(op.total, len(rows))
    progress_pct = round(100.0 * completed / total) if total else 0
    return _templates(request).TemplateResponse(
        request,
        "cleanup/step4_purge_in_progress.html",
        {
            "step": "purge",
            "op_id": op.id,
            "meetings": rows,
            "total": total,
            "completed": completed,
            "progress_pct": progress_pct,
        },
    )


@router.get("/cleanup/purge/done")
async def step4_done(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Render the post-purge summary; redirect when running or missing."""
    state = wizard_session.get_state(_store(request), _sid(request))
    op_id = state.get("operation_id")
    if not op_id:
        return _redirect("/cleanup/purge")
    try:
        op = _registry(request).get(op_id)
    except KeyError:
        return _redirect("/cleanup/purge")
    if op.state == "running":
        return _redirect("/cleanup/purge/in-progress")

    rows, deleted, failed = _replay_purge_states(op)
    return _templates(request).TemplateResponse(
        request,
        "cleanup/step4_purge_done.html",
        {
            "step": "purge",
            "op_id": op.id,
            "meetings": rows,
            "deleted_count": deleted,
            "failed_count": failed,
        },
    )


@router.post("/cleanup/mark-deleted")
async def step4_mark_deleted(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Reconcile the wizard's archived meetings as deleted-in-Fireflies.

    The Step 4 handoff design assumes the user has bulk-deleted the
    selected meetings via Fireflies' web UI (the only path that bypasses
    the per-account API daily-quota cap). This route walks the wizard's
    ``selected_ids``, transitions each row currently in ARCHIVED or
    DELETED_FAILED to DELETED with reason ``manual_external_delete_via_wizard``
    in state_log, and renders a summary page.

    Skipped rows: any selected_id whose state is no longer ARCHIVED /
    DELETED_FAILED is counted as skipped rather than failed — the most
    common cause is a sync tick reconciling the row before this POST,
    which is benign.

    Status codes:
      - 303 (redirect to /cleanup/archive/done) when the wizard has no
        selected_ids — the user navigated here directly.
      - 303 (redirect to /cleanup/review?error=empty-selection) when the
        selected meetings have all vanished from the cache between
        archive and now.
      - 200 + the mark-deleted summary page on success.
    """
    state = wizard_session.get_state(_store(request), _sid(request))
    selected_ids = list(state.get("selected_ids") or [])
    if not selected_ids:
        return _redirect("/cleanup/archive/done")

    meetings = await _selected_meetings(deps, selected_ids)
    if not meetings:
        return _redirect("/cleanup/review?error=empty-selection")
    # Mirror the Step 4 preflight: only act on meetings the user actually
    # saw on the handoff page. Non-host rows are auto-marked DELETED at
    # archive time, so feeding them through here would inflate the
    # ``skipped`` count and confuse the summary. Sort matches the page
    # order for deterministic state_log ordering.
    meetings = _meetings_for_step4(meetings, deps.config.fireflies.user_email)

    eligible_states = {MeetingState.ARCHIVED, MeetingState.DELETED_FAILED}
    now = deps.clock.now()
    marked = 0
    skipped = 0
    for meeting in meetings:
        rec = deps.manifest.get(meeting.meeting_id)
        if rec is None or rec.state not in eligible_states:
            skipped += 1
            continue
        try:
            deps.manifest.transition(
                meeting.meeting_id,
                to=MeetingState.DELETED,
                at=now,
                details={"reason": "manual_external_delete_via_wizard"},
            )
        except IllegalStateTransition:
            # Race window — sync may have reconciled this row between our
            # ``get`` and the transition. Treat as skipped, not an error.
            logger.warning(
                "step4_mark_deleted: skipped %s (state changed mid-run)",
                meeting.meeting_id,
            )
            skipped += 1
            continue
        marked += 1

    return _templates(request).TemplateResponse(
        request,
        "cleanup/step4_mark_deleted_done.html",
        {
            "step": "purge",
            "marked_count": marked,
            "skipped_count": skipped,
            "total_count": len(meetings),
        },
    )


@router.post("/cleanup/purge/finalize")
async def step4_finalize(request: Request) -> Response:
    """Clear the wizard slice and redirect to the dashboard."""
    store = _store(request)
    sid = _sid(request)
    # Clear the wizard slice entirely. ``set_state`` shallow-merges, so we
    # explicitly write an empty dict to drop the slice keys.
    store.update(sid, {"wizard": {}})
    return _redirect("/")


@router.post("/cleanup/purge/restart")
async def step4_restart(request: Request) -> Response:
    """Reset the wizard back to Step 1, preserving filters as a starting point."""
    state = wizard_session.get_state(_store(request), _sid(request))
    new_state = wizard_session.WizardState(
        step="filter",
        filters=state.get("filters", {}),
        selected_ids=[],
        operation_id=None,
    )
    wizard_session.set_state(_store(request), _sid(request), new_state)
    return _redirect("/cleanup")


# Re-export ScanFilters so future wizard steps can import from one place.
__all__ = ["ScanFilters", "router"]
