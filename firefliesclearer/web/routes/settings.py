"""Settings page — /settings with 6 collapsible sections.

Phase 8 — Task 31: Connection, Archive, Defaults, Presets-link,
Logs & Data, Danger Zone.

Each section has its own POST endpoint.  All saves use atomic TOML
writes.  The config dict is built fresh each time — existing config is
loaded, mutated into a new dict (never in-place), then written back.
"""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from firefliesclearer.application.setup_service import (
    InvalidApiKeyError,
    SetupService,
)
from firefliesclearer.infra.atomic_toml import write_atomic_toml
from firefliesclearer.infra.open_folder import open_folder

router = APIRouter(prefix="/settings")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _templates(request: Request) -> Jinja2Templates:
    t: Jinja2Templates = request.app.state.templates
    return t


def _config_path(request: Request) -> Path:
    p: Path = request.app.state.config_path
    return p


def _svc(request: Request) -> SetupService:
    factory: Any = request.app.state.repo_factory
    return SetupService(repo_factory=factory)


def _load_raw(config_path: Path) -> dict[str, Any]:
    """Load config TOML as a raw dict (for atomic partial updates)."""
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def _save_raw(config_path: Path, payload: dict[str, Any]) -> None:
    """Atomically write *payload* back to *config_path*."""
    write_atomic_toml(config_path, payload)


def _is_writable(p: Path) -> bool:
    test = p / ".ffc_write_test"
    try:
        test.write_text("ok")
        test.unlink()
        return True
    except OSError:
        return False


def _archive_has_content(root: Path) -> bool:
    """Return True if the archive root contains any files."""
    if not root.exists():
        return False
    return any(root.rglob("*"))


# ---------------------------------------------------------------------------
# GET /settings
# ---------------------------------------------------------------------------


@router.get("")
async def settings_page(request: Request) -> Response:
    """Render the settings page with all sections and current values."""
    cfg_path = _config_path(request)
    raw = _load_raw(cfg_path)

    run = raw.get("run", {})
    archive = raw.get("archive", {})

    ctx: dict[str, Any] = {
        "archive_root": archive.get("root_dir", ""),
        "concurrency": run.get("concurrency", 3),
        "default_age_days": run.get("default_age_days", 180),
        "delete_confirmation_threshold": run.get("delete_confirmation_threshold", 10),
        "log_retention_days": run.get("log_retention_days", 30),
        "flash": None,
        "errors": {},
    }
    return _templates(request).TemplateResponse(request, "settings.html", ctx)


# ---------------------------------------------------------------------------
# POST /settings/connection/test  — HTMX fragment
# ---------------------------------------------------------------------------


@router.post("/connection/test")
async def settings_connection_test(request: Request) -> Response:
    """Test the submitted API key and return an HTMX status fragment."""
    form = dict(await request.form())
    api_key = str(form.get("api_key", "")).strip()

    svc = _svc(request)
    try:
        email = await svc.verify_api_key(api_key)
        html = f'<span class="settings-connection-ok">Connected as <strong>{email}</strong></span>'
    except (InvalidApiKeyError, OSError, ValueError) as exc:
        html = f'<span class="settings-connection-err">Connection failed: {exc}</span>'

    from fastapi.responses import HTMLResponse

    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# POST /settings/connection  — save new API key
# ---------------------------------------------------------------------------


@router.post("/connection")
async def settings_connection_save(request: Request) -> Response:
    """Validate the new API key then save it to config."""
    form = dict(await request.form())
    api_key = str(form.get("api_key", "")).strip()

    if not api_key:
        return _render_settings(
            request,
            errors={"connection": "API key must not be empty."},
        )

    svc = _svc(request)
    try:
        await svc.verify_api_key(api_key)
    except (InvalidApiKeyError, OSError, ValueError) as exc:
        return _render_settings(
            request,
            errors={"connection": f"Key rejected: {exc}"},
        )

    cfg_path = _config_path(request)
    raw = _load_raw(cfg_path)
    new_raw = {**raw, "fireflies": {**raw.get("fireflies", {}), "api_key": api_key}}
    _save_raw(cfg_path, new_raw)
    return _render_settings(request, flash="API key updated.")


# ---------------------------------------------------------------------------
# POST /settings/archive  — save archive root
# ---------------------------------------------------------------------------


@router.post("/archive")
async def settings_archive_save(request: Request) -> Response:
    """Validate archive root path and save; warn if old root has content."""
    form = dict(await request.form())
    new_root_str = str(form.get("root_dir", "")).strip()

    if not new_root_str:
        return _render_settings(request, errors={"archive": "Archive root must not be empty."})

    new_root = Path(new_root_str).expanduser()

    # Existence / create logic
    create = str(form.get("create", "")).strip()
    if not new_root.exists():
        if create != "yes":
            return _render_settings(
                request,
                errors={
                    "archive": ("Folder does not exist. Tick 'Create this folder' to make it.")
                },
            )
        try:
            new_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _render_settings(request, errors={"archive": f"Cannot create folder: {exc}"})

    if not new_root.is_dir():
        return _render_settings(request, errors={"archive": "Path is not a directory."})
    if not _is_writable(new_root):
        return _render_settings(request, errors={"archive": "Folder is not writable."})

    cfg_path = _config_path(request)
    raw = _load_raw(cfg_path)
    old_root_str: str = raw.get("archive", {}).get("root_dir", "")
    old_root = Path(old_root_str) if old_root_str else None

    move_warning: str | None = None
    if old_root and str(old_root) != str(new_root) and _archive_has_content(old_root):
        move_warning = (
            f"Existing archive at `{old_root}` is NOT moved automatically. "
            "Move it manually before continuing if you want to preserve history."
        )

    new_raw: dict[str, Any] = {
        **raw,
        "archive": {**raw.get("archive", {}), "root_dir": str(new_root)},
    }
    _save_raw(cfg_path, new_raw)

    flash = "Archive root updated."
    return _render_settings(request, flash=flash, move_warning=move_warning)


# ---------------------------------------------------------------------------
# POST /settings/defaults  — save run defaults
# ---------------------------------------------------------------------------


@router.post("/defaults")
async def settings_defaults_save(request: Request) -> Response:
    """Validate and save concurrency, default_age_days, delete_confirmation_threshold."""
    form = dict(await request.form())

    errors: dict[str, str] = {}

    try:
        concurrency = int(str(form.get("concurrency", 3)))
    except (ValueError, TypeError):
        errors["defaults"] = "Concurrency must be an integer."
        return _render_settings(request, errors=errors)

    if not 1 <= concurrency <= 10:
        errors["defaults"] = "Concurrency must be between 1 and 10."
        return _render_settings(request, errors=errors)

    try:
        default_age_days = int(str(form.get("default_age_days", 180)))
    except (ValueError, TypeError):
        errors["defaults"] = "Default age days must be an integer."
        return _render_settings(request, errors=errors)

    if default_age_days < 1:
        errors["defaults"] = "Default age days must be at least 1."
        return _render_settings(request, errors=errors)

    try:
        delete_confirmation_threshold = int(str(form.get("delete_confirmation_threshold", 10)))
    except (ValueError, TypeError):
        errors["defaults"] = "Delete confirmation threshold must be an integer."
        return _render_settings(request, errors=errors)

    if delete_confirmation_threshold < 0:
        errors["defaults"] = "Delete confirmation threshold must be non-negative."
        return _render_settings(request, errors=errors)

    cfg_path = _config_path(request)
    raw = _load_raw(cfg_path)
    new_run = {
        **raw.get("run", {}),
        "concurrency": concurrency,
        "default_age_days": default_age_days,
        "delete_confirmation_threshold": delete_confirmation_threshold,
    }
    _save_raw(cfg_path, {**raw, "run": new_run})
    return _render_settings(request, flash="Defaults saved.")


# ---------------------------------------------------------------------------
# POST /settings/logs/retention  — save log retention days
# ---------------------------------------------------------------------------


@router.post("/logs/retention")
async def settings_logs_retention_save(request: Request) -> Response:
    """Save log_retention_days to config."""
    form = dict(await request.form())

    try:
        log_retention_days = int(str(form.get("log_retention_days", 30)))
    except (ValueError, TypeError):
        return _render_settings(request, errors={"logs": "Log retention days must be an integer."})

    if log_retention_days < 1:
        return _render_settings(request, errors={"logs": "Log retention days must be at least 1."})

    cfg_path = _config_path(request)
    raw = _load_raw(cfg_path)
    new_run = {**raw.get("run", {}), "log_retention_days": log_retention_days}
    _save_raw(cfg_path, {**raw, "run": new_run})
    return _render_settings(request, flash="Log retention saved.")


# ---------------------------------------------------------------------------
# GET /settings/logs/today  — HTMX fragment: today's log
# ---------------------------------------------------------------------------


@router.get("/logs/today")
async def settings_logs_today(request: Request) -> Response:
    """Return an HTMX fragment containing today's log file (or empty state)."""
    cfg_path = _config_path(request)
    raw = _load_raw(cfg_path)
    archive_root = Path(raw.get("archive", {}).get("root_dir", ""))
    today = date.today().isoformat()
    log_file = archive_root / "logs" / f"{today}.log"

    content = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else None

    return _templates(request).TemplateResponse(
        request,
        "_log_viewer.html",
        {"log_content": content, "today": today},
    )


# ---------------------------------------------------------------------------
# POST /settings/open-archive-folder  — shell-out to file explorer
# ---------------------------------------------------------------------------


@router.post("/open-archive-folder")
async def settings_open_archive_folder(request: Request) -> Response:
    """Open the archive folder in the platform's file explorer.

    SECURITY: the path is read from config only, never from user input.
    """
    cfg_path = _config_path(request)
    raw = _load_raw(cfg_path)
    archive_root = Path(raw.get("archive", {}).get("root_dir", ""))

    if archive_root and archive_root.is_dir():
        open_folder(archive_root)

    from starlette.responses import Response as StarletteResponse

    return StarletteResponse(status_code=204)


# ---------------------------------------------------------------------------
# POST /settings/reset  — danger zone: delete config.toml
# ---------------------------------------------------------------------------


@router.post("/reset")
async def settings_reset(request: Request) -> Response:
    """Confirm with typed 'RESET' then delete config.toml and redirect to setup."""
    form = dict(await request.form())
    confirmation = str(form.get("confirmation_text", "")).strip()

    if confirmation != "RESET":
        return _render_settings(
            request,
            errors={"danger": "Type RESET exactly to confirm."},
        )

    cfg_path = _config_path(request)
    cfg_path.unlink(missing_ok=True)
    return RedirectResponse("/setup/welcome", status_code=303)


# ---------------------------------------------------------------------------
# Private: re-render full settings page with flash/errors
# ---------------------------------------------------------------------------


def _render_settings(
    request: Request,
    *,
    flash: str | None = None,
    errors: dict[str, str] | None = None,
    move_warning: str | None = None,
) -> Response:
    """Reload config and re-render settings.html with contextual feedback."""
    cfg_path = _config_path(request)
    raw = _load_raw(cfg_path)

    run = raw.get("run", {})
    archive = raw.get("archive", {})

    ctx: dict[str, Any] = {
        "archive_root": archive.get("root_dir", ""),
        "concurrency": run.get("concurrency", 3),
        "default_age_days": run.get("default_age_days", 180),
        "delete_confirmation_threshold": run.get("delete_confirmation_threshold", 10),
        "log_retention_days": run.get("log_retention_days", 30),
        "flash": flash,
        "errors": errors or {},
        "move_warning": move_warning,
    }
    return _templates(request).TemplateResponse(request, "settings.html", ctx)
