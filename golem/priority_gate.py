"""Priority-aware concurrency gate for session scheduling.

Up to *max_concurrent* tasks can run simultaneously.  When all slots are
taken, waiting tasks are served in priority order (lower number = higher
priority).  Within the same priority level, tasks are served FIFO.

Usage::

    gate = PriorityGate(max_concurrent=3)

    async with gate.slot(priority=2):
        await do_work()
"""

import asyncio
import heapq
import itertools
import logging

logger = logging.getLogger("golem.priority_gate")

_counter = itertools.count()


class _Slot:
    """Async context manager returned by ``PriorityGate.slot``."""

    def __init__(self, gate: "PriorityGate", priority: int):
        self._gate = gate
        self._priority = priority
        self._event: asyncio.Event | None = None
        self._seq = next(_counter)

    async def __aenter__(self):
        async with self._gate._lock:
            if self._gate._running < self._gate._max:
                self._gate._running += 1
                return self

            self._event = asyncio.Event()
            entry = (self._priority, self._seq, self._event)
            heapq.heappush(self._gate._waiters, entry)

        try:
            await self._event.wait()
        except asyncio.CancelledError:
            async with self._gate._lock:
                self._gate._waiters = [
                    w for w in self._gate._waiters if w[2] is not self._event
                ]
                heapq.heapify(self._gate._waiters)
            raise
        return self

    async def __aexit__(self, *exc):
        async with self._gate._lock:
            if self._gate._waiters:
                _, _, event = heapq.heappop(self._gate._waiters)
                event.set()
            else:
                self._gate._running -= 1


class PriorityGate:
    """Concurrency gate that serves waiters in priority order.

    Args:
        max_concurrent: Maximum number of tasks allowed to run at once.

    Priority values: lower number = higher priority.
    Tasks with equal priority are served in arrival order (FIFO).
    """

    DEFAULT_PRIORITY = 5

    def __init__(self, max_concurrent: int = 3):
        self._max = max_concurrent
        self._running = 0
        self._waiters: list[tuple[int, int, asyncio.Event]] = []
        self._lock = asyncio.Lock()

    def slot(self, priority: int = DEFAULT_PRIORITY) -> _Slot:
        """Return an async context manager that blocks until a slot is available."""
        return _Slot(self, priority)

    @property
    def running_count(self) -> int:
        return self._running

    @property
    def waiting_count(self) -> int:
        return len(self._waiters)

    @property
    def max_concurrent(self) -> int:
        return self._max
