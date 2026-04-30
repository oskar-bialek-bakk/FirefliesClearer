"""Presets CRUD routes — /presets (list, new, edit, delete).

Task 6.4: Ships the /presets page + save-from-wizard form.

Deferred (follow-up tasks):
- HTMX modal for edit (full-page forms used instead, simpler for now).
- "Run cleanup with this preset" action is a plain link to /cleanup?preset=NAME;
  the full wiring happens in the cleanup route update in this same task.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.responses import Response

from firefliesclearer.application.preset_service import (
    PresetAlreadyExistsError,
    PresetNotFoundError,
    PresetService,
)
from firefliesclearer.infra.config import ScanFiltersModel
from firefliesclearer.web.wizard_session import parse_filter_form

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _templates(request: Request) -> Jinja2Templates:
    t: Jinja2Templates = request.app.state.templates
    return t


def _config_path(request: Request) -> Path:
    p: Path = request.app.state.config_path
    return p


def _svc(request: Request) -> PresetService:
    return PresetService(_config_path(request))


def _redirect(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=303)


def _filters_from_form(form: dict[str, str | None]) -> ScanFiltersModel:
    """Parse filter form fields into a ``ScanFiltersModel`` for storage."""
    from firefliesclearer.web.wizard_session import filters_to_dict

    scan_filters = parse_filter_form(form)
    d = filters_to_dict(scan_filters)
    return ScanFiltersModel.model_validate(d)


# ---------------------------------------------------------------------------
# GET /presets — list
# ---------------------------------------------------------------------------


@router.get("/presets")
async def presets_list(request: Request) -> Response:
    """Render the presets list page."""
    svc = _svc(request)
    presets = svc.list()
    return _templates(request).TemplateResponse(
        request,
        "presets/list.html",
        {"presets": presets},
    )


# ---------------------------------------------------------------------------
# GET /presets/new — new preset form
# ---------------------------------------------------------------------------


@router.get("/presets/new")
async def presets_new_form(request: Request) -> Response:
    """Render the new-preset form."""
    return _templates(request).TemplateResponse(
        request,
        "presets/new.html",
        {
            "error": None,
            "form_values": {},
        },
    )


# ---------------------------------------------------------------------------
# POST /presets — create
# ---------------------------------------------------------------------------


@router.post("/presets")
async def presets_create(request: Request) -> Response:
    """Create a new preset from form data. Redirect to /presets on success."""
    form = dict(await request.form())
    name = str(form.get("preset_name", "")).strip()
    description = str(form.get("preset_description", "")).strip()
    default = str(form.get("preset_default", "")) in {"on", "true", "1", "yes"}

    if not name:
        return _templates(request).TemplateResponse(
            request,
            "presets/new.html",
            {
                "error": "Preset name is required.",
                "form_values": form,
            },
        )

    filters_model = _filters_from_form(
        {k: str(v) if v is not None else None for k, v in form.items()}
    )

    svc = _svc(request)
    try:
        svc.create(name, description, filters_model, default=default)
    except PresetAlreadyExistsError:
        return _templates(request).TemplateResponse(
            request,
            "presets/new.html",
            {
                "error": f'A preset named "{name}" already exists.',
                "form_values": form,
            },
            status_code=200,
        )
    except ValidationError as exc:
        return _templates(request).TemplateResponse(
            request,
            "presets/new.html",
            {
                "error": f"Validation error: {exc}",
                "form_values": form,
            },
        )

    return _redirect("/presets")


# ---------------------------------------------------------------------------
# GET /presets/{name}/edit — edit form
# ---------------------------------------------------------------------------


@router.get("/presets/{name}/edit")
async def presets_edit_form(request: Request, name: str) -> Response:
    """Render the edit form for an existing preset."""
    svc = _svc(request)
    try:
        preset = svc.get(name)
    except PresetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Preset {name!r} not found.") from exc
    return _templates(request).TemplateResponse(
        request,
        "presets/edit.html",
        {
            "preset": preset,
            "error": None,
        },
    )


# ---------------------------------------------------------------------------
# POST /presets/{name} — update
# ---------------------------------------------------------------------------


@router.post("/presets/{name}")
async def presets_update(request: Request, name: str) -> Response:
    """Apply a partial update to an existing preset."""
    svc = _svc(request)
    try:
        svc.get(name)
    except PresetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Preset {name!r} not found.") from exc

    form = dict(await request.form())
    description = str(form.get("preset_description", "")).strip()
    default_raw = str(form.get("preset_default", ""))
    # HTML checkboxes are absent when unchecked, so if key missing → False.
    new_default: bool = default_raw in {"on", "true", "1", "yes"}

    filters_model = _filters_from_form(
        {k: str(v) if v is not None else None for k, v in form.items()}
    )

    try:
        svc.update(name, description=description, filters=filters_model, default=new_default)
    except PresetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Preset {name!r} not found.") from exc

    return _redirect("/presets")


# ---------------------------------------------------------------------------
# POST /presets/{name}/delete — delete
# ---------------------------------------------------------------------------


@router.post("/presets/{name}/delete")
async def presets_delete(request: Request, name: str) -> Response:
    """Delete a preset. 404 if not found."""
    svc = _svc(request)
    try:
        svc.delete(name)
    except PresetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Preset {name!r} not found.") from exc

    return _redirect("/presets")
