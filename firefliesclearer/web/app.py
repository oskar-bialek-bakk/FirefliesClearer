"""FastAPI app factory."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from firefliesclearer import __version__
from firefliesclearer.infra.system_clock import SystemClock
from firefliesclearer.ports.clock import Clock
from firefliesclearer.ports.meeting_repository import MeetingRepository
from firefliesclearer.web.lifecycle import HeartbeatTracker, ShutdownCoordinator
from firefliesclearer.web.operations import OperationRegistry
from firefliesclearer.web.routes import (
    _heartbeat,
    _quit,
    cleanup,
    dashboard,
    history,
    presets,
    progress,
    settings,
    setup,
)
from firefliesclearer.web.security import SecurityConfig, install_security
from firefliesclearer.web.sessions import SessionStore

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def create_app(
    *,
    session_token: str | None = None,
    csrf_secret: str | None = None,
    clock: Clock | None = None,
    is_active_callable: Callable[[], bool] = lambda: False,
    config_path: Path | None = None,
    repo_factory: Callable[[str], MeetingRepository] | None = None,
) -> FastAPI:
    """Build the FastAPI app. Caller wires services into app.state.*."""
    clock = clock or SystemClock()
    session_token = session_token or secrets.token_urlsafe(24)
    csrf_secret = csrf_secret or secrets.token_urlsafe(32)

    app = FastAPI(title="FirefliesClearer")

    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    shutdown = ShutdownCoordinator(
        tracker=tracker,
        is_active=is_active_callable,
        clock=clock,
        poll_interval=timedelta(seconds=5),
    )

    app.state.tracker = tracker
    app.state.shutdown_coordinator = shutdown
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.version = __version__
    app.state.session_token = session_token
    app.state.config_path = config_path
    app.state.repo_factory = repo_factory
    app.state.session_store = SessionStore()
    app.state.operation_registry = OperationRegistry(clock=clock)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(_heartbeat.router)
    app.include_router(_quit.router)
    app.include_router(setup.router)
    app.include_router(dashboard.router)
    app.include_router(cleanup.router)
    app.include_router(presets.router)
    app.include_router(progress.router)
    app.include_router(history.router)
    app.include_router(settings.router)

    # CRITICAL: redirect middleware MUST be added BEFORE install_security
    # so it ends up INNERMOST in the middleware stack. Order in Starlette:
    # middleware added later wraps earlier ones, and outermost runs first.
    # Desired request order: SessionToken (auth) -> CSRF -> redirect -> route.
    # Achieve by adding: redirect first (innermost), then install_security
    # (which adds CSRF then SessionToken).
    @app.middleware("http")
    async def redirect_to_setup(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path.startswith(("/setup", "/static", "/_alive", "/_quit")):
            return await call_next(request)
        cfg: Path | None = request.app.state.config_path
        if cfg and cfg.exists():
            return await call_next(request)
        return RedirectResponse("/setup/welcome", status_code=303)

    # install_security must come AFTER the @app.middleware decoration above.
    install_security(app, SecurityConfig(session_token=session_token, csrf_secret=csrf_secret))

    return app
