"""LMSW Volume-Spike strategy.

Mechanic, plain English:
  - For each (code, bar) compute a rolling-window volume z-score over the
    previous N bars (the current bar is EXCLUDED — no lookahead).
  - Bar return = (close - open) / open. We use the bar's own move, not
    close-to-close, to keep the signal pure-intraday.
  - When z-score > `volume_z_threshold` AND |bar return| > `return_floor_pct`,
    emit a signal:
      - `side_mode="continuation"`: trade IN the direction of the bar move
        (high volume + up bar -> long; high volume + down bar -> short).
      - `side_mode="reversal"`: trade OPPOSITE the bar move.
  - Stop is `entry * (1 ± stop_pct)`; engine handles the touch exit.
  - Time-based exit per `exit_policy`, same shape as ORB.

References:
  - Llorente, Michaely, Saar, Wang (2002), "Dynamic Volume-Return
    Relation of Individual Stocks." The original paper finds the
    continuation/reversal flip is asset-specific (informed vs hedger
    driven). At intraday horizon we let the parameter sweep pick which
    direction works on the Bursa universe.

Iron rules:
  - Stateless: pure-polars expression graph, no module-level state.
  - No lookahead: z-score window is closed on the left (`closed="left"`).
  - Filters zero-volume bars (phantom KL lunch break leftovers) before
    computing volume stats.
"""
from __future__ import annotations

from typing import ClassVar, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from bursahack.intraday.calendar import KL_OFFSET_HOURS
from bursahack.intraday.registry import (
    SIGNAL_SCHEMA,
    register_strategy,
    validate_signal_frame,
)


# Same KL session window the ORB strategy uses.
SESSION_OPEN_KL_MIN: int = 9 * 60        # 09:00 KL
SESSION_CLOSE_KL_MIN: int = 16 * 60 + 59  # 16:59 KL


_EXIT_POLICY_TO_KL_MIN: dict[str, int | None] = {
    "session_close": None,
    "kl_15_00": 15 * 60,
    "kl_16_00": 16 * 60,
}


class LMSWParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    z_window_bars: Literal[20, 60, 120] = 60
    volume_z_threshold: Literal[2.0, 3.0, 4.0] = 3.0
    return_floor_pct: float = Field(0.10, ge=0.0, le=10.0)
    stop_pct: Literal[0.5, 1.0, 1.5, 2.0] = 1.0
    exit_policy: Literal["session_close", "kl_15_00", "kl_16_00"] = "session_close"
    side_mode: Literal["continuation", "reversal"] = "continuation"


@register_strategy(
    "lmsw",
    description="Volume-Spike continuation/reversal (Llorente-Michaely-Saar-Wang 2002)",
)
class LMSWStrategy:
    """Stateless volume-spike signal generator."""

    name: ClassVar[str] = "lmsw"
    params_model: ClassVar[type[BaseModel]] = LMSWParams
    version: ClassVar[str] = "1.0.0"

    def generate_signals(
        self,
        bars: pl.LazyFrame | pl.DataFrame,
        universe_members: pl.DataFrame | None,
        params: LMSWParams,
    ) -> pl.DataFrame:
        if isinstance(bars, pl.LazyFrame):
            bars = bars.collect()
        if bars.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        # Filter zero-volume bars before stats (phantom KL-lunch leftovers).
        df = bars.filter(pl.col("volume") > 0).sort(["code", "ts"])

        # KL-minute + KL-date columns. Cast hour/min to Int32 (u8 trap).
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

        # Rolling-window volume mean + std, per code, EXCLUDING the current bar.
        # `shift(1)` removes same-bar contamination; `.over("code")` groups
        # the whole expression so windows don't bleed across symbols.
        df = df.with_columns([
            pl.col("volume").shift(1).rolling_mean(window_size=params.z_window_bars)
                .over("code").alias("_vol_mu"),
            pl.col("volume").shift(1).rolling_std(window_size=params.z_window_bars, ddof=1)
                .over("code").alias("_vol_sigma"),
        ])

        # Bar return = (close - open) / open. Cast for stable division.
        df = df.with_columns([
            ((pl.col("close") - pl.col("open")) / pl.col("open") * 100.0).alias("_ret_pct"),
        ])

        # Z-score; null when sigma is 0/missing (early bars in each code).
        df = df.with_columns([
            pl.when(pl.col("_vol_sigma").is_null() | (pl.col("_vol_sigma") == 0.0))
              .then(None)
              .otherwise((pl.col("volume") - pl.col("_vol_mu")) / pl.col("_vol_sigma"))
              .alias("_vol_z"),
        ])

        # Restrict signals to the regular session window (no signals during
        # KL lunch — we've already filtered zero-volume bars, but a real
        # midday print would otherwise count).
        df = df.filter(
            (pl.col("_kl_min") >= SESSION_OPEN_KL_MIN)
            & (pl.col("_kl_min") <= SESSION_CLOSE_KL_MIN)
        )

        # Trigger conditions.
        z_th = float(params.volume_z_threshold)
        ret_floor = float(params.return_floor_pct)
        candidates = df.filter(
            pl.col("_vol_z").is_not_null()
            & (pl.col("_vol_z") >= z_th)
            & (pl.col("_ret_pct").abs() >= ret_floor)
        )

        if candidates.height == 0:
            return pl.DataFrame(schema=SIGNAL_SCHEMA)

        # Side selection.
        # continuation: same sign as bar return.
        # reversal: opposite sign.
        sign_expr = (
            pl.when(pl.col("_ret_pct") > 0).then(1)
              .when(pl.col("_ret_pct") < 0).then(-1)
              .otherwise(0)
        )
        if params.side_mode == "reversal":
            sign_expr = -sign_expr

        # Stop:
        #   long  -> entry * (1 - stop_pct/100)
        #   short -> entry * (1 + stop_pct/100)
        # We use `close` as the entry-price hint (engine still fills next-bar-open).
        stop_expr = pl.when(sign_expr > 0).then(
            pl.col("close") * (1.0 - params.stop_pct / 100.0)
        ).when(sign_expr < 0).then(
            pl.col("close") * (1.0 + params.stop_pct / 100.0)
        ).otherwise(None)

        sigs = candidates.select([
            pl.col("ts"),
            pl.col("code"),
            sign_expr.cast(pl.Int8).alias("side"),
            pl.col("close").alias("entry_price"),
            stop_expr.alias("stop_price"),
            pl.lit(None, dtype=pl.Float64).alias("target_price"),
            _exit_at_expr(params).alias("exit_at"),
        ]).filter(pl.col("side") != 0)

        # Per (code, kl_date) keep first signal only — no pyramiding.
        sigs = sigs.with_columns(
            (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.date().alias("_d")
        ).sort(["code", "_d", "ts"]).group_by(
            ["code", "_d"], maintain_order=True
        ).first().drop("_d")

        return validate_signal_frame(sigs)


def _exit_at_expr(params: LMSWParams) -> pl.Expr:
    """Polars expression for the `exit_at` timestamp column."""
    target_kl_min = _EXIT_POLICY_TO_KL_MIN.get(params.exit_policy)
    if target_kl_min is None:
        return pl.lit(None, dtype=pl.Datetime("ns"))
    kl_date = (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS)).dt.date()
    return (
        kl_date.cast(pl.Datetime("ns"))
        + pl.duration(minutes=target_kl_min)
        - pl.duration(hours=KL_OFFSET_HOURS)
    )


__all__ = ["LMSWParams", "LMSWStrategy", "SESSION_OPEN_KL_MIN", "SESSION_CLOSE_KL_MIN"]
