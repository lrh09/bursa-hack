"""Content-hash result cache for intraday backtests.

Design contract:
  - Cache key is a SHA-256 over EVERYTHING that determines the result:
      strategy name + version + params + snapshot_hash + universe_sha
      + impact_sha + cost_regimes_sha + engine_version.
    Anything missing here is a cache-invalidation bug.
  - Storage layout under `root/`:
      pnl/<key>.parquet     -- long-format per-regime equity series
      trades/<key>.parquet  -- trade log (regime column distinguishes runs)
      runs.parquet          -- append-only metadata index (DuckDB-queryable)
  - `put` is idempotent (existing key = no-op); `get` returns the full
    BacktestResult round-tripped from disk.

DuckDB consumability: `runs.parquet` is the queryable index over the cache.
A typical "best Sharpe by family" query reads only this one file plus the
PnL series for shortlisted keys.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import polars as pl
from pydantic import BaseModel

from bursahack.intraday.engine import (
    BacktestResult,
    RunMetadata,
    cost_regimes_sha,
    impact_sha,
    params_sha,
)
from bursahack.intraday.impact import ImpactModel
from bursahack.intraday.registry import StrategySpec


RUNS_SCHEMA: dict[str, pl.DataType] = {
    "cache_key": pl.Utf8,
    "run_id": pl.Utf8,
    "started_at": pl.Datetime("ns"),
    "wallclock_ms": pl.Int64,
    "n_bars": pl.Int64,
    "n_trades": pl.Int64,
    "snapshot_hash": pl.Utf8,
    "git_sha": pl.Utf8,
    "signal_version": pl.Utf8,
    "params_sha": pl.Utf8,
    "engine_version": pl.Utf8,
    "cost_regimes_sha": pl.Utf8,
    "impact_model_sha": pl.Utf8,
    "universe_sha": pl.Utf8,
    "holdout_excluded": pl.Boolean,
}


class ResultCache(BaseModel):
    """Content-hash addressed cache for `BacktestResult` objects."""

    root: Path = Path("data/intraday/_results")

    # --------------------------- key --------------------------------

    def compute_key(
        self,
        spec: StrategySpec,
        params: BaseModel,
        snapshot_hash: str,
        universe_sha: str,
        impact_model_sha: str,
        cost_regimes_sha_val: str,
        engine_version: str,
    ) -> str:
        """Deterministic SHA-256 over every result-determining input.

        ANY change to ANY input must change the key. We intentionally pass
        already-hashed sub-objects so the caller controls fidelity (e.g.
        whether to hash universe-spec only or universe-spec + materialized
        membership table).
        """
        payload = {
            "strategy_name": spec.name,
            "strategy_version": spec.version,
            "params": params.model_dump(),
            "snapshot_hash": snapshot_hash,
            "universe_sha": universe_sha,
            "impact_model_sha": impact_model_sha,
            "cost_regimes_sha": cost_regimes_sha_val,
            "engine_version": engine_version,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("ascii")).hexdigest()

    # --------------------------- io ---------------------------------

    def _pnl_path(self, key: str) -> Path:
        return self.root / "pnl" / f"{key}.parquet"

    def _trades_path(self, key: str) -> Path:
        return self.root / "trades" / f"{key}.parquet"

    def _runs_path(self) -> Path:
        return self.root / "runs.parquet"

    def has(self, key: str) -> bool:
        return self._pnl_path(key).exists() and self._trades_path(key).exists()

    def get(self, key: str) -> BacktestResult | None:
        """Reconstruct a `BacktestResult` from disk, or None if missing."""
        if not self.has(key):
            return None
        pnl_long = pl.read_parquet(self._pnl_path(key))
        trades = pl.read_parquet(self._trades_path(key))

        # Split long-format pnl back into a dict[regime -> DataFrame]
        pnl: dict[str, pl.DataFrame] = {}
        for regime in pnl_long.get_column("regime").unique().to_list():
            pnl[regime] = pnl_long.filter(pl.col("regime") == regime)

        # Reconstruct metadata from runs.parquet
        runs = self._read_runs()
        row = runs.filter(pl.col("cache_key") == key)
        if row.height == 0:
            raise FileNotFoundError(f"runs.parquet missing entry for cache_key={key!r}")
        r = row.to_dicts()[-1]  # last write wins if dupes ever slip in
        meta = RunMetadata(
            run_id=r["run_id"],
            started_at=r["started_at"],
            wallclock_ms=r["wallclock_ms"],
            n_bars=r["n_bars"],
            n_trades=r["n_trades"],
            snapshot_hash=r["snapshot_hash"],
            git_sha=r["git_sha"],
            signal_version=r["signal_version"],
            params_sha=r["params_sha"],
            engine_version=r["engine_version"],
            cost_regimes_sha=r["cost_regimes_sha"],
            impact_model_sha=r["impact_model_sha"],
            universe_sha=r["universe_sha"],
            holdout_excluded=r["holdout_excluded"],
        )
        return BacktestResult(pnl=pnl, trades=trades, metadata=meta)

    def put(self, key: str, result: BacktestResult) -> None:
        """Persist `result` under `key`. Idempotent: existing key is no-op."""
        if self.has(key):
            return
        self._pnl_path(key).parent.mkdir(parents=True, exist_ok=True)
        self._trades_path(key).parent.mkdir(parents=True, exist_ok=True)

        # PnL is stored long-format (regime, ts, equity, ret).
        if result.pnl:
            pnl_long = pl.concat(list(result.pnl.values())) if len(result.pnl) > 1 \
                else list(result.pnl.values())[0]
        else:
            pnl_long = pl.DataFrame(schema={
                "regime": pl.Utf8, "ts": pl.Datetime("ns"),
                "equity": pl.Float64, "ret": pl.Float64,
            })
        pnl_long.write_parquet(self._pnl_path(key))
        result.trades.write_parquet(self._trades_path(key))

        # Append to runs.parquet
        row = pl.DataFrame([{
            "cache_key": key,
            "run_id": result.metadata.run_id,
            "started_at": result.metadata.started_at,
            "wallclock_ms": result.metadata.wallclock_ms,
            "n_bars": result.metadata.n_bars,
            "n_trades": result.metadata.n_trades,
            "snapshot_hash": result.metadata.snapshot_hash,
            "git_sha": result.metadata.git_sha,
            "signal_version": result.metadata.signal_version,
            "params_sha": result.metadata.params_sha,
            "engine_version": result.metadata.engine_version,
            "cost_regimes_sha": result.metadata.cost_regimes_sha,
            "impact_model_sha": result.metadata.impact_model_sha,
            "universe_sha": result.metadata.universe_sha,
            "holdout_excluded": result.metadata.holdout_excluded,
        }], schema=RUNS_SCHEMA)
        existing = self._read_runs()
        out = pl.concat([existing, row]) if existing.height else row
        out.write_parquet(self._runs_path())

    def _read_runs(self) -> pl.DataFrame:
        p = self._runs_path()
        if not p.exists():
            return pl.DataFrame(schema=RUNS_SCHEMA)
        return pl.read_parquet(p)


__all__ = ["ResultCache", "RUNS_SCHEMA"]
