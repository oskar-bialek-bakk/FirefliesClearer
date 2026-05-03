"""Dashboard route + sidebar status fragment + single-meeting retry.

Owns ``GET /`` (full dashboard page), ``GET /sidebar/status`` (HTMX poll
fragment for the left-rail health summary), and ``POST /retry/{meeting_id}``
(start a single-meeting retry op and return the inline progress card).

The first two are read-only views over the manifest via
``AuditService.summary()``. The retry route reuses the same
``OperationRegistry`` machinery as the cleanup wizard so progress streams,
cancellation, and the SSE endpoint all work uniformly across multi-meeting
wizard runs and single-meeting retries.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from firefliesclearer.application.audit_service import AuditService, StateSummary
from firefliesclearer.core.manifest import Manifest, MeetingRecord
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.web.deps import get_deps
from firefliesclearer.web.operations import (
    Event,
    OperationKind,
    OperationRegistry,
    SameKindAlreadyRunning,
    _RunnerContext,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Retry kind lookup
# ---------------------------------------------------------------------------

# States that mean "archive failed" — retry-archive recovers them.
_FAILED_ARCHIVE_STATES: frozenset[MeetingState] = frozenset(
    {
        MeetingState.FAILED_FETCH,
        MeetingState.FAILED_DOWNLOAD,
        MeetingState.FAILED_RENDER,
        MeetingState.FAILED_VERIFY,
    }
)

# State that means "delete (purge) failed" — retry-purge recovers it.
_FAILED_PURGE_STATES: frozenset[MeetingState] = frozenset({MeetingState.DELETED_FAILED})


def _retry_kind_for_state(state: MeetingState) -> OperationKind | None:
    """Return the retry op kind for a failed state, or None if not retry-able."""
    if state in _FAILED_ARCHIVE_STATES:
        return OperationKind.RETRY_ARCHIVE
    if state in _FAILED_PURGE_STATES:
        return OperationKind.RETRY_PURGE
    return None


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _templates(request: Request) -> Jinja2Templates:
    t: Jinja2Templates = request.app.state.templates
    return t


def _registry(request: Request) -> OperationRegistry:
    reg: OperationRegistry = request.app.state.operation_registry
    return reg


def _needs_attention_rows(manifest: Manifest, summary: StateSummary) -> list[MeetingRecord]:
    """Resolve failed meeting IDs into MeetingRecord rows for the template."""
    rows: list[MeetingRecord] = []
    for mid in summary.failed_meeting_ids:
        rec = manifest.get(mid)
        if rec is not None:
            rows.append(rec)
    return rows


def _fetch_meeting_from_cache(*, manifest: Manifest, meeting_id: str) -> Meeting | None:
    """Look up the Meeting metadata from the local cache.

    Used by the retry flow to reconstruct the full Meeting (duration, host,
    participants, tags) needed to write a faithful metadata.json.

    Reads come from the cache rather than the live API. The previous version
    walked ``repo.list_meetings(MeetingFilter())`` — a network round-trip
    that paginated through every meeting in the user's account just to find
    one row by id. With a slow upstream or under rate-limit pressure
    (the user hit 429s during the 2026-05-03 incident), the retry button
    appeared to "do nothing" because the request silently 5xx'd before HTMX
    could swap in any feedback. The cache has the same metadata after a
    sync, with no network cost.

    ``include_archived=True`` covers retry-archive (failed states) and
    retry-purge (archived state); ``include_gone=False`` skips rows the
    last full sync flagged as gone-from-source so we don't try to operate
    on transcripts the user already removed in Fireflies.
    """
    for meeting in manifest.list_known(include_archived=True, include_gone=False):
        if meeting.meeting_id == meeting_id:
            return meeting
    # Fall back to the MeetingRecord directly. ``list_known`` filters out rows
    # that lack snapshot fields (legacy ``register()``-only inserts that no
    # sync has touched yet); the retry flow just needs a ``Meeting`` with a
    # workable identity + title to feed the runner. Default the missing
    # snapshot fields to safe sentinels — the worst case is a metadata.json
    # with empty host/participant info, not a crash.
    rec = manifest.get(meeting_id)
    if rec is None:
        return None
    # Honour the gone-from-source flag in the fallback path too — otherwise
    # a row whose snapshot fields are missing (skipped by ``list_known``)
    # but which the last sync already marked as ``gone`` would be served up
    # as if still present, and the retry would inevitably 404 against the
    # live API anyway.
    if rec.source_state == "gone":
        return None
    return Meeting(
        meeting_id=rec.meeting_id,
        title=rec.title,
        meeting_date=rec.meeting_date,
        duration_minutes=float(rec.duration_minutes) if rec.duration_minutes is not None else 0.0,
        host_email=rec.host_email or "",
        participant_count=int(rec.participant_count) if rec.participant_count is not None else 0,
        tags=rec.tags or (),
        has_transcript=bool(rec.has_transcript) if rec.has_transcript is not None else True,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/")
async def dashboard(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    from firefliesclearer.web.routes.sync import maybe_status_for_template

    audit = AuditService(manifest=deps.manifest)
    summary = audit.summary()
    rows = _needs_attention_rows(deps.manifest, summary)
    ctx: dict[str, object] = {
        "summary": summary,
        "needs_attention": rows,
        "version": request.app.state.version,
        "MeetingState": MeetingState,
        "show_sync_opt_in": (
            deps.config.sync.enabled is False and deps.config.sync.opt_in_dismissed is False
        ),
    }
    sync_status = maybe_status_for_template(request, deps)
    if sync_status is not None:
        ctx["sync_status"] = sync_status
    return _templates(request).TemplateResponse(request, "dashboard.html", ctx)


@router.get("/sidebar/status")
async def sidebar_status(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    audit = AuditService(manifest=deps.manifest)
    summary = audit.summary()
    return _templates(request).TemplateResponse(
        request,
        "partials/sidebar_status.html",
        {"summary": summary},
    )


@router.get("/dashboard/state-counts")
async def dashboard_state_counts(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Return the state-counts cards as a standalone fragment.

    Driven by the ``hx-trigger="every 10s"`` poll on the section so the
    Total / Archived / Pending / Failed / Deleted numbers stay current
    while a sync run is adding rows in the background.
    """
    audit = AuditService(manifest=deps.manifest)
    summary = audit.summary()
    return _templates(request).TemplateResponse(
        request,
        "partials/state_counts.html",
        {"summary": summary, "MeetingState": MeetingState},
    )


@router.post("/retry/{meeting_id}")
async def retry_meeting(
    request: Request,
    meeting_id: str,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Start a single-meeting retry op and return the inline progress card.

    Status codes:
      - 404 if the manifest has no record for *meeting_id*.
      - 409 if the meeting's current state is not retry-able (e.g. PENDING,
        ARCHIVED, DELETED) or another op of the same retry kind is running.
      - 200 + HTMX-targetable HTML fragment on success.
    """
    rec = deps.manifest.get(meeting_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Meeting not found in manifest")

    kind = _retry_kind_for_state(rec.state)
    if kind is None:
        raise HTTPException(
            status_code=409,
            detail="Meeting not in a retry-able state.",
        )

    meeting = _fetch_meeting_from_cache(manifest=deps.manifest, meeting_id=meeting_id)
    if meeting is None:
        raise HTTPException(
            status_code=404,
            detail="Meeting metadata not in local cache. Run a sync first.",
        )

    runner = _make_retry_runner(deps=deps, meeting=meeting, kind=kind)
    try:
        op = await _registry(request).start(
            kind=kind,
            meeting_ids=[meeting.meeting_id],
            runner=runner,
        )
    except SameKindAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail="Another retry of this kind is already running.",
        ) from exc

    sse_kind = "purge" if kind is OperationKind.RETRY_PURGE else "archive"
    return _templates(request).TemplateResponse(
        request,
        "partials/_retry_progress.html",
        {"op": op, "meeting": meeting, "kind": sse_kind},
    )


# ---------------------------------------------------------------------------
# Retry runner
# ---------------------------------------------------------------------------


def _make_retry_runner(
    *,
    deps: SimpleNamespace,
    meeting: Meeting,
    kind: OperationKind,
) -> Callable[[_RunnerContext], Awaitable[None]]:
    """Build a single-meeting runner that re-runs archive_one / purge_one.

    Mirrors the per-meeting event vocabulary of ``_make_archive_runner`` /
    ``_make_purge_runner`` in ``cleanup.py`` so the inline progress card and
    the SSE script can reuse the same labels and terminal-event handling.
    """
    if kind is OperationKind.RETRY_ARCHIVE:
        return _make_archive_retry_runner(deps=deps, meeting=meeting)
    if kind is OperationKind.RETRY_PURGE:
        return _make_purge_retry_runner(deps=deps, meeting=meeting)
    raise ValueError(f"Unsupported retry kind: {kind!r}")


def _make_archive_retry_runner(
    *, deps: SimpleNamespace, meeting: Meeting
) -> Callable[[_RunnerContext], Awaitable[None]]:
    # Late import keeps the module-import graph quick and lets test fakes
    # patch the service in app.state.deps.pipeline as usual.
    from firefliesclearer.application.archive_service import ArchiveService

    async def runner(ctx: _RunnerContext) -> None:
        if ctx.cancel_event.is_set():
            return
        svc = ArchiveService(pipeline=deps.pipeline, manifest=deps.manifest)
        ctx.emit(
            Event(
                seq=ctx.next_seq(),
                kind="meeting_state",
                data={
                    "id": meeting.meeting_id,
                    "title": meeting.title,
                    "sub_state": "fetching",
                },
            )
        )
        async for outcome in svc.archive_meetings([meeting]):
            sub_state = "failed" if outcome.state.value.startswith("failed_") else "done"
            ctx.emit(
                Event(
                    seq=ctx.next_seq(),
                    kind="meeting_state",
                    data={
                        "id": outcome.meeting_id,
                        "title": outcome.title or meeting.title,
                        "sub_state": sub_state,
                        "error": outcome.error,
                    },
                )
            )

    return runner


def _make_purge_retry_runner(
    *, deps: SimpleNamespace, meeting: Meeting
) -> Callable[[_RunnerContext], Awaitable[None]]:
    from firefliesclearer.application.purge_service import PurgeService

    async def runner(ctx: _RunnerContext) -> None:
        if ctx.cancel_event.is_set():
            return
        svc = PurgeService(pipeline=deps.pipeline, manifest=deps.manifest)
        ctx.emit(
            Event(
                seq=ctx.next_seq(),
                kind="meeting_state",
                data={
                    "id": meeting.meeting_id,
                    "title": meeting.title,
                    "sub_state": "deleting",
                },
            )
        )
        async for outcome in svc.purge_meetings([meeting]):
            sub_state = "done" if outcome.state.value == "deleted" else "failed"
            ctx.emit(
                Event(
                    seq=ctx.next_seq(),
                    kind="meeting_state",
                    data={
                        "id": outcome.meeting_id,
                        "title": outcome.title or meeting.title,
                        "sub_state": sub_state,
                        "error": outcome.error,
                    },
                )
            )

    return runner
