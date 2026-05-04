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

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from firefliesclearer.application.audit_service import (
    FAILED_STATES,
    AuditService,
    StateSummary,
)
from firefliesclearer.core.manifest import Manifest, MeetingRecord
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.web.deps import get_deps
from firefliesclearer.web.operations import (
    Event,
    MeetingAlreadyInProgress,
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

# Per-state routing: archive-side failures map to RETRY_ARCHIVE,
# delete-side failures to RETRY_PURGE. Derived from the canonical
# ``FAILED_STATES`` tuple so adding a new failed state in one place
# stays consistent with the dashboard count and history filter.
_FAILED_PURGE_STATES: frozenset[MeetingState] = frozenset({MeetingState.DELETED_FAILED})
_FAILED_ARCHIVE_STATES: frozenset[MeetingState] = frozenset(FAILED_STATES) - _FAILED_PURGE_STATES


def _retry_kind_for_state(state: MeetingState) -> OperationKind | None:
    """Return the retry op kind for a failed state, or None if not retry-able."""
    if state in _FAILED_ARCHIVE_STATES:
        return OperationKind.RETRY_ARCHIVE
    if state in _FAILED_PURGE_STATES:
        return OperationKind.RETRY_PURGE
    return None


# String projection of FAILED_STATES for templates / query strings — kept
# next to the routing logic so the two lists never drift.
FAILED_STATE_VALUES: tuple[str, ...] = tuple(s.value for s in FAILED_STATES)


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


class _CacheLookup:
    """Outcome tagging for ``_fetch_meeting_from_cache``.

    The retry endpoint needs to distinguish three outcomes so it can return
    different 404 messages: present-and-usable, marked-gone-by-sync, and
    truly-absent-from-the-manifest. The previous "return Meeting | None"
    signature collapsed the latter two into the same "Run a sync first"
    message even when sync was the last thing that learned the meeting was
    gone — actively misleading (Copilot review on PR #19).
    """

    PRESENT = "present"
    GONE = "gone"
    ABSENT = "absent"


def _fetch_meeting_from_cache(*, manifest: Manifest, meeting_id: str) -> tuple[str, Meeting | None]:
    """Look up the Meeting metadata from the local cache.

    Returns ``(_CacheLookup.PRESENT, Meeting)`` when the row is usable,
    ``(_CacheLookup.GONE, None)`` when the last full sync flagged the row
    as gone-from-source, and ``(_CacheLookup.ABSENT, None)`` when no row
    exists at all.

    Lookup goes through ``manifest.get(meeting_id)`` directly — that's an
    indexed primary-key query, O(log n). Iterating
    ``manifest.list_known(...)`` for a single id was O(n) Python work over
    every cached row, which Copilot flagged as a hot-path concern on
    larger manifests.
    """
    rec = manifest.get(meeting_id)
    if rec is None:
        return (_CacheLookup.ABSENT, None)
    if rec.source_state == "gone":
        return (_CacheLookup.GONE, None)
    # Default missing snapshot fields to safe sentinels — legacy
    # ``register()``-only rows lack them until a sync touches the row.
    # The metadata.json will have empty host/participant info in that
    # case rather than crashing.
    meeting = Meeting(
        meeting_id=rec.meeting_id,
        title=rec.title,
        meeting_date=rec.meeting_date,
        duration_minutes=float(rec.duration_minutes) if rec.duration_minutes is not None else 0.0,
        host_email=rec.host_email or "",
        participant_count=int(rec.participant_count) if rec.participant_count is not None else 0,
        tags=rec.tags or (),
        has_transcript=bool(rec.has_transcript) if rec.has_transcript is not None else True,
    )
    return (_CacheLookup.PRESENT, meeting)


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
    # Retry-all is only useful when at least one row is actually retry-able
    # (rows whose source_state == "gone" can't progress further; they're
    # filtered out by the bulk runner and would otherwise produce a 409).
    retry_eligible = sum(1 for r in rows if r.source_state != "gone")
    ctx: dict[str, object] = {
        "summary": summary,
        "needs_attention": rows,
        "retry_eligible_count": retry_eligible,
        "failed_state_values": FAILED_STATE_VALUES,
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
        {
            "summary": summary,
            "MeetingState": MeetingState,
            "failed_state_values": FAILED_STATE_VALUES,
        },
    )


@router.post("/retry/all")
async def retry_all_attention(
    request: Request,
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Start a bulk retry op covering every needs-attention meeting.

    The runner walks the manifest's failed rows and routes each one through
    ``archive_one`` (any ``failed_*`` archive state) or ``purge_one``
    (``deleted_failed``) in sequence. Sequential dispatch keeps Fireflies
    rate-limit pressure predictable and matches the existing single-retry
    code path.

    Status codes:
      - 409 if there's nothing to retry, or another ``retry-attention`` op
        is already running.
      - 200 + an inline progress fragment that replaces the entire
        ``needs-attention`` section on success.
    """
    audit = AuditService(manifest=deps.manifest)
    summary = audit.summary()
    failed_ids: list[str] = list(summary.failed_meeting_ids)
    if not failed_ids:
        raise HTTPException(status_code=409, detail="Nothing to retry.")

    # Resolve to (meeting, kind) pairs while filtering rows whose cache is
    # missing — those would only blow up inside the runner with a confusing
    # per-row error. A row marked source_state == "gone" from a sync run is
    # also dropped (its archive/delete can't progress further).
    targets: list[tuple[Meeting, OperationKind]] = []
    skipped: list[str] = []
    for mid in failed_ids:
        rec = deps.manifest.get(mid)
        if rec is None or rec.source_state == "gone":
            skipped.append(mid)
            continue
        kind = _retry_kind_for_state(rec.state)
        if kind is None:
            skipped.append(mid)
            continue
        cache_state, meeting = _fetch_meeting_from_cache(manifest=deps.manifest, meeting_id=mid)
        if cache_state != _CacheLookup.PRESENT or meeting is None:
            skipped.append(mid)
            continue
        targets.append((meeting, kind))

    if not targets:
        raise HTTPException(
            status_code=409,
            detail="No retry-able meetings remain (all are gone-from-source or not cached).",
        )

    runner = _make_retry_all_runner(deps=deps, targets=targets)
    try:
        op = await _registry(request).start(
            kind=OperationKind.RETRY_ATTENTION,
            meeting_ids=[m.meeting_id for m, _ in targets],
            runner=runner,
        )
    except MeetingAlreadyInProgress as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "One of these meetings is already in another running operation. "
                "Wait for it to finish, then retry."
            ),
        ) from exc
    except SameKindAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail="A retry-all is already running.",
        ) from exc

    return _templates(request).TemplateResponse(
        request,
        "partials/_retry_all_progress.html",
        {"op": op, "total": len(targets), "skipped": len(skipped)},
    )


@router.post("/retry/{meeting_id}")
async def retry_meeting(
    request: Request,
    meeting_id: str,
    ui: str = Form(default=""),
    deps: SimpleNamespace = Depends(get_deps),  # noqa: B008
) -> Response:
    """Start a single-meeting retry op and return the inline progress card.

    Parameters
    ----------
    ui:
        Optional form param identifying the calling surface so the
        response template matches the click context. ``"history"``
        returns a ``<tr>`` fragment that swaps into the history table
        and uses SSE to drive the eventual reload — anything else
        defaults to the dashboard's ``<li>`` Needs Attention card.

    Status codes:
      - 404 if the manifest has no record for *meeting_id*.
      - 409 if the meeting's current state is not retry-able (e.g. PENDING,
        ARCHIVED, DELETED), the meeting is already in a running op, or
        another op of the same retry kind is running.
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

    cache_state, meeting = _fetch_meeting_from_cache(manifest=deps.manifest, meeting_id=meeting_id)
    if cache_state == _CacheLookup.GONE:
        # Sync has already learned the meeting is no longer in Fireflies —
        # retrying won't change that. Tell the user the truth instead of
        # asking them to sync again.
        raise HTTPException(
            status_code=404,
            detail="Meeting is no longer in Fireflies (detected by last sync).",
        )
    if cache_state == _CacheLookup.ABSENT or meeting is None:
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
    except MeetingAlreadyInProgress as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "This meeting is already being processed by another running "
                "operation (likely a retry-all). Wait for it to finish."
            ),
        ) from exc
    except SameKindAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail="Another retry of this kind is already running.",
        ) from exc

    sse_kind = "purge" if kind is OperationKind.RETRY_PURGE else "archive"
    template = (
        "partials/_retry_history_row.html" if ui == "history" else "partials/_retry_progress.html"
    )
    return _templates(request).TemplateResponse(
        request,
        template,
        {"op": op, "meeting": meeting, "kind": sse_kind, "record": rec},
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


def _make_retry_all_runner(
    *,
    deps: SimpleNamespace,
    targets: list[tuple[Meeting, OperationKind]],
) -> Callable[[_RunnerContext], Awaitable[None]]:
    """Build a runner that walks every needs-attention row sequentially.

    Each (meeting, kind) pair is dispatched through the matching service —
    ``ArchiveService.archive_meetings`` for any ``failed_*`` archive state,
    ``PurgeService.purge_meetings`` for ``deleted_failed``. The runner emits
    one ``meeting_state`` event per row entry/exit so the inline progress
    card can show running counters.
    """
    from firefliesclearer.application.archive_service import ArchiveService
    from firefliesclearer.application.purge_service import PurgeService

    async def runner(ctx: _RunnerContext) -> None:
        archive_svc = ArchiveService(pipeline=deps.pipeline, manifest=deps.manifest)
        purge_svc = PurgeService(pipeline=deps.pipeline, manifest=deps.manifest)
        for meeting, kind in targets:
            if ctx.cancel_event.is_set():
                return
            entry_sub_state = "deleting" if kind is OperationKind.RETRY_PURGE else "fetching"
            ctx.emit(
                Event(
                    seq=ctx.next_seq(),
                    kind="meeting_state",
                    data={
                        "id": meeting.meeting_id,
                        "title": meeting.title,
                        "sub_state": entry_sub_state,
                    },
                )
            )
            if kind is OperationKind.RETRY_PURGE:
                async for purge_outcome in purge_svc.purge_meetings([meeting]):
                    sub_state = "done" if purge_outcome.state.value == "deleted" else "failed"
                    ctx.emit(
                        Event(
                            seq=ctx.next_seq(),
                            kind="meeting_state",
                            data={
                                "id": purge_outcome.meeting_id,
                                "title": purge_outcome.title or meeting.title,
                                "sub_state": sub_state,
                                "error": purge_outcome.error,
                            },
                        )
                    )
            else:
                async for archive_outcome in archive_svc.archive_meetings([meeting]):
                    sub_state = (
                        "failed" if archive_outcome.state.value.startswith("failed_") else "done"
                    )
                    ctx.emit(
                        Event(
                            seq=ctx.next_seq(),
                            kind="meeting_state",
                            data={
                                "id": archive_outcome.meeting_id,
                                "title": archive_outcome.title or meeting.title,
                                "sub_state": sub_state,
                                "error": archive_outcome.error,
                            },
                        )
                    )

    return runner
