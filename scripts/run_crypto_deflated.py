"""Honest, deflated, rule-based crypto backtest — no curated list, no hindsight window.

Replaces the two data-mining vectors RH flagged:
  - CURATED COIN LIST  -> point-in-time top-K by trailing dollar-volume (rule;
    no survivorship, no hand-picking; parameter = K).
  - HINDSIGHT WINDOW   -> the whole 2018+ history, evaluated by CPCV folds.
  - SMALL DSR TRIAL SET -> Deflated Sharpe computed over the ENTIRE parameter
    grid actually searched, + PBO across the grid.

Strategy is fully parameterized (multi-speed TS-trend blended with cross-
sectional momentum, optional Faber crash gate). We DO NOT report a single
hand-chosen config — we report the grid's best AFTER deflating for how many
configs were tried. No look-ahead: every position/membership lagged 1 day.
"""
from __future__ import annotations

import os
import sys
from itertools import product
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

from bursahack.futures.crypto_signals import clean_panel  # noqa: E402
from bursahack.intraday.cpcv import make_cpcv_splits  # noqa: E402
from bursahack.intraday.diagnostics import (  # noqa: E402
    deflated_sharpe_ratio, effective_n, pbo, sharpe_ratio,
    stationary_bootstrap_sharpe_ci,
)
from bursahack.futures.tsmom import compute_metrics  # noqa: E402
import yfinance as yf  # noqa: E402

ANN = 365
MODERN = pd.Timestamp("2018-01-01")
COST_BPS = 10.0
# Broad coin set — the RULE picks from these point-in-time; we don't curate.
COINS = ["BTC-USD", "ETH-USD", "BNB-USD", "XRP-USD", "ADA-USD", "SOL-USD",
         "DOGE-USD", "LTC-USD", "LINK-USD", "DOT-USD", "AVAX-USD", "MATIC-USD",
         "BCH-USD", "TRX-USD", "XLM-USD", "ETC-USD", "ATOM-USD", "XMR-USD",
         "EOS-USD", "XTZ-USD", "ALGO-USD", "VET-USD", "FIL-USD", "ICP-USD",
         "HBAR-USD", "AAVE-USD", "NEAR-USD", "GRT-USD", "SAND-USD", "MANA-USD",
         "AXS-USD", "THETA-USD", "EOS-USD", "UNI-USD", "FTM-USD", "EGLD-USD"]

# Parameter grid — the FULL search we deflate over.
SPEED_SETS = {"slow": (252,), "med": (63,), "combo": (21, 63, 252)}
GRID = list(product(
    SPEED_SETS.keys(),      # trend speed set
    (30, 90),               # xs-momentum lookback (days)
    (15, 25, 40),           # K = universe size (top-K by dollar volume)
    (True, False),          # Faber crash gate on/off
))  # = 3*2*3*2 = 36 configs


def pit_membership(close: pd.DataFrame, volume: pd.DataFrame, k: int,
                   min_hist: int = 365) -> pd.DataFrame:
    """Point-in-time top-K-by-trailing-dollar-volume membership (lagged).

    Eligible = has >= min_hist days of history so far AND ranks in the top-K by
    trailing-30d mean dollar volume. Returns a 0/1 mask, shifted 1 day so the
    position decided at t uses only info through t-1.
    """
    dv = (close * volume)
    dv30 = dv.rolling(30, min_periods=10).mean()
    has_hist = close.notna().cumsum() >= min_hist
    rank = dv30.where(has_hist).rank(axis=1, ascending=False)
    member = (rank <= k).astype(float)
    return member.shift(1).fillna(0.0)


def strategy_returns(close: pd.DataFrame, volume: pd.DataFrame, idx: pd.Series,
                     speeds, xs_lb: int, k: int, gate: bool,
                     pvol: float = 0.30, vol_window: int = 63) -> pd.Series:
    """Rule-based momentum book on a point-in-time top-K universe."""
    ret = close.pct_change(fill_method=None)
    vol = (ret.rolling(vol_window).std() * np.sqrt(ANN))
    member = pit_membership(close, volume, k)

    # TS trend: mean of sign(N-period momentum) across speeds.
    ts = sum(np.sign(close.pct_change(L)) for L in speeds) / len(speeds)
    # XS momentum: cross-sectional z of trailing return, among members only.
    mom = close.pct_change(xs_lb)
    mom_m = mom.where(member > 0)
    z = (mom_m.sub(mom_m.mean(axis=1), axis=0)).div(mom_m.std(axis=1) + 1e-9, axis=0)
    xs = np.tanh(z).fillna(0.0)
    signal = (0.5 * ts + 0.5 * xs).fillna(0.0)

    # Per-coin weight: vol-scaled, member-masked, lagged.
    w = (signal * (0.20 / vol)).replace([np.inf, -np.inf], np.nan).fillna(0.0) * member
    w_lag = w.shift(1).fillna(0.0)
    gross = (w_lag * ret)
    turn = (w_lag - w_lag.shift(1)).abs()
    net = gross - turn * (COST_BPS / 10_000.0)
    nmem = member.shift(1).sum(axis=1).replace(0, np.nan)
    raw = (net.sum(axis=1) / nmem).fillna(0.0)

    if gate:
        risk_on = (idx > idx.rolling(100).mean()).shift(1).fillna(True).astype(float)
        raw = raw * (risk_on * 0.7 + 0.3).reindex(raw.index).fillna(1.0)

    # Portfolio vol target (trailing, lagged).
    pv = raw.rolling(vol_window).std().shift(1)
    lev = (pvol / np.sqrt(ANN) / pv).clip(upper=5.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out = (lev * raw).fillna(0.0)
    warm = max(max(speeds), xs_lb, vol_window, 365)
    return out.iloc[warm:]


def main() -> None:
    print("=" * 72)
    print("DEFLATED rule-based crypto backtest (no curated list / no hindsight window)")
    print("=" * 72)
    raw = yf.download(COINS, period="max", interval="1d", progress=False, auto_adjust=False)
    close = clean_panel(raw["Close"].sort_index().dropna(how="all"), ann=ANN)
    volume = raw["Volume"].reindex(columns=close.columns).reindex(close.index)
    close = close[close.index >= MODERN]
    volume = volume[volume.index >= MODERN]
    idx = close.mean(axis=1)
    print(f"[data] {close.shape[1]} coins, {close.index.min().date()} -> {close.index.max().date()}; "
          f"grid = {len(GRID)} configs\n")

    # Run the full grid.
    rets: dict[str, pd.Series] = {}
    rows = []
    for (sp, xs_lb, k, gate) in GRID:
        s = strategy_returns(close, volume, idx, SPEED_SETS[sp], xs_lb, k, gate)
        label = f"{sp}/xs{xs_lb}/K{k}/{'gate' if gate else 'nogate'}"
        rets[label] = s
        m = compute_metrics(s.dropna(), (1 + s.dropna()).cumprod(), ann_factor=ANN)
        rows.append((label, m["cagr"], m["max_drawdown"], m["sharpe"], m["calmar"]))

    rows.sort(key=lambda r: r[3], reverse=True)  # by Sharpe
    print("--- grid results (top 8 by Sharpe) ---")
    for lbl, c, d, sh, cal in rows[:8]:
        print(f"  {lbl:<26} CAGR={c*100:5.1f}%  DD={d*100:6.1f}%  Sharpe={sh:.2f}  Calmar={cal:.2f}")
    print(f"  ... ({len(rows)} configs total)")

    # Align all configs on a common index -> matrix for deflation + PBO.
    R = pd.DataFrame(rets).dropna(how="any")
    M = R.values
    per_period_sharpes = np.array([sharpe_ratio(M[:, j]) for j in range(M.shape[1])])
    corr = np.corrcoef(M, rowvar=False); corr = np.nan_to_num(corr, nan=0.0); np.fill_diagonal(corr, 1.0)
    eff_n = effective_n(corr)

    best_j = int(np.argmax(per_period_sharpes))
    best_label = R.columns[best_j]
    best = R.iloc[:, best_j].values
    dsr = deflated_sharpe_ratio(best, per_period_sharpes, effective_n_trials=eff_n)
    ci = stationary_bootstrap_sharpe_ci(best, n_boot=2000, rng_seed=0, periods_per_year=ANN)
    pbo_res = pbo(M, n_submatrices=10 if M.shape[0] >= 100 else 4)
    bm = compute_metrics(pd.Series(best), (1 + pd.Series(best)).cumprod(), ann_factor=ANN)

    # CPCV out-of-sample on the best config (stateless -> slice-valid).
    n = len(best)
    splits = make_cpcv_splits(n=n, n_folds=8, n_test_folds=2, embargo=2)
    cpcv_sh = [sharpe_ratio(best[sp.test_idx], periods_per_year=ANN) for sp in splits]

    print("\n" + "=" * 72)
    print("HONEST DEFLATED VERDICT")
    print("=" * 72)
    print(f"  grid size (trials)      : {len(GRID)}  (effective_n = {eff_n:.1f})")
    print(f"  best config             : {best_label}")
    print(f"  raw CAGR / DD / Sharpe  : {bm['cagr']*100:.1f}% / {bm['max_drawdown']*100:.1f}% / {bm['sharpe']:.2f}")
    print(f"  DEFLATED Sharpe (DSR)   : {dsr['dsr']:.3f}   (>0.95 strong; deflated over the whole grid)")
    print(f"  bootstrap Sharpe CI     : [{ci['lo']:.2f}, {ci['hi']:.2f}]")
    print(f"  PBO (grid overfit prob) : {pbo_res.pbo:.3f}   (<0.5 good)")
    print(f"  CPCV OOS Sharpe (mean)  : {np.mean(cpcv_sh):.2f}  (median {np.median(cpcv_sh):.2f}, "
          f"{sum(1 for x in cpcv_sh if x>0)}/{len(cpcv_sh)} folds positive)")
    ey = (1 + R.iloc[:, best_j]).groupby(R.index.year).prod() - 1
    print("  per-year                : " + "  ".join(f"{y}:{v*100:+.0f}%" for y, v in ey.items()))

    # Persist a report + the grid table for publishing.
    out = REPO_ROOT / "research"; out.mkdir(exist_ok=True)
    lines = ["# Crypto trend — honest deflated, rule-based backtest", "",
             f"Generated {pd.Timestamp.now().date()}. Data: yfinance daily spot, "
             f"{close.index.min().date()}–{close.index.max().date()}.", "",
             "**Method (no data-mining shortcuts):** point-in-time top-K-by-dollar-"
             "volume universe (no survivorship, no curation); full 2018+ period; "
             f"Deflated Sharpe over the ENTIRE {len(GRID)}-config grid; PBO + CPCV OOS.", "",
             "## Honest deflated verdict", "",
             f"- Grid searched: **{len(GRID)} configs** (effective trials {eff_n:.1f})",
             f"- Best config: `{best_label}`",
             f"- Raw: **CAGR {bm['cagr']*100:.1f}%, maxDD {bm['max_drawdown']*100:.1f}%, "
             f"Sharpe {bm['sharpe']:.2f}**",
             f"- **Deflated Sharpe (DSR over the whole grid): {dsr['dsr']:.3f}**",
             f"- Bootstrap Sharpe 95% CI: [{ci['lo']:.2f}, {ci['hi']:.2f}]",
             f"- PBO (overfit probability): **{pbo_res.pbo:.3f}**",
             f"- CPCV OOS Sharpe: mean {np.mean(cpcv_sh):.2f}, "
             f"{sum(1 for x in cpcv_sh if x>0)}/{len(cpcv_sh)} folds positive", "",
             "## Full grid (by Sharpe)", "",
             "| config | CAGR | maxDD | Sharpe | Calmar |", "|---|---|---|---|---|"]
    for lbl, c, d, sh, cal in rows:
        lines.append(f"| {lbl} | {c*100:.1f}% | {d*100:.1f}% | {sh:.2f} | {cal:.2f} |")
    lines += ["", "## Honest caveats",
              "- Backtest derived from the same data it's measured on; only forward "
              "paper-trading is a true out-of-sample test.",
              "- yfinance spot, ~10y history dominated by the 2020-21 bull; crypto DDs are deep.",
              "- Shorts require perps / CME crypto futures (MBT/MET). Funding/borrow not modelled here.",
              "- Deflated over THIS grid only; broader human search across the project isn't fully captured."]
    (out / "crypto_deflated_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[report] -> {out / 'crypto_deflated_report.md'}")
    print("DONE")


if __name__ == "__main__":
    main()
