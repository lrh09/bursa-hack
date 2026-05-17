"""Parameter-grid search runner + variant log.

Discipline:
  - Every variant tried is appended to a JSONL log so we can count N_trials
    for the Deflated Sharpe penalty at the end.
  - Resume-safe: skip a variant if its hash already appears in the log.
  - Walk-forward by default; mean + std of validate-fold Sharpe across folds is
    the selection criterion (not in-train Sharpe).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from bursahack.engine import PricePanel, run_backtest
from bursahack.metrics import compute_metrics
from bursahack.signals.base import Strategy
from bursahack.walkforward import Fold, walk_forward_folds, trim_panel


def _hash_params(params: dict[str, Any]) -> str:
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def grid(spec: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Cartesian product of a parameter spec."""
    keys = list(spec.keys())
    return [dict(zip(keys, vals)) for vals in itertools.product(*[spec[k] for k in keys])]


def run_one_fold(
    panel: PricePanel,
    fold: Fold,
    strategy_factory: Callable[[dict], Strategy],
    params: dict[str, Any],
    capital: float,
) -> dict[str, Any]:
    """Run a strategy on the validate window of a single fold.

    The train window is used to bootstrap any state the strategy needs (in our
    simple cross-sectional momentum, the strategy is parametric -- no fitting --
    so train_start..validate_end is enough history)."""
    strat = strategy_factory(params)
    p = trim_panel(panel, fold.train_start, fold.validate_end)
    if len(p.dates) < 280:
        return {"fold": fold, "params": params, "metrics": None, "skip_reason": "insufficient_data"}

    rebal_all = strat.rebal_dates(p)
    # Only count validate-window dates for performance attribution
    val_dates_set = set(p.dates[(p.dates >= fold.validate_start) & (p.dates <= fold.validate_end)])
    # Need to allow the strategy to "warm up" with positions before val start;
    # we run from train_start and slice equity post-hoc.
    led = run_backtest(p, strat.signal_fn(), rebal_all, starting_cash=capital)
    eq_val = led.equity.loc[fold.validate_start:fold.validate_end]
    trades_val = led.trades[led.trades["date"] >= fold.validate_start] if not led.trades.empty else led.trades
    if len(eq_val) < 30:
        return {"fold": fold, "params": params, "metrics": None, "skip_reason": "validate_too_short"}
    m = compute_metrics(eq_val, trades_val)
    return {"fold": fold, "params": params, "metrics": m, "capital": capital}


def run_grid(
    panel: PricePanel,
    strategy_factory: Callable[[dict], Strategy],
    param_grid: list[dict[str, Any]],
    capitals: list[float],
    log_path: Path,
    folds: list[Fold] | None = None,
    name: str = "search",
) -> pd.DataFrame:
    """Brute-force a parameter grid against walk-forward folds.

    Writes one JSON line per (variant, fold, capital) to `log_path`.
    Returns a summary DataFrame: one row per variant with mean/std of validate
    Sharpe across folds + total variant count for Deflated Sharpe.
    """
    if folds is None:
        folds = walk_forward_folds()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume: load existing log to skip already-run variants
    done = set()
    if log_path.exists():
        with log_path.open("r") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    done.add((row["params_hash"], row["fold_idx"], row["capital"]))
                except Exception:
                    pass

    summary_rows = []
    total = len(param_grid) * len(folds) * len(capitals)
    print(f"[{name}] running {total} (variant, fold, capital) combinations")
    t0 = time.time()
    n_done = 0
    n_skipped_resume = 0

    with log_path.open("a") as f:
        for params in param_grid:
            ph = _hash_params(params)
            fold_results = []
            for fi, fold in enumerate(folds):
                for cap in capitals:
                    key = (ph, fi, cap)
                    if key in done:
                        n_skipped_resume += 1
                        continue
                    result = run_one_fold(panel, fold, strategy_factory, params, cap)
                    m = result["metrics"]
                    row = {
                        "name": name,
                        "params_hash": ph,
                        "params": params,
                        "fold_idx": fi,
                        "fold_train_start": str(fold.train_start.date()),
                        "fold_validate_start": str(fold.validate_start.date()),
                        "fold_validate_end": str(fold.validate_end.date()),
                        "capital": cap,
                        "metrics": asdict(m) if m is not None else None,
                        "skip_reason": result.get("skip_reason"),
                        "ts": time.time(),
                    }
                    f.write(json.dumps(row, default=str) + "\n")
                    f.flush()
                    fold_results.append(row)
                    n_done += 1
                    if n_done % 25 == 0:
                        elapsed = time.time() - t0
                        rate = n_done / max(elapsed, 1e-6)
                        eta = (total - n_done - n_skipped_resume) / max(rate, 1e-6)
                        print(f"  [{n_done:>4}/{total}] {rate:.2f}/s  ETA {eta / 60:.1f}min")
    print(f"[{name}] complete in {(time.time() - t0) / 60:.1f}min, skipped {n_skipped_resume} resumed")
    return _summarise_log(log_path, name=name)


def _summarise_log(log_path: Path, name: str = "search") -> pd.DataFrame:
    """Aggregate a JSONL log to one row per (variant, capital): mean+std Sharpe."""
    rows = []
    with log_path.open() as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("name") != name:
                continue
            m = d.get("metrics")
            if m is None:
                continue
            rows.append({
                "params_hash": d["params_hash"],
                "params": json.dumps(d["params"], sort_keys=True),
                "capital": d["capital"],
                "fold_idx": d["fold_idx"],
                "sharpe": m["sharpe"],
                "cagr": m["cagr"],
                "max_dd": m["max_drawdown"],
                "avg_cost_bps": m["avg_cost_bps"],
                "n_trades": m["n_trades"],
                "turnover": m["turnover"],
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    g = df.groupby(["params_hash", "params", "capital"]).agg(
        sharpe_mean=("sharpe", "mean"),
        sharpe_std=("sharpe", "std"),
        sharpe_min=("sharpe", "min"),
        cagr_mean=("cagr", "mean"),
        max_dd_mean=("max_dd", "mean"),
        turnover_mean=("turnover", "mean"),
        cost_bps_mean=("avg_cost_bps", "mean"),
        n_folds=("sharpe", "count"),
    ).reset_index()
    return g.sort_values("sharpe_mean", ascending=False)
