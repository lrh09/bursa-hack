"""Tests for crypto market-neutral sleeves."""
from __future__ import annotations

import numpy as np
import pandas as pd

from bursahack.futures.crypto_signals import (
    ensemble_returns, xs_momentum_returns, xs_reversal_returns,
)


def _panel(n: int, k: int = 6) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2018-01-01", periods=n)
    cols = {}
    for j in range(k):
        drift = 0.0005 * (j - k / 2)  # spread of trends across coins
        cols[f"C{j}"] = 100 * np.cumprod(1 + drift + rng.normal(0, 0.02, n))
    return pd.DataFrame(cols, index=idx)


def test_xs_momentum_no_lookahead():
    n = 400
    prices = _panel(n)
    base = xs_momentum_returns(prices, ann=365)
    shocked = prices.copy()
    shocked.iloc[-1, 0] *= 1.5  # final-day spike in C0
    after = xs_momentum_returns(shocked, ann=365)
    # All returns except the last must be unchanged (no future leak).
    np.testing.assert_allclose(base.iloc[:-1].values, after.iloc[:-1].values,
                               atol=1e-12, err_msg="lookahead leak in xs_momentum")


def test_xs_reversal_no_lookahead():
    n = 400
    prices = _panel(n)
    base = xs_reversal_returns(prices, ann=365)
    shocked = prices.copy()
    shocked.iloc[-1, 0] *= 1.5
    after = xs_reversal_returns(shocked, ann=365)
    np.testing.assert_allclose(base.iloc[:-1].values, after.iloc[:-1].values,
                               atol=1e-12, err_msg="lookahead leak in xs_reversal")


def test_returns_are_finite_and_nonempty():
    prices = _panel(500)
    for s in (xs_momentum_returns(prices), xs_reversal_returns(prices)):
        assert len(s) > 0
        assert np.isfinite(s.values).all()


def test_ensemble_blends():
    prices = _panel(500)
    a = xs_momentum_returns(prices)
    b = xs_reversal_returns(prices)
    ens = ensemble_returns([a, b])
    assert len(ens) > 0
    assert np.isfinite(ens.values).all()
