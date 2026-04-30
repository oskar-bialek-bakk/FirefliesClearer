"""Browser-driven server lifecycle: heartbeat + graceful shutdown.

The browser pings POST /_alive every 10 seconds. When pings stop for >60s and
no operation is in flight, ShutdownCoordinator fires its callback (uvicorn's
`Server.should_exit = True`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from firefliesclearer.ports.clock import Clock

logger = logging.getLogger(__name__)


class HeartbeatTracker:
    def __init__(self, clock: Clock, idle_threshold: timedelta) -> None:
        self._clock = clock
        self._threshold = idle_threshold
        self._last_seen = clock.now()

    def ping(self) -> None:
        self._last_seen = self._clock.now()

    def last_seen(self) -> datetime:
        return self._last_seen

    def is_idle(self) -> bool:
        return self._clock.now() - self._last_seen > self._threshold


class ShutdownCoordinator:
    def __init__(
        self,
        *,
        tracker: HeartbeatTracker,
        is_active: Callable[[], bool],
        clock: Clock,
        poll_interval: timedelta,
    ) -> None:
        self._tracker = tracker
        self._is_active = is_active
        self._clock = clock
        self._poll_interval = poll_interval
        self._on_shutdown: list[Callable[[], None]] = []
        self._stop = asyncio.Event()
        self._tick = asyncio.Event()
        self._quit_requested = False

    def on_shutdown_requested(self, cb: Callable[[], None]) -> None:
        self._on_shutdown.append(cb)

    def request_quit(self) -> None:
        self._quit_requested = True
        self._tick.set()

    def stop(self) -> None:
        self._stop.set()
        self._tick.set()

    def tick_now_for_test(self) -> None:
        self._tick.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._tick.wait(), timeout=self._poll_interval.total_seconds()
                )
            self._tick.clear()

            if self._stop.is_set():
                return

            should_shutdown = self._quit_requested or self._tracker.is_idle()
            if should_shutdown and not self._is_active():
                for cb in self._on_shutdown:
                    try:
                        cb()
                    except Exception:
                        # One failing callback must not block the others.
                        logger.exception("Shutdown callback raised; continuing.")
                return
