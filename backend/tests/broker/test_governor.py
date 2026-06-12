"""T08: governor — serialization, header tracking, throttling, backoff,
priority ordering, protective escalation."""

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from backend.broker.errors import RateLimitedError
from backend.broker.governor import EndpointBudget, Governor, GovernorExhaustedError, Priority

ORDERS_PATH = "/api/v0/equity/orders/market"
SUMMARY_PATH = "/api/v0/equity/account/summary"


class StubClient:
    """Scriptable stand-in for T212Client.request."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.responses: dict[str, list[Any]] = {}
        self.default_headers: dict[str, str] = {}
        self.gate: asyncio.Event | None = None

    def script(self, path: str, *outcomes: Any) -> None:
        self.responses.setdefault(path, []).extend(outcomes)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.gate is not None:
                await self.gate.wait()
            await asyncio.sleep(0)
            self.calls.append((method, path))
            queued = self.responses.get(path)
            outcome: Any = queued.pop(0) if queued else None
            if isinstance(outcome, Exception):
                raise outcome
            if isinstance(outcome, httpx.Response):
                return outcome
            return httpx.Response(200, json={}, headers=self.default_headers)
        finally:
            self.in_flight -= 1


def make_governor(stub: StubClient, **kwargs: Any) -> tuple[Governor, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0)

    governor = Governor(
        stub,  # type: ignore[arg-type]
        spacing=kwargs.pop("spacing", 0.0),
        backoff_base=kwargs.pop("backoff_base", 0.5),
        sleep=kwargs.pop("sleep", fake_sleep),
        rng=kwargs.pop("rng", lambda: 0.5),
        **kwargs,
    )
    return governor, sleeps


@pytest.fixture()
def stub() -> StubClient:
    return StubClient()


@pytest.fixture(autouse=True)
def capture_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str]]]:
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "backend.broker.governor.log_event",
        lambda level, code, message, context=None: captured.append((level.value, code)),
    )
    yield captured


async def test_no_two_requests_in_flight(stub: StubClient) -> None:
    governor, _ = make_governor(stub)
    await governor.start()
    try:
        await asyncio.gather(
            *(governor.request("GET", SUMMARY_PATH, priority=Priority.RECONCILE) for _ in range(8))
        )
    finally:
        await governor.stop()
    assert stub.max_in_flight == 1
    assert len(stub.calls) == 8


async def test_header_state_stored_per_endpoint(stub: StubClient) -> None:
    stub.script(
        SUMMARY_PATH,
        httpx.Response(
            200,
            json={},
            headers={
                "x-ratelimit-limit": "50",
                "x-ratelimit-remaining": "49",
                "x-ratelimit-reset": "1780000000",
            },
        ),
    )
    governor, _ = make_governor(stub)
    await governor.start()
    try:
        await governor.request("GET", SUMMARY_PATH, priority=Priority.RECONCILE)
    finally:
        await governor.stop()
    budget = governor.budgets[SUMMARY_PATH]
    assert (budget.limit, budget.remaining, budget.reset_at) == (50, 49, 1780000000.0)


async def test_low_budget_delays_until_reset(stub: StubClient) -> None:
    now = time.time()
    governor, sleeps = make_governor(stub, now=lambda: now)
    governor.budgets[SUMMARY_PATH] = EndpointBudget(limit=50, remaining=5, reset_at=now + 12.0)
    await governor.start()
    try:
        await governor.request("GET", SUMMARY_PATH, priority=Priority.RECONCILE)
    finally:
        await governor.stop()
    assert any(abs(s - 12.0) < 0.01 for s in sleeps), f"no throttle sleep in {sleeps}"
    assert len(stub.calls) == 1


async def test_healthy_budget_does_not_throttle(stub: StubClient) -> None:
    now = time.time()
    governor, sleeps = make_governor(stub, now=lambda: now)
    governor.budgets[SUMMARY_PATH] = EndpointBudget(limit=50, remaining=30, reset_at=now + 12.0)
    await governor.start()
    try:
        await governor.request("GET", SUMMARY_PATH, priority=Priority.RECONCILE)
    finally:
        await governor.stop()
    assert sleeps == []


async def test_429_backoff_with_jitter_then_success(stub: StubClient) -> None:
    stub.script(
        SUMMARY_PATH,
        RateLimitedError("limited", reset_at=None),
        RateLimitedError("limited", reset_at=None),
        httpx.Response(200, json={"ok": True}),
    )
    governor, sleeps = make_governor(stub)
    await governor.start()
    try:
        resp = await governor.request("GET", SUMMARY_PATH, priority=Priority.RECONCILE)
    finally:
        await governor.stop()
    assert resp.json() == {"ok": True}
    # rng=0.5 → backoff * 1.5: 0.5*1.5, 1.0*1.5
    assert sleeps == [0.75, 1.5]


async def test_429_storm_exhausts_after_5_retries(stub: StubClient) -> None:
    stub.script(SUMMARY_PATH, *[RateLimitedError("limited") for _ in range(10)])
    governor, sleeps = make_governor(stub)
    await governor.start()
    try:
        with pytest.raises(GovernorExhaustedError):
            await governor.request("GET", SUMMARY_PATH, priority=Priority.RECONCILE)
    finally:
        await governor.stop()
    assert len(stub.calls) == 6  # initial + 5 retries
    assert len(sleeps) == 5


async def test_protective_failure_emits_critical_and_requeues(
    stub: StubClient, capture_events: list[tuple[str, str]]
) -> None:
    stub.script(ORDERS_PATH, *[RateLimitedError("limited") for _ in range(50)])
    governor, _ = make_governor(stub)
    await governor.start()
    try:
        with pytest.raises(GovernorExhaustedError):
            await governor.request("POST", ORDERS_PATH, priority=Priority.PROTECTIVE)
    finally:
        await governor.stop()
    # 1 original run + 3 requeues, each emitting CRITICAL on exhaustion
    assert capture_events.count(("CRITICAL", "PROTECTIVE_ORDER_FAILED")) == 4
    assert len(stub.calls) == 24  # 4 cycles x 6 attempts


async def test_non_protective_failure_does_not_requeue(
    stub: StubClient, capture_events: list[tuple[str, str]]
) -> None:
    stub.script(SUMMARY_PATH, *[RateLimitedError("limited") for _ in range(10)])
    governor, _ = make_governor(stub)
    await governor.start()
    try:
        with pytest.raises(GovernorExhaustedError):
            await governor.request("GET", SUMMARY_PATH, priority=Priority.HISTORY)
    finally:
        await governor.stop()
    assert capture_events == []
    assert len(stub.calls) == 6


async def test_priority_ordering_under_contention(stub: StubClient) -> None:
    stub.gate = asyncio.Event()
    governor, _ = make_governor(stub)
    await governor.start()
    try:
        first = asyncio.create_task(governor.request("GET", "/first", priority=Priority.HISTORY))
        await asyncio.sleep(0.05)  # worker now blocked on the gate

        tasks = [
            asyncio.create_task(governor.request("GET", "/history", priority=Priority.HISTORY)),
            asyncio.create_task(governor.request("GET", "/reconcile", priority=Priority.RECONCILE)),
            asyncio.create_task(governor.request("POST", "/entry", priority=Priority.ENTRY)),
            asyncio.create_task(
                governor.request("POST", "/protective", priority=Priority.PROTECTIVE)
            ),
        ]
        await asyncio.sleep(0.05)  # all enqueued while worker is blocked
        stub.gate.set()
        await asyncio.gather(first, *tasks)
    finally:
        await governor.stop()

    order = [path for _, path in stub.calls]
    assert order == ["/first", "/protective", "/entry", "/reconcile", "/history"]
