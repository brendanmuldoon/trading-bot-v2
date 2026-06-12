"""Typed broker operations over the governor (T10).

Public API speaks `side: BUY|SELL` with positive quantities; the C6
negative-quantity sell convention lives ONLY here and never leaks to
strategy/risk code. Enforces the C12 sanity cap on pending orders per
ticker.
"""

from typing import Any

from backend.broker.errors import BrokerValidationError
from backend.broker.governor import Priority
from backend.broker.instruments import Requester
from backend.broker.models import BrokerOrder, BrokerPosition
from backend.models import OrderSide

API = "/api/v0"

# C12: T212 allows max 50 pending orders per ticker; trip well below it.
PENDING_ORDERS_CAP = 45


class PendingCapExceededError(BrokerValidationError):
    """Refusing to place an order — pending-per-ticker sanity cap hit."""


class BrokerAPI:
    def __init__(self, requester: Requester) -> None:
        self._requester = requester

    # ---- positions (C9: usable as quote fallback) ---------------------------

    async def get_positions(
        self, *, priority: Priority = Priority.RECONCILE
    ) -> list[BrokerPosition]:
        resp = await self._requester.request("GET", f"{API}/equity/positions", priority=priority)
        return [BrokerPosition.model_validate(item) for item in resp.json()]

    # ---- orders --------------------------------------------------------------

    async def get_open_orders(
        self, *, priority: Priority = Priority.RECONCILE
    ) -> list[BrokerOrder]:
        resp = await self._requester.request("GET", f"{API}/equity/orders", priority=priority)
        return [BrokerOrder.model_validate(item) for item in resp.json()]

    async def get_order(
        self, order_id: int, *, priority: Priority = Priority.RECONCILE
    ) -> BrokerOrder:
        resp = await self._requester.request(
            "GET", f"{API}/equity/orders/{order_id}", priority=priority
        )
        return BrokerOrder.model_validate(resp.json())

    async def get_recent_filled_orders(
        self, *, priority: Priority = Priority.RECONCILE, limit: int = 50
    ) -> list[BrokerOrder]:
        """First page of order history, newest orders included — used by
        the T11 verify step to spot an already-executed ambiguous submit."""
        resp = await self._requester.request(
            "GET",
            f"{API}/equity/history/orders",
            priority=priority,
            params={"limit": limit},
        )
        items = resp.json().get("items", [])
        return [BrokerOrder.model_validate(item["order"]) for item in items if item.get("order")]

    async def cancel_order(self, order_id: int, *, priority: Priority = Priority.ENTRY) -> None:
        await self._requester.request(
            "DELETE", f"{API}/equity/orders/{order_id}", priority=priority
        )

    async def place_market_order(
        self,
        ticker: str,
        side: OrderSide,
        quantity: float,
        *,
        priority: Priority,
    ) -> BrokerOrder:
        """`quantity` must be positive; the sign convention is internal."""
        payload = {"ticker": ticker, "quantity": _signed(side, quantity)}
        await self._enforce_pending_cap(ticker)
        resp = await self._requester.request(
            "POST", f"{API}/equity/orders/market", priority=priority, json=payload
        )
        return BrokerOrder.model_validate(resp.json())

    async def place_stop_order(
        self,
        ticker: str,
        side: OrderSide,
        quantity: float,
        stop_price: float,
        *,
        priority: Priority = Priority.PROTECTIVE,
        time_validity: str = "GOOD_TILL_CANCEL",
    ) -> BrokerOrder:
        payload: dict[str, Any] = {
            "ticker": ticker,
            "quantity": _signed(side, quantity),
            "stopPrice": stop_price,
            "timeValidity": time_validity,
        }
        await self._enforce_pending_cap(ticker)
        resp = await self._requester.request(
            "POST", f"{API}/equity/orders/stop", priority=priority, json=payload
        )
        return BrokerOrder.model_validate(resp.json())

    async def _enforce_pending_cap(self, ticker: str) -> None:
        pending = [o for o in await self.get_open_orders() if o.ticker == ticker]
        if len(pending) >= PENDING_ORDERS_CAP:
            raise PendingCapExceededError(
                f"{ticker}: {len(pending)} pending orders ≥ cap {PENDING_ORDERS_CAP} (C12)"
            )


def _signed(side: OrderSide, quantity: float) -> float:
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    return quantity if side is OrderSide.BUY else -quantity
