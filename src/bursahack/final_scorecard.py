"""Top-K final scorecard runner + report.

After the expanded search completes, this:
  1. Loads the full search log
  2. Identifies the top-K variants by walk-forward mean Sharpe
  3. For each, runs the full 12-gate scorecard from overfitting_diagnostics
  4. Writes results/FINAL_REPORT.md with per-variant scorecards + a comparison table

Parameter-neighbourhood stability (Gate 4) is the most expensive gate;
we run it only on top-K_full variants for cost reasons.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pandas as pd

from bursahack.data_loader import load_panel
from bursahack.engine import run_backtest
from bursahack.overfitting_diagnostics import (
    fmt_scorecard,
    load_fold_sharpe_matrix,
    score_strategy,
)
from bursahack.paths import RESULTS_DIR
from bursahack.signals.breakout import DonchianBreakout
from bursahack.signals.clenow_som import ClenowSOM
from bursahack.signals.momentum import Momentum
from bursahack.signals.reversal import Reversal
from bursahack.signals.rotation import DualSlopeRotation
from bursahack.signals.timeseries_momentum import TimeSeriesMomentum
from bursahack.walkforward import HOLDOUT_END, HOLDOUT_START


STRATEGY_FACTORY = {
    "rotation":   DualSlopeRotation,
    "momentum":   Momentum,
    "reversal":   Reversal,
    "clenow_som": ClenowSOM,
    "tsmom":      TimeSeriesMomentum,
    "breakout":   DonchianBreakout,
}


def _hash_params(params):
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:16]


def aggregate_search_log(log_path: Path) -> pd.DataFrame:
    """One row per (strategy, params_hash): mean+std fold Sharpe across folds."""
    rows = []
    with open(log_path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            m = d.get("metrics")
            if not m or m.get("sharpe") is None:
                continue
            rows.append({
                "strategy": d["name"],
                "params_hash": d["params_hash"],
                "params": json.dumps(d["params"], sort_keys=True),
                "param_dict": d["params"],
                "fold_idx": d["fold_idx"],
                "sharpe": m["sharpe"],
                "cagr": m["cagr"],
                "max_dd": m["max_drawdown"],
                "cost_bps": m["avg_cost_bps"],
                "turnover": m["turnover"],
            })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    g = df.groupby(["strategy", "params_hash", "params"]).agg(
        sharpe_mean=("sharpe", "mean"),
        sharpe_std=("sharpe", "std"),
        sharpe_min=("sharpe", "min"),
        cagr_mean=("cagr", "mean"),
        max_dd_mean=("max_dd", "mean"),
        cost_bps_mean=("cost_bps", "mean"),
        turnover_mean=("turnover", "mean"),
        n_folds=("sharpe", "count"),
    ).reset_index()
    # Pull a sample param_dict back in
    pdict = df.drop_duplicates("params_hash").set_index("params_hash")["param_dict"]
    g["param_dict"] = g["params_hash"].map(pdict)
    return g.sort_values("sharpe_mean", ascending=False).reset_index(drop=True)


def run_holdout_for_variant(strategy_cls, params: dict, panel_oos, capital=350_000.0):
    strat = strategy_cls(name=strategy_cls.__name__, params=dict(params))
    rebal = strat.rebal_dates(panel_oos)
    rebal = [d for d in rebal if d >= HOLDOUT_START]
    led = run_backtest(panel_oos, strat.signal_fn(), rebal, starting_cash=capital)
    eq = led.equity.loc[HOLDOUT_START:HOLDOUT_END]
    tr = led.trades[led.trades["date"] >= HOLDOUT_START] if not led.trades.empty else led.trades
    return eq, tr


def main(top_k_full: int = 5, top_k_quick: int = 10):
    print("[final] loading search log...")
    summary = aggregate_search_log(RESULTS_DIR / "search_log.jsonl")
    if summary.empty:
        print("no rows in log")
        return
    print(f"  {len(summary)} variants in log")
    print(f"  by strategy: {summary['strategy'].value_counts().to_dict()}")

    print("[final] loading fold-sharpe matrix...")
    fold_mat = load_fold_sharpe_matrix()

    print("[final] loading panels...")
    panel_oos, _ = load_panel(2018, 2022)
    panel_full, _ = load_panel(2007, 2022)

    print(f"\n[final] running scorecards on top-{top_k_quick}")
    print("        (full param-stability check only on top-{top_k_full})")

    rows = []
    scorecards = []
    for i, row in summary.head(top_k_quick).iterrows():
        strategy = row["strategy"]
        cls = STRATEGY_FACTORY.get(strategy)
        if cls is None:
            print(f"  [skip] unknown strategy {strategy}")
            continue
        params = row["param_dict"]
        ph = row["params_hash"]
        print(f"\n  [{i+1}/{top_k_quick}] {strategy:12s}  sharpe_mean={row['sharpe_mean']:.3f}  params_hash={ph}")
        t0 = time.time()
        try:
            eq, tr = run_holdout_for_variant(cls, params, panel_oos)
        except Exception as e:
            print(f"    [fail] holdout run: {e}")
            continue
        do_param_stab = i < top_k_full
        try:
            sc = score_strategy(
                strategy_name=f"{strategy}_rank_{i+1}",
                params=params,
                strategy_factory=lambda p: cls(params=dict(p)),
                panel=panel_full,
                per_variant_fold_sharpe=fold_mat,
                this_variant_id=ph,
                holdout_equity=eq,
                holdout_trades=tr,
                capital=350_000.0,
                n_subsamples_pbo=500,
                run_param_neighbourhood=do_param_stab,
            )
        except Exception as e:
            print(f"    [fail] scorecard: {e}")
            continue
        scorecards.append(sc)
        rows.append({
            "rank": i + 1,
            "strategy": strategy,
            "params_hash": ph,
            "wf_sharpe": row["sharpe_mean"],
            "wf_sharpe_std": row["sharpe_std"],
            "tier": sc.tier,
            "rec": sc.recommendation,
            "pbo": sc.gates["pbo"].value,
            "dsr_eff": sc.gates["dsr_effective_n"].value,
            "oos_sharpe": sc.gates["oos_sharpe"].value,
            "param_stab": sc.gates.get("param_stability").value if sc.gates.get("param_stability") else None,
            "cov": sc.gates["fold_cov"].value,
            "slip_drag": sc.gates["slip_drag"].value,
            "order_mult": sc.gates["order_size"].value,
            "cagr_oos": sc.gates["cagr_vs_hurdle"].value,
            "max_dd": sc.gates["max_dd"].value,
            "monthly_hit": sc.gates["monthly_hit"].value,
            "n_pass": sc.n_passed(),
            "n_eval": sc.n_evaluated(),
        })
        print(f"    tier={sc.tier:1s}  rec={sc.recommendation}  passed={sc.n_passed()}/{sc.n_evaluated()}  took {time.time() - t0:.1f}s")

    if rows:
        out_df = pd.DataFrame(rows)
        out_csv = RESULTS_DIR / "final_scorecards.csv"
        out_df.to_csv(out_csv, index=False)
        print(f"\n[final] wrote {out_csv}")

        out_md = RESULTS_DIR / "FINAL_REPORT.md"
        with open(out_md, "w", encoding="utf-8") as f:
            f.write("# Final Strategy Scorecards\n\n")
            f.write("Top-K survivors of the 186-variant expanded brute-force search, evaluated against the 12-gate DEPLOYMENT_FRAMEWORK.md (v1.0 Advisory).\n\n")
            f.write("## Summary Table\n\n")
            f.write("| Rank | Strategy | Tier | Recommendation | WF Sharpe | OOS Sharpe | PBO | DSR-eff | CAGR(OOS) | Max DD | Pass/Eval |\n")
            f.write("|---:|---|:---:|---|---:|---:|---:|---:|---:|---:|---:|\n")
            for r in rows:
                f.write(
                    f"| {r['rank']} | {r['strategy']} | **{r['tier']}** | {r['rec']} | "
                    f"{r['wf_sharpe']:.2f} | {r['oos_sharpe']:.2f} | {r['pbo']:.2f} | {r['dsr_eff']:.2f} | "
                    f"{(r['cagr_oos'] or 0) * 100:+.2f}% | {(r['max_dd'] or 0) * 100:.1f}% | "
                    f"{r['n_pass']}/{r['n_eval']} |\n"
                )
            f.write("\n---\n\n## Per-Variant Scorecards\n\n")
            for sc in scorecards:
                f.write(fmt_scorecard(sc))
                f.write("\n\n---\n\n")
        print(f"[final] wrote {out_md}")


if __name__ == "__main__":
    main(top_k_full=5, top_k_quick=10)
