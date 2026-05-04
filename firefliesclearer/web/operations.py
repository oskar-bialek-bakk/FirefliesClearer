"""In-memory registry for long-running archive/purge operations.

A single ``OperationRegistry`` is attached to ``app.state.operation_registry``
by ``create_app`` (wired by Task 5.4 / 5.5 / 5.6). Each ``Operation`` owns its
own asyncio task, cooperative cancel flag, replay buffer, and live subscriber
queues.  See spec sections 10.1-10.2 for the design contract.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from firefliesclearer.ports.clock import Clock

logger = logging.getLogger(__name__)

_TERMINAL_STATES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})


class OperationKind(StrEnum):
    ARCHIVE = "archive"
    PURGE = "purge"
    RETRY_ARCHIVE = "retry-archive"
    RETRY_PURGE = "retry-purge"
    # Bulk "Retry all" from the dashboard's needs-attention list. Mixes
    # archive- and purge-style retries in a single op so a busy user can
    # clear the whole list with one click.
    RETRY_ATTENTION = "retry-attention"


class SameKindAlreadyRunning(RuntimeError):  # noqa: N818
    """Raised by ``OperationRegistry.start`` when an op of this kind is live."""

    def __init__(self, existing_op_id: str) -> None:
        super().__init__(f"Operation {existing_op_id} already running for this kind.")
        self.existing_op_id = existing_op_id


class MeetingAlreadyInProgress(RuntimeError):  # noqa: N818
    """Raised when a new op would overlap an in-flight op on the same meeting.

    Without this guard, a user with /history open could click Retry on a row
    that the dashboard's Retry-all has already queued — the registry's
    same-kind check doesn't catch this because RETRY_ATTENTION is a
    different kind from RETRY_ARCHIVE / RETRY_PURGE. Two coroutines then
    race the manifest state machine for one meeting.
    """

    def __init__(self, meeting_id: str, existing_op_id: str) -> None:
        super().__init__(f"Meeting {meeting_id} is already in operation {existing_op_id}.")
        self.meeting_id = meeting_id
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
        """Yield every event in this operation, including those emitted before
        subscription, with no gap window. Terminates on terminal operation_state.
        """
        q: asyncio.Queue[Event] = asyncio.Queue()
        # Register BEFORE snapshotting so any concurrent _emit() reaches us.
        self._subscribers.append(q)
        try:
            snapshot = list(self._events)
            seen_seqs: set[int] = set()
            for evt in snapshot:
                seen_seqs.add(evt.seq)
                yield evt
                if evt.kind == "operation_state" and evt.data.get("state") in _TERMINAL_STATES:
                    return
            while True:
                evt = await q.get()
                if (
                    evt.seq in seen_seqs
                ):  # pragma: no cover — asyncio single-thread prevents this race
                    continue
                yield evt
                if evt.kind == "operation_state" and evt.data.get("state") in _TERMINAL_STATES:
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
            requested = set(meeting_ids)
            for existing in self._ops.values():
                if existing.state != "running":
                    continue
                if existing.kind == kind:
                    raise SameKindAlreadyRunning(existing.id)
                # Per-meeting interlock: if any existing running op shares a
                # meeting id with this one, refuse. Catches the cross-kind
                # race (RETRY_ATTENTION batch vs. a single RETRY_ARCHIVE /
                # RETRY_PURGE on the same meeting).
                overlap = requested.intersection(s.meeting_id for s in existing.meetings)
                if overlap:
                    raise MeetingAlreadyInProgress(next(iter(overlap)), existing.id)
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
        # Safe without self._lock: sync method, no awaits, runs atomically
        # relative to other coroutines in this event loop.
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
            terminal_data: dict[str, Any] = {"state": terminal}
        except Exception as exc:
            logger.exception("Operation %s failed", op_id)
            terminal = "failed"
            terminal_data = {"state": terminal, "error": str(exc)}
        op.state = terminal
        op.finished_at = self._clock.now()
        op._emit(Event(seq=next_seq(), kind="operation_state", data=terminal_data))
