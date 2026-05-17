"""Universe definition + ticker-chain handling.

The default equity universe covers vanilla listed equity on Bursa Malaysia:
  - Main Market (4-digit ticker, e.g. `1295` for Public Bank)
  - Stapled Securities (4-digit + `SS`, e.g. `5235SS` for KLCCP Stapled)
  - ACE Market (5-digit starting with `03`, e.g. `03001` for Cloudaron)

Excluded by default:
  - Company warrants    (xxxxWA / WB / WC / WD / WE)        -- different return profile
  - Structured warrants (xxxxCx / Hx / Px, 5-6 digit codes) -- bank-issued derivatives
  - Rights / temp codes (xxxxOR / TR / RR / PR)             -- live for only a few days
  - ETFs                (xxxxEA)                            -- mostly inverse/leveraged trackers

Ticker chains: when the same company's SECURITY_ID changes (e.g. KLCC reorganised
from `5089` to `5235SS` in 2013), the engine must concat the price series to
preserve a continuous return history. The chain map below was derived empirically
from same-name + adjacent-date pairs in the master table; see investigate_2021.py
and the DQ findings.
"""
from __future__ import annotations

import re

import pandas as pd

# Maps successor SECURITY_ID/ticker -> predecessor. The engine concats the older
# series into the newer one so a single time series represents the economic entity.
TICKER_CHAINS: dict[str, str] = {
    "5235SS": "5089",  # KLCC Stapled (2013-05-10+) <- KLCC Property Hldgs (2007-2013)
}

_EQUITY_PATTERNS = (
    re.compile(r"^\d{4}$"),       # Main Market (incl. REITs)
    re.compile(r"^\d{4}SS$"),     # Stapled Securities
    re.compile(r"^03\d{3}$"),     # ACE Market
)


def _strip_suffix(ticker: str) -> str:
    return ticker.replace(":MK", "") if isinstance(ticker, str) else ""


def is_equity(ticker: str) -> bool:
    """Return True if `ticker` is a vanilla Bursa equity instrument."""
    core = _strip_suffix(ticker)
    return any(p.match(core) for p in _EQUITY_PATTERNS)


def equity_mask(tickers: pd.Series) -> pd.Series:
    """Vectorised `is_equity` for a pandas Series of tickers."""
    core = tickers.fillna("").str.replace(":MK", "", regex=False)
    return (
        core.str.match(r"^\d{4}$")
        | core.str.match(r"^\d{4}SS$")
        | core.str.match(r"^03\d{3}$")
    )


def filter_equity(df: pd.DataFrame, ticker_col: str = "TICKER") -> pd.DataFrame:
    """Filter a prices dataframe down to vanilla equity rows."""
    return df[equity_mask(df[ticker_col])].copy()
