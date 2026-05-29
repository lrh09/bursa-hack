"""Market-neutral crypto sleeves + ensemble — for regime-robust returns.

Plain time-series trend on crypto is regime-dependent (great in bull years,
flat since 2021). These sleeves are dollar-neutral cross-sectional bets that
do NOT depend on overall crypto direction, so they're the honest shot at
"consistent return across regimes":

  - xs_momentum: long the strongest coins, short the weakest (relative
    strength). Earns when winners keep winning vs losers, regardless of
    whether the whole market is up or down.
  - xs_reversal: long recent losers, short recent winners (short-horizon
    mean reversion). Earns in choppy/ranging regimes.
  - ensemble: equal-risk blend of sleeves, re-vol-targeted.

No-lookahead: every weight is built from data up to close t, then LAGGED one
day (earns t+1's return). Vol-targeting uses trailing (lagged) vol.

All functions return a daily portfolio-return pd.Series.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _vol_target(raw: pd.Series, pvol: float, vol_window: int, ann: int,
                max_lev: float = 5.0) -> pd.Series:
    """Scale a raw return stream to a target annual vol (trailing, lagged)."""
    pv = raw.rolling(vol_window).std().shift(1)
    target_daily = pvol / np.sqrt(ann)
    lev = (target_daily / pv).clip(upper=max_lev)
    lev = lev.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return (lev * raw).fillna(0.0)


def _dollar_neutral_weights(score: pd.DataFrame, top_frac: float) -> pd.DataFrame:
    """Long top `top_frac`, short bottom `top_frac`, dollar-neutral, equal-wt.

    `score` higher = more long. Ranks cross-sectionally per row (day), only
    among coins that have a score that day (NaNs excluded from the rank).
    """
    rank = score.rank(axis=1, pct=True)
    long = (rank >= 1.0 - top_frac).astype(float)
    short = (rank <= top_frac).astype(float)
    nlong = long.sum(axis=1).replace(0, np.nan)
    nshort = short.sum(axis=1).replace(0, np.nan)
    w = long.div(nlong, axis=0).fillna(0.0) - short.div(nshort, axis=0).fillna(0.0)
    return w


def _ls_returns(prices: pd.DataFrame, score: pd.DataFrame, *, top_frac: float,
                cost_bps: float, pvol: float, vol_window: int, ann: int) -> pd.Series:
    """Generic dollar-neutral long-short backtest from a score matrix."""
    ret = prices.pct_change(fill_method=None)
    w = _dollar_neutral_weights(score, top_frac)
    w_lag = w.shift(1).fillna(0.0)
    gross = (w_lag * ret).sum(axis=1, skipna=True)
    turnover = (w_lag - w_lag.shift(1)).abs().sum(axis=1)
    cost = turnover * (cost_bps / 10_000.0)
    raw = (gross - cost).fillna(0.0)
    return _vol_target(raw, pvol, vol_window, ann)


def xs_momentum_returns(prices: pd.DataFrame, *, lookback: int = 30,
                        top_frac: float = 0.33, cost_bps: float = 10.0,
                        pvol: float = 0.20, vol_window: int = 63,
                        ann: int = 365) -> pd.Series:
    """Cross-sectional momentum: long strongest, short weakest coins."""
    score = prices.pct_change(lookback)              # trailing momentum, observable at t
    out = _ls_returns(prices, score, top_frac=top_frac, cost_bps=cost_bps,
                      pvol=pvol, vol_window=vol_window, ann=ann)
    warmup = lookback + vol_window
    return out.iloc[warmup:]


def xs_reversal_returns(prices: pd.DataFrame, *, lookback: int = 5,
                        top_frac: float = 0.33, cost_bps: float = 10.0,
                        pvol: float = 0.20, vol_window: int = 63,
                        ann: int = 365) -> pd.Series:
    """Short-horizon cross-sectional reversal: long recent losers, short winners."""
    score = -prices.pct_change(lookback)             # NEGATIVE momentum = reversal
    out = _ls_returns(prices, score, top_frac=top_frac, cost_bps=cost_bps,
                      pvol=pvol, vol_window=vol_window, ann=ann)
    warmup = lookback + vol_window
    return out.iloc[warmup:]


def ensemble_returns(sleeves: list[pd.Series], *, pvol: float = 0.20,
                     vol_window: int = 63, ann: int = 365) -> pd.Series:
    """Equal-risk blend of sleeve return streams, then re-vol-target the book.

    Each sleeve is already vol-targeted, so equal-weight averaging is roughly
    equal-risk; the final vol-target sets the overall dial and harvests the
    cross-sleeve diversification.
    """
    df = pd.concat(sleeves, axis=1).dropna(how="all")
    raw = df.mean(axis=1, skipna=True).fillna(0.0)
    return _vol_target(raw, pvol, vol_window, ann)


__all__ = ["xs_momentum_returns", "xs_reversal_returns", "ensemble_returns"]
