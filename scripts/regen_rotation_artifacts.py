"""Regenerate rotation winner (rank-1) equity at 3 capitals for the portal toggle.

Mirrors scripts/regen_clenow_artifacts.py shape. Iron rule: do NOT re-tune the
holdout; we only re-run the same params (the rotation rank-1 hash from
results/final_scorecards.csv) at different capital levels.

Outputs:
  results/rotation_winner_equity_100k.csv
  results/rotation_winner_equity_350k.csv  (default, also kept as rotation_winner_equity.csv)
  results/rotation_winner_equity_1M.csv
  results/rotation_winner_trades.csv       (RM350k trade log)

Total wall time ~3-10 minutes on a modern box.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd

from bursahack.data_loader import load_panel
from bursahack.engine import run_backtest
from bursahack.paths import RESULTS_DIR
from bursahack.signals.rotation import DualSlopeRotation


ROTATION_1_PARAMS = {
    "adv_floor": 500_000.0,
    "ascending": False,
    "max_period_vol": 0.4,
    "min_period_vol": 0.01,
    "min_slope": 20.0,
    "price_floor": 0.2,
    "rebal_freq": "M",
    "slope_lookback_long": 90,
    "slope_lookback_short": 30,
    "top_n": 20,
    "vol_period": 90,
    "weight_cap": 0.1,
}

CAPITALS = [100_000.0, 350_000.0, 1_000_000.0]


def _save_equity(equity: pd.Series, path: Path) -> None:
    df = pd.DataFrame({"equity": equity})
    df.index.name = ""
    df.to_csv(path)
    print(f"  wrote {path.relative_to(REPO)}  ({len(df)} rows, end={equity.iloc[-1]:,.0f})")


def main() -> None:
    print("[rotation] loading 2006-2022 panel (warmup for slope_lookback_long=90)...")
    t0 = time.time()
    panel, _ = load_panel(2006, 2022)
    print(f"  panel: {len(panel.dates)} dates, {panel.adj_close.shape[1]} securities  ({time.time() - t0:.1f}s)")

    strat = DualSlopeRotation(name="rotation_rank_1", params=dict(ROTATION_1_PARAMS))
    rebal = strat.rebal_dates(panel)
    print(f"  {len(rebal)} rebal dates in full window")

    ledgers: dict[float, object] = {}
    for cap in CAPITALS:
        print(f"[rotation] backtest @ RM {cap:,.0f} ...")
        t1 = time.time()
        led = run_backtest(panel, strat.signal_fn(), rebal, starting_cash=cap)
        ledgers[cap] = led
        print(f"  done in {time.time() - t1:.1f}s ; final equity={led.equity.iloc[-1]:,.0f}")

    led_350 = ledgers[350_000.0]
    _save_equity(led_350.equity, RESULTS_DIR / "rotation_winner_equity.csv")
    led_350.trades.to_csv(RESULTS_DIR / "rotation_winner_trades.csv", index=False)
    print(f"  wrote results/rotation_winner_trades.csv  ({len(led_350.trades)} trades)")

    for cap, led in ledgers.items():
        tag = {100_000.0: "100k", 350_000.0: "350k", 1_000_000.0: "1M"}[cap]
        _save_equity(led.equity, RESULTS_DIR / f"rotation_winner_equity_{tag}.csv")

    print(f"[rotation] total wall time {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
