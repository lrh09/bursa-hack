"""Position-sizing abstractions for the intraday engine.

Design contract:
  - A `Sizer` turns a per-trade context (equity, entry price, stop price,
    ADV-bar in shares, participation cap) into an integer `qty` (shares to
    trade). The engine consumes one Sizer per run; strategies do NOT size.
  - Sizers are pydantic-frozen models so their config is hashable / cacheable.
  - Two concrete sizers ship today:
      * FixedFractionalRiskSizer  -- "risk N% of equity per trade", which is
        the textbook Kelly-lite approach and the default for ORB. It REQUIRES
        the strategy to publish a `stop_price` (no stop -> no risk-per-share
        -> can't size, so we refuse).
      * FixedFractionNotionalSizer -- "spend N% of equity per trade". Simpler;
        doesn't need a stop. Registered now for future mean-reversion / VWAP
        strategies that don't always carry a stop.
  - Participation cap is applied at the Sizer level (NOT the impact model)
    because it determines `qty`, which feeds back into impact_bps and the
    min-notional gate. The impact model still independently clips for cost
    calculation, but the books-of-record qty is what comes out of `size()`.
  - Min-notional gating is the engine's job, not the Sizer's. A Sizer returns
    a `qty`; the engine decides whether to take the trade per-regime (since
    different regimes have different min-notionals). This keeps Sizer pure
    and regime-independent.

Scalability: all methods are pure functions over scalars, no I/O, no state.
Trivially parallel across (variant, code, ts) inside the engine loop.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field


class Sizer(BaseModel, ABC):
    """Abstract position sizer.

    Subclasses MUST override `size()`. Subclass identity is part of the
    backtest's cache key (via `model_dump()` on the engine's `sizer` field),
    so swapping sizers invalidates cached results -- intentional.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = "abstract"

    @abstractmethod
    def size(
        self,
        equity: float,
        entry_price: float,
        stop_price: float | None,
        adv_bar_shares: float,
        participation_cap: float,
    ) -> int:
        """Return the integer share quantity to trade.

        Args:
            equity            : current account equity in RM.
            entry_price       : expected fill price in RM/share (>0).
            stop_price        : strategy-published stop price in RM/share, or
                                None if the strategy emits no stop. Risk-based
                                sizers raise on None; notional sizers ignore.
            adv_bar_shares    : 20-day median of per-minute shares for the code,
                                lagged 1 trading day. Used for participation cap.
            participation_cap : fractional cap on (qty / adv_bar_shares).

        Returns: integer shares (>=0). Zero means "the math says don't trade"
        (e.g. risk-per-share is zero, equity is zero, ADV is zero).
        """


# ---------------------------------------------------------------------------
# Fixed-fractional-risk
# ---------------------------------------------------------------------------


class FixedFractionalRiskSizer(Sizer):
    """Risk `risk_per_trade_pct` percent of equity per trade.

    qty_uncapped = floor( (equity * pct/100) / |entry - stop| )
    qty          = min(qty_uncapped, floor(participation_cap * adv_bar_shares))

    Refuses if `stop_price` is None: a risk-based sizer NEEDS a stop. The
    strategy's responsibility is to emit a stop on every signal.
    """

    name: str = "fixed_fractional_risk"
    risk_per_trade_pct: float = Field(0.5, gt=0.0, le=100.0)

    def size(
        self,
        equity: float,
        entry_price: float,
        stop_price: float | None,
        adv_bar_shares: float,
        participation_cap: float,
    ) -> int:
        if equity <= 0 or entry_price <= 0:
            return 0
        if stop_price is None or (isinstance(stop_price, float) and math.isnan(stop_price)):
            raise ValueError(
                f"{self.name}: stop_price is required for risk-based sizing; "
                "strategy must emit non-null stop on every signal"
            )
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            return 0
        risk_rm = equity * (self.risk_per_trade_pct / 100.0)
        target_qty = math.floor(risk_rm / risk_per_share)
        if target_qty <= 0:
            return 0
        if adv_bar_shares > 0 and participation_cap > 0:
            cap_qty = math.floor(participation_cap * adv_bar_shares)
            return max(0, min(target_qty, cap_qty))
        return target_qty


# ---------------------------------------------------------------------------
# Fixed-fraction-notional
# ---------------------------------------------------------------------------


class FixedFractionNotionalSizer(Sizer):
    """Spend `fraction` of equity per trade (no stop required).

    qty_uncapped = floor( (equity * fraction) / entry_price )
    qty          = min(qty_uncapped, floor(participation_cap * adv_bar_shares))
    """

    name: str = "fixed_fraction_notional"
    fraction: float = Field(0.02, gt=0.0, le=1.0)

    def size(
        self,
        equity: float,
        entry_price: float,
        stop_price: float | None,  # noqa: ARG002 -- unused, kept for ABC parity
        adv_bar_shares: float,
        participation_cap: float,
    ) -> int:
        if equity <= 0 or entry_price <= 0:
            return 0
        notional_target = equity * self.fraction
        target_qty = math.floor(notional_target / entry_price)
        if target_qty <= 0:
            return 0
        if adv_bar_shares > 0 and participation_cap > 0:
            cap_qty = math.floor(participation_cap * adv_bar_shares)
            return max(0, min(target_qty, cap_qty))
        return target_qty


__all__ = [
    "FixedFractionNotionalSizer",
    "FixedFractionalRiskSizer",
    "Sizer",
]
