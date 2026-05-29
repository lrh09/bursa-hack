"""Crypto multi-factor: trend + cross-sectional momentum + reversal + ensemble.

The plain-trend first pass met the bar on full history but died after 2021.
This tests whether MARKET-NEUTRAL sleeves (which don't need a bull market)
deliver CONSISTENT returns — evaluated on full history AND on the recent
2022-2026 regime separately, plus per-year. The bar (CAGR>15%, DD<40%) must
hold on the RECENT regime to count as 'consistent'.
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

from bursahack.futures.tsmom import TSMOMConfig, backtest_tsmom, compute_metrics  # noqa: E402
from bursahack.futures.crypto_signals import (  # noqa: E402
    xs_momentum_returns, xs_reversal_returns, ensemble_returns,
)
from bursahack.intraday.diagnostics import (  # noqa: E402
    deflated_sharpe_ratio, sharpe_ratio, stationary_bootstrap_sharpe_ci,
)
import yfinance as yf  # noqa: E402

CRYPTO = ["BTC-USD", "ETH-USD", "BNB-USD", "XRP-USD", "ADA-USD", "SOL-USD",
          "DOGE-USD", "LTC-USD", "LINK-USD", "DOT-USD", "AVAX-USD", "MATIC-USD",
          "BCH-USD"]
ANN = 365
COST = 10.0
BAR_CAGR, BAR_DD = 0.15, -0.40
RECENT_START = pd.Timestamp("2022-01-01")


def metrics_for(returns: pd.Series, label: str) -> dict:
    r = returns.dropna()
    eq = (1 + r).cumprod()
    m = compute_metrics(r, eq, ann_factor=ANN)
    m["label"] = label
    return m


def fmt(m: dict, bar: bool = True) -> str:
    flag = ""
    if bar:
        flag = "  <-- BAR PASS" if (m["cagr"] >= BAR_CAGR and m["max_drawdown"] >= BAR_DD) else ""
    return (f"CAGR={m['cagr']*100:6.1f}%  vol={m['ann_vol']*100:4.0f}%  Sharpe={m['sharpe']:5.2f}  "
            f"maxDD={m['max_drawdown']*100:6.1f}%  Calmar={m['calmar']:.2f}  skew={m['skew']:+.1f}{flag}")


def main() -> None:
    print("=" * 72)
    print("Crypto multi-factor (trend + xs-mom + reversal + ensemble)")
    print("=" * 72)
    raw = yf.download(CRYPTO, period="max", interval="1d", progress=False, auto_adjust=False)
    prices = raw["Close"].copy().sort_index().dropna(how="all")
    print(f"[fetch] {prices.shape[0]} rows x {prices.shape[1]} coins; "
          f"{prices.index.min().date()} -> {prices.index.max().date()}\n")
    costs = {t: COST for t in CRYPTO}

    # --- Build sleeves (each a daily return series) ---
    trend_cfg = TSMOMConfig(speeds=(21, 63, 252), signal_mode="sign",
                            portfolio_vol_target=0.20, ann_factor=ANN)
    trend = backtest_tsmom(prices, cost_bps=costs, cfg=trend_cfg).portfolio_returns
    xsmom = xs_momentum_returns(prices, lookback=30, cost_bps=COST, pvol=0.20, ann=ANN)
    xsrev = xs_reversal_returns(prices, lookback=5, cost_bps=COST, pvol=0.20, ann=ANN)
    # Ensemble = trend + xs-momentum ONLY. xs-reversal is proven toxic in crypto
    # (momentum market, not reversal) — kept in the per-sleeve table as evidence,
    # excluded from the blend.
    ens = ensemble_returns([trend, xsmom], pvol=0.20, ann=ANN)

    sleeves = {"trend (TS)": trend, "xs-momentum": xsmom,
               "xs-reversal": xsrev, "ENSEMBLE(tr+xs)": ens}

    # --- Full history ---
    print("--- FULL HISTORY ---")
    for name, s in sleeves.items():
        print(f"  {name:<14} {fmt(metrics_for(s, name))}")

    # --- Recent regime (2022+) — the consistency test ---
    print("\n--- RECENT REGIME 2022-2026 (the consistency test) ---")
    recent = {}
    for name, s in sleeves.items():
        sr = s[s.index >= RECENT_START]
        m = metrics_for(sr, name)
        recent[name] = m
        print(f"  {name:<14} {fmt(m)}")

    # --- Per-year of the ensemble ---
    print("\n--- ENSEMBLE per-calendar-year ---")
    ey = (1 + ens).groupby(ens.index.year).prod() - 1
    for yr, v in ey.items():
        print(f"  {yr}: {v*100:>7.1f}%")

    # --- Harness gate on the ensemble (full + recent) ---
    print("\n--- HARNESS GATE (ensemble) ---")
    trial = np.array([sharpe_ratio(s.dropna().values) for s in sleeves.values()])
    for tag, series in (("full", ens), ("recent", ens[ens.index >= RECENT_START])):
        v = series.dropna().values
        dsr = deflated_sharpe_ratio(v, trial)
        ci = stationary_bootstrap_sharpe_ci(v, n_boot=2000, rng_seed=0, periods_per_year=ANN)
        m = metrics_for(series, "ens")
        bar = m["cagr"] >= BAR_CAGR and m["max_drawdown"] >= BAR_DD
        print(f"  [{tag:>6}] CAGR={m['cagr']*100:5.1f}%  maxDD={m['max_drawdown']*100:6.1f}%  "
              f"DSR={dsr['dsr']:.3f}  bootCI=[{ci['lo']:.2f},{ci['hi']:.2f}]  "
              f"BAR={'PASS' if bar else 'FAIL'}")

    # --- xs-momentum lookback/vol scan ON THE RECENT REGIME ---
    # Is ANY config able to clear 15%/40% in 2022-2026? (Guard: this is a
    # search on recent data — DSR/PBO would haircut it; treat as a ceiling probe.)
    print("\n--- xs-momentum recent-regime scan (2022-2026) ---")
    best = None
    for lb in (15, 30, 60, 90):
        for pv in (0.20, 0.30, 0.40):
            s = xs_momentum_returns(prices, lookback=lb, cost_bps=COST, pvol=pv, ann=ANN)
            sr = s[s.index >= RECENT_START]
            m = metrics_for(sr, f"lb{lb}/pv{int(pv*100)}")
            ok = m["cagr"] >= BAR_CAGR and m["max_drawdown"] >= BAR_DD
            if ok:
                print(f"  lb={lb:>2} pv={pv*100:.0f}%  CAGR={m['cagr']*100:5.1f}%  "
                      f"maxDD={m['max_drawdown']*100:6.1f}%  Sharpe={m['sharpe']:.2f}  <-- CLEARS BAR")
            if best is None or m["cagr"] > best[1]["cagr"]:
                best = (f"lb{lb}/pv{int(pv*100)}", m)
    bm = best[1]
    print(f"  best recent CAGR: {best[0]}  CAGR={bm['cagr']*100:.1f}%  "
          f"maxDD={bm['max_drawdown']*100:.1f}%  Sharpe={bm['sharpe']:.2f}")

    # --- Output ---
    out = REPO_ROOT / ".tmp" / "crypto"
    out.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 6))
        for name, s in sleeves.items():
            eq = (1 + s).cumprod()
            ax.plot(eq.index, eq.values, label=name, lw=1.2)
        ax.axvline(RECENT_START, color="red", ls=":", lw=1, label="2022 (recent regime)")
        ax.set_yscale("log"); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)
        ax.set_title("Crypto multi-factor sleeves (log equity) — does anything work AFTER 2022?")
        fig.tight_layout(); fig.savefig(out / "crypto_multifactor.png", dpi=130)
        print(f"\n[plot] -> {out / 'crypto_multifactor.png'}")
    except Exception as exc:
        print(f"[plot] skipped: {exc}")
    print("\nDONE")


if __name__ == "__main__":
    main()
