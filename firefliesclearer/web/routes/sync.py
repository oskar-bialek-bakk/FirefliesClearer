"""Sync routes — manual trigger endpoint and status polling endpoint."""

from __future__ import annotations

import asyncio
import logging
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from firefliesclearer.application.sync_service import SyncMode, SyncService, SyncTrigger
from firefliesclearer.infra.atomic_toml import write_atomic_toml
from firefliesclearer.infra.sync_scheduler import MANUAL_SYNC_COOLDOWN
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


def make_scheduler_hooks(
    app_state: Any,
) -> tuple[
    Callable[[int, int, int, int, int], None],
    Callable[[str, str, datetime], None],
    Callable[[], None],
]:
    """Build the SyncService + run_scheduler callbacks that coordinate with
    ``app.state.sync_lock`` and ``app.state.current_sync``.

    Returns ``(snapshot_callback, on_run_started, on_run_finished)``:

    - ``snapshot_callback`` updates the live banner counters between pages.
    - ``on_run_started`` creates a fresh :class:`CurrentSyncSnapshot` so
      ``/sync/status`` reports the in-flight scheduled run instead of idle.
    - ``on_run_finished`` clears the snapshot when the tick ends.

    Without these, scheduler-driven runs are invisible to the UI and can
    race with /sync/now triggers (the lock is taken inside ``run_scheduler``).
    """

    def snapshot_callback(
        seen: int, added: int, updated: int, gone: int, last_page_size: int
    ) -> None:
        snap = getattr(app_state, "current_sync", None)
        if snap is None:
            return
        snap.meetings_seen = seen
        snap.meetings_added = added
        snap.meetings_updated = updated
        snap.meetings_gone = gone
        snap.last_page_size = last_page_size

    def on_run_started(mode: str, trigger: str, started_at: datetime) -> None:
        app_state.current_sync = CurrentSyncSnapshot(
            run_id=0,
            mode=mode,
            trigger_source=trigger,
            started_at=started_at,
        )

    def on_run_finished() -> None:
        app_state.current_sync = None

    return snapshot_callback, on_run_started, on_run_finished


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


def _format_local(value: datetime | None) -> str | None:
    """Render a tz-aware datetime in the server's local timezone for the UI.

    Templates show this string to the user; the JSON API still emits ISO-8601
    UTC alongside, so machine consumers are unchanged.
    """
    if value is None:
        return None
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


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
            "started_at_local": _format_local(last_run.started_at),
            "finished_at": (last_run.finished_at.isoformat() if last_run.finished_at else None),
            "finished_at_local": _format_local(last_run.finished_at),
            "meetings_seen": last_run.meetings_seen,
            "meetings_added": last_run.meetings_added,
            "meetings_updated": last_run.meetings_updated,
            "meetings_gone": last_run.meetings_gone,
            "next_resume_at": (
                last_run.next_resume_at.isoformat() if last_run.next_resume_at else None
            ),
            "next_resume_at_local": _format_local(last_run.next_resume_at),
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
) -> Response:
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

    # Phase 5: cooldown — prevent click-spamming the manual sync button
    # from burning daily API quota. The scheduler already paces background
    # ticks; this only affects user-initiated syncs. Trigger ``bootstrap``
    # is exempt because it's the first-run population flow that the user
    # cannot retrigger casually.
    if sync_trigger != SyncTrigger.BOOTSTRAP:
        last_run = deps.manifest.get_last_sync_run()
        if last_run is not None and last_run.finished_at is not None:
            now = deps.clock.now()
            elapsed = now - last_run.finished_at
            if elapsed < MANUAL_SYNC_COOLDOWN:
                remaining = MANUAL_SYNC_COOLDOWN - elapsed
                return JSONResponse(
                    {
                        "error": "cooldown",
                        "message": (
                            f"Last sync finished {int(elapsed.total_seconds())}s ago. "
                            f"Wait {int(remaining.total_seconds())}s and try again."
                        ),
                        "retry_after_seconds": int(remaining.total_seconds()),
                    },
                    status_code=429,
                    headers={"Retry-After": str(int(remaining.total_seconds()))},
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

    # Always build a fresh SyncService so this route's ``_update_snapshot``
    # callback is wired in. Reusing ``app.state.sync_service`` (set by the
    # scheduler) skipped the callback, leaving the banner at 0/0 mid-run.
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

    # HTMX clients (the dashboard / review-toolbar Sync now button) swap the
    # response into ``#sync-banner``. Render the banner partial so the user
    # sees the running state immediately instead of having to refresh the
    # page. Non-HTMX callers (CLI, scripts, tests) keep the JSON 202 contract.
    if request.headers.get("HX-Request") == "true":
        templates: Jinja2Templates = request.app.state.templates
        sync_status = _build_status_dict(request, deps)
        return templates.TemplateResponse(
            request,
            "partials/_sync_banner.html",
            {"sync_status": sync_status},
        )

    return JSONResponse(
        {
            "state": "running",
            "mode": mode,
            "trigger_source": trigger,
            "started_at": snapshot.started_at.isoformat(),
        },
        status_code=202,
    )


@router.post("/sync/enable")
async def enable_or_dismiss(request: Request) -> Response:
    """Persist the user's choice from the dashboard opt-in banner.

    ``action=enable`` flips ``[sync] enabled = true``; ``action=dismiss``
    sets ``[sync] opt_in_dismissed = true`` so the banner stops appearing.
    Either way, the cached deps are invalidated so the next request sees
    the new config and (for ``enable``) starts the scheduler lazily.
    """
    form = await request.form()
    action = form.get("action", "")
    if action not in ("enable", "dismiss"):
        raise HTTPException(status_code=422, detail=f"Invalid action: {action!r}")

    cfg_path = getattr(request.app.state, "config_path", None)
    if cfg_path is None or not cfg_path.exists():
        raise HTTPException(status_code=500, detail="No config to update")

    with open(cfg_path, "rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    sync_section = dict(data.get("sync", {}))
    if action == "enable":
        sync_section["enabled"] = True
    else:
        sync_section["opt_in_dismissed"] = True
    data["sync"] = sync_section
    write_atomic_toml(cfg_path, data)

    # Invalidate cached deps so the next request rebuilds with the new config.
    request.app.state.deps = None

    return RedirectResponse("/", status_code=303)
