"""Tests for diagnostics: DSR, PBO, stationary bootstrap CI."""
from __future__ import annotations

import numpy as np
import pytest

from bursahack.intraday.diagnostics import (
    deflated_sharpe_ratio,
    effective_n,
    pbo,
    sharpe_ratio,
    stationary_bootstrap_sharpe_ci,
)


# ---------- Sharpe primitive ----------


def test_sharpe_zero_when_constant():
    r = np.ones(100)
    assert sharpe_ratio(r) == 0.0


def test_sharpe_known_value():
    rng = np.random.default_rng(0)
    r = rng.normal(loc=0.01, scale=0.02, size=10_000)
    sr = sharpe_ratio(r)
    # True SR = 0.01/0.02 = 0.5; finite-sample noise small at T=10k.
    assert 0.45 < sr < 0.55


# ---------- Effective N ----------


def test_effective_n_independent_is_n():
    c = np.eye(10)
    assert effective_n(c) == pytest.approx(10.0)


def test_effective_n_perfect_correlation_collapses():
    c = np.ones((5, 5))
    # All off-diagonal = 1.0 → N_eff = max(1, N*(1-1)) = 1.0
    assert effective_n(c) == 1.0


def test_effective_n_mixed():
    n = 10
    c = np.eye(n) + 0.5 * (1 - np.eye(n))  # off-diag 0.5
    # N_eff = 10 * (1 - 0.5) = 5.0
    assert effective_n(c) == pytest.approx(5.0)


# ---------- DSR ----------


def test_dsr_kills_noise_strategy():
    """A 'best of 1000 random trials' should NOT pass DSR."""
    rng = np.random.default_rng(42)
    n_trials = 1000
    t = 200
    # 1000 random strategies, each T=200 returns from N(0, 1).
    trials = rng.normal(size=(n_trials, t))
    sharpes = np.array([sharpe_ratio(r) for r in trials])
    best_idx = int(np.argmax(sharpes))
    best_returns = trials[best_idx]
    # Use the best one's Sharpe inside trial_sharpes (it IS the max).
    res = deflated_sharpe_ratio(best_returns, sharpes)
    # The "winner" of a noise sweep should look unimpressive after DSR.
    assert res["dsr"] < 0.5, f"DSR should reject noise winner, got {res['dsr']}"


def test_dsr_recognises_real_strategy():
    """A strategy with edge clearly above the noise-max threshold passes DSR.

    For N=20 noise trials at T=2000, the expected-max Sharpe (SR0) is
    roughly sqrt(1/T) * Φ⁻¹(1-1/N) ≈ 0.022*1.96 ≈ 0.043. Our real strat
    is engineered to have SR ≈ 0.30, well above SR0. DSR should pass.
    """
    rng = np.random.default_rng(7)
    n_trials = 20
    t = 2000
    trials = [rng.normal(size=t) for _ in range(n_trials - 1)]
    real = rng.normal(loc=0.30, scale=1.0, size=t)  # SR ≈ 0.30
    trials.append(real)
    sharpes = np.array([sharpe_ratio(r) for r in trials])
    res = deflated_sharpe_ratio(real, sharpes)
    assert res["dsr"] > 0.95, f"DSR should accept clear edge, got {res['dsr']}"


def test_dsr_handles_constant_returns():
    res = deflated_sharpe_ratio(
        np.ones(100), np.array([0.5, 0.3, 0.1])
    )
    assert res["observed_sharpe"] == 0.0
    assert res["dsr"] == 0.5  # Φ(0) = 0.5 — neither rejects nor accepts


# ---------- PBO ----------


def test_pbo_high_for_pure_noise():
    """Pure-noise sweep should give PBO ≈ 0.5 (overfit)."""
    rng = np.random.default_rng(1)
    t = 800
    n_variants = 50
    returns = rng.normal(size=(t, n_variants))
    result = pbo(returns, n_submatrices=8)
    assert 0.3 < result.pbo < 0.7, f"PBO on noise should be ~0.5, got {result.pbo}"


def test_pbo_low_for_one_dominant_strategy():
    """If one variant truly dominates, PBO should be low."""
    rng = np.random.default_rng(3)
    t = 400
    n_variants = 20
    returns = rng.normal(size=(t, n_variants))
    # Make variant 0 dominant.
    returns[:, 0] += 0.3
    result = pbo(returns, n_submatrices=8)
    assert result.pbo < 0.2, f"PBO with dominant strat should be low, got {result.pbo}"


def test_pbo_rejects_bad_args():
    with pytest.raises(ValueError):
        pbo(np.ones(5), n_submatrices=8)  # 1D
    with pytest.raises(ValueError):
        pbo(np.ones((10, 5)), n_submatrices=3)  # odd S
    with pytest.raises(ValueError):
        pbo(np.ones((5, 5)), n_submatrices=8)  # T<S


# ---------- Stationary bootstrap ----------


def test_bootstrap_ci_brackets_truth():
    rng = np.random.default_rng(2)
    r = rng.normal(loc=0.05, scale=1.0, size=500)  # true SR ≈ 0.05
    res = stationary_bootstrap_sharpe_ci(r, n_boot=500, rng_seed=2)
    assert res["lo"] <= res["observed_sharpe"] <= res["hi"]
    # CI should bracket truth most of the time.
    assert res["lo"] - 0.1 < 0.05 < res["hi"] + 0.1


def test_bootstrap_ci_widens_with_smaller_n():
    rng = np.random.default_rng(5)
    small = rng.normal(loc=0.05, scale=1.0, size=50)
    big = rng.normal(loc=0.05, scale=1.0, size=2000)
    ci_small = stationary_bootstrap_sharpe_ci(small, n_boot=300, rng_seed=0)
    ci_big = stationary_bootstrap_sharpe_ci(big, n_boot=300, rng_seed=0)
    width_small = ci_small["hi"] - ci_small["lo"]
    width_big = ci_big["hi"] - ci_big["lo"]
    assert width_small > width_big
