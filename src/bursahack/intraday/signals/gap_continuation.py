"""Gap Continuation strategy -- Caginalp-Laurent 1998 / Crabel 1990.

Mechanic, plain English:
  - A large opening gap on confirming volume tends to lead to a TREND DAY in
    the gap direction. (Small gaps tend to revert -- that is the "fade" / gap-
    fill setup, which is the OPPOSITE of what this strategy trades. We build
    CONTINUATION only: trade IN the gap direction.)
  - Per (code, KL-session-day):
      prev_close   = the previous session's last close (lagged via shift over
                     the per-code daily series, ordered by KL-date).
      today_open   = first bar's open of the current session.
      gap          = (today_open - prev_close) / prev_close.
  - Normalize by daily volatility:
      ATR_20d      = 20-session rolling mean of daily (high-low)/prev_close,
                     using ONLY prior sessions (shift(1) THEN rolling).
      gap_z        = gap / ATR_20d.
  - Volume confirmation WITHOUT lookahead:
      first_window_volume = total volume of the first `or_minutes` bars of the
                            CURRENT session (these are in the PAST relative to
                            the signal bar, which fires at/after the window
                            closes -- so observing them is legal).
      baseline            = per-code median of PRIOR sessions' same-window
                            (first `or_minutes` bars) total volume, lagged via
                            shift(1) over the per-code daily series.
      Confirmed iff first_window_volume >= vol_mult * baseline.
  - Signal:
      gap_z >= +gap_z_threshold AND confirmed AND side in {long, both}
          -> LONG, entered on the first bar at/after KL minute (540+or_minutes).
      gap_z <= -gap_z_threshold AND confirmed AND side in {short, both}
          -> SHORT, same entry timing.
      Continuation => trade IN the gap direction.
  - Stop:
      long  -> entry * (1 - stop_pct/100)
      short -> entry * (1 + stop_pct/100)
    entry_price hint = close of the signal bar (engine fills next-bar-open).
  - One signal per (code, KL-date) maximum. Zero-volume bars filtered first.

No-lookahead invariant (this strategy is HIGH RISK for it):
  - prev_close, ATR_20d, and the volume baseline ALL use only PRIOR sessions
    (shift(1) over the per-code daily series ordered by KL-date, applied
    BEFORE any rolling). The gap uses today's open (observable at session
    start) and prev_close (past). The volume-confirm window is the first
    `or_minutes` bars; the signal is emitted only at/after that window closes,
    so those bars are in the past relative to the signal bar. We NEVER read any
    bar after the signal bar.

Iron rules:
  - Stateless: pure-polars expression graph, no module-level state.
  - Accept LazyFrame | DataFrame; return validate_signal_frame(...).
  - Empty input -> empty SignalFrame.
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


SESSION_OPEN_KL_MIN: int = 9 * 60        # 09:00 KL = minute 540
SESSION_CLOSE_KL_MIN: int = 16 * 60 + 59  # 16:59 KL = minute 1019

# 20 prior sessions warm up the ATR; need a window of prior sessions for the
# volume baseline median too.
_ATR_WINDOW: int = 20


_EXIT_POLICY_TO_KL_MIN: dict[str, int | None] = {
    "session_close": None,          # engine handles
    "kl_15_00": 15 * 60,            # 900
    "kl_16_00": 16 * 60,            # 960
}


class GapContinuationParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gap_z_threshold: Literal[1.0, 1.5, 2.0] = 1.5
    vol_mult: Literal[1.5, 2.0, 3.0] = 2.0
    or_minutes: Literal[5, 15] = 5
    stop_pct: Literal[0.5, 1.0, 1.5, 2.0] = 1.0
    exit_policy: Literal["session_close", "kl_15_00", "kl_16_00"] = "session_close"
    side: Literal["long", "short", "both"] = "both"


@register_strategy(
    "gap_continuation",
    description="Gap continuation on confirming volume (Caginalp-Laurent)",
)
class GapContinuationStrategy:
    """Stateless gap-continuation signal generator."""

    name: ClassVar[str] = "gap_continuation"
    params_model: ClassVar[type[BaseModel]] = GapContinuationParams
    version: ClassVar[str] = "1.0.0"

    def generate_signals(
        self,
        bars: pl.LazyFrame | pl.DataFrame,
        universe_members: pl.DataFrame | None,
        params: GapContinuationParams,
    ) -> pl.DataFrame:
        if isinstance(bars, pl.LazyFrame):
            bars = bars.collect()
        if bars.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        # Filter zero-volume bars (phantom KL-lunch leftovers) before any stats.
        df = bars.filter(pl.col("volume") > 0).sort(["code", "ts"])
        if df.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        # KL-minute + KL-date. Cast hour/min to Int32 first (u8 *60 overflow).
        kl_shift = pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)
        df = df.with_columns([
            (
                kl_shift.dt.hour().cast(pl.Int32) * 60
                + kl_shift.dt.minute().cast(pl.Int32)
            ).alias("_kl_min"),
            kl_shift.dt.date().alias("_kl_date"),
        ])

        or_end = SESSION_OPEN_KL_MIN + int(params.or_minutes)  # exclusive

        # ---- Per (code, session-day) daily aggregates -------------------
        # daily high/low/last-close, today's open (first bar at/after open),
        # and the first-window total volume (the confirm window).
        session_bars = df.filter(
            (pl.col("_kl_min") >= SESSION_OPEN_KL_MIN)
            & (pl.col("_kl_min") <= SESSION_CLOSE_KL_MIN)
        )

        first_window = session_bars.filter(
            (pl.col("_kl_min") >= SESSION_OPEN_KL_MIN)
            & (pl.col("_kl_min") < or_end)
        )
        fw_vol = first_window.group_by(["code", "_kl_date"]).agg(
            pl.col("volume").sum().alias("_fw_vol")
        )

        daily = (
            session_bars.group_by(["code", "_kl_date"])
            .agg([
                pl.col("high").max().alias("_d_high"),
                pl.col("low").min().alias("_d_low"),
                # today's open = open of the first session bar (sorted by ts).
                pl.col("open").sort_by("ts").first().alias("_d_open"),
                pl.col("close").sort_by("ts").last().alias("_d_close"),
            ])
            .join(fw_vol, on=["code", "_kl_date"], how="left")
            .sort(["code", "_kl_date"])
        )

        # ---- Lagged prior-session stats (NO LOOKAHEAD) ------------------
        # prev_close: previous session's last close, lagged one day per code.
        daily = daily.with_columns(
            pl.col("_d_close").shift(1).over("code").alias("_prev_close"),
        )

        # gap = (today_open - prev_close) / prev_close.
        daily = daily.with_columns(
            ((pl.col("_d_open") - pl.col("_prev_close")) / pl.col("_prev_close"))
            .alias("_gap")
        )

        # Daily true-ish range proxy = (high - low) / prev_close. ATR_20d is the
        # 20-session rolling MEAN of this, using ONLY prior sessions: shift(1)
        # FIRST, then rolling_mean. The current session's range never enters.
        daily = daily.with_columns(
            ((pl.col("_d_high") - pl.col("_d_low")) / pl.col("_prev_close"))
            .alias("_d_range_pct")
        )
        daily = daily.with_columns(
            pl.col("_d_range_pct").shift(1).rolling_mean(window_size=_ATR_WINDOW)
            .over("code").alias("_atr20"),
            # Volume baseline = median of PRIOR sessions' first-window total
            # volume, lagged. shift(1) THEN rolling median over the same window.
            pl.col("_fw_vol").shift(1).rolling_median(window_size=_ATR_WINDOW)
            .over("code").alias("_vol_base"),
        )

        # gap_z = gap / ATR_20d. Null when ATR missing/zero (warmup).
        daily = daily.with_columns(
            pl.when(pl.col("_atr20").is_null() | (pl.col("_atr20") == 0.0))
            .then(None)
            .otherwise(pl.col("_gap") / pl.col("_atr20"))
            .alias("_gap_z")
        )

        # Volume confirmation: first_window_volume >= vol_mult * baseline.
        vol_mult = float(params.vol_mult)
        daily = daily.with_columns(
            (
                pl.col("_vol_base").is_not_null()
                & (pl.col("_vol_base") > 0.0)
                & (pl.col("_fw_vol") >= vol_mult * pl.col("_vol_base"))
            ).alias("_vol_ok")
        )

        gap_th = float(params.gap_z_threshold)

        # Direction per session: long if gap_z >= +th, short if gap_z <= -th.
        long_ok = params.side in ("long", "both")
        short_ok = params.side in ("short", "both")

        daily = daily.with_columns(
            pl.when(
                pl.col("_gap_z").is_not_null()
                & pl.col("_vol_ok")
                & (pl.col("_gap_z") >= gap_th)
                & pl.lit(long_ok)
            ).then(1)
            .when(
                pl.col("_gap_z").is_not_null()
                & pl.col("_vol_ok")
                & (pl.col("_gap_z") <= -gap_th)
                & pl.lit(short_ok)
            ).then(-1)
            .otherwise(0)
            .alias("_dir")
        )

        firing = daily.filter(pl.col("_dir") != 0).select(
            ["code", "_kl_date", "_dir"]
        )
        if firing.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        # ---- Entry bar: first bar at/after the confirm window closes -----
        # i.e. first bar with _kl_min >= or_end in the firing session.
        entry_pool = session_bars.filter(pl.col("_kl_min") >= or_end).join(
            firing, on=["code", "_kl_date"], how="inner"
        )
        if entry_pool.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        entry_bar = (
            entry_pool.sort(["code", "_kl_date", "ts"])
            .group_by(["code", "_kl_date"], maintain_order=True)
            .first()
        )

        sign = pl.col("_dir")
        stop_expr = pl.when(sign > 0).then(
            pl.col("close") * (1.0 - params.stop_pct / 100.0)
        ).when(sign < 0).then(
            pl.col("close") * (1.0 + params.stop_pct / 100.0)
        ).otherwise(None)

        sigs = entry_bar.select([
            pl.col("ts"),
            pl.col("code"),
            sign.cast(pl.Int8).alias("side"),
            pl.col("close").alias("entry_price"),  # hint; engine fills next-bar-open
            stop_expr.alias("stop_price"),
            pl.lit(None, dtype=pl.Float64).alias("target_price"),
            _exit_at_expr(params).alias("exit_at"),
        ]).filter(pl.col("side") != 0)

        # One signal per (code, KL-date) max -- the daily aggregate already
        # guarantees this (one _dir per session), but keep first defensively.
        sigs = sigs.with_columns(
            (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.date().alias("_d")
        ).sort(["code", "_d", "ts"]).group_by(
            ["code", "_d"], maintain_order=True
        ).first().drop("_d")

        return validate_signal_frame(sigs)


def _exit_at_expr(params: GapContinuationParams) -> pl.Expr:
    """Polars expression for the `exit_at` timestamp column."""
    target_kl_min = _EXIT_POLICY_TO_KL_MIN.get(params.exit_policy)
    if target_kl_min is None:
        return pl.lit(None, dtype=pl.Datetime("ns"))
    # exit_at = KL-midnight-of-date + target minutes, expressed in UTC.
    kl_date = (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.date()
    return (
        kl_date.cast(pl.Datetime("ns"))
        + pl.duration(minutes=target_kl_min)
        - pl.duration(hours=KL_OFFSET_HOURS)
    )


__all__ = [
    "GapContinuationParams",
    "GapContinuationStrategy",
    "SESSION_OPEN_KL_MIN",
    "SESSION_CLOSE_KL_MIN",
]
