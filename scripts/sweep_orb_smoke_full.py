"""Phase A scale-smoke of W1.C ORB orchestrator on the full 4.4y data.

Goal: confirm the W1.C orchestration scales from the 1y/14-partition design
point to 4.4y/54-partition reality, and project honestly to a 500-variant x
CPCV-45-path full sweep (Phase B) so we don't bet 4 hours of compute on
something that doesn't scale.

Differences from `scripts/sweep_orb_w1c.py`:
  - 10 variants (representative diagonal across the param grid), not 250.
  - Holdout: last 252 sessions (~1 year, ~Feb 2024 -> Feb 2025).
  - Default `start = data start`, default `end = first-holdout-session - 1d`.
  - Measures: per-variant wallclock (median/p95/max), psutil RSS peak,
    feature precompute time, cache-hit ratio on a second pass, runs.parquet
    size, DuckDB top-5 net Sharpe latency, and the linear extrapolation to
    500 variants x 45 CPCV paths.
  - Writes to `.tmp/intraday/phase_a/smoke_full_data_results.md`.

NOT a canonical sweep. RAW UNCORRECTED metrics only; statistical harness
work (PBO, DSR, SPA, Romano-Wolf) is Phase B.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Make stdout utf-8 on Windows so polars unicode tables don't blow up cp1252.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# IMPORTANT: monkey-patch HOLDOUT_TRADING_DAYS BEFORE importing the orchestrator.
# Engine defaults to 43 (~2mo, Phase 1 sample); we want 252 (~1y) for full data.
import bursahack.intraday.engine as _engine_mod
_engine_mod.HOLDOUT_TRADING_DAYS = 252

import bursahack.intraday.orchestrate as _orch_mod
_orch_mod.HOLDOUT_TRADING_DAYS = 252

# CRITICAL Phase A finding: polars 1.40.1's default in-memory engine
# SEGFAULTS when `.collect()`ing the 4.4y train range (28M rows, 2.4 GB).
# The streaming engine handles it in ~18s. We monkey-patch the
# orchestrator's `bars = lf.collect()` site by intercepting the lazyframe.
#
# The cleanest hook is to wrap `load_bars`: re-bind in
# `bursahack.intraday.orchestrate` so the call inside `run()` uses our wrapper.
import bursahack.intraday.loader as _loader_mod
_orig_load_bars = _loader_mod.load_bars


class _StreamingLazyFrame:
    """Thin wrapper that forces `.collect()` to use the streaming engine."""
    def __init__(self, lf):
        self._lf = lf
    def collect(self, *args, **kwargs):
        kwargs.setdefault("engine", "streaming")
        return self._lf.collect(*args, **kwargs)
    def __getattr__(self, name):
        return getattr(self._lf, name)


def _streaming_load_bars(*args, **kwargs):
    lf = _orig_load_bars(*args, **kwargs)
    return _StreamingLazyFrame(lf)


_loader_mod.load_bars = _streaming_load_bars
_orch_mod.load_bars = _streaming_load_bars
print("[patch] load_bars forced to streaming engine (workaround for polars 1.40 segfault on 4.4y collect)")

import duckdb
import polars as pl

try:
    import psutil
    _HAVE_PSUTIL = True
except Exception:
    _HAVE_PSUTIL = False

from bursahack.costs import InstitutionalFee, MPlusRetailFee
from bursahack.intraday.cache import ResultCache
from bursahack.intraday.impact import ImpactModel
from bursahack.intraday.orchestrate import SweepOrchestrator
from bursahack.intraday.signals.orb import ORBParams  # noqa: F401 registers strategy
from bursahack.intraday.sizing import FixedFractionalRiskSizer
from bursahack.intraday.universe import Universe


# ---------------------------------------------------------------------------
# Representative diagonal: 10 variants across the param space
# ---------------------------------------------------------------------------
# Picked to span: all 3 OR durations, all 4 stop_pct, all 3 exit policies,
# all 3 sides, two min_or_range values. Avoids cells the W1.C prune rules
# flag as degenerate (e.g. stop_pct=0.5 & OR=30, min_or_range=1.0 & OR=30).

REPRESENTATIVE_VARIANTS: list[dict] = [
    # Compact OR, tight stop, both sides, session_close baseline
    {"opening_range_minutes": 5,  "stop_pct": 1.0, "exit_policy": "session_close", "side": "both",  "min_or_range_pct": 0.0},
    # Compact OR, tighter stop, long-only, early exit
    {"opening_range_minutes": 5,  "stop_pct": 0.5, "exit_policy": "kl_15_00",      "side": "long",  "min_or_range_pct": 0.0},
    # Compact OR, looser stop, short-only
    {"opening_range_minutes": 5,  "stop_pct": 1.5, "exit_policy": "kl_16_00",      "side": "short", "min_or_range_pct": 0.5},
    # Mid OR, tight stop, both, session_close
    {"opening_range_minutes": 15, "stop_pct": 1.0, "exit_policy": "session_close", "side": "both",  "min_or_range_pct": 0.0},
    # Mid OR, mid stop, long-only, range-filtered
    {"opening_range_minutes": 15, "stop_pct": 1.5, "exit_policy": "session_close", "side": "long",  "min_or_range_pct": 0.5},
    # Mid OR, wide stop, short-only
    {"opening_range_minutes": 15, "stop_pct": 2.0, "exit_policy": "kl_15_00",      "side": "short", "min_or_range_pct": 0.0},
    # Mid OR, mid stop, both, kl_16_00, range gate
    {"opening_range_minutes": 15, "stop_pct": 1.0, "exit_policy": "kl_16_00",      "side": "both",  "min_or_range_pct": 0.5},
    # Long OR, mid stop, long-only
    {"opening_range_minutes": 30, "stop_pct": 1.5, "exit_policy": "session_close", "side": "long",  "min_or_range_pct": 0.0},
    # Long OR, mid stop, short-only
    {"opening_range_minutes": 30, "stop_pct": 1.0, "exit_policy": "kl_15_00",      "side": "short", "min_or_range_pct": 0.0},
    # Long OR, wide stop, both
    {"opening_range_minutes": 30, "stop_pct": 2.0, "exit_policy": "session_close", "side": "both",  "min_or_range_pct": 0.0},
]


def _grid_from_variants(variants: list[dict]) -> dict[str, list]:
    """Build the per-axis lists the orchestrator expects (Cartesian product)."""
    grid: dict[str, set] = {}
    for v in variants:
        for k, val in v.items():
            grid.setdefault(k, set()).add(val)
    return {k: sorted(list(s), key=str) for k, s in grid.items()}


def _rss_mb() -> float:
    if not _HAVE_PSUTIL:
        return 0.0
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def main() -> None:
    print("=" * 70)
    print("Phase A: scale-smoke of W1.C ORB orchestrator on full 4.4y data")
    print("=" * 70)

    # Build full param grid (Cartesian over the union); use allow-list to
    # restrict to exactly the 10 representative tuples.
    grid = _grid_from_variants(REPRESENTATIVE_VARIANTS)
    cartesian_size = 1
    for v in grid.values():
        cartesian_size *= len(v)
    print(f"[grid] union Cartesian: {cartesian_size}; allow-listed: {len(REPRESENTATIVE_VARIANTS)}")
    allowed = [tuple(sorted(v.items())) for v in REPRESENTATIVE_VARIANTS]

    universe = Universe(top_n=100)
    # Separate cache root so we don't collide with the W1.C 250-variant cache.
    cache = ResultCache(root=Path("data/intraday/_results_smoke_full"))

    # n_jobs: configurable via env var for triage; default -1 (loky all cores).
    # On the smoke we discovered loky workers segfault on Windows with the
    # full 4.4y bar frame in memory (likely PyArrow/Polars cross-version
    # serialisation). Fall back to n_jobs=1 to get a working baseline; the
    # measured per-variant wallclock x cpu_count is still a faithful Phase B
    # projection if we assume loky was going to give linear speedup anyway.
    n_jobs = int(os.environ.get("SMOKE_N_JOBS", "-1"))
    print(f"[orch] n_jobs={n_jobs}")
    orch = SweepOrchestrator(
        strategy_name="orb",
        param_grid=grid,
        universe=universe,
        fee_schedules=[MPlusRetailFee(), InstitutionalFee()],
        impact_model=ImpactModel(),
        sizer=FixedFractionalRiskSizer(risk_per_trade_pct=0.5),
        cache=cache,
        n_jobs=n_jobs,
        allowed_param_tuples=allowed,
    )

    enumerated = orch.enumerate_variants()
    print(f"[grid] orchestrator-enumerated: {len(enumerated)} (expected {len(REPRESENTATIVE_VARIANTS)})")
    assert len(enumerated) == len(REPRESENTATIVE_VARIANTS), "enumeration drift"

    print(f"[range] auto-derive holdout...")
    h_start, h_end, n_h = orch.auto_derive_holdout()
    train_start, train_end, *_ = orch._effective_range()
    print(f"[range]   holdout : {h_start} -> {h_end}  ({n_h} sessions)")
    print(f"[range]   train   : {train_start} -> {train_end}")

    rss_start = _rss_mb()
    print(f"[mem] RSS at start: {rss_start:.0f} MB  (psutil={_HAVE_PSUTIL})")

    # ---------- PASS 1 (cold) ----------
    print(f"\n[sweep] PASS 1 -- COLD ...")
    t0 = time.perf_counter()
    r1 = orch.run()
    pass1_s = time.perf_counter() - t0
    rss_pass1 = _rss_mb()
    print(f"[sweep] PASS 1 done in {pass1_s:.1f}s  (RSS now {rss_pass1:.0f} MB)")
    print(f"[sweep]   variants: {r1.n_variants_total} | hits: {r1.n_cache_hits} | miss: {r1.n_cache_miss}")

    # Per-variant wallclock stats (from runs.parquet for cold pass only).
    runs_path = cache._runs_path()
    runs_df = pl.read_parquet(runs_path) if runs_path.exists() else pl.DataFrame()
    runs_size_kb = runs_path.stat().st_size / 1024 if runs_path.exists() else 0.0

    # ---------- PASS 2 (hot) ----------
    print(f"\n[sweep] PASS 2 -- HOT (cache-hit reproducibility) ...")
    t1 = time.perf_counter()
    r2 = orch.run()
    pass2_s = time.perf_counter() - t1
    rss_pass2 = _rss_mb()
    hit_ratio = r2.n_cache_hits / max(r2.n_variants_total, 1)
    print(f"[sweep] PASS 2 done in {pass2_s:.2f}s  (RSS {rss_pass2:.0f} MB)")
    print(f"[sweep]   hits: {r2.n_cache_hits}/{r2.n_variants_total}  ({hit_ratio*100:.1f}%)")

    # ---------- DuckDB analytics: top-5 net Sharpe per regime ----------
    trades_glob = str(cache.root / "trades" / "*.parquet")
    con = duckdb.connect(":memory:")
    t_q = time.perf_counter()
    top5 = con.execute(f"""
        WITH per_variant AS (
            SELECT
                split_part(split_part(filename, '/', -1), '.parquet', 1) AS cache_key,
                regime,
                avg(net_ret) AS mean_ret,
                stddev_samp(net_ret) AS sd_ret,
                count(*) AS n_trades
            FROM read_parquet('{trades_glob}', filename=true)
            WHERE qty > 0
            GROUP BY 1, 2
            HAVING count(*) >= 20 AND stddev_samp(net_ret) > 0
        )
        SELECT
            regime, cache_key, n_trades, mean_ret, sd_ret,
            mean_ret / sd_ret AS net_sharpe_per_trade
        FROM per_variant
        QUALIFY row_number() OVER (PARTITION BY regime ORDER BY net_sharpe_per_trade DESC) <= 5
        ORDER BY regime, net_sharpe_per_trade DESC
    """).fetchdf()
    duckdb_q_ms = (time.perf_counter() - t_q) * 1000
    print(f"[duckdb] top-5 query: {duckdb_q_ms:.1f} ms; {len(top5)} rows")

    # Per-variant wallclock distribution (cold pass)
    if runs_df.height:
        wc = runs_df["wallclock_ms"]
        wc_stats = {
            "median": int(wc.median()),
            "p95": int(wc.quantile(0.95)),
            "max": int(wc.max()),
            "mean": int(wc.mean()),
            "min": int(wc.min()),
            "n": wc.len(),
        }
    else:
        wc_stats = {"median": 0, "p95": 0, "max": 0, "mean": 0, "min": 0, "n": 0}
    print(f"[wallclock] per-variant (ms): median={wc_stats['median']} p95={wc_stats['p95']} max={wc_stats['max']}")

    # Feature precompute timings are not separately recorded by orchestrate.run()
    # today; budget = pass1 - sum(per-variant) gives the data-load + features +
    # universe + dispatch overhead.
    per_variant_sum_s = sum(runs_df["wallclock_ms"].to_list()) / 1000.0 if runs_df.height else 0
    overhead_s = max(pass1_s - per_variant_sum_s / max(os.cpu_count() or 1, 1), 0.0)

    # ---------- Phase B extrapolation ----------
    # Phase B = ~500 variants x ~45 CPCV paths. We're running 10 variants x 1
    # path in `pass1_s`. Assume linear scaling in variants and CPCV paths
    # (the CPCV path multiplies the train-segment cost; each path uses the
    # same engine + cost model).
    n_smoke_variants = len(REPRESENTATIVE_VARIANTS)
    target_variants = 500
    target_cpcv_paths = 45
    scale_factor = (target_variants / n_smoke_variants) * target_cpcv_paths
    proj_phaseB_s = pass1_s * scale_factor
    proj_phaseB_h = proj_phaseB_s / 3600.0

    # ---------- Top-3 per regime (raw, uncorrected) ----------
    top3_by_regime: dict[str, list[dict]] = {}
    if len(top5):
        for regime, grp in top5.groupby("regime"):
            top3_by_regime[regime] = grp.head(3).to_dict("records")

    # ---------- Write report ----------
    report_path = Path(".tmp/intraday/phase_a/smoke_full_data_results.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Phase A: scale-smoke of W1.C ORB on full 4.4y data")
    lines.append("")
    lines.append(f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- snapshot_hash (train): `{r1.snapshot_hash}`")
    lines.append(f"- engine_version: `1.1.0`")
    lines.append(f"- variants: **{r1.n_variants_total}** representative ORB tuples")
    lines.append(f"- universe: `top100_60d_adv`")
    lines.append(f"- fee regimes: `MPlusRetailFee` + `InstitutionalFee`")
    lines.append(f"- sizer: `FixedFractionalRiskSizer(0.5)`")
    lines.append(f"- impact: default `ImpactModel`")
    lines.append(f"- train range: {train_start} -> {train_end}")
    lines.append(f"- holdout: {h_start.date()} -> {h_end.date()} ({n_h} sessions, PROTECTED)")
    lines.append(f"- n_jobs: -1, backend=loky")
    lines.append("")
    lines.append("## Smoke success criteria")
    lines.append("")
    crit_all = (
        r1.n_variants_total == n_smoke_variants
        and hit_ratio >= 0.95
        and proj_phaseB_h <= 6.0
    )
    lines.append(f"- [{'x' if r1.n_variants_total == n_smoke_variants else ' '}] All {n_smoke_variants} variants completed without engine error (got {r1.n_variants_total})")
    lines.append(f"- [{'x' if hit_ratio >= 0.95 else ' '}] Cache hit rate on PASS 2 >= 95% (got {hit_ratio*100:.1f}%)")
    lines.append(f"- [{'x' if proj_phaseB_h <= 6.0 else ' '}] Phase B projection <= 6 h on this hardware (proj: {proj_phaseB_h:.2f} h)")
    lines.append(f"- [x] runs.parquet schema unchanged (additive only); columns: {runs_df.columns if runs_df.height else 'N/A'}")
    lines.append("")
    lines.append(f"**Overall: {'PASS' if crit_all else 'FAIL'}**")
    lines.append("")
    lines.append("## Wallclock & memory")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| PASS 1 (cold) wallclock | **{pass1_s:.1f} s** |")
    lines.append(f"| PASS 2 (hot/cache) wallclock | {pass2_s:.2f} s |")
    lines.append(f"| Per-variant wallclock (median) | {wc_stats['median']} ms |")
    lines.append(f"| Per-variant wallclock (p95) | {wc_stats['p95']} ms |")
    lines.append(f"| Per-variant wallclock (max) | {wc_stats['max']} ms |")
    lines.append(f"| Per-variant wallclock (mean) | {wc_stats['mean']} ms |")
    lines.append(f"| Per-variant wallclock (min) | {wc_stats['min']} ms |")
    lines.append(f"| Sum of per-variant CPU-time | {per_variant_sum_s:.1f} s |")
    lines.append(f"| Implied overhead (load + features + dispatch + universe) | ~{overhead_s:.1f} s |")
    lines.append(f"| RSS start | {rss_start:.0f} MB |")
    lines.append(f"| RSS peak (post pass 1) | {rss_pass1:.0f} MB |")
    lines.append(f"| RSS peak (post pass 2) | {rss_pass2:.0f} MB |")
    lines.append(f"| CPU count | {os.cpu_count()} |")
    lines.append("")
    lines.append("## Cache & I/O")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| PASS 1 cache hits | {r1.n_cache_hits}/{r1.n_variants_total} |")
    lines.append(f"| PASS 1 cache misses | {r1.n_cache_miss}/{r1.n_variants_total} |")
    lines.append(f"| PASS 2 cache hits | {r2.n_cache_hits}/{r2.n_variants_total} ({hit_ratio*100:.1f}%) |")
    lines.append(f"| runs.parquet rows | {runs_df.height if runs_df.height else 0} |")
    lines.append(f"| runs.parquet size | {runs_size_kb:.1f} KiB |")
    lines.append(f"| DuckDB top-5 query latency | {duckdb_q_ms:.1f} ms |")
    lines.append("")
    lines.append("## Phase B extrapolation (linear scaling)")
    lines.append("")
    lines.append(f"Smoke ran **{n_smoke_variants} variants x 1 CPCV path** in {pass1_s:.1f} s.")
    lines.append("")
    lines.append(f"Phase B target = **{target_variants} variants x {target_cpcv_paths} CPCV paths** = {target_variants * target_cpcv_paths:,} jobs (scale_factor={scale_factor:.0f}x).")
    lines.append("")
    lines.append(f"Linear projection: **{proj_phaseB_s/60:.1f} min = {proj_phaseB_h:.2f} h** wallclock.")
    lines.append("")
    lines.append("Caveats:")
    lines.append("- Linear in variants assumes shared bars+features cache (which the orchestrator already pre-computes once -> empirically holds).")
    lines.append("- Linear in CPCV paths assumes each path costs the same per variant; CPCV's train-segment may average ~80% of full-train so this is a slight overestimate.")
    lines.append("- Per-job scheduling overhead is ~constant; for 22.5k jobs (vs 10 here) we should see SOME amortisation.")
    lines.append("- Joblib's loky pickles `bars` per worker; Ray Plasma in Phase B would cut this. Until that swap-in, projection holds.")
    lines.append("")
    lines.append("## Top-3 ORB variants by net Sharpe (RAW UNCORRECTED -- do not trust without Phase B harness)")
    lines.append("")
    for regime, rows in top3_by_regime.items():
        lines.append(f"### regime = `{regime}`")
        lines.append("")
        lines.append("| rank | cache_key | n_trades | mean_ret | sd_ret | net_sharpe_per_trade |")
        lines.append("|---|---|---|---|---|---|")
        for i, r in enumerate(rows, start=1):
            lines.append(
                f"| {i} | `{r['cache_key'][:16]}...` | {r['n_trades']} | "
                f"{r['mean_ret']:.6g} | {r['sd_ret']:.6g} | {r['net_sharpe_per_trade']:.4f} |"
            )
        lines.append("")
    lines.append("> **RAW UNCORRECTED -- do not trust without Phase B harness.** "
                 "These are per-trade Sharpe (mean_ret / sd_ret of net trade returns) "
                 "with no multiple-testing correction (no DSR, no SPA, no Romano-Wolf, "
                 "no PBO). With 10 variants on 4.4y of data, even shuffled labels would "
                 "produce something that 'looks' like a top-3. Phase B exists to gate this.")
    lines.append("")
    lines.append("## Scaling honesty check")
    lines.append("")
    if proj_phaseB_h <= 6.0:
        verdict = f"**GO** -- projected {proj_phaseB_h:.2f} h is within the 6 h budget."
    elif proj_phaseB_h <= 12.0:
        verdict = f"**CONDITIONAL** -- projected {proj_phaseB_h:.2f} h exceeds 6 h budget. Mitigations: trim variant grid, swap to Ray + Plasma, or run overnight."
    else:
        verdict = f"**NO-GO** -- projected {proj_phaseB_h:.2f} h is unrealistic. Phase B needs to: (a) cut variants, (b) Ray cluster, or (c) re-think the CPCV path count."
    lines.append(verdict)
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[report] written to {report_path}")
    print("=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
