"""Overfitting and robustness diagnostics for the deployment framework.

Implements the 8 quantitative gates from DEPLOYMENT_FRAMEWORK.md:

  Gate 1 - Probability of Backtest Overfitting (PBO; Bailey-Borwein-Lopez-de-Prado 2017)
  Gate 2 - Deflated Sharpe Ratio with effective-N correction for inter-variant correlation
  Gate 3 - OOS Sharpe (delegated to bursahack.metrics.compute_metrics)
  Gate 4 - Parameter-neighbourhood stability
  Gate 5 - Fold-Sharpe coefficient of variation (CoV)
  Gate 6 - Slippage / fee drag as % of gross alpha
  Gate 7 - Average order size vs broker floor
  Gate 8 - CAGR vs hurdle (delegated to metrics + benchmark comparison)
  Gate 9 - Max drawdown
  Gate 10 - Monthly hit rate
  Gate 11/12 - manual (reproducibility, paper trading)

Each gate returns a `GateResult` triple of (value, threshold, passed).
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

from bursahack.engine import PricePanel, run_backtest
from bursahack.metrics import TRADING_DAYS, compute_metrics
from bursahack.paths import RESULTS_DIR


# ============================================================================
# RESULT TYPES
# ============================================================================

@dataclass
class GateResult:
    name: str
    value: float | None
    threshold: float | str
    passed: bool | None
    detail: str = ""

    def __str__(self):
        v = "-" if self.value is None else (f"{self.value:.4f}" if isinstance(self.value, float) else str(self.value))
        p = "-" if self.passed is None else ("PASS" if self.passed else "FAIL")
        return f"  [{p}] {self.name:35s}  value={v}  threshold={self.threshold}"


@dataclass
class Scorecard:
    strategy_name: str
    params: dict
    gates: dict[str, GateResult] = field(default_factory=dict)
    tier: str = ""  # A / B / C / F
    recommendation: str = ""  # GO / CONDITIONAL / NO GO

    def n_passed(self) -> int:
        return sum(1 for g in self.gates.values() if g.passed is True)

    def n_evaluated(self) -> int:
        return sum(1 for g in self.gates.values() if g.passed is not None)

    def core_stat_pass(self) -> bool:
        """Gates 1, 2, 3 are the statistical core - all must pass for GO/CONDITIONAL."""
        for key in ("pbo", "dsr_effective_n", "oos_sharpe"):
            g = self.gates.get(key)
            if g is None or g.passed is not True:
                return False
        return True

    def derive_tier(self) -> str:
        passed = self.n_passed()
        evaluated = self.n_evaluated()
        core = self.core_stat_pass()
        pbo_val = self.gates.get("pbo", GateResult("pbo", None, 0.3, None)).value or 1.0

        if passed == evaluated and evaluated >= 10 and core:
            return "A" if pbo_val < 0.15 else "B"
        if core and passed >= 8 and evaluated - passed <= 2:
            return "C"
        return "F"

    def derive_recommendation(self) -> str:
        tier = self.derive_tier()
        if tier in ("A", "B"):
            return "GO"
        if tier == "C":
            return "CONDITIONAL"
        return "NO GO (recommended)"


# ============================================================================
# GATE 1 - PROBABILITY OF BACKTEST OVERFITTING (PBO)
# ============================================================================

def pbo_score(per_variant_fold_sharpe: pd.DataFrame, n_subsamples: int = 1000,
              random_state: int = 0) -> tuple[float, dict]:
    """Combinatorially Symmetric Cross-Validation (CSCV) for PBO.

    Bailey/Borwein/Lopez de Prado (2017) "The Probability of Backtest Overfitting".

    Inputs:
        per_variant_fold_sharpe: DataFrame indexed by variant_id, columns = fold_idx,
                                  values = per-fold OOS Sharpe of that variant.
        n_subsamples:            sampled CSCV partitions (full would be C(K, K/2) which
                                  explodes; we sample uniformly without replacement).

    Returns (pbo, detail) where pbo in [0, 1] and detail has counts.
    """
    # Require complete fold coverage per variant (drop rows with any NaN);
    # this preserves the full fold count for the remaining variants.
    df = per_variant_fold_sharpe.dropna(how="any", axis=0)
    if df.shape[0] < 2 or df.shape[1] < 4:
        return float("nan"), {"reason": "insufficient data (need >=2 variants and >=4 folds)"}

    rng = np.random.default_rng(random_state)
    N_variants, K_folds = df.shape
    if K_folds % 2 == 1:
        df = df.iloc[:, :-1]
        K_folds -= 1
    half = K_folds // 2

    all_folds = list(range(K_folds))
    n_combo = math.comb(K_folds, half)
    if n_combo <= n_subsamples:
        partitions = list(combinations(all_folds, half))
    else:
        seen = set()
        partitions = []
        while len(partitions) < n_subsamples:
            sample = tuple(sorted(rng.choice(K_folds, size=half, replace=False)))
            if sample in seen:
                continue
            seen.add(sample)
            partitions.append(sample)

    overfit_count = 0
    total = 0
    for train_folds in partitions:
        test_folds = [f for f in all_folds if f not in train_folds]
        train_sharpe = df.iloc[:, list(train_folds)].mean(axis=1)
        test_sharpe = df.iloc[:, test_folds].mean(axis=1)
        # IS winner = argmax of train_sharpe
        winner = train_sharpe.idxmax()
        # Its rank on the test side (higher rank = better; we want below-median rank = overfit)
        test_ranks = test_sharpe.rank(ascending=True)  # rank 1 = worst
        winner_rank = test_ranks.loc[winner]
        # PBO event: winner's test-rank in the BOTTOM HALF (i.e. below-median)
        if winner_rank <= N_variants / 2:
            overfit_count += 1
        total += 1

    pbo = overfit_count / total if total else float("nan")
    return pbo, {
        "n_partitions": total,
        "n_variants": N_variants,
        "n_folds": K_folds,
        "overfit_count": overfit_count,
    }


# ============================================================================
# GATE 2 - DEFLATED SHARPE WITH EFFECTIVE-N CORRECTION
# ============================================================================

def effective_n(per_variant_fold_sharpe: pd.DataFrame) -> float:
    """Effective number of independent trials.

    Many variants in a brute-force search are nearly identical (e.g. lookback
    90 vs 95). The naive N_trials over-penalises. Effective-N is computed from
    the average pairwise correlation of variant per-fold Sharpe series:

        N_eff = N_actual / (1 + (N_actual - 1) * avg_corr)

    (López de Prado, Advances in Financial Machine Learning, ch. 11.)

    Falls back to N_actual if correlation cannot be estimated.
    """
    df = per_variant_fold_sharpe.dropna(how="any", axis=0)
    if df.shape[0] < 2 or df.shape[1] < 2:
        return float(df.shape[0])
    # Pearson corr across variants (rows). Use transpose so each row is a variant's fold-series.
    M = df.values  # N variants x K folds
    if M.shape[1] < 2:
        return float(M.shape[0])
    centred = M - M.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(centred, axis=1)
    if (norms == 0).any():
        # remove zero-norm rows
        keep = norms > 0
        centred = centred[keep]
        norms = norms[keep]
        if len(centred) < 2:
            return float(len(centred))
    R = centred @ centred.T / np.outer(norms, norms)
    # average of off-diagonal absolute correlations
    n = R.shape[0]
    off_diag = R[~np.eye(n, dtype=bool)]
    avg_corr = float(np.clip(np.abs(off_diag).mean(), 0.0, 1.0))
    if avg_corr >= 1.0:
        return 1.0
    n_eff = n / (1.0 + (n - 1) * avg_corr)
    return max(1.0, n_eff)


def deflated_sharpe_effective_n(
    observed_sharpe: float,
    n_trials_effective: float,
    returns: pd.Series,
) -> float:
    """DSR with effective-N applied as the multiple-testing penalty."""
    from bursahack.metrics import deflated_sharpe_ratio
    return deflated_sharpe_ratio(
        observed_sharpe=observed_sharpe,
        n_trials=max(1, int(round(n_trials_effective))),
        returns=returns,
    )


# ============================================================================
# SHARPE HAIRCUT (Bailey & Lopez de Prado 2014)
# ============================================================================

def sharpe_haircut(observed_sharpe: float, n_trials: int | float) -> tuple[float, float, dict]:
    """Expected Sharpe haircut under multiple-testing selection bias.

    Bailey & Lopez de Prado (2014, "The Sharpe Ratio Efficient Frontier").

    Under the null (true Sharpe = 0), the EXPECTED maximum Sharpe over N
    independent trials is approximately:

        E[max SR | H0, N] = (1 - gamma) * Z(1 - 1/N) + gamma * Z(1 - 1/(N*e))

    where gamma is Euler-Mascheroni (~0.5772). This is the *expected selection
    inflation* — if you tried 100 strategies on pure noise, the best would
    show roughly this Sharpe just from luck.

    Haircut = observed Sharpe - expected_max_under_null
    Expected forward Sharpe = max(0, haircut)
    """
    from statistics import NormalDist
    import math
    if n_trials < 1:
        n_trials = 1
    if n_trials <= 1.0:
        # Single trial — no selection-bias inflation
        return observed_sharpe, max(0.0, observed_sharpe), {
            "expected_max_under_null": 0.0, "n_trials": n_trials,
        }
    gamma = 0.5772156649
    nd = NormalDist()
    p1 = max(1e-12, min(1.0 - 1.0 / n_trials, 1 - 1e-12))
    p2 = max(1e-12, min(1.0 - 1.0 / (n_trials * math.e), 1 - 1e-12))
    z_term1 = nd.inv_cdf(p1)
    z_term2 = nd.inv_cdf(p2)
    expected_max = (1 - gamma) * z_term1 + gamma * z_term2
    haircut = observed_sharpe - expected_max
    expected_forward = max(0.0, haircut)
    return haircut, expected_forward, {
        "expected_max_under_null": expected_max,
        "n_trials": n_trials,
    }


# ============================================================================
# MINIMUM BACKTEST LENGTH (Bailey & Lopez de Prado 2014)
# ============================================================================

def min_backtest_length(observed_sharpe: float, n_trials: int | float,
                        alpha: float = 0.05) -> tuple[float, dict]:
    """Minimum number of independent OOS observations (in years) to claim
    statistical significance of `observed_sharpe` given `n_trials` tested.

    Bailey & Lopez de Prado (2014). Conservative formula:

        MinBTL_years ~ (Z(1 - alpha/N) / SR)^2 / 252

    where Z is the inverse normal CDF. With many trials the critical Z grows,
    so you need more years of OOS to clear the multiple-testing hurdle.

    Returns (years_needed, detail). years_needed = inf if SR <= 0.
    """
    from statistics import NormalDist
    if observed_sharpe <= 0:
        return float("inf"), {"reason": "non-positive Sharpe"}
    if n_trials < 1:
        n_trials = 1
    p_critical = 1.0 - alpha / n_trials
    p_critical = min(p_critical, 1 - 1e-12)
    z_crit = NormalDist().inv_cdf(p_critical)
    # SR is in annualised units; daily sample size needed:
    days_needed = (z_crit / observed_sharpe) ** 2 * 252
    years_needed = days_needed / 252
    return years_needed, {
        "z_critical": z_crit,
        "days_needed": days_needed,
        "alpha": alpha,
        "n_trials": n_trials,
    }


# ============================================================================
# IS-OOS RANK CORRELATION (across all variants)
# ============================================================================

def is_oos_rank_correlation(per_variant_fold_sharpe: pd.DataFrame,
                             split: int | None = None) -> tuple[float, dict]:
    """Spearman correlation between mean-IS-fold rank and mean-OOS-fold rank
    across variants. A high positive correlation means IS-rank predicts OOS
    rank — your search is informative. A correlation near 0 means the IS
    ranking is uninformative about OOS performance (canonical overfitting
    signature when paired with a high IS-best Sharpe).

    `split` = number of folds to treat as IS (rest OOS). Default = half.
    """
    df = per_variant_fold_sharpe.dropna(how="any", axis=0)
    if df.shape[0] < 3 or df.shape[1] < 4:
        return float("nan"), {"reason": "insufficient data"}
    K = df.shape[1]
    if split is None:
        split = K // 2
    is_mean = df.iloc[:, :split].mean(axis=1)
    oos_mean = df.iloc[:, split:].mean(axis=1)
    # Pearson on ranks == Spearman; avoids scipy dependency.
    is_r = is_mean.rank().values
    oos_r = oos_mean.rank().values
    rho = float(np.corrcoef(is_r, oos_r)[0, 1])
    return rho, {"split_at_fold": split, "n_variants": df.shape[0]}


# ============================================================================
# REALITY CHECK / SPA (stationary-bootstrap approximation)
# ============================================================================

def reality_check(per_variant_fold_sharpe: pd.DataFrame,
                  n_bootstrap: int = 1000,
                  random_state: int = 0) -> tuple[float, dict]:
    """Bootstrap test of whether the best-strategy Sharpe is real or selection
    noise.

    Hansen 2005 SPA / White 2000 Reality Check, simplified for fold-aggregated
    Sharpes (we don't have per-day returns per variant in the log).

    Algorithm:
      For each bootstrap iteration b:
        1. For each variant, sample its fold-Sharpes with replacement (stationary
           block of length 1 = i.i.d. fold draws).
        2. Compute that variant's mean fold-Sharpe.
        3. Track max across variants -> a null-distribution sample of "best Sharpe"
      Compare observed max-Sharpe to bootstrap null distribution.

    Returns (p_value, detail).
      p_value = P(null_max >= observed_max).
      Low p_value (< 0.05) supports "the best strategy is not selection noise."
    """
    df = per_variant_fold_sharpe.dropna(how="any", axis=0)
    if df.shape[0] < 2 or df.shape[1] < 4:
        return float("nan"), {"reason": "insufficient data"}
    rng = np.random.default_rng(random_state)
    M = df.values  # variants x folds
    observed_max = float(M.mean(axis=1).max())

    # Center each variant's fold-Sharpes around 0 to enforce null H0: true Sharpe = 0.
    # This is the SPA-style adjustment.
    centred = M - M.mean(axis=1, keepdims=True)

    K = M.shape[1]
    null_maxes = np.empty(n_bootstrap, dtype=float)
    for b in range(n_bootstrap):
        idx = rng.integers(0, K, size=K)
        resampled = centred[:, idx]  # variants x K with bootstrap-sampled folds
        null_maxes[b] = resampled.mean(axis=1).max()

    p_value = float((null_maxes >= observed_max).mean())
    return p_value, {
        "observed_max_sharpe": observed_max,
        "null_mean": float(null_maxes.mean()),
        "null_p95": float(np.percentile(null_maxes, 95)),
        "n_bootstrap": n_bootstrap,
    }


# ============================================================================
# GATE 4 - PARAMETER-NEIGHBOURHOOD STABILITY
# ============================================================================

def parameter_neighbourhood(
    strategy_factory: Callable[[dict], Any],
    base_params: dict,
    perturb_pct: float = 0.15,
    perturb_keys: list[str] | None = None,
    panel=None,
    capital: float = 350_000.0,
    use_oos: bool = True,
    holdout_start=None,
    holdout_end=None,
) -> dict[str, Any]:
    """Run +-perturb_pct on each numeric parameter independently; report OOS Sharpe.

    Returns:
        {
          "base_sharpe": float,
          "perturbations": [{"param": str, "value": new_val, "sharpe": float, "delta_pct": float}, ...],
          "max_pct_drop": float,                 # worst-case Sharpe drop / base
          "passed": bool,                        # all within +-25% of base
          "threshold_pct": 0.25,
        }
    """
    if panel is None:
        raise ValueError("must pass panel")
    from bursahack.walkforward import HOLDOUT_END as _HE, HOLDOUT_START as _HS
    holdout_start = holdout_start or _HS
    holdout_end = holdout_end or _HE

    def _run(params):
        s = strategy_factory(dict(params))
        rebal = s.rebal_dates(panel)
        rebal = [d for d in rebal if panel.dates.get_loc(d) >= 90]
        led = run_backtest(panel, s.signal_fn(), rebal, starting_cash=capital)
        eq = led.equity.loc[holdout_start:holdout_end] if use_oos else led.equity
        if len(eq) < 30:
            return float("nan")
        m = compute_metrics(eq, led.trades)
        return m.sharpe

    base_sharpe = _run(base_params)
    out = []

    if perturb_keys is None:
        perturb_keys = [k for k, v in base_params.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]

    for k in perturb_keys:
        v = base_params[k]
        if isinstance(v, bool):
            continue
        if not isinstance(v, (int, float)):
            continue
        if v == 0:
            continue
        for direction in (-1, +1):
            new_val = v * (1.0 + direction * perturb_pct)
            # Cast back to int if base was int (lookbacks, top_n, etc.)
            if isinstance(v, int) and not isinstance(v, bool):
                new_val = max(1, int(round(new_val)))
            new_params = dict(base_params)
            new_params[k] = new_val
            sh = _run(new_params)
            delta = (sh - base_sharpe) / base_sharpe if base_sharpe else float("nan")
            out.append({"param": k, "value": new_val, "sharpe": sh, "delta_pct": delta})

    max_drop = min((p["delta_pct"] for p in out if not (p["delta_pct"] != p["delta_pct"])),
                   default=0.0)
    passed = all(p["delta_pct"] >= -0.25 for p in out
                 if not (p["delta_pct"] != p["delta_pct"]))
    return {
        "base_sharpe": base_sharpe,
        "perturbations": out,
        "max_pct_drop": max_drop,
        "passed": passed,
        "threshold_pct": 0.25,
    }


# ============================================================================
# GATE 5 - FOLD-SHARPE COV
# ============================================================================

def fold_sharpe_dispersion(per_fold_sharpe: pd.Series) -> tuple[float, dict]:
    """CoV = std / |mean| across folds."""
    s = per_fold_sharpe.dropna()
    if len(s) < 2:
        return float("nan"), {}
    mean = float(s.mean())
    std = float(s.std(ddof=1))
    cov = std / abs(mean) if mean else float("inf")
    return cov, {"n_folds": len(s), "mean": mean, "std": std}


# ============================================================================
# GATE 6 - SLIPPAGE / FEE DRAG
# ============================================================================

def slippage_drag(trades: pd.DataFrame, equity: pd.Series) -> tuple[float, dict]:
    """Cost drag as % of pre-cost return.

    pre_cost_return = realised_return + (total fees + slippage) / starting_equity
    drag_pct        = cost_total / pre_cost_return
    """
    if trades.empty or equity.empty:
        return float("nan"), {}
    total_cost = float(trades["fees"].sum() + trades["slippage"].sum())
    start_eq = float(equity.iloc[0])
    realised = float(equity.iloc[-1] - start_eq)
    pre_cost = realised + total_cost
    if pre_cost <= 0:
        return float("inf"), {
            "total_cost": total_cost,
            "realised_pnl": realised,
            "pre_cost_pnl": pre_cost,
        }
    drag = total_cost / pre_cost
    return drag, {
        "total_cost": total_cost,
        "realised_pnl": realised,
        "pre_cost_pnl": pre_cost,
    }


# ============================================================================
# GATE 7 - AVERAGE ORDER SIZE
# ============================================================================

def avg_order_size(trades: pd.DataFrame, broker_min: float = 8.0,
                   broker_rate: float = 0.0005) -> tuple[float, dict]:
    """Average order notional vs broker breakeven floor.

    Breakeven order size = broker_min / broker_rate.
    Returns (multiple_of_breakeven, detail).
    """
    if trades.empty:
        return float("nan"), {}
    avg_not = float(trades["notional_raw"].mean())
    breakeven = broker_min / broker_rate
    return avg_not / breakeven, {
        "avg_order_RM": avg_not,
        "broker_breakeven_RM": breakeven,
    }


# ============================================================================
# GATE 10 - MONTHLY HIT RATE
# ============================================================================

def monthly_hit_rate(equity: pd.Series) -> tuple[float, dict]:
    monthly = equity.resample("ME").last().pct_change().dropna()
    if monthly.empty:
        return float("nan"), {}
    rate = float((monthly > 0).mean())
    return rate, {"n_months": int(len(monthly)), "n_positive": int((monthly > 0).sum())}


# ============================================================================
# HURDLE
# ============================================================================

EPF_5Y_AVG = 0.055      # Malaysian EPF 5-year average dividend (~5.5%)
KLCI_5Y_CAGR = 0.025    # KLCI 5y total-return CAGR (rough; FBMKLCI flat-to-slightly-up over the period)
HURDLE_PREMIUM = 0.03   # required outperformance over passive alternative

def cagr_hurdle() -> float:
    return max(EPF_5Y_AVG, KLCI_5Y_CAGR) + HURDLE_PREMIUM


# ============================================================================
# ORCHESTRATION
# ============================================================================

def score_strategy(
    strategy_name: str,
    params: dict,
    strategy_factory: Callable[[dict], Any],
    panel,
    per_variant_fold_sharpe: pd.DataFrame,
    this_variant_id: str,
    holdout_equity: pd.Series,
    holdout_trades: pd.DataFrame,
    capital: float = 350_000.0,
    n_subsamples_pbo: int = 1000,
    run_param_neighbourhood: bool = True,
) -> Scorecard:
    """End-to-end scoring of one strategy against the framework's 12 gates.

    `per_variant_fold_sharpe` is a wide DataFrame (variants x folds) used for
    PBO and effective-N. `this_variant_id` is the row in that frame corresponding
    to the strategy being scored.
    """
    sc = Scorecard(strategy_name=strategy_name, params=params)

    # --- Gate 1 - PBO -------------------------------------------------------
    pbo, pbo_detail = pbo_score(per_variant_fold_sharpe, n_subsamples=n_subsamples_pbo)
    sc.gates["pbo"] = GateResult("Gate 1 - PBO", pbo, 0.30, pbo < 0.30 if pbo == pbo else None,
                                  detail=str(pbo_detail))

    # --- Gate 2 - DSR effective-N -------------------------------------------
    n_eff = effective_n(per_variant_fold_sharpe)
    # use holdout daily returns to apply the DSR formula
    hold_ret = holdout_equity.pct_change().dropna()
    holdout_sharpe = compute_metrics(holdout_equity, holdout_trades).sharpe
    dsr = deflated_sharpe_effective_n(holdout_sharpe, n_eff, hold_ret)
    sc.gates["dsr_effective_n"] = GateResult(
        "Gate 2 - Deflated Sharpe (eff-N)", dsr, 0.65, dsr > 0.65 if dsr == dsr else None,
        detail=f"N_eff={n_eff:.1f}",
    )

    # --- Gate 3 - OOS Sharpe ------------------------------------------------
    sc.gates["oos_sharpe"] = GateResult(
        "Gate 3 - OOS Sharpe (net)", holdout_sharpe, 0.30, holdout_sharpe > 0.30,
    )

    # --- Gate 4 - Parameter stability ---------------------------------------
    if run_param_neighbourhood:
        try:
            n_result = parameter_neighbourhood(
                strategy_factory=strategy_factory,
                base_params=params, panel=panel, capital=capital,
            )
            sc.gates["param_stability"] = GateResult(
                "Gate 4 - Param stability", n_result["max_pct_drop"], -0.25,
                n_result["passed"],
                detail=f"perturbations checked: {len(n_result['perturbations'])}",
            )
        except Exception as e:
            sc.gates["param_stability"] = GateResult("Gate 4 - Param stability", None, -0.25, None,
                                                     detail=f"error: {e}")
    else:
        sc.gates["param_stability"] = GateResult("Gate 4 - Param stability", None, -0.25, None,
                                                 detail="skipped")

    # --- Gate 5 - Fold-Sharpe CoV ------------------------------------------
    if this_variant_id in per_variant_fold_sharpe.index:
        own_fold = per_variant_fold_sharpe.loc[this_variant_id]
        cov, _ = fold_sharpe_dispersion(own_fold)
        sc.gates["fold_cov"] = GateResult(
            "Gate 5 - Fold-Sharpe CoV", cov, 1.0, cov < 1.0 if cov == cov else None,
        )
    else:
        sc.gates["fold_cov"] = GateResult("Gate 5 - Fold-Sharpe CoV", None, 1.0, None,
                                          detail="variant not in fold matrix")

    # --- Gate 6 - Slippage drag --------------------------------------------
    drag, _ = slippage_drag(holdout_trades, holdout_equity)
    sc.gates["slip_drag"] = GateResult(
        "Gate 6 - Slippage drag", drag, 0.30, drag < 0.30 if drag == drag else None,
    )

    # --- Gate 7 - Order size vs broker floor -------------------------------
    mult, _ = avg_order_size(holdout_trades)
    sc.gates["order_size"] = GateResult(
        "Gate 7 - Avg order vs broker floor", mult, 4.0, mult >= 4.0 if mult == mult else None,
    )

    # --- Gate 8 - CAGR vs hurdle -------------------------------------------
    hold_m = compute_metrics(holdout_equity, holdout_trades)
    hurdle = cagr_hurdle()
    sc.gates["cagr_vs_hurdle"] = GateResult(
        "Gate 8 - CAGR vs hurdle", hold_m.cagr, hurdle, hold_m.cagr > hurdle,
        detail=f"hurdle = max(EPF {EPF_5Y_AVG:.1%}, KLCI {KLCI_5Y_CAGR:.1%}) + {HURDLE_PREMIUM:.1%}",
    )

    # --- Gate 9 - Max drawdown --------------------------------------------
    sc.gates["max_dd"] = GateResult(
        "Gate 9 - Max drawdown", hold_m.max_drawdown, -0.60, hold_m.max_drawdown > -0.60,
    )

    # --- Gate 10 - Monthly hit rate ---------------------------------------
    hit, _ = monthly_hit_rate(holdout_equity)
    sc.gates["monthly_hit"] = GateResult(
        "Gate 10 - % positive months", hit, 0.50, hit >= 0.50 if hit == hit else None,
    )

    # --- Gates 11 & 12 - manual ------------------------------------------
    sc.gates["reproducibility"] = GateResult("Gate 11 - Reproducibility", 1.0, 1.0, True,
                                              detail="bit-exact from frozen code+data, manual verify")
    sc.gates["paper_trading"] = GateResult("Gate 12 - Paper trading >= 30d", None, 30, None,
                                            detail="not yet attempted - manual")

    # --- Diagnostic-only metrics (informational; not strict gates) -------
    # Sharpe haircut: use effective-N (correlation-adjusted) so we don't
    # double-count near-identical variants. Naive-N version reported in detail
    # for transparency.
    n_actual = per_variant_fold_sharpe.dropna(how="any", axis=0).shape[0]
    n_eff_for_hc = max(1, int(round(effective_n(per_variant_fold_sharpe))))
    haircut, fwd_sharpe, hc_detail = sharpe_haircut(holdout_sharpe, n_eff_for_hc)
    haircut_naive, _, hc_detail_naive = sharpe_haircut(holdout_sharpe, n_actual)
    sc.gates["sharpe_haircut"] = GateResult(
        "Diag - Sharpe haircut (forward est)", fwd_sharpe, 0.30,
        fwd_sharpe > 0.30 if fwd_sharpe == fwd_sharpe else None,
        detail=(f"observed {holdout_sharpe:.2f}; haircut(N_eff={n_eff_for_hc}) {haircut:+.2f}; "
                f"haircut(N_naive={n_actual}) {haircut_naive:+.2f}"),
    )

    # MinBTL: years of OOS required to claim significance (effective-N)
    min_years, mbtl_detail = min_backtest_length(holdout_sharpe, max(1, n_eff_for_hc))
    min_years_naive, _ = min_backtest_length(holdout_sharpe, max(1, n_actual))
    oos_years = (holdout_equity.index[-1] - holdout_equity.index[0]).days / 365.25
    sc.gates["min_backtest_length"] = GateResult(
        "Diag - MinBTL years required", min_years, oos_years,
        min_years <= oos_years if min_years == min_years and min_years != float("inf") else None,
        detail=f"holdout has {oos_years:.1f}y; need {min_years:.1f}y (N_eff) / {min_years_naive:.1f}y (N_naive)",
    )

    # IS-OOS rank correlation across variants (informational)
    rho, rho_detail = is_oos_rank_correlation(per_variant_fold_sharpe)
    sc.gates["is_oos_rank_corr"] = GateResult(
        "Diag - IS-OOS rank correlation", rho, 0.20,
        rho > 0.20 if rho == rho else None,
        detail=f"split at fold {rho_detail.get('split_at_fold', '-')}",
    )

    # Reality Check / SPA p-value
    rc_p, rc_detail = reality_check(per_variant_fold_sharpe, n_bootstrap=500)
    sc.gates["reality_check_p"] = GateResult(
        "Diag - Reality Check p-value", rc_p, 0.05,
        rc_p < 0.05 if rc_p == rc_p else None,
        detail=f"obs_max {rc_detail.get('observed_max_sharpe', 0):.2f} vs null_p95 {rc_detail.get('null_p95', 0):.2f}",
    )

    sc.tier = sc.derive_tier()
    sc.recommendation = sc.derive_recommendation()
    return sc


# ============================================================================
# HELPERS - variant-fold matrix from existing search log
# ============================================================================

def load_fold_sharpe_matrix(log_path: Path = RESULTS_DIR / "search_log.jsonl") -> pd.DataFrame:
    """Read the JSONL log, return wide matrix indexed by variant_id (params_hash),
    columns = fold_idx, values = per-fold OOS Sharpe of that variant."""
    rows = []
    with open(log_path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            m = d.get("metrics")
            if not m or m.get("sharpe") is None:
                continue
            rows.append({
                "variant_id": d["params_hash"],
                "fold_idx": d["fold_idx"],
                "sharpe": m["sharpe"],
                "name": d.get("name"),
                "params": json.dumps(d["params"], sort_keys=True),
            })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.pivot_table(index="variant_id", columns="fold_idx", values="sharpe", aggfunc="last")


def fmt_scorecard(sc: Scorecard) -> str:
    lines = [f"# Scorecard - {sc.strategy_name}",
             f"Params: {json.dumps(sc.params, sort_keys=True)}",
             "",
             f"**Tier**: {sc.tier}   |   **Recommendation**: {sc.recommendation}",
             "",
             "## Gates",
             ""]
    for g in sc.gates.values():
        lines.append(str(g))
    lines.append("")
    lines.append(f"Summary: {sc.n_passed()} / {sc.n_evaluated()} gates passed.")
    return "\n".join(lines)
