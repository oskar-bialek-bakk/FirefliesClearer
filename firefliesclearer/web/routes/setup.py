"""Setup wizard — 4-step first-run configuration flow.

Renders welcome -> api-key -> archive-root -> defaults; on final submit,
writes config.toml atomically via SetupService and redirects to /.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from platformdirs import user_documents_dir
from starlette.responses import Response

from firefliesclearer.application.setup_service import (
    ApiUnavailableError,
    InvalidApiKeyError,
    SetupService,
    SetupValues,
)
from firefliesclearer.web.sessions import SessionStore

router = APIRouter(prefix="/setup")


def _sid(request: Request) -> str:
    return request.cookies.get("ffc_session", "")


def _store(request: Request) -> SessionStore:
    store: SessionStore = request.app.state.session_store
    return store


def _service(request: Request) -> SetupService:
    factory: Any = request.app.state.repo_factory
    if factory is None:
        raise HTTPException(
            status_code=500,
            detail="Server is missing a repo_factory; cannot verify API keys.",
        )
    return SetupService(repo_factory=factory)


def _templates(request: Request) -> Jinja2Templates:
    t: Jinja2Templates = request.app.state.templates
    return t


@router.get("/welcome")
async def welcome(request: Request) -> Response:
    return _templates(request).TemplateResponse(request, "setup/welcome.html", {})


@router.get("/api-key")
async def api_key_form(request: Request, error: str | None = None) -> Response:
    return _templates(request).TemplateResponse(request, "setup/api_key.html", {"error": error})


@router.post("/api-key")
async def api_key_submit(request: Request, api_key: str = Form(...)) -> Response:
    svc = _service(request)
    try:
        email = await svc.verify_api_key(api_key)
    except InvalidApiKeyError as exc:
        return _templates(request).TemplateResponse(
            request,
            "setup/api_key.html",
            {"error": f"Fireflies rejected this key: {exc}"},
        )
    except ApiUnavailableError as exc:
        msg = str(exc).lower()
        if "too_many_requests" in msg or "rate" in msg:
            friendly = (
                "Fireflies is currently rate-limiting this account. The key may be "
                "fine. Wait until the retry window resets (usually next UTC midnight) "
                "and try again."
            )
        else:
            friendly = (
                f"Could not reach Fireflies to verify the key: {exc}. "
                "Check your network connection and try again."
            )
        return _templates(request).TemplateResponse(
            request,
            "setup/api_key.html",
            {"error": friendly},
        )
    _store(request).update(_sid(request), {"api_key": api_key, "email": email})
    return RedirectResponse("/setup/archive-root", status_code=303)


@router.get("/archive-root")
async def archive_root_form(request: Request, error: str | None = None) -> Response:
    default_root = Path(user_documents_dir()) / "firefliesclearer-archive"
    return _templates(request).TemplateResponse(
        request,
        "setup/archive_root.html",
        {"default_root": default_root, "error": error},
    )


@router.post("/archive-root")
async def archive_root_submit(
    request: Request,
    archive_root: str = Form(...),
    create: str = Form(""),
) -> Response:
    p = Path(archive_root).expanduser()
    if not p.exists():
        if create != "yes":
            return _templates(request).TemplateResponse(
                request,
                "setup/archive_root.html",
                {
                    "default_root": p,
                    "error": "Folder does not exist. Tick 'Create this folder' to make it.",
                },
            )
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _templates(request).TemplateResponse(
                request,
                "setup/archive_root.html",
                {"default_root": p, "error": f"Cannot create: {exc}"},
            )
    if not p.is_dir():
        return _templates(request).TemplateResponse(
            request,
            "setup/archive_root.html",
            {"default_root": p, "error": "Path is not a directory."},
        )
    if not _is_writable(p):
        return _templates(request).TemplateResponse(
            request,
            "setup/archive_root.html",
            {"default_root": p, "error": "Folder is not writable."},
        )

    _store(request).update(_sid(request), {"archive_root": str(p)})
    return RedirectResponse("/setup/defaults", status_code=303)


@router.get("/defaults")
async def defaults_form(request: Request) -> Response:
    return _templates(request).TemplateResponse(request, "setup/defaults.html", {})


@router.post("/defaults")
async def defaults_submit(
    request: Request,
    age_days: int = Form(90),
    concurrency: int = Form(3),
) -> Response:
    sid = _sid(request)
    state = _store(request).get(sid)
    api_key: str | None = state.get("api_key")
    archive_root: str | None = state.get("archive_root")
    # ``email`` was captured during the api-key step from the same
    # ``ping_user`` call that validated the key. Persisting it here means
    # the runtime never has to refetch — see Phase 6 in CLAUDE.md.
    email: str | None = state.get("email")
    if not api_key or not archive_root:
        raise HTTPException(
            status_code=400, detail="Setup state lost. Restart from /setup/welcome."
        )

    svc = _service(request)
    config_path: Path | None = request.app.state.config_path
    if config_path is None:
        raise HTTPException(
            status_code=500,
            detail="Server is missing a config_path; cannot write config.",
        )
    svc.write_config(
        config_path,
        SetupValues(
            api_key=api_key,
            archive_root=Path(archive_root),
            default_age_days=age_days,
            concurrency=concurrency,
            user_email=email,
        ),
        force=False,
    )
    _store(request).delete(sid)
    return RedirectResponse("/", status_code=303)


def _is_writable(p: Path) -> bool:
    test = p / ".ffc_write_test"
    try:
        test.write_text("ok")
        test.unlink()
        return True
    except OSError:
        return False
