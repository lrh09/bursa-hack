"""Result cache: deterministic key, idempotent put, round-trip get."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl
import pytest
from pydantic import BaseModel

from bursahack.intraday.cache import ResultCache
from bursahack.intraday.engine import BacktestResult, RunMetadata
from bursahack.intraday.registry import StrategySpec


class _P(BaseModel):
    a: int = 1
    b: float = 2.0


class _Dummy:
    pass


def _fake_spec() -> StrategySpec:
    return StrategySpec(
        cls=_Dummy, name="fake", version="0.0.1",
        params_model=_P, description="",
    )


def _fake_result() -> BacktestResult:
    pnl = pl.DataFrame({
        "regime": ["mplus_retail"] * 3,
        "ts": [datetime(2024, 1, 3, 1, 0), datetime(2024, 1, 3, 2, 0),
               datetime(2024, 1, 3, 3, 0)],
        "equity": [100_000.0, 100_100.0, 100_050.0],
        "ret": [0.0, 0.001, -0.0005],
    }, schema={"regime": pl.Utf8, "ts": pl.Datetime("ns"),
               "equity": pl.Float64, "ret": pl.Float64})
    trades = pl.DataFrame({
        "code": ["X"],
        "entry_ts": [datetime(2024, 1, 3, 1, 0)],
        "exit_ts": [datetime(2024, 1, 3, 3, 0)],
        "entry_px": [10.0],
        "exit_px": [10.05],
        "qty": [1.0],
        "gross_ret": [0.005],
        "fees_bps": [10.0],
        "impact_bps": [0.0],
        "net_ret": [0.004],
    }, schema={"code": pl.Utf8, "entry_ts": pl.Datetime("ns"),
               "exit_ts": pl.Datetime("ns"), "entry_px": pl.Float64,
               "exit_px": pl.Float64, "qty": pl.Float64,
               "gross_ret": pl.Float64, "fees_bps": pl.Float64,
               "impact_bps": pl.Float64, "net_ret": pl.Float64})
    trades = trades.with_columns(pl.lit("mplus_retail").alias("regime"))
    meta = RunMetadata(
        run_id="run-xxx", started_at=datetime(2024, 1, 3),
        wallclock_ms=42, n_bars=360, n_trades=1,
        snapshot_hash="snap-x", git_sha="abc123",
        signal_version="0.0.1", params_sha="p-sha",
        engine_version="1.0.0", cost_regimes_sha="cr-sha",
        impact_model_sha="im-sha", universe_sha="uni-sha",
        holdout_excluded=True,
    )
    return BacktestResult(pnl={"mplus_retail": pnl}, trades=trades, metadata=meta)


def test_key_deterministic():
    cache = ResultCache(root=Path("data/intraday/_results"))
    k1 = cache.compute_key(
        spec=_fake_spec(), params=_P(a=1, b=2.0),
        snapshot_hash="s", universe_sha="u",
        impact_model_sha="i", cost_regimes_sha_val="c",
        engine_version="1.0.0",
    )
    k2 = cache.compute_key(
        spec=_fake_spec(), params=_P(a=1, b=2.0),
        snapshot_hash="s", universe_sha="u",
        impact_model_sha="i", cost_regimes_sha_val="c",
        engine_version="1.0.0",
    )
    assert k1 == k2 and len(k1) == 64


def test_key_changes_on_input_change():
    cache = ResultCache(root=Path("data/intraday/_results"))
    base_kwargs = dict(
        spec=_fake_spec(), params=_P(a=1, b=2.0),
        snapshot_hash="s", universe_sha="u",
        impact_model_sha="i", cost_regimes_sha_val="c",
        engine_version="1.0.0",
    )
    k_base = cache.compute_key(**base_kwargs)
    for field, mutated in [
        ("snapshot_hash", "s2"),
        ("universe_sha", "u2"),
        ("impact_model_sha", "i2"),
        ("cost_regimes_sha_val", "c2"),
        ("engine_version", "1.0.1"),
    ]:
        kw = {**base_kwargs, field: mutated}
        assert cache.compute_key(**kw) != k_base, (
            f"key did not change when {field} changed"
        )
    # params change
    kw = {**base_kwargs, "params": _P(a=2, b=2.0)}
    assert cache.compute_key(**kw) != k_base


def test_put_get_roundtrip_and_idempotent(tmp_path: Path):
    cache = ResultCache(root=tmp_path / "results")
    spec = _fake_spec()
    key = cache.compute_key(
        spec=spec, params=_P(), snapshot_hash="s", universe_sha="u",
        impact_model_sha="i", cost_regimes_sha_val="c", engine_version="1.0.0",
    )
    res = _fake_result()
    assert not cache.has(key)
    cache.put(key, res)
    assert cache.has(key)

    # Idempotent: a second put should NOT duplicate rows in runs.parquet.
    runs_before = pl.read_parquet(cache._runs_path()).height
    cache.put(key, res)
    runs_after = pl.read_parquet(cache._runs_path()).height
    assert runs_before == runs_after == 1

    rt = cache.get(key)
    assert rt is not None
    assert rt.metadata.run_id == "run-xxx"
    assert "mplus_retail" in rt.pnl
    assert rt.pnl["mplus_retail"].height == 3
    assert rt.trades.height == 1


def test_get_missing_returns_none(tmp_path: Path):
    cache = ResultCache(root=tmp_path / "empty")
    assert cache.get("no-such-key") is None
