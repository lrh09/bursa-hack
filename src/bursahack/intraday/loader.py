"""Polars + DuckDB lazy loader over the hive parquet store.

Scalability design: the loader returns `pl.LazyFrame` so downstream code can
chain expressions and only materialize the slice each strategy actually needs.
DuckDB is reserved for ASOF-shaped queries (universe membership, calendar
holiday gaps) where SQL is the natural form; everything else stays in Polars
to keep the memory profile flat as data grows.

Determinism: `snapshot_hash(start, end)` is a Merkle hash over the partition
SHAs in the manifest covering [start, end]. A new month landing later does
NOT invalidate earlier-month hashes -> partial cache reuse Just Works.

Scale-smoke finding (Phase A, 2026-05-19):
  Polars 1.40.x's default in-memory engine SEGFAULTS when `.collect()`ing
  a multi-year (>= ~2y) bar frame from the hive store (28M+ rows). Streaming
  engine handles it cleanly in ~18s. `_collect()` below routes large
  collects through `engine="streaming"`.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pyarrow.parquet as pq

from bursahack.intraday.calendar import ExchangeCalendar

if TYPE_CHECKING:
    from bursahack.intraday.universe import Universe


# Canonical store root. Override via env var if you've moved the parquet store.
def _default_data_root() -> Path:
    from bursahack.paths import REPO_ROOT
    return REPO_ROOT / "data" / "intraday"


# ============================================================================
# Snapshot hash
# ============================================================================

def _load_manifest(data_root: Path | None = None) -> pl.DataFrame:
    root = data_root or _default_data_root()
    mpath = root / "_manifest.parquet"
    if not mpath.exists():
        raise FileNotFoundError(
            f"manifest not found at {mpath} -- run scripts/migrate_to_hive.py first"
        )
    return pl.read_parquet(mpath)


def _partitions_in_range(
    manifest: pl.DataFrame, start: date, end: date
) -> pl.DataFrame:
    """Filter manifest to partitions whose (year, month) intersects [start, end]."""
    # Extract year/month from partition_path "exchange=XKLS/year=YYYY/month=MM/..."
    df = manifest.with_columns([
        pl.col("partition_path").str.extract(r"year=(\d{4})", 1).cast(pl.Int32).alias("_y"),
        pl.col("partition_path").str.extract(r"month=(\d{2})", 1).cast(pl.Int32).alias("_m"),
    ])
    start_key = start.year * 100 + start.month
    end_key = end.year * 100 + end.month
    df = df.filter(
        (pl.col("_y") * 100 + pl.col("_m") >= start_key)
        & (pl.col("_y") * 100 + pl.col("_m") <= end_key)
    )
    return df.sort("partition_path")


def snapshot_hash(
    start: date, end: date, data_root: Path | None = None
) -> str:
    """Merkle hash over partition SHAs for [start, end] (inclusive).

    Stable across machines: hash is SHA-256(b"|".join(sorted(partition_sha))).
    Adding a future partition does NOT change earlier hashes.
    """
    manifest = _load_manifest(data_root)
    parts = _partitions_in_range(manifest, start, end)
    shas = sorted(parts.get_column("partition_sha").to_list())
    h = hashlib.sha256()
    for s in shas:
        h.update(s.encode("ascii"))
        h.update(b"|")
    return h.hexdigest()


# ============================================================================
# Streaming-engine wrapper
# ============================================================================

# Thresholds for picking the streaming engine. Polars 1.40.x default
# (in-memory) engine segfaults on the 4.4y full data collect (28M rows,
# 2.4 GB). Streaming handles it. We force streaming when EITHER the
# date range exceeds STREAMING_DAYS_THRESHOLD or `force_streaming=True`.
STREAMING_DAYS_THRESHOLD = 365  # any window > 1 year uses streaming
STREAMING_ROW_THRESHOLD = 10_000_000


class _StreamingLazyFrame:
    """Thin wrapper that forces `.collect()` to use the streaming engine.

    Returned by `load_bars()` whenever the requested window is wider than
    `STREAMING_DAYS_THRESHOLD` days, OR `force_streaming=True` was passed.
    Pass-through for all other LazyFrame ops via `__getattr__`.
    """

    __slots__ = ("_lf", "_force_streaming")

    def __init__(self, lf: pl.LazyFrame, force_streaming: bool = True) -> None:
        self._lf = lf
        self._force_streaming = force_streaming

    def collect(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self._force_streaming and "engine" not in kwargs:
            kwargs["engine"] = "streaming"
        return self._lf.collect(*args, **kwargs)

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        attr = getattr(self._lf, name)
        # Methods that return a LazyFrame should preserve the streaming
        # wrapper so chained .filter().collect() still streams.
        if callable(attr):
            def wrapped(*a, **kw):  # type: ignore[no-untyped-def]
                r = attr(*a, **kw)
                if isinstance(r, pl.LazyFrame):
                    return _StreamingLazyFrame(r, self._force_streaming)
                return r
            return wrapped
        return attr


def _collect(lf: pl.LazyFrame, *, streaming: bool) -> pl.DataFrame:
    """Materialize a LazyFrame using the streaming engine when requested.

    Centralised so both new query paths and the legacy direct-collect
    sites converge on one place that decides engine. Existing callers
    (`load_bars(...).collect()`) get the same behaviour automatically
    because `load_bars` now returns a `_StreamingLazyFrame` for wide
    windows.
    """
    if streaming:
        return lf.collect(engine="streaming")
    return lf.collect()


# ============================================================================
# Bar loader
# ============================================================================

_FREQ_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "1d": "1d",
}


def load_bars(
    start: date,
    end: date,
    universe: "Universe | None" = None,
    freq: str = "1m",
    drop_phantom_bars: bool = True,
    data_root: Path | None = None,
    exchange: str = "XKLS",
    *,
    force_streaming: bool | None = None,
) -> pl.LazyFrame:
    """Load OHLCV bars over [start, end] at the requested freq.

    Args:
        start, end : inclusive date range (interpreted in KL local time).
        universe   : if given, members are joined as-of and rows outside the
                     active set are dropped. None = no filter.
        freq       : "1m" | "5m" | "15m" | "30m" | "1h" | "1d".
        drop_phantom_bars : drop 12:30-14:30 KL rows BEFORE resampling for 1m;
                            for resampled bars, drop only fully-empty bars.
        data_root  : override the parquet root.

    Returns: pl.LazyFrame with columns [ts (UTC), code, open, high, low, close,
                                        volume, value, exchange, session].
    """
    if freq not in _FREQ_MAP:
        raise ValueError(f"unsupported freq {freq!r}; expected one of {list(_FREQ_MAP)}")

    root = data_root or _default_data_root()
    pattern = str(root / "exchange=*" / "year=*" / "month=*" / "*.parquet")
    lf = pl.scan_parquet(pattern, hive_partitioning=True)

    # F4 schema regression: hive partitions have mixed `code` width
    # (`pyarrow.string()` vs `pyarrow.large_string()`). Polars' Utf8 absorbs
    # both today via implicit promotion, but PyArrow upgrades could break this.
    # Cast explicitly to Utf8 right after the scan so the loader's contract
    # is one stable dtype across partitions.
    lf = lf.with_columns(pl.col("code").cast(pl.Utf8))

    # Bound ts. The ts column is UTC; user gave KL-local dates.
    # Conservative bound: [start - 1 day, end + 1 day] in UTC; precise filter below.
    lf = lf.filter(
        (pl.col("ts") >= datetime.combine(start, datetime.min.time()) - timedelta(days=1))
        & (pl.col("ts") < datetime.combine(end, datetime.min.time()) + timedelta(days=2))
    )

    # Restrict to KL-local session date in [start, end]
    cal = ExchangeCalendar(exchange)
    from bursahack.intraday.calendar import KL_OFFSET_HOURS
    lf = lf.with_columns([
        (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.date().alias("_kl_date"),
    ]).filter(
        (pl.col("_kl_date") >= start) & (pl.col("_kl_date") <= end)
    )

    if drop_phantom_bars and freq == "1m":
        # Drop phantom (lunch-break) bars.
        # Cast to Int32 explicitly -- dt.hour/dt.minute return u8 which overflows on *60.
        kl_min = (
            (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.hour().cast(pl.Int32) * 60
            + (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.minute().cast(pl.Int32)
        )
        lf = lf.filter(~((kl_min >= 750) & (kl_min < 870)))

    # Universe filter (as-of-date)
    if universe is not None:
        members = universe.members_for_range(start, end)  # cols: date, code
        # Make sure both sides have the same date type
        members_lf = members.lazy().rename({"date": "_kl_date"})
        lf = lf.join(members_lf, on=["_kl_date", "code"], how="inner")

    # Resample if needed
    if freq != "1m":
        every = freq
        lf = lf.sort(["code", "ts"]).group_by_dynamic(
            index_column="ts",
            every=every,
            group_by=["code"],
            closed="left",
            label="left",
        ).agg([
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
            pl.col("value").sum().alias("value"),
            pl.col("exchange").first().alias("exchange"),
            pl.col("session").first().alias("session"),
        ])
        if drop_phantom_bars:
            lf = lf.filter(pl.col("volume") > 0)
    else:
        # Keep _kl_date out of result for cleanliness
        pass

    lf = lf.drop("_kl_date") if "_kl_date" in lf.collect_schema().names() else lf

    # Decide whether to wrap with the streaming-engine shim. Caller can force
    # via `force_streaming=True`; otherwise auto-detect on date range.
    if force_streaming is None:
        span_days = (end - start).days
        use_streaming = span_days >= STREAMING_DAYS_THRESHOLD
    else:
        use_streaming = force_streaming

    if use_streaming:
        return _StreamingLazyFrame(lf, force_streaming=True)  # type: ignore[return-value]
    return lf


# ============================================================================
# Data range (manifest-derived; cheap, no parquet scan)
# ============================================================================


def get_data_range(
    data_root: Path | None = None, exchange: str = "XKLS"
) -> tuple[datetime, datetime]:
    """Inclusive (min_ts, max_ts) covered by the hive store.

    Cheap: reads the manifest only, derives a (year, month) range from the
    `exchange=<x>/year=YYYY/month=MM/...` partition paths and returns the
    first instant of the earliest month -> last instant of the latest month
    (UTC-naive, the same shape parquet timestamps land in).

    Used by the orchestrator to auto-derive the holdout window without
    loading a single bar.
    """
    manifest = _load_manifest(data_root)
    df = manifest.with_columns([
        pl.col("partition_path").str.extract(r"exchange=([^/]+)", 1).alias("_ex"),
        pl.col("partition_path").str.extract(r"year=(\d{4})", 1).cast(pl.Int32).alias("_y"),
        pl.col("partition_path").str.extract(r"month=(\d{2})", 1).cast(pl.Int32).alias("_m"),
    ]).filter(pl.col("_ex") == exchange)
    if df.height == 0:
        raise FileNotFoundError(
            f"manifest has no partitions for exchange={exchange!r}"
        )
    ys = df.get_column("_y").to_list()
    ms = df.get_column("_m").to_list()
    keys = sorted(zip(ys, ms))
    y0, m0 = keys[0]
    y1, m1 = keys[-1]
    # min = first instant of (y0, m0); max = last instant of (y1, m1)
    from calendar import monthrange
    min_ts = datetime(y0, m0, 1)
    last_day = monthrange(y1, m1)[1]
    max_ts = datetime(y1, m1, last_day, 23, 59, 59)
    return min_ts, max_ts


__all__ = [
    "_StreamingLazyFrame",
    "_collect",
    "STREAMING_DAYS_THRESHOLD",
    "STREAMING_ROW_THRESHOLD",
    "get_data_range",
    "load_bars",
    "snapshot_hash",
]
