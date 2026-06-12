"""Indicators (T15): SMA, RSI, Bollinger Bands, ATR.

Implemented directly (open question #3 resolved: pandas-ta is not
maintained against pandas 3.x) as pure functions over Series/DataFrames
— no I/O, no config. RSI and ATR use Wilder's smoothing with the
classic seeding (simple mean of the first `period` values, then
recursive smoothing), matching textbook reference values. Warm-up
periods are NaN, never garbage.
"""

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average; NaN until `window` bars exist."""
    return series.rolling(window=window, min_periods=window).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI. First value appears at index `period` (needs that many
    deltas); NaN before."""
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) <= period:
        return pd.Series(out, index=series.index)

    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_value(avg_gain, avg_loss)
    return pd.Series(out, index=series.index)


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def bollinger(
    series: pd.Series, window: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(lower, middle, upper). Population std (ddof=0), the charting
    convention. NaN during warm-up."""
    middle = sma(series, window)
    std = series.rolling(window=window, min_periods=window).std(ddof=0)
    return middle - num_std * std, middle, middle + num_std * std


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder ATR over true range. First value at index `period` (the
    first TR needs a previous close); NaN before."""
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)  # noqa: E741
    c = close.to_numpy(dtype=float)
    out = np.full(len(c), np.nan)
    if len(c) <= period:
        return pd.Series(out, index=close.index)

    prev_close = c[:-1]
    tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - prev_close), np.abs(l[1:] - prev_close)])
    value = tr[:period].mean()
    out[period] = value
    for i in range(period, len(tr)):
        value = (value * (period - 1) + tr[i]) / period
        out[i + 1] = value
    return pd.Series(out, index=close.index)
