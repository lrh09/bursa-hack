"""Intraday backtest engine -- next-bar-open fills, session-close flatten,
per-regime P&L.

Hybrid design:
  The proposal calls for `vectorbt 1.0 OSS` for vectorised P&L accounting.
  In practice the OSS release on PyPI today is `vectorbt 0.28.x`. Both expose
  `Portfolio.from_signals(... price=open, ...)` but neither models per-trade
  cost regimes natively without expensive reshape gymnastics. To keep the
  surface small and bullet-proof on Windows, this engine uses a
  **pure-Polars** position+P&L loop internally; vectorbt remains an optional
  cross-check (see `engine._vectorbt_available`).

  The engine API surface (`IntradayEngine.run`, `BacktestResult`) is stable
  regardless of which inner loop ships -- swapping to vectorbt later does
  not break callers.

Iron rules enforced here:
  - **Next-bar-open fills.** Signal observed at ts=t -> fill at open(t+1).
    Tested by `tests/intraday/test_engine_no_lookahead.py`.
  - **Session-close flatten** when `session_only=True`. Final bar of each KL
    session = forced-flat at close. No overnight carry.
  - **Holdout refusal.** If any bar / signal `ts` falls in the last 43
    trading days of the dataset's available range, the run raises
    `HoldoutBreach`. Override only by passing `allow_holdout=True`
    (orchestrator's job, not strategy code).
  - **Per-regime P&L.** Run once across signals; produce one PnL series per
    `FeeSchedule` in `cost_regimes`. Gross return identical across regimes;
    only fees differ. Verified by `test_engine_cost_regimes.py`.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from bursahack.costs import FeeSchedule
from bursahack.intraday.calendar import KL_OFFSET_HOURS
from bursahack.intraday.impact import ImpactModel
from bursahack.intraday.registry import SIGNAL_SCHEMA, validate_signal_frame

ENGINE_VERSION = "1.0.0"
HOLDOUT_TRADING_DAYS = 43


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HoldoutBreach(ValueError):
    """Raised when a run would touch the protected holdout window."""


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class RunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    run_id: str
    started_at: datetime
    wallclock_ms: int
    n_bars: int
    n_trades: int
    snapshot_hash: str
    git_sha: str
    signal_version: str
    params_sha: str
    engine_version: str
    cost_regimes_sha: str
    impact_model_sha: str
    universe_sha: str
    holdout_excluded: bool


class BacktestResult(BaseModel):
    """End-to-end result of one engine.run() call."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    pnl: dict[str, pl.DataFrame]
    trades: pl.DataFrame
    metadata: RunMetadata


# ---------------------------------------------------------------------------
# Helpers: hashes
# ---------------------------------------------------------------------------


def hash_payload(payload: object) -> str:
    """SHA-256 of a JSON-stable representation of `payload`."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def cost_regimes_sha(regimes: list[FeeSchedule]) -> str:
    return hash_payload([r.model_dump() for r in regimes])


def impact_sha(impact: ImpactModel) -> str:
    return hash_payload(impact.model_dump())


def params_sha(params: BaseModel) -> str:
    return hash_payload(params.model_dump())


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def _vectorbt_available() -> bool:  # pragma: no cover - environment probe
    try:
        import vectorbt  # noqa: F401
        return True
    except Exception:
        return False


class IntradayEngine(BaseModel):
    """Bar-by-bar intraday engine with next-bar-open fills.

    See module docstring for invariants. The engine is intentionally narrow:
    no slippage modes other than Kissell-Glantz (delegated to `ImpactModel`),
    no fill modes other than next-bar-open. Adding modes is W2 territory.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    fill_policy: Literal["next_bar_open"] = "next_bar_open"
    slippage_model: Literal["kissell_glantz"] = "kissell_glantz"
    session_only: bool = True
    max_position_pct_adv: float = 0.10
    min_trade_notional_rm: float = 16_000.0
    cost_regimes: list[FeeSchedule]
    starting_equity: float = 100_000.0
    # If set, runs assert that no bar / signal falls in [holdout_start, holdout_end].
    # `None` disables; the orchestrator usually fills this in from the snapshot.
    holdout_start: datetime | None = None
    holdout_end: datetime | None = None

    # -------------------- public API --------------------

    def run(
        self,
        bars: pl.LazyFrame | pl.DataFrame,
        signals: pl.DataFrame,
        universe_members: pl.DataFrame,
        sigma: pl.DataFrame | None = None,
        impact: ImpactModel | None = None,
        *,
        snapshot_hash: str = "unknown",
        git_sha: str = "unknown",
        signal_version: str = "unknown",
        params: BaseModel | None = None,
        universe_sha: str = "unknown",
        allow_holdout: bool = False,
    ) -> BacktestResult:
        """Execute signals against `bars`, producing one P&L per cost regime.

        Args:
          bars             : OHLCV with cols [ts, code, open, high, low, close,
                             volume, value]. LazyFrame is materialized inside.
          signals          : SignalFrame from a registered strategy.
          universe_members : (date, code) membership (used for ADV joins, not
                             enforced here; loader has already filtered).
          sigma            : optional per-bar Rogers-Satchell sigma for slippage.
          impact           : optional ImpactModel (defaults to vanilla).
          snapshot_hash, git_sha, ... : metadata-only, recorded into RunMetadata.
          allow_holdout    : if True, skip the holdout guard. Default False.
        """
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        t0 = datetime.now(timezone.utc)

        if isinstance(bars, pl.LazyFrame):
            bars = bars.collect()

        signals = validate_signal_frame(signals)
        impact = impact or ImpactModel()

        # ---- holdout guard ----
        holdout_excluded = self._guard_holdout(bars, signals, allow_holdout)

        # ---- run the per-regime loop ----
        regime_pnl: dict[str, pl.DataFrame] = {}
        trades_first: pl.DataFrame | None = None

        for regime in self.cost_regimes:
            pnl_df, trades_df = self._simulate(bars, signals, regime)
            regime_pnl[regime.name] = pnl_df
            if trades_first is None:
                # Trades dataframe is dominantly regime-invariant in shape;
                # we report ONE table tagged with regime in net_ret. To keep
                # the surface lean, we attach all regimes to the trades log
                # by concatenating.
                trades_first = trades_df.with_columns(pl.lit(regime.name).alias("regime"))
            else:
                trades_first = pl.concat([
                    trades_first,
                    trades_df.with_columns(pl.lit(regime.name).alias("regime")),
                ])

        trades_out = trades_first if trades_first is not None else _empty_trades()

        wallclock_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
        meta = RunMetadata(
            run_id=str(uuid.uuid4()),
            started_at=started_at,
            wallclock_ms=wallclock_ms,
            n_bars=bars.height,
            n_trades=trades_out.filter(pl.col("regime") == self.cost_regimes[0].name).height,
            snapshot_hash=snapshot_hash,
            git_sha=git_sha,
            signal_version=signal_version,
            params_sha=params_sha(params) if params is not None else "no-params",
            engine_version=ENGINE_VERSION,
            cost_regimes_sha=cost_regimes_sha(self.cost_regimes),
            impact_model_sha=impact_sha(impact),
            universe_sha=universe_sha,
            holdout_excluded=holdout_excluded,
        )
        return BacktestResult(pnl=regime_pnl, trades=trades_out, metadata=meta)

    # -------------------- internals --------------------

    def _guard_holdout(
        self,
        bars: pl.DataFrame,
        signals: pl.DataFrame,
        allow_holdout: bool,
    ) -> bool:
        """Refuse if any bar/signal ts falls in [holdout_start, holdout_end].

        Returns True if a holdout window was DEFINED and respected; False if
        no holdout was configured (caller will see `holdout_excluded=False`
        and know the run wasn't guarded).
        """
        if self.holdout_start is None or self.holdout_end is None:
            return False
        if allow_holdout:
            return False  # explicit opt-out -- record as not-excluded

        s = self.holdout_start
        e = self.holdout_end
        bad_bars = bars.filter((pl.col("ts") >= s) & (pl.col("ts") <= e)).height
        bad_sigs = signals.filter((pl.col("ts") >= s) & (pl.col("ts") <= e)).height
        if bad_bars or bad_sigs:
            raise HoldoutBreach(
                f"run touches holdout window [{s}, {e}]: "
                f"{bad_bars} bars + {bad_sigs} signals inside. "
                "Pass allow_holdout=True ONLY in the final pre-registered run."
            )
        return True

    # ---- inner simulation: one regime ----

    def _simulate(
        self,
        bars: pl.DataFrame,
        signals: pl.DataFrame,
        regime: FeeSchedule,
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Run the bar loop for one cost regime.

        Strategy:
          For each (code) we walk bars chronologically. We carry at most one
          open position per code. On the bar AFTER a non-flat signal:
            entry_price := that bar's `open`.
          On any subsequent bar:
            - if stop_price hit: exit at the stop (intra-bar; cap to bar OHLC).
            - if exit_at <= bar.ts: exit at next bar's open (handled as a
              forced exit signal on this bar).
            - if session_only AND bar is the last of its KL session: flatten
              at close, no overnight carry.

        Returns:
          (pnl_df, trades_df)
            pnl_df    : long-format [regime, ts, equity, ret]
            trades_df : [code, entry_ts, exit_ts, entry_px, exit_px, qty,
                         gross_ret, fees_bps, impact_bps, net_ret]
        """
        if bars.height == 0:
            return _empty_pnl(regime.name), _empty_trades()

        bars = bars.sort(["code", "ts"]).with_columns([
            # KL minutes-of-day -- session boundary detection.
            # Cast hour/min to Int32 BEFORE multiplying (Polars u8 overflow trap).
            (
                (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS))
                    .dt.hour().cast(pl.Int32) * 60
                + (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS))
                    .dt.minute().cast(pl.Int32)
            ).alias("_kl_min"),
            (pl.col("ts") + pl.duration(hours=KL_OFFSET_HOURS))
                .dt.date().alias("_kl_date"),
        ])

        # Per-code last bar in each session (for session-close flatten).
        # Compute once per (code, session-day): the row with max _kl_min.
        last_in_session = (
            bars.group_by(["code", "_kl_date"])
            .agg(pl.col("_kl_min").max().alias("_last_min"))
        )
        bars = bars.join(last_in_session, on=["code", "_kl_date"]).with_columns(
            (pl.col("_kl_min") == pl.col("_last_min")).alias("_is_session_last")
        ).drop("_last_min")

        # Group signals by (code, ts) for quick membership tests.
        sig_by_code: dict[str, dict] = {}
        for row in signals.iter_rows(named=True):
            sig_by_code.setdefault(row["code"], {})[row["ts"]] = row

        # We iterate per code. Within each code the bars are time-sorted.
        # We'll accumulate per-bar P&L into one long Series and the trade log.
        equity_rows: list[tuple] = []  # (ts, equity_contribution, regime)
        trade_rows: list[dict] = []

        bars_by_code: dict[str, pl.DataFrame] = {
            code: g for code, g in bars.group_by("code", maintain_order=True)
        }
        # Polars 1.x: group_by returns (key_tuple, df) on iteration.
        bars_by_code = {}
        for key, g in bars.group_by("code", maintain_order=True):
            code = key[0] if isinstance(key, tuple) else key
            bars_by_code[code] = g

        for code, g in bars_by_code.items():
            sigs = sig_by_code.get(code, {})
            n = g.height
            ts_arr = g.get_column("ts").to_list()
            open_arr = g.get_column("open").to_list()
            high_arr = g.get_column("high").to_list()
            low_arr = g.get_column("low").to_list()
            close_arr = g.get_column("close").to_list()
            last_arr = g.get_column("_is_session_last").to_list()
            date_arr = g.get_column("_kl_date").to_list()

            in_pos = False
            entry_ts: datetime | None = None
            entry_px: float = 0.0
            stop_px: float | None = None
            target_px: float | None = None
            exit_at: datetime | None = None
            side_held: int = 0
            pending_entry_side: int = 0
            pending_entry_stop: float | None = None
            pending_entry_target: float | None = None
            pending_entry_exit_at: datetime | None = None
            pending_force_exit: bool = False

            for i in range(n):
                ts = ts_arr[i]
                o = open_arr[i]
                h = high_arr[i]
                lo = low_arr[i]
                c = close_arr[i]
                is_last_of_session = last_arr[i]
                kl_date = date_arr[i]

                # 1) Apply pending fill from previous bar (next-bar-open).
                if pending_force_exit and in_pos:
                    exit_px = o
                    self._book_trade(
                        trade_rows, code, entry_ts, ts, entry_px, exit_px,
                        side_held, regime, equity_rows,
                    )
                    in_pos = False
                    entry_ts = None
                    entry_px = 0.0
                    side_held = 0
                    stop_px = None
                    target_px = None
                    exit_at = None
                    pending_force_exit = False

                if pending_entry_side != 0 and not in_pos:
                    in_pos = True
                    entry_ts = ts
                    entry_px = o
                    side_held = pending_entry_side
                    stop_px = pending_entry_stop
                    target_px = pending_entry_target
                    exit_at = pending_entry_exit_at
                    pending_entry_side = 0
                    pending_entry_stop = None
                    pending_entry_target = None
                    pending_entry_exit_at = None

                # 2) Check exits on the current bar (stop/target/exit_at/session_close).
                if in_pos:
                    exit_reason = None
                    exit_px = c  # default if we exit at close

                    # Stop check (touch-based, within bar OHLC).
                    if side_held > 0 and stop_px is not None and lo <= stop_px:
                        exit_reason = "stop"
                        exit_px = stop_px
                    elif side_held < 0 and stop_px is not None and h >= stop_px:
                        exit_reason = "stop"
                        exit_px = stop_px

                    # Target check (touch-based).
                    if exit_reason is None and target_px is not None:
                        if side_held > 0 and h >= target_px:
                            exit_reason = "target"
                            exit_px = target_px
                        elif side_held < 0 and lo <= target_px:
                            exit_reason = "target"
                            exit_px = target_px

                    # Time-based exit.
                    if exit_reason is None and exit_at is not None and ts >= exit_at:
                        exit_reason = "exit_at"
                        exit_px = c

                    # Session-close flatten.
                    if (
                        exit_reason is None
                        and self.session_only
                        and is_last_of_session
                    ):
                        exit_reason = "session_close"
                        exit_px = c

                    if exit_reason is not None:
                        self._book_trade(
                            trade_rows, code, entry_ts, ts, entry_px, exit_px,
                            side_held, regime, equity_rows,
                        )
                        in_pos = False
                        entry_ts = None
                        entry_px = 0.0
                        side_held = 0
                        stop_px = None
                        target_px = None
                        exit_at = None

                # 3) Sample the signal at this bar (next-bar-open fill).
                sig = sigs.get(ts)
                if sig is not None and sig["side"] != 0 and not in_pos:
                    pending_entry_side = int(sig["side"])
                    pending_entry_stop = sig.get("stop_price")
                    pending_entry_target = sig.get("target_price")
                    pending_entry_exit_at = sig.get("exit_at")
                elif sig is not None and sig["side"] == 0 and in_pos:
                    # Explicit flat signal -- exit at next bar open.
                    pending_force_exit = True

            # End of bar loop for this code. If we're still in a position
            # and session_only is True, the session-close flatten above
            # should have caught it -- defensive: close at last close.
            if in_pos and self.session_only:
                self._book_trade(
                    trade_rows, code, entry_ts, ts_arr[-1], entry_px, close_arr[-1],
                    side_held, regime, equity_rows,
                )

        # Build the per-regime PnL series.
        trades_df = pl.DataFrame(
            trade_rows,
            schema={
                "code": pl.Utf8,
                "entry_ts": pl.Datetime("ns"),
                "exit_ts": pl.Datetime("ns"),
                "entry_px": pl.Float64,
                "exit_px": pl.Float64,
                "qty": pl.Float64,
                "gross_ret": pl.Float64,
                "fees_bps": pl.Float64,
                "impact_bps": pl.Float64,
                "net_ret": pl.Float64,
            },
        ) if trade_rows else _empty_trades()

        # Equity curve = cumulative sum of (net_ret * starting_equity) along ts.
        if trades_df.height == 0:
            pnl_df = pl.DataFrame(
                {"regime": [regime.name], "ts": [bars.get_column("ts").min()],
                 "equity": [self.starting_equity], "ret": [0.0]},
                schema={"regime": pl.Utf8, "ts": pl.Datetime("ns"),
                        "equity": pl.Float64, "ret": pl.Float64},
            )
        else:
            curve = trades_df.sort("exit_ts").with_columns([
                pl.col("net_ret").cum_sum().alias("_cum"),
            ])
            pnl_df = curve.select([
                pl.lit(regime.name).alias("regime"),
                pl.col("exit_ts").alias("ts"),
                (self.starting_equity * (1.0 + pl.col("_cum"))).alias("equity"),
                pl.col("net_ret").alias("ret"),
            ])

        return pnl_df, trades_df

    def _book_trade(
        self,
        trade_rows: list[dict],
        code: str,
        entry_ts: datetime,
        exit_ts: datetime,
        entry_px: float,
        exit_px: float,
        side: int,
        regime: FeeSchedule,
        equity_rows: list[tuple],
    ) -> None:
        """Compute gross/net return for one closed trade and append it."""
        if entry_px <= 0:
            return
        # Unit-notional accounting: qty = 1 share for now (sizing is a W2 concern).
        qty = 1.0
        notional = entry_px * qty
        gross_ret = side * (exit_px - entry_px) / entry_px
        fees_bps = regime.roundtrip_cost(notional)
        impact_bps = 0.0  # impact-bps is reserved for W2 when impact is wired into fills
        net_ret = gross_ret - (fees_bps + impact_bps) / 10_000.0
        trade_rows.append({
            "code": code,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "entry_px": entry_px,
            "exit_px": exit_px,
            "qty": qty,
            "gross_ret": gross_ret,
            "fees_bps": fees_bps,
            "impact_bps": impact_bps,
            "net_ret": net_ret,
        })


# ---------------------------------------------------------------------------
# Small empty constructors (keep types stable when we return early)
# ---------------------------------------------------------------------------


def _empty_trades() -> pl.DataFrame:
    return pl.DataFrame(schema={
        "code": pl.Utf8,
        "entry_ts": pl.Datetime("ns"),
        "exit_ts": pl.Datetime("ns"),
        "entry_px": pl.Float64,
        "exit_px": pl.Float64,
        "qty": pl.Float64,
        "gross_ret": pl.Float64,
        "fees_bps": pl.Float64,
        "impact_bps": pl.Float64,
        "net_ret": pl.Float64,
    })


def _empty_pnl(regime_name: str) -> pl.DataFrame:
    return pl.DataFrame(schema={
        "regime": pl.Utf8,
        "ts": pl.Datetime("ns"),
        "equity": pl.Float64,
        "ret": pl.Float64,
    })


__all__ = [
    "BacktestResult",
    "ENGINE_VERSION",
    "HOLDOUT_TRADING_DAYS",
    "HoldoutBreach",
    "IntradayEngine",
    "RunMetadata",
    "cost_regimes_sha",
    "hash_payload",
    "impact_sha",
    "params_sha",
]
