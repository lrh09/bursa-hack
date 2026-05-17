"""Run JUST the rotation grid against the walk-forward folds.

Skips momentum/reversal/clenow tail by going direct to rotation. Resume-safe
via the shared search_log.jsonl.
"""
from __future__ import annotations

import pandas as pd

from bursahack.data_loader import load_panel
from bursahack.paths import RESULTS_DIR
from bursahack.search import grid, run_grid
from bursahack.signals.rotation import DualSlopeRotation
from bursahack.walkforward import walk_forward_folds


# Focused on the axis RH cares about (rebal_freq) + a few other dimensions.
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
    print("[run_rotation_grid] loading dev-set panel (2007 -> 2019)...")
    panel, master = load_panel(2007, 2019)
    print(f"  {len(panel.dates):,} dates x {panel.adj_close.shape[1]:,} secs")
    folds = walk_forward_folds()
    print(f"  {len(folds)} folds, last validate ends {folds[-1].validate_end.date()}")
    print(f"  rotation grid: {len(ROTATION_GRID)} variants x {len(folds)} folds = {len(ROTATION_GRID) * len(folds)} backtests")

    log_path = RESULTS_DIR / "search_log.jsonl"
    summary = run_grid(
        panel=panel,
        strategy_factory=lambda p: DualSlopeRotation(params=dict(p)),
        param_grid=ROTATION_GRID,
        capitals=[350_000.0],
        log_path=log_path,
        folds=folds,
        name="rotation",
    )
    out = RESULTS_DIR / "rotation_summary.csv"
    summary.to_csv(out, index=False)
    print(f"\nWrote {out}")
    print("\nTop 10 by walk-forward mean Sharpe:")
    print(summary.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
