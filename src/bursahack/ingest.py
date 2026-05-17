"""CSV -> partitioned parquet.

Reads the Sentieo/FactSet XKLS daily-EOD CSV, drops constant + redundant columns,
dedupes on (SECURITY_ID, DATE), sorts, and writes parquet partitioned by year.
Also builds a master table with first/last date per SECURITY_ID for the
data-quality report.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pcsv
import pyarrow.parquet as pq

from bursahack.paths import RAW_CSV, PARQUET_DIR, MASTER_PARQUET, DATA_DIR

KEEP_COLUMNS = [
    "SECURITY_ID",
    "NAME",
    "TICKER",
    "FIGI",
    "COMPOSITE_FIGI",
    "DATE",
    "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME",
    "ADJ_OPEN", "ADJ_HIGH", "ADJ_LOW", "ADJ_CLOSE", "ADJ_VOLUME",
    "ADJ_FACTOR", "EX_DIVIDEND", "SPLIT_RATIO",
    "CHANGE", "PERCENT_CHANGE",
    "FIFTY_TWO_WEEK_HIGH", "FIFTY_TWO_WEEK_LOW",
]

FLOAT_COLUMNS = [
    "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME",
    "ADJ_OPEN", "ADJ_HIGH", "ADJ_LOW", "ADJ_CLOSE", "ADJ_VOLUME",
    "ADJ_FACTOR", "EX_DIVIDEND", "SPLIT_RATIO",
    "CHANGE", "PERCENT_CHANGE",
    "FIFTY_TWO_WEEK_HIGH", "FIFTY_TWO_WEEK_LOW",
]


def read_csv(path: Path) -> pa.Table:
    read_options = pcsv.ReadOptions(block_size=64 * 1024 * 1024)
    parse_options = pcsv.ParseOptions(delimiter=",")
    column_types = {col: pa.float64() for col in FLOAT_COLUMNS}
    column_types["DATE"] = pa.date32()
    convert_options = pcsv.ConvertOptions(
        include_columns=KEEP_COLUMNS,
        column_types=column_types,
        null_values=["", "NA", "NaN", "null"],
        strings_can_be_null=True,
    )
    return pcsv.read_csv(path, read_options, parse_options, convert_options)


def ingest(raw_csv: Path = RAW_CSV, out_dir: Path = PARQUET_DIR) -> dict:
    if not raw_csv.exists():
        raise FileNotFoundError(f"raw CSV not found at {raw_csv}")

    t0 = time.time()
    print(f"[ingest] reading {raw_csv.name} ({raw_csv.stat().st_size / 1e6:.0f} MB)")
    table = read_csv(raw_csv)
    raw_rows = len(table)
    print(f"[ingest]   read {raw_rows:,} rows in {time.time() - t0:.1f}s")

    df = table.to_pandas()
    del table

    before = len(df)
    df = df.drop_duplicates(["SECURITY_ID", "DATE"]).sort_values(["SECURITY_ID", "DATE"])
    df = df.reset_index(drop=True)
    dedup_dropped = before - len(df)

    df["year"] = df["DATE"].astype("datetime64[ns]").dt.year.astype("int32")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    arrow_table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_to_dataset(
        arrow_table,
        root_path=str(out_dir),
        partition_cols=["year"],
        compression="snappy",
        existing_data_behavior="overwrite_or_ignore",
    )
    print(f"[ingest]   wrote {len(df):,} rows to {out_dir}")

    master = (
        df.groupby("SECURITY_ID", as_index=False)
        .agg(
            name=("NAME", "first"),
            ticker=("TICKER", "first"),
            figi=("FIGI", "first"),
            first_date=("DATE", "min"),
            last_date=("DATE", "max"),
            rows=("DATE", "count"),
            mean_close=("ADJ_CLOSE", "mean"),
            mean_volume=("ADJ_VOLUME", "mean"),
        )
        .sort_values("SECURITY_ID")
        .reset_index(drop=True)
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    master.to_parquet(MASTER_PARQUET, index=False)
    print(f"[ingest]   wrote master ({len(master):,} securities) to {MASTER_PARQUET.name}")

    stats = {
        "raw_rows": raw_rows,
        "kept_rows": len(df),
        "dedup_dropped": dedup_dropped,
        "n_securities": len(master),
        "first_date": str(master["first_date"].min()),
        "last_date": str(master["last_date"].max()),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    print(f"[ingest] done in {stats['elapsed_sec']}s — {stats}")
    return stats


if __name__ == "__main__":
    ingest()
