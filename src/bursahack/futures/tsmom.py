"""Multi-speed, vol-targeted time-series-momentum backtest (return space).

Faithful to Hurst-Ooi-Pedersen "A Century of Evidence": combine multiple
trend speeds, scale each market to a constant ex-ante volatility, then set
the overall book to a target portfolio volatility. Working in vol-scaled
RETURN space (not contract-count space) makes the result independent of
point values and of the roll-adjustment that free data lacks — exactly the
right abstraction for a first-pass "is the edge there?" backtest.

No-lookahead discipline (the only thing that matters for honesty):
  - signal_t and vol_t are computed from data up to and INCLUDING close t
    (both observable at the close of day t).
  - the position decided at close t earns day t+1's return. Implemented by
    LAGGING the weight one day: sleeve_ret_t = w_{t-1} * ret_t.
  - portfolio vol-scaling uses a TRAILING (lagged) portfolio-vol estimate.

Cost: turnover (|Δ weight|) × per-market round-trip bps. Trend turnover is
low (positions held weeks), so cost drag is small but non-zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(frozen=True)
class TSMOMConfig:
    speeds: tuple[int, ...] = (21, 63, 252)   # ~1, 3, 12 months
    signal_mode: str = "sign"                  # "sign" or "continuous" (tanh)
    sleeve_vol_target: float = 0.10            # per-market ex-ante annual vol
    portfolio_vol_target: float = 0.15         # whole-book annual vol (the risk dial)
    vol_window: int = 63                       # trailing realized-vol window (days)
    max_leverage: float = 5.0                  # cap on portfolio vol-scaling
    default_cost_bps: float = 2.0              # round-trip bps if ticker not in map
    ann_factor: int = 252                      # periods/year for annualization
                                               # (252 futures/equities, 365 crypto)


@dataclass
class TSMOMResult:
    portfolio_returns: pd.Series               # daily net portfolio returns
    equity: pd.Series                          # cumulative (starts at 1.0)
    sleeve_returns: pd.DataFrame               # per-market net sleeve returns
    metrics: dict[str, float]
    config: TSMOMConfig = field(default_factory=TSMOMConfig)


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------


def compute_signal(prices: pd.DataFrame, cfg: TSMOMConfig) -> pd.DataFrame:
    """Combined multi-speed trend signal in [-1, 1], observable at close t.

    For each speed L: momentum = price_t / price_{t-L} - 1. The combined
    signal is the average across speeds of either the sign (mode="sign") or
    a tanh-squashed normalized momentum (mode="continuous").
    """
    sigs = []
    for L in cfg.speeds:
        mom = prices / prices.shift(L) - 1.0
        if cfg.signal_mode == "continuous":
            # Normalize momentum by its own trailing vol so speeds are comparable.
            norm = mom / mom.rolling(cfg.vol_window).std()
            sigs.append(np.tanh(norm))
        else:
            sigs.append(np.sign(mom))
    combined = sum(sigs) / len(sigs)
    return combined


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------


def backtest_tsmom(
    prices: pd.DataFrame,
    cost_bps: dict[str, float] | None = None,
    cfg: TSMOMConfig | None = None,
) -> TSMOMResult:
    """Run the multi-speed vol-targeted TSMOM backtest on a price panel.

    Args:
      prices: daily close panel, DatetimeIndex × tickers (columns).
      cost_bps: per-ticker round-trip cost in bps of notional. Missing
        tickers use cfg.default_cost_bps.
      cfg: TSMOMConfig.

    Returns: TSMOMResult.
    """
    cfg = cfg or TSMOMConfig()
    cost_bps = cost_bps or {}
    af = cfg.ann_factor
    prices = prices.sort_index()
    ret = prices.pct_change(fill_method=None)

    # Per-market trailing realized vol (annualized), lagged so the position
    # decided at close t uses only info up to t.
    daily_vol = ret.rolling(cfg.vol_window).std()
    ann_vol = daily_vol * np.sqrt(af)

    signal = compute_signal(prices, cfg)

    # Per-sleeve weight: scale signal so each market targets sleeve_vol_target.
    # weight = signal × (target_vol / market_vol). Decided at close t.
    weight = signal * (cfg.sleeve_vol_target / ann_vol)
    weight = weight.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # Lag the weight: position decided at close t earns t+1 return.
    w_lag = weight.shift(1).fillna(0.0)

    # Gross sleeve returns.
    sleeve_gross = w_lag * ret

    # Turnover cost: |Δ held weight| × round-trip bps.
    turnover = (w_lag - w_lag.shift(1)).abs().fillna(0.0)
    cost_frac = pd.DataFrame(
        {t: cost_bps.get(t, cfg.default_cost_bps) / 10_000.0 for t in prices.columns},
        index=prices.index,
    )
    sleeve_cost = turnover * cost_frac
    sleeve_net = sleeve_gross - sleeve_cost

    # Equal-risk portfolio: mean across markets (each already vol-targeted).
    # Use only markets that have data on each day (skipna).
    raw_port = sleeve_net.mean(axis=1, skipna=True).fillna(0.0)

    # Portfolio-level vol targeting (the risk dial). Trailing, lagged.
    port_daily_vol = raw_port.rolling(cfg.vol_window).std().shift(1)
    target_daily = cfg.portfolio_vol_target / np.sqrt(af)
    leverage = (target_daily / port_daily_vol).clip(upper=cfg.max_leverage)
    leverage = leverage.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    port_ret = (leverage * raw_port).fillna(0.0)

    # Trim the warmup (longest speed + vol window) where signal is undefined.
    warmup = max(cfg.speeds) + cfg.vol_window
    port_ret = port_ret.iloc[warmup:]
    sleeve_net = sleeve_net.iloc[warmup:]

    equity = (1.0 + port_ret).cumprod()
    metrics = compute_metrics(port_ret, equity, ann_factor=af)

    return TSMOMResult(
        portfolio_returns=port_ret,
        equity=equity,
        sleeve_returns=sleeve_net,
        metrics=metrics,
        config=cfg,
    )


def compute_metrics(returns: pd.Series, equity: pd.Series,
                    ann_factor: int = TRADING_DAYS) -> dict[str, float]:
    """CAGR, vol, Sharpe, max-DD, Calmar, skew — the CAGR-investor's panel."""
    r = returns.dropna()
    if len(r) < 2 or equity.empty:
        return {k: 0.0 for k in
                ("cagr", "ann_vol", "sharpe", "max_drawdown", "calmar", "skew", "n_days")}
    n = len(r)
    total_growth = float(equity.iloc[-1])
    years = n / ann_factor
    cagr = total_growth ** (1.0 / years) - 1.0 if total_growth > 0 and years > 0 else -1.0
    ann_vol = float(r.std() * np.sqrt(ann_factor))
    sharpe = float(r.mean() / r.std() * np.sqrt(ann_factor)) if r.std() > 0 else 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_dd = float(drawdown.min())
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else 0.0
    skew = float(r.skew())
    return {
        "cagr": cagr, "ann_vol": ann_vol, "sharpe": sharpe,
        "max_drawdown": max_dd, "calmar": calmar, "skew": skew, "n_days": float(n),
    }


__all__ = ["TSMOMConfig", "TSMOMResult", "backtest_tsmom", "compute_signal",
           "compute_metrics", "TRADING_DAYS"]
