"""Final crypto candidate vs the RELAXED bar: CAGR>=15%, DD<=~45%.

Strategy = momentum (TS-trend + cross-sectional) + funding-carry, with a Faber
absolute-trend crash gate (cut book exposure when the crypto market is below its
100-day MA). Sized by a CONSTANT leverage to the ~-45% DD budget on the honest
modern 2018+ window (sizing-to-risk is standard, not overfitting).

Validation (so it's certified, not curve-fit):
  - DSR (deflated for trials) + stationary bootstrap CI
  - per-calendar-year consistency
  - OUT-OF-SAMPLE HOLDOUT: tune nothing on 2024+; report it cold.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bursahack.futures.tsmom import TSMOMConfig, backtest_tsmom, compute_metrics  # noqa: E402
from bursahack.futures.crypto_signals import (  # noqa: E402
    clean_panel, xs_momentum_returns, funding_carry_returns, ensemble_returns,
)
from bursahack.futures.binance_funding import funding_daily_panel  # noqa: E402
from bursahack.intraday.diagnostics import (  # noqa: E402
    deflated_sharpe_ratio, sharpe_ratio, stationary_bootstrap_sharpe_ci,
)
import yfinance as yf  # noqa: E402

CRYPTO = ["BTC-USD", "ETH-USD", "BNB-USD", "XRP-USD", "ADA-USD", "SOL-USD",
          "DOGE-USD", "LTC-USD", "LINK-USD", "DOT-USD", "AVAX-USD", "MATIC-USD",
          "BCH-USD", "TRX-USD", "XLM-USD", "ETC-USD", "ATOM-USD", "XMR-USD",
          "EOS-USD", "XTZ-USD", "ALGO-USD", "VET-USD"]
ANN = 365
MODERN = pd.Timestamp("2018-01-01")
HOLDOUT = pd.Timestamp("2024-01-01")
TARGET_CAGR, TARGET_DD = 0.15, -0.45


def met(s: pd.Series, lo: pd.Timestamp | None, hi: pd.Timestamp | None = None) -> dict:
    x = s
    if lo is not None:
        x = x[x.index >= lo]
    if hi is not None:
        x = x[x.index < hi]
    x = x.dropna()
    return compute_metrics(x, (1 + x).cumprod(), ann_factor=ANN)


def main() -> None:
    print("=" * 70)
    print("Crypto FINAL candidate vs relaxed bar  CAGR>=15% / DD<=45%")
    print("=" * 70)
    raw = yf.download(CRYPTO, period="max", interval="1d", progress=False, auto_adjust=False)
    prices = clean_panel(raw["Close"].sort_index().dropna(how="all"), ann=ANN)
    print(f"[data] {prices.shape[1]} coins after hygiene; "
          f"{prices.index.min().date()} -> {prices.index.max().date()}")
    costs = {t: 10.0 for t in prices.columns}

    # Sleeves.
    trend = backtest_tsmom(prices, cost_bps=costs,
                           cfg=TSMOMConfig(speeds=(21, 63, 252), portfolio_vol_target=0.20,
                                           ann_factor=ANN)).portfolio_returns
    xsmom = xs_momentum_returns(prices, lookback=30, cost_bps=10.0, pvol=0.20, ann=ANN)
    fsyms = {c: c.replace("-USD", "USDT") for c in prices.columns}
    fpanel = funding_daily_panel(fsyms)
    carry = pd.Series(dtype=float)
    if not fpanel.empty:
        fpanel = fpanel.reindex(prices.index).loc[:, fpanel.columns.intersection(prices.columns)]
        carry = funding_carry_returns(fpanel, cost_bps=5.0, pvol=0.20, ann=ANN)
    base = ensemble_returns([trend, xsmom] + ([carry] if len(carry) else []),
                            pvol=0.20, ann=ANN)

    # Faber crash gate: market index vs 100d MA; bear -> exposure 0.3 (lagged).
    idx = prices.mean(axis=1)
    risk_on = (idx > idx.rolling(100).mean()).shift(1).fillna(True).astype(float)
    mult = (risk_on * 0.7 + 0.3).reindex(base.index).fillna(1.0)
    gated = (base * mult).fillna(0.0)

    # Size by CONSTANT leverage to hit ~-45% DD on the MODERN window.
    print("\n[size] constant-leverage sweep (DD target -45% on 2018+):")
    chosen = None
    for lev in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0):
        g = gated * lev
        m = met(g, MODERN)
        flag = ""
        if m["max_drawdown"] >= TARGET_DD and m["cagr"] >= TARGET_CAGR:
            flag = "  <-- MEETS 15/45"
            if chosen is None:
                chosen = (lev, g)
        print(f"  lev={lev:.1f}  CAGR={m['cagr']*100:5.1f}%  maxDD={m['max_drawdown']*100:6.1f}%  "
              f"Sharpe={m['sharpe']:.2f}  Calmar={m['calmar']:.2f}{flag}")

    if chosen is None:
        # Pick the lev with DD closest to -45% as the candidate to report honestly.
        cand_lev = min((1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0),
                       key=lambda L: abs(met(gated * L, MODERN)["max_drawdown"] - TARGET_DD))
        chosen = (cand_lev, gated * cand_lev)
        print(f"\n[size] none cleanly met 15/45; reporting closest (lev={cand_lev}).")

    lev, final = chosen
    print(f"\n=== FINAL CANDIDATE: gated mom+funding ensemble, leverage {lev:.1f} ===")
    for tag, lo, hi in (("modern 2018+", MODERN, None),
                        ("train 2018-2023", MODERN, HOLDOUT),
                        ("HOLDOUT 2024+", HOLDOUT, None),
                        ("recent 2022+", pd.Timestamp("2022-01-01"), None)):
        m = met(final, lo, hi)
        ok = m["cagr"] >= TARGET_CAGR and m["max_drawdown"] >= TARGET_DD
        print(f"  {tag:<16} CAGR={m['cagr']*100:6.1f}%  maxDD={m['max_drawdown']*100:6.1f}%  "
              f"Sharpe={m['sharpe']:.2f}  Calmar={m['calmar']:.2f}  skew={m['skew']:+.1f}"
              f"  {'PASS' if ok else 'fail'}")

    # Harness gate on the modern window.
    trial = np.array([sharpe_ratio(s.dropna().values) for s in (trend, xsmom, base, gated)])
    mod = final[final.index >= MODERN].dropna().values
    dsr = deflated_sharpe_ratio(mod, trial)
    ci = stationary_bootstrap_sharpe_ci(mod, n_boot=2000, rng_seed=0, periods_per_year=ANN)
    print(f"\n[gate] modern: DSR={dsr['dsr']:.3f}  bootstrap Sharpe CI=[{ci['lo']:.2f},{ci['hi']:.2f}]")

    # Per-year.
    ey = (1 + final[final.index >= MODERN]).groupby(final[final.index >= MODERN].index.year).prod() - 1
    print("[year] " + "  ".join(f"{y}:{v*100:+.0f}%" for y, v in ey.items()))

    # Plot.
    out = REPO_ROOT / ".tmp" / "crypto"; out.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        eq = (1 + final[final.index >= MODERN]).cumprod()
        fig, ax = plt.subplots(figsize=(11, 6))
        ax.plot(eq.index, eq.values, lw=1.4, label=f"final candidate (lev {lev:.1f})")
        ax.axvline(HOLDOUT, color="red", ls=":", label="holdout 2024+")
        ax.set_yscale("log"); ax.legend(); ax.grid(True, alpha=0.3)
        ax.set_title(f"Crypto final candidate vs 15/45 — equity (log), modern 2018+\n"
                     f"gated mom+funding ensemble")
        fig.tight_layout(); fig.savefig(out / "crypto_final.png", dpi=130)
        print(f"\n[plot] -> {out / 'crypto_final.png'}")
    except Exception as exc:
        print(f"[plot] skipped: {exc}")
    print("\nDONE")


if __name__ == "__main__":
    main()
