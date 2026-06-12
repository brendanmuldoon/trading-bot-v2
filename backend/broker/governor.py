"""Rate-limit governor (T08, spec §8.2 / C5).

Every T212 call passes through here. One worker → never two requests
in flight; a priority queue orders traffic when budget is tight:
protective exits > entries > reconciliation > history sync.

Per-endpoint `x-ratelimit-*` state drives proactive throttling
(remaining < 20% of limit → wait for reset). 429s get exponential
backoff with jitter, max 5 retries; an exhausted protective request
emits a CRITICAL event and re-queues at top priority — never silently
dropped. Only 429s are retried here: ambiguous write outcomes
(AmbiguousResultError) propagate to the order workflow's verify step.

Clock/sleep/rng are injectable for deterministic tests.
"""

import asyncio
import contextlib
import itertools
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

import httpx

from backend.broker.errors import BrokerError, RateLimitedError
from backend.broker.t212 import T212Client
from backend.events import log_event
from backend.models import EventLevel

logger = logging.getLogger("bot.broker.governor")


class Priority(IntEnum):
    PROTECTIVE = 0  # exits / stops — must never starve
    ENTRY = 1
    RECONCILE = 2
    HISTORY = 3


class GovernorExhaustedError(BrokerError):
    """Retries exhausted — the calling cycle should be marked failed."""


@dataclass
class EndpointBudget:
    limit: int | None = None
    remaining: int | None = None
    reset_at: float | None = None  # epoch seconds

    def low(self) -> bool:
        if self.limit is None or self.remaining is None:
            return False
        return self.remaining < 0.2 * self.limit


@dataclass(order=True)
class _Job:
    priority: int
    seq: int
    method: str = field(compare=False)
    path: str = field(compare=False)
    json: dict[str, Any] | None = field(compare=False)
    params: dict[str, Any] | None = field(compare=False)
    future: asyncio.Future[httpx.Response] = field(compare=False)
    requeues: int = field(compare=False, default=0)


class Governor:
    MAX_RETRIES = 5
    MAX_PROTECTIVE_REQUEUES = 3

    def __init__(
        self,
        client: T212Client,
        *,
        spacing: float = 0.25,
        backoff_base: float = 0.5,
        backoff_cap: float = 30.0,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._client = client
        self._spacing = spacing
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._now = now
        self._sleep = sleep
        self._rng = rng
        self._queue: asyncio.PriorityQueue[_Job] = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._worker: asyncio.Task[None] | None = None
        self._last_request_at: float | None = None
        self.budgets: dict[str, EndpointBudget] = {}

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        priority: Priority,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Enqueue and await. Raises what the underlying call raised, or
        GovernorExhaustedError after a 429 storm."""
        loop = asyncio.get_running_loop()
        job = _Job(
            priority=int(priority),
            seq=next(self._seq),
            method=method,
            path=path,
            json=json,
            params=params,
            future=loop.create_future(),
        )
        await self._queue.put(job)
        return await job.future

    # ---- worker -------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            if job.future.cancelled():
                continue
            try:
                response = await self._execute(job)
            except Exception as exc:
                requeued = self._maybe_requeue_protective(job, exc)
                if not requeued and not job.future.cancelled():
                    job.future.set_exception(exc)
            else:
                if not job.future.cancelled():
                    job.future.set_result(response)

    async def _execute(self, job: _Job) -> httpx.Response:
        budget_key = _budget_key(job.path)
        for attempt in range(self.MAX_RETRIES + 1):
            await self._respect_spacing()
            await self._respect_budget(budget_key)
            try:
                response = await self._client.request(
                    job.method, job.path, json=job.json, params=job.params
                )
            except RateLimitedError as exc:
                if exc.reset_at is not None:
                    self.budgets.setdefault(budget_key, EndpointBudget()).remaining = 0
                    self.budgets[budget_key].reset_at = float(exc.reset_at)
                if attempt == self.MAX_RETRIES:
                    raise GovernorExhaustedError(
                        f"{job.method} {job.path}: 429 after {attempt + 1} attempts"
                    ) from exc
                backoff = min(self._backoff_base * 2**attempt, self._backoff_cap)
                await self._sleep(backoff * (1 + self._rng()))
                continue
            self._record_headers(budget_key, response)
            return response
        raise AssertionError("unreachable")

    async def _respect_spacing(self) -> None:
        if self._last_request_at is not None:
            elapsed = self._now() - self._last_request_at
            if elapsed < self._spacing:
                await self._sleep(self._spacing - elapsed)
        self._last_request_at = self._now()

    async def _respect_budget(self, budget_key: str) -> None:
        budget = self.budgets.get(budget_key)
        if budget and budget.low() and budget.reset_at is not None:
            delay = budget.reset_at - self._now()
            if delay > 0:
                logger.info("throttling %s for %.1fs (budget low)", budget_key, delay)
                await self._sleep(delay)
            budget.remaining = None  # stale after reset

    def _record_headers(self, budget_key: str, response: httpx.Response) -> None:
        headers = response.headers
        budget = self.budgets.setdefault(budget_key, EndpointBudget())
        if "x-ratelimit-limit" in headers:
            budget.limit = _int_or_none(headers["x-ratelimit-limit"])
        if "x-ratelimit-remaining" in headers:
            budget.remaining = _int_or_none(headers["x-ratelimit-remaining"])
        if "x-ratelimit-reset" in headers:
            reset = _int_or_none(headers["x-ratelimit-reset"])
            budget.reset_at = float(reset) if reset is not None else None

    def _maybe_requeue_protective(self, job: _Job, exc: Exception) -> bool:
        """A failed protective order is never dropped silently (§8.2)."""
        if job.priority != Priority.PROTECTIVE or not isinstance(exc, GovernorExhaustedError):
            return False
        log_event(
            EventLevel.CRITICAL,
            "PROTECTIVE_ORDER_FAILED",
            f"protective request {job.method} {job.path} exhausted retries",
            {"requeues": job.requeues},
        )
        if job.requeues >= self.MAX_PROTECTIVE_REQUEUES:
            return False
        job.requeues += 1
        job.seq = -next(self._seq)  # jump ahead of equal-priority jobs
        self._queue.put_nowait(job)
        return True


def _budget_key(path: str) -> str:
    """Orders/{id} share one budget with the orders collection."""
    tail = path.rsplit("/", 1)[-1]
    return path.rsplit("/", 1)[0] if tail.isdigit() else path


def _int_or_none(raw: str) -> int | None:
    return int(raw) if raw.lstrip("-").isdigit() else None
