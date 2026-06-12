"""T13: yfinance provider — batching, normalization, error mapping."""

from datetime import UTC

import pandas as pd
import pytest

from backend.marketdata.provider import CANDLE_COLUMNS, MarketDataError
from backend.marketdata.yfinance_provider import YFinanceProvider


def make_yf_frame(tickers: list[str], bars: int = 5) -> pd.DataFrame:
    """Mimic yf.download(group_by='ticker') MultiIndex output."""
    index = pd.date_range("2026-06-10 13:30", periods=bars, freq="1h", tz="America/New_York")
    pieces: dict[tuple[str, str], list[float]] = {}
    for ticker in tickers:
        for field in ["Open", "High", "Low", "Close", "Volume"]:
            base = 100.0 if field != "Volume" else 1000
            pieces[(ticker, field)] = [base + i for i in range(bars)]
    frame = pd.DataFrame(pieces, index=index)
    frame.columns = pd.MultiIndex.from_tuples(list(pieces.keys()))
    return frame


@pytest.fixture()
def downloads(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture yf.download calls; tests assign .return_value."""
    calls: list[dict[str, object]] = []
    holder: dict[str, object] = {"value": pd.DataFrame()}

    def fake_download(**kwargs: object) -> object:
        calls.append(kwargs)
        value = holder["value"]
        if isinstance(value, Exception):
            raise value
        return value

    import yfinance

    monkeypatch.setattr(yfinance, "download", lambda **kw: fake_download(**kw))
    calls.append({"_holder": holder})  # smuggle the holder to tests
    return calls


def set_response(downloads: list[dict[str, object]], value: object) -> None:
    holder = downloads[0]["_holder"]
    holder["value"] = value  # type: ignore[index]


def real_calls(downloads: list[dict[str, object]]) -> list[dict[str, object]]:
    return downloads[1:]


def test_batched_single_request_for_universe(downloads: list[dict[str, object]]) -> None:
    set_response(downloads, make_yf_frame(["SPY", "QQQ", "GLD"]))
    provider = YFinanceProvider()
    frames = provider.get_candles(["SPY", "QQQ", "GLD"], "1h", lookback_bars=5)
    assert len(real_calls(downloads)) == 1, "must be ONE batched request"
    assert real_calls(downloads)[0]["tickers"] == ["SPY", "QQQ", "GLD"]
    assert sorted(frames) == ["GLD", "QQQ", "SPY"]


def test_canonical_frame_shape(downloads: list[dict[str, object]]) -> None:
    set_response(downloads, make_yf_frame(["SPY"]))
    frames = YFinanceProvider().get_candles(["SPY"], "1h", lookback_bars=5)
    frame = frames["SPY"]
    assert list(frame.columns) == CANDLE_COLUMNS
    assert frame.index.name == "ts"
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert str(frame.index.tz) == "UTC"
    assert frame["volume"].dtype == "int64"


def test_lookback_trims_history(downloads: list[dict[str, object]]) -> None:
    set_response(downloads, make_yf_frame(["SPY"], bars=10))
    frames = YFinanceProvider().get_candles(["SPY"], "1h", lookback_bars=3)
    assert len(frames["SPY"]) == 3


def test_missing_ticker_omitted(downloads: list[dict[str, object]]) -> None:
    set_response(downloads, make_yf_frame(["SPY"]))
    frames = YFinanceProvider().get_candles(["SPY", "NOPE"], "1h", lookback_bars=5)
    assert "SPY" in frames
    assert "NOPE" not in frames


def test_download_exception_maps_to_market_data_error(
    downloads: list[dict[str, object]],
) -> None:
    set_response(downloads, RuntimeError("rate limited by yahoo"))
    with pytest.raises(MarketDataError):
        YFinanceProvider().get_candles(["SPY"], "1h", lookback_bars=5)


def test_empty_response_is_an_error(downloads: list[dict[str, object]]) -> None:
    set_response(downloads, pd.DataFrame())
    with pytest.raises(MarketDataError):
        YFinanceProvider().get_candles(["SPY"], "1h", lookback_bars=5)


def test_quotes_from_last_close(downloads: list[dict[str, object]]) -> None:
    set_response(downloads, make_yf_frame(["SPY", "QQQ"]))
    quotes = YFinanceProvider().get_quote(["SPY", "QQQ"])
    assert quotes["SPY"].price == 104.0  # last close (100 + 4)
    assert quotes["SPY"].ts.tzinfo is not None
    assert quotes["SPY"].ts.astimezone(UTC) == quotes["SPY"].ts
