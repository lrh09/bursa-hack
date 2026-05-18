"""Per-regime P&L: gross is invariant; net is regime-specific (retail > insto)."""
from __future__ import annotations

from datetime import date

import polars as pl

from bursahack.costs import InstitutionalFee, MPlusRetailFee
from bursahack.intraday.engine import IntradayEngine
from bursahack.intraday.registry import SIGNAL_SCHEMA

from tests.intraday._fixtures import synthetic_session


def test_gross_invariant_net_regime_specific():
    """Same signals run against both regimes:
       - gross_ret identical across regimes (rounded)
       - net_ret_retail < net_ret_institutional (retail is more expensive)
    """
    def price(i):
        o = 10.0 + 0.001 * i
        c = 10.0 + 0.001 * (i + 0.5)
        return (o, max(o, c), min(o, c), c, 1000, 1000 * c)
    bars = synthetic_session(date(2024, 1, 3), code="REG", price_fn=price)
    ts = bars.get_column("ts")[10]
    sig = pl.DataFrame({
        "ts": [ts], "code": ["REG"], "side": [1],
        "entry_price": [10.0], "stop_price": [None],
        "target_price": [None], "exit_at": [None],
    }, schema=SIGNAL_SCHEMA)

    engine = IntradayEngine(
        cost_regimes=[MPlusRetailFee(), InstitutionalFee()],
    )
    res = engine.run(
        bars=bars, signals=sig,
        universe_members=pl.DataFrame({"date": [], "code": []}),
    )
    retail = res.trades.filter(pl.col("regime") == "mplus_retail")
    insto = res.trades.filter(pl.col("regime") == "institutional")
    assert retail.height == 1 and insto.height == 1

    gross_r = retail.get_column("gross_ret")[0]
    gross_i = insto.get_column("gross_ret")[0]
    assert abs(gross_r - gross_i) < 1e-12, (
        f"gross should be regime-invariant: retail={gross_r}, insto={gross_i}"
    )

    net_r = retail.get_column("net_ret")[0]
    net_i = insto.get_column("net_ret")[0]
    assert net_r < net_i, (
        f"retail net should be lower (more cost): retail={net_r}, insto={net_i}"
    )
