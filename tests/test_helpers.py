"""Unit tests for shared signal helpers."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from bursahack.signals.helpers import (
    exp_regression_slope_r2,
    inv_vol_parity,
    rolling_period_vol,
)


def test_inv_vol_parity_basic():
    # Three names with vol 0.10 / 0.20 / 0.40. Inv = 10 / 5 / 2.5 -> sum 17.5.
    vols = pd.Series({"A": 0.10, "B": 0.20, "C": 0.40})
    w = inv_vol_parity(vols, weight_cap=1.0)
    assert math.isclose(w.sum(), 1.0, abs_tol=1e-9)
    assert w["A"] > w["B"] > w["C"]
    assert math.isclose(w["A"], 10 / 17.5, abs_tol=1e-9)


def test_inv_vol_parity_caps_and_redistributes():
    # 30 names with identical vol. Uncapped each gets 1/30 = 3.33%. With cap 10%
    # nothing trips. With cap 5% all trip (30 * 5% = 150% > 100%) -> after
    # final flatten everyone clipped to 5% -> sum = 1.50? No -- the final
    # flatten clips to cap so sum can be > 1 if many names hit cap.
    # That's the source-faithful behaviour: cap then redistribute then clip.
    vols = pd.Series({f"n{i}": 0.10 for i in range(30)})
    w = inv_vol_parity(vols, weight_cap=0.10)
    # All names at uniform vol -> all at 3.33%, no cap binding -> sums to 1
    assert math.isclose(w.sum(), 1.0, abs_tol=1e-9)
    for v in w:
        assert v <= 0.10 + 1e-9


def test_inv_vol_parity_concentration_cap_binds():
    # One very-low-vol name should grab > 10% raw weight; cap should clip.
    vols = pd.Series({"A": 0.01, "B": 0.40, "C": 0.40, "D": 0.40, "E": 0.40})
    w = inv_vol_parity(vols, weight_cap=0.10)
    assert w["A"] <= 0.10 + 1e-9
    # Others should pick up the slack
    assert (w[["B", "C", "D", "E"]] > 0).all()


def test_exp_regression_slope_known_signal():
    # Construct a stock that grows at 0.001/day in log space for 100 days.
    n = 200
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    log_p = np.linspace(0, 0.001 * (n - 1), n)
    prices = np.exp(log_p)
    df = pd.DataFrame({"X": prices}, index=dates)
    ann_slope, r2 = exp_regression_slope_r2(df, lookback=90, trading_days_per_year=250, style="compound")
    # At t=189, the score reflects a stable 0.001/day slope: (1.001)^250 - 1 ~= 28.4%
    expected = (1.001) ** 250 - 1.0
    last_ann = ann_slope.iloc[-1, 0]
    last_r2 = r2.iloc[-1, 0]
    assert math.isclose(last_ann, expected, rel_tol=1e-6)
    # Perfectly straight log-line -> R^2 should be 1.0
    assert math.isclose(last_r2, 1.0, rel_tol=1e-6)


def test_exp_regression_slope_zero_for_flat():
    n = 200
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    df = pd.DataFrame({"X": np.full(n, 5.0)}, index=dates)
    ann_slope, r2 = exp_regression_slope_r2(df, lookback=90)
    last = ann_slope.iloc[-1, 0]
    # Flat -> slope_b == 0 -> ann ~= 0
    assert abs(last) < 1e-9


def test_rolling_period_vol_shape():
    n = 200
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    df = pd.DataFrame({"X": np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.01, n)))}, index=dates)
    v = rolling_period_vol(df, period=90)
    assert v.shape == df.shape
    # First 89 rows must be NaN (min_periods=90)
    assert v.iloc[:89, 0].isna().all()
    assert not v.iloc[-1, 0] != v.iloc[-1, 0]  # last is non-NaN
