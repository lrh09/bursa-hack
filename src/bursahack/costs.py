"""Per-leg transaction-cost model — M+ Online (Malacca Securities) on Bursa.

All costs apply to BOTH buy and sell legs and are computed on the raw RM
contract notional (shares x raw price), not the adjusted price.

  brokerage = max(0.05% x notional, RM 8.00)  x  (1 + 8% SST)
  clearing  = min(0.03% x notional, RM 1,000)
  stamp     = min(0.10% x notional, RM 1,000)         # Budget 2024 baseline
  slippage  = k_bps x sqrt(notional / 20d_ADV)        # impact model

Holding currency is RM. All inputs/outputs in RM.

References:
  - MPlus fee schedule: mplusonline.com/blog/help-sections/fees-charges/
  - SST rate: 8% on financial services (raised from 6% in March 2024)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeConfig:
    brokerage_rate: float = 0.0005       # 0.05%
    brokerage_min: float = 8.0           # RM 8 minimum
    sst_rate: float = 0.08               # 8% on brokerage
    clearing_rate: float = 0.0003        # 0.03%
    clearing_cap: float = 1_000.0        # RM 1,000 cap
    stamp_rate: float = 0.0010           # 0.10%
    stamp_cap: float = 1_000.0           # RM 1,000 cap


@dataclass(frozen=True)
class SlippageConfig:
    """sqrt-impact model: slippage_bps = k * sqrt(participation), where
    participation = order_notional / 20-day average daily turnover."""
    k_bps: float = 10.0                  # 10 bps at 100% of ADV (1.0 participation)
    min_bps: float = 2.0                 # half-spread floor for liquid names
    max_bps: float = 200.0               # cap at 2% to avoid pathologies on illiquids


MPLUS = FeeConfig()
SLIPPAGE = SlippageConfig()


def fees(notional: float, cfg: FeeConfig = MPLUS) -> float:
    """Total per-leg fees (RM) for a single-side trade of `notional` RM."""
    if notional <= 0:
        return 0.0
    brokerage = max(cfg.brokerage_rate * notional, cfg.brokerage_min) * (1 + cfg.sst_rate)
    clearing = min(cfg.clearing_rate * notional, cfg.clearing_cap)
    stamp = min(cfg.stamp_rate * notional, cfg.stamp_cap)
    return brokerage + clearing + stamp


def slippage_bps(notional: float, adv_20d: float, cfg: SlippageConfig = SLIPPAGE) -> float:
    """Estimated slippage in basis points for a trade `notional` against 20d ADV.

    Falls back to `max_bps` if ADV is missing/zero (highly illiquid name).
    """
    if adv_20d <= 0:
        return cfg.max_bps
    participation = notional / adv_20d
    bps = cfg.k_bps * (participation ** 0.5)
    return max(cfg.min_bps, min(cfg.max_bps, bps))


def slippage(notional: float, adv_20d: float, cfg: SlippageConfig = SLIPPAGE) -> float:
    """Slippage in RM for a single-side trade."""
    return notional * slippage_bps(notional, adv_20d, cfg) / 10_000.0


def total_cost(notional: float, adv_20d: float,
               fee_cfg: FeeConfig = MPLUS,
               slip_cfg: SlippageConfig = SLIPPAGE) -> tuple[float, float, float]:
    """Return (fees, slippage, total) for one leg."""
    f = fees(notional, fee_cfg)
    s = slippage(notional, adv_20d, slip_cfg)
    return f, s, f + s


def cost_bps(notional: float, adv_20d: float,
             fee_cfg: FeeConfig = MPLUS,
             slip_cfg: SlippageConfig = SLIPPAGE) -> float:
    """Round-trip cost in bps -- useful as a "is this strategy economic" gate."""
    if notional <= 0:
        return 0.0
    _, _, one_leg = total_cost(notional, adv_20d, fee_cfg, slip_cfg)
    return 2.0 * one_leg / notional * 10_000.0


# =============================================================================
# Pluggable FeeSchedule (intraday platform extension, 2026-05-18)
# =============================================================================
#
# The dataclass-based FeeConfig above is the *daily-resolution* cost model
# kept intact for backward compat. The classes below are pydantic-backed
# FeeSchedule variants the intraday platform plugs in (per-regime backtests
# report under every listed FeeSchedule side by side).
#
# `MPlusRetailFee` delegates to the existing `fees()` function so the two
# layers stay numerically identical on retail trades.

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402


class FeeSchedule(BaseModel):
    """Abstract base: pluggable cost regime for intraday backtests.

    Each subclass implements `roundtrip_cost(notional_rm) -> float` returning
    the per-trade round-trip cost in BPS (already x10000 / notional).
    Slippage / market-impact is NOT modeled here; see `intraday.impact`.
    Fees-only is intentional so the two effects compose cleanly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = "abstract"

    def roundtrip_cost(self, notional_rm: float) -> float:  # pragma: no cover - abstract
        raise NotImplementedError


class MPlusRetailFee(FeeSchedule):
    """MPlus (Malacca Securities) retail schedule -- Bursa 2021 baseline.

    Delegates to the existing `fees()` so the daily and intraday layers stay
    numerically identical on this regime.
    """

    name: str = "mplus_retail"
    brokerage_rate: float = 0.0005
    brokerage_min: float = 8.0
    sst_rate: float = 0.08
    clearing_rate: float = 0.0003
    clearing_cap: float = 1_000.0
    stamp_rate: float = 0.0010
    stamp_cap: float = 1_000.0

    def _as_fee_config(self) -> FeeConfig:
        return FeeConfig(
            brokerage_rate=self.brokerage_rate,
            brokerage_min=self.brokerage_min,
            sst_rate=self.sst_rate,
            clearing_rate=self.clearing_rate,
            clearing_cap=self.clearing_cap,
            stamp_rate=self.stamp_rate,
            stamp_cap=self.stamp_cap,
        )

    def roundtrip_cost(self, notional_rm: float) -> float:
        if notional_rm <= 0:
            return 0.0
        one_leg = fees(notional_rm, self._as_fee_config())
        return 2.0 * one_leg / notional_rm * 10_000.0


class InstitutionalFee(FeeSchedule):
    """Flat 5 bps round-trip, no minimum. Standard insto desk simplification."""

    name: str = "institutional"
    roundtrip_bps: float = 5.0

    def roundtrip_cost(self, notional_rm: float) -> float:
        if notional_rm <= 0:
            return 0.0
        return self.roundtrip_bps


class CustomFee(FeeSchedule):
    """Fully explicit fee schedule (no defaults). Used for sensitivity tests."""

    name: str = "custom"
    brokerage_rate: float = Field(...)
    brokerage_min: float = Field(...)
    sst_rate: float = Field(...)
    clearing_rate: float = Field(...)
    clearing_cap: float = Field(...)
    stamp_rate: float = Field(...)
    stamp_cap: float = Field(...)

    def roundtrip_cost(self, notional_rm: float) -> float:
        if notional_rm <= 0:
            return 0.0
        cfg = FeeConfig(
            brokerage_rate=self.brokerage_rate,
            brokerage_min=self.brokerage_min,
            sst_rate=self.sst_rate,
            clearing_rate=self.clearing_rate,
            clearing_cap=self.clearing_cap,
            stamp_rate=self.stamp_rate,
            stamp_cap=self.stamp_cap,
        )
        one_leg = fees(notional_rm, cfg)
        return 2.0 * one_leg / notional_rm * 10_000.0
