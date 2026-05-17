"""Strategy-agnostic backtest engine.

Time discipline:
  - Signal computed at end-of-day t using only data <= t (engine enforces this).
  - Fill simulated at OPEN of day t+1 using raw OPEN price for cost notional
    and adjusted-OPEN for return tracking.
  - Daily mark-to-market on ADJ_CLOSE.

Suspension policy:
  - If a held security has not traded in `suspension_max_days` consecutive
    trading days, force-exit at last-known ADJ_CLOSE on the next rebal date.

Dividends:
  - Implicit via ADJ_CLOSE (total-return prices). No separate cash credit.

Outputs:
  - `Ledger.equity` : daily equity curve (pd.Series, indexed by date)
  - `Ledger.holdings` : daily holdings snapshot (DataFrame, rows=date, cols=sec)
  - `Ledger.trades` : trade-level audit log (one row per order)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from bursahack.costs import FeeConfig, MPLUS, SLIPPAGE, SlippageConfig, fees, slippage


# A signal function takes (asof_date, prices_panel_up_to_asof) and returns a
# pd.Series of target weights indexed by SECURITY_ID. Weights need not sum to 1
# (cash is implicit residual). NaN/missing names are treated as zero weight.
SignalFn = Callable[[pd.Timestamp, "PricePanel"], pd.Series]


@dataclass
class PricePanel:
    """Wide-format price tables for fast date-slicing.

    Each table: index=DATE (sorted), columns=SECURITY_ID, values=float.
    """
    adj_close: pd.DataFrame
    adj_open: pd.DataFrame
    raw_open: pd.DataFrame
    volume_rm: pd.DataFrame   # daily traded value in RM (adj_close * adj_volume)

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.adj_close.index

    def asof(self, t: pd.Timestamp) -> "PricePanel":
        """Return a panel containing only rows with date <= t (lookahead guard)."""
        return PricePanel(
            adj_close=self.adj_close.loc[:t],
            adj_open=self.adj_open.loc[:t],
            raw_open=self.raw_open.loc[:t],
            volume_rm=self.volume_rm.loc[:t],
        )

    def adv20(self, t: pd.Timestamp) -> pd.Series:
        """20-day average daily traded RM, as of date t (inclusive)."""
        sub = self.volume_rm.loc[:t].tail(20)
        return sub.mean(axis=0)


@dataclass
class Ledger:
    equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    holdings: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    cash: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))


def build_price_panel(prices: pd.DataFrame) -> PricePanel:
    """Pivot the long-format prices into wide tables for the engine.

    All four tables are aligned to the union of dates and union of SECURITY_IDs,
    so per-date lookups (e.g. `raw_open.loc[t]`) never KeyError.
    """
    prices = prices.copy()
    prices["DATE"] = pd.to_datetime(prices["DATE"])
    prices["VOLUME_RM"] = prices["ADJ_CLOSE"] * prices["ADJ_VOLUME"]
    p = lambda col: prices.pivot_table(index="DATE", columns="SECURITY_ID", values=col, aggfunc="first").sort_index()
    adj_close = p("ADJ_CLOSE")
    adj_open = p("ADJ_OPEN")
    raw_open = p("OPEN")
    volume_rm = p("VOLUME_RM")

    all_dates = adj_close.index.union(adj_open.index).union(raw_open.index).union(volume_rm.index).sort_values()
    all_secs = adj_close.columns.union(adj_open.columns).union(raw_open.columns).union(volume_rm.columns)

    def _align(df: pd.DataFrame) -> pd.DataFrame:
        return df.reindex(index=all_dates, columns=all_secs)

    return PricePanel(
        adj_close=_align(adj_close),
        adj_open=_align(adj_open),
        raw_open=_align(raw_open),
        volume_rm=_align(volume_rm),
    )


def _apply_orders(
    cash: float,
    holdings: dict[str, int],
    orders: dict[str, int],
    fill_raw_open: pd.Series,
    fill_adj_open: pd.Series,
    adv20: pd.Series,
    fee_cfg: FeeConfig,
    slip_cfg: SlippageConfig,
    trade_log: list[dict],
    fill_date: pd.Timestamp,
) -> float:
    """Mutate `holdings` in place, return new cash."""
    for sec_id, qty in orders.items():
        raw_px = fill_raw_open.get(sec_id, np.nan)
        adj_px = fill_adj_open.get(sec_id, np.nan)
        if pd.isna(raw_px) or pd.isna(adj_px) or raw_px <= 0:
            continue
        notional_raw = abs(qty) * raw_px
        adv = float(adv20.get(sec_id, 0.0) or 0.0)
        leg_fees = fees(notional_raw, fee_cfg)
        leg_slip = slippage(notional_raw, adv, slip_cfg)
        # adjusted fill: buyer pays adj_px + slip; seller receives adj_px - slip
        signed_slip_px = (leg_slip / abs(qty)) * (1 if qty > 0 else -1)
        fill_px_adj = adj_px + signed_slip_px
        # cash impact uses adjusted price (so return accounting stays consistent
        # with mark-to-market on adj_close); fees come out of cash separately
        cash -= qty * fill_px_adj
        cash -= leg_fees
        holdings[sec_id] = holdings.get(sec_id, 0) + qty
        if holdings[sec_id] == 0:
            del holdings[sec_id]
        trade_log.append({
            "date": fill_date,
            "sec_id": sec_id,
            "qty": qty,
            "raw_px": raw_px,
            "adj_fill_px": fill_px_adj,
            "notional_raw": notional_raw,
            "fees": leg_fees,
            "slippage": leg_slip,
            "cost_bps": (leg_fees + leg_slip) / notional_raw * 10_000.0,
        })
    return cash


def _force_exit_suspended(
    holdings: dict[str, int],
    cash: float,
    today: pd.Timestamp,
    panel: PricePanel,
    suspension_max_days: int,
    fee_cfg: FeeConfig,
    slip_cfg: SlippageConfig,
    trade_log: list[dict],
) -> float:
    """Exit any name with no observed trade for > suspension_max_days."""
    cutoff = panel.adj_close.index[max(0, panel.adj_close.index.get_loc(today) - suspension_max_days)]
    for sec_id in list(holdings):
        recent = panel.volume_rm[sec_id].loc[cutoff:today]
        if (recent.fillna(0) > 0).sum() == 0:
            last_px = panel.adj_close[sec_id].loc[:today].dropna()
            if last_px.empty:
                continue
            adj_px = last_px.iloc[-1]
            qty = -holdings[sec_id]
            notional = abs(qty) * adj_px
            leg_fees = fees(notional, fee_cfg)
            leg_slip = slippage(notional, 0.0, slip_cfg)   # max slippage
            cash -= qty * adj_px
            cash -= (leg_fees + leg_slip)
            trade_log.append({
                "date": today, "sec_id": sec_id, "qty": qty,
                "raw_px": adj_px, "adj_fill_px": adj_px,
                "notional_raw": notional, "fees": leg_fees, "slippage": leg_slip,
                "cost_bps": (leg_fees + leg_slip) / notional * 10_000.0,
                "reason": "suspended_force_exit",
            })
            del holdings[sec_id]
    return cash


def _mark_to_market(holdings: dict[str, int], cash: float, adj_close_row: pd.Series) -> float:
    mv = sum(qty * adj_close_row.get(sec_id, np.nan) for sec_id, qty in holdings.items()
             if not pd.isna(adj_close_row.get(sec_id, np.nan)))
    return cash + (mv if not pd.isna(mv) else 0.0)


def run_backtest(
    panel: PricePanel,
    signal_fn: SignalFn,
    rebal_dates: list[pd.Timestamp],
    starting_cash: float,
    fee_cfg: FeeConfig = MPLUS,
    slip_cfg: SlippageConfig = SLIPPAGE,
    suspension_max_days: int = 7,
) -> Ledger:
    """Run a single backtest. Signal fires at close(t); fill at open(t+1)."""
    dates = panel.dates
    cash = starting_cash
    holdings: dict[str, int] = {}
    trade_log: list[dict] = []
    equity_curve: dict[pd.Timestamp, float] = {}
    cash_curve: dict[pd.Timestamp, float] = {}
    holdings_snapshots: dict[pd.Timestamp, dict[str, int]] = {}

    pending_orders: dict[str, int] | None = None
    rebal_set = set(rebal_dates)

    from bursahack.portfolio import orders_from_delta, target_shares

    for i, t in enumerate(dates):
        # 1. Execute any pending orders at today's open
        if pending_orders is not None:
            cash = _apply_orders(
                cash, holdings, pending_orders,
                fill_raw_open=panel.raw_open.loc[t],
                fill_adj_open=panel.adj_open.loc[t],
                adv20=panel.adv20(dates[max(0, i - 1)]),  # ADV as of yesterday
                fee_cfg=fee_cfg, slip_cfg=slip_cfg,
                trade_log=trade_log, fill_date=t,
            )
            pending_orders = None

        # 2. Force-exit suspended names (cheap, only on rebal dates)
        if t in rebal_set and len(holdings) > 0:
            cash = _force_exit_suspended(
                holdings, cash, t, panel,
                suspension_max_days, fee_cfg, slip_cfg, trade_log,
            )

        # 3. Generate signal at close-of-today; orders fill at next open
        if t in rebal_set and i + 1 < len(dates):
            weights = signal_fn(t, panel)
            if weights is not None and len(weights) > 0:
                # Use today's close as the price reference for sizing
                eq = _mark_to_market(holdings, cash, panel.adj_close.loc[t])
                target = target_shares(weights, eq, panel.adj_close.loc[t])
                pending_orders = orders_from_delta(holdings, target)

        # 4. Mark to market on today's close
        equity_curve[t] = _mark_to_market(holdings, cash, panel.adj_close.loc[t])
        cash_curve[t] = cash
        holdings_snapshots[t] = dict(holdings)

    equity = pd.Series(equity_curve).sort_index()
    cash_s = pd.Series(cash_curve).sort_index()
    trades_df = pd.DataFrame(trade_log)
    # Reindex against the engine's full date range -- pandas 3.0 from_dict drops
    # index entries whose value-dict is empty (i.e. days with no holdings).
    holdings_df = pd.DataFrame.from_dict(holdings_snapshots, orient="index")
    holdings_df = holdings_df.reindex(list(holdings_snapshots.keys())).fillna(0).astype(int)

    return Ledger(equity=equity, holdings=holdings_df, trades=trades_df, cash=cash_s)
