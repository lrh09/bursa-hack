"""Canonical paths for the BursaHack project."""
from __future__ import annotations
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

RAW_CSV = REPO_ROOT / "stock_prices_xkls_all_file-1.csv"
DATA_DIR = REPO_ROOT / "data"
PARQUET_DIR = DATA_DIR / "parquet"
MASTER_PARQUET = DATA_DIR / "master.parquet"
RESULTS_DIR = REPO_ROOT / "results"
