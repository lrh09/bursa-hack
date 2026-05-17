"""Analyse the brute-force search log.

Aggregates walk-forward fold metrics per variant, applies Deflated Sharpe Ratio
with N_trials = total variants tested, and prints the top survivors.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from bursahack.metrics import deflated_sharpe_ratio
from bursahack.paths import RESULTS_DIR


def load_log(path: Path = RESULTS_DIR / "search_log.jsonl") -> pd.DataFrame:
    rows = []
    with path.open() as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            m = d.get("metrics")
            if m is None:
                continue
            rows.append({
                "strategy": d["name"],
                "params_hash": d["params_hash"],
                "params": json.dumps(d["params"], sort_keys=True),
                "param_dict": d["params"],
                "fold_idx": d["fold_idx"],
                "capital": d["capital"],
                **m,
            })
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["strategy", "params_hash", "params", "capital"]).agg(
        sharpe_mean=("sharpe", "mean"),
        sharpe_std=("sharpe", "std"),
        sharpe_min=("sharpe", "min"),
        sharpe_max=("sharpe", "max"),
        cagr_mean=("cagr", "mean"),
        max_dd_mean=("max_drawdown", "mean"),
        cost_bps_mean=("avg_cost_bps", "mean"),
        turnover_mean=("turnover", "mean"),
        n_folds=("sharpe", "count"),
    ).reset_index()
    return g.sort_values("sharpe_mean", ascending=False).reset_index(drop=True)


def main():
    df = load_log()
    if df.empty:
        print("no log rows")
        return
    print(f"loaded {len(df)} (variant, fold) results across {df['strategy'].nunique()} strategies")
    print(f"unique variants: {df['params_hash'].nunique()}")
    print(f"unique folds:    {df['fold_idx'].nunique()}")

    summary = summarise(df)
    n_trials = summary["params_hash"].nunique()

    # Apply Deflated Sharpe Ratio per variant using pooled validate-fold returns
    # Approximate: use the mean+std across folds and assume folds are
    # independent-ish for the DSR.
    print(f"\nn_trials for DSR penalty: {n_trials}")

    # For DSR we need returns; approximate with fold-Sharpe distribution
    # (less precise than full daily-return-based DSR but informative)
    summary["t_stat"] = summary["sharpe_mean"] / (summary["sharpe_std"] / np.sqrt(summary["n_folds"]).replace(0, 1))

    # Better: compute DSR using a synthetic 252-day return series matching the
    # variant's annualised mean+vol. Conservative.
    from math import sqrt
    dsr_vals = []
    for _, row in summary.iterrows():
        # Generate a Gaussian daily-return series matching the variant's
        # annualised performance for DSR computation.
        ann_ret = row["cagr_mean"]
        ann_vol = max(row["sharpe_std"] * sqrt(252), 1e-6)
        if not np.isfinite(ann_ret) or not np.isfinite(ann_vol):
            dsr_vals.append(None); continue
        # daily params
        daily_mean = ann_ret / 252
        daily_vol = ann_vol / sqrt(252)
        # Use one fold-year of pseudo-daily returns for the deflation
        rng = np.random.default_rng(42)
        synth = pd.Series(rng.normal(daily_mean, daily_vol, 252))
        dsr_vals.append(deflated_sharpe_ratio(row["sharpe_mean"], n_trials, synth))
    summary["dsr"] = dsr_vals

    print()
    print(f"{'='*72}")
    print(f"  TOP 10 by walk-forward mean Sharpe")
    print(f"{'='*72}")
    cols = ["strategy", "params", "sharpe_mean", "sharpe_std", "cagr_mean",
            "max_dd_mean", "cost_bps_mean", "turnover_mean", "dsr"]
    pd.set_option("display.max_colwidth", 200)
    pd.set_option("display.width", 300)
    print(summary.head(10)[cols].to_string(index=False))

    print()
    print(f"{'='*72}")
    print(f"  BOTTOM 5 (sanity check the bad strategies really are bad)")
    print(f"{'='*72}")
    print(summary.tail(5)[cols].to_string(index=False))

    out_csv = RESULTS_DIR / "search_summary.csv"
    summary.to_csv(out_csv, index=False)
    print(f"\nwrote {out_csv}")


if __name__ == "__main__":
    main()
