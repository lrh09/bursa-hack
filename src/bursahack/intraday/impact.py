"""Market impact + volatility estimators for intraday backtests.

Scalability note: this module is pure-function over (q, sigma, adv_bar)
scalars or vectorized Polars expressions. NO database access, NO state.
That keeps it cheap to run inside every backtest worker and trivially
testable.

References:
- Kissell, R. and Glantz, M. (2003). "Optimal Trading Strategies."
  Cost impact lambda = c * sigma * sqrt(q / ADV_bar).
- Rogers, L. C. G. and Satchell, S. E. (1991). "Estimating variance from
  high, low and closing prices."  sigma^2 = E[ln(H/C)ln(H/O) + ln(L/C)ln(L/O)].
  Drift-independent, lower-noise than close-to-close at minute scale.
"""
from __future__ import annotations

import math
from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field


def rogers_satchell_sigma(
    bars: pl.DataFrame, window_bars: int, *, by_code: bool = True
) -> pl.DataFrame:
    """Rolling Rogers-Satchell volatility.

    Returns a DataFrame with columns [ts, code, sigma_rs] (or [ts, sigma_rs]
    when `by_code=False`). The sigma column is the rolling-mean of the per-bar
    RS variance, sqrt-ed, NOT annualised. Units: per-bar log-return std.

    Per-bar RS term:
        u = ln(H/O); d = ln(L/O); c = ln(C/O)
        rs = u*(u - c) + d*(d - c)
    """
    required = {"ts", "open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"rogers_satchell_sigma missing columns: {missing}")

    out = bars.with_columns([
        (pl.col("high").log() - pl.col("open").log()).alias("_u"),
        (pl.col("low").log() - pl.col("open").log()).alias("_d"),
        (pl.col("close").log() - pl.col("open").log()).alias("_c"),
    ]).with_columns([
        (pl.col("_u") * (pl.col("_u") - pl.col("_c"))
         + pl.col("_d") * (pl.col("_d") - pl.col("_c"))).alias("_rs"),
    ])

    if by_code:
        out = out.sort(["code", "ts"]).with_columns([
            pl.col("_rs").rolling_mean(window_size=window_bars).over("code").alias("_rs_mean"),
        ])
    else:
        out = out.sort("ts").with_columns([
            pl.col("_rs").rolling_mean(window_size=window_bars).alias("_rs_mean"),
        ])

    out = out.with_columns([
        pl.when(pl.col("_rs_mean").is_null())
          .then(None)
          .when(pl.col("_rs_mean") > 0)
          .then(pl.col("_rs_mean").sqrt())
          .otherwise(0.0)
          .alias("sigma_rs"),
    ])

    keep = ["ts", "sigma_rs"]
    if by_code:
        keep = ["ts", "code", "sigma_rs"]
    return out.select(keep)


def kissell_glantz_impact(
    q: float, sigma: float, adv_bar: float, c: float = 1.0
) -> float:
    """Kissell-Glantz lambda: cost = c * sigma * sqrt(q / ADV_bar).

    Args:
        q: shares to trade
        sigma: per-bar vol estimate (e.g. from rogers_satchell_sigma); unitless
        adv_bar: average shares per bar (NOT per day)
        c: scaling constant (1.0 liquid, 1.5 illiquid)

    Returns: impact as a fractional price move (multiply by price to get RM).
    """
    if q <= 0 or adv_bar <= 0 or sigma < 0:
        return 0.0
    return c * sigma * math.sqrt(q / adv_bar)


# ----- ExecutionQuote + ImpactModel -----


class ExecutionQuote(BaseModel):
    """Inputs to a per-trade cost estimate.

    All fields are at the moment of intended fill. price/q are positive.
    `side` is "buy" or "sell" (currently cost is symmetric).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    side: Literal["buy", "sell"]
    q: float = Field(..., ge=0, description="order size in shares")
    price: float = Field(..., gt=0, description="reference price RM/share")
    sigma_bar: float = Field(..., ge=0, description="per-bar Rogers-Satchell sigma")
    adv_bar_shares: float = Field(..., ge=0,
                                  description="average shares per bar over lookback")
    is_liquid: bool = True


class ImpactModel(BaseModel):
    """Composite market-impact model used by the intraday engine.

    Reports per-side cost in bps, bounded by:
      - `participation_cap` (10% by default; trades above this are clipped)
      - `min_trade_notional_rm` (gates out economically-pointless trades;
        BPS becomes effectively infinite below the gate)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    c_liquid: float = 1.0
    c_illiquid: float = 1.5
    participation_cap: float = 0.10
    min_trade_notional_rm: float = 16_000.0

    def cost_bps(self, quote: ExecutionQuote) -> float:
        """Estimated per-side impact in bps for `quote`.

        Returns 0 for q==0. Returns INF (1e9 bps in practice) if the trade
        notional is below `min_trade_notional_rm` (= economic non-starter).
        Participation above `participation_cap` is clipped to the cap before
        the Kissell-Glantz formula is applied (RH spec).
        """
        if quote.q == 0:
            return 0.0
        notional = quote.q * quote.price
        if notional < self.min_trade_notional_rm:
            return 1e9
        # Participation clip
        if quote.adv_bar_shares > 0:
            participation = quote.q / quote.adv_bar_shares
            q_eff = quote.q
            if participation > self.participation_cap:
                q_eff = self.participation_cap * quote.adv_bar_shares
        else:
            q_eff = quote.q  # no ADV info -> don't clip, but cost will be huge anyway

        c = self.c_liquid if quote.is_liquid else self.c_illiquid
        # fractional price move
        frac = kissell_glantz_impact(
            q=q_eff, sigma=quote.sigma_bar, adv_bar=quote.adv_bar_shares, c=c
        )
        return frac * 10_000.0


__all__ = [
    "ExecutionQuote",
    "ImpactModel",
    "kissell_glantz_impact",
    "rogers_satchell_sigma",
]
