"""Expanded brute-force search: add TSMOM + Donchian to the existing log.

Resume-safe via search_log.jsonl - already-logged variants are skipped.
"""
from __future__ import annotations

import pandas as pd

from bursahack.data_loader import load_panel
from bursahack.paths import RESULTS_DIR
from bursahack.search import grid, run_grid
from bursahack.signals.breakout import DonchianBreakout
from bursahack.signals.timeseries_momentum import TimeSeriesMomentum
from bursahack.walkforward import walk_forward_folds


TSMOM_GRID = grid({
    "lookback": [63, 126, 252],
    "trend_ma": [50, 100, 200],
    "top_n": [20, 30],
    "rebal_freq": ["M", "Q"],
    "adv_floor": [500_000.0],
    "price_floor": [0.20],
    "ascending": [False],
})  # 3 x 3 x 2 x 2 = 36 variants

DONCHIAN_GRID = grid({
    "lookback": [126, 252],
    "breakout_thresh": [0.90, 0.95, 1.00],
    "trend_ma": [50, 100],
    "top_n": [20, 30],
    "rebal_freq": ["M", "Q"],
    "adv_floor": [500_000.0],
    "price_floor": [0.20],
    "ascending": [False],
})  # 2 x 3 x 2 x 2 x 2 = 48 variants


def main():
    print("[expanded_search] loading dev-set panel 2007-2019...")
    panel, master = load_panel(2007, 2019)
    folds = walk_forward_folds()
    print(f"  {len(panel.dates):,} dates x {panel.adj_close.shape[1]:,} secs; {len(folds)} folds")
    print(f"  TSMOM grid:    {len(TSMOM_GRID)} variants")
    print(f"  Donchian grid: {len(DONCHIAN_GRID)} variants")
    print(f"  Total new:     {len(TSMOM_GRID) + len(DONCHIAN_GRID)} variants x 16 folds")

    log_path = RESULTS_DIR / "search_log.jsonl"

    run_grid(
        panel=panel,
        strategy_factory=lambda p: TimeSeriesMomentum(params=dict(p)),
        param_grid=TSMOM_GRID,
        capitals=[350_000.0],
        log_path=log_path,
        folds=folds,
        name="tsmom",
    )
    run_grid(
        panel=panel,
        strategy_factory=lambda p: DonchianBreakout(params=dict(p)),
        param_grid=DONCHIAN_GRID,
        capitals=[350_000.0],
        log_path=log_path,
        folds=folds,
        name="breakout",
    )
    print("\n[expanded_search] complete")


if __name__ == "__main__":
    main()
