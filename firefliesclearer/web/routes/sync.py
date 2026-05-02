"""Sync routes — manual trigger endpoint and status polling endpoint."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from firefliesclearer.web.deps import get_deps

logger = logging.getLogger(__name__)
router = APIRouter()


@dataclass(slots=True)
class CurrentSyncSnapshot:
    """Mutable mirror of an in-flight sync run.

    The route stores this on ``app.state.current_sync`` while a sync task is
    running. It is intentionally mutable: the runner overwrites the live
    counters once :class:`SyncOutcome` is returned. Tests may construct one
    directly to simulate an in-flight run.
    """

    run_id: int
    mode: str
    trigger_source: str
    started_at: datetime
    meetings_seen: int = 0
    meetings_added: int = 0
    meetings_updated: int = 0
    meetings_gone: int = 0


@router.get("/sync/status")
async def status_endpoint(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> JSONResponse:
    return JSONResponse(_build_status_dict(request, deps))


def _build_status_dict(request: Request, deps: SimpleNamespace) -> dict[str, Any]:
    """Build the status payload shared by JSON + HTML banner endpoints."""
    current = getattr(request.app.state, "current_sync", None)
    last_run = deps.manifest.get_last_sync_run()
    last_run_dict: dict[str, Any] | None = None
    if last_run is not None:
        last_run_dict = {
            "id": last_run.id,
            "mode": last_run.mode,
            "outcome": last_run.outcome,
            "started_at": last_run.started_at.isoformat(),
            "finished_at": (last_run.finished_at.isoformat() if last_run.finished_at else None),
            "meetings_seen": last_run.meetings_seen,
            "meetings_added": last_run.meetings_added,
            "meetings_updated": last_run.meetings_updated,
            "meetings_gone": last_run.meetings_gone,
            "next_resume_at": (
                last_run.next_resume_at.isoformat() if last_run.next_resume_at else None
            ),
            "error_message": last_run.error_message,
        }
    if current is not None:
        return {
            "state": "running",
            "run_id": current.run_id,
            "mode": current.mode,
            "trigger_source": current.trigger_source,
            "started_at": current.started_at.isoformat(),
            "meetings_seen": current.meetings_seen,
            "meetings_added": current.meetings_added,
            "meetings_updated": current.meetings_updated,
            "meetings_gone": current.meetings_gone,
            "last_run": last_run_dict,
        }
    return {"state": "idle", "last_run": last_run_dict}
