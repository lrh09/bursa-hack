"""Lock in the decision layer: scorecard, liquidation-prob, ranking.

Built on the full 9-position TSLA book (see test_combine.py for the id map).

Contracts asserted:
  * prob_liquidation is non-decreasing in sigma at a FIXED seed (the GBM
    diffusion term scales with sigma against a fixed barrier on identical
    shocks) -- compared on a net-long subset near its down-liquidation spot so
    the probabilities are non-trivial.
  * rank_decisions is deterministic: two calls with the same seed return
    identical ids AND identical scores, and ranked[0]['rank'] == 1.
  * scorecard returns every documented key, VaR99 >= VaR95, defined_risk is a
    bool.

MC path counts are kept modest here for test runtime; the monotonicity gap is
robust because the same seed reuses identical shocks across the two sigmas.
"""
from __future__ import annotations

import math
from dataclasses import replace

import pytest

from bursahack.paths import REPO_ROOT
from bursahack.options import bookio, combine, decision
from bursahack.options.types import Measure


BOOK_PATH = REPO_ROOT / "configs" / "options" / "tsla_book_2026-06-18.yaml"


@pytest.fixture(scope="module")
def book():
    return bookio.load_book(BOOK_PATH)


@pytest.fixture(scope="module")
def market(book):
    return book.markets["TSLA"]


@pytest.fixture(scope="module")
def account(book):
    return book.account


# ===========================================================================
# 1. prob_liquidation monotone in sigma (same seed, same shocks)
# ===========================================================================
def test_prob_liquidation_monotone_in_sigma(book, market, account):
    # Net-long subset (drop the naked short calls 8/9) with a thinned netliq so
    # the down-liquidation sits ~ -16% spot -> a 90d MC produces non-trivial,
    # sigma-distinguishable touch probabilities.
    sub = combine.select_subset(book, [1, 2, 3, 4, 5, 6, 7])
    acct = replace(account, netliq=250_000.0)

    mkt_lo = replace(market, sigma=0.30)
    mkt_hi = replace(market, sigma=0.70)

    kw = dict(horizon_days=90, n_paths=4_000, n_steps=40, seed=7)
    p_lo = decision.prob_liquidation(sub, mkt_lo, acct, **kw)["prob_liquidation"]
    p_hi = decision.prob_liquidation(sub, mkt_hi, acct, **kw)["prob_liquidation"]

    # both non-trivial at this thinned cushion, and higher vol => higher touch
    assert 0.0 < p_lo < 1.0
    assert p_hi >= p_lo

    # the returned shape carries the documented keys
    res = decision.prob_liquidation(sub, mkt_hi, acct, **kw)
    for key in ("prob_liquidation", "expected_time_to_touch_days", "terminal",
                "el_terminal", "n_paths", "n_steps", "horizon_days", "measure"):
        assert key in res
    assert set(res["terminal"]) == {"mean", "p5", "p50", "p95"}
    assert res["measure"] == Measure.RISK_NEUTRAL.value


# ===========================================================================
# 2. rank_decisions determinism (ids + scores identical at fixed seed)
# ===========================================================================
def test_rank_determinism(book, market, account):
    acct = replace(account, netliq=400_000.0)
    candidates = [[1, 2], [8, 9], [1, 5, 6, 8]]

    r1 = decision.rank_decisions(book, market, acct, candidates, horizon_days=30, seed=7)
    r2 = decision.rank_decisions(book, market, acct, candidates, horizon_days=30, seed=7)

    ids1 = [row["ids"] for row in r1["ranked"]]
    ids2 = [row["ids"] for row in r2["ranked"]]
    scores1 = [row["score"] for row in r1["ranked"]]
    scores2 = [row["score"] for row in r2["ranked"]]

    assert ids1 == ids2
    assert scores1 == scores2

    # top row is rank 1; ranks are a contiguous 1..N
    assert r1["ranked"][0]["rank"] == 1
    assert [row["rank"] for row in r1["ranked"]] == list(range(1, len(candidates) + 1))

    # deterministic verdict strings are derived from the numbers (no randomness)
    top = r1["ranked"][0]
    assert isinstance(top["recommendation"], str) and top["recommendation"]
    assert isinstance(top["dominating_risk"], str) and top["dominating_risk"]
    assert isinstance(top["tradeoff"], str) and top["tradeoff"]
    # the verdict strings are also stable across runs
    assert top["recommendation"] == r2["ranked"][0]["recommendation"]
    assert top["dominating_risk"] == r2["ranked"][0]["dominating_risk"]


# ===========================================================================
# 3. scorecard returns all documented keys; VaR99 >= VaR95; defined_risk bool
# ===========================================================================
def test_scorecard_keys(book, market, account):
    acct = replace(account, netliq=400_000.0)
    # subset 8 = short 460C: undefined-risk with a real up-tail loss, so the MC
    # VaR/CVaR are non-zero and the VaR99 >= VaR95 ordering is exercised.
    sc = decision.scorecard(book, [8], market, acct, horizon_days=30,
                            n_paths=20_000, seed=7)

    documented = {
        "ids", "labels", "horizon_days", "measure", "T",
        "ev_rn", "ev_rw", "pop", "p_max_profit", "p_max_loss",
        "var_95", "var_99", "cvar_95", "cvar_99",
        "max_loss", "max_profit", "defined_risk",
        "bp_consumed", "excess_liq_after",
        "liquidation_down_pct", "liquidation_up_pct", "liquidation_prob",
        "delta", "vega", "theta_day",
        "capital_efficiency", "reward_risk",
    }
    assert documented.issubset(set(sc.keys()))

    # VaR/CVaR are positive losses, monotone in alpha
    assert sc["var_99"] >= sc["var_95"]
    assert sc["cvar_99"] >= sc["cvar_95"]
    assert sc["var_95"] >= 0.0
    # this subset has a genuine tail loss (undefined short call)
    assert sc["var_99"] > 0.0

    assert isinstance(sc["defined_risk"], bool)
    assert sc["defined_risk"] is False          # short 460C is undefined-risk
    assert sc["measure"] == Measure.RISK_NEUTRAL.value
    assert sc["ev_rw"] is None                  # no mu passed -> rw EV is None

    # ev_rw populated when mu is given
    sc_rw = decision.scorecard(book, [8], market, acct, horizon_days=30,
                               measure=Measure.REAL_WORLD, mu=0.10,
                               n_paths=20_000, seed=7)
    assert sc_rw["ev_rw"] is not None
    assert sc_rw["measure"] == Measure.REAL_WORLD.value
