"""Fetch + cache Binance OHLCV klines (for intraday crypto backtests).

Free public spot API. Paginated, cached to parquet. Used to test whether
INTRADAY crypto (higher frequency -> potentially higher Sharpe) can clear a
return/drawdown bar that daily strategies cannot.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

_BASE = "https://api.binance.com/api/v3/klines"


def _cache_dir() -> Path:
    from bursahack.paths import REPO_ROOT
    d = REPO_ROOT / ".tmp" / "crypto" / "klines"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def fetch_klines(symbol: str, interval: str = "1h", start_ms: int = 0,
                 refresh: bool = False) -> pd.DataFrame:
    """OHLCV history for one symbol at `interval`. Cached to parquet.

    Returns DataFrame indexed by UTC close time with columns
    [open, high, low, close, volume].
    """
    cache = _cache_dir() / f"{symbol}_{interval}.parquet"
    if cache.exists() and not refresh:
        return pd.read_parquet(cache)

    rows: list = []
    start = start_ms
    while True:
        url = f"{_BASE}?symbol={symbol}&interval={interval}&limit=1000"
        if start:
            url += f"&startTime={start}"
        batch = _get(url)
        if not batch:
            break
        rows.extend(batch)
        last_open = int(batch[-1][0])
        if len(batch) < 1000:
            break
        start = last_open + 1
        time.sleep(0.2)
        if last_open > int(time.time() * 1000):
            break

    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=[
        "openTime", "open", "high", "low", "close", "volume", "closeTime",
        "qav", "trades", "tbav", "tqav", "ignore"])
    df["ts"] = pd.to_datetime(df["openTime"], unit="ms")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    out = df[["ts", "open", "high", "low", "close", "volume"]].drop_duplicates(
        "ts").set_index("ts").sort_index()
    out.to_parquet(cache)
    return out


def close_panel(symbols: dict[str, str], interval: str = "1h",
                start_ms: int = 0) -> pd.DataFrame:
    """Close-price panel (DatetimeIndex × output names) for a set of symbols."""
    cols = {}
    for name, sym in symbols.items():
        try:
            df = fetch_klines(sym, interval=interval, start_ms=start_ms)
        except Exception as exc:  # noqa: BLE001
            print(f"[klines] {sym} failed: {exc!r}")
            continue
        if not df.empty:
            cols[name] = df["close"]
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index()


__all__ = ["fetch_klines", "close_panel"]
