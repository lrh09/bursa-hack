"""Fetch + cache Binance perpetual funding-rate history.

Funding is the genuinely uncorrelated, recently-persistent crypto edge: perp
longs pay shorts (positive funding) most of the time, so a delta-neutral
"harvest" (long spot / short perp when funding>0) earns a steady market-
neutral yield that does NOT depend on price direction — exactly what the
momentum sleeves lack in flat regimes.

Data: Binance USD-M futures `fundingRate` endpoint (public, no auth). Funding
posts every 8h. Cached to parquet so we don't re-hit the API each run.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

_BASE = "https://fapi.binance.com/fapi/v1/fundingRate"


def _cache_dir() -> Path:
    from bursahack.paths import REPO_ROOT
    d = REPO_ROOT / ".tmp" / "crypto" / "funding"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def fetch_funding(symbol: str, refresh: bool = False) -> pd.DataFrame:
    """Full funding-rate history for one perp symbol (e.g. 'BTCUSDT').

    Returns DataFrame indexed by UTC timestamp with a 'funding' column
    (per-8h rate as a decimal, e.g. 0.0001 = 1bp). Cached to parquet.
    """
    cache = _cache_dir() / f"{symbol}.parquet"
    if cache.exists() and not refresh:
        return pd.read_parquet(cache)

    rows: list[dict] = []
    start = 0  # ms epoch; 0 = from inception
    while True:
        url = f"{_BASE}?symbol={symbol}&limit=1000"
        if start:
            url += f"&startTime={start}"
        batch = _get(url)
        if not batch:
            break
        rows.extend(batch)
        last = int(batch[-1]["fundingTime"])
        if len(batch) < 1000:
            break
        start = last + 1
        time.sleep(0.25)  # be polite to the API
        if last > int(time.time() * 1000):
            break

    if not rows:
        return pd.DataFrame(columns=["funding"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["funding"] = df["fundingRate"].astype(float)
    out = df[["ts", "funding"]].drop_duplicates("ts").set_index("ts").sort_index()
    out.to_parquet(cache)
    return out


def funding_daily_panel(symbols: dict[str, str], refresh: bool = False) -> pd.DataFrame:
    """Daily per-coin funding (sum of the 3 daily 8h rates).

    Args:
      symbols: map of {output_column_name: binance_symbol}, e.g.
               {'BTC-USD': 'BTCUSDT', ...} so columns align with the spot panel.

    Returns: DataFrame (DatetimeIndex date × output_column_name) of daily
             total funding (decimal). NaN where a perp didn't exist yet.
    """
    cols = {}
    for out_name, sym in symbols.items():
        try:
            f = fetch_funding(sym, refresh=refresh)
        except Exception as exc:  # noqa: BLE001
            print(f"[funding] {sym} fetch failed: {exc!r}")
            continue
        if f.empty:
            continue
        daily = f["funding"].groupby(f.index.normalize()).sum()
        cols[out_name] = daily
    if not cols:
        return pd.DataFrame()
    panel = pd.DataFrame(cols)
    panel.index = pd.to_datetime(panel.index)
    return panel.sort_index()


__all__ = ["fetch_funding", "funding_daily_panel"]
