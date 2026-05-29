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


# Core 13-market basket (the original diversification-test set).
CORE_BASKET: list[ContractSpec] = [
    ContractSpec("ES=F", "S&P 500",      "equity", 0.8,  50.0,  "MES=F"),
    ContractSpec("NQ=F", "Nasdaq 100",   "equity", 0.8,  20.0,  "MNQ=F"),
    ContractSpec("ZN=F", "10Y T-Note",   "rates",  0.8,  1000.0),
    ContractSpec("ZB=F", "30Y T-Bond",   "rates",  1.0,  1000.0),
    ContractSpec("6E=F", "EUR/USD",      "fx",     0.8,  125000.0, "M6E=F"),
    ContractSpec("6J=F", "JPY/USD",      "fx",     1.0,  12500000.0),
    ContractSpec("GC=F", "Gold",         "metal",  1.5,  100.0, "MGC=F"),
    ContractSpec("SI=F", "Silver",       "metal",  2.0,  5000.0),
    ContractSpec("CL=F", "WTI Crude",    "energy", 1.5,  1000.0, "MCL=F"),
    ContractSpec("NG=F", "Natural Gas",  "energy", 3.0,  10000.0),
    ContractSpec("ZC=F", "Corn",         "grain",  3.0,  50.0),
    ContractSpec("ZS=F", "Soybeans",     "grain",  3.0,  50.0),
    ContractSpec("ZW=F", "Wheat",        "grain",  3.5,  50.0),
]

# Extended additions — chosen by SECTOR LOGIC (more uncorrelated bets),
# not by backtest performance. ~22 more markets across the same + new
# sub-sectors. Some yfinance series are short/thin; the loader drops
# all-NaN columns and the backtest contributes each market when it has data.
_EXTENDED_ADDITIONS: list[ContractSpec] = [
    # More equity index (US small/mid + Dow). Correlated to ES — modest add.
    ContractSpec("YM=F", "Dow Jones",    "equity", 1.0,  5.0,   "MYM=F"),
    ContractSpec("RTY=F", "Russell 2000","equity", 1.0,  50.0,  "M2K=F"),
    # More rates — short end (own factor vs long bonds).
    ContractSpec("ZF=F", "5Y T-Note",    "rates",  0.8,  1000.0),
    ContractSpec("ZT=F", "2Y T-Note",    "rates",  0.8,  2000.0),
    # More FX — a real diversifier (commodity + safe-haven currencies).
    ContractSpec("6B=F", "GBP/USD",      "fx",     1.0,  62500.0),
    ContractSpec("6A=F", "AUD/USD",      "fx",     1.0,  100000.0, "M6A=F"),
    ContractSpec("6C=F", "CAD/USD",      "fx",     1.0,  100000.0),
    ContractSpec("6S=F", "CHF/USD",      "fx",     1.2,  125000.0),
    ContractSpec("6N=F", "NZD/USD",      "fx",     1.2,  100000.0),
    ContractSpec("DX=F", "US Dollar Idx","fx",     1.2,  1000.0),
    # More metals — copper (industrial) + PGMs (own cycle).
    ContractSpec("HG=F", "Copper",       "metal",  1.5,  25000.0),
    ContractSpec("PL=F", "Platinum",     "metal",  2.5,  50.0),
    ContractSpec("PA=F", "Palladium",    "metal",  3.0,  100.0),
    # More energy — refined products + Brent.
    ContractSpec("HO=F", "Heating Oil",  "energy", 2.0,  42000.0),
    ContractSpec("RB=F", "RBOB Gasoline","energy", 2.0,  42000.0),
    ContractSpec("BZ=F", "Brent Crude",  "energy", 1.8,  1000.0),
    # Full grains + oilseeds complex.
    ContractSpec("ZL=F", "Soybean Oil",  "grain",  3.0,  600.0),
    ContractSpec("ZM=F", "Soybean Meal", "grain",  3.0,  100.0),
    ContractSpec("ZO=F", "Oats",         "grain",  5.0,  50.0),
    # Softs — genuinely uncorrelated weather/crop markets.
    ContractSpec("KC=F", "Coffee",       "soft",   4.0,  37500.0),
    ContractSpec("SB=F", "Sugar",        "soft",   3.5,  112000.0),
    ContractSpec("CC=F", "Cocoa",        "soft",   4.0,  10.0),
    ContractSpec("CT=F", "Cotton",       "soft",   3.5,  500.0),
    # Livestock — own (uncorrelated) cycle.
    ContractSpec("LE=F", "Live Cattle",  "livestock", 5.0, 400.0),
    ContractSpec("HE=F", "Lean Hogs",    "livestock", 5.0, 400.0),
    # Crypto — high vol, low correlation; size tiny.
    ContractSpec("BTC=F", "Bitcoin",     "crypto", 5.0,  5.0,   "MBT=F"),
    ContractSpec("ETH=F", "Ether",       "crypto", 5.0,  50.0,  "MET=F"),
]

# Default basket used by the backtest = core + extended (~38 markets).
BASKET: list[ContractSpec] = CORE_BASKET + _EXTENDED_ADDITIONS


def basket_tickers() -> list[str]:
    return [c.ticker for c in BASKET]


def cost_bps_by_ticker() -> dict[str, float]:
    return {c.ticker: c.roundtrip_cost_bps for c in BASKET}


def sector_by_ticker() -> dict[str, str]:
    return {c.ticker: c.sector for c in BASKET}


__all__ = ["BASKET", "ContractSpec", "basket_tickers", "cost_bps_by_ticker",
           "sector_by_ticker"]
