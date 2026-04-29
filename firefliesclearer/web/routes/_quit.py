"""POST /_quit — explicit shutdown request from the sidebar."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from firefliesclearer.web.deps import get_shutdown_coordinator
from firefliesclearer.web.lifecycle import ShutdownCoordinator

router = APIRouter()


@router.post("/_quit")
async def quit_app(
    coord: ShutdownCoordinator = Depends(get_shutdown_coordinator),  # noqa: B008
) -> Response:
    coord.request_quit()
    return Response(status_code=204)
