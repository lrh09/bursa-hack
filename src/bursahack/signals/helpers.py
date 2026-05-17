"""Vectorised helpers shared across signal strategies.

All functions return DataFrames indexed by date, columns by SECURITY_ID,
matching the shape of `panel.adj_close`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bursahack.engine import PricePanel


def atr(panel: PricePanel, window: int = 20) -> pd.DataFrame:
    """Average True Range (Wilder's true range averaged over `window` days).

    True Range_t = max(
        high_t - low_t,
        |high_t - close_{t-1}|,
        |low_t  - close_{t-1}|
    )

    Bursa CSV does not give us adjusted HIGH/LOW, so we approximate
    TR with the daily |adj_close pct_change| times the adj_close. This is
    an acceptable proxy for cross-sectional position sizing (the relative
    ATR ordering across stocks is preserved).
    """
    ac = panel.adj_close
    daily_range_proxy = ac.pct_change().abs() * ac.shift(1)
    return daily_range_proxy.rolling(window, min_periods=window // 2).mean()


def exp_regression_slope_r2(
    adj_close: pd.DataFrame,
    lookback: int = 90,
    trading_days_per_year: int = 252,
    style: str = "exp",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Annualized exponential regression slope and R^2.

    Fits ln(price) = a + b * t over a rolling `lookback` window. Returns:
      annualized_slope =
        - style="exp":      exp(b * trading_days_per_year) - 1                   (Clenow-style)
        - style="compound": (1 + b) ** trading_days_per_year - 1                 (RH's original)
      r_squared          = b^2 * var(t) / var(ln_price)   (population variance)

    Vectorised across all securities via N shifted sums (no Python loop).
    """
    N = lookback
    log_p = np.log(adj_close.where(adj_close > 0))

    # x = 0, 1, ..., N-1 (window-relative time index)
    x = np.arange(N, dtype=float)
    x_centered = x - x.mean()
    # Sum_{i in window} (x_centered[i] * log_p[t - (N-1-i)])
    # = Sum over offsets (N-1-i) of x_centered[i] * log_p.shift(N-1-i)
    numerator = sum(
        x_centered[i] * log_p.shift(N - 1 - i) for i in range(N)
    )
    var_x = ((N * N - 1) / 12.0)  # population variance of 0..N-1
    sum_sq_x = N * var_x
    slope_b = numerator / sum_sq_x

    var_y = log_p.rolling(N, min_periods=N).var(ddof=0)
    r_squared = (slope_b ** 2) * var_x / var_y.replace(0, np.nan)

    if style == "exp":
        annualized_slope = np.exp(slope_b * trading_days_per_year) - 1.0
    elif style == "compound":
        annualized_slope = (1.0 + slope_b) ** trading_days_per_year - 1.0
    else:
        raise ValueError(f"unknown style {style!r} (use 'exp' or 'compound')")
    return annualized_slope, r_squared


def inv_vol_parity(
    vols: pd.Series, weight_cap: float = 0.10
) -> pd.Series:
    """Inverse-volatility weights normalised to sum to 1, with a per-name cap.

    Equivalent to RH's `risk_parity` function: any name with raw weight above
    `weight_cap` gets capped, the excess is redistributed pro-rata to the
    uncapped names, and a final flatten clips any still-above-cap names
    (which can happen after redistribution if many names hit the cap).
    """
    v = vols.astype(float).copy()
    v[~np.isfinite(v)] = np.nan
    v[v <= 0] = np.nan
    v = v.dropna()
    if v.empty:
        return pd.Series(dtype=float)
    inv = 1.0 / v
    w = inv / inv.sum()
    if weight_cap >= 1.0 or w.empty:
        return w
    above = w > weight_cap
    if not above.any():
        return w
    w.loc[above] = weight_cap
    remainder = 1.0 - w.loc[above].sum()
    below = w[~above]
    if below.sum() > 0:
        w.loc[~above] = below * (remainder / below.sum())
    # Final flatten (any still-above-cap after redistribution gets clipped)
    w = w.clip(upper=weight_cap)
    return w


def rolling_period_vol(adj_close: pd.DataFrame, period: int = 90) -> pd.DataFrame:
    """Per-period vol = std(daily pct_change) * sqrt(period).

    NOTE: this matches RH's original `add_annualized_Vol` which uses
    sqrt(period), not sqrt(252). It is *not* truly annualised when period != 252.
    Kept for fidelity to the source strategy.
    """
    pct = adj_close.pct_change()
    return pct.rolling(period, min_periods=period).std() * np.sqrt(period)


def market_proxy(panel: PricePanel, top_n_by_adv: int = 30, window: int = 252) -> pd.Series:
    """Equal-weight return index built from the `top_n_by_adv` most-liquid names
    (averaged across the whole panel). Used as a regime filter proxy when an
    explicit index series isn't available.

    Returns a Series indexed by date, with the index level normalised to 1.0 at
    the first non-NaN date.
    """
    adv = panel.volume_rm.mean(axis=0).sort_values(ascending=False)
    top = adv.head(top_n_by_adv).index
    rets = panel.adj_close[top].pct_change().mean(axis=1)
    level = (1.0 + rets.fillna(0)).cumprod()
    return level


def trend_filter(adj_close: pd.DataFrame, ma_window: int = 100) -> pd.DataFrame:
    """Boolean: each stock is above its `ma_window`-day moving average."""
    ma = adj_close.rolling(ma_window, min_periods=ma_window // 2).mean()
    return adj_close > ma


def regime_filter(market_level: pd.Series, ma_window: int = 200) -> pd.Series:
    """Boolean Series: market is above its `ma_window`-day MA."""
    ma = market_level.rolling(ma_window, min_periods=ma_window // 2).mean()
    return market_level > ma


def gap_filter(adj_close: pd.DataFrame, lookback: int = 90, max_gap: float = 0.15) -> pd.DataFrame:
    """Boolean: stock had NO single-day absolute return > max_gap in last
    `lookback` days.
    """
    daily_abs_ret = adj_close.pct_change().abs()
    max_in_window = daily_abs_ret.rolling(lookback, min_periods=lookback // 2).max()
    return max_in_window < max_gap
