"""T15: indicators vs independently hand-computed reference values.

Reference math is worked in the comments so it can be re-checked
without running anything.
"""

import math

import numpy as np
import pandas as pd
import pytest

from backend.strategies.indicators import atr, bollinger, rsi, sma


def s(*values: float) -> pd.Series:
    return pd.Series(list(values), dtype=float)


# ---- SMA ----------------------------------------------------------------------


def test_sma_known_values() -> None:
    result = sma(s(1, 2, 3, 4, 5), window=3)
    # (1+2+3)/3=2, (2+3+4)/3=3, (3+4+5)/3=4
    assert math.isnan(result.iloc[0]) and math.isnan(result.iloc[1])
    assert result.iloc[2:].tolist() == [2.0, 3.0, 4.0]


def test_sma_warmup_is_nan_not_partial() -> None:
    result = sma(s(10, 20), window=3)
    assert result.isna().all()


# ---- RSI ----------------------------------------------------------------------


def test_rsi_known_values_wilder_seeded() -> None:
    # series 1,2,3,2,3,4 → deltas +1,+1,-1,+1,+1 with period 3:
    # seed: avg_gain=(1+1+0)/3=2/3, avg_loss=(0+0+1)/3=1/3 → RS=2 → RSI=66.667 @i3
    # next (+1): g=(2/3*2+1)/3=7/9, l=(1/3*2+0)/3=2/9 → RS=3.5 → RSI=77.778 @i4
    # next (+1): g=(7/9*2+1)/3=23/27, l=(2/9*2)/3=4/27 → RS=5.75 → RSI=85.185 @i5
    result = rsi(s(1, 2, 3, 2, 3, 4), period=3)
    assert result.iloc[:3].isna().all()
    assert result.iloc[3] == pytest.approx(66.6667, abs=1e-3)
    assert result.iloc[4] == pytest.approx(77.7778, abs=1e-3)
    assert result.iloc[5] == pytest.approx(85.1852, abs=1e-3)


def test_rsi_all_gains_is_100() -> None:
    result = rsi(s(*range(1, 12)), period=3)
    assert result.iloc[-1] == 100.0


def test_rsi_all_losses_is_0() -> None:
    result = rsi(s(*range(12, 1, -1)), period=3)
    assert result.iloc[-1] == 0.0


def test_rsi_warmup_is_nan() -> None:
    result = rsi(s(1, 2, 3), period=14)
    assert result.isna().all()


# ---- Bollinger ---------------------------------------------------------------


def test_bollinger_known_values() -> None:
    # window 3 over 2,4,6: mid=4, population std=sqrt(((2-4)^2+(0)^2+(2)^2)/3)
    #   = sqrt(8/3) = 1.63299; 2σ band → 4 ± 3.26599
    lower, middle, upper = bollinger(s(2, 4, 6), window=3, num_std=2.0)
    assert middle.iloc[2] == pytest.approx(4.0)
    assert upper.iloc[2] == pytest.approx(4 + 2 * math.sqrt(8 / 3), abs=1e-6)
    assert lower.iloc[2] == pytest.approx(4 - 2 * math.sqrt(8 / 3), abs=1e-6)
    assert middle.iloc[:2].isna().all()


def test_bollinger_flat_series_zero_width() -> None:
    lower, middle, upper = bollinger(s(5, 5, 5, 5), window=3)
    assert lower.iloc[-1] == middle.iloc[-1] == upper.iloc[-1] == 5.0


# ---- ATR ----------------------------------------------------------------------


def test_atr_known_values() -> None:
    # bars (h,l,c): (12,8,10),(13,9,11),(15,10,14),(16,13,15)
    # TR_1 = max(13-9, |13-10|, |9-10|)  = 4
    # TR_2 = max(15-10, |15-11|, |10-11|) = 5
    # TR_3 = max(16-13, |16-14|, |13-14|) = 3
    # ATR(2): seed=(4+5)/2=4.5 @i2; then (4.5*1+3)/2=3.75 @i3
    high = s(12, 13, 15, 16)
    low = s(8, 9, 10, 13)
    close = s(10, 11, 14, 15)
    result = atr(high, low, close, period=2)
    assert result.iloc[:2].isna().all()
    assert result.iloc[2] == pytest.approx(4.5)
    assert result.iloc[3] == pytest.approx(3.75)


def test_atr_gap_uses_previous_close() -> None:
    # Gap up: bar2 (h=30,l=28) after close=20 → TR = |30-20| = 10, not h-l=2
    high = s(21, 30, 31)
    low = s(19, 28, 29)
    close = s(20, 29, 30)
    result = atr(high, low, close, period=1)
    assert result.iloc[1] == pytest.approx(10.0)


def test_atr_warmup_is_nan() -> None:
    result = atr(s(1, 2), s(1, 2), s(1, 2), period=14)
    assert result.isna().all()


# ---- purity -------------------------------------------------------------------


def test_indicators_do_not_mutate_input() -> None:
    series = s(1, 2, 3, 4, 5, 4, 3, 2, 3, 4)
    original = series.copy()
    sma(series, 3)
    rsi(series, 3)
    bollinger(series, 3)
    atr(series, series, series, 3)
    assert np.array_equal(series.to_numpy(), original.to_numpy())
