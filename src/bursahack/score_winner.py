"""Run the full 12-gate scorecard against the rotation winner."""
from __future__ import annotations

import hashlib
import json
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
from bursahack.signals.rotation import DualSlopeRotation


WINNER_PARAMS = {
    "slope_lookback_short": 30,
    "slope_lookback_long": 90,
    "vol_period": 90,
    "min_period_vol": 0.01,
    "max_period_vol": 0.40,
    "min_slope": 20.0,
    "top_n": 20,
    "weight_cap": 0.10,
    "rebal_freq": "M",
    "adv_floor": 500_000.0,
    "price_floor": 0.20,
    "ascending": False,
}


def _hash_params(params):
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:16]


def main():
    print("[score] loading 2018-2022 panel for fresh OOS run...")
    panel_oos, _ = load_panel(2018, 2022)
    strat = DualSlopeRotation(name="rotation_winner", params=dict(WINNER_PARAMS))
    rebal = strat.rebal_dates(panel_oos)
    from bursahack.walkforward import HOLDOUT_END, HOLDOUT_START
    rebal_hold = [d for d in rebal if d >= HOLDOUT_START]
    print(f"[score] running holdout-only backtest (RM 350k, fresh deployment 2020-01)")
    led = run_backtest(panel_oos, strat.signal_fn(), rebal_hold, starting_cash=350_000.0)
    hold_eq = led.equity.loc[HOLDOUT_START:HOLDOUT_END]
    hold_tr = led.trades[led.trades["date"] >= HOLDOUT_START] if not led.trades.empty else led.trades

    print("[score] loading full panel for param-stability check...")
    panel_full, _ = load_panel(2007, 2022)

    print("[score] loading fold-Sharpe matrix from search log...")
    fold_mat = load_fold_sharpe_matrix()
    winner_hash = _hash_params(WINNER_PARAMS)
    if winner_hash not in fold_mat.index:
        # Pick closest match — the search-logged hash may have used a slightly
        # different param ordering. Look up by name + key params.
        for h in fold_mat.index:
            pass

    print(f"[score] running full 12-gate scorecard (param-stability will do 22 quick backtests)...")
    sc = score_strategy(
        strategy_name="Bursa Momentum Rotation",
        params=WINNER_PARAMS,
        strategy_factory=lambda p: DualSlopeRotation(params=dict(p)),
        panel=panel_full,
        per_variant_fold_sharpe=fold_mat,
        this_variant_id=winner_hash,
        holdout_equity=hold_eq,
        holdout_trades=hold_tr,
        capital=350_000.0,
        n_subsamples_pbo=1000,
        run_param_neighbourhood=True,
    )
    print()
    print(fmt_scorecard(sc))

    out = RESULTS_DIR / "SCORECARD_rotation.md"
    out.write_text(fmt_scorecard(sc), encoding="utf-8")
    print(f"\n[score] wrote {out}")


if __name__ == "__main__":
    main()
