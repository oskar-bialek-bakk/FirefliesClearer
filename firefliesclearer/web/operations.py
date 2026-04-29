"""In-memory registry for long-running archive/purge operations.

A single ``OperationRegistry`` is attached to ``app.state.operation_registry``
by ``create_app`` (wired by Task 5.4 / 5.5 / 5.6). Each ``Operation`` owns its
own asyncio task, cooperative cancel flag, replay buffer, and live subscriber
queues.  See spec sections 10.1-10.2 for the design contract.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from firefliesclearer.ports.clock import Clock


class OperationKind(StrEnum):
    ARCHIVE = "archive"
    PURGE = "purge"
    RETRY_ARCHIVE = "retry-archive"
    RETRY_PURGE = "retry-purge"


class SameKindAlreadyRunning(RuntimeError):  # noqa: N818
    """Raised by ``OperationRegistry.start`` when an op of this kind is live."""

    def __init__(self, existing_op_id: str) -> None:
        super().__init__(f"Operation {existing_op_id} already running for this kind.")
        self.existing_op_id = existing_op_id


@dataclass
class Event:
    seq: int
    kind: str
    data: dict[str, Any]
    at: datetime | None = None


@dataclass
class MeetingSlot:
    meeting_id: str
    title: str = ""
    sub_state: str = "queued"
    error: str | None = None


@dataclass
class _RunnerContext:
    """Public surface a runner sees: emit events, check cancel, allocate seqs."""

    meeting_ids: list[str]
    cancel_event: asyncio.Event
    _emit: Callable[[Event], None]
    _next: Callable[[], int]

    def emit(self, evt: Event) -> None:
        self._emit(evt)

    def next_seq(self) -> int:
        return self._next()


@dataclass
class Operation:
    id: str
    kind: OperationKind
    total: int
    state: str
    meetings: list[MeetingSlot]
    started_at: datetime
    finished_at: datetime | None
    cancel_event: asyncio.Event
    task: asyncio.Task[None]
    _events: list[Event] = field(default_factory=list)
    _subscribers: list[asyncio.Queue[Event]] = field(default_factory=list)
    _seq_counter: int = 0

    def replay_buffer(self) -> Iterator[Event]:
        """Snapshot iterator over events emitted so far (not a live cursor)."""
        return iter(list(self._events))

    async def subscribe(self) -> AsyncIterator[Event]:
        """Yield live events until a terminal ``operation_state`` arrives."""
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.append(q)
        try:
            while True:
                evt = await q.get()
                yield evt
                if evt.kind == "operation_state" and evt.data.get("state") in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }:
                    return
        finally:
            self._subscribers.remove(q)

    def _emit(self, evt: Event) -> None:
        self._events.append(evt)
        for q in self._subscribers:
            q.put_nowait(evt)


class OperationRegistry:
    """Per-process registry of live + recently-finished operations."""

    GC_AGE = timedelta(minutes=30)

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._ops: dict[str, Operation] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        kind: OperationKind,
        meeting_ids: list[str],
        runner: Callable[[_RunnerContext], Awaitable[None]],
    ) -> Operation:
        async with self._lock:
            for existing in self._ops.values():
                if existing.kind == kind and existing.state == "running":
                    raise SameKindAlreadyRunning(existing.id)
            op_id = f"op_{self._clock.now().strftime('%Y-%m-%dT%H-%M-%S')}_{secrets.token_hex(2)}"
            # create_task schedules the runner; it cannot run until we yield
            # back to the loop, so the dict write below is visible to it.
            op = Operation(
                id=op_id,
                kind=kind,
                total=len(meeting_ids),
                state="running",
                meetings=[MeetingSlot(meeting_id=mid) for mid in meeting_ids],
                started_at=self._clock.now(),
                finished_at=None,
                cancel_event=asyncio.Event(),
                task=asyncio.create_task(self._run(op_id, meeting_ids, runner)),
            )
            self._ops[op_id] = op
            return op

    def get(self, op_id: str) -> Operation:
        return self._ops[op_id]

    def cancel(self, op_id: str) -> None:
        op = self._ops[op_id]
        op.cancel_event.set()

    def has_active(self) -> bool:
        return any(o.state == "running" for o in self._ops.values())

    def gc(self) -> None:
        now = self._clock.now()
        for op_id, op in list(self._ops.items()):
            if op.finished_at is not None and (now - op.finished_at) > self.GC_AGE:
                del self._ops[op_id]

    async def _run(
        self,
        op_id: str,
        meeting_ids: list[str],
        runner: Callable[[_RunnerContext], Awaitable[None]],
    ) -> None:
        op = self._ops[op_id]

        def next_seq() -> int:
            op._seq_counter += 1
            return op._seq_counter

        ctx = _RunnerContext(
            meeting_ids=list(meeting_ids),
            cancel_event=op.cancel_event,
            _emit=op._emit,
            _next=next_seq,
        )
        try:
            await runner(ctx)
            terminal = "cancelled" if op.cancel_event.is_set() else "succeeded"
        except Exception as exc:
            op._emit(
                Event(
                    seq=next_seq(),
                    kind="operation_state",
                    data={"state": "failed", "error": str(exc)},
                )
            )
            terminal = "failed"
        op.state = terminal
        op.finished_at = self._clock.now()
        op._emit(Event(seq=next_seq(), kind="operation_state", data={"state": terminal}))
