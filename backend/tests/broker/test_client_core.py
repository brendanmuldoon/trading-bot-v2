"""T07: client core — auth, env switch, typed models, error mapping."""

import pytest

from backend.broker.errors import (
    AmbiguousResultError,
    AuthError,
    BrokerTimeoutError,
    BrokerValidationError,
    NotFoundError,
    RateLimitedError,
    ServerError,
)
from backend.broker.t212 import API, T212Client
from backend.tests.broker.fake_t212 import FakeT212


@pytest.fixture()
def fake() -> FakeT212:
    f = FakeT212(api_key="k", api_secret="s")
    f.add_instrument("SPY_US_EQ", price=500.0)
    return f


def make_client(fake: FakeT212, api_key: str = "k", api_secret: str = "s") -> T212Client:
    return T212Client(api_key, api_secret, env="demo", transport=fake.transport)


def test_base_url_is_config_driven() -> None:
    demo = T212Client("k", "s", env="demo")
    live = T212Client("k", "s", env="live")
    assert demo.base_url == "https://demo.trading212.com"
    assert live.base_url == "https://live.trading212.com"


def test_repr_never_contains_credentials() -> None:
    client = T212Client("super-secret-key", "super-secret-secret", env="demo")
    assert "super-secret" not in repr(client)
    assert "super-secret" not in str(client)


async def test_account_summary_typed(fake: FakeT212) -> None:
    client = make_client(fake)
    summary = await client.get_account_summary()
    assert summary.currency == "USD"
    assert summary.cash.available_to_trade == 10_000.0
    assert summary.total_value >= 10_000.0
    await client.aclose()


async def test_account_cash_derived_from_summary(fake: FakeT212) -> None:
    client = make_client(fake)
    cash = await client.get_account_cash()
    assert cash.available_to_trade == 10_000.0
    await client.aclose()


async def test_bad_credentials_raise_auth_error(fake: FakeT212) -> None:
    client = make_client(fake, api_key="wrong")
    with pytest.raises(AuthError):
        await client.get_account_summary()
    await client.aclose()


async def test_429_maps_to_rate_limited_with_reset(fake: FakeT212) -> None:
    fake.queue_429(f"{API}/equity/account/summary", count=1)
    client = make_client(fake)
    with pytest.raises(RateLimitedError) as excinfo:
        await client.get_account_summary()
    assert excinfo.value.reset_at is not None
    await client.aclose()


async def test_400_maps_to_validation_error(fake: FakeT212) -> None:
    fake.queue_error(f"{API}/equity/account/summary", 400)
    client = make_client(fake)
    with pytest.raises(BrokerValidationError):
        await client.get_account_summary()
    await client.aclose()


async def test_404_maps_to_not_found(fake: FakeT212) -> None:
    fake.queue_error(f"{API}/equity/account/summary", 404)
    client = make_client(fake)
    with pytest.raises(NotFoundError):
        await client.get_account_summary()
    await client.aclose()


async def test_5xx_on_read_maps_to_server_error(fake: FakeT212) -> None:
    fake.queue_error(f"{API}/equity/account/summary", 503)
    client = make_client(fake)
    with pytest.raises(ServerError):
        await client.get_account_summary()
    await client.aclose()


async def test_timeout_on_read_is_retryable_timeout(fake: FakeT212) -> None:
    fake.queue_timeout(f"{API}/equity/account/summary")
    client = make_client(fake)
    with pytest.raises(BrokerTimeoutError):
        await client.get_account_summary()
    await client.aclose()


async def test_timeout_on_write_is_ambiguous(fake: FakeT212) -> None:
    fake.queue_timeout(f"{API}/equity/orders/market")
    client = make_client(fake)
    with pytest.raises(AmbiguousResultError):
        await client.request(
            "POST", f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": 1}
        )
    await client.aclose()


async def test_5xx_on_write_is_ambiguous(fake: FakeT212) -> None:
    fake.queue_error(f"{API}/equity/orders/market", 502)
    client = make_client(fake)
    with pytest.raises(AmbiguousResultError):
        await client.request(
            "POST", f"{API}/equity/orders/market", json={"ticker": "SPY_US_EQ", "quantity": 1}
        )
    await client.aclose()
