"""Holdout verdict — touch ONCE.

Runs the chosen winner from the walk-forward search against the held-out
2020-01-01 -> 2022-02-15 window. Whatever this number is, it is the answer.
Do not re-run with tweaked params, do not re-tune to improve. If the result
is unsatisfactory the correct response is to design a NEW search with a
NEW holdout window, not to re-touch this one.

Supports any of the four strategies. Pass `--strategy rotation` etc.
Winner params for each strategy are filled in from `search_summary.csv`
once the search is complete.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from bursahack.data_loader import load_panel
from bursahack.engine import run_backtest
from bursahack.metrics import compute_metrics, fmt_metrics
from bursahack.paths import RESULTS_DIR
from bursahack.signals.clenow_som import ClenowSOM
from bursahack.signals.momentum import Momentum
from bursahack.signals.reversal import Reversal
from bursahack.signals.rotation import DualSlopeRotation
from bursahack.walkforward import HOLDOUT_START, HOLDOUT_END


STRATEGIES = {
    "momentum": (Momentum, {
        "lookback": 126, "skip": 0, "top_n": 30, "rebal_freq": "M",
        "adv_floor": 500_000.0, "price_floor": 0.20, "ascending": False,
    }),
    "clenow_som": (ClenowSOM, None),     # filled in post-search
    "reversal":   (Reversal, None),
    "rotation":   (DualSlopeRotation, {
        # Winner from the 102-variant walk-forward search:
        # walk-forward mean Sharpe 1.19, CAGR +25.9%, max DD -16.5%, DSR 0.85.
        "slope_lookback_short": 30, "slope_lookback_long": 90,
        "vol_period": 90, "min_period_vol": 0.01, "max_period_vol": 0.40,
        "min_slope": 20.0, "top_n": 20, "weight_cap": 0.10,
        "rebal_freq": "M", "adv_floor": 500_000.0, "price_floor": 0.20,
        "ascending": False,
    }),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="rotation", choices=STRATEGIES.keys())
    ap.add_argument("--n-trials", type=int, default=112,
                    help="N variants in the search (used for Deflated Sharpe penalty)")
    args = ap.parse_args()

    cls, default_params = STRATEGIES[args.strategy]
    if default_params is None:
        raise SystemExit(f"no winner params frozen for strategy={args.strategy}")

    print(f"[holdout] strategy={args.strategy}  params={default_params}")
    print(f"[holdout] loading 2018-2022 (need lookback warmup)...")
    panel, master = load_panel(2018, 2022)
    print(f"  {len(panel.dates)} dates, {panel.adj_close.shape[1]} securities")
    print(f"  HOLDOUT window: {HOLDOUT_START.date()} -> {HOLDOUT_END.date()}")

    strat = cls(name=f"{args.strategy}_holdout_winner", params=dict(default_params))
    rebal = strat.rebal_dates(panel)
    rebal = [d for d in rebal if d >= HOLDOUT_START]
    print(f"  {len(rebal)} rebal dates inside holdout")

    results = {}
    for cap in [100_000.0, 350_000.0, 1_000_000.0]:
        led = run_backtest(panel, strat.signal_fn(), rebal, starting_cash=cap)
        eq_hold = led.equity.loc[HOLDOUT_START:HOLDOUT_END]
        trades_hold = led.trades[led.trades["date"] >= HOLDOUT_START] if not led.trades.empty else led.trades
        m = compute_metrics(eq_hold, trades_hold, n_trials=args.n_trials)
        print()
        print(f"=== HOLDOUT  strategy={args.strategy}  capital=RM {cap:,.0f} ===")
        print(f"  start equity:  RM {eq_hold.iloc[0]:>14,.2f}")
        print(f"  final equity:  RM {eq_hold.iloc[-1]:>14,.2f}")
        print(fmt_metrics(m))
        results[cap] = {
            "metrics": m.__dict__,
            "start_equity": float(eq_hold.iloc[0]),
            "final_equity": float(eq_hold.iloc[-1]),
        }

    out = RESULTS_DIR / f"holdout_verdict_{args.strategy}.json"
    out.write_text(json.dumps({
        "strategy": args.strategy,
        "winner_params": default_params,
        "n_trials_penalty": args.n_trials,
        "holdout_window": [str(HOLDOUT_START.date()), str(HOLDOUT_END.date())],
        "results_by_capital": {str(k): v for k, v in results.items()},
    }, indent=2, default=str))
    print(f"\nVERDICT saved to {out}")


if __name__ == "__main__":
    main()
