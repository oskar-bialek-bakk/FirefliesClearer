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

    async def runner(ctx):
        for mid in ctx.meeting_ids:
            if ctx.cancel_event.is_set():
                break
            ctx.emit(
                Event(seq=ctx.next_seq(), kind="meeting_state", data={"id": mid, "state": "done"})
            )
            await asyncio.sleep(0)

    op = await reg.start(kind=OperationKind.ARCHIVE, meeting_ids=["m1", "m2", "m3"], runner=runner)
    await asyncio.sleep(0.01)
    reg.cancel(op.id)
    await op.task

    events = list(op.replay_buffer())
    assert any(e.data.get("id") == "m1" for e in events)
    assert len([e for e in events if e.kind == "meeting_state"]) < 3


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
