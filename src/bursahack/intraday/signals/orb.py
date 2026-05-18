"""Opening Range Breakout (ORB) -- Crabel / Zarattini-Barbon-Aziz 2024.

Mechanic:
  - For each (code, session-day) compute the high/low of the first
    `opening_range_minutes` real bars after the KL session open (09:00 KL).
  - First bar AFTER the OR window whose `high > OR_high` (long) or
    `low < OR_low` (short) emits an entry signal at that bar's `ts`. The
    engine fills at the NEXT bar's open.
  - Stop is `entry_price * (1 - stop_pct/100)` for longs, mirrored for shorts.
    Engine handles the stop-touch exit.
  - Time-based exit per `exit_policy`. session_close = engine flatten at
    the session's last bar.
  - `min_or_range_pct` gates out micro-ORs (1-tick wide on quiet names).

Iron rule: this module sees `pl.LazyFrame` of bars and emits a SignalFrame.
It does NOT call the engine, does NOT fetch data, does NOT have state.
"""
from __future__ import annotations

from datetime import time
from typing import ClassVar, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from bursahack.intraday.calendar import KL_OFFSET_HOURS
from bursahack.intraday.registry import (
    SIGNAL_SCHEMA,
    register_strategy,
    validate_signal_frame,
)


SESSION_OPEN_KL_MIN: int = 9 * 60  # 09:00 KL = minute 540
SESSION_CLOSE_KL_MIN: int = 16 * 60 + 59  # 16:59 KL = minute 1019


_EXIT_POLICY_TO_KL_MIN: dict[str, int | None] = {
    "session_close": None,          # engine handles
    "kl_15_00": 15 * 60,            # 900
    "kl_16_00": 16 * 60,            # 960
}


class ORBParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    opening_range_minutes: Literal[5, 15, 30] = 5
    stop_pct: Literal[0.5, 1.0, 1.5, 2.0] = 1.0
    exit_policy: Literal["session_close", "kl_15_00", "kl_16_00"] = "session_close"
    side: Literal["long", "short", "both"] = "both"
    min_or_range_pct: float = Field(0.0, ge=0.0, le=100.0)


@register_strategy("orb", description="Opening Range Breakout (Crabel / Zarattini 2024)")
class ORBStrategy:
    """Stateless ORB signal generator."""

    name: ClassVar[str] = "orb"
    params_model: ClassVar[type[BaseModel]] = ORBParams
    version: ClassVar[str] = "1.0.0"

    def generate_signals(
        self,
        bars: pl.LazyFrame | pl.DataFrame,
        universe_members: pl.DataFrame | None,
        params: ORBParams,
    ) -> pl.DataFrame:
        """Emit point-in-time ORB signals for every (code, session-day)."""
        if isinstance(bars, pl.LazyFrame):
            bars = bars.collect()
        if bars.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        # KL-minute + KL-date columns. Cast hour/min to Int32 first (u8 trap).
        df = bars.with_columns([
            (
                (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS))
                    .dt.hour().cast(pl.Int32) * 60
                + (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS))
                    .dt.minute().cast(pl.Int32)
            ).alias("_kl_min"),
            (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS))
                .dt.date().alias("_kl_date"),
        ]).sort(["code", "ts"])

        or_end = SESSION_OPEN_KL_MIN + params.opening_range_minutes  # exclusive

        # OR window: KL-min in [SESSION_OPEN_KL_MIN, or_end).
        or_window = df.filter(
            (pl.col("_kl_min") >= SESSION_OPEN_KL_MIN)
            & (pl.col("_kl_min") < or_end)
        )
        or_agg = or_window.group_by(["code", "_kl_date"]).agg([
            pl.col("high").max().alias("or_high"),
            pl.col("low").min().alias("or_low"),
            pl.col("open").first().alias("or_open"),
        ])

        # Bars strictly AFTER the OR window are eligible for entry.
        post_or = df.filter(pl.col("_kl_min") >= or_end).join(
            or_agg, on=["code", "_kl_date"], how="inner"
        )

        # Range filter (skip narrow-OR sessions).
        if params.min_or_range_pct > 0:
            post_or = post_or.filter(
                (pl.col("or_high") - pl.col("or_low"))
                / pl.col("or_open") * 100.0
                >= params.min_or_range_pct
            )

        # Long breakout: first bar whose high > or_high. The signal fires
        # on that BAR'S close (ts). Engine fills next-bar-open.
        signals: list[pl.DataFrame] = []
        if params.side in ("long", "both"):
            long_bars = post_or.filter(pl.col("high") > pl.col("or_high"))
            first_long = (
                long_bars.sort(["code", "_kl_date", "ts"])
                .group_by(["code", "_kl_date"], maintain_order=True).first()
            )
            long_sigs = first_long.select([
                pl.col("ts"),
                pl.col("code"),
                pl.lit(1, dtype=pl.Int8).alias("side"),
                pl.col("close").alias("entry_price"),  # hint; engine overrides with next-bar-open
                (pl.col("close") * (1.0 - params.stop_pct / 100.0)).alias("stop_price"),
                pl.lit(None, dtype=pl.Float64).alias("target_price"),
                _exit_at_expr(params).alias("exit_at"),
            ])
            signals.append(long_sigs)

        if params.side in ("short", "both"):
            short_bars = post_or.filter(pl.col("low") < pl.col("or_low"))
            first_short = (
                short_bars.sort(["code", "_kl_date", "ts"])
                .group_by(["code", "_kl_date"], maintain_order=True).first()
            )
            short_sigs = first_short.select([
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

        # If side=="both" and both legs fire in the same session, keep the
        # earlier one (first signal wins per session per code).
        out = out.sort("ts").group_by(["code"], maintain_order=True).agg([
            pl.all().sort_by("ts"),
        ]).explode(pl.exclude("code"))
        # Per (code, kl_date) keep first only.
        out = out.with_columns(
            (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.date().alias("_d")
        ).sort(["code", "_d", "ts"]).group_by(["code", "_d"], maintain_order=True).first().drop("_d")

        return validate_signal_frame(out)


def _exit_at_expr(params: ORBParams) -> pl.Expr:
    """Polars expression for the `exit_at` timestamp column."""
    target_kl_min = _EXIT_POLICY_TO_KL_MIN.get(params.exit_policy)
    if target_kl_min is None:
        return pl.lit(None, dtype=pl.Datetime("ns"))
    # exit_at = midnight-KL-date + target_kl_min minutes -- expressed in UTC.
    # KL midnight in UTC = KL midnight - 8h.
    kl_date = (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.date()
    return (
        kl_date.cast(pl.Datetime("ns"))
        + pl.duration(minutes=target_kl_min)
        - pl.duration(hours=KL_OFFSET_HOURS)
    )


__all__ = ["ORBParams", "ORBStrategy", "SESSION_OPEN_KL_MIN", "SESSION_CLOSE_KL_MIN"]
