"""Score -> weights -> share counts.

Pure functions: no engine state lives here. Each helper transforms a
score vector into a target portfolio.

Conventions:
  - scores: pd.Series indexed by SECURITY_ID, NaN allowed (treated as ineligible).
  - weights: pd.Series indexed by SECURITY_ID, summing to ~1.0 across selected names.
  - target_shares: dict[SECURITY_ID, int], in whole lots of `lot_size`.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

LOT_SIZE = 100  # Bursa Malaysia standard board lot


def top_n_equal_weight(scores: pd.Series, n: int, ascending: bool = False) -> pd.Series:
    """Pick top-N by score, equal-weight."""
    s = scores.dropna()
    if len(s) == 0:
        return pd.Series(dtype=float)
    s = s.sort_values(ascending=ascending).head(n)
    w = pd.Series(1.0 / len(s), index=s.index)
    return w


def top_n_vol_scaled(scores: pd.Series, vols: pd.Series, n: int,
                     target_vol: float = 0.15, ascending: bool = False) -> pd.Series:
    """Inverse-vol weighting of top-N names, scaled to target portfolio vol.

    `vols` is the per-name realized annualized vol (e.g. 60-day stdev * sqrt(252))."""
    s = scores.dropna().sort_values(ascending=ascending).head(n)
    if len(s) == 0:
        return pd.Series(dtype=float)
    v = vols.reindex(s.index).clip(lower=1e-4)   # avoid div-by-zero
    inv = 1.0 / v
    w = inv / inv.sum()                          # equal-risk
    # naive vol scaling: scale to hit target vol (ignores correlation -- conservative)
    w = w * (target_vol / v.median())
    return w.clip(lower=0.0, upper=1.0)


def target_shares(weights: pd.Series, capital: float, prices: pd.Series,
                  lot_size: int = LOT_SIZE) -> dict[str, int]:
    """Translate target weights to whole-lot share counts at given prices.

    Rounds DOWN to the nearest lot to avoid over-spending. Names with NaN price
    or zero/negative weight are dropped.
    """
    out: dict[str, int] = {}
    for sec_id, w in weights.items():
        if w <= 0 or pd.isna(w):
            continue
        px = prices.get(sec_id, np.nan)
        if pd.isna(px) or px <= 0:
            continue
        raw_shares = (capital * w) / px
        lots = math.floor(raw_shares / lot_size)
        if lots > 0:
            out[sec_id] = lots * lot_size
    return out


def orders_from_delta(current: dict[str, int],
                      target: dict[str, int]) -> dict[str, int]:
    """Compute share-count deltas to move from current to target holdings.

    Positive = buy, negative = sell. Names dropped from target are fully sold.
    """
    sec_ids = set(current) | set(target)
    delta = {}
    for s in sec_ids:
        d = target.get(s, 0) - current.get(s, 0)
        if d != 0:
            delta[s] = d
    return delta


def turnover(orders: dict[str, int], prices: dict[str, float]) -> float:
    """Single-side turnover in RM (sum of |order_value|)."""
    return sum(abs(q) * prices.get(s, 0.0) for s, q in orders.items())
