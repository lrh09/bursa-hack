"""As-of-date, liquidity-screened universe (intraday-aware).

Scalability note: per-asof results are cached on disk as
`data/intraday/_universe_cache/<sha>.parquet` so a 10y backtest doesn't
recompute the universe N x N times. The cache key folds in:
  - universe definition fields (top_n, lookback_days, min_session_minutes)
  - asof date
  - the snapshot hash of the underlying data slice the membership read

That way the cache is sound under data churn: a new month landing later
DOES change the asof keys that read from that month, but earlier asofs
stay valid.

Hard invariant (asserted): `members_as_of(asof)` MUST refuse to read any
data with ts >= asof local-KL. The PyArrow read explicitly passes a
`(ts < asof_utc)` filter, asserted as the only call in the property test.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field

from bursahack.intraday.calendar import ExchangeCalendar, KL_OFFSET_HOURS


def _default_data_root() -> Path:
    from bursahack.paths import REPO_ROOT
    return REPO_ROOT / "data" / "intraday"


def _kl_date_to_utc_naive(d: date) -> datetime:
    """Return the UTC instant corresponding to KL midnight on date `d`."""
    # KL midnight = UTC (d - 8h) ; we return midnight-UTC of (d - 1 day) + 16h
    # Equivalently: subtract KL_OFFSET_HOURS from KL midnight.
    kl_midnight = datetime.combine(d, datetime.min.time())
    return kl_midnight - timedelta(hours=KL_OFFSET_HOURS)


class Universe(BaseModel):
    """Liquidity-screened, monthly-rebalanced universe.

    Selection algorithm (per asof):
      1. Look at trailing `lookback_days` STRICTLY BEFORE asof (KL local).
      2. Per code, compute (median daily traded value) and
         (median daily active-minute count) where "active minute" = volume>0
         outside the phantom break.
      3. Drop codes with median active minutes < `min_session_minutes`.
      4. Rank remaining by median daily value, keep top `top_n`.

    `min_session_minutes=100` follows Phase 1 finding "top-30 by value still
    have 42% flat minutes" -> ~227 real-active minutes in a 360-min real session;
    100 is a safe inclusivity floor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = "top100_60d_adv"
    top_n: int = 100
    lookback_days: int = 60
    rebalance_freq: str = "M"   # "M" = monthly
    min_session_minutes: int = 100
    exchange: str = "XKLS"

    # ------------------------------ keys ------------------------------

    def _spec_key(self) -> str:
        """Deterministic hash of the universe definition (no asof)."""
        payload = json.dumps(
            self.model_dump(), sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        return hashlib.sha256(payload).hexdigest()[:16]

    def _cache_key(self, asof: date, data_root: Path) -> tuple[Path, str]:
        from bursahack.intraday.loader import snapshot_hash
        start = asof - timedelta(days=self.lookback_days + 7)
        try:
            snap = snapshot_hash(start, asof - timedelta(days=1), data_root)
        except FileNotFoundError:
            snap = "no-manifest"
        key = f"{self._spec_key()}_{asof.isoformat()}_{snap[:16]}"
        return data_root / "_universe_cache" / f"{key}.parquet", key

    # ------------------------------ read ------------------------------

    def _read_window(
        self, start: date, asof: date, data_root: Path
    ) -> pa.Table:
        """Read parquet rows with ts strictly less than asof KL midnight.

        We pass the asof filter explicitly to pyarrow so the property test
        can intercept it.
        """
        asof_utc = _kl_date_to_utc_naive(asof)
        start_utc = _kl_date_to_utc_naive(start)
        partitions = sorted((data_root).glob(
            f"exchange={self.exchange}/year=*/month=*/*.parquet"
        ))
        if not partitions:
            raise FileNotFoundError(
                f"no parquet partitions for exchange={self.exchange} under {data_root}"
            )
        filt = [
            ("ts", ">=", pa.scalar(start_utc, pa.timestamp("ns"))),
            ("ts", "<", pa.scalar(asof_utc, pa.timestamp("ns"))),
        ]
        tables: list[pa.Table] = []
        for p in partitions:
            t = pq.read_table(
                p,
                columns=["ts", "code", "volume", "value"],
                filters=filt,
            )
            if t.num_rows:
                tables.append(t)
        if not tables:
            return pa.table({
                "ts": pa.array([], type=pa.timestamp("ns")),
                "code": pa.array([], type=pa.string()),
                "volume": pa.array([], type=pa.int64()),
                "value": pa.array([], type=pa.float64()),
            })
        return pa.concat_tables(tables)

    # ------------------------------ compute ------------------------------

    def members_as_of(
        self, asof: date, data_root: Path | None = None
    ) -> list[str]:
        """Return ordered list of `top_n` codes selected as of `asof`.

        STRICTLY uses data with ts < asof (KL midnight). The selection looks
        back `lookback_days` calendar days.
        """
        root = data_root or _default_data_root()
        cache_path, _key = self._cache_key(asof, root)
        if cache_path.exists():
            df = pl.read_parquet(cache_path)
            return df.get_column("code").to_list()

        start = asof - timedelta(days=self.lookback_days)
        tbl = self._read_window(start, asof, root)
        if tbl.num_rows == 0:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame({"code": [], "rank": []}, schema={
                "code": pl.Utf8, "rank": pl.Int64
            }).write_parquet(cache_path)
            return []

        df = pl.from_arrow(tbl)
        # KL local date
        df = df.with_columns([
            (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.date().alias("kl_date"),
            # KL minutes-of-day. Cast to Int32 -- dt.hour/dt.minute return u8 (overflows on *60).
            (
                (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.hour().cast(pl.Int32) * 60
                + (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.minute().cast(pl.Int32)
            ).alias("_kl_min"),
        ])
        df = df.filter(~((pl.col("_kl_min") >= 750) & (pl.col("_kl_min") < 870)))

        # Per (code, day): sum value, count active minutes
        per_day = df.group_by(["code", "kl_date"]).agg([
            pl.col("value").sum().alias("day_value"),
            (pl.col("volume") > 0).sum().alias("active_min"),
        ])
        # Per code: medians across the days observed
        per_code = per_day.group_by("code").agg([
            pl.col("day_value").median().alias("med_value"),
            pl.col("active_min").median().alias("med_active_min"),
            pl.col("kl_date").n_unique().alias("n_days"),
        ])
        # Liquidity screen + rank
        eligible = per_code.filter(
            pl.col("med_active_min") >= self.min_session_minutes
        ).sort("med_value", descending=True).head(self.top_n)
        codes = eligible.get_column("code").to_list()

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({
            "code": codes,
            "rank": list(range(1, len(codes) + 1)),
        }).write_parquet(cache_path)
        return codes

    # ----------------------------- range -----------------------------

    def members_for_range(
        self, start: date, end: date, data_root: Path | None = None
    ) -> pl.DataFrame:
        """Materialize daily membership over [start, end] (KL session dates).

        Rebalance only on the first session day of each month; other days
        inherit the previous month's set. Cold-start: if no rebalance has
        been seen yet, the first session in `start`'s month is the bootstrap.
        """
        root = data_root or _default_data_root()
        cal = ExchangeCalendar(self.exchange)
        sessions = cal.sessions(start, end)
        if not sessions:
            return pl.DataFrame({"date": [], "code": []}, schema={
                "date": pl.Date, "code": pl.Utf8
            })

        firsts = cal.first_session_of_each_month(start, end)
        # Cold-start: if the first session in window is not a "first-of-month"
        # (because window mid-starts a month), we still need a membership set
        # for that day. Use that session as the bootstrap rebalance.
        if sessions[0] not in firsts:
            firsts = [sessions[0]] + firsts

        # Compute membership at each rebalance date
        rebalance_members: dict[date, list[str]] = {}
        for r in firsts:
            rebalance_members[r] = self.members_as_of(r, root)

        # Walk every session and inherit the most recent rebalance
        rows_date: list[date] = []
        rows_code: list[str] = []
        current: list[str] = []
        firsts_set = set(firsts)
        for d in sessions:
            if d in firsts_set:
                current = rebalance_members[d]
            for c in current:
                rows_date.append(d)
                rows_code.append(c)

        return pl.DataFrame({"date": rows_date, "code": rows_code})


__all__ = ["Universe"]
