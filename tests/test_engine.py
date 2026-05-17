"""Engine smoke tests with synthetic and real fixtures."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bursahack.engine import PricePanel, Ledger, build_price_panel, run_backtest


def _synthetic_panel(n_days: int = 30) -> PricePanel:
    """Two stocks, 30 days. Stock A flat at RM 1.00; stock B +1%/day."""
    dates = pd.date_range("2020-01-02", periods=n_days, freq="B")
    a = np.full(n_days, 1.00)
    b = 1.00 * (1.01 ** np.arange(n_days))
    adj_close = pd.DataFrame({"A": a, "B": b}, index=dates)
    adj_open = adj_close.shift(1).bfill()                    # open = prior close
    raw_open = adj_open.copy()
    # Plenty of volume so slippage is negligible
    volume_rm = pd.DataFrame({"A": np.full(n_days, 1e9), "B": np.full(n_days, 1e9)}, index=dates)
    return PricePanel(adj_close=adj_close, adj_open=adj_open, raw_open=raw_open, volume_rm=volume_rm)


def test_buy_and_hold_one_stock_total_return_after_fees():
    """RM 100k all into stock B (which grows 1%/day for 30 business days).
    Closed-form total return = 1.01**29 - 1 ~= 33.45% on the position; fees
    eat a small amount on the single buy leg."""
    panel = _synthetic_panel(30)
    rebal = [panel.dates[0]]

    def signal(t, p):
        return pd.Series({"B": 1.0})  # 100% weight on B

    ledger = run_backtest(panel, signal, rebal, starting_cash=100_000.0)
    final = ledger.equity.iloc[-1]
    # Compare against the no-cost ideal: 100k * 1.01^29 (positions sized at close
    # of day 0, filled at open of day 1 which equals close of day 0 in our fixture).
    # Lot rounding gives slightly less than full deployment.
    ideal = 100_000.0 * 1.01 ** (len(panel.dates) - 1)
    assert 0.985 * ideal < final < ideal, (
        f"buy-and-hold final equity {final:,.0f} vs ideal {ideal:,.0f}"
    )
    # Exactly one buy trade
    assert len(ledger.trades) == 1
    assert ledger.trades.iloc[0]["qty"] > 0


def test_signal_receives_asof_timestamp():
    """Engine passes the rebal timestamp to signal_fn. Strategies are responsible
    for not looking past `t` (vectorised signals do this via precomputed matrices
    that are mathematically NaN-padded at the front)."""
    panel = _synthetic_panel(10)
    rebal = [panel.dates[3]]
    seen = {}

    def signal(t, p):
        seen["t"] = t
        return pd.Series({"A": 0.5, "B": 0.5})

    run_backtest(panel, signal, rebal, starting_cash=100_000.0)
    assert seen["t"] == rebal[0]


def test_zero_signal_keeps_cash():
    panel = _synthetic_panel(10)
    rebal = [panel.dates[0]]

    def signal(t, p):
        return pd.Series(dtype=float)

    ledger = run_backtest(panel, signal, rebal, starting_cash=50_000.0)
    assert ledger.equity.iloc[-1] == pytest.approx(50_000.0, abs=1e-6)
    assert len(ledger.trades) == 0


def test_sell_to_zero_then_repurchase_clean():
    panel = _synthetic_panel(20)
    rebal = [panel.dates[0], panel.dates[5], panel.dates[10]]

    def signal(t, p):
        # Alternate between full-A and full-B
        i = list(rebal).index(t)
        return pd.Series({"A": 1.0}) if i % 2 == 0 else pd.Series({"B": 1.0})

    ledger = run_backtest(panel, signal, rebal, starting_cash=100_000.0)
    # 3 rebalances -> 5 trades (initial buy A, sell A+buy B, sell B+buy A)
    assert len(ledger.trades) == 5
    # Final position should be in A
    final_holdings = ledger.holdings.iloc[-1]
    assert final_holdings["A"] > 0
    assert final_holdings["B"] == 0


@pytest.mark.skipif(
    not Path("data/master.parquet").exists(),
    reason="real data not ingested in this environment",
)
def test_real_data_equal_weight_top10_sanity():
    """Sanity check on real data: equal-weight 10 most-liquid 4-digit names,
    rebalance monthly across 2018-01 to 2018-12, end equity should be within
    a sensible band (no crashes, no negative equity)."""
    import pyarrow.parquet as pq
    from bursahack.paths import PARQUET_DIR
    from bursahack.universe import equity_mask

    df = pq.read_table(
        PARQUET_DIR,
        columns=["SECURITY_ID", "TICKER", "DATE", "OPEN", "ADJ_OPEN", "ADJ_CLOSE", "ADJ_VOLUME"],
        filters=[("year", ">=", 2017), ("year", "<=", 2018)],
    ).to_pandas()
    df["DATE"] = pd.to_datetime(df["DATE"])
    df = df[equity_mask(df["TICKER"])]
    panel = build_price_panel(df)

    # Pick top-10 by 2017 average traded value (avoid lookahead by using 2017 only)
    adv = panel.volume_rm.loc[:"2017-12-31"].mean().sort_values(ascending=False).head(10)
    top10 = adv.index.tolist()

    rebal = pd.date_range("2018-01-02", "2018-12-31", freq="BMS")
    rebal = [d for d in rebal if d in panel.dates]

    def signal(t, p):
        return pd.Series({s: 1.0 / len(top10) for s in top10})

    ledger = run_backtest(panel, signal, rebal, starting_cash=350_000.0)
    final = ledger.equity.iloc[-1]
    # 2018 was a down year on Bursa; a top-10 equal-weight portfolio could plausibly
    # finish anywhere in [-25%, +25%]. Just check we didn't blow up.
    assert 250_000 < final < 500_000, f"unrealistic final equity {final:,.0f}"
    assert (ledger.equity > 0).all()
    assert len(ledger.trades) > 0
