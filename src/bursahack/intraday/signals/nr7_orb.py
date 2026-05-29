"""NR7-conditioned Opening Range Breakout (Crabel 1990 / Raschke-Connors 1995).

Mechanic, plain English:
  - Volatility is mean-reverting. A daily bar whose range (high-low) is the
    NARROWEST of the last N daily bars (NR7 = narrowest of last 7) signals
    compression about to release. The day AFTER such a session, an opening-range
    breakout has elevated odds of catching a wide-range trend day.
  - So: only ARM the ORB on sessions whose PRIOR session was an NR(N) day.

Pipeline:
  1. Aggregate 1-min bars to a per-(code, KL-session-day) DAILY range:
     day_high = max(high), day_low = min(low), day_range = day_high - day_low.
  2. Flag session day `d` as NR(N) if day_range[d] is STRICTLY the minimum of
     the trailing N sessions [d-N+1 .. d] (inclusive of d, Crabel convention).
     Known at the CLOSE of day d (uses only days <= d -> rolling_min over the
     current+prior N-1 sessions, per code, ordered by date).
  3. The ORB is armed on day d+1 (the session immediately AFTER an NR(N) day).
     For each session, look at whether the PREVIOUS session (shift(1) over the
     per-code daily series) was NR(N). Only emit ORB signals on armed sessions.
  4. ORB mechanic, identical to orb.py: OR_high/OR_low over the first
     `opening_range_minutes` bars after KL open (09:00 = minute 540); first bar
     after the OR window with high > OR_high -> long (low < OR_low -> short).
     Stop = entry * (1 -/+ stop_pct/100). exit_at via exit_policy.
  5. One signal per (code, KL-date) max.

Iron rules:
  - No lookahead. NR(N) for day d uses ONLY daily ranges of days <= d. The
    "armed" flag for day d+1 uses NR(N) of day d (a PAST completed session).
    The OR window uses only the first K minutes of the current session.
  - Stateless, pure-polars. Return empty SignalFrame on empty input.
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


SESSION_OPEN_KL_MIN: int = 9 * 60  # 09:00 KL = minute 540
SESSION_CLOSE_KL_MIN: int = 16 * 60 + 59  # 16:59 KL = minute 1019


_EXIT_POLICY_TO_KL_MIN: dict[str, int | None] = {
    "session_close": None,          # engine handles
    "kl_15_00": 15 * 60,            # 900
    "kl_16_00": 16 * 60,            # 960
}


class NR7ORBParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nr_window: Literal[4, 7] = 7
    opening_range_minutes: Literal[5, 15, 30] = 5
    stop_pct: Literal[0.5, 1.0, 1.5, 2.0] = 1.0
    exit_policy: Literal["session_close", "kl_15_00", "kl_16_00"] = "session_close"
    side: Literal["long", "short", "both"] = "both"


@register_strategy(
    "nr7_orb",
    description="NR7-conditioned Opening Range Breakout (Crabel/Raschke)",
)
class NR7ORBStrategy:
    """Stateless NR(N)-conditioned ORB signal generator."""

    name: ClassVar[str] = "nr7_orb"
    params_model: ClassVar[type[BaseModel]] = NR7ORBParams
    version: ClassVar[str] = "1.0.0"

    def generate_signals(
        self,
        bars: pl.LazyFrame | pl.DataFrame,
        universe_members: pl.DataFrame | None,
        params: NR7ORBParams,
    ) -> pl.DataFrame:
        """Emit point-in-time ORB signals only on NR(N)-armed sessions."""
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

        # ------------------------------------------------------------------
        # 1. Per-(code, session-day) DAILY range.
        # ------------------------------------------------------------------
        daily = (
            df.group_by(["code", "_kl_date"]).agg([
                pl.col("high").max().alias("_day_high"),
                pl.col("low").min().alias("_day_low"),
            ])
            .with_columns(
                (pl.col("_day_high") - pl.col("_day_low")).alias("_day_range")
            )
            .sort(["code", "_kl_date"])
        )

        # ------------------------------------------------------------------
        # 2. NR(N) flag for day d: day_range[d] strictly the min of the
        #    trailing N sessions [d-N+1 .. d] (inclusive of d). Uses ONLY
        #    days <= d (rolling_min closed on the right over the current
        #    window) -> known at the CLOSE of day d, no lookahead.
        #
        #    A window needs N completed sessions before NR(N) is defined; with
        #    fewer than N priors the rolling_min is null -> not armed (no
        #    spurious early signals).
        # ------------------------------------------------------------------
        nrw = int(params.nr_window)
        daily = daily.with_columns(
            pl.col("_day_range")
                .rolling_min(window_size=nrw, min_samples=nrw)
                .over("code")
                .alias("_trailing_min")
        ).with_columns(
            (
                pl.col("_trailing_min").is_not_null()
                & (pl.col("_day_range") <= pl.col("_trailing_min"))
            ).alias("_is_nr")
        )
        # `<=` against the trailing-N min: since the window INCLUDES day d,
        # the min is at most day_range[d]. Equality holds iff day d is the
        # (joint) narrowest of the window -> Crabel's NR(N). Strictness vs the
        # OTHER N-1 sessions is what matters; ties make day d still narrowest.

        # ------------------------------------------------------------------
        # 3. ARM day d+1 when the PREVIOUS session was NR(N). shift(1) over the
        #    per-code daily series pulls the prior completed session's flag
        #    forward — a strictly PAST session, no lookahead.
        # ------------------------------------------------------------------
        daily = daily.with_columns(
            pl.col("_is_nr").shift(1).over("code").fill_null(False).alias("_armed")
        )

        armed_days = daily.filter(pl.col("_armed")).select(["code", "_kl_date"])
        if armed_days.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        # Restrict bars to armed sessions only.
        df = df.join(armed_days, on=["code", "_kl_date"], how="inner")
        if df.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        # ------------------------------------------------------------------
        # 4. ORB mechanic (identical to orb.py) on the armed sessions.
        # ------------------------------------------------------------------
        or_end = SESSION_OPEN_KL_MIN + params.opening_range_minutes  # exclusive

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
                pl.col("close").alias("entry_price"),  # hint; engine fills next-bar-open
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


def _exit_at_expr(params: NR7ORBParams) -> pl.Expr:
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


__all__ = [
    "NR7ORBParams",
    "NR7ORBStrategy",
    "SESSION_OPEN_KL_MIN",
    "SESSION_CLOSE_KL_MIN",
]
