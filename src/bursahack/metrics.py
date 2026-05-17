"""Backtest performance metrics.

Standard set plus the Deflated Sharpe Ratio (Bailey + de Prado 2014) which
adjusts a strategy's observed Sharpe for the multiple-testing penalty of
having tried N strategies. This is the gate for the brute-force search.

Conventions:
  - Daily equity curve (pd.Series indexed by date, in RM).
  - Returns derived from equity_curve.pct_change().
  - Sharpe annualised assuming 252 trading days.
  - Sortino uses negative semi-deviation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(frozen=True)
class Metrics:
    cagr: float
    vol: float
    sharpe: float
    sortino: float
    max_drawdown: float
    calmar: float
    hit_rate: float
    n_obs: int
    n_trades: int
    avg_cost_bps: float
    turnover: float                  # mean(|order_notional|) / mean(equity)
    deflated_sharpe: float | None    # None unless n_trials passed in


def daily_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def cagr(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    n_years = (equity.index[-1] - equity.index[0]).days / 365.25
    if n_years <= 0 or equity.iloc[0] <= 0:
        return 0.0
    return (equity.iloc[-1] / equity.iloc[0]) ** (1.0 / n_years) - 1.0


def annual_vol(ret: pd.Series) -> float:
    return float(ret.std(ddof=1) * math.sqrt(TRADING_DAYS))


def sharpe(ret: pd.Series, rf_annual: float = 0.0) -> float:
    if ret.empty or ret.std(ddof=1) == 0:
        return 0.0
    rf_daily = rf_annual / TRADING_DAYS
    excess = ret - rf_daily
    return float(excess.mean() / ret.std(ddof=1) * math.sqrt(TRADING_DAYS))


def sortino(ret: pd.Series, rf_annual: float = 0.0) -> float:
    if ret.empty:
        return 0.0
    rf_daily = rf_annual / TRADING_DAYS
    excess = ret - rf_daily
    downside = excess[excess < 0]
    if downside.empty or downside.std(ddof=1) == 0:
        return float("inf") if excess.mean() > 0 else 0.0
    return float(excess.mean() / downside.std(ddof=1) * math.sqrt(TRADING_DAYS))


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(dd.min())


def hit_rate(ret: pd.Series) -> float:
    if ret.empty:
        return 0.0
    return float((ret > 0).mean())


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trials: int,
    returns: pd.Series,
    benchmark_sharpe: float = 0.0,
) -> float:
    """Bailey + de Prado (2014) Deflated Sharpe Ratio.

    Returns the probability that the true Sharpe exceeds `benchmark_sharpe`
    given that `n_trials` strategies were tested and `observed_sharpe` was
    the best (or any sampled). 1.0 = very strong; 0.5 = no evidence; < 0.5 = worse than noise.

    Uses the higher moments of the daily returns to deflate.
    """
    from math import erf, log, sqrt

    if returns.empty or n_trials < 1:
        return 0.0
    T = len(returns)
    if T < 30:
        return 0.0

    skew = float(returns.skew())
    # Excess kurtosis (Pearson kurtosis - 3)
    kurt = float(returns.kurt())

    # Expected max Sharpe under null (Bailey + de Prado 2014, eq 13):
    # E[SR*] = sqrt(2 ln N) * (1 - gamma) + gamma * sqrt(2/T)
    # But the simpler / more conservative version uses Z(1 - 1/N) approximations.
    # We use the standard formula:
    gamma = 0.5772156649  # Euler-Mascheroni
    z_n = (1.0 - gamma) * _z_score(1.0 - 1.0 / n_trials) + gamma * _z_score(1.0 - 1.0 / (n_trials * math.e))
    expected_max_sr = z_n / math.sqrt(TRADING_DAYS)
    # Convert observed daily-Sharpe units for the deflation step
    sr_daily = observed_sharpe / math.sqrt(TRADING_DAYS)
    bench_daily = max(benchmark_sharpe, expected_max_sr) / math.sqrt(TRADING_DAYS)

    denom = math.sqrt(1.0 - skew * sr_daily + ((kurt) / 4.0) * sr_daily ** 2)
    if denom <= 0:
        return 0.0
    numerator = (sr_daily - bench_daily) * math.sqrt(T - 1)
    dsr_z = numerator / denom
    return _phi(dsr_z)


def _z_score(p: float) -> float:
    """Inverse normal CDF (approx) -- enough accuracy for DSR."""
    # Beasley-Springer-Moro is overkill; use scipy if available, else fallback.
    try:
        from statistics import NormalDist
        return NormalDist().inv_cdf(min(max(p, 1e-12), 1 - 1e-12))
    except Exception:
        # crude fallback
        return 5.0 if p > 0.999999 else -5.0 if p < 1e-6 else 0.0


def _phi(z: float) -> float:
    """Standard-normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def compute_metrics(
    equity: pd.Series,
    trades: pd.DataFrame | None = None,
    n_trials: int | None = None,
) -> Metrics:
    """Roll up every standard metric for an equity curve."""
    ret = daily_returns(equity)
    cgr = cagr(equity)
    vol = annual_vol(ret)
    sr = sharpe(ret)
    sortino_v = sortino(ret)
    mdd = max_drawdown(equity)
    calmar = (cgr / abs(mdd)) if mdd < 0 else float("inf") if cgr > 0 else 0.0
    hr = hit_rate(ret)

    if trades is not None and not trades.empty:
        n_trades = int(len(trades))
        avg_cost_bps = float(trades["cost_bps"].mean()) if "cost_bps" in trades.columns else 0.0
        gross_notional = trades["notional_raw"].sum() if "notional_raw" in trades.columns else 0.0
        n_years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-6)
        # one-way annualised turnover as a fraction of mean equity
        turnover_v = (gross_notional / 2.0) / n_years / equity.mean() if equity.mean() > 0 else 0.0
    else:
        n_trades = 0
        avg_cost_bps = 0.0
        turnover_v = 0.0

    dsr = (
        deflated_sharpe_ratio(sr, n_trials, ret) if n_trials and n_trials >= 1 else None
    )

    return Metrics(
        cagr=cgr,
        vol=vol,
        sharpe=sr,
        sortino=sortino_v,
        max_drawdown=mdd,
        calmar=calmar,
        hit_rate=hr,
        n_obs=int(len(ret)),
        n_trades=n_trades,
        avg_cost_bps=avg_cost_bps,
        turnover=turnover_v,
        deflated_sharpe=dsr,
    )


def fmt_metrics(m: Metrics) -> str:
    lines = [
        f"  CAGR             : {m.cagr * 100:>8.2f} %",
        f"  Vol (ann)        : {m.vol * 100:>8.2f} %",
        f"  Sharpe           : {m.sharpe:>8.2f}",
        f"  Sortino          : {m.sortino:>8.2f}",
        f"  Max DD           : {m.max_drawdown * 100:>8.2f} %",
        f"  Calmar           : {m.calmar:>8.2f}",
        f"  Hit rate         : {m.hit_rate * 100:>8.2f} %",
        f"  Days observed    : {m.n_obs:>8}",
        f"  Trades           : {m.n_trades:>8}",
        f"  Avg cost / leg   : {m.avg_cost_bps:>8.1f} bps",
        f"  Turnover (annual): {m.turnover:>8.2f} x",
    ]
    if m.deflated_sharpe is not None:
        lines.append(f"  Deflated Sharpe  : {m.deflated_sharpe:>8.3f}  (prob > benchmark)")
    return "\n".join(lines)
