"""First-pass futures TSMOM backtest + harness gate.

Pulls the cross-asset basket (free yfinance daily — NOT roll-adjusted, so
absolute CAGR is approximate; the SHAPE — is the edge there, what's the DD,
does it clear DSR — is real), runs several trend-speed / vol-target variants,
ranks by CAGR (RH's objective), and runs the top variant through the reused
DSR + bootstrap-CI gate.

Output: .tmp/futures/tsmom_scorecard.md + equity-curve PNG.

Honesty caveats baked into the report: yfinance front-month is not roll-
adjusted; a final go/no-go needs Norgate/Databento. This pass tells us
whether to spend on that.
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

from bursahack.futures.contracts import (  # noqa: E402
    BASKET, basket_tickers, cost_bps_by_ticker, sector_by_ticker,
)
from bursahack.futures.tsmom import TSMOMConfig, backtest_tsmom  # noqa: E402
from bursahack.intraday.diagnostics import (  # noqa: E402
    deflated_sharpe_ratio, effective_n, sharpe_ratio, stationary_bootstrap_sharpe_ci,
)

import yfinance as yf  # noqa: E402


# Variant grid: trend speeds × portfolio vol target × signal mode.
# RH wants CAGR-over-Sharpe + tolerates bumps -> include faster/higher-vol.
VARIANTS = [
    {"label": "combined 1/3/12, 15% vol", "speeds": (21, 63, 252), "mode": "sign", "pvol": 0.15},
    {"label": "combined 1/3/12, 20% vol", "speeds": (21, 63, 252), "mode": "sign", "pvol": 0.20},
    {"label": "combined 1/3/12, 10% vol", "speeds": (21, 63, 252), "mode": "sign", "pvol": 0.10},
    {"label": "slow 12mo, 15% vol",       "speeds": (252,),         "mode": "sign", "pvol": 0.15},
    {"label": "medium 3mo, 15% vol",      "speeds": (63,),          "mode": "sign", "pvol": 0.15},
    {"label": "fast 1mo, 15% vol",        "speeds": (21,),          "mode": "sign", "pvol": 0.15},
    {"label": "fast+med 1/3, 20% vol",    "speeds": (21, 63),       "mode": "sign", "pvol": 0.20},
    {"label": "combined continuous, 15%", "speeds": (21, 63, 252), "mode": "continuous", "pvol": 0.15},
]


def fetch_prices() -> pd.DataFrame:
    print(f"[fetch] {len(BASKET)} futures (max history) via yfinance ...")
    raw = yf.download(basket_tickers(), period="max", interval="1d",
                      progress=False, auto_adjust=False)
    closes = raw["Close"].copy().sort_index()
    closes = closes.dropna(how="all")
    print(f"[fetch] {closes.shape[0]} rows x {closes.shape[1]} markets; "
          f"{closes.index.min().date()} -> {closes.index.max().date()}")
    return closes


def main() -> None:
    print("=" * 70)
    print("Futures TSMOM first-pass backtest + harness gate")
    print("=" * 70)

    closes = fetch_prices()
    costs = cost_bps_by_ticker()

    results = []
    for v in VARIANTS:
        cfg = TSMOMConfig(
            speeds=v["speeds"], signal_mode=v["mode"],
            portfolio_vol_target=v["pvol"],
        )
        res = backtest_tsmom(closes, cost_bps=costs, cfg=cfg)
        results.append((v, res))
        m = res.metrics
        print(f"  {v['label']:<32} CAGR={m['cagr']*100:6.2f}%  "
              f"vol={m['ann_vol']*100:5.1f}%  Sharpe={m['sharpe']:.2f}  "
              f"maxDD={m['max_drawdown']*100:6.1f}%  Calmar={m['calmar']:.2f}  "
              f"skew={m['skew']:+.2f}")

    # Rank by CAGR (RH's objective).
    results.sort(key=lambda x: x[1].metrics["cagr"], reverse=True)

    # --- Harness gate on the variant set ---
    # Align variant daily returns on a common index for the trial-correlation
    # and DSR. Per-period (non-annualized) Sharpes are the DSR trial set.
    ret_df = pd.DataFrame({v["label"]: res.portfolio_returns
                           for v, res in results}).dropna(how="any")
    trial_sharpes = np.array([sharpe_ratio(ret_df[c].values) for c in ret_df.columns])
    corr = np.corrcoef(ret_df.values, rowvar=False)
    corr = np.nan_to_num(corr, nan=0.0)
    np.fill_diagonal(corr, 1.0)
    eff_n = effective_n(corr)
    print(f"\n[gate] variants={len(results)}  effective_n={eff_n:.1f}  "
          f"(mean pairwise corr={corr[~np.eye(len(corr),dtype=bool)].mean():+.2f})")

    top_v, top_res = results[0]
    top_daily = top_res.portfolio_returns.values
    dsr = deflated_sharpe_ratio(top_daily, trial_sharpes, effective_n_trials=eff_n)
    ci = stationary_bootstrap_sharpe_ci(top_daily, n_boot=2000, rng_seed=0,
                                        periods_per_year=252)
    print(f"[gate] TOP = {top_v['label']}")
    print(f"[gate]   CAGR={top_res.metrics['cagr']*100:.2f}%  "
          f"maxDD={top_res.metrics['max_drawdown']*100:.1f}%  "
          f"Calmar={top_res.metrics['calmar']:.2f}  skew={top_res.metrics['skew']:+.2f}")
    print(f"[gate]   DSR={dsr['dsr']:.3f}   bootstrap Sharpe CI=[{ci['lo']:.2f}, {ci['hi']:.2f}]")

    # --- Outputs ---
    out_dir = REPO_ROOT / ".tmp" / "futures"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Equity chart: top-3 variants.
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 6))
        for v, res in results[:3]:
            ax.plot(res.equity.index, res.equity.values, label=v["label"], lw=1.3)
        ax.set_yscale("log")
        ax.set_title("Futures TSMOM — equity curves (log scale), top-3 by CAGR\n"
                     "yfinance front-month (NOT roll-adjusted) — shape, not absolute PnL")
        ax.set_ylabel("growth of 1 (log)")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        chart = out_dir / "tsmom_equity.png"
        fig.savefig(chart, dpi=130)
        print(f"[plot] -> {chart}")
    except Exception as exc:
        print(f"[plot] skipped: {exc}")

    # Scorecard.
    md = ["# Futures TSMOM — first-pass scorecard", "",
          f"- Data: yfinance front-month daily, {closes.index.min().date()} -> "
          f"{closes.index.max().date()} (**NOT roll-adjusted** — shape only, not absolute PnL).",
          f"- Basket: {len(BASKET)} markets across "
          f"{len(set(sector_by_ticker().values()))} sectors.",
          f"- Variants tried: {len(results)} (trend speed × vol target × signal mode).",
          f"- Ranked by **CAGR** (RH's objective).", "",
          "| rank | variant | CAGR | ann vol | Sharpe | maxDD | Calmar | skew |",
          "|---|---|---|---|---|---|---|---|"]
    for i, (v, res) in enumerate(results, 1):
        m = res.metrics
        md.append(f"| {i} | {v['label']} | {m['cagr']*100:.2f}% | {m['ann_vol']*100:.1f}% | "
                  f"{m['sharpe']:.2f} | {m['max_drawdown']*100:.1f}% | {m['calmar']:.2f} | "
                  f"{m['skew']:+.2f} |")
    md += ["",
           "## Harness gate (top variant by CAGR)", "",
           f"- TOP: **{top_v['label']}**",
           f"- CAGR **{top_res.metrics['cagr']*100:.2f}%**, max-DD "
           f"**{top_res.metrics['max_drawdown']*100:.1f}%**, Calmar "
           f"{top_res.metrics['calmar']:.2f}, **skew {top_res.metrics['skew']:+.2f}** "
           "(positive skew = the convexity we want).",
           f"- DSR (deflated for {len(results)} trials, effective_n={eff_n:.1f}): "
           f"**{dsr['dsr']:.3f}**  (>0.95 strong, ~0.5 ambiguous).",
           f"- Bootstrap Sharpe 95% CI: **[{ci['lo']:.2f}, {ci['hi']:.2f}]** "
           f"(lo>0 = bootstrap-significant).", "",
           "## Honest caveats",
           "- yfinance front-month is NOT roll-adjusted: absolute CAGR is approximate "
           "(roll gaps add noise both ways). A final go/no-go needs Norgate/Databento.",
           "- Skew + DD are the load-bearing numbers for a CAGR-maximizing investor, "
           "not Sharpe.",
           "- Costs modeled as turnover × per-market round-trip bps (conservative for "
           "liquid contracts at IBKR).", ""]
    (out_dir / "tsmom_scorecard.md").write_text("\n".join(md), encoding="utf-8")
    print(f"[report] -> {out_dir / 'tsmom_scorecard.md'}")
    print("\nDONE")


if __name__ == "__main__":
    main()
