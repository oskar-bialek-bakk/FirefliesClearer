"""Tests for the SSE progress endpoint at GET /api/operations/{op_id}/events.

The endpoint streams operation progress to the in-progress views via the
``Operation.subscribe()`` async generator (gap-free per Task 5.1's I1 fix —
yields snapshot first, then live events; terminates on terminal
``operation_state``).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from firefliesclearer.application.scan_service import ScanFilters
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.web.operations import Event, OperationKind
from firefliesclearer.web.wizard_session import WizardState, filters_to_dict, set_state
from tests.fakes.in_memory_repository import InMemoryMeetingRepository

NOW = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def configured_client(configured_app):
    """Persistent-portal TestClient (mirrors Step 3/4 pattern)."""
    with TestClient(configured_app) as c:
        c.get("/?token=T")
        yield c


def _seed_repo(app, count: int) -> list[Meeting]:
    meetings = [
        Meeting(
            meeting_id=f"m{i}",
            title=f"Meeting {i}",
            meeting_date=NOW - timedelta(days=100 + i),
            duration_minutes=30.0,
            host_email="h@x",
            participant_count=4,
        )
        for i in range(count)
    ]
    repo = InMemoryMeetingRepository(meetings=meetings, api_key="ff_test")
    repo.set_user_email_for_key("ff_test", "oskar@example.com")
    app.state.deps.client = repo
    return meetings


def _sid(c: TestClient) -> str:
    return c.cookies.get("ffc_session", "") or ""


def _csrf(c: TestClient) -> str:
    return c.cookies.get("ffc_csrf", "") or ""


def _set_wizard(app, sid: str, *, step: str, selected: list[str], op_id: str | None = None) -> None:
    state = WizardState(
        step=step,
        filters=filters_to_dict(ScanFilters(older_than_days=30)),
        selected_ids=list(selected),
        operation_id=op_id,
    )
    set_state(app.state.session_store, sid, state)


async def _await_op(app, op_id: str) -> None:
    op = app.state.operation_registry.get(op_id)
    await op.task


def _wait_for_op(client: TestClient, app, op_id: str) -> None:
    assert client.portal is not None
    client.portal.call(_await_op, app, op_id)


def _parse_sse_events(body: str) -> list[dict[str, str]]:
    """Parse an SSE response body into a list of {event, id, data} dicts.

    sse-starlette emits CRLF line terminators per the EventStream spec; this
    parser normalises CRLF to LF so the simple ``\\n\\n``-block split works
    on Windows-emitted streams too.  Comment-only blocks (sse-starlette
    keepalives, prefixed with ``:``) are dropped.
    """
    events: list[dict[str, str]] = []
    normalised = body.replace("\r\n", "\n").replace("\r", "\n")
    for block in normalised.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        evt: dict[str, str] = {}
        skip_block = False
        for line in block.split("\n"):
            if line.startswith(":"):
                # Comment / keepalive — entire block ignored if no data follows.
                skip_block = True
                continue
            if ":" not in line:
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            evt[field] = value
        if not skip_block and evt:
            events.append(evt)
    return events


# ---------------------------------------------------------------------------
# 404 — unknown op_id
# ---------------------------------------------------------------------------


def test_events_returns_404_for_unknown_op(configured_client: TestClient) -> None:
    r = configured_client.get("/api/operations/op_does_not_exist/events")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Happy path — terminal-already op replays full event log then closes
# ---------------------------------------------------------------------------


def test_events_replays_buffer_for_terminal_op(
    configured_client: TestClient, configured_app
) -> None:
    """Late subscriber sees every event before the stream closes.

    Start an archive op for one meeting and let it complete.  Then GET the
    SSE endpoint — because ``Operation.subscribe()`` snapshots the event log
    first and the terminal ``operation_state`` is already buffered, the
    response body must contain at least one ``meeting_state`` and the
    closing ``operation_state`` event in seq order.
    """
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0"])

    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = configured_app.state.session_store.get(sid)["wizard"]["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)

    r = configured_client.get(f"/api/operations/{op_id}/events")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse_events(r.text)
    assert events, f"no SSE events parsed from body: {r.text!r}"

    # seq ids strictly increasing.
    seq_ids = [int(e["id"]) for e in events if "id" in e]
    assert seq_ids == sorted(seq_ids)
    assert len(set(seq_ids)) == len(seq_ids), "duplicate seq detected (replay double-yield)"

    # At least one meeting_state and exactly one terminal operation_state.
    kinds = [e.get("event") for e in events]
    assert "meeting_state" in kinds
    assert kinds.count("operation_state") == 1
    assert kinds[-1] == "operation_state"

    terminal_data = json.loads(events[-1]["data"])
    assert terminal_data["state"] in {"succeeded", "failed", "cancelled"}


# ---------------------------------------------------------------------------
# Stream closes on terminal operation_state (no more events after)
# ---------------------------------------------------------------------------


def test_events_closes_on_terminal_operation_state(
    configured_client: TestClient, configured_app
) -> None:
    """No events follow the terminal ``operation_state``."""
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0", "m1"])
    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = configured_app.state.session_store.get(sid)["wizard"]["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)

    r = configured_client.get(f"/api/operations/{op_id}/events")
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    # operation_state must be the last event in the stream.
    kinds = [e.get("event") for e in events]
    last_op_idx = max(i for i, k in enumerate(kinds) if k == "operation_state")
    assert last_op_idx == len(events) - 1


# ---------------------------------------------------------------------------
# Multiple subscribers each receive every event
# ---------------------------------------------------------------------------


def test_multiple_subscribers_each_receive_full_stream(
    configured_client: TestClient, configured_app
) -> None:
    """Two independent SSE GETs each see the same complete event log."""
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0", "m1"])
    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = configured_app.state.session_store.get(sid)["wizard"]["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)

    r1 = configured_client.get(f"/api/operations/{op_id}/events")
    r2 = configured_client.get(f"/api/operations/{op_id}/events")
    assert r1.status_code == 200
    assert r2.status_code == 200

    e1 = _parse_sse_events(r1.text)
    e2 = _parse_sse_events(r2.text)
    # Identical seq ordering for both — replay buffer is deterministic.
    assert [evt.get("id") for evt in e1] == [evt.get("id") for evt in e2]
    assert [evt.get("event") for evt in e1] == [evt.get("event") for evt in e2]


# ---------------------------------------------------------------------------
# Live streaming — events emitted after subscription are delivered
# ---------------------------------------------------------------------------


def test_events_streams_live_then_closes(configured_client: TestClient, configured_app) -> None:
    """Subscribe to a still-running op, then drive it to completion.

    The pipeline blocks on a portal-loop ``asyncio.Event`` so the operation
    holds open while we open the SSE stream; releasing the event lets the
    runner finish and the terminal ``operation_state`` closes the stream.

    The ``asyncio.Event`` is allocated **on the portal loop** so its waiters
    and setters share the same loop — setting it from the worker thread
    requires ``client.portal.call`` because asyncio.Event is not
    thread-safe.
    """
    _seed_repo(configured_app, 3)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0"])

    assert configured_client.portal is not None

    async def _make_event() -> asyncio.Event:
        return asyncio.Event()

    block: asyncio.Event = configured_client.portal.call(_make_event)

    class BlockingPipeline:
        async def archive_one(self, meeting):
            await block.wait()
            return MeetingState.ARCHIVED

    configured_app.state.deps.pipeline = BlockingPipeline()
    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = configured_app.state.session_store.get(sid)["wizard"]["operation_id"]

    # Release the pipeline BEFORE the SSE GET so the op terminates while
    # the stream is being consumed.  The TestClient buffers the body until
    # the response ends, which the terminal ``operation_state`` triggers.
    async def _release() -> None:
        block.set()

    configured_client.portal.call(_release)
    _wait_for_op(configured_client, configured_app, op_id)

    r = configured_client.get(f"/api/operations/{op_id}/events")
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    assert events, "expected at least one streamed event"
    kinds = [e.get("event") for e in events]
    assert kinds[-1] == "operation_state"
    terminal_data = json.loads(events[-1]["data"])
    assert terminal_data["state"] == "succeeded"


# ---------------------------------------------------------------------------
# Event payload shape — meeting_state carries id/title/sub_state
# ---------------------------------------------------------------------------


def test_meeting_state_payload_carries_expected_fields(
    configured_client: TestClient, configured_app
) -> None:
    _seed_repo(configured_app, 2)
    sid = _sid(configured_client)
    _set_wizard(configured_app, sid, step="archive", selected=["m0"])
    configured_client.post(
        "/cleanup/archive/start",
        data={"_csrf": _csrf(configured_client)},
        follow_redirects=False,
    )
    op_id = configured_app.state.session_store.get(sid)["wizard"]["operation_id"]
    _wait_for_op(configured_client, configured_app, op_id)

    r = configured_client.get(f"/api/operations/{op_id}/events")
    events = _parse_sse_events(r.text)
    meeting_events = [e for e in events if e.get("event") == "meeting_state"]
    assert meeting_events, "no meeting_state events present"
    payload = json.loads(meeting_events[0]["data"])
    assert payload["id"] == "m0"
    assert payload["title"] == "Meeting 0"
    assert "sub_state" in payload


# ---------------------------------------------------------------------------
# Direct registry test — subscribe alone (no replay_buffer) is gap-free
# ---------------------------------------------------------------------------


async def test_subscribe_alone_is_gap_free_for_terminal_op(configured_app) -> None:
    """Sanity check on Task 5.1's invariant: subscribing to a terminal op
    yields the full event log and returns, no double-yield.
    """
    registry = configured_app.state.operation_registry

    async def runner(ctx) -> None:
        ctx.emit(
            Event(
                seq=ctx.next_seq(),
                kind="meeting_state",
                data={"id": "m0", "title": "x", "sub_state": "done"},
            )
        )

    op = await registry.start(kind=OperationKind.ARCHIVE, meeting_ids=["m0"], runner=runner)
    await op.task
    seen: list[tuple[int, str]] = []
    async for evt in op.subscribe():
        seen.append((evt.seq, evt.kind))
    seqs = [s for s, _ in seen]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs), "double-yield detected"
    assert seen[-1][1] == "operation_state"
