"""Impact model: monotone-in-q, zero-at-zero, participation clip, min-notional gate."""
from __future__ import annotations

import pytest

from bursahack.intraday.impact import (
    ExecutionQuote,
    ImpactModel,
    kissell_glantz_impact,
    rogers_satchell_sigma,
)
import polars as pl
from datetime import datetime, timedelta


def test_kg_zero_at_zero() -> None:
    assert kissell_glantz_impact(0, 0.01, 1000) == 0.0
    assert kissell_glantz_impact(100, 0.0, 1000) == 0.0


def test_impact_monotone_in_q() -> None:
    m = ImpactModel()
    base = dict(code="1155", side="buy", price=10.0, sigma_bar=0.001,
                adv_bar_shares=1_000_000.0, is_liquid=True)
    prev = -1.0
    # q below clip ceiling so we actually see the sqrt curve
    for q in [10_000, 20_000, 50_000, 80_000]:
        bps = m.cost_bps(ExecutionQuote(q=q, **base))
        assert bps > prev, f"non-monotone at q={q}: {bps} <= {prev}"
        prev = bps


def test_impact_zero_q_is_zero() -> None:
    m = ImpactModel()
    q = ExecutionQuote(code="1155", side="buy", q=0, price=10.0,
                      sigma_bar=0.001, adv_bar_shares=1_000_000.0)
    assert m.cost_bps(q) == 0.0


def test_impact_participation_clip() -> None:
    m = ImpactModel(participation_cap=0.10)
    base = dict(code="1155", side="buy", price=10.0, sigma_bar=0.001,
                adv_bar_shares=1_000_000.0, is_liquid=True)
    at_cap = m.cost_bps(ExecutionQuote(q=100_000, **base))     # 10% participation
    above_cap = m.cost_bps(ExecutionQuote(q=300_000, **base))  # 30% participation
    # Above cap should be clipped to at_cap (same cost)
    assert above_cap == pytest.approx(at_cap, rel=1e-6)


def test_impact_min_notional_gate() -> None:
    m = ImpactModel(min_trade_notional_rm=16_000.0)
    base = dict(code="1155", side="buy", price=10.0, sigma_bar=0.001,
                adv_bar_shares=1_000_000.0, is_liquid=True)
    # 1500 shares * 10 = 15,000 RM -> below gate
    bps = m.cost_bps(ExecutionQuote(q=1500, **base))
    assert bps >= 1e9


def test_rogers_satchell_basic() -> None:
    ts = [datetime(2021, 1, 1, 9, 0) + timedelta(minutes=i) for i in range(20)]
    df = pl.DataFrame({
        "ts": ts,
        "code": ["X"] * 20,
        "open":  [10.0] * 20,
        "high":  [10.1] * 20,
        "low":   [9.9] * 20,
        "close": [10.05] * 20,
    })
    out = rogers_satchell_sigma(df, window_bars=5)
    # First 4 rows should be null (window not full)
    sig = out.get_column("sigma_rs").to_list()
    assert sig[0] is None
    # Once window fills, sigma > 0
    later = [s for s in sig if s is not None]
    assert all(s > 0 for s in later)
