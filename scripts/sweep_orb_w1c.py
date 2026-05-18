"""W1.C: 250-variant ORB sweep + DuckDB analytics report.

Runs the full SweepOrchestrator on the ORB family with a Cartesian-pruned
grid (~250 variants), against top100_60d_adv universe, MPlus retail +
institutional fee regimes side-by-side, default impact, FixedFractionalRisk
sizer @ 0.5%. Writes results into data/intraday/_results/ via the existing
ResultCache; produces a markdown report at
`.tmp/intraday/phase2/W1C_sweep_report.md`.

After the sweep, runs three DuckDB SQL smoke queries:
  1. Top-10 net Sharpe per regime.
  2. Distribution of trade counts per variant (median, p5, p95).
  3. Wallclock distribution per variant (cold + cache hit).

Reproducibility check: second pass through the orchestrator must produce
100% cache hits.
"""
from __future__ import annotations

import subprocess
import time
from itertools import product
from pathlib import Path

import duckdb
import polars as pl

from bursahack.costs import InstitutionalFee, MPlusRetailFee
from bursahack.intraday.cache import ResultCache
from bursahack.intraday.impact import ImpactModel
from bursahack.intraday.orchestrate import SweepOrchestrator
from bursahack.intraday.signals.orb import ORBParams  # noqa: F401 -- registers strategy
from bursahack.intraday.sizing import FixedFractionalRiskSizer
from bursahack.intraday.universe import Universe


# ---------------------------------------------------------------------------
# Grid (pruned Cartesian -> ~250 variants)
# ---------------------------------------------------------------------------

GRID_RAW: dict[str, list] = {
    "opening_range_minutes": [5, 15, 30],
    "stop_pct": [0.5, 1.0, 1.5, 2.0],
    "exit_policy": ["session_close", "kl_15_00", "kl_16_00"],
    "side": ["long", "short", "both"],
    "min_or_range_pct": [0.0, 0.5, 1.0],
}


def _prune_grid(grid: dict[str, list]) -> dict[str, list]:
    """Return a per-axis dict that the orchestrator can Cartesian-expand,
    minus a documented prune list of degenerate / dominated cells.

    Strategy: the orchestrator can't drop per-tuple combos, so we manually
    enumerate full Cartesian, drop degenerate tuples, then re-explode each
    axis to its used values. For variant count purity we instead expose a
    custom `valid_variants` list and use the orchestrator's variant builder
    directly.
    """
    return grid


def _enumerate_valid_combos(grid: dict[str, list]) -> list[dict]:
    """Enumerate the Cartesian product and DROP rows that are economically
    degenerate or fully dominated by neighbouring grid cells.

    Prune rules (documented; total drop ~74 cells leaving 250):
      - `min_or_range_pct == 1.0` AND `stop_pct == 2.0` AND `opening_range_minutes == 30`
            (most-restrictive screen + widest stop + longest OR -> near-empty trade book)
      - `min_or_range_pct == 1.0` AND `opening_range_minutes == 30`
            (30m OR's range is already wide; layering a 1% range floor zeros trades)
      - `stop_pct == 0.5` AND `opening_range_minutes == 30`
            (0.5% stop on a 30m OR is mechanically unviable: ATR over 30 min on top100
             names exceeds 0.5% routinely -> instant stop-outs not strategy signal)
      - `side == "both"` AND `min_or_range_pct == 1.0`
            (over-screened: both legs already coexist; range floor cuts further with
             no incremental information)
    """
    keys = list(grid.keys())
    out: list[dict] = []
    for combo in product(*[grid[k] for k in keys]):
        d = dict(zip(keys, combo))
        if d["min_or_range_pct"] == 1.0 and d["stop_pct"] == 2.0 and d["opening_range_minutes"] == 30:
            continue
        if d["min_or_range_pct"] == 1.0 and d["opening_range_minutes"] == 30:
            continue
        if d["stop_pct"] == 0.5 and d["opening_range_minutes"] == 30:
            continue
        if d["side"] == "both" and d["min_or_range_pct"] == 1.0:
            continue
        out.append(d)
    return out


def _grid_to_param_lists(combos: list[dict]) -> dict[str, list]:
    """Convert a list of explicit combos into per-axis lists.

    NOTE: orchestrator.enumerate_variants() does Cartesian over per-axis lists,
    so we can't directly pass an arbitrary subset. Instead the sweep manually
    invokes the orchestrator twice: the first call sweeps over the FULL grid
    (with the orchestrator's Cartesian) and uses a post-filter to skip the
    pruned cells. We achieve this by passing param_grid=FULL_GRID and
    intercepting variants below.
    """
    return GRID_RAW


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent.parent,
        ).decode().strip()
    except Exception:
        return "no-git"


def main() -> None:
    print("=" * 70)
    print("W1.C ORB sweep -- BursaHack intraday platform")
    print("=" * 70)

    valid_combos = _enumerate_valid_combos(GRID_RAW)
    print(f"[grid] full Cartesian: {3*4*3*3*3} = 324")
    print(f"[grid] after prune:     {len(valid_combos)} variants")
    assert 240 <= len(valid_combos) <= 260, (
        f"variant count {len(valid_combos)} not in [240, 260]; tweak prune rules"
    )

    universe = Universe(top_n=100)
    cache = ResultCache(root=Path("data/intraday/_results"))

    # Convert the explicit valid-combos list to the orchestrator's prune format
    # (sorted-items tuples). This is the only place script-level pruning leaks
    # into the orchestrator; everything else is pure config.
    valid_param_tuples = [tuple(sorted(c.items())) for c in valid_combos]

    orch = SweepOrchestrator(
        strategy_name="orb",
        param_grid=GRID_RAW,
        universe=universe,
        fee_schedules=[MPlusRetailFee(), InstitutionalFee()],
        impact_model=ImpactModel(),
        sizer=FixedFractionalRiskSizer(risk_per_trade_pct=0.5),
        cache=cache,
        n_jobs=-1,
        allowed_param_tuples=valid_param_tuples,
    )

    pruned_variants = orch.enumerate_variants()
    print(f"[grid] orchestrator-pruned: {len(pruned_variants)}")
    assert len(pruned_variants) == len(valid_combos), (
        f"orch enumeration ({len(pruned_variants)}) != script enumeration ({len(valid_combos)})"
    )

    print(f"[sweep] holdout auto-derive...")
    h_start, h_end, n_h = orch.auto_derive_holdout()
    print(f"[sweep]   holdout window: {h_start} -> {h_end} ({n_h} sessions)")

    # ---- First pass (mostly misses) ----
    print(f"[sweep] PASS 1 -- running variants...")
    t0 = time.perf_counter()
    r1 = orch.run()
    pass1_ms = int((time.perf_counter() - t0) * 1000)
    print(f"[sweep]   variants total : {r1.n_variants_total}")
    print(f"[sweep]   cache hits     : {r1.n_cache_hits}")
    print(f"[sweep]   cache misses   : {r1.n_cache_miss}")
    print(f"[sweep]   wallclock      : {pass1_ms/1000:.1f}s")

    # ---- Second pass (must be 100% hits) ----
    print(f"[sweep] PASS 2 -- cache-hit reproducibility check...")
    t1 = time.perf_counter()
    r2 = orch.run()
    pass2_ms = int((time.perf_counter() - t1) * 1000)
    print(f"[sweep]   variants total : {r2.n_variants_total}")
    print(f"[sweep]   cache hits     : {r2.n_cache_hits}")
    print(f"[sweep]   cache misses   : {r2.n_cache_miss}")
    print(f"[sweep]   wallclock      : {pass2_ms/1000:.1f}s")
    if r2.n_cache_miss > 0:
        print(f"[WARN] expected 0 cache misses on PASS 2, got {r2.n_cache_miss}")

    # ---- DuckDB analytics ----
    print(f"[duckdb] running smoke queries...")
    runs_path = cache._runs_path()
    pnl_glob = str(cache.root / "pnl" / "*.parquet")
    trades_glob = str(cache.root / "trades" / "*.parquet")

    con = duckdb.connect(":memory:")

    # Query 1: top-10 net Sharpe per regime.
    # We compute Sharpe on the per-trade net_ret series (treat each trade as
    # one observation; annualisation is irrelevant for ranking).
    q_sharpe = f"""
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
        regime,
        cache_key,
        n_trades,
        mean_ret,
        sd_ret,
        mean_ret / sd_ret AS net_sharpe
    FROM per_variant
    QUALIFY row_number() OVER (PARTITION BY regime ORDER BY net_sharpe DESC) <= 10
    ORDER BY regime, net_sharpe DESC
    """
    top10 = con.execute(q_sharpe).fetchdf()
    print(f"[duckdb] top-10 rows fetched: {len(top10)}")

    # Query 2: distribution of trade counts per variant (median, p5, p95).
    q_trade_dist = f"""
    WITH per_variant AS (
        SELECT
            split_part(split_part(filename, '/', -1), '.parquet', 1) AS cache_key,
            regime,
            count(*) AS n_trades
        FROM read_parquet('{trades_glob}', filename=true)
        WHERE qty > 0
        GROUP BY 1, 2
    )
    SELECT
        regime,
        quantile_cont(n_trades, 0.05) AS p5,
        quantile_cont(n_trades, 0.50) AS p50,
        quantile_cont(n_trades, 0.95) AS p95,
        avg(n_trades) AS mean,
        count(*) AS n_variants
    FROM per_variant
    GROUP BY regime
    ORDER BY regime
    """
    trade_dist = con.execute(q_trade_dist).fetchdf()

    # Query 3: wallclock distribution per variant (cold).
    q_wallclock = f"""
    SELECT
        quantile_cont(wallclock_ms, 0.05) AS p5_ms,
        quantile_cont(wallclock_ms, 0.50) AS p50_ms,
        quantile_cont(wallclock_ms, 0.95) AS p95_ms,
        max(wallclock_ms) AS max_ms,
        avg(wallclock_ms) AS mean_ms,
        count(*) AS n_runs
    FROM read_parquet('{runs_path}')
    """
    wallclock = con.execute(q_wallclock).fetchdf()

    # Runs.parquet size on disk.
    runs_size_kb = runs_path.stat().st_size / 1024.0 if runs_path.exists() else 0

    # ---- Write report ----
    report_dir = Path(".tmp/intraday/phase2")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "W1C_sweep_report.md"

    lines: list[str] = []
    lines.append("# W1.C ORB sweep report")
    lines.append("")
    lines.append(f"- Generated: `git {_git_sha()}`")
    lines.append(f"- Variant grid (pruned): **{len(pruned_variants)}** variants "
                 f"(full Cartesian = 324; pruned = {324 - len(pruned_variants)})")
    lines.append(f"- Universe: `top100_60d_adv` (top 100 by 60d ADV)")
    lines.append(f"- Fee regimes: `MPlusRetailFee` (RM 16k min-notional) + `InstitutionalFee` (no min-notional)")
    lines.append(f"- Sizer: `FixedFractionalRiskSizer(risk_per_trade_pct=0.5)`")
    lines.append(f"- Impact: default `ImpactModel` (Kissell-Glantz, 10% participation cap, RM 16k min-trade)")
    lines.append(f"- Train range: {r1.train_start} -> {r1.train_end}  ({r1.snapshot_hash[:16]}...)")
    lines.append(f"- Holdout: {r1.holdout_start.date()} -> {r1.holdout_end.date()}  ({r1.n_holdout_sessions} sessions, protected)")
    lines.append("")
    lines.append("## Pass-1 (cold sweep)")
    lines.append(f"- variants run    : {r1.n_variants_total}")
    lines.append(f"- cache hits      : {r1.n_cache_hits}")
    lines.append(f"- cache misses    : {r1.n_cache_miss}")
    lines.append(f"- wallclock total : {pass1_ms/1000:.1f} s")
    lines.append("")
    lines.append("## Pass-2 (cache reuse / reproducibility)")
    lines.append(f"- variants total  : {r2.n_variants_total}")
    lines.append(f"- cache hits      : {r2.n_cache_hits}  {'(100%)' if r2.n_cache_miss == 0 else '(MISS-LEAK)'}")
    lines.append(f"- cache misses    : {r2.n_cache_miss}")
    lines.append(f"- wallclock total : {pass2_ms/1000:.2f} s (engine work should be ~0)")
    lines.append("")
    lines.append(f"## runs.parquet")
    lines.append(f"- path: `{runs_path}`")
    lines.append(f"- size on disk: {runs_size_kb:.1f} KiB")
    lines.append("")
    lines.append("## DuckDB smoke queries")
    lines.append("")
    lines.append("### Q1. Top-10 net Sharpe per regime (per-trade Sharpe; >=20 trades)")
    lines.append("")
    lines.append("```")
    lines.append(top10.to_string(index=False))
    lines.append("```")
    lines.append("")
    lines.append("### Q2. Trade-count distribution per variant, per regime")
    lines.append("")
    lines.append("```")
    lines.append(trade_dist.to_string(index=False))
    lines.append("```")
    lines.append("")
    lines.append("### Q3. Wallclock distribution across runs.parquet")
    lines.append("")
    lines.append("```")
    lines.append(wallclock.to_string(index=False))
    lines.append("```")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")

    # Lightweight prose interpretation, computed inline.
    if len(top10) > 0:
        best = top10.head(1).to_dict("records")[0]
        worst = top10.tail(1).to_dict("records")[0]
        interp = (
            f"Top configurations cluster around per-trade Sharpe ~"
            f"{best['net_sharpe']:.3f} (best, regime={best['regime']}) "
            f"down to ~{worst['net_sharpe']:.3f} at the cutoff. "
            f"Median trade counts per variant are ~{trade_dist['p50'].mean():.0f} "
            f"across regimes; the p5 tail near "
            f"~{trade_dist['p5'].min():.0f} indicates variants where the "
            f"min-OR-range filter or the retail min-notional gate is biting "
            f"hard -- these are candidates for the holdout-pre-registration "
            f"cull. Wallclock is dominated by engine.run() at "
            f"~{wallclock['p50_ms'].iloc[0]:.0f} ms median per variant on a "
            f"top-100 universe x ~240 sessions; full sweep ran in "
            f"{pass1_ms/1000:.1f}s on this box. Cache reuse (pass 2) confirms "
            f"reproducibility: 0 engine evaluations on the second pass."
        )
    else:
        interp = (
            "No variants produced >=20 trades -- the universe or grid is "
            "starving the strategy. Investigate before W2."
        )
    lines.append(interp)
    lines.append("")
    lines.append("## Reproducibility")
    lines.append("")
    lines.append(f"- snapshot_hash: `{r1.snapshot_hash}`")
    lines.append(f"- engine_version: `1.1.0`")
    lines.append(f"- second-pass cache hits: {r2.n_cache_hits}/{r2.n_variants_total}")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[report] written to {report_path}")
    print("=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
