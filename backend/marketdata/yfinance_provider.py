"""yfinance implementation of MarketDataProvider (T13).

One batched yf.download per call (C8: T212 has no candles; §7: one
request per signal cycle). yfinance is unofficial and may be delayed —
acceptable for hourly paper trading, upgrade before live (README).
"""

import logging
import math
from datetime import UTC, datetime
from typing import Any, cast

import pandas as pd

from backend.marketdata.provider import CANDLE_COLUMNS, MarketDataError, Quote

logger = logging.getLogger("bot.marketdata")

_INTERVAL_BARS_PER_DAY = {"1h": 7, "1d": 1}  # regular-session hourly bars/day
_MAX_PERIOD_DAYS = {"1h": 720, "1d": 10_000}  # yfinance limits 1h to ~730d


class YFinanceProvider:
    def get_candles(
        self, tickers: list[str], interval: str, lookback_bars: int
    ) -> dict[str, pd.DataFrame]:
        period_days = self._period_days(interval, lookback_bars)
        raw = self._download(tickers, interval=interval, period=f"{period_days}d")
        out: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            frame = _extract(raw, ticker)
            if frame is not None and not frame.empty:
                out[ticker] = frame.tail(lookback_bars)
        if not out:
            raise MarketDataError(f"yfinance returned no data for {tickers}")
        return out

    def get_quote(self, tickers: list[str]) -> dict[str, Quote]:
        """Lightweight: last close of today's hourly bars, one batch."""
        raw = self._download(tickers, interval="1h", period="2d")
        quotes: dict[str, Quote] = {}
        for ticker in tickers:
            frame = _extract(raw, ticker)
            if frame is None or frame.empty:
                continue
            last = frame.dropna(subset=["close"]).tail(1)
            if last.empty:
                continue
            ts = last.index[-1].to_pydatetime()
            quotes[ticker] = Quote(symbol=ticker, price=float(last["close"].iloc[-1]), ts=ts)
        if not quotes:
            raise MarketDataError(f"yfinance returned no quotes for {tickers}")
        return quotes

    def _download(self, tickers: list[str], **kwargs: Any) -> pd.DataFrame:
        import yfinance as yf

        try:
            raw = yf.download(
                tickers=tickers,
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=False,
                **kwargs,
            )
        except Exception as exc:
            raise MarketDataError(f"yfinance download failed: {exc}") from exc
        if raw is None:
            raise MarketDataError("yfinance returned nothing")
        return cast(pd.DataFrame, raw)

    @staticmethod
    def _period_days(interval: str, lookback_bars: int) -> int:
        bars_per_day = _INTERVAL_BARS_PER_DAY.get(interval, 7)
        # Calendar fudge x1.6 covers weekends/holidays.
        days = math.ceil(lookback_bars / bars_per_day * 1.6) + 2
        return min(days, _MAX_PERIOD_DAYS.get(interval, 720))


def _extract(raw: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """Normalize one ticker's slice to the canonical frame. Handles both
    MultiIndex (ticker, field) and flat single-ticker layouts."""
    if raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return None
        frame = cast(pd.DataFrame, raw[ticker]).copy()
    else:
        frame = raw.copy()

    frame.columns = [str(c).lower() for c in frame.columns]
    missing = [c for c in CANDLE_COLUMNS if c not in frame.columns]
    if missing:
        logger.warning("%s: missing columns %s from provider", ticker, missing)
        return None

    frame = frame[CANDLE_COLUMNS].dropna(subset=["open", "high", "low", "close"])
    index = pd.to_datetime(frame.index)
    frame.index = index.tz_localize(UTC) if index.tz is None else index.tz_convert(UTC)
    frame.index.name = "ts"
    frame["volume"] = frame["volume"].fillna(0).astype("int64")
    return frame


def _utcnow() -> datetime:
    return datetime.now(UTC)
