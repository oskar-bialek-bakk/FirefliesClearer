"""Tests for the in-memory OperationRegistry."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from firefliesclearer.web.operations import (
    Event,
    OperationKind,
    OperationRegistry,
    SameKindAlreadyRunning,
)
from tests.fakes.frozen_clock import FrozenClock


async def test_register_and_get():
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))

    async def runner(ctx):
        return None

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1"], runner=runner)
    assert reg.get(op.id) is op


async def test_second_same_kind_raises_409_equivalent():
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))

    async def slow(ctx):
        await asyncio.sleep(0.5)

    op1 = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1"], runner=slow)

    with pytest.raises(SameKindAlreadyRunning) as exc:
        await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m2"], runner=slow)
    assert exc.value.existing_op_id == op1.id

    op1.task.cancel()


async def test_cancel_completes_after_current_meeting():
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))
    first_emitted = asyncio.Event()
    let_cancel = asyncio.Event()

    async def runner(ctx):
        for mid in ctx.meeting_ids:
            ctx.emit(
                Event(seq=ctx.next_seq(), kind="meeting_state", data={"id": mid, "state": "done"})
            )
            if mid == "m1":
                first_emitted.set()
                await let_cancel.wait()  # Test controls when cancel is observed.
            if ctx.cancel_event.is_set():
                break

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1", "m2", "m3"], runner=runner)
    await first_emitted.wait()
    reg.cancel(op.id)
    let_cancel.set()
    await op.task

    events = list(op.replay_buffer())
    meeting_events = [e for e in events if e.kind == "meeting_state"]
    assert len(meeting_events) == 1  # Only m1 emitted.
    assert meeting_events[0].data["id"] == "m1"


async def test_runner_exception_emits_single_failed_terminal():
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))

    async def boom(ctx):
        raise RuntimeError("nope")

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1"], runner=boom)
    await op.task

    events = list(op.replay_buffer())
    terminals = [e for e in events if e.kind == "operation_state"]
    assert len(terminals) == 1
    assert terminals[0].data["state"] == "failed"
    assert terminals[0].data["error"] == "nope"
    assert op.state == "failed"


async def test_subscribe_yields_live_events_then_terminates_on_terminal():
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))
    started = asyncio.Event()
    can_finish = asyncio.Event()

    async def runner(ctx):
        ctx.emit(Event(seq=ctx.next_seq(), kind="meeting_state", data={"id": "m1"}))
        started.set()
        await can_finish.wait()
        ctx.emit(Event(seq=ctx.next_seq(), kind="meeting_state", data={"id": "m2"}))

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1", "m2"], runner=runner)
    await started.wait()

    received: list[Event] = []

    async def consume():
        async for evt in op.subscribe():
            received.append(evt)

    consumer_task = asyncio.create_task(consume())
    await asyncio.sleep(0)  # Let subscribe() register.
    can_finish.set()
    await op.task
    await consumer_task

    # Expect: m1 (replayed), m2 (live), terminal operation_state.
    assert len(received) == 3
    assert received[0].kind == "meeting_state"
    assert received[0].data["id"] == "m1"
    assert received[1].kind == "meeting_state"
    assert received[1].data["id"] == "m2"
    assert received[-1].kind == "operation_state"
    assert received[-1].data["state"] == "succeeded"


async def test_subscribe_replay_then_live_no_gap():
    """Subscriber connecting AFTER an early event but BEFORE terminal sees both."""
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))
    early_emitted = asyncio.Event()
    can_finish = asyncio.Event()

    async def runner(ctx):
        ctx.emit(Event(seq=ctx.next_seq(), kind="meeting_state", data={"id": "early"}))
        early_emitted.set()
        await can_finish.wait()
        ctx.emit(Event(seq=ctx.next_seq(), kind="meeting_state", data={"id": "late"}))

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["early", "late"], runner=runner)
    await early_emitted.wait()

    received: list[Event] = []

    async def consume():
        async for evt in op.subscribe():
            received.append(evt)

    consumer_task = asyncio.create_task(consume())
    await asyncio.sleep(0)  # Let subscribe() register and snapshot.
    can_finish.set()
    await op.task
    await consumer_task

    ids = [e.data.get("id") for e in received if e.kind == "meeting_state"]
    assert ids == ["early", "late"]
    # No duplicates from snapshot+queue overlap.
    assert len({e.seq for e in received}) == len(received)


async def test_two_subscribers_both_receive_all_events():
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))
    first_emitted = asyncio.Event()
    second_consumer_ready = asyncio.Event()
    can_finish = asyncio.Event()

    async def runner(ctx):
        ctx.emit(Event(seq=ctx.next_seq(), kind="meeting_state", data={"id": "a"}))
        first_emitted.set()
        await second_consumer_ready.wait()
        ctx.emit(Event(seq=ctx.next_seq(), kind="meeting_state", data={"id": "b"}))
        await can_finish.wait()
        ctx.emit(Event(seq=ctx.next_seq(), kind="meeting_state", data={"id": "c"}))

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["a", "b", "c"], runner=runner)

    received_a: list[Event] = []

    async def consume_a():
        async for evt in op.subscribe():
            received_a.append(evt)

    consumer_a = asyncio.create_task(consume_a())
    await first_emitted.wait()
    await asyncio.sleep(0)  # Let consumer_a register.

    received_b: list[Event] = []

    async def consume_b():
        async for evt in op.subscribe():
            received_b.append(evt)

    consumer_b = asyncio.create_task(consume_b())
    await asyncio.sleep(0)  # Let consumer_b register.
    second_consumer_ready.set()
    can_finish.set()
    await op.task
    await consumer_a
    await consumer_b

    ids_a = [e.data.get("id") for e in received_a if e.kind == "meeting_state"]
    ids_b = [e.data.get("id") for e in received_b if e.kind == "meeting_state"]
    assert ids_a == ["a", "b", "c"]
    assert ids_b == ["a", "b", "c"]
    assert received_a[-1].kind == "operation_state"
    assert received_a[-1].data["state"] == "succeeded"
    assert received_b[-1].kind == "operation_state"
    assert received_b[-1].data["state"] == "succeeded"


async def test_subscribe_after_terminal_replays_full_history():
    """Late subscriber connecting after terminal still gets the full sequence."""
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))

    async def runner(ctx):
        ctx.emit(Event(seq=ctx.next_seq(), kind="meeting_state", data={"id": "m1"}))

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1"], runner=runner)
    await op.task

    received = [evt async for evt in op.subscribe()]
    # Snapshot replay yields meeting_state then the terminal operation_state and stops.
    assert received[0].kind == "meeting_state"
    assert received[-1].kind == "operation_state"
    assert received[-1].data["state"] == "succeeded"


async def test_has_active_true_while_running_false_after_done():
    reg = OperationRegistry(clock=FrozenClock(datetime(2026, 4, 29, tzinfo=UTC)))
    can_finish = asyncio.Event()

    async def runner(ctx):
        await can_finish.wait()

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1"], runner=runner)
    assert reg.has_active() is True
    can_finish.set()
    await op.task
    assert reg.has_active() is False


async def test_gc_drops_completed_after_window():
    clock = FrozenClock(datetime(2026, 4, 29, tzinfo=UTC))
    reg = OperationRegistry(clock=clock)

    async def runner(ctx):
        return None

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1"], runner=runner)
    await op.task
    clock.advance(timedelta(minutes=31))
    reg.gc()

    with pytest.raises(KeyError):
        reg.get(op.id)
