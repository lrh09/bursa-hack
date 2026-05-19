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
      2. Materializes universe members for [start, end] ONCE; writes to a
         per-sweep parquet so workers can re-read on demand.
      3. Computes sigma + ADV tables ONCE (cached to feature parquets).
      4. Dispatches variants. Workers receive ONLY config dicts + paths;
         they re-scan parquet with their own filter and never inherit a
         pickled bar frame from the parent (Phase A blocker #2).
      5. For each variant: check cache; if hit, skip; if miss, dispatch the
         engine to a worker via `_dispatch_variant_from_paths`.
  - Worker contract (Phase B.0 F2): the worker receives a `_VariantTask`
    dataclass that's a pure-data envelope: parquet glob, date range,
    universe-members path, sigma-table path, adv-table path, fee_schedules
    + impact_model + sizer serialised as pydantic dumps, params dict +
    strategy name. The worker does its own `pl.scan_parquet(...).filter(...)
    .collect(engine="streaming")` so RSS stays flat at ~2-3 GB per worker.
  - Per-variant progress logging (Phase B.0 F3): start + finish lines
    flushed to stdout, results appended to a per-run log parquet for
    crash-safe partial state, rolling ETA emit every 10 completions.
  - Determinism: identical sweep config = identical cache keys = full hit on
    second run. The sweep wallclock on a hot cache is dominated by
    `cache.has(key)` lookups, not engine runs.

Scalability note (still deferred to Ray, W2):
  - joblib loky now scales because we don't pickle 2-3 GB bar frames per
    task. Each worker re-scans parquet (streaming engine) for its slice.
    For 10^5+ tasks (CPCV x families), Ray's Plasma object store would
    avoid even the per-worker scan startup; W2 swap-in point unchanged.
"""
from __future__ import annotations

import hashlib
import os
import statistics
import time
import uuid
from datetime import date as _date
from datetime import datetime, timedelta, timezone
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

    def _run_log_dir(self) -> Path:
        from bursahack.paths import REPO_ROOT
        return REPO_ROOT / "data" / "intraday" / "_results" / "_run_log"

    def run(self, sweep_id: str | None = None) -> SweepResult:
        """Execute the sweep.

        Args:
          sweep_id: optional run-log slug. If None, a uuid4-based ID is
                    generated. Used as the filename stem under
                    `data/intraday/_results/_run_log/<sweep_id>.parquet`
                    where per-variant outcomes are append-written as they
                    complete (crash-safe partial state).
        """
        t_total = time.perf_counter()
        train_start, train_end, holdout_start, holdout_end, n_holdout = self._effective_range()

        snap = snapshot_hash(train_start, train_end)
        uni_sha = self.universe._spec_key()

        sweep_id = sweep_id or f"{self.strategy_name}_{uuid.uuid4().hex[:12]}"
        run_log_dir = self._run_log_dir()
        run_log_dir.mkdir(parents=True, exist_ok=True)
        run_log_path = run_log_dir / f"{sweep_id}.parquet"

        # 1) universe members.
        members = self.universe.members_for_range(train_start, train_end)

        # 2) Stage materialised members to a per-sweep parquet so workers can
        # re-read instead of receiving a pickled frame.
        staging_dir = self._run_log_dir().parent / "_staging" / sweep_id
        staging_dir.mkdir(parents=True, exist_ok=True)
        members_path = staging_dir / "members.parquet"
        members.write_parquet(members_path)

        # 3) Bars: parent loads ONCE for the features step, then we DROP the
        # frame before dispatching workers (workers re-scan parquet themselves).
        # Use force_streaming on multi-year windows (handled by loader auto).
        bars = load_bars(
            train_start, train_end, universe=self.universe, freq=self.freq,
        ).collect()

        # 4) features (cached to feature parquets).
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
        # Resolve feature parquet paths so workers re-read them.
        from bursahack.intraday.features import _default_features_root
        feat_root = _default_features_root()
        sigma_path = feat_root / f"sigma_{f_sha}.parquet"
        adv_path = feat_root / f"adv_{f_sha}.parquet"
        # If compute_*_table didn't write because input_sha=None on legacy call,
        # write here as a fallback so workers have something to read.
        if not sigma_path.exists():
            sigma_path.parent.mkdir(parents=True, exist_ok=True)
            sigma_table.write_parquet(sigma_path)
        if not adv_path.exists():
            adv_path.parent.mkdir(parents=True, exist_ok=True)
            adv_table.write_parquet(adv_path)

        # 5) DROP the parent's bar frame to release memory before dispatch.
        # Workers will re-scan parquet (streaming engine) for their slice.
        del bars

        variants = self.enumerate_variants()
        spec = self.spec

        # 6) Split into cache-hits (skip) and cache-misses (dispatch).
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

        # 7) Serialise reusable worker-side config payloads ONCE.
        fee_schedules_payload = [
            {"cls": type(r).__module__ + "." + type(r).__name__, "data": r.model_dump()}
            for r in self.fee_schedules
        ]
        impact_payload = {
            "cls": type(self.impact_model).__module__ + "." + type(self.impact_model).__name__,
            "data": self.impact_model.model_dump(),
        }
        sizer_payload = {
            "cls": type(self.sizer).__module__ + "." + type(self.sizer).__name__,
            "data": self.sizer.model_dump(),
        }

        # 8) Dispatch misses to joblib.
        total_to_run = len(to_run)
        total_with_hits = len(variants)
        print(
            f"[orch] sweep_id={sweep_id} variants={total_with_hits} "
            f"miss={total_to_run} hit={total_with_hits - total_to_run}",
            flush=True,
        )

        wallclock_history: list[int] = []

        if to_run:
            n_jobs = self.n_jobs
            if n_jobs == -1:
                n_jobs = min(os.cpu_count() or 1, 12)

            if n_jobs == 1:
                # Sequential path: per-variant progress logging happens
                # synchronously and `_append_run_log` is cheap.
                for i, (vid, params, ckey) in enumerate(to_run):
                    print(f"[{i+1}/{total_to_run}] variant {vid} START", flush=True)
                    r = _dispatch_variant_from_paths(
                        vid=vid,
                        params_data=params.model_dump(),
                        params_cls=type(params).__module__ + "." + type(params).__name__,
                        ckey=ckey,
                        spec_name=self.strategy_name,
                        parquet_glob=_default_parquet_glob(),
                        train_start_iso=train_start.isoformat(),
                        train_end_iso=train_end.isoformat(),
                        universe_members_path=str(members_path),
                        sigma_table_path=str(sigma_path),
                        adv_table_path=str(adv_path),
                        freq=self.freq,
                        fee_schedules_payload=fee_schedules_payload,
                        impact_payload=impact_payload,
                        sizer_payload=sizer_payload,
                        holdout_start_iso=holdout_start.isoformat(),
                        holdout_end_iso=holdout_end.isoformat(),
                        snapshot_hash_val=snap,
                        universe_sha=uni_sha,
                        starting_equity=self.starting_equity,
                        max_position_pct_adv=self.max_position_pct_adv,
                    )
                    self.cache.put(r["ckey"], r["result"])
                    outcomes.append(VariantOutcome(
                        variant_id=r["vid"], cache_key=r["ckey"],
                        cache_hit=False, wallclock_ms=r["wallclock_ms"],
                    ))
                    wallclock_history.append(r["wallclock_ms"])
                    print(
                        f"[{i+1}/{total_to_run}] variant {vid} DONE in "
                        f"{r['wallclock_ms']}ms (cache_hit=False)",
                        flush=True,
                    )
                    _append_run_log(run_log_path, vid, ckey, r["wallclock_ms"])
                    if (i + 1) % 10 == 0 and wallclock_history:
                        _log_eta(i + 1, total_to_run, wallclock_history, t_total)
            else:
                # Parallel path. Workers don't see the parent's frames; only
                # config dicts + paths. RSS stays flat at ~2-3 GB per worker.
                jobs = (
                    delayed(_dispatch_variant_from_paths)(
                        vid=vid,
                        params_data=params.model_dump(),
                        params_cls=type(params).__module__ + "." + type(params).__name__,
                        ckey=ckey,
                        spec_name=self.strategy_name,
                        parquet_glob=_default_parquet_glob(),
                        train_start_iso=train_start.isoformat(),
                        train_end_iso=train_end.isoformat(),
                        universe_members_path=str(members_path),
                        sigma_table_path=str(sigma_path),
                        adv_table_path=str(adv_path),
                        freq=self.freq,
                        fee_schedules_payload=fee_schedules_payload,
                        impact_payload=impact_payload,
                        sizer_payload=sizer_payload,
                        holdout_start_iso=holdout_start.isoformat(),
                        holdout_end_iso=holdout_end.isoformat(),
                        snapshot_hash_val=snap,
                        universe_sha=uni_sha,
                        starting_equity=self.starting_equity,
                        max_position_pct_adv=self.max_position_pct_adv,
                    )
                    for (vid, params, ckey) in to_run
                )
                # We dispatch in chunks of n_jobs so we can interleave
                # cache writes + progress logs. joblib's `return_as="generator"`
                # would be ideal but is version-fragile; chunked dispatch is
                # equivalent and lock-step.
                chunk_size = max(n_jobs, 1)
                completed = 0
                jobs = list(jobs)
                for chunk_start in range(0, len(jobs), chunk_size):
                    chunk = jobs[chunk_start: chunk_start + chunk_size]
                    print(
                        f"[orch] dispatching variants "
                        f"{chunk_start+1}..{chunk_start+len(chunk)} of {total_to_run}",
                        flush=True,
                    )
                    chunk_results = Parallel(
                        n_jobs=n_jobs, backend="loky", verbose=0
                    )(chunk)
                    for r in chunk_results:
                        self.cache.put(r["ckey"], r["result"])
                        outcomes.append(VariantOutcome(
                            variant_id=r["vid"], cache_key=r["ckey"],
                            cache_hit=False, wallclock_ms=r["wallclock_ms"],
                        ))
                        wallclock_history.append(r["wallclock_ms"])
                        completed += 1
                        print(
                            f"[{completed}/{total_to_run}] variant {r['vid']} DONE in "
                            f"{r['wallclock_ms']}ms (cache_hit=False)",
                            flush=True,
                        )
                        _append_run_log(run_log_path, r["vid"], r["ckey"], r["wallclock_ms"])
                        if completed % 10 == 0 and wallclock_history:
                            _log_eta(completed, total_to_run, wallclock_history, t_total)

        total_ms = int((time.perf_counter() - t_total) * 1000)
        n_hit = sum(1 for o in outcomes if o.cache_hit)
        print(
            f"[orch] sweep_id={sweep_id} DONE wallclock={total_ms}ms "
            f"({total_ms/1000:.1f}s)  hits={n_hit}/{len(outcomes)}",
            flush=True,
        )
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
# Helpers (top-level for joblib pickling)
# ---------------------------------------------------------------------------


def _default_parquet_glob() -> str:
    """The hive-store glob that workers re-scan."""
    from bursahack.paths import REPO_ROOT
    return str(
        REPO_ROOT / "data" / "intraday" / "exchange=*" / "year=*" / "month=*" / "*.parquet"
    )


def _import_attr(dotted: str) -> Any:
    """Resolve `module.path.Class` -> class object."""
    mod_name, _, attr = dotted.rpartition(".")
    if not mod_name:
        raise ValueError(f"bad dotted name: {dotted!r}")
    import importlib
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr)


def _append_run_log(path: Path, vid: str, ckey: str, wallclock_ms: int) -> None:
    """Append-write one outcome row to the crash-safe run-log parquet.

    Polars doesn't support true parquet append; we read+rewrite. The log
    is small (one row per variant) so this is cheap relative to the
    engine cost. If the file doesn't exist yet, write a fresh one.
    """
    row = {
        "ts": datetime.now(timezone.utc).replace(tzinfo=None),
        "variant_id": vid,
        "cache_key": ckey,
        "wallclock_ms": int(wallclock_ms),
    }
    new_df = pl.DataFrame([row], schema={
        "ts": pl.Datetime("us"),
        "variant_id": pl.Utf8,
        "cache_key": pl.Utf8,
        "wallclock_ms": pl.Int64,
    })
    if path.exists():
        try:
            existing = pl.read_parquet(path)
            new_df = pl.concat([existing, new_df], how="vertical_relaxed")
        except Exception:
            # If the log got corrupted, overwrite with the new row alone.
            pass
    new_df.write_parquet(path)


def _log_eta(
    completed: int,
    total: int,
    wallclock_history: list[int],
    t_total_start: float,
) -> None:
    """Emit rolling-median ETA every 10 completions."""
    elapsed = time.perf_counter() - t_total_start
    remaining = total - completed
    if not wallclock_history or remaining <= 0:
        return
    # Rolling median over the last 20 variants (or full history if shorter).
    window = wallclock_history[-20:]
    median_s = statistics.median(window) / 1000.0
    # Parallel speedup: when n_jobs>1 the wallclock per variant is per-worker
    # CPU-time, NOT wallclock. We don't know exact parallelism here, so we
    # report a CPU-time projection (conservative -- real wallclock <= this).
    proj_s = remaining * median_s
    print(
        f"[orch] progress {completed}/{total}  elapsed={elapsed/60:.1f}min  "
        f"median_per_variant={median_s:.1f}s  remaining_cpu_time~={proj_s/60:.1f}min",
        flush=True,
    )


def _dispatch_variant_from_paths(
    vid: str,
    params_data: dict,
    params_cls: str,
    ckey: str,
    spec_name: str,
    parquet_glob: str,
    train_start_iso: str,
    train_end_iso: str,
    universe_members_path: str,
    sigma_table_path: str,
    adv_table_path: str,
    freq: str,
    fee_schedules_payload: list[dict],
    impact_payload: dict,
    sizer_payload: dict,
    holdout_start_iso: str,
    holdout_end_iso: str,
    snapshot_hash_val: str,
    universe_sha: str,
    starting_equity: float,
    max_position_pct_adv: float,
) -> dict:
    """Worker: re-scan parquet for [train_start, train_end], run engine.

    Top-level so loky can pickle it. Parent passes ONLY paths + dicts; worker
    re-materialises the bar frame via `pl.scan_parquet(...).filter(...)
    .collect(engine="streaming")` so RSS stays flat at ~2-3 GB per worker
    regardless of total dataset size.
    """
    t0 = time.perf_counter()
    # Re-import inside the worker for fork-spawn safety + strategy registration.
    import bursahack.intraday.signals.orb  # noqa: F401
    try:
        import bursahack.intraday.signals.lmsw  # noqa: F401
    except ImportError:
        pass

    # Rebuild pydantic configs from payloads.
    params_cls_obj = _import_attr(params_cls)
    params = params_cls_obj(**params_data)
    fee_schedules = [_import_attr(p["cls"])(**p["data"]) for p in fee_schedules_payload]
    impact_model = _import_attr(impact_payload["cls"])(**impact_payload["data"])
    sizer = _import_attr(sizer_payload["cls"])(**sizer_payload["data"])

    holdout_start = datetime.fromisoformat(holdout_start_iso)
    holdout_end = datetime.fromisoformat(holdout_end_iso)
    train_start = _date.fromisoformat(train_start_iso)
    train_end = _date.fromisoformat(train_end_iso)

    # Re-load bars by re-scanning parquet (streaming engine).
    # We don't pass the Universe instance (which would re-trigger universe
    # materialisation); we filter via the already-resolved members parquet.
    members = pl.read_parquet(universe_members_path)
    sigma_table = pl.read_parquet(sigma_table_path)
    adv_table = pl.read_parquet(adv_table_path)

    # Use loader.load_bars but pass the members parquet via universe=None,
    # then inner-join with members. This keeps the worker independent of
    # Universe.members_for_range cache races.
    # CRITICAL: push the member-code filter INTO the lazy frame BEFORE collect,
    # so streaming actually reduces the materialised set. Collecting the full
    # universe (285M rows = ~17 GB) before filtering would OOM/segfault on
    # any multi-year window.
    from bursahack.intraday.loader import load_bars as _load_bars
    member_codes = members["code"].unique().to_list()
    bars_lf = _load_bars(
        train_start, train_end, universe=None, freq=freq, force_streaming=True,
    )
    bars_lf = bars_lf.filter(pl.col("code").is_in(member_codes))
    bars = bars_lf.collect()
    # Join on (kl_date, code) to attach per-(date, code) membership flag.
    from bursahack.intraday.calendar import KL_OFFSET_HOURS as _KL
    bars = bars.with_columns(
        (pl.col("ts") + pl.duration(hours=_KL)).dt.date().alias("_kl_date")
    ).join(
        members.rename({"date": "_kl_date"}), on=["_kl_date", "code"], how="inner"
    ).drop("_kl_date")

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


# Backwards-compat shim: keep `_dispatch_variant` name resolvable for any
# external caller (tests) that imported it. New code should use
# `_dispatch_variant_from_paths`.
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
    """LEGACY worker that takes pre-collected frames. Kept for test compat.

    New orchestrator path uses `_dispatch_variant_from_paths` to avoid the
    pickle storm. This function is retained because `test_orchestrate.py`
    may import it.
    """
    t0 = time.perf_counter()
    import bursahack.intraday.signals.orb  # noqa: F401
    try:
        import bursahack.intraday.signals.lmsw  # noqa: F401
    except ImportError:
        pass

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
