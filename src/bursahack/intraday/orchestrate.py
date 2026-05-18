"""Sweep orchestrator: param-grid -> cached BacktestResults via joblib loky.

Design contract:
  - One `SweepOrchestrator` instance describes EVERY input that determines the
    sweep's output: strategy, param grid, universe, fee_schedules, impact,
    sizer, freq, date range, n_jobs, cache root. All are hashable / serialisable
    so the worker processes can reconstruct them.
  - `enumerate_variants()` yields (variant_id, params) for the Cartesian
    product of `param_grid`. `variant_id` is the params_sha (first 16 chars)
    -- stable across runs, fine for cache lookup.
  - `run()`:
      1. Auto-derives the holdout window from loader.get_data_range() + the
         calendar; refuses any signal touching it. Records 43 sessions.
      2. Materializes universe members for [start, end] ONCE.
      3. Computes sigma + ADV tables ONCE (cached).
      4. Loads bars for [start, end] ONCE (collected DataFrame).
      5. For each variant: check cache; if hit, skip; if miss, dispatch the
         engine to a worker.
  - Worker contract: takes the materialized bars (round-trip a parquet
    snapshot path is the scalable shape, but for W1.C we pass the DataFrame
    directly since loky pickles it efficiently per task -- bar frames here
    are ~50-100 MB max). Returns the BacktestResult; the parent process
    writes it to the cache to keep `runs.parquet` writes serialised.
  - Determinism: identical sweep config = identical cache keys = full hit on
    second run. The sweep wallclock on a hot cache is dominated by
    `cache.has(key)` lookups, not engine runs.

Scalability note (deferred to Ray, W2):
  - joblib loky is the right shape for ~10^2 - 10^3 tasks. For 10^5+ tasks
    (CPCV x families), Ray's Plasma object store avoids re-pickling the
    bar frame per worker. Swap-in point is `_dispatch_variant` -- keep it
    pure-function over the closure of (bars, sigma, adv, members, ...).
"""
from __future__ import annotations

import time
from datetime import date as _date
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path
from typing import Any, Iterable

import polars as pl
from joblib import Parallel, delayed
from pydantic import BaseModel, ConfigDict, Field

from bursahack.costs import FeeSchedule
from bursahack.intraday.cache import ResultCache
from bursahack.intraday.calendar import ExchangeCalendar
from bursahack.intraday.engine import (
    ENGINE_VERSION,
    HOLDOUT_TRADING_DAYS,
    IntradayEngine,
    cost_regimes_sha,
    hash_payload,
    impact_sha,
    params_sha,
)
from bursahack.intraday.features import (
    compute_adv_bar_table,
    compute_sigma_table,
    features_input_sha,
)
from bursahack.intraday.impact import ImpactModel
from bursahack.intraday.loader import get_data_range, load_bars, snapshot_hash
from bursahack.intraday.registry import StrategySpec, get_strategy
from bursahack.intraday.sizing import FixedFractionalRiskSizer, Sizer
from bursahack.intraday.universe import Universe


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class VariantOutcome(BaseModel):
    """One row in a SweepResult: which variant, did it hit the cache, how long."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    variant_id: str
    cache_key: str
    cache_hit: bool
    wallclock_ms: int


class SweepResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    outcomes: list[VariantOutcome]
    holdout_start: datetime
    holdout_end: datetime
    n_holdout_sessions: int
    train_start: _date
    train_end: _date
    snapshot_hash: str
    n_variants_total: int
    n_cache_hits: int
    n_cache_miss: int
    total_wallclock_ms: int


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class SweepOrchestrator(BaseModel):
    """Drive a parameter sweep over one strategy family.

    See module docstring for the contract. Public surface is exactly
    `enumerate_variants()` + `run()`.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    strategy_name: str
    param_grid: dict[str, list[Any]]
    universe: Universe
    fee_schedules: list[FeeSchedule]
    impact_model: ImpactModel = Field(default_factory=ImpactModel)
    sizer: Sizer = Field(default_factory=FixedFractionalRiskSizer)
    freq: str = "1m"
    start: _date | None = None
    end: _date | None = None
    n_jobs: int = -1
    cache: ResultCache = Field(default_factory=ResultCache)
    starting_equity: float = 100_000.0
    max_position_pct_adv: float = 0.10
    sigma_window_bars: int = 20
    adv_window_days: int = 20
    # Optional explicit allow-list of params dicts. When set, the Cartesian
    # product from `param_grid` is filtered down to only these tuples. Used
    # by sweep scripts to prune degenerate cells (see scripts/sweep_orb_w1c.py).
    allowed_param_tuples: list[tuple] | None = None

    # --------------------------- helpers ---------------------------

    @property
    def spec(self) -> StrategySpec:
        return get_strategy(self.strategy_name)

    def enumerate_variants(self) -> list[tuple[str, BaseModel]]:
        """Yield (variant_id, params_instance) for every cell in `param_grid`.

        If `allowed_param_tuples` is set, only combos whose sorted-items tuple
        is in that allow-list are returned. This is the prune hook for sweep
        scripts that want to drop degenerate cells without rewriting the grid.
        """
        keys = sorted(self.param_grid.keys())
        values = [self.param_grid[k] for k in keys]
        out: list[tuple[str, BaseModel]] = []
        spec = self.spec
        allow = self.allowed_param_tuples
        for combo in product(*values):
            kwargs = dict(zip(keys, combo))
            if allow is not None:
                key = tuple(sorted(kwargs.items()))
                if key not in allow:
                    continue
            try:
                params = spec.params_model(**kwargs)
            except Exception:
                # Skip combos the params_model rejects (e.g. Literal constraints).
                continue
            vid = params_sha(params)[:16]
            out.append((vid, params))
        return out

    def auto_derive_holdout(self) -> tuple[datetime, datetime, int]:
        """Compute the (holdout_start, holdout_end, n_sessions) tuple.

        Reads `loader.get_data_range()` -> sessions over that range from the
        calendar -> last `HOLDOUT_TRADING_DAYS` sessions.
        """
        min_ts, max_ts = get_data_range(exchange=self.universe.exchange)
        cal = ExchangeCalendar(self.universe.exchange)
        sessions = cal.sessions(min_ts.date(), max_ts.date())
        if len(sessions) < HOLDOUT_TRADING_DAYS + 5:
            raise ValueError(
                f"only {len(sessions)} sessions available; need at least "
                f"{HOLDOUT_TRADING_DAYS + 5} to define a holdout."
            )
        holdout_first_session = sessions[-HOLDOUT_TRADING_DAYS]
        # Inclusive holdout window in UTC-naive datetimes.
        # holdout_start = KL midnight of the first holdout session, expressed UTC.
        # We keep things simple and use UTC midnight as the lower bound which
        # is conservative (a few hours earlier than the KL session open) -- any
        # bar / signal within the protected sessions still triggers the guard.
        holdout_start = datetime.combine(holdout_first_session, datetime.min.time())
        holdout_end = max_ts
        return holdout_start, holdout_end, HOLDOUT_TRADING_DAYS

    def _effective_range(self) -> tuple[_date, _date, datetime, datetime, int]:
        """Resolve (train_start, train_end, holdout_start, holdout_end, n)."""
        holdout_start, holdout_end, n_holdout = self.auto_derive_holdout()
        # Default start = data start.
        min_ts, _max_ts = get_data_range(exchange=self.universe.exchange)
        train_start = self.start or min_ts.date()
        # Default end = first holdout session - 1 day (calendar-naive).
        if self.end is not None:
            train_end = self.end
        else:
            train_end = (holdout_start - timedelta(days=1)).date()
        return train_start, train_end, holdout_start, holdout_end, n_holdout

    # ----------------------------- run -----------------------------

    def run(self) -> SweepResult:
        t_total = time.perf_counter()
        train_start, train_end, holdout_start, holdout_end, n_holdout = self._effective_range()

        snap = snapshot_hash(train_start, train_end)
        uni_sha = self.universe._spec_key()

        # 1) universe members.
        members = self.universe.members_for_range(train_start, train_end)

        # 2) bars for [train_start, train_end].
        bars = load_bars(
            train_start, train_end, universe=self.universe, freq=self.freq,
        ).collect()

        # 3) features (cached).
        f_sha = features_input_sha(
            snapshot_hash=snap,
            universe_sha=uni_sha,
            freq=self.freq,
            window=self.sigma_window_bars,
        )
        sigma_table = compute_sigma_table(
            bars, window_bars=self.sigma_window_bars, input_sha=f_sha,
        )
        adv_table = compute_adv_bar_table(
            bars, window_days=self.adv_window_days, input_sha=f_sha,
        )

        variants = self.enumerate_variants()
        spec = self.spec

        # 4) Split into cache-hits (skip) and cache-misses (dispatch).
        to_run: list[tuple[str, BaseModel, str]] = []
        outcomes: list[VariantOutcome] = []
        for vid, params in variants:
            ckey = self.cache.compute_key(
                spec=spec, params=params, snapshot_hash=snap,
                universe_sha=uni_sha,
                impact_model_sha=impact_sha(self.impact_model),
                cost_regimes_sha_val=cost_regimes_sha(self.fee_schedules),
                engine_version=ENGINE_VERSION,
            )
            if self.cache.has(ckey):
                outcomes.append(VariantOutcome(
                    variant_id=vid, cache_key=ckey, cache_hit=True, wallclock_ms=0,
                ))
            else:
                to_run.append((vid, params, ckey))

        # 5) Dispatch misses to joblib.
        if to_run:
            jobs = (
                delayed(_dispatch_variant)(
                    vid=vid,
                    params=params,
                    ckey=ckey,
                    spec_name=self.strategy_name,
                    bars=bars,
                    members=members,
                    sigma_table=sigma_table,
                    adv_table=adv_table,
                    fee_schedules=self.fee_schedules,
                    impact_model=self.impact_model,
                    sizer=self.sizer,
                    holdout_start=holdout_start,
                    holdout_end=holdout_end,
                    snapshot_hash_val=snap,
                    universe_sha=uni_sha,
                    starting_equity=self.starting_equity,
                    max_position_pct_adv=self.max_position_pct_adv,
                )
                for (vid, params, ckey) in to_run
            )
            results = Parallel(n_jobs=self.n_jobs, backend="loky", verbose=0)(jobs)
            # 6) Serialised cache writes.
            for r in results:
                self.cache.put(r["ckey"], r["result"])
                outcomes.append(VariantOutcome(
                    variant_id=r["vid"], cache_key=r["ckey"],
                    cache_hit=False, wallclock_ms=r["wallclock_ms"],
                ))

        total_ms = int((time.perf_counter() - t_total) * 1000)
        n_hit = sum(1 for o in outcomes if o.cache_hit)
        return SweepResult(
            outcomes=outcomes,
            holdout_start=holdout_start,
            holdout_end=holdout_end,
            n_holdout_sessions=n_holdout,
            train_start=train_start,
            train_end=train_end,
            snapshot_hash=snap,
            n_variants_total=len(outcomes),
            n_cache_hits=n_hit,
            n_cache_miss=len(outcomes) - n_hit,
            total_wallclock_ms=total_ms,
        )


# ---------------------------------------------------------------------------
# Worker function (top-level for joblib pickling)
# ---------------------------------------------------------------------------


def _dispatch_variant(
    vid: str,
    params: BaseModel,
    ckey: str,
    spec_name: str,
    bars: pl.DataFrame,
    members: pl.DataFrame,
    sigma_table: pl.DataFrame,
    adv_table: pl.DataFrame,
    fee_schedules: list[FeeSchedule],
    impact_model: ImpactModel,
    sizer: Sizer,
    holdout_start: datetime,
    holdout_end: datetime,
    snapshot_hash_val: str,
    universe_sha: str,
    starting_equity: float,
    max_position_pct_adv: float,
) -> dict:
    """Worker: build strategy + engine, run, return BacktestResult.

    Top-level so loky can pickle it. The engine's `run()` writes nothing to
    disk; the parent process owns the cache write so `runs.parquet` updates
    stay serialised.
    """
    t0 = time.perf_counter()
    # Re-import inside the worker to be defensive against fork-spawn differences.
    # (Side-effect: also re-registers strategies.)
    import bursahack.intraday.signals.orb  # noqa: F401

    spec = get_strategy(spec_name)
    strat = spec.cls()
    signals = strat.generate_signals(bars=bars, universe_members=members, params=params)

    engine = IntradayEngine(
        cost_regimes=fee_schedules,
        sizer=sizer,
        starting_equity=starting_equity,
        max_position_pct_adv=max_position_pct_adv,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
    )
    result = engine.run(
        bars=bars,
        signals=signals,
        universe_members=members,
        impact=impact_model,
        sigma_table=sigma_table,
        adv_table=adv_table,
        snapshot_hash=snapshot_hash_val,
        signal_version=spec.version,
        params=params,
        universe_sha=universe_sha,
    )
    wallclock_ms = int((time.perf_counter() - t0) * 1000)
    return {"vid": vid, "ckey": ckey, "result": result, "wallclock_ms": wallclock_ms}


__all__ = [
    "SweepOrchestrator",
    "SweepResult",
    "VariantOutcome",
]
