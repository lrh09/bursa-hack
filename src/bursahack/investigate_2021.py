"""Investigate the 2021 row-count + new-security anomaly.

DQ surfaced: 2,578 SECURITY_IDs first appear in 2021 vs ~20-50/year typical,
and total rows roughly double. Hypothesis: the vendor onboarded a second
data source mid-2021 that created shadow IDs for existing equity.

This script tests that hypothesis by checking FIGI / name / ticker-root
overlap between the 2021-onboarded cohort and pre-2021 securities.
"""
from __future__ import annotations

import re

import pandas as pd
import pyarrow.parquet as pq

from bursahack.paths import PARQUET_DIR, MASTER_PARQUET


def _hr(t: str) -> None:
    print(f"\n{'=' * 72}\n  {t}\n{'=' * 72}")


def _sub(t: str) -> None:
    print(f"\n--- {t} ---")


CUTOFF = pd.Timestamp("2021-01-01")


def load_master() -> pd.DataFrame:
    df = pd.read_parquet(MASTER_PARQUET)
    df["first_date"] = pd.to_datetime(df["first_date"])
    df["last_date"] = pd.to_datetime(df["last_date"])
    return df


def root_ticker(t: str | None) -> str:
    if t is None or not isinstance(t, str):
        return ""
    m = re.match(r"^(\d+)([A-Z]*)(?::MK)?$", t)
    return m.group(1) if m else t


def main() -> None:
    master = load_master()
    print(f"total securities: {len(master):,}")

    pre = master[master["first_date"] < CUTOFF].copy()
    new = master[master["first_date"] >= CUTOFF].copy()
    print(f"  pre-2021 (first_date < 2021-01-01): {len(pre):,}")
    print(f"  new in 2021+:                       {len(new):,}")

    pre["root"] = pre["ticker"].map(root_ticker)
    new["root"] = new["ticker"].map(root_ticker)

    _hr("FIGI overlap")
    pre_figis = set(pre["figi"].dropna())
    new_figis_overlap = new["figi"].isin(pre_figis).sum()
    new_with_figi = new["figi"].notna().sum()
    print(f"  new-2021 securities with a FIGI:           {new_with_figi:,} / {len(new):,}")
    print(f"  of those, FIGI matches a pre-2021 sec:     {new_figis_overlap:,}")

    _hr("Ticker-root overlap (numeric part of ticker)")
    pre_roots = set(pre["root"]) - {""}
    new_roots_overlap = new["root"].isin(pre_roots).sum()
    print(f"  new-2021 securities with non-empty root:   {(new['root'] != '').sum():,}")
    print(f"  of those, root matches a pre-2021 ticker:  {new_roots_overlap:,}")

    _hr("Name overlap (exact)")
    pre_names = set(pre["name"].dropna().str.strip().str.upper())
    new_norm = new["name"].fillna("").str.strip().str.upper()
    new_name_overlap = new_norm.isin(pre_names).sum()
    print(f"  new-2021 securities with non-empty name:   {(new_norm != '').sum():,}")
    print(f"  of those, exact name match to pre-2021:    {new_name_overlap:,}")

    _hr("Any-key overlap (FIGI OR root OR name)")
    new["matches"] = (
        new["figi"].isin(pre_figis)
        | new["root"].isin(pre_roots)
        | new_norm.isin(pre_names)
    )
    matched = new["matches"].sum()
    print(f"  new-2021 securities matched on ANY key:    {matched:,}  ({matched / len(new):.1%})")
    print(f"  new-2021 securities NOT matched anywhere:  {len(new) - matched:,}")

    _sub("ticker-suffix breakdown of unmatched new-2021 cohort")
    unmatched = new[~new["matches"]].copy()
    unmatched["suffix"] = unmatched["ticker"].map(
        lambda t: re.match(r"^\d+([A-Z]*)(?::MK)?$", str(t) or "").group(1)
        if re.match(r"^\d+([A-Z]*)(?::MK)?$", str(t) or "")
        else "(non-standard)"
    )
    by_suf = unmatched.groupby("suffix").size().sort_values(ascending=False)
    for suf, n in by_suf.head(15).items():
        print(f"    {suf!r:<20} {n:>5,}")

    _sub("sample unmatched new-2021 securities")
    print(unmatched[["ticker", "name", "first_date", "last_date", "rows"]].head(10).to_string(index=False))

    _hr("Proposed dedupe")
    same_root = new[new["root"].isin(pre_roots)].copy()
    print(f"  same-root collisions (likely shadow IDs):  {len(same_root):,}")
    print("  Action: for each new-2021 SECURITY_ID whose root matches a pre-2021 ticker,")
    print("          either drop it OR remap to the pre-2021 SECURITY_ID and concat series.")
    print()
    print("  Need to check first: do the price series OVERLAP in time (i.e. both IDs trade")
    print("  in 2021), or does the new ID appear only after the pre-2021 ID stops trading?")

    _sub("Time overlap check on root-collision cohort")
    pre_by_root = pre.set_index("root")[["SECURITY_ID", "last_date"]].rename(
        columns={"SECURITY_ID": "pre_id", "last_date": "pre_last"}
    )
    same_root_join = same_root.merge(
        pre_by_root, left_on="root", right_index=True, how="left"
    )
    same_root_join["pre_stopped_before_new_starts"] = (
        same_root_join["pre_last"] < same_root_join["first_date"]
    )
    overlap = (~same_root_join["pre_stopped_before_new_starts"]).sum()
    nonoverlap = same_root_join["pre_stopped_before_new_starts"].sum()
    print(f"  pre-id stopped trading BEFORE new-id starts (clean handoff): {nonoverlap:,}")
    print(f"  pre-id and new-id BOTH trade simultaneously (true dupe):     {overlap:,}")


if __name__ == "__main__":
    main()
