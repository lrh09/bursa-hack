"""F4 schema-width regression: loader normalises `code` column to Utf8.

Phase A finding (A2_data_quality_full.md sec 5):
  Some hive partitions have `code` typed as `pyarrow.string()`, others as
  `pyarrow.large_string()`. The default polars/pyarrow promotion path handles
  it today, but a future PyArrow upgrade could change behaviour. Pin the
  loader contract: regardless of underlying parquet partition dtype, the
  returned DataFrame's `code` column is `pl.Utf8`.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pyarrow as pa
import pytest

from bursahack.intraday.loader import load_bars


pytestmark = pytest.mark.skipif(
    not Path(
        r"C:/Users/Workstation/Desktop/BursaHack/data/intraday/_manifest.parquet"
    ).exists(),
    reason="hive parquet store not migrated yet",
)


def test_code_column_is_utf8_single_day() -> None:
    df = load_bars(date(2020, 9, 24), date(2020, 9, 24)).collect()
    assert df.schema["code"] == pl.Utf8, (
        f"loader must normalise `code` to Utf8, got {df.schema['code']}"
    )


def test_code_column_is_utf8_multi_month_span() -> None:
    """Multi-month window crosses partition boundaries; both string and
    large_string variants must collapse to Utf8."""
    df = load_bars(date(2020, 9, 24), date(2020, 12, 31)).collect()
    assert df.schema["code"] == pl.Utf8


def test_code_dtype_stable_when_concatenating_partitions() -> None:
    """Build a synthetic two-partition concat (string + large_string) and
    confirm the loader path normalises to a single dtype.

    This is a unit-level guard: it doesn't go through load_bars (which
    works through scan_parquet); it pins the explicit promotion contract.
    """
    t1 = pa.table({"code": pa.array(["A", "B"], type=pa.string())})
    t2 = pa.table({"code": pa.array(["C", "D"], type=pa.large_string())})
    # Default promote_options="default" raises ArrowInvalid on string+large
    # mixes in some PyArrow releases; promote_options="permissive" handles
    # both. We assert the permissive path produces a single string column.
    combined = pa.concat_tables([t1, t2], promote_options="permissive")
    pl_df = pl.from_arrow(combined)
    assert isinstance(pl_df, pl.DataFrame)
    assert pl_df.schema["code"] in (pl.Utf8,)
