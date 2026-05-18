"""Migrate by-date parquets -> hive-partitioned `exchange=XKLS/year=/month=`.

Idempotent: re-runs are no-ops unless a source file's SHA changes. Each partition
gets one parquet (one row group ~128MB ceiling, ZSTD-3) sorted by (code, ts).
Source rows keep their phantom-break (12:30-14:30 KL zero-volume) intact;
filtering happens in the loader, not storage.

Output schema:
    ts        datetime64[ns]   UTC, naive
    code      str
    open,high,low,close   float64
    volume    int64    (shares)
    value     float64  (RM traded)
    exchange  dict<str>  (categorical: "XKLS")
    session   dict<str>  (categorical: "morning" | "afternoon")

Sidecar manifest `data/intraday/_manifest.parquet` rows (one per partition):
    partition_path : str (relative to repo root)
    source_files   : list[str]
    source_sha     : list[str]   (SHA-256 of source bytes, hex)
    partition_sha  : str         (SHA-256 of the partition file bytes, hex)
    n_rows         : int64
    written_at     : datetime64[ns] (UTC)
    code_version   : str         (git SHA of bursahack repo or "unknown")

Run:
    python scripts/migrate_to_hive.py [--src PATH] [--dst PATH] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# Add src to path so we can import bursahack
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bursahack.intraday.calendar import KL_OFFSET_HOURS  # noqa: E402

DEFAULT_SRC = REPO_ROOT / "Historical_1m_20200924_20250225"
DEFAULT_DST = REPO_ROOT / "data" / "intraday"
EXCHANGE = "XKLS"

_DATE_RE = re.compile(r"(\d{8})\.parq$")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _list_source_files(src_dir: Path) -> list[Path]:
    files = sorted(p for p in src_dir.iterdir() if _DATE_RE.search(p.name))
    return files


def _partition_key(src_file: Path) -> tuple[int, int]:
    """Return (year, month) for a YYYYMMDD.parq source file."""
    m = _DATE_RE.search(src_file.name)
    assert m, f"unexpected filename {src_file.name}"
    s = m.group(1)
    return int(s[:4]), int(s[4:6])


def _read_source(path: Path) -> pa.Table:
    """Read one source parquet, convert KL -> UTC, add exchange/session cols."""
    t = pq.read_table(path)
    df = t.to_pandas()
    # Source 'date' is KL-local naive datetime. Convert to UTC by subtracting +8h.
    df = df.rename(columns={"date": "ts"})
    df["ts"] = df["ts"] - pd.Timedelta(hours=KL_OFFSET_HOURS)
    # Session label: derive from KL-local time
    kl_minutes = (df["ts"] + pd.Timedelta(hours=KL_OFFSET_HOURS)).dt.hour * 60 + \
                 (df["ts"] + pd.Timedelta(hours=KL_OFFSET_HOURS)).dt.minute
    df["exchange"] = EXCHANGE
    df["session"] = (kl_minutes < 12 * 60 + 30).map({True: "morning", False: "afternoon"})
    # Ensure dtypes
    df["code"] = df["code"].astype(str)
    df["volume"] = df["volume"].astype("int64")
    df["value"] = df["value"].astype("float64")
    df = df[["ts", "code", "open", "high", "low", "close", "volume", "value",
             "exchange", "session"]]
    return pa.Table.from_pandas(df, preserve_index=False)


def _write_partition(
    tbl: pa.Table, out_path: Path, sort_keys: tuple[str, ...] = ("code", "ts")
) -> None:
    """Write a single partition parquet with ZSTD-3, large row groups, sorted."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Sort
    tbl = tbl.sort_by([(k, "ascending") for k in sort_keys])
    # Cast exchange/session to dictionary (categorical)
    exch_idx = tbl.schema.get_field_index("exchange")
    sess_idx = tbl.schema.get_field_index("session")
    if exch_idx >= 0:
        tbl = tbl.set_column(
            exch_idx, "exchange", tbl.column("exchange").dictionary_encode()
        )
    if sess_idx >= 0:
        tbl = tbl.set_column(
            sess_idx, "session", tbl.column("session").dictionary_encode()
        )
    pq.write_table(
        tbl,
        out_path,
        compression="zstd",
        compression_level=3,
        row_group_size=1_500_000,  # ~128MB for our row size
        use_dictionary=True,
        write_statistics=True,
    )


def _load_manifest(dst_root: Path) -> dict[str, dict]:
    """Load existing manifest as {partition_path: row_dict} or empty dict."""
    mpath = dst_root / "_manifest.parquet"
    if not mpath.exists():
        return {}
    df = pq.read_table(mpath).to_pandas()
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        out[str(r["partition_path"])] = {
            "partition_path": str(r["partition_path"]),
            "source_files": list(r["source_files"]),
            "source_sha": list(r["source_sha"]),
            "partition_sha": str(r["partition_sha"]),
            "n_rows": int(r["n_rows"]),
            "written_at": r["written_at"],
            "code_version": str(r["code_version"]),
        }
    return out


def _write_manifest(dst_root: Path, rows: list[dict]) -> None:
    """Write the manifest parquet, sorted deterministically by partition_path."""
    rows = sorted(rows, key=lambda r: r["partition_path"])
    schema = pa.schema([
        ("partition_path", pa.string()),
        ("source_files", pa.list_(pa.string())),
        ("source_sha", pa.list_(pa.string())),
        ("partition_sha", pa.string()),
        ("n_rows", pa.int64()),
        ("written_at", pa.timestamp("ns")),
        ("code_version", pa.string()),
    ])
    arrays = [
        pa.array([r["partition_path"] for r in rows], type=pa.string()),
        pa.array([r["source_files"] for r in rows], type=pa.list_(pa.string())),
        pa.array([r["source_sha"] for r in rows], type=pa.list_(pa.string())),
        pa.array([r["partition_sha"] for r in rows], type=pa.string()),
        pa.array([r["n_rows"] for r in rows], type=pa.int64()),
        pa.array([r["written_at"] for r in rows], type=pa.timestamp("ns")),
        pa.array([r["code_version"] for r in rows], type=pa.string()),
    ]
    tbl = pa.Table.from_arrays(arrays, schema=schema)
    out = dst_root / "_manifest.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(tbl, out, compression="zstd", compression_level=3)


def migrate(
    src_dir: Path = DEFAULT_SRC, dst_root: Path = DEFAULT_DST, force: bool = False
) -> list[dict]:
    """Migrate source by-date parquets into hive layout. Returns manifest rows.

    Idempotent. If `force=True`, rewrites every partition regardless of SHA match.
    """
    src_files = _list_source_files(src_dir)
    if not src_files:
        raise FileNotFoundError(f"no source parquets found in {src_dir}")

    # Group by (year, month)
    by_partition: dict[tuple[int, int], list[Path]] = {}
    for sf in src_files:
        by_partition.setdefault(_partition_key(sf), []).append(sf)

    # Compute source SHAs (cached per file)
    sha_cache: dict[Path, str] = {}
    print(f"hashing {len(src_files)} source files...", flush=True)
    for sf in src_files:
        sha_cache[sf] = _sha256_file(sf)

    existing = _load_manifest(dst_root)
    code_version = _git_sha()
    rows: list[dict] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for (yr, mo), parts in sorted(by_partition.items()):
        parts = sorted(parts)  # deterministic
        rel = f"exchange={EXCHANGE}/year={yr}/month={mo:02d}/part-00000.parquet"
        out_path = dst_root / rel
        src_names = [p.name for p in parts]
        src_shas = [sha_cache[p] for p in parts]

        prior = existing.get(rel)
        unchanged = (
            not force
            and prior is not None
            and out_path.exists()
            and list(prior["source_files"]) == src_names
            and list(prior["source_sha"]) == src_shas
        )
        if unchanged:
            print(f"  skip  {rel}  ({len(parts)} src, {prior['n_rows']:,} rows)")
            rows.append(prior)
            continue

        # Read+merge all source files for this (year, month)
        tables = [_read_source(p) for p in parts]
        merged = pa.concat_tables(tables)
        _write_partition(merged, out_path)
        psha = _sha256_file(out_path)
        n = merged.num_rows
        size_mb = out_path.stat().st_size / (1 << 20)
        print(f"  write {rel}  ({len(parts)} src, {n:,} rows, {size_mb:.1f} MB)")
        rows.append({
            "partition_path": rel,
            "source_files": src_names,
            "source_sha": src_shas,
            "partition_sha": psha,
            "n_rows": n,
            "written_at": now,
            "code_version": code_version,
        })

    _write_manifest(dst_root, rows)
    print(f"\nwrote manifest: {dst_root / '_manifest.parquet'}")
    print(f"  partitions: {len(rows)}")
    print(f"  total rows: {sum(r['n_rows'] for r in rows):,}")
    return rows


# Local import to avoid making pandas a top-level dependency announcement
import pandas as pd  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--dst", type=Path, default=DEFAULT_DST)
    ap.add_argument("--force", action="store_true",
                    help="rewrite every partition regardless of SHA match")
    args = ap.parse_args(argv)
    migrate(args.src, args.dst, force=args.force)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
