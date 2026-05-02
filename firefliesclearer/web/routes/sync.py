"""Sync routes — manual trigger endpoint and status polling endpoint."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from firefliesclearer.application.sync_service import SyncMode, SyncService, SyncTrigger
from firefliesclearer.web.deps import get_deps

# Manual triggers permitted from the public POST /sync/now endpoint.
# 'scheduled' / 'bootstrap' are reserved for the scheduler / first-run flow.
_ALLOWED_MANUAL_TRIGGERS: frozenset[str] = frozenset({"manual_review", "manual_settings"})

logger = logging.getLogger(__name__)
router = APIRouter()


@dataclass(slots=True)
class CurrentSyncSnapshot:
    """Mutable mirror of an in-flight sync run.

    The route stores this on ``app.state.current_sync`` while a sync task is
    running. It is intentionally mutable: the runner overwrites the live
    counters once :class:`SyncOutcome` is returned. Tests may construct one
    directly to simulate an in-flight run.

    ``last_page_size`` carries the size of the most recently fetched page so
    :func:`estimate_total` can derive an "approximately N" upper bound for
    the bootstrap progress banner.
    """

    run_id: int
    mode: str
    trigger_source: str
    started_at: datetime
    meetings_seen: int = 0
    meetings_added: int = 0
    meetings_updated: int = 0
    meetings_gone: int = 0
    last_page_size: int = 0


@router.get("/sync/status")
async def status_endpoint(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> JSONResponse:
    return JSONResponse(_build_status_dict(request, deps))


@router.get("/sync/status/banner")
async def status_banner(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """HTMX-polled endpoint that renders the banner partial as HTML."""
    templates: Jinja2Templates = request.app.state.templates
    sync_status = _build_status_dict(request, deps)
    return templates.TemplateResponse(
        request,
        "partials/_sync_banner.html",
        {"sync_status": sync_status},
    )


def maybe_status_for_template(request: Request, deps: SimpleNamespace) -> dict[str, Any] | None:
    """Return the sync-banner status dict when [sync] enabled, else None.

    Used by other route modules (cleanup, dashboard) to thread ``sync_status``
    into their template context without coupling them to the JSON endpoint.
    """
    config = getattr(deps, "config", None)
    sync_cfg = getattr(config, "sync", None) if config is not None else None
    if sync_cfg is None or not getattr(sync_cfg, "enabled", False):
        return None
    return _build_status_dict(request, deps)


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
        from firefliesclearer.application.sync_service import estimate_total

        estimated_total = estimate_total(
            seen=current.meetings_seen,
            last_page_size=current.last_page_size,
        )
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
            "estimated_total": estimated_total,
            "is_bootstrap": current.trigger_source == "bootstrap",
            "last_run": last_run_dict,
        }
    return {"state": "idle", "last_run": last_run_dict}


@router.post("/sync/now")
async def trigger_sync(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> JSONResponse:
    """Acquire ``sync_lock`` and spawn a background SyncService run.

    Returns:
        202 + status payload on success.
        409 + ``current_run_id`` when a sync is already in flight.
        422 on invalid ``mode`` or ``trigger``.
    """
    form = await request.form()
    mode = str(form.get("mode", "incremental"))
    trigger = str(form.get("trigger", "manual_review"))

    try:
        sync_mode = SyncMode(mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid mode: {mode!r}") from exc

    if trigger not in _ALLOWED_MANUAL_TRIGGERS:
        raise HTTPException(status_code=422, detail=f"Invalid trigger: {trigger!r}")
    sync_trigger = SyncTrigger(trigger)

    sync_lock: asyncio.Lock = request.app.state.sync_lock
    if sync_lock.locked():
        current = getattr(request.app.state, "current_sync", None)
        return JSONResponse(
            {"current_run_id": current.run_id if current else None},
            status_code=409,
        )

    await sync_lock.acquire()
    snapshot = CurrentSyncSnapshot(
        run_id=0,  # filled in by the runner once start_sync_run returns
        mode=mode,
        trigger_source=trigger,
        started_at=deps.clock.now(),
    )
    request.app.state.current_sync = snapshot

    def _update_snapshot(
        seen: int, added: int, updated: int, gone: int, last_page_size: int
    ) -> None:
        snap = getattr(request.app.state, "current_sync", None)
        if snap is None:
            return
        snap.meetings_seen = seen
        snap.meetings_added = added
        snap.meetings_updated = updated
        snap.meetings_gone = gone
        snap.last_page_size = last_page_size

    # Prefer a SyncService already on app.state (set by the scheduler), fall
    # back to building one ad-hoc from deps. The ad-hoc service gets a
    # snapshot callback so the live banner reflects per-page progress.
    service: SyncService | None = getattr(request.app.state, "sync_service", None)
    if service is None:
        service = SyncService(
            repo=deps.client,
            manifest=deps.manifest,
            clock=deps.clock,
            snapshot_callback=_update_snapshot,
        )

    async def _runner() -> None:
        try:
            outcome = await service.run(mode=sync_mode, trigger=sync_trigger)
            snapshot.run_id = outcome.run_id
            snapshot.meetings_seen = outcome.meetings_seen
            snapshot.meetings_added = outcome.meetings_added
            snapshot.meetings_updated = outcome.meetings_updated
            snapshot.meetings_gone = outcome.meetings_gone
        except Exception:
            # Log + release; the scheduler / next manual trigger will retry.
            logger.exception("Sync task failed")
        finally:
            request.app.state.current_sync = None
            sync_lock.release()

    # Park the task on app.state so it isn't GC'd mid-flight.
    task = asyncio.create_task(_runner())
    request.app.state.current_sync_task = task

    return JSONResponse(
        {
            "state": "running",
            "mode": mode,
            "trigger_source": trigger,
            "started_at": snapshot.started_at.isoformat(),
        },
        status_code=202,
    )
