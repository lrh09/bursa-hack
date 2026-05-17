"""First end-to-end momentum run on the dev set."""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from bursahack.data_loader import load_panel
from bursahack.engine import run_backtest
from bursahack.metrics import compute_metrics, fmt_metrics
from bursahack.paths import RESULTS_DIR
from bursahack.signals.momentum import Momentum


def main():
    print("[run_momentum] loading 2007-2019 dev-set price data...")
    t0 = time.time()
    panel, master = load_panel(2007, 2019)
    print(f"  loaded {len(panel.dates):,} dates x {panel.adj_close.shape[1]:,} securities in {time.time() - t0:.1f}s")

    strat = Momentum(name="momentum_12_1_top20", params={
        "lookback": 252,
        "skip": 21,
        "top_n": 20,
        "rebal_freq": "M",
        "adv_floor": 500_000.0,
        "price_floor": 0.20,
        "ascending": False,
    })

    rebal = strat.rebal_dates(panel)
    # Drop early rebals where there isn't enough history for the signal
    min_pos = 252 + 21 + 1
    rebal = [d for d in rebal if panel.dates.get_loc(d) >= min_pos]
    print(f"  {len(rebal)} rebalances ({rebal[0].date()} -> {rebal[-1].date()})")

    t1 = time.time()
    print(f"[run_momentum] running RM 350,000 base...")
    ledger = run_backtest(panel, strat.signal_fn(), rebal, starting_cash=350_000.0)
    print(f"  done in {time.time() - t1:.1f}s")

    m = compute_metrics(ledger.equity, ledger.trades, n_trials=1)
    print()
    print(f"=== momentum_12_1_top20 (RM 350k) — dev set 2007-2019 ===")
    print(fmt_metrics(m))

    RESULTS_DIR.mkdir(exist_ok=True)
    out = {
        "strategy": strat.name,
        "params": strat.params,
        "capital": 350_000.0,
        "window": "2007-2019",
        "final_equity": float(ledger.equity.iloc[-1]),
        "metrics": {k: v for k, v in asdict(m).items()},
    }
    (RESULTS_DIR / "first_momentum_run.json").write_text(json.dumps(out, indent=2, default=str))
    ledger.equity.to_csv(RESULTS_DIR / "first_momentum_equity.csv", header=["equity"])
    ledger.trades.to_csv(RESULTS_DIR / "first_momentum_trades.csv", index=False)
    print(f"\nWrote {RESULTS_DIR}/first_momentum_*.csv/.json")


if __name__ == "__main__":
    main()
