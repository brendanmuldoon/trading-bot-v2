"""Verify-before-resubmit matching (T11, §8.4 step-2 recovery rule).

T212's POST is not idempotent (C11), so after an ambiguous submit we
look for a broker order matching the intent's context (ticker, side,
quantity, recency) that no other DB order has already claimed. Found →
adopt it; not found → safe to resubmit.
"""

from backend.broker.api import BrokerAPI
from backend.broker.governor import Priority
from backend.broker.models import BrokerOrder
from backend.models import OrderSide


def match_order(
    candidates: list[BrokerOrder],
    *,
    ticker: str,
    side: OrderSide,
    quantity: float,
    claimed_ids: set[int],
    qty_tolerance: float = 1e-6,
) -> BrokerOrder | None:
    """Pick the first unclaimed broker order matching the intent."""
    for order in candidates:
        if order.id in claimed_ids:
            continue
        if order.ticker != ticker or order.side != side.value:
            continue
        if abs(order.abs_quantity - quantity) > qty_tolerance:
            continue
        return order
    return None


async def find_existing_order(
    broker: BrokerAPI,
    *,
    ticker: str,
    side: OrderSide,
    quantity: float,
    claimed_ids: set[int],
) -> BrokerOrder | None:
    """Fetch open orders + recent history and match. Recency is implicit:
    open orders are current, and we search history newest-first."""
    open_orders = await broker.get_open_orders(priority=Priority.RECONCILE)
    found = match_order(
        open_orders, ticker=ticker, side=side, quantity=quantity, claimed_ids=claimed_ids
    )
    if found is not None:
        return found

    recent = await broker.get_recent_filled_orders(priority=Priority.RECONCILE)
    return match_order(recent, ticker=ticker, side=side, quantity=quantity, claimed_ids=claimed_ids)
