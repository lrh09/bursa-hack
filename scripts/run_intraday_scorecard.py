"""End-to-end intraday scorecard: ORB + LMSW under CPCV + DSR + PBO.

Glue script that joins the pieces:
  orchestrator (runs backtests + caches) -> CPCV splits over the test-period
  PnL -> per-variant per-fold Sharpe matrix -> DSR / PBO / bootstrap CI ->
  markdown + JSON scorecard.

CPCV shortcut used here:
  Our strategies are STATELESS — ORB and LMSW have no learnable parameters,
  just fixed thresholds. So slicing the cached PnL by fold boundaries is
  equivalent to the canonical train-fit / test-evaluate CPCV loop. If we
  ever add a fitted strategy this script needs the full per-split engine
  re-run; for ORB / LMSW the shortcut is exact.

Scope: this is a SMOKE run, not the canonical sweep. Defaults:
  - 1 year of data (the 2023 calendar year is the default window)
  - Top-30 universe (60d ADV), monthly rebalance
  - 4 ORB variants + 4 LMSW variants = 8 total
  - 8 CPCV folds x 2 test folds = 28 splits (per the Lopez de Prado AFML §12 default)
  - Sequential (n_jobs=1) for Windows-loky safety; smoke finishes in minutes

Outputs (under .tmp/intraday/phase_b/):
  scorecard.md   — human-readable report
  scorecard.json — machine-readable scores + diagnostics

Override via env vars:
  SCORE_START=YYYY-MM-DD          (default: 2023-01-01)
  SCORE_END=YYYY-MM-DD            (default: 2023-12-31)
  SCORE_TOP_N=30                  (universe size)
  SCORE_N_JOBS=1                  (joblib workers)
  SCORE_N_FOLDS=8
  SCORE_N_TEST_FOLDS=2
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

# Make stdout utf-8 on Windows so polars unicode tables don't blow up cp1252.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# Streaming-engine patch (Phase A finding — polars 1.40 segfaults on big collects).
import bursahack.intraday.loader as _loader_mod
import bursahack.intraday.orchestrate as _orch_mod

_orig_load_bars = _loader_mod.load_bars


class _StreamingLazyFrame:
    def __init__(self, lf): self._lf = lf
    def collect(self, *a, **kw):
        kw.setdefault("engine", "streaming")
        return self._lf.collect(*a, **kw)
    def __getattr__(self, name):
        return getattr(self._lf, name)


def _streaming_load_bars(*a, **kw):
    return _StreamingLazyFrame(_orig_load_bars(*a, **kw))


_loader_mod.load_bars = _streaming_load_bars
_orch_mod.load_bars = _streaming_load_bars

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

from bursahack.costs import InstitutionalFee, MPlusRetailFee  # noqa: E402
from bursahack.intraday.cache import ResultCache  # noqa: E402
from bursahack.intraday.cpcv import make_cpcv_splits  # noqa: E402
from bursahack.intraday.diagnostics import (  # noqa: E402
    deflated_sharpe_ratio,
    pbo,
    sharpe_ratio,
    stationary_bootstrap_sharpe_ci,
)
from bursahack.intraday.impact import ImpactModel  # noqa: E402
from bursahack.intraday.orchestrate import SweepOrchestrator  # noqa: E402
import bursahack.intraday.signals  # noqa: F401, E402  (registers all strategy families)
from bursahack.intraday.sizing import FixedFractionalRiskSizer  # noqa: E402
from bursahack.intraday.universe import Universe  # noqa: E402


# ---------------------------------------------------------------------------
# Variant grids — kept small for smoke.
# ---------------------------------------------------------------------------

ORB_VARIANTS = [
    {"opening_range_minutes": 5,  "stop_pct": 1.0, "exit_policy": "session_close", "side": "both",  "min_or_range_pct": 0.0},
    {"opening_range_minutes": 5,  "stop_pct": 1.5, "exit_policy": "session_close", "side": "long",  "min_or_range_pct": 0.0},
    {"opening_range_minutes": 15, "stop_pct": 1.0, "exit_policy": "session_close", "side": "long",  "min_or_range_pct": 0.0},
    {"opening_range_minutes": 15, "stop_pct": 1.5, "exit_policy": "kl_15_00",      "side": "both",  "min_or_range_pct": 0.5},
    {"opening_range_minutes": 15, "stop_pct": 2.0, "exit_policy": "session_close", "side": "both",  "min_or_range_pct": 0.0},
    {"opening_range_minutes": 30, "stop_pct": 1.5, "exit_policy": "session_close", "side": "long",  "min_or_range_pct": 0.5},
    {"opening_range_minutes": 30, "stop_pct": 2.0, "exit_policy": "session_close", "side": "long",  "min_or_range_pct": 0.0},
]

LMSW_VARIANTS = [
    {"z_window_bars": 60,  "volume_z_threshold": 3.0, "return_floor_pct": 0.10, "stop_pct": 1.0, "exit_policy": "session_close", "side_mode": "continuation"},
    {"z_window_bars": 60,  "volume_z_threshold": 4.0, "return_floor_pct": 0.20, "stop_pct": 1.0, "exit_policy": "session_close", "side_mode": "continuation"},
    {"z_window_bars": 60,  "volume_z_threshold": 3.0, "return_floor_pct": 0.10, "stop_pct": 1.0, "exit_policy": "session_close", "side_mode": "reversal"},
    {"z_window_bars": 60,  "volume_z_threshold": 4.0, "return_floor_pct": 0.20, "stop_pct": 1.5, "exit_policy": "session_close", "side_mode": "reversal"},
    {"z_window_bars": 120, "volume_z_threshold": 3.0, "return_floor_pct": 0.10, "stop_pct": 1.5, "exit_policy": "kl_15_00",      "side_mode": "reversal"},
    {"z_window_bars": 120, "volume_z_threshold": 3.0, "return_floor_pct": 0.10, "stop_pct": 1.0, "exit_policy": "session_close", "side_mode": "continuation"},
    {"z_window_bars": 20,  "volume_z_threshold": 3.0, "return_floor_pct": 0.10, "stop_pct": 1.0, "exit_policy": "session_close", "side_mode": "reversal"},
]

VWAP_RECLAIM_VARIANTS = [
    {"vwap_price": "close",   "min_below_minutes": 15, "stop_pct": 1.0, "exit_policy": "session_close", "side": "both"},
    {"vwap_price": "close",   "min_below_minutes": 30, "stop_pct": 1.0, "exit_policy": "session_close", "side": "long"},
    {"vwap_price": "close",   "min_below_minutes": 30, "stop_pct": 1.5, "exit_policy": "session_close", "side": "both"},
    {"vwap_price": "typical", "min_below_minutes": 15, "stop_pct": 1.0, "exit_policy": "session_close", "side": "both"},
    {"vwap_price": "typical", "min_below_minutes": 30, "stop_pct": 1.5, "exit_policy": "kl_15_00",      "side": "long"},
    {"vwap_price": "close",   "min_below_minutes": 60, "stop_pct": 2.0, "exit_policy": "session_close", "side": "long"},
]

NR7_ORB_VARIANTS = [
    {"nr_window": 7, "opening_range_minutes": 5,  "stop_pct": 1.0, "exit_policy": "session_close", "side": "both"},
    {"nr_window": 7, "opening_range_minutes": 5,  "stop_pct": 1.5, "exit_policy": "session_close", "side": "long"},
    {"nr_window": 7, "opening_range_minutes": 15, "stop_pct": 1.5, "exit_policy": "session_close", "side": "both"},
    {"nr_window": 4, "opening_range_minutes": 5,  "stop_pct": 1.0, "exit_policy": "session_close", "side": "both"},
    {"nr_window": 4, "opening_range_minutes": 15, "stop_pct": 2.0, "exit_policy": "kl_15_00",      "side": "long"},
]

GAP_CONTINUATION_VARIANTS = [
    {"gap_z_threshold": 1.0, "vol_mult": 1.5, "or_minutes": 5,  "stop_pct": 1.0, "exit_policy": "session_close", "side": "both"},
    {"gap_z_threshold": 1.5, "vol_mult": 2.0, "or_minutes": 5,  "stop_pct": 1.0, "exit_policy": "session_close", "side": "both"},
    {"gap_z_threshold": 1.5, "vol_mult": 2.0, "or_minutes": 5,  "stop_pct": 1.5, "exit_policy": "session_close", "side": "long"},
    {"gap_z_threshold": 2.0, "vol_mult": 3.0, "or_minutes": 5,  "stop_pct": 1.5, "exit_policy": "session_close", "side": "both"},
    {"gap_z_threshold": 1.5, "vol_mult": 2.0, "or_minutes": 15, "stop_pct": 1.0, "exit_policy": "kl_15_00",      "side": "long"},
    {"gap_z_threshold": 2.0, "vol_mult": 2.0, "or_minutes": 5,  "stop_pct": 2.0, "exit_policy": "session_close", "side": "long"},
]

# Strategy registry for the sweep: name -> variant list.
FAMILIES: dict[str, list[dict]] = {
    "orb": ORB_VARIANTS,
    "lmsw": LMSW_VARIANTS,
    "vwap_reclaim": VWAP_RECLAIM_VARIANTS,
    "nr7_orb": NR7_ORB_VARIANTS,
    "gap_continuation": GAP_CONTINUATION_VARIANTS,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _grid_from_variants(variants: list[dict]) -> dict[str, list]:
    grid: dict[str, set] = {}
    for v in variants:
        for k, val in v.items():
            grid.setdefault(k, set()).add(val)
    return {k: sorted(list(s), key=str) for k, s in grid.items()}


def _daily_returns(
    trades: pl.DataFrame, regime: str, starting_equity: float = 100_000.0
) -> pl.DataFrame:
    """Aggregate per-trade PnL to a daily equity-fraction return.

    Per-trade RM-PnL = qty * entry_px * net_ret. Sum the RM-PnL across all
    trades closed on a given KL date, then divide by starting_equity to get
    a stable equity-fraction return (no compounding within the smoke
    window).
    """
    df = trades.filter((pl.col("regime") == regime) & (pl.col("qty") > 0))
    if df.height == 0:
        return pl.DataFrame(schema={"date": pl.Date, "ret": pl.Float64})
    return (
        df.with_columns([
            pl.col("exit_ts").dt.date().alias("date"),
            (pl.col("qty") * pl.col("entry_px") * pl.col("net_ret")).alias("_pnl_rm"),
        ])
          .group_by("date")
          .agg(pl.col("_pnl_rm").sum().alias("_day_pnl_rm"))
          .with_columns((pl.col("_day_pnl_rm") / starting_equity).alias("ret"))
          .select(["date", "ret"])
          .sort("date")
    )


def _align_daily_pnl(
    variant_daily_returns: list[pl.DataFrame],
) -> tuple[list[date], np.ndarray]:
    """Build the (T_days, N_variants) return matrix on a shared date axis.

    Variants with no trades on a given date contribute 0.0 for that date.
    """
    all_dates: set[date] = set()
    for d in variant_daily_returns:
        if d.height:
            all_dates |= set(d["date"].to_list())
    dates = sorted(all_dates)
    n_dates = len(dates)
    n_var = len(variant_daily_returns)
    out = np.zeros((n_dates, n_var))
    date_to_idx = {d: i for i, d in enumerate(dates)}
    for v, df in enumerate(variant_daily_returns):
        if df.height == 0:
            continue
        for row in df.iter_rows(named=True):
            out[date_to_idx[row["date"]], v] = row["ret"]
    return dates, out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 70)
    print("Intraday scorecard: ORB + LMSW with CPCV + DSR + PBO")
    print("=" * 70)

    start = date.fromisoformat(os.environ.get("SCORE_START", "2023-01-01"))
    end = date.fromisoformat(os.environ.get("SCORE_END", "2023-12-31"))
    top_n = int(os.environ.get("SCORE_TOP_N", "30"))
    n_jobs = int(os.environ.get("SCORE_N_JOBS", "1"))
    n_folds = int(os.environ.get("SCORE_N_FOLDS", "8"))
    n_test_folds = int(os.environ.get("SCORE_N_TEST_FOLDS", "2"))

    total_variants = sum(len(v) for v in FAMILIES.values())
    print(f"[config] window     : {start} -> {end}")
    print(f"[config] universe   : top_{top_n} by 60d ADV")
    print(f"[config] families   : {', '.join(f'{k}({len(v)})' for k, v in FAMILIES.items())}")
    print(f"[config] variants   : {total_variants} total")
    print(f"[config] n_jobs     : {n_jobs}")
    print(f"[config] cpcv       : {n_folds} folds x {n_test_folds} test")

    universe = Universe(top_n=top_n)
    cache = ResultCache(root=Path("data/intraday/_results_scorecard"))

    # --- Run every family through the orchestrator, gather variant meta ---
    variant_meta: list[dict] = []
    for fam_name, fam_variants in FAMILIES.items():
        print(f"\n[orch] running {fam_name} sweep ({len(fam_variants)} variants) ...")
        t0 = time.perf_counter()
        orch = SweepOrchestrator(
            strategy_name=fam_name,
            param_grid=_grid_from_variants(fam_variants),
            universe=universe,
            fee_schedules=[MPlusRetailFee(), InstitutionalFee()],
            impact_model=ImpactModel(),
            sizer=FixedFractionalRiskSizer(risk_per_trade_pct=0.5),
            cache=cache,
            n_jobs=n_jobs,
            start=start,
            end=end,
            allowed_param_tuples=[tuple(sorted(v.items())) for v in fam_variants],
        )
        res = orch.run(sweep_id=f"scorecard_{fam_name}")
        print(f"[orch] {fam_name} done in {time.perf_counter() - t0:.1f}s "
              f"({res.n_cache_hits} hits / {res.n_cache_miss} miss)")
        for vid, params in orch.enumerate_variants():
            ck = next(o.cache_key for o in res.outcomes if o.variant_id == vid)
            variant_meta.append({
                "family": fam_name, "variant_id": vid, "cache_key": ck,
                "params": params.model_dump(),
            })

    # --- Build daily PnL per variant per regime ---
    regimes = ["mplus_retail", "institutional"]
    daily_by_regime: dict[str, list[pl.DataFrame]] = {r: [] for r in regimes}
    for vm in variant_meta:
        result = cache.get(vm["cache_key"])
        if result is None:
            for r in regimes:
                daily_by_regime[r].append(pl.DataFrame(schema={"date": pl.Date, "ret": pl.Float64}))
            continue
        for r in regimes:
            daily_by_regime[r].append(_daily_returns(result.trades, regime=r))

    # --- CPCV + diagnostics per regime ---
    scorecard: dict = {
        "config": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "top_n": top_n,
            "n_folds": n_folds,
            "n_test_folds": n_test_folds,
            "n_variants": len(variant_meta),
        },
        "variants": variant_meta,
        "per_regime": {},
    }

    for regime in regimes:
        print(f"\n[diag] regime = {regime}")
        dates, ret_matrix = _align_daily_pnl(daily_by_regime[regime])
        t = len(dates)
        if t < n_folds * 2:
            print(f"  skip: only {t} trading days available; need >= {n_folds*2}")
            continue

        # CPCV splits over the date axis.
        splits = make_cpcv_splits(
            n=t, n_folds=n_folds, n_test_folds=n_test_folds,
            embargo=1,  # 1-day embargo (intraday strategies close same-day)
        )

        # Per-variant per-split test-period Sharpe.
        n_var = ret_matrix.shape[1]
        path_sharpe = np.zeros((len(splits), n_var))
        for s_idx, sp in enumerate(splits):
            for v in range(n_var):
                test_ret = ret_matrix[sp.test_idx, v]
                path_sharpe[s_idx, v] = sharpe_ratio(test_ret, periods_per_year=252)

        # Mean Sharpe per variant (over the 28 split paths) — what we rank by.
        mean_sr_by_variant = path_sharpe.mean(axis=0)

        # DSR for the top-K variants.
        per_variant_scores: list[dict] = []
        ranked = np.argsort(-mean_sr_by_variant)  # descending
        # All-trial Sharpes (one per variant, averaged over splits) feed the
        # false-strategy-theorem correction.
        trial_sharpes = mean_sr_by_variant.copy()

        for v_idx in ranked:
            vm = variant_meta[v_idx]
            v_returns = ret_matrix[:, v_idx]
            # DSR uses the raw daily returns (not split-mean Sharpes) so it
            # captures the per-period noise structure.
            dsr_res = deflated_sharpe_ratio(v_returns, trial_sharpes)
            ci_res = stationary_bootstrap_sharpe_ci(
                v_returns, n_boot=500, rng_seed=v_idx, periods_per_year=252,
            )
            per_variant_scores.append({
                "family": vm["family"],
                "variant_id": vm["variant_id"],
                "params": vm["params"],
                "mean_path_sharpe": float(mean_sr_by_variant[v_idx]),
                "raw_sharpe": float(sharpe_ratio(v_returns, periods_per_year=252)),
                "dsr": dsr_res["dsr"],
                "dsr_sr0": dsr_res["sr0"],
                "ci_lo": ci_res["lo"],
                "ci_hi": ci_res["hi"],
                "n_trading_days": int((v_returns != 0).sum()),
            })

        # PBO over the daily return matrix.
        try:
            pbo_res = pbo(ret_matrix, n_submatrices=8 if t >= 64 else 4)
            pbo_dict = {
                "pbo": pbo_res.pbo,
                "n_combinations": pbo_res.n_combinations,
                "median_logit_lambda": pbo_res.median_logit_lambda,
            }
        except Exception as exc:
            pbo_dict = {"error": str(exc)}

        scorecard["per_regime"][regime] = {
            "n_trading_days": t,
            "n_splits": len(splits),
            "per_variant": per_variant_scores,
            "pbo": pbo_dict,
        }

        print(f"  trading days   : {t}")
        print(f"  cpcv splits    : {len(splits)}")
        print(f"  PBO            : {pbo_dict.get('pbo', '?'):.3f}"
              if "pbo" in pbo_dict else f"  PBO            : ERROR {pbo_dict}")
        print(f"  top variant    : {per_variant_scores[0]['family']}/"
              f"{per_variant_scores[0]['variant_id']}  "
              f"raw_sr={per_variant_scores[0]['raw_sharpe']:.3f}  "
              f"DSR={per_variant_scores[0]['dsr']:.3f}")

    # --- Write outputs ---
    out_dir = Path(".tmp/intraday/phase_b")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "scorecard.json"
    md_path = out_dir / "scorecard.md"

    json_path.write_text(json.dumps(scorecard, indent=2, default=str), encoding="utf-8")

    # Markdown scorecard.
    md: list[str] = []
    md.append("# Intraday scorecard")
    md.append("")
    md.append(f"- Window: **{start} → {end}**  ({scorecard['per_regime'].get('mplus_retail', {}).get('n_trading_days', '?')} trading days)")
    md.append(f"- Universe: top-{top_n} by 60d ADV")
    md.append(f"- Families: {', '.join(f'{k} ({len(v)})' for k, v in FAMILIES.items())}")
    md.append(f"- CPCV: {n_folds} folds × {n_test_folds} test = "
              f"{scorecard['per_regime'].get('mplus_retail', {}).get('n_splits', '?')} splits")
    md.append("")
    md.append("**How to read this:**")
    md.append("- `raw_sharpe` = annualised, no correction. Vapor if standalone.")
    md.append("- `mean_path_sharpe` = mean over CPCV test-period Sharpes. Less optimistic.")
    md.append("- `DSR` = deflated Sharpe ratio. **> 0.95 = strong**, ~0.5 = ambiguous, < 0.5 = reject.")
    md.append("- `CI [lo, hi]` = 95% block-bootstrap CI. If `lo > 0`, Sharpe is bootstrap-significant.")
    md.append("- `PBO` per regime = probability that picking the IS-best variant underperforms median OOS. **< 0.5 good**, ~ 0.5 = overfit family.")
    md.append("")
    for regime, rinfo in scorecard["per_regime"].items():
        md.append(f"## Regime: `{regime}`")
        md.append("")
        pbo_d = rinfo["pbo"]
        if "pbo" in pbo_d:
            verdict = "GOOD (family-level)" if pbo_d["pbo"] < 0.5 else "OVERFIT (family-level)"
            md.append(f"- PBO: **{pbo_d['pbo']:.3f}** over {pbo_d['n_combinations']} combos — {verdict}")
        else:
            md.append(f"- PBO: ERROR — {pbo_d}")
        md.append("")
        md.append("| rank | family | variant | raw_SR | mean_path_SR | DSR | CI lo | CI hi | days | params |")
        md.append("|---|---|---|---|---|---|---|---|---|---|")
        for i, s in enumerate(rinfo["per_variant"], 1):
            dsr_flag = "✓" if s["dsr"] > 0.95 else ("≈" if s["dsr"] > 0.5 else "✗")
            md.append(
                f"| {i} | {s['family']} | `{s['variant_id']}` | "
                f"{s['raw_sharpe']:+.3f} | {s['mean_path_sharpe']:+.3f} | "
                f"{s['dsr']:.3f} {dsr_flag} | {s['ci_lo']:+.3f} | {s['ci_hi']:+.3f} | "
                f"{s['n_trading_days']} | `{s['params']}` |"
            )
        md.append("")

    md.append("---")
    md.append("")
    md.append("**Notes:**")
    md.append("- CPCV slicing on already-cached PnL is valid for stateless strategies (ORB + LMSW). Fitted strategies would need per-split re-runs.")
    md.append("- Smoke run; not the canonical sweep. Canonical = full 4.4y, ~500 variants, n_folds=10/n_test_folds=2.")
    md.append("- Cost regimes: MPlus retail + institutional (5 bps). Per-regime min-notional gates already applied at engine level.")
    md.append("")

    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\n[report] scorecard.md  -> {md_path}")
    print(f"[report] scorecard.json -> {json_path}")
    print("=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
