"""VWAP Reclaim directional strategy -- Zarattini-Aziz 2023, Madhavan 2002.

Mechanic, plain English:
  - Intraday VWAP is the institutional fair-value reference. We compute a
    RUNNING VWAP per (code, KL-session-day), reset every session day:
        VWAP_t = cumsum(price * volume) / cumsum(volume)   over bars 1..t
    where `price` is either the bar close or the typical price
    (high + low + close) / 3, selectable via the `vwap_price` param. VWAP at
    bar t is contemporaneous and observable (uses bars 1..t inclusive), so it
    is not lookahead.
  - A reclaim is a directional signal: after price has sat BELOW VWAP for at
    least `min_below_minutes` consecutive bars and then crosses back ABOVE it
        close[t-1] <= vwap[t-1]  AND  close[t] > vwap[t]
    we emit a LONG at bar t. Mirror for SHORT (sustained ABOVE, cross below).
  - The sustained-departure count at bar t uses only bars up to t (it is the
    run length of "below-VWAP" ending at the prior bar, t-1). The cross test
    uses t-1 and t. No bar > t is ever read.
  - Stop is `entry * (1 - stop_pct/100)` for longs, mirrored for shorts.
    Engine handles the stop-touch exit; `close` is the entry-price hint
    (engine overrides with next-bar-open).
  - Time-based exit per `exit_policy`, same shape as ORB / LMSW.

Iron rules:
  - Stateless: pure-polars expression graph, no module-level state.
  - No lookahead: VWAP/run-length use only bars <= t; cross uses t-1, t.
  - Filters zero-volume bars (phantom KL-lunch leftovers) before any stats,
    so the running VWAP isn't poisoned by midday phantom prints.
  - Only fires within the regular session window (09:00..16:59 KL).
  - One signal per (code, KL-date) max -- keep the first.
"""
from __future__ import annotations

from typing import ClassVar, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict

from bursahack.intraday.calendar import KL_OFFSET_HOURS
from bursahack.intraday.registry import (
    SIGNAL_SCHEMA,
    register_strategy,
    validate_signal_frame,
)


# Same KL session window the ORB / LMSW strategies use.
SESSION_OPEN_KL_MIN: int = 9 * 60        # 09:00 KL = minute 540
SESSION_CLOSE_KL_MIN: int = 16 * 60 + 59  # 16:59 KL = minute 1019


_EXIT_POLICY_TO_KL_MIN: dict[str, int | None] = {
    "session_close": None,          # engine handles
    "kl_15_00": 15 * 60,            # 900
    "kl_16_00": 16 * 60,            # 960
}


class VWAPReclaimParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    vwap_price: Literal["close", "typical"] = "close"
    min_below_minutes: Literal[15, 30, 60] = 15
    stop_pct: Literal[0.5, 1.0, 1.5, 2.0] = 1.0
    exit_policy: Literal["session_close", "kl_15_00", "kl_16_00"] = "session_close"
    side: Literal["long", "short", "both"] = "both"


@register_strategy(
    "vwap_reclaim",
    description="VWAP Reclaim directional (Zarattini-Aziz 2023)",
)
class VWAPReclaimStrategy:
    """Stateless VWAP-reclaim signal generator."""

    name: ClassVar[str] = "vwap_reclaim"
    params_model: ClassVar[type[BaseModel]] = VWAPReclaimParams
    version: ClassVar[str] = "1.0.0"

    def generate_signals(
        self,
        bars: pl.LazyFrame | pl.DataFrame,
        universe_members: pl.DataFrame | None,
        params: VWAPReclaimParams,
    ) -> pl.DataFrame:
        if isinstance(bars, pl.LazyFrame):
            bars = bars.collect()
        if bars.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        # Filter zero-volume bars before any stats (phantom KL-lunch leftovers).
        # A zero-volume bar contributes nothing to a value-weighted average and
        # would only break the consecutive-bar run logic.
        df = bars.filter(pl.col("volume") > 0).sort(["code", "ts"])
        if df.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        # KL-minute + KL-date columns. Cast hour/min to Int32 FIRST (u8 trap:
        # polars hour/minute return UInt8 and `hour*60` silently overflows).
        df = df.with_columns([
            (
                (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS))
                    .dt.hour().cast(pl.Int32) * 60
                + (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS))
                    .dt.minute().cast(pl.Int32)
            ).alias("_kl_min"),
            (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS))
                .dt.date().alias("_kl_date"),
        ])

        # VWAP price source.
        if params.vwap_price == "typical":
            price_expr = (pl.col("high") + pl.col("low") + pl.col("close")) / 3.0
        else:
            price_expr = pl.col("close")
        df = df.with_columns(price_expr.alias("_vwap_px"))

        # Running intraday VWAP, RESET per (code, KL-date). cum_sum over the
        # ordered bars within each session day. Uses bars 1..t inclusive of t,
        # which is observable -> not lookahead.
        df = df.with_columns([
            (pl.col("_vwap_px") * pl.col("volume"))
                .cum_sum().over(["code", "_kl_date"]).alias("_pv_cum"),
            pl.col("volume")
                .cum_sum().over(["code", "_kl_date"]).alias("_v_cum"),
        ])
        df = df.with_columns(
            (pl.col("_pv_cum") / pl.col("_v_cum")).alias("_vwap")
        )

        # Per-bar position relative to VWAP, using the bar's CLOSE (the price
        # the cross is measured on, matching the brief's cross condition).
        df = df.with_columns([
            (pl.col("close") > pl.col("_vwap")).alias("_above"),
            (pl.col("close") < pl.col("_vwap")).alias("_below"),
        ])

        # Run length (consecutive bars) of "strictly below VWAP" and "strictly
        # above VWAP", ending at each bar, computed per (code, KL-date).
        # Technique: a new run starts whenever the boolean flips; cum_sum of
        # those starts gives a run id; cum_count within (run id) is the length.
        df = self._with_run_lengths(df, flag="_below", out="_below_run")
        df = self._with_run_lengths(df, flag="_above", out="_above_run")

        # The "sustained-departure" count we need at bar t is the run length
        # ending at the PRIOR bar (t-1). Shift within the session day.
        df = df.with_columns([
            pl.col("_below_run").shift(1).over(["code", "_kl_date"]).alias("_below_run_prev"),
            pl.col("_above_run").shift(1).over(["code", "_kl_date"]).alias("_above_run_prev"),
            pl.col("_vwap").shift(1).over(["code", "_kl_date"]).alias("_vwap_prev"),
            pl.col("close").shift(1).over(["code", "_kl_date"]).alias("_close_prev"),
        ])

        # Restrict signals to the regular session window.
        df = df.filter(
            (pl.col("_kl_min") >= SESSION_OPEN_KL_MIN)
            & (pl.col("_kl_min") <= SESSION_CLOSE_KL_MIN)
        )

        thresh = int(params.min_below_minutes)

        signals: list[pl.DataFrame] = []

        if params.side in ("long", "both"):
            # Long reclaim: prior close <= prior VWAP (was at/below), current
            # close > current VWAP (now above), AND the below-run ending at t-1
            # was >= threshold bars.
            long_cands = df.filter(
                pl.col("_close_prev").is_not_null()
                & pl.col("_vwap_prev").is_not_null()
                & (pl.col("_close_prev") <= pl.col("_vwap_prev"))
                & (pl.col("close") > pl.col("_vwap"))
                & (pl.col("_below_run_prev").fill_null(0) >= thresh)
            )
            long_sigs = long_cands.select([
                pl.col("ts"),
                pl.col("code"),
                pl.lit(1, dtype=pl.Int8).alias("side"),
                pl.col("close").alias("entry_price"),
                (pl.col("close") * (1.0 - params.stop_pct / 100.0)).alias("stop_price"),
                pl.lit(None, dtype=pl.Float64).alias("target_price"),
                _exit_at_expr(params).alias("exit_at"),
            ])
            signals.append(long_sigs)

        if params.side in ("short", "both"):
            # Short reclaim (mirror): prior close >= prior VWAP, current close
            # < current VWAP, AND the above-run ending at t-1 was >= threshold.
            short_cands = df.filter(
                pl.col("_close_prev").is_not_null()
                & pl.col("_vwap_prev").is_not_null()
                & (pl.col("_close_prev") >= pl.col("_vwap_prev"))
                & (pl.col("close") < pl.col("_vwap"))
                & (pl.col("_above_run_prev").fill_null(0) >= thresh)
            )
            short_sigs = short_cands.select([
                pl.col("ts"),
                pl.col("code"),
                pl.lit(-1, dtype=pl.Int8).alias("side"),
                pl.col("close").alias("entry_price"),
                (pl.col("close") * (1.0 + params.stop_pct / 100.0)).alias("stop_price"),
                pl.lit(None, dtype=pl.Float64).alias("target_price"),
                _exit_at_expr(params).alias("exit_at"),
            ])
            signals.append(short_sigs)

        if not signals:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        out = pl.concat(signals)
        if out.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        # Per (code, kl_date) keep the first signal only (earliest ts). When
        # side=="both" a long and short can't fire on the same bar, but could
        # fire on different bars in the same session -- the earlier one wins.
        out = out.with_columns(
            (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.date().alias("_d")
        ).sort(["code", "_d", "ts"]).group_by(
            ["code", "_d"], maintain_order=True
        ).first().drop("_d")

        return validate_signal_frame(out)

    @staticmethod
    def _with_run_lengths(df: pl.DataFrame, *, flag: str, out: str) -> pl.DataFrame:
        """Add `out` = consecutive run length of `flag==True` ending at each
        bar, computed per (code, KL-date). When `flag` is False the run length
        is 0.

        Run id = cum_sum of "this bar starts a new block" (block boundary is
        any change in the flag value vs the previous bar). The length within a
        True block is the cumulative count of bars since that block began.
        """
        d = df.with_columns(
            # A new block starts when the flag differs from the previous bar
            # (or there is no previous bar in this session).
            (
                pl.col(flag) != pl.col(flag).shift(1).over(["code", "_kl_date"])
            ).fill_null(True).alias("_blk_start")
        )
        d = d.with_columns(
            pl.col("_blk_start").cum_sum().over(["code", "_kl_date"]).alias("_blk_id")
        )
        # cumulative count within (code, kl_date, block); 1-based.
        d = d.with_columns(
            pl.int_range(1, pl.len() + 1)
              .over(["code", "_kl_date", "_blk_id"]).alias("_blk_pos")
        )
        d = d.with_columns(
            pl.when(pl.col(flag)).then(pl.col("_blk_pos")).otherwise(0).alias(out)
        )
        return d.drop(["_blk_start", "_blk_id", "_blk_pos"])


def _exit_at_expr(params: VWAPReclaimParams) -> pl.Expr:
    """Polars expression for the `exit_at` timestamp column."""
    target_kl_min = _EXIT_POLICY_TO_KL_MIN.get(params.exit_policy)
    if target_kl_min is None:
        return pl.lit(None, dtype=pl.Datetime("ns"))
    # exit_at = midnight-KL-date + target_kl_min minutes, expressed in UTC.
    # KL midnight in UTC = KL midnight - 8h.
    kl_date = (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.date()
    return (
        kl_date.cast(pl.Datetime("ns"))
        + pl.duration(minutes=target_kl_min)
        - pl.duration(hours=KL_OFFSET_HOURS)
    )


__all__ = [
    "VWAPReclaimParams",
    "VWAPReclaimStrategy",
    "SESSION_OPEN_KL_MIN",
    "SESSION_CLOSE_KL_MIN",
]
