"""Contract specs + the default cross-asset futures basket.

For the return-space first-pass backtest we mostly need:
  - ticker (yfinance continuous front-month symbol)
  - sector (for diversification reporting)
  - roundtrip_cost_bps (commission + half-spread, in bps of notional, per
    round-trip) — used to charge turnover.

`point_value` / `tick_size` are recorded for the eventual contract-count /
margin sizing layer (live execution), but the return-space backtest does
NOT need them — vol-targeting in return units is point-value-independent.

Cost bps are conservative retail-at-IBKR estimates for LIQUID contracts
(commission + half the typical bid-ask, on notional). Grains/energy carry
wider spreads than financials. These are approximate — verify against the
live IBKR commission schedule before trusting absolute net numbers.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContractSpec:
    ticker: str            # yfinance continuous front-month symbol
    name: str
    sector: str
    roundtrip_cost_bps: float   # commission + half-spread, bps of notional, round-trip
    point_value: float = 0.0    # USD per 1.0 price move per contract (live sizing only)
    micro_ticker: str | None = None  # CME micro equivalent, if one exists


# Default cross-asset basket. Sectors chosen for diversification (the
# diversification test showed ~12 effective bets across these).
BASKET: list[ContractSpec] = [
    # Equity index (ES + NQ are ~0.8 correlated in trend space — ~1 factor).
    ContractSpec("ES=F", "S&P 500",      "equity", 0.8,  50.0,  "MES=F"),
    ContractSpec("NQ=F", "Nasdaq 100",   "equity", 0.8,  20.0,  "MNQ=F"),
    # Rates / bonds.
    ContractSpec("ZN=F", "10Y T-Note",   "rates",  0.8,  1000.0),
    ContractSpec("ZB=F", "30Y T-Bond",   "rates",  1.0,  1000.0),
    # FX.
    ContractSpec("6E=F", "EUR/USD",      "fx",     0.8,  125000.0, "M6E=F"),
    ContractSpec("6J=F", "JPY/USD",      "fx",     1.0,  12500000.0),
    # Metals.
    ContractSpec("GC=F", "Gold",         "metal",  1.5,  100.0, "MGC=F"),
    ContractSpec("SI=F", "Silver",       "metal",  2.0,  5000.0),
    # Energy.
    ContractSpec("CL=F", "WTI Crude",    "energy", 1.5,  1000.0, "MCL=F"),
    ContractSpec("NG=F", "Natural Gas",  "energy", 3.0,  10000.0),
    # Grains (the key non-financial diversifier).
    ContractSpec("ZC=F", "Corn",         "grain",  3.0,  50.0),
    ContractSpec("ZS=F", "Soybeans",     "grain",  3.0,  50.0),
    ContractSpec("ZW=F", "Wheat",        "grain",  3.5,  50.0),
]


def basket_tickers() -> list[str]:
    return [c.ticker for c in BASKET]


def cost_bps_by_ticker() -> dict[str, float]:
    return {c.ticker: c.roundtrip_cost_bps for c in BASKET}


def sector_by_ticker() -> dict[str, str]:
    return {c.ticker: c.sector for c in BASKET}


__all__ = ["BASKET", "ContractSpec", "basket_tickers", "cost_bps_by_ticker",
           "sector_by_ticker"]
