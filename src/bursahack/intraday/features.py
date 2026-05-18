"""Precomputed per-bar features for the intraday engine.

Two tables, both cached to `data/intraday/_features/`:

  sigma_<input_sha>.parquet   -- columns [ts, code, sigma]
    Rolling Rogers-Satchell per-bar volatility over the last `window_bars`
    bars per code (default 20). Used by the impact model for per-trade
    cost estimation.

  adv_<input_sha>.parquet     -- columns [date, code, adv_bar_shares,
                                          adv_bar_value_rm]
    20-day median of per-minute volume + RM-value per code, LAGGED BY 1
    TRADING DAY (no same-day leakage). The lag mirrors the universe-rebalance
    pattern: a backtest seeing a signal on day D consumes the ADV figure
    computed from days [D-21, D-1]. Used by the Sizer for participation
    capping and by the impact model.

Determinism / caching:
  input_sha = SHA256(snapshot_hash || universe_sha || freq || window).
  Cache miss -> compute + write. Cache hit -> read parquet, no recompute.
  Cache is "additive only": new (snapshot, universe, freq, window) tuples
  create new files; existing files are never overwritten.

Scalability note: both compute paths are pure Polars expressions over the
materialized bar frame. For 10y x top-300 the bar frame is ~hundreds of
millions of rows; both functions stream the rolling window via Polars'
native `rolling_*().over(code)` which doesn't materialize per-group copies.
"""
from __future__ import annotations

import hashlib
from datetime import date as _date
from pathlib import Path
from typing import Literal

import polars as pl

from bursahack.intraday.impact import rogers_satchell_sigma


def _default_features_root() -> Path:
    from bursahack.paths import REPO_ROOT
    return REPO_ROOT / "data" / "intraday" / "_features"


def features_input_sha(
    snapshot_hash: str,
    universe_sha: str,
    freq: str,
    window: int,
) -> str:
    """Deterministic SHA-256 over the inputs that determine a feature table."""
    raw = "|".join([
        f"snap={snapshot_hash}",
        f"uni={universe_sha}",
        f"freq={freq}",
        f"window={window}",
    ]).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Sigma table (per-bar)
# ---------------------------------------------------------------------------


def compute_sigma_table(
    bars: pl.DataFrame,
    window_bars: int = 20,
    method: Literal["rogers_satchell"] = "rogers_satchell",
    *,
    input_sha: str | None = None,
    features_root: Path | None = None,
) -> pl.DataFrame:
    """Rolling per-bar sigma. Cached on (input_sha) when provided.

    Returns: DataFrame [ts, code, sigma] (rows where sigma is null until the
    window fills are KEPT -- the engine treats null sigma as "no impact info").
    """
    if method != "rogers_satchell":
        raise ValueError(f"unsupported sigma method: {method!r}")

    root = features_root or _default_features_root()
    cache_path = root / f"sigma_{input_sha}.parquet" if input_sha else None
    if cache_path is not None and cache_path.exists():
        return pl.read_parquet(cache_path)

    rs = rogers_satchell_sigma(bars, window_bars=window_bars, by_code=True)
    out = rs.rename({"sigma_rs": "sigma"}).select(["ts", "code", "sigma"])

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        out.write_parquet(cache_path)
    return out


# ---------------------------------------------------------------------------
# ADV-bar table (per (date, code))
# ---------------------------------------------------------------------------


def compute_adv_bar_table(
    bars: pl.DataFrame,
    window_days: int = 20,
    *,
    input_sha: str | None = None,
    features_root: Path | None = None,
) -> pl.DataFrame:
    """20-day median of per-minute shares + RM-value, LAGGED 1 trading day.

    Algorithm:
      1. Per (code, KL-session-date), compute the median per-minute volume
         and per-minute value across the day's real (non-phantom) bars.
      2. Per code, take a rolling median (window_days) of those daily medians,
         producing adv_bar_shares / adv_bar_value_rm.
      3. SHIFT the rolling result FORWARD by 1 trading day (per code), so a
         signal on day D consumes ADV computed strictly on days [D-window, D-1].

    Returns: DataFrame [date, code, adv_bar_shares, adv_bar_value_rm].
    Joinable into the engine on (entry_date, code).
    """
    root = features_root or _default_features_root()
    cache_path = root / f"adv_{input_sha}.parquet" if input_sha else None
    if cache_path is not None and cache_path.exists():
        return pl.read_parquet(cache_path)

    # KL-local date is the natural session bucket. The loader already stamps
    # _kl_date when materializing, but feature precompute may be called on
    # a freshly-collected DataFrame, so we recompute here.
    from bursahack.intraday.calendar import KL_OFFSET_HOURS

    df = bars.with_columns([
        (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.date().alias("date"),
    ])

    # Per (code, day): median per-minute volume + median per-minute value.
    per_day = (
        df.group_by(["code", "date"])
        .agg([
            pl.col("volume").median().alias("_med_vol_day"),
            pl.col("value").median().alias("_med_val_day"),
        ])
        .sort(["code", "date"])
    )

    # Rolling 20-day median of the daily medians, per code.
    per_day = per_day.with_columns([
        pl.col("_med_vol_day")
            .rolling_median(window_size=window_days, min_samples=1)
            .over("code")
            .alias("_adv_shares_raw"),
        pl.col("_med_val_day")
            .rolling_median(window_size=window_days, min_samples=1)
            .over("code")
            .alias("_adv_value_raw"),
    ])

    # Shift forward by 1 trading day per code: the value valid FOR day D was
    # computed from days <= D-1. The shift creates a null on the first day
    # of each code (intentional -- no ADV info on bootstrap).
    per_day = per_day.with_columns([
        pl.col("_adv_shares_raw").shift(1).over("code").alias("adv_bar_shares"),
        pl.col("_adv_value_raw").shift(1).over("code").alias("adv_bar_value_rm"),
    ])

    out = per_day.select(["date", "code", "adv_bar_shares", "adv_bar_value_rm"])

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        out.write_parquet(cache_path)
    return out


__all__ = [
    "compute_adv_bar_table",
    "compute_sigma_table",
    "features_input_sha",
]
