"""Stateful fake of the T212 API (T06).

JSON shapes follow the OpenAPI bundle (re-fetched 2026-06-12; see
docs/t212-contract-notes.md for drift vs spec §8.1). Used via
httpx.MockTransport for unit tests; T11 wraps the same handler in a
real HTTP server for cross-process durability tests.

Simulates (per spec §13 / C5, C6, C10, C11):
- rate-limit headers on every response
- forced 429 bursts per endpoint
- market-order fills, partial fills, and no-fill
- rejected stop orders
- timeout-then-success with the duplicate-order hazard: the order IS
  created server-side even though the caller saw a timeout (C11)
- cursor pagination via nextPagePath (C10)
- negative-quantity sells (C6)
- re-POSTing an identical order creates a second order (C11)
"""

import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

import httpx

API = "/api/v0"

FillMode = Literal["full", "partial", "none"]

# Default per-endpoint limits taken from the OpenAPI bundle examples.
DEFAULT_LIMITS = {
    f"{API}/equity/account/summary": (1, 5),
    f"{API}/equity/metadata/instruments": (1, 50),
    f"{API}/equity/positions": (1, 1),
    f"{API}/equity/orders": (1, 5),
    f"{API}/equity/orders/market": (50, 60),
    f"{API}/equity/orders/stop": (1, 2),
    f"{API}/equity/history/orders": (6, 60),
    f"{API}/equity/history/transactions": (6, 60),
}


@dataclass
class FakeInstrument:
    ticker: str  # T212 ticker, e.g. SPY_US_EQ
    currency: str = "USD"
    name: str = ""
    type: str = "ETF"
    price: float = 100.0

    def metadata_json(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "type": self.type,
            "currencyCode": self.currency,
            "name": self.name or self.ticker,
            "shortName": self.ticker.split("_")[0],
            "isin": "US0000000000",
            "addedOn": "2020-01-01T00:00:00Z",
            "extendedHours": False,
            "maxOpenQuantity": 100000.0,
            "workingScheduleId": 1,
        }

    def instrument_json(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "currency": self.currency,
            "isin": "US0000000000",
            "name": self.name or self.ticker,
        }


@dataclass
class FakePosition:
    ticker: str
    quantity: float
    avg_price: float


@dataclass
class FakeOrder:
    id: int
    ticker: str
    quantity: float  # signed, as on the wire
    type: str
    status: str
    filled_quantity: float = 0.0
    stop_price: float | None = None
    limit_price: float | None = None
    created_at: str = "2026-06-12T14:00:00Z"
    currency: str = "USD"

    def json(self, instrument: FakeInstrument | None) -> dict[str, Any]:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "instrument": instrument.instrument_json() if instrument else None,
            "quantity": self.quantity,
            "filledQuantity": self.filled_quantity,
            "side": "SELL" if self.quantity < 0 else "BUY",
            "status": self.status,
            "type": self.type,
            "createdAt": self.created_at,
            "currency": self.currency,
            "timeInForce": "DAY",
            "limitPrice": self.limit_price,
            "stopPrice": self.stop_price,
            "strategy": "QUANTITY",
            "value": None,
            "filledValue": None,
            "extendedHours": False,
            "initiatedFrom": "API",
        }


class FakeT212:
    """In-memory T212. Build an httpx client with `.transport`."""

    def __init__(self, api_key: str = "key", api_secret: str = "secret") -> None:
        self._expected_auth = (
            "Basic " + base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        )
        self.currency = "USD"
        self.cash_available = 10_000.0
        self.instruments: dict[str, FakeInstrument] = {}
        self.positions: dict[str, FakePosition] = {}
        self.orders: dict[int, FakeOrder] = {}
        self.history_orders: list[dict[str, Any]] = []
        self.transactions: list[dict[str, Any]] = []
        self.fill_mode: FillMode = "full"
        self.partial_ratio = 0.5
        self.reject_stops = False
        self._next_order_id = 1000
        self._forced_429: dict[str, int] = {}
        self._forced_timeouts: dict[str, list[bool]] = {}  # path -> [place_order_anyway,...]
        self.limits: dict[str, tuple[int, int]] = dict(DEFAULT_LIMITS)
        self.request_log: list[tuple[str, str, dict[str, Any] | None]] = []

    # ---- test-scenario configuration -------------------------------------

    def add_instrument(self, ticker: str, currency: str = "USD", price: float = 100.0) -> None:
        self.instruments[ticker] = FakeInstrument(ticker=ticker, currency=currency, price=price)

    def set_price(self, ticker: str, price: float) -> None:
        self.instruments[ticker].price = price

    def queue_429(self, path: str, count: int) -> None:
        """Force the next `count` requests to `path` to return 429."""
        self._forced_429[path] = self._forced_429.get(path, 0) + count

    def queue_timeout(self, path: str, *, place_order: bool = False) -> None:
        """Force the next request to `path` to raise a timeout. With
        place_order=True the order is still created server-side first —
        the C11 ambiguous-result hazard."""
        self._forced_timeouts.setdefault(path, []).append(place_order)

    def seed_history_orders(self, count: int, ticker: str = "SPY_US_EQ") -> None:
        for i in range(count):
            order = FakeOrder(
                id=i + 1,
                ticker=ticker,
                quantity=1.0,
                type="MARKET",
                status="FILLED",
                filled_quantity=1.0,
            )
            self.history_orders.append(
                {
                    "order": order.json(self.instruments.get(ticker)),
                    "fill": {"price": 100.0 + i, "quantity": 1.0},
                }
            )

    def seed_transactions(self, count: int) -> None:
        for i in range(count):
            self.transactions.append(
                {
                    "amount": 100.0 + i,
                    "currency": self.currency,
                    "dateTime": "2026-06-12T14:00:00Z",
                    "reference": f"tx-{i + 1}",
                    "type": "DEPOSIT",
                }
            )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle_httpx)

    # ---- request handling --------------------------------------------------

    def _handle_httpx(self, request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        body: dict[str, Any] | None = None
        if request.content:
            body = json.loads(request.content)
        status, payload, headers = self.handle(
            request.method,
            parsed.path,
            {k: v[0] for k, v in parse_qs(parsed.query).items()},
            body,
            dict(request.headers),
        )
        return httpx.Response(status, json=payload, headers=headers)

    def handle(
        self,
        method: str,
        path: str,
        query: dict[str, str],
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[int, Any, dict[str, str]]:
        self.request_log.append((method, path, body))
        limit_path = path if not path.rsplit("/", 1)[-1].isdigit() else path.rsplit("/", 1)[0]
        rl_headers = self._rate_limit_headers(limit_path)

        if headers.get("authorization") != self._expected_auth:
            return 401, {"errorMessage": "Bad API key"}, rl_headers

        if self._forced_429.get(limit_path, 0) > 0:
            self._forced_429[limit_path] -= 1
            return 429, {"errorMessage": "Limited"}, {**rl_headers, "x-ratelimit-remaining": "0"}

        timeouts = self._forced_timeouts.get(limit_path)
        if timeouts:
            place_order = timeouts.pop(0)
            if place_order and method == "POST" and body is not None:
                self._create_order(path, body)  # the hazard: created despite timeout
            raise httpx.ReadTimeout(f"forced timeout on {path}")

        route = (method, path)
        if route == ("GET", f"{API}/equity/account/summary"):
            return 200, self._account_summary(), rl_headers
        if route == ("GET", f"{API}/equity/metadata/instruments"):
            return 200, [i.metadata_json() for i in self.instruments.values()], rl_headers
        if route == ("GET", f"{API}/equity/positions"):
            return 200, self._positions_json(query.get("ticker")), rl_headers
        if route == ("GET", f"{API}/equity/orders"):
            pending = [
                o.json(self.instruments.get(o.ticker))
                for o in self.orders.values()
                if o.status in ("NEW", "PARTIALLY_FILLED")
            ]
            return 200, pending, rl_headers
        if method == "GET" and path.startswith(f"{API}/equity/orders/"):
            order = self.orders.get(int(path.rsplit("/", 1)[-1]))
            if order is None:
                return 404, {"errorMessage": "Order not found"}, rl_headers
            return 200, order.json(self.instruments.get(order.ticker)), rl_headers
        if method == "DELETE" and path.startswith(f"{API}/equity/orders/"):
            order = self.orders.get(int(path.rsplit("/", 1)[-1]))
            if order is None:
                return 404, {"errorMessage": "Order not found"}, rl_headers
            if order.status in ("NEW", "PARTIALLY_FILLED"):
                order.status = "CANCELLED"
            return 200, None, rl_headers
        if route == ("POST", f"{API}/equity/orders/market"):
            assert body is not None
            order = self._create_order(path, body)
            return 200, order.json(self.instruments.get(order.ticker)), rl_headers
        if route == ("POST", f"{API}/equity/orders/stop"):
            assert body is not None
            if self.reject_stops:
                return 400, {"errorMessage": "Stop orders not supported"}, rl_headers
            order = self._create_order(path, body)
            return 200, order.json(self.instruments.get(order.ticker)), rl_headers
        if route == ("GET", f"{API}/equity/history/orders"):
            return 200, self._paginate(self.history_orders, path, query), rl_headers
        if route == ("GET", f"{API}/equity/history/transactions"):
            return 200, self._paginate(self.transactions, path, query), rl_headers

        return 404, {"errorMessage": f"no fake route for {method} {path}"}, rl_headers

    # ---- behavior ----------------------------------------------------------

    def _rate_limit_headers(self, path: str) -> dict[str, str]:
        limit, period = self.limits.get(path, (10, 60))
        return {
            "x-ratelimit-limit": str(limit),
            "x-ratelimit-period": str(period),
            "x-ratelimit-remaining": str(max(limit - 1, 0)),
            "x-ratelimit-used": "1",
            "x-ratelimit-reset": str(int(time.time()) + period),
        }

    def _account_summary(self) -> dict[str, Any]:
        invested = sum(
            p.quantity * self.instruments[p.ticker].price
            for p in self.positions.values()
            if p.ticker in self.instruments
        )
        return {
            "id": 12345678,
            "currency": self.currency,
            "totalValue": self.cash_available + invested,
            "cash": {
                "availableToTrade": self.cash_available,
                "inPies": 0.0,
                "reservedForOrders": 0.0,
            },
            "investments": {
                "currentValue": invested,
                "totalCost": invested,
                "realizedProfitLoss": 0.0,
                "unrealizedProfitLoss": 0.0,
            },
        }

    def _positions_json(self, ticker_filter: str | None) -> list[dict[str, Any]]:
        out = []
        for p in self.positions.values():
            if ticker_filter and p.ticker != ticker_filter:
                continue
            inst = self.instruments.get(p.ticker)
            price = inst.price if inst else p.avg_price
            out.append(
                {
                    "instrument": inst.instrument_json() if inst else {"ticker": p.ticker},
                    "quantity": p.quantity,
                    "quantityAvailableForTrading": p.quantity,
                    "quantityInPies": 0.0,
                    "averagePricePaid": p.avg_price,
                    "currentPrice": price,
                    "createdAt": "2026-06-12T14:00:00Z",
                    "walletImpact": {
                        "value": p.quantity * price,
                        "unrealizedProfitLoss": p.quantity * (price - p.avg_price),
                    },
                }
            )
        return out

    def _create_order(self, path: str, body: dict[str, Any]) -> FakeOrder:
        """Always creates a new order — identical re-POSTs duplicate (C11)."""
        order_type = "STOP" if path.endswith("/stop") else "MARKET"
        order = FakeOrder(
            id=self._next_order_id,
            ticker=body["ticker"],
            quantity=float(body["quantity"]),
            type=order_type,
            status="NEW",
            stop_price=body.get("stopPrice"),
        )
        self._next_order_id += 1
        self.orders[order.id] = order
        if order_type == "MARKET":
            self._fill(order)
        return order

    def _fill(self, order: FakeOrder) -> None:
        if self.fill_mode == "none":
            return
        ratio = self.partial_ratio if self.fill_mode == "partial" else 1.0
        order.filled_quantity = round(order.quantity * ratio, 8)
        order.status = "FILLED" if ratio >= 1.0 else "PARTIALLY_FILLED"
        self._apply_fill(order.ticker, order.filled_quantity)

    def fill_remainder(self, order_id: int) -> None:
        """Test hook: complete a partial fill."""
        order = self.orders[order_id]
        remainder = order.quantity - order.filled_quantity
        order.filled_quantity = order.quantity
        order.status = "FILLED"
        self._apply_fill(order.ticker, remainder)

    def _apply_fill(self, ticker: str, signed_qty: float) -> None:
        inst = self.instruments.get(ticker)
        price = inst.price if inst else 100.0
        pos = self.positions.get(ticker)
        if pos is None:
            self.positions[ticker] = FakePosition(
                ticker=ticker, quantity=signed_qty, avg_price=price
            )
        else:
            new_qty = pos.quantity + signed_qty
            if signed_qty > 0:
                pos.avg_price = (pos.avg_price * pos.quantity + price * signed_qty) / new_qty
            pos.quantity = new_qty
            if abs(pos.quantity) < 1e-9:
                del self.positions[ticker]
        self.cash_available -= signed_qty * price

    def _paginate(
        self, items: list[dict[str, Any]], path: str, query: dict[str, str]
    ) -> dict[str, Any]:
        limit = min(int(query.get("limit", "20")), 50)
        cursor = int(query.get("cursor", "0"))
        page = items[cursor : cursor + limit]
        next_cursor = cursor + limit
        next_page_path = (
            f"{path}?cursor={next_cursor}&limit={limit}" if next_cursor < len(items) else None
        )
        return {"items": page, "nextPagePath": next_page_path}


def make_client(
    fake: FakeT212, api_key: str = "key", api_secret: str = "secret"
) -> httpx.AsyncClient:
    """An httpx client wired to the fake with correct Basic auth."""
    return httpx.AsyncClient(
        base_url="https://demo.trading212.com",
        transport=fake.transport,
        auth=(api_key, api_secret),
    )
