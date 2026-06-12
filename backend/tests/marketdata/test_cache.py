"""T14: delta fetch, staleness boundary, outage path, upsert idempotency."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db import get_sessionmaker
from backend.marketdata.cache import CandleCache
from backend.marketdata.provider import MarketDataError, Quote
from backend.models import Candle

NOW = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, int]] = []
        self.frames: dict[str, pd.DataFrame] = {}
        self.fail = False

    def get_candles(
        self, tickers: list[str], interval: str, lookback_bars: int
    ) -> dict[str, pd.DataFrame]:
        self.calls.append((tickers, interval, lookback_bars))
        if self.fail:
            raise MarketDataError("provider down")
        return {t: f.tail(lookback_bars) for t, f in self.frames.items() if t in tickers}

    def get_quote(self, tickers: list[str]) -> "dict[str, Quote]":  # pragma: no cover
        raise NotImplementedError


def hourly_frame(end: datetime, bars: int) -> pd.DataFrame:
    index = pd.date_range(end=end, periods=bars, freq="1h", tz=UTC, name="ts")
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(bars)],
            "high": [101.0 + i for i in range(bars)],
            "low": [99.0 + i for i in range(bars)],
            "close": [100.5 + i for i in range(bars)],
            "volume": [1000] * bars,
        },
        index=index,
    )


@pytest.fixture()
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return get_sessionmaker(db_engine)


@pytest.fixture(autouse=True)
def clean_candles(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.query(Candle).delete()
        session.commit()


@pytest.fixture()
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture()
def cache(session_factory: sessionmaker[Session], provider: FakeProvider) -> CandleCache:
    return CandleCache(session_factory, provider, seed_lookback_bars=100)


@pytest.fixture(autouse=True)
def capture_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str]]]:
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "backend.marketdata.cache.log_event",
        lambda level, code, message, context=None: captured.append((level.value, code)),
    )
    yield captured


def count_candles(session_factory: sessionmaker[Session], symbol: str) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count(Candle.id)).where(Candle.symbol == symbol)) or 0


def test_first_refresh_seeds_full_lookback(
    cache: CandleCache, provider: FakeProvider, session_factory: sessionmaker[Session]
) -> None:
    provider.frames["SPY"] = hourly_frame(NOW, 50)
    written = cache.refresh(["SPY"], now=NOW)
    assert provider.calls[0][2] == 100  # seed lookback on empty cache
    assert written["SPY"] == 50
    assert count_candles(session_factory, "SPY") == 50


def test_repeat_cycle_requests_only_delta(
    cache: CandleCache, provider: FakeProvider, session_factory: sessionmaker[Session]
) -> None:
    provider.frames["SPY"] = hourly_frame(NOW, 50)
    cache.refresh(["SPY"], now=NOW)

    later = NOW + timedelta(hours=2)
    provider.frames["SPY"] = hourly_frame(later, 50)
    written = cache.refresh(["SPY"], now=later)

    delta_lookback = provider.calls[1][2]
    assert delta_lookback <= 4, f"delta fetch should be small, asked for {delta_lookback}"
    assert written["SPY"] == 2  # only the two new bars inserted
    assert count_candles(session_factory, "SPY") == 52


def test_overlapping_upsert_never_violates_unique_constraint(
    cache: CandleCache, provider: FakeProvider, session_factory: sessionmaker[Session]
) -> None:
    provider.frames["SPY"] = hourly_frame(NOW, 20)
    cache.refresh(["SPY"], now=NOW)
    cache.refresh(["SPY"], now=NOW)  # identical window again
    assert count_candles(session_factory, "SPY") == 20


def test_get_frames_serves_from_cache_without_provider(
    cache: CandleCache, provider: FakeProvider
) -> None:
    provider.frames["SPY"] = hourly_frame(NOW, 30)
    cache.refresh(["SPY"], now=NOW)
    calls_before = len(provider.calls)

    frames = cache.get_frames(["SPY"], lookback_bars=10, now=NOW)
    assert len(provider.calls) == calls_before  # no provider traffic
    assert len(frames["SPY"]) == 10
    assert list(frames["SPY"].columns) == ["open", "high", "low", "close", "volume"]
    assert frames["SPY"].index.is_monotonic_increasing


def test_staleness_boundary_exactly_two_intervals_is_fresh(
    cache: CandleCache, provider: FakeProvider, capture_events: list[tuple[str, str]]
) -> None:
    provider.frames["SPY"] = hourly_frame(NOW, 10)
    cache.refresh(["SPY"], now=NOW)

    at_boundary = NOW + timedelta(hours=2)  # age == 2 intervals exactly
    frames = cache.get_frames(["SPY"], 10, now=at_boundary)
    assert "SPY" in frames
    assert ("WARNING", "DATA_STALE") not in capture_events


def test_older_than_two_intervals_is_stale_and_skipped(
    cache: CandleCache, provider: FakeProvider, capture_events: list[tuple[str, str]]
) -> None:
    provider.frames["SPY"] = hourly_frame(NOW, 10)
    cache.refresh(["SPY"], now=NOW)

    past_boundary = NOW + timedelta(hours=2, seconds=1)
    frames = cache.get_frames(["SPY"], 10, now=past_boundary)
    assert frames == {}
    assert ("WARNING", "DATA_STALE") in capture_events


def test_unknown_symbol_treated_as_stale(
    cache: CandleCache, capture_events: list[tuple[str, str]]
) -> None:
    frames = cache.get_frames(["NOPE"], 10, now=NOW)
    assert frames == {}
    assert ("WARNING", "DATA_STALE") in capture_events


def test_provider_outage_logs_event_and_raises(
    cache: CandleCache,
    provider: FakeProvider,
    session_factory: sessionmaker[Session],
    capture_events: list[tuple[str, str]],
) -> None:
    provider.frames["SPY"] = hourly_frame(NOW, 10)
    cache.refresh(["SPY"], now=NOW)

    provider.fail = True
    with pytest.raises(MarketDataError):
        cache.refresh(["SPY"], now=NOW + timedelta(hours=1))
    assert ("ERROR", "DATA_OUTAGE") in capture_events
    assert count_candles(session_factory, "SPY") == 10  # cache intact
