"""T10: order CRUD + positions via BrokerAPI against the fake server."""

from collections.abc import Iterator

import pytest

from backend.broker.api import PENDING_ORDERS_CAP, BrokerAPI, PendingCapExceededError
from backend.broker.governor import Governor, Priority
from backend.broker.t212 import T212Client
from backend.models import OrderSide
from backend.tests.broker.fake_t212 import FakeT212


async def _instant_sleep(_seconds: float) -> None:
    return None


@pytest.fixture()
def fake() -> FakeT212:
    f = FakeT212(api_key="k", api_secret="s")
    f.add_instrument("SPY_US_EQ", price=500.0)
    return f


@pytest.fixture()
async def broker(fake: FakeT212) -> "Iterator[BrokerAPI]":  # type: ignore[misc]
    client = T212Client("k", "s", env="demo", transport=fake.transport)
    governor = Governor(client, spacing=0.0, sleep=_instant_sleep)
    await governor.start()
    yield BrokerAPI(governor)
    await governor.stop()
    await client.aclose()


async def test_buy_sends_positive_quantity(fake: FakeT212, broker: BrokerAPI) -> None:
    order = await broker.place_market_order(
        "SPY_US_EQ", OrderSide.BUY, 2.0, priority=Priority.ENTRY
    )
    assert order.side == "BUY"
    posted = [b for m, p, b in fake.request_log if m == "POST"]
    assert posted[-1] is not None and posted[-1]["quantity"] == 2.0


async def test_sell_sends_negative_quantity_on_the_wire(fake: FakeT212, broker: BrokerAPI) -> None:
    await broker.place_market_order("SPY_US_EQ", OrderSide.BUY, 2.0, priority=Priority.ENTRY)
    order = await broker.place_market_order(
        "SPY_US_EQ", OrderSide.SELL, 2.0, priority=Priority.PROTECTIVE
    )
    assert order.side == "SELL"
    assert order.abs_quantity == 2.0  # public view is positive
    posted = [b for m, p, b in fake.request_log if m == "POST"]
    assert posted[-1] is not None and posted[-1]["quantity"] == -2.0  # wire is signed


async def test_non_positive_quantity_rejected_client_side(broker: BrokerAPI) -> None:
    with pytest.raises(ValueError):
        await broker.place_market_order("SPY_US_EQ", OrderSide.BUY, 0, priority=Priority.ENTRY)
    with pytest.raises(ValueError):
        await broker.place_market_order("SPY_US_EQ", OrderSide.SELL, -1, priority=Priority.ENTRY)


async def test_stop_order_and_cancel(fake: FakeT212, broker: BrokerAPI) -> None:
    stop = await broker.place_stop_order("SPY_US_EQ", OrderSide.SELL, 2.0, 450.0)
    assert stop.status == "NEW"
    assert stop.stop_price == 450.0
    posted = [b for m, p, b in fake.request_log if m == "POST"]
    assert posted[-1] is not None and posted[-1]["quantity"] == -2.0

    await broker.cancel_order(stop.id)
    refreshed = await broker.get_order(stop.id)
    assert refreshed.status == "CANCELLED"


async def test_open_orders_lists_pending_only(fake: FakeT212, broker: BrokerAPI) -> None:
    fake.fill_mode = "none"

    await broker.place_market_order("SPY_US_EQ", OrderSide.BUY, 1.0, priority=Priority.ENTRY)
    open_orders = await broker.get_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].status == "NEW"


async def test_positions_parse_with_pnl(fake: FakeT212, broker: BrokerAPI) -> None:
    await broker.place_market_order("SPY_US_EQ", OrderSide.BUY, 2.0, priority=Priority.ENTRY)
    fake.set_price("SPY_US_EQ", 510.0)
    positions = await broker.get_positions()
    assert len(positions) == 1
    pos = positions[0]
    assert pos.ticker == "SPY_US_EQ"
    assert pos.quantity == 2.0
    assert pos.average_price == 500.0
    assert pos.unrealized_pnl == pytest.approx(20.0)


async def test_pending_cap_enforced(fake: FakeT212, broker: BrokerAPI) -> None:
    fake.fill_mode = "none"
    for _ in range(PENDING_ORDERS_CAP):
        await broker.place_market_order("SPY_US_EQ", OrderSide.BUY, 1.0, priority=Priority.ENTRY)
    with pytest.raises(PendingCapExceededError):
        await broker.place_market_order("SPY_US_EQ", OrderSide.BUY, 1.0, priority=Priority.ENTRY)
