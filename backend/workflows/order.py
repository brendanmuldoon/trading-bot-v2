"""Durable per-order DBOS workflow (T11, §8.4).

Step 1 records the intent row (PENDING_SUBMIT, local client_ref).
Step 2 submits with verify-first semantics: because DBOS re-runs an
interrupted step from scratch and T212's POST is not idempotent (C11),
the step ALWAYS verifies before POSTing — a crash mid-POST or an
AmbiguousResultError both funnel into adopt-or-resubmit.
Step 3 polls to a terminal status and updates the order row; partial
fills follow actual fills, with the remainder handled by the monitor
loop (T28 hook: cancel after 2 cycles).

Protective orders enqueue at higher priority than entries on the same
priority-enabled DBOS queue, so rate-limit pressure never starves an
exit.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from dbos import DBOS, Queue, SetEnqueueOptions
from sqlalchemy import select

from backend.broker.errors import AmbiguousResultError
from backend.broker.governor import Priority
from backend.broker.verify import find_existing_order
from backend.models import Order, OrderSide, OrderStatus, OrderType
from backend.workflows import runtime

ORDER_QUEUE = "orders"
QUEUE_PRIORITY_PROTECTIVE = 1
QUEUE_PRIORITY_ENTRY = 10

# Declarative registration (works at import time, before DBOS() exists).
# Tight polling: order latency matters (protective exits ride this queue).
order_queue = Queue(ORDER_QUEUE, priority_enabled=True, concurrency=1, polling_interval_sec=0.25)

CONFIRM_ATTEMPTS = 10
CONFIRM_DELAY_SECONDS = 0.5

_TERMINAL = {
    "FILLED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "REPLACED": OrderStatus.CANCELLED,
}


def submit_order(
    *,
    strategy_id: int,
    ticker: str,
    side: OrderSide,
    quantity: float,
    reason: str,
    protective: bool,
) -> Any:
    """Single entry point: enqueue a durable order workflow (§3 — the
    one order path). Returns a DBOS workflow handle."""
    request = {
        "strategy_id": strategy_id,
        "ticker": ticker,
        "side": side.value,
        "quantity": quantity,
        "reason": reason,
        "protective": protective,
        "client_ref": f"bot-{uuid.uuid4().hex[:20]}",
    }
    priority = QUEUE_PRIORITY_PROTECTIVE if protective else QUEUE_PRIORITY_ENTRY
    with SetEnqueueOptions(priority=priority):
        return order_queue.enqueue(order_workflow, request)


@DBOS.workflow()
async def order_workflow(request: dict[str, Any]) -> dict[str, Any]:
    order_id = await step_record_intent(request)
    submit = await step_submit_or_adopt(request, order_id)
    final = await step_confirm(request, order_id, submit["t212_order_id"])
    return {"order_id": order_id, **submit, **final}


@DBOS.step()
async def step_record_intent(request: dict[str, Any]) -> int:
    """Step 1: the audit-trail row exists before anything hits the wire."""
    with runtime.session_factory()() as session:
        existing = session.scalar(select(Order).where(Order.client_ref == request["client_ref"]))
        if existing is not None:  # recovery after checkpoint loss
            return existing.id
        order = Order(
            client_ref=request["client_ref"],
            strategy_id=request["strategy_id"],
            symbol=request["ticker"],
            side=OrderSide(request["side"]),
            qty=Decimal(str(request["quantity"])),
            type=OrderType.MARKET,
            status=OrderStatus.PENDING_SUBMIT,
            requested_at=datetime.now(UTC),
            reason=request["reason"],
        )
        session.add(order)
        session.commit()
        return order.id


@DBOS.step(retries_allowed=True, max_attempts=3)
async def step_submit_or_adopt(request: dict[str, Any], order_id: int) -> dict[str, Any]:
    """Step 2: verify-first submit. Never blindly POSTs after ambiguity.

    Step retries are safe precisely because the step is verify-first:
    a transient read failure (BrokerTimeoutError on the verify GETs)
    re-runs the whole step, which re-verifies before any POST."""
    broker = runtime.broker()
    side = OrderSide(request["side"])
    quantity = float(request["quantity"])
    priority = Priority.PROTECTIVE if request["protective"] else Priority.ENTRY

    existing = await find_existing_order(
        broker,
        ticker=request["ticker"],
        side=side,
        quantity=quantity,
        claimed_ids=_claimed_t212_ids(exclude_order_id=order_id),
    )
    if existing is not None:
        _store_submission(order_id, existing.id, adopted=True)
        return {"t212_order_id": existing.id, "adopted": True}

    try:
        placed = await broker.place_market_order(
            request["ticker"], side, quantity, priority=priority
        )
    except AmbiguousResultError:
        found = await find_existing_order(
            broker,
            ticker=request["ticker"],
            side=side,
            quantity=quantity,
            claimed_ids=_claimed_t212_ids(exclude_order_id=order_id),
        )
        if found is not None:
            _store_submission(order_id, found.id, adopted=True)
            return {"t212_order_id": found.id, "adopted": True}
        placed = await broker.place_market_order(  # verified absent → safe
            request["ticker"], side, quantity, priority=priority
        )

    _store_submission(order_id, placed.id, adopted=False)
    return {"t212_order_id": placed.id, "adopted": False}


@DBOS.step(retries_allowed=True, max_attempts=3)
async def step_confirm(
    request: dict[str, Any], order_id: int, t212_order_id: int
) -> dict[str, Any]:
    """Step 3: poll to terminal status; partial fills tracked from actual
    fills. A still-pending remainder is left for the monitor loop (T28)."""
    broker = runtime.broker()
    status = OrderStatus.SUBMITTED
    filled_qty = 0.0
    raw: dict[str, Any] | None = None

    for _ in range(CONFIRM_ATTEMPTS):
        remote = await broker.get_order(t212_order_id, priority=Priority.RECONCILE)
        raw = remote.model_dump()
        filled_qty = remote.abs_filled_quantity
        if remote.status in _TERMINAL:
            status = _TERMINAL[remote.status]
            break
        if remote.status == "PARTIALLY_FILLED":
            status = OrderStatus.PARTIALLY_FILLED
        await asyncio.sleep(CONFIRM_DELAY_SECONDS)

    with runtime.session_factory()() as session:
        order = session.get(Order, order_id)
        assert order is not None
        order.status = status
        order.raw_response = raw
        if filled_qty > 0:
            order.filled_at = datetime.now(UTC)
        session.commit()

    return {"status": status.value, "filled_quantity": filled_qty}


def _claimed_t212_ids(*, exclude_order_id: int) -> set[int]:
    """T212 order ids already attributed to other DB orders — an adopted
    order must not be claimed twice."""
    with runtime.session_factory()() as session:
        rows = session.scalars(
            select(Order.t212_order_id).where(
                Order.t212_order_id.is_not(None), Order.id != exclude_order_id
            )
        ).all()
    return {int(r) for r in rows if r is not None}


def _store_submission(order_id: int, t212_order_id: int, *, adopted: bool) -> None:
    with runtime.session_factory()() as session:
        order = session.get(Order, order_id)
        assert order is not None
        order.t212_order_id = str(t212_order_id)
        order.status = OrderStatus.SUBMITTED
        if adopted and order.reason:
            order.reason = f"{order.reason} [adopted after ambiguous submit]"
        session.commit()
