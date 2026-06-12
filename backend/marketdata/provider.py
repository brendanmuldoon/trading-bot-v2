"""MarketDataProvider interface (T13, §7).

Strategies and risk code depend only on this interface — provider
types never leak out. Canonical candle frame: UTC DatetimeIndex named
'ts', columns open/high/low/close/volume (floats; volume int64).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import pandas as pd

CANDLE_COLUMNS = ["open", "high", "low", "close", "volume"]


class MarketDataError(Exception):
    """Provider failure. No internal retries — the signal cycle decides
    (skip cycle + DATA_OUTAGE, per §7)."""


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    ts: datetime


class MarketDataProvider(Protocol):
    def get_candles(
        self, tickers: list[str], interval: str, lookback_bars: int
    ) -> dict[str, pd.DataFrame]:
        """One batched fetch for all tickers. Tickers with no data are
        simply absent from the result."""
        ...

    def get_quote(self, tickers: list[str]) -> dict[str, Quote]: ...
