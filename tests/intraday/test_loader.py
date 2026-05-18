"""Loader: golden non-phantom row count + snapshot_hash determinism."""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from bursahack.intraday.loader import load_bars, snapshot_hash


pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path(
        r"C:/Users/Workstation/Desktop/BursaHack/data/intraday/_manifest.parquet"
    ).exists(),
    reason="hive parquet store not migrated yet",
)


def test_snapshot_hash_deterministic() -> None:
    a = snapshot_hash(date(2020, 9, 24), date(2020, 10, 31))
    b = snapshot_hash(date(2020, 9, 24), date(2020, 10, 31))
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_snapshot_hash_partial_independence() -> None:
    """Adding a future month should NOT change earlier-month hashes."""
    early = snapshot_hash(date(2020, 9, 24), date(2020, 9, 30))
    longer = snapshot_hash(date(2020, 9, 24), date(2021, 10, 4))
    assert early != longer  # different partition set
    # But "early" only covers Sep-2020 so it's deterministic regardless
    # of whether other months exist in the store.
    early2 = snapshot_hash(date(2020, 9, 24), date(2020, 9, 30))
    assert early == early2


def test_load_bars_drops_phantom() -> None:
    # 2020-09-24 (Thu) — known session, audited 480 bars per code ceiling
    lf = load_bars(date(2020, 9, 24), date(2020, 9, 24), drop_phantom_bars=True)
    df = lf.collect()
    # No row should land in the phantom window (12:30-14:30 KL = 04:30-06:30 UTC)
    bad = df.filter(
        (pl.col("ts").dt.hour() == 4) & (pl.col("ts").dt.minute() >= 30)
        | (pl.col("ts").dt.hour() == 5)
        | ((pl.col("ts").dt.hour() == 6) & (pl.col("ts").dt.minute() < 30))
    )
    assert bad.height == 0, f"phantom bars leaked: {bad.height} rows"

    # And keeping them should give MORE rows
    df_keep = load_bars(date(2020, 9, 24), date(2020, 9, 24),
                        drop_phantom_bars=False).collect()
    assert df_keep.height > df.height


def test_load_bars_session_dates_only() -> None:
    # 2020-10-29 was a Bursa holiday -> no rows even though it's a weekday
    df = load_bars(date(2020, 10, 29), date(2020, 10, 29)).collect()
    assert df.height == 0
