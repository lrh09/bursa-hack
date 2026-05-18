"""Universe: as-of-date strictness + mega-cap sanity."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pytest

from bursahack.intraday.calendar import KL_OFFSET_HOURS
from bursahack.intraday.universe import Universe

DATA_ROOT = Path(r"C:/Users/Workstation/Desktop/BursaHack/data/intraday")

pytestmark = pytest.mark.skipif(
    not (DATA_ROOT / "_manifest.parquet").exists(),
    reason="hive parquet store not migrated yet",
)


def test_members_as_of_refuses_future_data() -> None:
    """`members_as_of(asof)` must pass a strict ts < asof filter to PyArrow."""
    u = Universe(top_n=10, lookback_days=30)
    asof = date(2021, 3, 1)
    asof_utc = datetime.combine(asof, datetime.min.time()) - timedelta(hours=KL_OFFSET_HOURS)
    seen_ts_upper: list[datetime] = []

    real_read = __import__("pyarrow.parquet", fromlist=["read_table"]).read_table

    def spy(*args, **kwargs):
        for f in kwargs.get("filters", []):
            if f[0] == "ts" and f[1] == "<":
                # `f[2]` is a pa.Scalar(timestamp)
                val = f[2]
                ts = val.as_py() if hasattr(val, "as_py") else val
                seen_ts_upper.append(ts)
        return real_read(*args, **kwargs)

    # Force a cache miss by using a fresh universe-spec
    cache_path, _ = u._cache_key(asof, DATA_ROOT)
    if cache_path.exists():
        cache_path.unlink()

    with patch("bursahack.intraday.universe.pq.read_table", side_effect=spy):
        u.members_as_of(asof, DATA_ROOT)

    # Every recorded upper bound must be strictly <= asof_utc
    assert seen_ts_upper, "no ts filter was passed to read_table"
    for ts in seen_ts_upper:
        assert ts <= asof_utc, f"future data read: {ts} > {asof_utc}"


def test_top100_includes_known_megacaps_for_sanity_date() -> None:
    u = Universe(top_n=100, lookback_days=60)
    members = set(u.members_as_of(date(2021, 6, 1), DATA_ROOT))
    # 1155 MAYBANK, 1023 CIMB, 5347 TENAGA are the canonical mega-caps.
    # Phase 1 liquidity table rank-1 was 7113 (Top Glove); these three should
    # comfortably sit inside the top 100 by 60-day median daily value.
    assert "1155" in members, "MAYBANK missing"
    assert "1023" in members, "CIMB missing"
    assert "5347" in members, "TENAGA missing"


def test_members_for_range_inherits_between_rebalances() -> None:
    u = Universe(top_n=20, lookback_days=30)
    df = u.members_for_range(date(2021, 3, 1), date(2021, 3, 31), DATA_ROOT)
    # Every session day in March should have 20 members
    assert df.height > 0
    per_day = df.group_by("date").len().sort("date")
    counts = per_day.get_column("len").unique().to_list()
    assert counts == [20], f"non-uniform daily member counts: {counts}"
