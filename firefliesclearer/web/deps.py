"""FastAPI Depends() providers — request-scoped lookups for app.state services."""

from __future__ import annotations

from fastapi import Request

from firefliesclearer.web.lifecycle import HeartbeatTracker, ShutdownCoordinator


def get_tracker(request: Request) -> HeartbeatTracker:
    tracker: HeartbeatTracker = request.app.state.tracker
    return tracker


def get_shutdown_coordinator(request: Request) -> ShutdownCoordinator:
    coord: ShutdownCoordinator = request.app.state.shutdown_coordinator
    return coord
