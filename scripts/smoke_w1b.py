"""W1.B smoke run: one ORB variant, top-100 universe, 20-session slice.

Confirms end-to-end wiring of registry + ORB + engine + cache. NOT a
sweep -- the orchestrator is W1.C.
"""
from __future__ import annotations

import subprocess
import time
from datetime import date
from pathlib import Path

import polars as pl

from bursahack.costs import InstitutionalFee, MPlusRetailFee
from bursahack.intraday.cache import ResultCache
from bursahack.intraday.calendar import ExchangeCalendar
from bursahack.intraday.engine import (
    ENGINE_VERSION,
    IntradayEngine,
    cost_regimes_sha,
    impact_sha,
)
from bursahack.intraday.impact import ImpactModel
from bursahack.intraday.loader import load_bars, snapshot_hash
from bursahack.intraday.registry import get_strategy
from bursahack.intraday.signals.orb import ORBParams  # noqa: F401 -- side-effect register
from bursahack.intraday.universe import Universe


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent.parent,
        ).decode().strip()
    except Exception:
        return "no-git"


def main() -> None:
    # 20-session slice in mid-data (well outside the last-43-day holdout).
    start = date(2021, 3, 1)
    end = date(2021, 3, 31)

    print(f"[smoke] window: {start} -> {end}")
    cal = ExchangeCalendar("XKLS")
    sessions = cal.sessions(start, end)
    print(f"[smoke] sessions: {len(sessions)}")

    universe = Universe(top_n=100)
    members = universe.members_for_range(start, end)
    print(f"[smoke] members rows: {members.height}")
    universe_sha = universe._spec_key()

    snap = snapshot_hash(start, end)
    print(f"[smoke] snapshot_hash: {snap[:16]}...")

    bars = load_bars(start, end, universe=universe, freq="1m").collect()
    print(f"[smoke] bars rows: {bars.height}, codes: {bars.get_column('code').n_unique()}")

    spec = get_strategy("orb")
    strat = spec.cls()
    params = spec.params_model()  # defaults
    sigs = strat.generate_signals(bars=bars, universe_members=members, params=params)
    print(f"[smoke] signals: {sigs.height}")

    impact = ImpactModel()
    regimes = [MPlusRetailFee(), InstitutionalFee()]
    engine = IntradayEngine(cost_regimes=regimes)

    cache = ResultCache(root=Path("data/intraday/_results"))
    key = cache.compute_key(
        spec=spec, params=params, snapshot_hash=snap,
        universe_sha=universe_sha,
        impact_model_sha=impact_sha(impact),
        cost_regimes_sha_val=cost_regimes_sha(regimes),
        engine_version=ENGINE_VERSION,
    )
    print(f"[smoke] cache key: {key[:16]}...")

    if cache.has(key):
        print("[smoke] WARN: key already exists; deleting for clean smoke run")
        cache._pnl_path(key).unlink(missing_ok=True)
        cache._trades_path(key).unlink(missing_ok=True)
        # leave runs.parquet alone -- put is idempotent so no dupe row

    t0 = time.perf_counter()
    res = engine.run(
        bars=bars, signals=sigs, universe_members=members,
        snapshot_hash=snap, git_sha=_git_sha(),
        signal_version=spec.version, params=params, universe_sha=universe_sha,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    print(f"[smoke] engine.run wallclock: {elapsed_ms}ms (engine reports {res.metadata.wallclock_ms}ms)")
    print(f"[smoke] n_trades (per regime): {res.metadata.n_trades}")
    for regime_name, pnl in res.pnl.items():
        if pnl.height:
            final_eq = pnl.get_column("equity")[-1]
        else:
            final_eq = engine.starting_equity
        print(f"[smoke]   regime={regime_name} final_equity={final_eq:.2f}")

    cache.put(key, res)
    print(f"[smoke] cached at {cache._pnl_path(key)}")

    # Reload from cache -- must hit.
    t1 = time.perf_counter()
    rt = cache.get(key)
    elapsed_reload = int((time.perf_counter() - t1) * 1000)
    assert rt is not None, "cache miss on reload -- bug"
    print(f"[smoke] reload wallclock: {elapsed_reload}ms (cache HIT)")
    print(f"[smoke] reload trades: {rt.trades.height}")

    # Confirm runs.parquet has the row.
    runs = pl.read_parquet(cache._runs_path())
    print(f"[smoke] runs.parquet rows: {runs.height}")
    matched = runs.filter(pl.col("cache_key") == key).height
    print(f"[smoke] runs.parquet rows for THIS key: {matched}")

    assert elapsed_ms < 60_000, f"wallclock budget breached: {elapsed_ms}ms"
    print("[smoke] OK")


if __name__ == "__main__":
    main()
