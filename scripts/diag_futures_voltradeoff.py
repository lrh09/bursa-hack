"""Diagnostic: vol-target tradeoff + per-year decomposition for TSMOM.

Answers "is 8%+ CAGR reachable, and at what drawdown?" on the current
(free, non-roll-adjusted) data. Uses the best-Sharpe config (slow 12-month
trend) and sweeps the portfolio vol target; then breaks the base case down
by calendar year to expose the 2011-2020 trend drought.
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

from bursahack.futures.contracts import basket_tickers, cost_bps_by_ticker  # noqa: E402
from bursahack.futures.tsmom import TSMOMConfig, backtest_tsmom, TRADING_DAYS  # noqa: E402

import yfinance as yf  # noqa: E402


def main() -> None:
    print("[fetch] basket via yfinance ...")
    raw = yf.download(basket_tickers(), period="max", interval="1d",
                      progress=False, auto_adjust=False)
    closes = raw["Close"].copy().sort_index().dropna(how="all")
    costs = cost_bps_by_ticker()
    print(f"[fetch] {closes.shape[0]} rows, {closes.index.min().date()} -> {closes.index.max().date()}")

    # --- Vol-target sweep on the slow-12mo config (best Sharpe) ---
    print("\n=== Vol-target tradeoff (slow 12-month trend) ===")
    print(f"{'vol target':>10} | {'CAGR':>7} | {'ann vol':>7} | {'Sharpe':>6} | {'maxDD':>7} | {'Calmar':>6}")
    print("-" * 60)
    base_res = None
    for pvol in (0.10, 0.15, 0.20, 0.25, 0.30):
        cfg = TSMOMConfig(speeds=(252,), signal_mode="sign", portfolio_vol_target=pvol)
        res = backtest_tsmom(closes, cost_bps=costs, cfg=cfg)
        m = res.metrics
        flag = "  <- 8% bar" if m["cagr"] >= 0.08 else ""
        print(f"{pvol*100:>9.0f}% | {m['cagr']*100:>6.2f}% | {m['ann_vol']*100:>6.1f}% | "
              f"{m['sharpe']:>6.2f} | {m['max_drawdown']*100:>6.1f}% | {m['calmar']:>6.2f}{flag}")
        if abs(pvol - 0.15) < 1e-9:
            base_res = res

    # --- Per-year decomposition of the 15% base case ---
    print("\n=== Per-calendar-year return (slow 12mo, 15% vol) ===")
    r = base_res.portfolio_returns
    by_year = (1.0 + r).groupby(r.index.year).prod() - 1.0
    for yr, yret in by_year.items():
        bar = "#" * max(0, int(yret * 100))
        neg = "" if yret >= 0 else f"({yret*100:.1f}%)"
        print(f"  {yr}: {yret*100:>6.1f}%  {bar}{neg if yret<0 else ''}")

    # --- Decade CAGR ---
    print("\n=== Decade CAGR (15% vol base) ===")
    eq = base_res.equity
    for lo, hi in ((2000, 2010), (2011, 2020), (2021, 2026)):
        seg = r[(r.index.year >= lo) & (r.index.year <= hi)]
        if len(seg) < 20:
            continue
        growth = float((1.0 + seg).prod())
        yrs = len(seg) / TRADING_DAYS
        cagr = growth ** (1.0 / yrs) - 1.0 if growth > 0 else -1.0
        eqs = (1.0 + seg).cumprod()
        dd = float((eqs / eqs.cummax() - 1.0).min())
        print(f"  {lo}-{hi}: CAGR {cagr*100:>6.2f}%  maxDD {dd*100:>6.1f}%")

    print("\nDONE")


if __name__ == "__main__":
    main()
