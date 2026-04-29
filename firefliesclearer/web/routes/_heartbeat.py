"""POST /_alive — keepalive ping from the browser."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from firefliesclearer.web.deps import get_tracker
from firefliesclearer.web.lifecycle import HeartbeatTracker

router = APIRouter()


@router.post("/_alive")
async def alive(tracker: HeartbeatTracker = Depends(get_tracker)) -> Response:  # noqa: B008
    tracker.ping()
    return Response(status_code=204)
