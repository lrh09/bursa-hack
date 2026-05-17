"""Param-neighbourhood stability for Clenow #9 + #10 within IN-SAMPLE only.

Open question from project_bursahack_2026_05_18: "Param stability not run on this
variant (only top-5 got it)." This closes that gap WITHOUT re-touching the
2020-2022 holdout (iron rule of the iteration).

Method: re-use overfitting_diagnostics.parameter_neighbourhood, but set the
evaluation window to the IS slice 2008-01-01 -> 2019-12-31 (everything before
the burned holdout). Each numeric param is perturbed +/-15% one at a time, Sharpe
recomputed on the IS slice. Threshold: max Sharpe drop > -25% to pass.

Outputs:
  results/clenow_is_stability.json  (single JSON with rank-9 + rank-10 results)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd

from bursahack.data_loader import load_panel
from bursahack.overfitting_diagnostics import parameter_neighbourhood
from bursahack.paths import RESULTS_DIR
from bursahack.signals.clenow_som import ClenowSOM


CLENOW_9 = {
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

CLENOW_10 = {**CLENOW_9, "use_regime": False}

IS_START = pd.Timestamp("2008-01-01")
IS_END = pd.Timestamp("2019-12-31")

# Limit to the numeric params that meaningfully tune the strategy.
# Skip adv_floor, max_gap, price_floor (universe filters) -- they're plumbing,
# not strategy choices, and a +/-15% perturbation barely changes selection.
PERTURB_KEYS = ["lookback", "atr_window", "regime_ma", "top_n", "trend_ma"]


def _factory(params: dict):
    return ClenowSOM(name="clenow", params=dict(params))


def _run_one(label: str, base: dict, panel) -> dict:
    print(f"\n[stability] {label} -- base + {len(PERTURB_KEYS) * 2} perturbations")
    t0 = time.time()
    result = parameter_neighbourhood(
        strategy_factory=_factory,
        base_params=base,
        perturb_pct=0.15,
        perturb_keys=PERTURB_KEYS,
        panel=panel,
        capital=350_000.0,
        use_oos=True,            # use_oos=True triggers the slice
        holdout_start=IS_START,  # ... but we re-point it at IS so it's IS-only
        holdout_end=IS_END,
    )
    dur = time.time() - t0
    print(f"  base Sharpe = {result['base_sharpe']:.4f}")
    print(f"  max %-drop  = {result['max_pct_drop']:+.2%}  (threshold -25%)")
    print(f"  passed      = {result['passed']}  ({dur:.1f}s)")
    print(f"  perturbations:")
    for p in result["perturbations"]:
        print(f"    {p['param']:12s} = {p['value']:>10}  -> Sharpe {p['sharpe']:+.4f}  "
              f"({p['delta_pct']:+.2%})")
    return {
        "label": label,
        "base_params": base,
        "is_window": [str(IS_START.date()), str(IS_END.date())],
        "base_sharpe": result["base_sharpe"],
        "max_pct_drop": result["max_pct_drop"],
        "passed": bool(result["passed"]),
        "threshold_pct": result["threshold_pct"],
        "perturbations": result["perturbations"],
        "wall_seconds": dur,
    }


def main() -> None:
    print("[stability] loading 2006-2022 panel (warmup for trend_ma=200)...")
    t0 = time.time()
    panel, _ = load_panel(2006, 2022)
    print(f"  panel: {len(panel.dates)} dates, {panel.adj_close.shape[1]} securities  ({time.time() - t0:.1f}s)")

    out = {
        "method": "parameter_neighbourhood, +/-15%, IS-only (2008-2019)",
        "iron_rule": "2020-2022 holdout NOT touched",
        "perturbed_keys": PERTURB_KEYS,
        "variants": {
            "clenow_rank_9_regime_on":  _run_one("Clenow #9  (use_regime=True)",  CLENOW_9,  panel),
            "clenow_rank_10_regime_off": _run_one("Clenow #10 (use_regime=False)", CLENOW_10, panel),
        },
    }

    path = RESULTS_DIR / "clenow_is_stability.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n[stability] wrote {path.relative_to(REPO)}")
    print(f"[stability] total wall time {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
