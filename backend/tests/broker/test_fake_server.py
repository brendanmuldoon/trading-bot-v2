"""T06: the fake T212 server's own behavior tests."""

from typing import Any

import httpx
import pytest

from backend.tests.broker.fake_t212 import API, FakeT212, make_client


@pytest.fixture()
def fake() -> FakeT212:
    f = FakeT212()
    f.add_instrument("SPY_US_EQ", price=500.0)
    f.add_instrument("GLD_US_EQ", price=180.0)
    return f


async def test_requires_basic_auth(fake: FakeT212) -> None:
    async with httpx.AsyncClient(
        base_url="https://demo.trading212.com", transport=fake.transport
    ) as client:
        resp = await client.get(f"{API}/equity/account/summary")
    assert resp.status_code == 401


async def test_account_summary_shape(fake: FakeT212) -> None:
    async with make_client(fake) as client:
        resp = await client.get(f"{API}/equity/account/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["currency"] == "USD"
    assert body["cash"]["availableToTrade"] == 10_000.0
    assert "x-ratelimit-remaining" in resp.headers
    assert "x-ratelimit-reset" in resp.headers


async def test_market_buy_fills_and_creates_position(fake: FakeT212) -> None:
    async with make_client(fake) as client:
        resp = await client.post(
            f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": 2}
        )
        body = resp.json()
        assert body["status"] == "FILLED"
        assert body["side"] == "BUY"
        assert body["filledQuantity"] == 2

        positions = (await client.get(f"{API}/equity/positions")).json()
    assert len(positions) == 1
    assert positions[0]["instrument"]["ticker"] == "SPY_US_EQ"
    assert positions[0]["quantity"] == 2
    assert fake.cash_available == 10_000.0 - 2 * 500.0


async def test_negative_quantity_is_a_sell(fake: FakeT212) -> None:
    async with make_client(fake) as client:
        await client.post(
            f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": 2}
        )
        resp = await client.post(
            f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": -2}
        )
        body = resp.json()
        assert body["side"] == "SELL"
        positions = (await client.get(f"{API}/equity/positions")).json()
    assert positions == []  # position closed out


async def test_partial_fill_mode(fake: FakeT212) -> None:
    fake.fill_mode = "partial"
    fake.partial_ratio = 0.5
    async with make_client(fake) as client:
        body = (
            await client.post(
                f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": 4}
            )
        ).json()
        assert body["status"] == "PARTIALLY_FILLED"
        assert body["filledQuantity"] == 2

        fake.fill_remainder(body["id"])
        refreshed = (await client.get(f"{API}/equity/orders/{body['id']}")).json()
    assert refreshed["status"] == "FILLED"
    assert refreshed["filledQuantity"] == 4


async def test_429_burst_then_recovery(fake: FakeT212) -> None:
    fake.queue_429(f"{API}/equity/orders/market", count=2)
    async with make_client(fake) as client:
        r1 = await client.post(
            f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": 1}
        )
        r2 = await client.post(
            f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": 1}
        )
        r3 = await client.post(
            f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": 1}
        )
    assert (r1.status_code, r2.status_code, r3.status_code) == (429, 429, 200)
    assert r1.headers["x-ratelimit-remaining"] == "0"


async def test_timeout_then_success_without_order(fake: FakeT212) -> None:
    fake.queue_timeout(f"{API}/equity/orders/market", place_order=False)
    async with make_client(fake) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.post(
                f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": 1}
            )
        open_orders = (await client.get(f"{API}/equity/orders")).json()
        assert fake.orders == {}  # nothing was placed
        resp = await client.post(
            f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": 1}
        )
    assert resp.status_code == 200
    assert open_orders == []


async def test_timeout_with_duplicate_hazard(fake: FakeT212) -> None:
    """C11: caller sees a timeout but the order WAS created server-side."""
    fake.fill_mode = "none"
    fake.queue_timeout(f"{API}/equity/orders/market", place_order=True)
    async with make_client(fake) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.post(
                f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": 3}
            )
        open_orders = (await client.get(f"{API}/equity/orders")).json()
    assert len(open_orders) == 1
    assert open_orders[0]["quantity"] == 3


async def test_reposting_identical_order_duplicates(fake: FakeT212) -> None:
    """C11: the fake must NOT be idempotent — that's the hazard T11 handles."""
    payload = {"ticker": "SPY_US_EQ", "quantity": 1}
    async with make_client(fake) as client:
        r1 = await client.post(f"{API}/equity/orders/market", json=payload)
        r2 = await client.post(f"{API}/equity/orders/market", json=payload)
    assert r1.json()["id"] != r2.json()["id"]
    assert len(fake.orders) == 2


async def test_stop_rejection_mode(fake: FakeT212) -> None:
    fake.reject_stops = True
    async with make_client(fake) as client:
        resp = await client.post(
            f"{API}/equity/orders/stop",
            json={
                "ticker": "SPY_US_EQ",
                "quantity": -2,
                "stopPrice": 450.0,
                "timeValidity": "GOOD_TILL_CANCEL",
            },
        )
    assert resp.status_code == 400


async def test_stop_accepted_stays_pending(fake: FakeT212) -> None:
    async with make_client(fake) as client:
        resp = await client.post(
            f"{API}/equity/orders/stop",
            json={
                "ticker": "SPY_US_EQ",
                "quantity": -2,
                "stopPrice": 450.0,
                "timeValidity": "GOOD_TILL_CANCEL",
            },
        )
        body = resp.json()
        assert body["status"] == "NEW"
        assert body["stopPrice"] == 450.0
        cancel = await client.delete(f"{API}/equity/orders/{body['id']}")
        refreshed = (await client.get(f"{API}/equity/orders/{body['id']}")).json()
    assert cancel.status_code == 200
    assert refreshed["status"] == "CANCELLED"


async def test_history_pagination_follows_next_page_path(fake: FakeT212) -> None:
    fake.seed_history_orders(12)
    collected: list[dict[str, Any]] = []
    async with make_client(fake) as client:
        path = f"{API}/equity/history/orders?limit=5"
        while path:
            body = (await client.get(path)).json()
            collected.extend(body["items"])
            path = body["nextPagePath"]
    assert len(collected) == 12
    assert collected[0]["order"]["id"] == 1
    assert collected[-1]["order"]["id"] == 12


async def test_transactions_pagination(fake: FakeT212) -> None:
    fake.seed_transactions(7)
    async with make_client(fake) as client:
        first = (await client.get(f"{API}/equity/history/transactions?limit=5")).json()
        second = (await client.get(first["nextPagePath"])).json()
    assert len(first["items"]) == 5
    assert len(second["items"]) == 2
    assert second["nextPagePath"] is None


async def test_unknown_order_404s(fake: FakeT212) -> None:
    async with make_client(fake) as client:
        resp = await client.get(f"{API}/equity/orders/99999")
    assert resp.status_code == 404
