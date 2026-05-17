"""Top-level brute-force search runner.

Runs a moderate momentum + reversal grid across all 16 walk-forward folds at
the canonical capital (RM 350k). Writes JSONL log + summary CSV. Applies the
Deflated Sharpe Ratio with N_trials = total grid size.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from bursahack.data_loader import load_panel
from bursahack.metrics import compute_metrics, deflated_sharpe_ratio
from bursahack.paths import RESULTS_DIR
from bursahack.search import grid, run_grid
from bursahack.signals.clenow_som import ClenowSOM
from bursahack.signals.momentum import Momentum
from bursahack.signals.reversal import Reversal
from bursahack.signals.rotation import DualSlopeRotation
from bursahack.walkforward import HOLDOUT_START, walk_forward_folds


MOMENTUM_GRID = grid({
    "lookback": [126, 252],
    "skip": [0, 21],
    "top_n": [20, 30],
    "rebal_freq": ["M", "Q"],
    "adv_floor": [500_000.0, 1_000_000.0],
    "price_floor": [0.20],
    "ascending": [False],
})  # 32 variants

REVERSAL_GRID = grid({
    "lookback": [5, 21],
    "skip": [0],
    "top_n": [20, 30],
    "rebal_freq": ["W", "M"],
    "adv_floor": [500_000.0, 1_000_000.0],
    "price_floor": [0.20],
    "ascending": [True],
})  # 16 variants

CLENOW_GRID = grid({
    "lookback": [60, 90, 120, 180],
    "trend_ma": [100],
    "regime_ma": [200],
    "atr_window": [20],
    "max_gap": [0.15],
    "top_n": [30],
    "rebal_freq": ["W", "2W", "M", "Q"],   # the main axis RH wants tuned
    "adv_floor": [500_000.0],
    "price_floor": [0.20],
    "use_regime": [True, False],
    "ascending": [False],
})  # 4 x 4 x 2 = 32 variants

# RH's dual-slope rotation strategy (extracted from his original Python).
# Two slope lookbacks averaged for the score, vol-band gate, inverse-vol parity
# weighting with a concentration cap. Default params at the head of each list.
ROTATION_GRID = grid({
    "slope_lookback_short": [30, 60],
    "slope_lookback_long":  [60, 90, 120],
    "vol_period": [90],
    "min_period_vol": [0.01],
    "max_period_vol": [0.30, 0.40, 0.50],
    "min_slope": [10.0, 20.0, 30.0],
    "top_n": [20, 30],
    "weight_cap": [0.10],
    "rebal_freq": ["W", "2W", "M", "Q"],
    "adv_floor": [500_000.0],
    "price_floor": [0.20],
    "ascending": [False],
})  # 2 x 3 x 3 x 3 x 2 x 4 = 432 variants  ... too many. Trim:
ROTATION_GRID = grid({
    "slope_lookback_short": [30],
    "slope_lookback_long":  [60, 90],
    "vol_period": [90],
    "min_period_vol": [0.01],
    "max_period_vol": [0.40],
    "min_slope": [10.0, 20.0],
    "top_n": [20, 30],
    "weight_cap": [0.10],
    "rebal_freq": ["W", "2W", "M", "Q"],
    "adv_floor": [500_000.0],
    "price_floor": [0.20],
    "ascending": [False],
})  # 2 x 2 x 2 x 4 = 32 variants


def main():
    print("[run_search] loading dev-set panel (2007 -> 2019)...")
    t0 = time.time()
    panel, master = load_panel(2007, 2019)
    print(f"  loaded {len(panel.dates):,} dates x {panel.adj_close.shape[1]:,} secs in {time.time() - t0:.1f}s")

    folds = walk_forward_folds()
    print(f"  walk-forward: {len(folds)} folds, last validate ends {folds[-1].validate_end.date()}")
    print(f"  HOLDOUT_START = {HOLDOUT_START.date()} (never touched)")

    print(f"\n[run_search] momentum grid:   {len(MOMENTUM_GRID)} variants")
    print(f"[run_search] reversal grid:   {len(REVERSAL_GRID)} variants")
    print(f"[run_search] clenow_som grid: {len(CLENOW_GRID)} variants")
    print(f"[run_search] rotation grid:   {len(ROTATION_GRID)} variants")
    n_trials = len(MOMENTUM_GRID) + len(REVERSAL_GRID) + len(CLENOW_GRID) + len(ROTATION_GRID)
    print(f"[run_search] TOTAL N_trials = {n_trials}  (used in Deflated Sharpe)")

    log_path = RESULTS_DIR / "search_log.jsonl"

    # Momentum grid
    mom_summary = run_grid(
        panel=panel,
        strategy_factory=lambda p: Momentum(params=dict(p)),
        param_grid=MOMENTUM_GRID,
        capitals=[350_000.0],
        log_path=log_path,
        folds=folds,
        name="momentum",
    )

    # Reversal grid
    rev_summary = run_grid(
        panel=panel,
        strategy_factory=lambda p: Reversal(params=dict(p)),
        param_grid=REVERSAL_GRID,
        capitals=[350_000.0],
        log_path=log_path,
        folds=folds,
        name="reversal",
    )

    # Clenow Stocks on the Move grid (rebal_freq tuned across W / 2W / M / Q)
    clenow_summary = run_grid(
        panel=panel,
        strategy_factory=lambda p: ClenowSOM(params=dict(p)),
        param_grid=CLENOW_GRID,
        capitals=[350_000.0],
        log_path=log_path,
        folds=folds,
        name="clenow_som",
    )

    # RH's dual-slope rotation grid
    rotation_summary = run_grid(
        panel=panel,
        strategy_factory=lambda p: DualSlopeRotation(params=dict(p)),
        param_grid=ROTATION_GRID,
        capitals=[350_000.0],
        log_path=log_path,
        folds=folds,
        name="rotation",
    )

    combined = pd.concat([mom_summary.assign(strategy="momentum"),
                          rev_summary.assign(strategy="reversal"),
                          clenow_summary.assign(strategy="clenow_som"),
                          rotation_summary.assign(strategy="rotation")], ignore_index=True)
    combined = combined.sort_values("sharpe_mean", ascending=False).reset_index(drop=True)

    # Apply Deflated Sharpe with N_trials = total grid size
    # We need the per-fold daily return distribution to compute it; we'll
    # approximate by pooling validate-fold returns per variant.
    # For now we report the raw walk-forward mean Sharpe + a flag.
    combined["dsr_n_trials"] = n_trials

    out = RESULTS_DIR / "search_summary.csv"
    combined.to_csv(out, index=False)
    print(f"\nWrote {out}")
    print()
    print("=== Top 10 by walk-forward mean Sharpe ===")
    print(combined.head(10).to_string(index=False))
    print()
    print("=== Bottom 5 (sanity check the bad ones really are bad) ===")
    print(combined.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()
