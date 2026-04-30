"""Tests for HeartbeatTracker and the shutdown coordinator."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from firefliesclearer.web.lifecycle import HeartbeatTracker, ShutdownCoordinator
from tests.fakes.frozen_clock import FrozenClock


def t0() -> datetime:
    return datetime(2026, 4, 29, 10, 0, tzinfo=UTC)


def test_first_seen_initialises_to_now():
    clock = FrozenClock(t0())
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))

    assert tracker.last_seen() == t0()
    assert not tracker.is_idle()


def test_ping_updates_last_seen():
    start = t0()
    clock = FrozenClock(start)
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    clock.advance(timedelta(seconds=30))

    tracker.ping()

    assert tracker.last_seen() == start + timedelta(seconds=30)
    assert not tracker.is_idle()


def test_idle_after_threshold():
    clock = FrozenClock(t0())
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    clock.advance(timedelta(seconds=61))

    assert tracker.is_idle()


async def test_shutdown_coordinator_fires_when_idle_and_no_active_op():
    clock = FrozenClock(t0())
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    coord = ShutdownCoordinator(
        tracker=tracker,
        is_active=lambda: False,
        clock=clock,
        poll_interval=timedelta(seconds=5),
    )

    fired = asyncio.Event()
    coord.on_shutdown_requested(lambda: fired.set())
    task = asyncio.create_task(coord.run())

    clock.advance(timedelta(seconds=70))
    await asyncio.sleep(0)
    coord.tick_now_for_test()

    await asyncio.wait_for(fired.wait(), timeout=1.0)
    coord.stop()
    await task


async def test_shutdown_deferred_while_op_active():
    clock = FrozenClock(t0())
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    active = True
    coord = ShutdownCoordinator(
        tracker=tracker,
        is_active=lambda: active,
        clock=clock,
        poll_interval=timedelta(seconds=5),
    )
    fired = asyncio.Event()
    coord.on_shutdown_requested(lambda: fired.set())
    task = asyncio.create_task(coord.run())

    clock.advance(timedelta(seconds=70))
    coord.tick_now_for_test()
    await asyncio.sleep(0.05)
    assert not fired.is_set()  # deferred

    active = False
    coord.tick_now_for_test()
    await asyncio.wait_for(fired.wait(), timeout=1.0)
    coord.stop()
    await task


async def test_quit_button_requests_shutdown_immediately():
    clock = FrozenClock(t0())
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    coord = ShutdownCoordinator(
        tracker=tracker,
        is_active=lambda: False,
        clock=clock,
        poll_interval=timedelta(seconds=5),
    )
    fired = asyncio.Event()
    coord.on_shutdown_requested(lambda: fired.set())
    task = asyncio.create_task(coord.run())

    coord.request_quit()
    coord.tick_now_for_test()

    await asyncio.wait_for(fired.wait(), timeout=1.0)
    coord.stop()
    await task


async def test_shutdown_callbacks_isolated_on_exception():
    """One failing callback must not block subsequent callbacks."""
    clock = FrozenClock(t0())
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    coord = ShutdownCoordinator(
        tracker=tracker,
        is_active=lambda: False,
        clock=clock,
        poll_interval=timedelta(seconds=5),
    )

    called = []

    def boom() -> None:
        raise RuntimeError("bad cleanup")

    def good() -> None:
        called.append("good")

    coord.on_shutdown_requested(boom)
    coord.on_shutdown_requested(good)

    task = asyncio.create_task(coord.run())

    clock.advance(timedelta(seconds=70))
    coord.tick_now_for_test()
    await asyncio.sleep(0)

    coord.stop()
    await task

    # good callback must have been called despite boom raising
    assert "good" in called


async def test_stop_during_tick_returns_immediately():
    """If stop() is called while run() is blocked in wait_for(), the early-return
    guard at line 78 executes — shutdown callbacks are NOT called."""
    clock = FrozenClock(t0())
    tracker = HeartbeatTracker(clock=clock, idle_threshold=timedelta(seconds=60))
    # Use a very long poll interval so wait_for doesn't time out on its own.
    coord = ShutdownCoordinator(
        tracker=tracker,
        is_active=lambda: False,
        clock=clock,
        poll_interval=timedelta(seconds=999),
    )

    fired = asyncio.Event()
    coord.on_shutdown_requested(lambda: fired.set())

    task = asyncio.create_task(coord.run())
    # Yield several times to ensure run() is blocked inside wait_for.
    for _ in range(5):
        await asyncio.sleep(0)

    # stop() sets _stop=True AND calls _tick.set() so wait_for unblocks.
    # After wait_for returns, _tick.clear() runs, then `if self._stop.is_set(): return`
    # hits line 78.
    coord.stop()
    await asyncio.wait_for(task, timeout=1.0)

    # The shutdown callback must NOT have fired — we returned early.
    assert not fired.is_set()
