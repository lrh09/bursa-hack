"""Data-quality report.

Surfaces the assumptions the backtest will rest on, so they're explicit
rather than implicit. Run with `uv run python -m bursahack.dq`.

Sections:
  1. Coverage      — securities + date range + rows per year
  2. Survivorship  — distribution of last-trade-date per security
  3. Symbology     — ticker suffix patterns (the `PR` question)
  4. Corp actions  — splits + dividends + ADJ_FACTOR consistency
  5. Liquidity     — price + ADV distribution, penny-stock floor candidates
  6. Suspensions   — zero-volume days + missing-day gaps
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from bursahack.paths import PARQUET_DIR, MASTER_PARQUET


def _hr(title: str) -> None:
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")


def _sub(title: str) -> None:
    print(f"\n--- {title} ---")


def load_master() -> pd.DataFrame:
    df = pd.read_parquet(MASTER_PARQUET)
    df["first_date"] = pd.to_datetime(df["first_date"])
    df["last_date"] = pd.to_datetime(df["last_date"])
    return df


def load_prices(columns: list[str] | None = None) -> pd.DataFrame:
    df = pq.read_table(PARQUET_DIR, columns=columns).to_pandas()
    if "DATE" in df.columns:
        df["DATE"] = pd.to_datetime(df["DATE"])
    return df


def section_coverage(master: pd.DataFrame, prices: pd.DataFrame) -> None:
    _hr("1. Coverage")
    print(f"  unique securities:  {len(master):,}")
    print(f"  total daily rows:   {len(prices):,}")
    print(f"  date range:         {prices['DATE'].min()}  ->  {prices['DATE'].max()}")
    print(f"  trading days:       {prices['DATE'].nunique():,}")

    _sub("rows per year")
    per_year = prices.groupby(prices["DATE"].dt.year).size()
    for yr, n in per_year.items():
        print(f"    {yr}: {n:>9,}")


def section_survivorship(master: pd.DataFrame, prices: pd.DataFrame) -> None:
    _hr("2. Survivorship")
    file_end = prices["DATE"].max()
    last_per_sec = master["last_date"]
    alive = (last_per_sec >= file_end - pd.Timedelta(days=14)).sum()
    dead = len(master) - alive
    print(f"  file ends:                       {file_end}")
    print(f"  securities still trading at end: {alive:,}  ({alive / len(master):.1%})")
    print(f"  securities that stop earlier:    {dead:,}  ({dead / len(master):.1%})")
    print("  note: if this is much less than ~30-50%, true survivorship coverage is suspect.")

    _sub("delistings by year")
    delisted = master[master["last_date"] < file_end - pd.Timedelta(days=14)]
    by_year = delisted.groupby(delisted["last_date"].dt.year).size()
    for yr, n in by_year.items():
        print(f"    {yr}: {n:>4,}")

    _sub("first-date distribution (listings)")
    new_listings = master.groupby(master["first_date"].dt.year).size()
    for yr, n in new_listings.items():
        print(f"    {yr}: {n:>4,}")


SUFFIX_RE = re.compile(r"^(\d+)([A-Z]*)(:MK)?$")


def section_symbology(master: pd.DataFrame, prices: pd.DataFrame) -> None:
    _hr("3. Symbology")
    tickers = master["ticker"].dropna().astype(str)

    suffixes: dict[str, int] = {}
    for t in tickers:
        m = SUFFIX_RE.match(t)
        if m:
            suf = m.group(2) or "(none)"
        else:
            suf = "(non-standard)"
        suffixes[suf] = suffixes.get(suf, 0) + 1

    _sub("ticker letter-suffix counts (numeric prefix + LETTERS)")
    for suf, n in sorted(suffixes.items(), key=lambda kv: -kv[1])[:15]:
        print(f"    {suf!r:<20} {n:>5,}")
    print("  note: guesses: PA=Warrant call, PR=Rights/PreferStock variant, WA=Warrant,")
    print("     PB/PC=structured warrants, etc. We'll likely filter all letter-suffixed")
    print("     names out of the equity-momentum universe (they trade differently).")

    _sub("sample PR-suffixed names")
    pr = master[master["ticker"].astype(str).str.match(r"^\d+PR$")]
    print(f"  count: {len(pr):,}")
    print(pr[["ticker", "name", "first_date", "last_date"]].head(8).to_string(index=False))


def section_corp_actions(prices: pd.DataFrame) -> None:
    _hr("4. Corp actions")
    splits = prices[prices["SPLIT_RATIO"].fillna(1.0) != 1.0]
    divs = prices[prices["EX_DIVIDEND"].fillna(0.0) != 0.0]
    print(f"  split events:        {len(splits):,}")
    print(f"  dividend events:     {len(divs):,}")
    print(f"  unique sec w/ split: {splits['SECURITY_ID'].nunique():,}")
    print(f"  unique sec w/ div:   {divs['SECURITY_ID'].nunique():,}")

    _sub("ADJ_FACTOR distribution")
    af = prices["ADJ_FACTOR"].dropna()
    pct = af.quantile([0.0, 0.01, 0.5, 0.99, 1.0])
    for q, v in pct.items():
        print(f"    p{q * 100:>5.1f}: {v}")
    print(f"  rows where ADJ_FACTOR != 1.0: {(af != 1.0).sum():,}  ({(af != 1.0).mean():.2%})")
    print("  note: sanity-check: ADJ vs raw price agreement on a no-corp-action sample.")

    _sub("adj vs raw close consistency (sample 1,000 no-action rows)")
    no_action = prices[(prices["ADJ_FACTOR"] == 1.0) & (prices["CLOSE"].notna()) & (prices["ADJ_CLOSE"].notna())]
    if len(no_action):
        s = no_action.sample(min(1000, len(no_action)), random_state=0)
        diff = (s["ADJ_CLOSE"] - s["CLOSE"]).abs()
        print(f"    max  diff: {diff.max():.6f}")
        print(f"    mean diff: {diff.mean():.6f}")
        print("    note: should be ~0 for unsplit/undividended rows.")


def section_liquidity(prices: pd.DataFrame) -> None:
    _hr("5. Liquidity")
    valid = prices[prices["ADJ_CLOSE"].notna() & prices["ADJ_VOLUME"].notna()]

    _sub("ADJ_CLOSE distribution (RM)")
    for q in [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]:
        print(f"    p{q * 100:>5.1f}: {valid['ADJ_CLOSE'].quantile(q):>8.3f}")

    _sub("daily traded value (RM = adj_close x adj_volume)")
    tv = valid["ADJ_CLOSE"] * valid["ADJ_VOLUME"]
    for q in [0.05, 0.25, 0.5, 0.75, 0.95, 0.99]:
        print(f"    p{q * 100:>5.1f}: RM {tv.quantile(q):>14,.0f}")

    _sub("penny-stock floor candidates")
    for floor in [0.05, 0.10, 0.20, 0.50, 1.00]:
        kept = (valid["ADJ_CLOSE"] >= floor).mean()
        print(f"    price >= RM {floor:>5.2f}:  keeps {kept:.1%} of rows")

    _sub("liquidity floor candidates (single-day traded value)")
    for floor in [50_000, 100_000, 250_000, 500_000, 1_000_000]:
        kept = (tv >= floor).mean()
        print(f"    daily $ >= RM {floor:>9,}:  keeps {kept:.1%} of rows")


def section_suspensions(prices: pd.DataFrame, master: pd.DataFrame) -> None:
    _hr("6. Suspensions / missing days")
    zero_vol = prices["VOLUME"].fillna(0) == 0
    print(f"  zero-volume rows:        {zero_vol.sum():,}  ({zero_vol.mean():.2%})")
    nan_close = prices["ADJ_CLOSE"].isna()
    print(f"  NaN ADJ_CLOSE rows:      {nan_close.sum():,}  ({nan_close.mean():.2%})")
    nan_vol = prices["VOLUME"].isna()
    print(f"  NaN VOLUME rows:         {nan_vol.sum():,}  ({nan_vol.mean():.2%})")

    _sub("median gap between trading days (per security)")
    df = prices[["SECURITY_ID", "DATE"]].sort_values(["SECURITY_ID", "DATE"])
    df["gap"] = df.groupby("SECURITY_ID")["DATE"].diff().dt.days
    gap = df["gap"].dropna()
    for q in [0.5, 0.9, 0.99, 0.999, 1.0]:
        print(f"    p{q * 100:>5.1f}: {gap.quantile(q):.0f} days")
    print("  note: normal market gap: 1 day weekdays, 3 days Fri->Mon, more across holidays.")
    print(f"    rows w/ gap > 7 days (likely suspension/halt): {(gap > 7).sum():,}")
    print(f"    rows w/ gap > 30 days:                          {(gap > 30).sum():,}")


def main() -> None:
    print("Loading parquet...")
    master = load_master()
    prices = load_prices(columns=[
        "SECURITY_ID", "TICKER", "DATE",
        "CLOSE", "VOLUME",
        "ADJ_CLOSE", "ADJ_VOLUME", "ADJ_FACTOR",
        "EX_DIVIDEND", "SPLIT_RATIO",
    ])

    section_coverage(master, prices)
    section_survivorship(master, prices)
    section_symbology(master, prices)
    section_corp_actions(prices)
    section_liquidity(prices)
    section_suspensions(prices, master)


if __name__ == "__main__":
    main()
