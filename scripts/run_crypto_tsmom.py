"""Crypto trend-following first-pass vs the goal bar (CAGR>15%, DD<40%).

Crypto data via yfinance is clean spot, 24/7, NO roll problem (the thing that
wrecked the futures test). Trend can go short (needs perps or CME micro crypto
futures MBT/MET — that's what keeps drawdown < native -80%).

The harness gate (DSR, bootstrap, CPCV) plus a per-year breakdown and a
BTC buy-and-hold comparison guard against the #1 trap: a backtest that's just
2020-21 bull-market beta in disguise.

Output: .tmp/crypto/crypto_scorecard.md + equity PNG.
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

from bursahack.futures.tsmom import (  # noqa: E402
    TSMOMConfig, backtest_tsmom, compute_metrics,
)
from bursahack.intraday.diagnostics import (  # noqa: E402
    deflated_sharpe_ratio, effective_n, sharpe_ratio, stationary_bootstrap_sharpe_ci,
)
import yfinance as yf  # noqa: E402

CRYPTO = {
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "BNB-USD": "BNB",
    "XRP-USD": "XRP", "ADA-USD": "Cardano", "SOL-USD": "Solana",
    "DOGE-USD": "Dogecoin", "LTC-USD": "Litecoin", "LINK-USD": "Chainlink",
    "DOT-USD": "Polkadot", "AVAX-USD": "Avalanche", "MATIC-USD": "Polygon",
    "BCH-USD": "Bitcoin Cash",
}
ANN = 365                # crypto trades every day
COST_BPS = 10.0          # round-trip; perp taker ~5bps/side, conservative
BAR_CAGR = 0.15
BAR_DD = -0.40

VARIANTS = [
    {"label": "combined 1/3/12, 20% vol", "speeds": (21, 63, 252), "mode": "sign", "pvol": 0.20},
    {"label": "combined 1/3/12, 30% vol", "speeds": (21, 63, 252), "mode": "sign", "pvol": 0.30},
    {"label": "combined 1/3/12, 40% vol", "speeds": (21, 63, 252), "mode": "sign", "pvol": 0.40},
    {"label": "slow 12mo, 30% vol",       "speeds": (252,),         "mode": "sign", "pvol": 0.30},
    {"label": "med 3mo, 30% vol",         "speeds": (63,),          "mode": "sign", "pvol": 0.30},
    {"label": "fast+med 1/3, 30% vol",    "speeds": (21, 63),       "mode": "sign", "pvol": 0.30},
    {"label": "combined continuous, 30%", "speeds": (21, 63, 252), "mode": "continuous", "pvol": 0.30},
]


def main() -> None:
    print("=" * 70)
    print("Crypto TSMOM first-pass vs goal (CAGR>15%, DD<40%)")
    print("=" * 70)
    print(f"[fetch] {len(CRYPTO)} coins via yfinance ...")
    raw = yf.download(list(CRYPTO.keys()), period="max", interval="1d",
                      progress=False, auto_adjust=False)
    closes = raw["Close"].copy().sort_index().dropna(how="all")
    print(f"[fetch] {closes.shape[0]} rows x {closes.shape[1]} coins; "
          f"{closes.index.min().date()} -> {closes.index.max().date()}")
    costs = {t: COST_BPS for t in CRYPTO}

    results = []
    for v in VARIANTS:
        cfg = TSMOMConfig(speeds=v["speeds"], signal_mode=v["mode"],
                          portfolio_vol_target=v["pvol"], ann_factor=ANN)
        res = backtest_tsmom(closes, cost_bps=costs, cfg=cfg)
        results.append((v, res))
        m = res.metrics
        meets = "  <-- MEETS BAR" if (m["cagr"] >= BAR_CAGR and m["max_drawdown"] >= BAR_DD) else ""
        print(f"  {v['label']:<28} CAGR={m['cagr']*100:6.1f}%  vol={m['ann_vol']*100:5.0f}%  "
              f"Sharpe={m['sharpe']:.2f}  maxDD={m['max_drawdown']*100:6.1f}%  "
              f"Calmar={m['calmar']:.2f}  skew={m['skew']:+.2f}{meets}")

    results.sort(key=lambda x: x[1].metrics["cagr"], reverse=True)

    # --- Buy-and-hold BTC comparison (is the strategy just beta?) ---
    btc = closes["BTC-USD"].dropna()
    btc_ret = btc.pct_change(fill_method=None).dropna()
    btc_eq = (1 + btc_ret).cumprod()
    btc_m = compute_metrics(btc_ret, btc_eq, ann_factor=ANN)
    print(f"\n[bench] BTC buy&hold: CAGR={btc_m['cagr']*100:.1f}%  "
          f"maxDD={btc_m['max_drawdown']*100:.1f}%  Sharpe={btc_m['sharpe']:.2f}")

    # --- Harness gate on top variant ---
    ret_df = pd.DataFrame({v["label"]: res.portfolio_returns
                           for v, res in results}).dropna(how="any")
    trial_sharpes = np.array([sharpe_ratio(ret_df[c].values) for c in ret_df.columns])
    corr = np.corrcoef(ret_df.values, rowvar=False)
    corr = np.nan_to_num(corr, nan=0.0); np.fill_diagonal(corr, 1.0)
    eff_n = effective_n(corr)
    # Pick the highest-CAGR variant that MEETS THE BAR (CAGR>15% AND DD<40%);
    # fall back to highest CAGR if none meets it.
    bar_meeting = [(v, res) for v, res in results
                   if res.metrics["cagr"] >= BAR_CAGR and res.metrics["max_drawdown"] >= BAR_DD]
    top_v, top_res = (bar_meeting[0] if bar_meeting else results[0])
    td = top_res.portfolio_returns.values
    dsr = deflated_sharpe_ratio(td, trial_sharpes, effective_n_trials=eff_n)
    ci = stationary_bootstrap_sharpe_ci(td, n_boot=2000, rng_seed=0, periods_per_year=ANN)
    print(f"\n[gate] TOP = {top_v['label']}")
    print(f"[gate]   CAGR={top_res.metrics['cagr']*100:.1f}%  maxDD={top_res.metrics['max_drawdown']*100:.1f}%  "
          f"Calmar={top_res.metrics['calmar']:.2f}  skew={top_res.metrics['skew']:+.2f}")
    print(f"[gate]   DSR={dsr['dsr']:.3f}  bootstrap Sharpe CI=[{ci['lo']:.2f}, {ci['hi']:.2f}]  "
          f"(variants={len(results)}, eff_n={eff_n:.1f})")
    bar_ok = top_res.metrics["cagr"] >= BAR_CAGR and top_res.metrics["max_drawdown"] >= BAR_DD
    print(f"[gate]   BAR (CAGR>15%, DD<40%): {'PASS' if bar_ok else 'FAIL'}")

    # --- Per-year of top variant (regime dependence) ---
    r = top_res.portfolio_returns
    by_year = (1.0 + r).groupby(r.index.year).prod() - 1.0
    print("\n[year] top variant per-calendar-year return:")
    for yr, yret in by_year.items():
        print(f"  {yr}: {yret*100:>7.1f}%")

    # --- Outputs ---
    out_dir = REPO_ROOT / ".tmp" / "crypto"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 6))
        for v, res in results[:3]:
            ax.plot(res.equity.index, res.equity.values, label=v["label"], lw=1.3)
        ax.plot(btc_eq.index, btc_eq.values, label="BTC buy&hold", lw=1.0,
                ls="--", color="gray")
        ax.set_yscale("log")
        ax.set_title("Crypto TSMOM — equity (log), top-3 vs BTC buy&hold\n"
                     "yfinance spot daily; trend can short (needs perps/CME crypto futures)")
        ax.legend(fontsize=8); ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(out_dir / "crypto_equity.png", dpi=130)
        print(f"\n[plot] -> {out_dir / 'crypto_equity.png'}")
    except Exception as exc:
        print(f"[plot] skipped: {exc}")

    md = ["# Crypto TSMOM — first-pass vs goal (CAGR>15%, DD<40%)", "",
          f"- Data: yfinance spot daily, {closes.index.min().date()} -> {closes.index.max().date()} "
          f"({closes.shape[1]} coins). Clean (no roll problem). Cost {COST_BPS}bps RT.",
          f"- Venue note: trend SHORTS in bear markets -> needs perps or CME micro crypto "
          "futures (MBT/MET), not spot.", "",
          "| rank | variant | CAGR | vol | Sharpe | maxDD | Calmar | skew | bar |",
          "|---|---|---|---|---|---|---|---|---|"]
    for i, (v, res) in enumerate(results, 1):
        m = res.metrics
        ok = "PASS" if (m["cagr"] >= BAR_CAGR and m["max_drawdown"] >= BAR_DD) else "fail"
        md.append(f"| {i} | {v['label']} | {m['cagr']*100:.1f}% | {m['ann_vol']*100:.0f}% | "
                  f"{m['sharpe']:.2f} | {m['max_drawdown']*100:.1f}% | {m['calmar']:.2f} | "
                  f"{m['skew']:+.2f} | {ok} |")
    md += ["", f"- BTC buy&hold benchmark: CAGR {btc_m['cagr']*100:.1f}%, maxDD "
           f"{btc_m['max_drawdown']*100:.1f}%, Sharpe {btc_m['sharpe']:.2f}.",
           f"- Harness gate (top): DSR **{dsr['dsr']:.3f}**, bootstrap Sharpe CI "
           f"[{ci['lo']:.2f}, {ci['hi']:.2f}], {len(results)} variants (eff_n {eff_n:.1f}).",
           f"- **BAR: {'PASS' if bar_ok else 'FAIL'}** on the in-sample full history.", "",
           "## Caveats / next gates",
           "- Full-history backtest includes 2017 + 2020-21 mega-bulls. The per-year table "
           "exposes regime dependence; a real edge must survive bear years (2018, 2022).",
           "- Next: CPCV across regimes + a TRUE holdout (last ~1y untouched) before trusting.",
           "- yfinance spot has no funding/borrow cost for shorts — real perps charge funding; "
           "rerun with a funding haircut before live.", ""]
    (out_dir / "crypto_scorecard.md").write_text("\n".join(md), encoding="utf-8")
    print(f"[report] -> {out_dir / 'crypto_scorecard.md'}")
    print("\nDONE")


if __name__ == "__main__":
    main()
