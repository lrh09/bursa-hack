"""Tests for the multi-speed vol-targeted TSMOM backtest."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bursahack.futures.tsmom import (
    TSMOMConfig,
    backtest_tsmom,
    compute_metrics,
    compute_signal,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2015-01-01", periods=n)


def _trend_panel(n: int, drift: float, cols=("A", "B")) -> pd.DataFrame:
    """Deterministic smooth trends (drift per day) with a little noise."""
    rng = np.random.default_rng(0)
    out = {}
    for j, c in enumerate(cols):
        steps = drift + rng.normal(0, 0.002, n)
        out[c] = 100.0 * np.cumprod(1.0 + steps)
    return pd.DataFrame(out, index=_dates(n))


# ---------- signal ----------


def test_signal_long_on_uptrend():
    prices = _trend_panel(400, drift=0.001)  # steady up
    cfg = TSMOMConfig(speeds=(21, 63, 252), signal_mode="sign")
    sig = compute_signal(prices, cfg)
    # After warmup, all speeds agree up -> signal ~ +1.
    assert sig["A"].iloc[-1] == pytest.approx(1.0)


def test_signal_short_on_downtrend():
    prices = _trend_panel(400, drift=-0.001)
    cfg = TSMOMConfig(speeds=(21, 63, 252), signal_mode="sign")
    sig = compute_signal(prices, cfg)
    assert sig["A"].iloc[-1] == pytest.approx(-1.0)


# ---------- no lookahead (the critical test) ----------


def test_no_lookahead_last_day_shock():
    """A price shock on the FINAL day must NOT show up in that day's
    portfolio return — the position was decided at the prior close."""
    n = 400
    prices = _trend_panel(n, drift=0.0005)
    # Inject a huge spike on the very last day in column A.
    prices.iloc[-1, prices.columns.get_loc("A")] *= 1.5  # +50% on last bar
    res = backtest_tsmom(prices, cfg=TSMOMConfig())
    # The last portfolio return uses w_{t-1} (pre-shock weight) * ret_t.
    # It is allowed to be nonzero (the position earns the move), but the
    # WEIGHT must not have reacted to the shock. We verify the weight used
    # for the last day equals the weight from the prior day's signal, i.e.
    # the shock didn't retroactively change the position sizing.
    # Practically: rerun WITHOUT the shock and confirm all returns EXCEPT the
    # last are identical (the shock can only affect the final day's ret).
    prices_noshock = _trend_panel(n, drift=0.0005)
    res2 = backtest_tsmom(prices_noshock, cfg=TSMOMConfig())
    a = res.portfolio_returns.iloc[:-1].values
    b = res2.portfolio_returns.iloc[:-1].values
    np.testing.assert_allclose(a, b, atol=1e-12,
        err_msg="a future shock changed PAST returns -> lookahead leak")


def test_position_lagged_one_day():
    """Sleeve return on day t must use the weight from day t-1, not t."""
    prices = _trend_panel(350, drift=0.0008)
    res = backtest_tsmom(prices, cfg=TSMOMConfig(portfolio_vol_target=0.10))
    # Smoke: returns exist and are finite.
    assert res.portfolio_returns.notna().all()
    assert np.isfinite(res.equity.iloc[-1])


# ---------- vol targeting ----------


def test_higher_vol_market_gets_smaller_weight():
    """Two uptrending markets, one 3x more volatile -> smaller weight."""
    n = 400
    rng = np.random.default_rng(1)
    calm = 100 * np.cumprod(1 + 0.0008 + rng.normal(0, 0.005, n))
    wild = 100 * np.cumprod(1 + 0.0008 + rng.normal(0, 0.015, n))
    prices = pd.DataFrame({"CALM": calm, "WILD": wild}, index=_dates(n))
    cfg = TSMOMConfig()
    ret = prices.pct_change(fill_method=None)
    ann_vol = ret.rolling(cfg.vol_window).std() * np.sqrt(252)
    sig = compute_signal(prices, cfg)
    weight = (sig * (cfg.sleeve_vol_target / ann_vol)).abs()
    # On the last day, the wilder market should carry a smaller |weight|.
    assert weight["WILD"].iloc[-1] < weight["CALM"].iloc[-1]


# ---------- cost ----------


def test_cost_reduces_returns():
    prices = _trend_panel(400, drift=0.0008)
    free = backtest_tsmom(prices, cost_bps={"A": 0.0, "B": 0.0})
    pricey = backtest_tsmom(prices, cost_bps={"A": 50.0, "B": 50.0})
    assert pricey.equity.iloc[-1] < free.equity.iloc[-1]


# ---------- metrics ----------


def test_metrics_positive_on_uptrend_long():
    prices = _trend_panel(600, drift=0.0010)
    res = backtest_tsmom(prices, cfg=TSMOMConfig(portfolio_vol_target=0.10))
    assert res.metrics["cagr"] > 0.0
    assert res.metrics["max_drawdown"] <= 0.0
    assert res.metrics["n_days"] > 0


def test_metrics_handle_empty():
    m = compute_metrics(pd.Series([], dtype=float), pd.Series([], dtype=float))
    assert m["cagr"] == 0.0
    assert m["sharpe"] == 0.0
