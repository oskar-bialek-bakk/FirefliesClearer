"""FastAPI app factory."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from firefliesclearer import __version__
from firefliesclearer.infra.system_clock import SystemClock
from firefliesclearer.ports.clock import Clock
from firefliesclearer.web.lifecycle import HeartbeatTracker, ShutdownCoordinator
from firefliesclearer.web.routes import _heartbeat, _quit
from firefliesclearer.web.security import SecurityConfig, install_security

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def create_app(
    *,
    session_token: str | None = None,
    csrf_secret: str | None = None,
    clock: Clock | None = None,
    is_active_callable: Callable[[], bool] = lambda: False,
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

    install_security(app, SecurityConfig(session_token=session_token, csrf_secret=csrf_secret))

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(_heartbeat.router)
    app.include_router(_quit.router)

    @app.get("/")
    def home(request: Request) -> Response:
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "base.html",
            {"version": request.app.state.version},
        )

    return app
