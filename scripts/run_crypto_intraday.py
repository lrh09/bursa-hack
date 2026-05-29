"""Intraday crypto test — the last untested frontier vs CAGR>15% / DD<40%.

Daily systematic crypto proved infeasible (recent Sharpe ~0.5 -> can't get 15%
at <40% DD). Higher frequency CAN have higher Sharpe (microstructure, not beta).
Crypto perps also dodge the cost wall that killed Bursa intraday (no stamp duty,
~5bps maker fees, big intraday moves).

Tests hourly momentum + hourly mean-reversion (per-coin, vol-targeted) with
realistic perp fees, through the DSR + bootstrap gate. Decisive: if nothing
clears here either, the search across asset classes is exhausted.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bursahack.futures.binance_klines import close_panel  # noqa: E402
from bursahack.futures.crypto_signals import _vol_target  # noqa: E402
from bursahack.futures.tsmom import compute_metrics  # noqa: E402
from bursahack.intraday.diagnostics import (  # noqa: E402
    deflated_sharpe_ratio, sharpe_ratio, stationary_bootstrap_sharpe_ci,
)

SYMS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT",
        "SOL": "SOLUSDT", "XRP": "XRPUSDT", "DOGE": "DOGEUSDT",
        "LINK": "LINKUSDT", "LTC": "LTCUSDT"}
INTERVAL = "1h"
ANN = 365 * 24                 # hourly bars per year
FEE_BPS = 10.0                 # round-trip perp taker; maker would be lower
BAR_CAGR, BAR_DD = 0.15, -0.40


def _per_coin_book(prices: pd.DataFrame, signal: pd.DataFrame, *,
                   pvol: float, vol_window: int, fee_bps: float) -> pd.Series:
    """Equal-risk book from a per-coin signal in [-1,1]. Vol-scale each coin,
    lag 1 bar, charge turnover, average, then vol-target the book."""
    ret = prices.pct_change(fill_method=None)
    vol = ret.rolling(vol_window).std() * np.sqrt(ANN)
    w = (signal * (0.20 / vol)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    w_lag = w.shift(1).fillna(0.0)
    gross = w_lag * ret
    turn = (w_lag - w_lag.shift(1)).abs()
    net = gross - turn * (fee_bps / 10_000.0)
    raw = net.mean(axis=1, skipna=True).fillna(0.0)
    return _vol_target(raw, pvol, vol_window, ANN)


def momentum_signal(prices: pd.DataFrame, lookback: int) -> pd.DataFrame:
    return np.sign(prices.pct_change(lookback))


def meanrev_signal(prices: pd.DataFrame, window: int, k: float) -> pd.DataFrame:
    ma = prices.rolling(window).mean()
    sd = prices.rolling(window).std()
    z = (prices - ma) / sd
    # Fade extremes: long when very oversold, short when very overbought.
    sig = (-z / k).clip(-1, 1)
    return sig.where(z.abs() >= k, 0.0)


def report(s: pd.Series, label: str) -> dict:
    r = s.dropna(); eq = (1 + r).cumprod()
    m = compute_metrics(r, eq, ann_factor=ANN); m["label"] = label
    ok = m["cagr"] >= BAR_CAGR and m["max_drawdown"] >= BAR_DD
    print(f"  {label:<26} CAGR={m['cagr']*100:6.1f}%  vol={m['ann_vol']*100:4.0f}%  "
          f"Sharpe={m['sharpe']:5.2f}  maxDD={m['max_drawdown']*100:6.1f}%  "
          f"Calmar={m['calmar']:.2f}{'  <-- BAR PASS' if ok else ''}")
    return m


def main() -> None:
    print("=" * 70)
    print("Intraday crypto (hourly) vs CAGR>15% / DD<40%")
    print("=" * 70)
    start_ms = int(pd.Timestamp("2020-01-01").timestamp() * 1000)
    print(f"[fetch] {len(SYMS)} coins, {INTERVAL} klines (cached) ...")
    prices = close_panel(SYMS, interval=INTERVAL, start_ms=start_ms).dropna(how="all")
    if prices.empty:
        print("[fetch] NO DATA"); return
    print(f"[fetch] {prices.shape[0]} bars x {prices.shape[1]} coins; "
          f"{prices.index.min()} -> {prices.index.max()}\n")

    sleeves: dict[str, pd.Series] = {}
    print("--- sleeves (hourly, 30% vol, 10bps RT) ---")
    for lb in (6, 24, 72, 168):
        s = _per_coin_book(prices, momentum_signal(prices, lb),
                           pvol=0.30, vol_window=168, fee_bps=FEE_BPS)
        sleeves[f"mom-{lb}h"] = s; report(s, f"momentum {lb}h")
    for w, k in ((24, 2.0), (72, 2.0), (168, 2.5)):
        s = _per_coin_book(prices, meanrev_signal(prices, w, k),
                           pvol=0.30, vol_window=168, fee_bps=FEE_BPS)
        sleeves[f"rev-{w}h"] = s; report(s, f"mean-rev {w}h k{k}")

    # Ensemble of the positive-Sharpe sleeves.
    pos = [s for s in sleeves.values() if sharpe_ratio(s.dropna().values) > 0]
    if pos:
        ens = _vol_target(pd.concat(pos, axis=1).mean(axis=1).fillna(0.0),
                          0.30, 168, ANN)
        print("\n--- ensemble (positive sleeves) ---")
        m = report(ens, "ENSEMBLE")
        # Harness gate.
        trial = np.array([sharpe_ratio(s.dropna().values) for s in sleeves.values()])
        v = ens.dropna().values
        dsr = deflated_sharpe_ratio(v, trial)
        ci = stationary_bootstrap_sharpe_ci(v, n_boot=2000, rng_seed=0, periods_per_year=ANN)
        bar = m["cagr"] >= BAR_CAGR and m["max_drawdown"] >= BAR_DD
        print(f"\n[gate] DSR={dsr['dsr']:.3f}  bootCI=[{ci['lo']:.2f},{ci['hi']:.2f}]  "
              f"BAR={'PASS' if bar else 'FAIL'}")
        # Per-year.
        ey = (1 + ens).groupby(ens.index.year).prod() - 1
        print("[year] " + "  ".join(f"{y}:{v*100:.0f}%" for y, v in ey.items()))
    print("\nDONE")


if __name__ == "__main__":
    main()
