"""Regenerate Clenow #9 (rank-9 of final search) equity + trades artifacts.

Uses the exact params from results/final_scorecards.csv row rank=9. Iron rule:
do NOT re-tune the holdout; we only re-run the same params to materialise the
equity curve for the web portal.

Outputs:
  results/clenow_winner_equity.csv          (full 2007-2022, RM 350k)
  results/clenow_winner_equity_100k.csv
  results/clenow_winner_equity_350k.csv
  results/clenow_winner_equity_1M.csv
  results/clenow_winner_trades.csv

Runs each capital sequentially. Total wall time ~3-10 minutes on a modern box.
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
from bursahack.signals.clenow_som import ClenowSOM


# Exact params for clenow_som rank-9 (from results/final_scorecards.csv).
CLENOW_9_PARAMS = {
    "adv_floor": 500_000.0,
    "ascending": False,
    "atr_window": 20,
    "lookback": 60,
    "max_gap": 0.15,
    "price_floor": 0.2,
    "rebal_freq": "M",
    "regime_ma": 200,
    "top_n": 30,
    "trend_ma": 100,
    "use_regime": True,
}

CAPITALS = [100_000.0, 350_000.0, 1_000_000.0]


def _save_equity(equity: pd.Series, path: Path) -> None:
    df = pd.DataFrame({"equity": equity})
    df.index.name = ""
    df.to_csv(path)
    print(f"  wrote {path.relative_to(REPO)}  ({len(df)} rows, end={equity.iloc[-1]:,.0f})")


def main() -> None:
    print("[clenow] loading 2006-2022 panel (need warmup for trend_ma=200)...")
    t0 = time.time()
    panel, _ = load_panel(2006, 2022)
    print(f"  panel: {len(panel.dates)} dates, {panel.adj_close.shape[1]} securities  ({time.time()-t0:.1f}s)")

    strat = ClenowSOM(name="clenow_rank_9", params=dict(CLENOW_9_PARAMS))
    rebal = strat.rebal_dates(panel)
    print(f"  {len(rebal)} rebal dates in full window")

    ledgers: dict[float, object] = {}
    for cap in CAPITALS:
        print(f"[clenow] backtest @ RM {cap:,.0f} ...")
        t1 = time.time()
        led = run_backtest(panel, strat.signal_fn(), rebal, starting_cash=cap)
        ledgers[cap] = led
        print(f"  done in {time.time()-t1:.1f}s ; final equity={led.equity.iloc[-1]:,.0f}")

    # Emit the standard "winner" equity (default = RM 350k) for parity with rotation.
    led_350 = ledgers[350_000.0]
    _save_equity(led_350.equity, RESULTS_DIR / "clenow_winner_equity.csv")
    led_350.trades.to_csv(RESULTS_DIR / "clenow_winner_trades.csv", index=False)
    print(f"  wrote results/clenow_winner_trades.csv  ({len(led_350.trades)} trades)")

    # Per-capital equity curves for the capital toggle on the FE.
    for cap, led in ledgers.items():
        tag = {100_000.0: "100k", 350_000.0: "350k", 1_000_000.0: "1M"}[cap]
        _save_equity(led.equity, RESULTS_DIR / f"clenow_winner_equity_{tag}.csv")

    print(f"[clenow] total wall time {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
