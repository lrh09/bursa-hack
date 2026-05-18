"""Polars + DuckDB lazy loader over the hive parquet store.

Scalability design: the loader returns `pl.LazyFrame` so downstream code can
chain expressions and only materialize the slice each strategy actually needs.
DuckDB is reserved for ASOF-shaped queries (universe membership, calendar
holiday gaps) where SQL is the natural form; everything else stays in Polars
to keep the memory profile flat as data grows.

Determinism: `snapshot_hash(start, end)` is a Merkle hash over the partition
SHAs in the manifest covering [start, end]. A new month landing later does
NOT invalidate earlier-month hashes -> partial cache reuse Just Works.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
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

    # Bound ts. The ts column is UTC; user gave KL-local dates.
    # Conservative bound: [start - 1 day, end + 1 day] in UTC; precise filter below.
    from datetime import timedelta
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
    return lf


__all__ = ["load_bars", "snapshot_hash"]
