"""SSE progress endpoint for long-running operations.

Streams progress events for a single operation to in-progress views via the
``Operation.subscribe()`` async generator.  Per Task 5.1's I1 fix the
generator yields the snapshot first then live queue events with seq-based
dedup, so a single ``async for`` over ``op.subscribe()`` is gap-free and
double-yield-free.  The stream auto-closes when the generator returns on
the terminal ``operation_state`` event.

Cancel is handled by the canonical ``POST /cleanup/operations/{op_id}/cancel``
route in ``routes/cleanup.py``; this module only owns the read-side stream.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from firefliesclearer.web.operations import OperationRegistry

# Heartbeat ping interval (seconds).  Keeps the connection alive through
# proxies/idle timeouts; sse-starlette emits these as SSE comment lines.
PING_INTERVAL_SECONDS = 15

router = APIRouter()


def _registry(request: Request) -> OperationRegistry:
    reg: OperationRegistry = request.app.state.operation_registry
    return reg


@router.get("/api/operations/{op_id}/events")
async def stream_events(op_id: str, request: Request) -> EventSourceResponse:
    """SSE stream of meeting_state + operation_state events for an op.

    Returns 404 when ``op_id`` is unknown.  The stream closes itself once
    ``Operation.subscribe()`` yields the terminal ``operation_state`` event
    and returns; ``EventSourceResponse`` then ends the HTTP response.
    """
    try:
        op = _registry(request).get(op_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="operation not found") from exc

    async def gen() -> AsyncIterator[dict[str, Any]]:
        async for evt in op.subscribe():
            yield {
                "event": evt.kind,
                "id": str(evt.seq),
                "data": json.dumps(evt.data),
            }

    return EventSourceResponse(gen(), ping=PING_INTERVAL_SECONDS)
