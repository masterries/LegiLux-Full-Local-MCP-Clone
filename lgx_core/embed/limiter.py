"""Throughput limiter of the embeddings API (PLAN §6.5, ``config/embedding.yaml``).

One rule, shared by ``lgx embed`` (bulk, build side) and the MCP server's query embedding (airgap):

* at most ``max_concurrency`` requests are in flight at any time, and
* each request **slot** waits ``cooldown_s`` after its request completed before it is reused.

So a slot is *occupied* from the start of its request until ``cooldown_s`` after the end of that request, and at
most ``max_concurrency`` slots are occupied at once. The effective throughput is therefore bounded by
``max_concurrency × batch_size / (latency + cooldown_s)`` texts per second (docs/dev/DEVELOPMENT.md § Embeddings).

``clock`` and ``sleep`` are injectable so tests can drive the limiter with a fake clock (no real waiting).
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class ThrottledError(RuntimeError):
    """No request slot became free within the caller's wait limit."""


class SlotLimiter:
    """``max_concurrency`` request slots with a per-slot cooldown after every request."""

    def __init__(
        self, max_concurrency: int, cooldown_s: float, *, clock: Clock | None = None, sleep: Sleep | None = None
    ) -> None:
        self.max_concurrency = max(1, int(max_concurrency))
        self.cooldown_s = max(0.0, float(cooldown_s))
        self._clock: Clock = clock or time.monotonic
        self._sleep: Sleep = sleep or asyncio.sleep
        self._in_use = [False] * self.max_concurrency
        self._free_at = [-math.inf] * self.max_concurrency  # when an idle slot may be reused
        self._released = asyncio.Event()
        self.in_flight = 0
        self.max_in_flight = 0
        self.requests = 0
        self.waited_s = 0.0

    def occupied(self) -> int:
        """Slots in flight or cooling down right now."""
        now = self._clock()
        return sum(1 for used, free in zip(self._in_use, self._free_at, strict=True) if used or free > now)

    def next_free_in(self) -> float:
        """Seconds until a slot is free (0 = one is free now; inf = all in flight)."""
        now = self._clock()
        idle = [free for used, free in zip(self._in_use, self._free_at, strict=True) if not used]
        return max(0.0, min(idle) - now) if idle else math.inf

    async def acquire(self, timeout: float | None = None) -> int:
        """Wait for a free slot (at most ``timeout`` seconds, else :class:`ThrottledError`); returns its index."""
        start = self._clock()
        while True:
            now = self._clock()
            idle = [i for i, used in enumerate(self._in_use) if not used]
            ready = [i for i in idle if self._free_at[i] <= now]
            if ready:
                slot = min(ready, key=lambda i: self._free_at[i])
                self._in_use[slot] = True
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
                self.requests += 1
                self.waited_s += now - start
                return slot
            wait: float | None = min(self._free_at[i] for i in idle) - now if idle else None
            if timeout is not None:
                deadline = start + timeout
                left = deadline - now
                # no slot can be free in time: every idle slot cools down past the deadline, and a slot in flight
                # would cool down for cooldown_s after its release — fail at once instead of sleeping the whole
                # timeout for nothing (the MCP query path: 5 s timeout, 20 s cooldown, M2 review)
                idle_late = not idle or min(self._free_at[i] for i in idle) > deadline
                hopeless = idle_late and (self.in_flight == 0 or now + self.cooldown_s > deadline)
                if left <= 0 or hopeless:
                    raise ThrottledError(
                        f"no embedding request slot free within {timeout:g} s "
                        f"(max_concurrency {self.max_concurrency}, cooldown {self.cooldown_s:g} s)"
                    )
                wait = left if wait is None else min(wait, left)
            await self._wait(wait)

    def release(self, slot: int) -> None:
        """The request of ``slot`` completed: the slot cools down for ``cooldown_s`` before it is reused."""
        if not self._in_use[slot]:
            raise RuntimeError(f"slot {slot} released twice")
        self._in_use[slot] = False
        self._free_at[slot] = self._clock() + self.cooldown_s
        self.in_flight -= 1
        released, self._released = self._released, asyncio.Event()
        released.set()

    @contextlib.asynccontextmanager
    async def slot(self, timeout: float | None = None) -> AsyncIterator[int]:
        """``async with limiter.slot():`` around exactly one HTTP request."""
        index = await self.acquire(timeout)
        try:
            yield index
        finally:
            self.release(index)

    async def _wait(self, seconds: float | None) -> None:
        """Until a slot is released or ``seconds`` passed (``None``: until a release)."""
        released = self._released
        if seconds is None:
            await released.wait()
            return
        waiter = asyncio.ensure_future(released.wait())
        sleeper = asyncio.ensure_future(self._sleep(max(0.0, seconds)))
        try:
            await asyncio.wait({waiter, sleeper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (waiter, sleeper):
                if not task.done():
                    task.cancel()
            for task in (waiter, sleeper):
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def stats(self) -> dict[str, float | int]:
        return {
            "max_concurrency": self.max_concurrency,
            "cooldown_s": self.cooldown_s,
            "requests": self.requests,
            "max_in_flight": self.max_in_flight,
            "waited_s": round(self.waited_s, 3),
        }


def estimate_duration_s(requests: int, max_concurrency: int, cooldown_s: float, latency_s: float) -> float:
    """Wall time of ``requests`` under the limiter: waves of ``max_concurrency`` requests, each wave taking
    ``latency + cooldown`` (the last one without the cooldown)."""
    if requests <= 0:
        return 0.0
    waves = math.ceil(requests / max(1, max_concurrency))
    return waves * (latency_s + cooldown_s) - cooldown_s


__all__ = ["SlotLimiter", "ThrottledError", "estimate_duration_s"]
