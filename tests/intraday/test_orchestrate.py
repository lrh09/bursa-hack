"""Orchestrator smoke + holdout-auto-derive + cache-reuse tests.

These tests need the on-disk parquet store, but only read a 5-session
slice so they're cheap. If the store is missing (CI / fresh clone), tests
skip with a clear reason.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from bursahack.costs import MPlusRetailFee
from bursahack.intraday.cache import ResultCache
from bursahack.intraday.calendar import ExchangeCalendar
from bursahack.intraday.engine import HOLDOUT_TRADING_DAYS
from bursahack.intraday.impact import ImpactModel
from bursahack.intraday.loader import get_data_range
from bursahack.intraday.orchestrate import SweepOrchestrator
from bursahack.intraday.sizing import FixedFractionalRiskSizer
from bursahack.intraday.universe import Universe

# Side-effect: register the ORB strategy.
import bursahack.intraday.signals.orb  # noqa: F401


def _store_available() -> bool:
    try:
        get_data_range()
        return True
    except FileNotFoundError:
        return False


pytestmark = pytest.mark.skipif(
    not _store_available(),
    reason="hive parquet store not available -- run scripts/migrate_to_hive.py first",
)


def _small_universe() -> Universe:
    # top-5 keeps the bar frame ~5x smaller than the production sweep.
    return Universe(top_n=5, lookback_days=30)


def test_holdout_auto_derive_matches_calendar():
    """Orchestrator's holdout_start MUST equal sessions[-43] from the calendar."""
    orch = SweepOrchestrator(
        strategy_name="orb",
        param_grid={"opening_range_minutes": [5]},
        universe=_small_universe(),
        fee_schedules=[MPlusRetailFee()],
        impact_model=ImpactModel(),
        sizer=FixedFractionalRiskSizer(),
        cache=ResultCache(),
    )
    h_start, h_end, n = orch.auto_derive_holdout()
    assert n == HOLDOUT_TRADING_DAYS

    min_ts, max_ts = get_data_range()
    cal = ExchangeCalendar("XKLS")
    sessions = cal.sessions(min_ts.date(), max_ts.date())
    expected_first_holdout = sessions[-HOLDOUT_TRADING_DAYS]
    assert h_start.date() == expected_first_holdout, (
        f"expected holdout_start {expected_first_holdout}, got {h_start.date()}"
    )


def test_2variant_smoke_and_cache_reuse(tmp_path: Path):
    """End-to-end: 2 ORB variants on a 5-session slice; second run = full cache."""
    # 5-session slice, mid-data, well outside the holdout.
    start = date(2021, 3, 1)
    end = date(2021, 3, 5)

    cache = ResultCache(root=tmp_path / "_results")
    orch = SweepOrchestrator(
        strategy_name="orb",
        param_grid={
            "opening_range_minutes": [5, 15],
            "stop_pct": [1.0],
            "exit_policy": ["session_close"],
            "side": ["long"],
            "min_or_range_pct": [0.0],
        },
        universe=_small_universe(),
        fee_schedules=[MPlusRetailFee()],
        impact_model=ImpactModel(),
        sizer=FixedFractionalRiskSizer(risk_per_trade_pct=0.5),
        cache=cache,
        start=start,
        end=end,
        n_jobs=1,   # serial -> easier debug + faster startup on small sweep
    )

    variants = orch.enumerate_variants()
    assert len(variants) == 2, f"expected 2 variants, got {len(variants)}"

    # First run -- both cache misses, both engines fire.
    r1 = orch.run()
    assert r1.n_variants_total == 2
    assert r1.n_cache_miss == 2
    assert r1.n_cache_hits == 0
    # runs.parquet has at least 2 rows (might have more across regimes if any
    # other code wrote earlier in the same root; we use tmp_path so should be 2).
    runs = pl.read_parquet(cache._runs_path())
    assert runs.height == 2, f"runs.parquet should have 2 rows, got {runs.height}"

    # Second run -- both cache hits, no engine work.
    r2 = orch.run()
    assert r2.n_variants_total == 2
    assert r2.n_cache_hits == 2
    assert r2.n_cache_miss == 0
    # No engine work means sum of cache_miss wallclocks is 0.
    miss_ms = sum(o.wallclock_ms for o in r2.outcomes if not o.cache_hit)
    assert miss_ms == 0
