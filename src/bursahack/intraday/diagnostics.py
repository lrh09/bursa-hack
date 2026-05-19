"""Statistical diagnostics: tells luck from real.

Three weapons here, each answering a different "is this real?" question:

1. **Deflated Sharpe Ratio (DSR)** — Bailey & Lopez de Prado 2014.
   Question: a variant's reported Sharpe looks great. But I tried N
   variants — what's the probability the true Sharpe is positive after
   correcting for multiple-testing AND for the non-normality of returns?
   `deflated_sharpe_ratio()` returns DSR in [0, 1]. > 0.95 = strong signal.

2. **PBO (Probability of Backtest Overfitting)** — Bailey, Borwein,
   Lopez de Prado, Zhu 2014, via CSCV. Question: if I pick the
   IS-best variant, how often does it underperform the median OOS?
   PBO = fraction of CSCV splits where that's true. PBO < 0.5 = good.
   PBO ~ 0.5 = the family is overfit; you can't pick a winner.

3. **Stationary block bootstrap CI on Sharpe** — Politis-Romano 1994.
   Question: given my realized returns are autocorrelated, what's a
   non-parametric 95% confidence interval on the Sharpe? Block bootstrap
   preserves short-range dependence; the geometric block length keeps
   it stationary.

What's NOT here yet:
  - Hansen SPA, Romano-Wolf StepM. Both would tighten the multiple-test
    correction further but are advisory. DSR + PBO + bootstrap CI is
    enough to certify or kill a strategy.

All functions are pure-numpy and stateless.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import comb, e as MATH_E

import numpy as np
from scipy.stats import norm

EULER_MASCHERONI = 0.5772156649015329


# ---------------------------------------------------------------------------
# Sharpe primitives
# ---------------------------------------------------------------------------


def sharpe_ratio(
    returns: np.ndarray,
    periods_per_year: int | None = None,
) -> float:
    """Plain Sharpe = mean/std. Annualised if `periods_per_year` is given."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if r.size < 2:
        return 0.0
    s = r.std(ddof=1)
    if s == 0.0:
        return 0.0
    sr = r.mean() / s
    if periods_per_year is not None:
        sr *= np.sqrt(periods_per_year)
    return float(sr)


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014)
# ---------------------------------------------------------------------------


def _expected_max_sharpe(n_trials: int, trial_sr_variance: float) -> float:
    """E[max SR_i] under the false-strategy theorem (LdP AFML §8).

    For N independent trial Sharpes drawn from a distribution with
    variance V, the expected maximum is approximately
        sqrt(V) * ((1 - γ) Φ⁻¹(1 - 1/N) + γ Φ⁻¹(1 - 1/(N e)))
    where γ is Euler-Mascheroni.
    """
    if n_trials <= 1 or trial_sr_variance <= 0.0:
        return 0.0
    sqrt_v = float(np.sqrt(trial_sr_variance))
    a = norm.ppf(1.0 - 1.0 / n_trials)
    b = norm.ppf(1.0 - 1.0 / (n_trials * MATH_E))
    return sqrt_v * ((1.0 - EULER_MASCHERONI) * a + EULER_MASCHERONI * b)


def effective_n(corr_matrix: np.ndarray) -> float:
    """Lopez de Prado's effective-N correction for correlated trials.

    N_eff = N * (1 - <ρ>) where <ρ> is the average off-diagonal pairwise
    correlation. Independent trials (<ρ>=0) → N_eff = N. Perfectly
    correlated trials (<ρ>=1) → N_eff = 0; floored at 1.0.
    """
    c = np.asarray(corr_matrix, dtype=float)
    n = c.shape[0]
    if n <= 1:
        return float(n)
    # off-diagonal mean
    mask = ~np.eye(n, dtype=bool)
    off = c[mask]
    if off.size == 0:
        return float(n)
    mean_off = float(np.nan_to_num(off.mean(), nan=0.0))
    n_eff = n * (1.0 - mean_off)
    return float(max(1.0, n_eff))


def deflated_sharpe_ratio(
    returns: np.ndarray,
    trial_sharpes: np.ndarray,
    effective_n_trials: float | None = None,
) -> dict[str, float]:
    """Compute DSR for a candidate strategy's returns.

    Args:
      returns: 1D array of per-period returns for the CANDIDATE strategy
        (e.g., the top-Sharpe variant's net_ret sequence).
      trial_sharpes: 1D array of per-variant Sharpe ratios across all
        trials in the sweep. The candidate's own Sharpe should be in
        this array (it's the max). Used to estimate trial variance and
        derive SR0.
      effective_n_trials: optional override for N. If None, uses
        `len(trial_sharpes)` directly. Pass `effective_n(corr_matrix)`
        when variants are correlated.

    Returns dict with keys:
      observed_sharpe, sr0, t, skewness, kurtosis, dsr, z
    """
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    t = r.size
    if t < 4:
        # Too few obs to evaluate — return Φ(0)=0.5, ambiguous.
        return {
            "observed_sharpe": 0.0, "sr0": 0.0, "t": t,
            "skewness": 0.0, "kurtosis": 0.0, "dsr": 0.5, "z": 0.0,
        }
    mu = r.mean()
    sigma = r.std(ddof=1)
    if sigma == 0.0:
        # Constant returns: undefined Sharpe; ambiguous.
        return {
            "observed_sharpe": 0.0, "sr0": 0.0, "t": t,
            "skewness": 0.0, "kurtosis": 0.0, "dsr": 0.5, "z": 0.0,
        }
    sr_hat = mu / sigma

    # Skewness and kurtosis (full kurtosis = excess + 3) of returns.
    z = (r - mu) / sigma
    skew = float(np.mean(z**3))
    kurt = float(np.mean(z**4))  # full kurtosis

    # Variance of trial Sharpes (across all sweep variants).
    ts = np.asarray(trial_sharpes, dtype=float)
    ts = ts[~np.isnan(ts)]
    if ts.size < 2:
        sr0 = 0.0
    else:
        n_trials = effective_n_trials if effective_n_trials is not None else float(ts.size)
        v_sr = float(ts.var(ddof=1))
        sr0 = _expected_max_sharpe(int(round(n_trials)), v_sr)

    # DSR formula (Bailey & Lopez de Prado 2014, eq. 9).
    denom = 1.0 - skew * sr_hat + ((kurt - 1.0) / 4.0) * sr_hat**2
    if denom <= 0.0:
        # Numerically pathological; the formula is undefined. Return
        # observed-Sharpe-vs-SR0 z-score without the kurtosis correction
        # so callers still get a usable number.
        z_dsr = (sr_hat - sr0) * np.sqrt(max(t - 1, 1))
    else:
        z_dsr = (sr_hat - sr0) * np.sqrt(max(t - 1, 1) / denom)
    dsr = float(norm.cdf(z_dsr))
    return {
        "observed_sharpe": float(sr_hat),
        "sr0": float(sr0),
        "t": int(t),
        "skewness": float(skew),
        "kurtosis": float(kurt),
        "dsr": dsr,
        "z": float(z_dsr),
    }


# ---------------------------------------------------------------------------
# PBO via CSCV (Bailey, Borwein, LdP, Zhu 2014)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PBOResult:
    """Probability-of-backtest-overfitting result."""
    pbo: float
    n_combinations: int
    median_logit_lambda: float


def pbo(
    period_returns: np.ndarray,
    n_submatrices: int = 16,
    max_combos: int = 5000,
    rng_seed: int = 0,
) -> PBOResult:
    """Compute PBO via combinatorially symmetric CV.

    Args:
      period_returns: (T, N) matrix of per-period returns. T = time
        observations (e.g., trades or daily PnL); N = number of variants.
      n_submatrices: S, the number of equal-size time-slices to split
        T into. Must be even; T must be >= S. Larger S = more combos
        but also smaller submatrices. S=16 is the BBL-Zhu default.
      max_combos: cap on the number of C(S, S/2) IS/OOS halvings to
        evaluate. C(16, 8) = 12870 which is fine; C(20, 10) = 184756
        which we'd subsample.
      rng_seed: deterministic subsampling when combo count exceeds max.

    Returns:
      PBOResult(pbo, n_combinations, median_logit_lambda).
    """
    m = np.asarray(period_returns, dtype=float)
    if m.ndim != 2:
        raise ValueError(f"period_returns must be 2D, got shape {m.shape}")
    t, n = m.shape
    if n_submatrices < 2 or n_submatrices % 2:
        raise ValueError("n_submatrices must be an even integer >= 2")
    if t < n_submatrices:
        raise ValueError(f"T={t} < S={n_submatrices}; not enough rows to split")

    # 1) Slice T into S equal blocks (last block absorbs remainder).
    sub_size = t // n_submatrices
    blocks: list[np.ndarray] = []
    for s in range(n_submatrices - 1):
        blocks.append(m[s * sub_size: (s + 1) * sub_size])
    blocks.append(m[(n_submatrices - 1) * sub_size:])

    # 2) Per-block Sharpe matrix: (S, N). Diagnostic: each block's per-
    # variant Sharpe. We aggregate to IS/OOS Sharpe by mean-of-blocks.
    block_sr = np.zeros((n_submatrices, n))
    for s, blk in enumerate(blocks):
        for v in range(n):
            block_sr[s, v] = sharpe_ratio(blk[:, v])

    # 3) Enumerate (or sample) halvings.
    half = n_submatrices // 2
    total_combos = comb(n_submatrices, half)
    if total_combos <= max_combos:
        is_iter: list[tuple[int, ...]] = list(combinations(range(n_submatrices), half))
    else:
        rng = np.random.default_rng(rng_seed)
        seen: set[tuple[int, ...]] = set()
        while len(seen) < max_combos:
            sel = tuple(sorted(rng.choice(n_submatrices, half, replace=False).tolist()))
            seen.add(sel)
        is_iter = list(seen)

    overfit_count = 0
    logits: list[float] = []
    for is_idx in is_iter:
        is_set = set(is_idx)
        oos_idx = [s for s in range(n_submatrices) if s not in is_set]
        is_mean = block_sr[list(is_set)].mean(axis=0)
        oos_mean = block_sr[oos_idx].mean(axis=0)
        # Pick IS-best variant n*.
        best_is = int(np.argmax(is_mean))
        # OOS rank of n*: percentile in [0, 1].
        order = np.argsort(oos_mean)
        oos_rank = int(np.where(order == best_is)[0][0])
        # 0-based rank → percentile.
        if n == 1:
            oos_pct = 1.0
        else:
            oos_pct = oos_rank / (n - 1)
        # Logit transform per BBL-Zhu: w = pct / (1 - pct).
        # Edge: clip away from 0 and 1 so log is defined.
        pct_clipped = float(np.clip(oos_pct, 1e-6, 1.0 - 1e-6))
        w = pct_clipped / (1.0 - pct_clipped)
        logits.append(float(np.log(w)))
        if oos_pct <= 0.5:
            overfit_count += 1

    pbo_stat = overfit_count / len(is_iter)
    return PBOResult(
        pbo=pbo_stat,
        n_combinations=len(is_iter),
        median_logit_lambda=float(np.median(logits)) if logits else 0.0,
    )


# ---------------------------------------------------------------------------
# Stationary block bootstrap (Politis-Romano 1994)
# ---------------------------------------------------------------------------


def stationary_bootstrap_sharpe_ci(
    returns: np.ndarray,
    block_length: float | None = None,
    n_boot: int = 2000,
    alpha: float = 0.05,
    rng_seed: int = 0,
    periods_per_year: int | None = None,
) -> dict[str, float]:
    """Block-bootstrap CI on the Sharpe ratio.

    Args:
      returns: 1D array of per-period returns.
      block_length: mean block length for the geometric distribution. If
        None, defaults to T^(1/3) (asymptotic optimal rate). Politis-
        White data-driven length is not implemented; T^(1/3) is robust.
      n_boot: number of bootstrap replications.
      alpha: tail probability (0.05 → 95% CI).
      rng_seed: RNG seed.

    Returns dict with keys: observed_sharpe, lo, hi, mean_boot, std_boot.
    """
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    t = r.size
    if t < 8:
        sr = sharpe_ratio(r, periods_per_year=periods_per_year)
        return {
            "observed_sharpe": sr, "lo": sr, "hi": sr,
            "mean_boot": sr, "std_boot": 0.0,
        }
    if block_length is None:
        block_length = max(2.0, float(t) ** (1.0 / 3.0))
    p_break = 1.0 / block_length  # geometric param: each step ends the block w.p. p

    rng = np.random.default_rng(rng_seed)
    sr_obs = sharpe_ratio(r, periods_per_year=periods_per_year)
    boots = np.empty(n_boot)

    for b in range(n_boot):
        # Build a length-T resample by drawing a starting index then
        # walking forward; at each step, with probability p_break, jump
        # to a new random start.
        out = np.empty(t)
        idx = int(rng.integers(0, t))
        for i in range(t):
            out[i] = r[idx]
            if rng.random() < p_break:
                idx = int(rng.integers(0, t))
            else:
                idx = (idx + 1) % t
        boots[b] = sharpe_ratio(out, periods_per_year=periods_per_year)

    lo = float(np.quantile(boots, alpha / 2.0))
    hi = float(np.quantile(boots, 1.0 - alpha / 2.0))
    return {
        "observed_sharpe": float(sr_obs),
        "lo": lo,
        "hi": hi,
        "mean_boot": float(boots.mean()),
        "std_boot": float(boots.std(ddof=1)),
    }


__all__ = [
    "PBOResult",
    "deflated_sharpe_ratio",
    "effective_n",
    "pbo",
    "sharpe_ratio",
    "stationary_bootstrap_sharpe_ci",
]
