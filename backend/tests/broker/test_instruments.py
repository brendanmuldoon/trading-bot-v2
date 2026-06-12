"""T09: universe → instrument mapping, currency validation, persistence."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from backend.broker.governor import Governor
from backend.broker.instruments import sync_instruments
from backend.broker.t212 import T212Client
from backend.db import get_sessionmaker
from backend.models import Instrument
from backend.tests.broker.fake_t212 import FakeT212


@pytest.fixture(autouse=True)
def capture_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str]]]:
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "backend.broker.instruments.log_event",
        lambda level, code, message, context=None: captured.append((level.value, code)),
    )
    yield captured


async def _instant_sleep(_seconds: float) -> None:
    return None


@pytest.fixture()
async def governor() -> "Iterator[Governor]":  # type: ignore[misc]
    fake = FakeT212(api_key="k", api_secret="s")
    fake.add_instrument("SPY_US_EQ", currency="USD", price=500.0)
    fake.add_instrument("QQQ_US_EQ", currency="USD", price=450.0)
    fake.add_instrument("VUKE_GB_EQ", currency="GBP", price=35.0)
    client = T212Client("k", "s", env="demo", transport=fake.transport)
    # Instant sleep: the fake advertises 1/50s budgets for instruments,
    # and the governor would otherwise (correctly) wait out the reset.
    governor = Governor(client, spacing=0.0, sleep=_instant_sleep)
    await governor.start()
    yield governor
    await governor.stop()
    await client.aclose()


async def test_happy_mapping_persists_tradable_rows(governor: Governor, db_engine: Engine) -> None:
    session_factory = get_sessionmaker(db_engine)
    tradable = await sync_instruments(governor, session_factory, ["SPY", "QQQ"], "USD")
    assert sorted(i.symbol for i in tradable) == ["QQQ", "SPY"]
    with session_factory() as session:
        spy = session.get(Instrument, "SPY")
        assert spy is not None
        assert spy.t212_ticker == "SPY_US_EQ"
        assert spy.tradable is True
        assert spy.currency == "USD"
        assert spy.min_qty is None  # bundle exposes no min-qty field


async def test_missing_symbol_dropped_with_warning(
    governor: Governor, db_engine: Engine, capture_events: list[tuple[str, str]]
) -> None:
    session_factory = get_sessionmaker(db_engine)
    tradable = await sync_instruments(governor, session_factory, ["SPY", "NOPE"], "USD")
    assert [i.symbol for i in tradable] == ["SPY"]
    assert ("WARNING", "INSTRUMENT_UNRESOLVED") in capture_events
    with session_factory() as session:
        nope = session.get(Instrument, "NOPE")
        assert nope is not None
        assert nope.tradable is False


async def test_wrong_currency_dropped_with_warning(
    governor: Governor, db_engine: Engine, capture_events: list[tuple[str, str]]
) -> None:
    session_factory = get_sessionmaker(db_engine)
    tradable = await sync_instruments(governor, session_factory, ["VUKE"], "USD")
    assert tradable == []
    assert ("WARNING", "INSTRUMENT_WRONG_CURRENCY") in capture_events
    with session_factory() as session:
        vuke = session.get(Instrument, "VUKE")
        assert vuke is not None
        assert vuke.tradable is False
        assert vuke.currency == "GBP"


async def test_rerun_updates_rather_than_duplicates(governor: Governor, db_engine: Engine) -> None:
    session_factory = get_sessionmaker(db_engine)
    await sync_instruments(governor, session_factory, ["SPY"], "USD")
    await sync_instruments(governor, session_factory, ["SPY"], "USD")
    with session_factory() as session:
        rows = session.query(Instrument).filter_by(symbol="SPY").all()
    assert len(rows) == 1
