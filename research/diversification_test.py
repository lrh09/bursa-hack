"""Empirical diversification test for a cross-asset futures trend book.

Answers RH's objection: "you can't diversify with micro CME because there
aren't enough assets." We don't argue it — we measure it.

Method:
  1. Pull free daily continuous front-month futures (yfinance) across 5 asset
     classes. (yfinance is NOT roll-adjusted — fine for CORRELATION STRUCTURE,
     not for a final backtest.)
  2. Per market, build a vol-scaled 12-month time-series-momentum (TSMOM)
     return series — i.e. what one sleeve of a vol-targeted trend book earns:
        strat_ret_t = sign(mom_{t-1}) * (ret_t / vol_{t-1})
     No lookahead: signal and vol are both lagged.
  3. Correlation matrix of those sleeve returns across markets.
  4. Diversification metrics:
       - mean pairwise correlation
       - effective_n (Lopez de Prado proxy: N*(1-mean_off_diag)) [reused from harness]
       - Meucci ENB (eigenvalue-entropy "effective number of bets")
  5. Heatmap + summary written to research/.

Interpretation: if effective-N / ENB is ~5-8 on a ~14-market basket, the book
is genuinely diversified and the "not enough micros" objection is wrong. If it
collapses toward ~2, the objection stands.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Reuse the harness effective_n (the one written for the DSR correction).
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from bursahack.intraday.diagnostics import effective_n  # noqa: E402

import yfinance as yf  # noqa: E402

# Candidate basket: 14 markets across 5 asset classes (+crypto).
# Deliberately includes ES + NQ together to expose the equity-correlation trap.
MARKETS: dict[str, str] = {
    "ES=F": "S&P500 (equity)",
    "NQ=F": "Nasdaq (equity)",
    "ZN=F": "10Y Note (rates)",
    "ZB=F": "30Y Bond (rates)",
    "6E=F": "EUR/USD (fx)",
    "6J=F": "JPY/USD (fx)",
    "GC=F": "Gold (metal)",
    "SI=F": "Silver (metal)",
    "CL=F": "WTI Crude (energy)",
    "NG=F": "NatGas (energy)",
    "ZC=F": "Corn (grain)",
    "ZS=F": "Soybeans (grain)",
    "ZW=F": "Wheat (grain)",
    "BTC=F": "Bitcoin (crypto)",
}

MOM_LOOKBACK = 252   # 12-month time-series momentum
VOL_WINDOW = 60      # trailing realized-vol window for risk scaling


def fetch_closes(period: str = "15y") -> pd.DataFrame:
    """Daily close panel for the basket, columns = tickers."""
    tickers = list(MARKETS.keys())
    raw = yf.download(tickers, period=period, interval="1d",
                      progress=False, auto_adjust=False)
    closes = raw["Close"].copy()
    closes = closes.dropna(how="all").sort_index()
    return closes


def sleeve_returns(closes: pd.DataFrame) -> pd.DataFrame:
    """Vol-scaled 12-month TSMOM sleeve return per market (no lookahead)."""
    ret = closes.pct_change()
    vol = ret.rolling(VOL_WINDOW).std().shift(1)            # trailing, lagged
    mom = closes.pct_change(MOM_LOOKBACK)                    # 12m return
    signal = np.sign(mom).shift(1)                          # lagged signal
    sleeve = signal * (ret / vol)                          # risk-equalized
    return sleeve.replace([np.inf, -np.inf], np.nan)


def meucci_enb(corr: np.ndarray) -> float:
    """Eigenvalue-entropy effective number of bets (Meucci 2009).

    ENB = exp(-sum p_i ln p_i), p_i = eigenvalue_i / sum(eigenvalues).
    Independent markets -> ENB = N; perfectly correlated -> ENB = 1.
    """
    w, _ = np.linalg.eigh(corr)
    w = np.clip(w, 1e-12, None)
    p = w / w.sum()
    entropy = -np.sum(p * np.log(p))
    return float(np.exp(entropy))


def main() -> None:
    out_dir = REPO_ROOT / "research"
    out_dir.mkdir(exist_ok=True)

    print("[fetch] downloading 14 futures (15y daily) via yfinance ...")
    closes = fetch_closes("15y")
    print(f"[fetch] panel: {closes.shape[0]} rows x {closes.shape[1]} markets")
    print(f"[fetch] date range: {closes.index.min().date()} -> {closes.index.max().date()}")

    sleeve = sleeve_returns(closes)

    # Use the common window where ALL markets have data (BTC truncates to ~2019+).
    sleeve_full = sleeve.dropna(how="any")
    # Also compute a no-crypto version on a longer window for contrast.
    no_crypto = [c for c in sleeve.columns if c != "BTC=F"]
    sleeve_ex_btc = sleeve[no_crypto].dropna(how="any")

    def report(panel: pd.DataFrame, label: str) -> dict:
        cols = list(panel.columns)
        corr = panel.corr().values
        n = len(cols)
        off = corr[~np.eye(n, dtype=bool)]
        mean_off = float(np.mean(off))
        eff_n = effective_n(corr)
        enb = meucci_enb(corr)
        print(f"\n=== {label} ===")
        print(f"  markets           : {n}  ({panel.shape[0]} obs)")
        print(f"  mean pairwise corr: {mean_off:+.3f}")
        print(f"  effective_n (LdP) : {eff_n:.2f}   (of {n})")
        print(f"  Meucci ENB        : {enb:.2f}   (of {n})")
        return {"label": label, "markets": cols, "n": n, "n_obs": panel.shape[0],
                "mean_off_corr": mean_off, "effective_n": eff_n, "enb": enb,
                "corr": pd.DataFrame(corr, index=cols, columns=cols)}

    r_full = report(sleeve_full, "Full 14-market basket (incl. BTC, short window)")
    r_ex = report(sleeve_ex_btc, "13-market basket (ex-BTC, long window)")

    # The equity-correlation trap, made explicit.
    if "ES=F" in sleeve.columns and "NQ=F" in sleeve.columns:
        es_nq = sleeve[["ES=F", "NQ=F"]].dropna().corr().iloc[0, 1]
        print(f"\n[trap] ES vs NQ trend-sleeve correlation: {es_nq:+.3f} "
              f"(high -> they're ~1 bet, not 2)")

    # Heatmap (matplotlib).
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        corr_df = r_ex["corr"]
        fig, ax = plt.subplots(figsize=(9, 7.5))
        im = ax.imshow(corr_df.values, cmap="RdBu_r", vmin=-1, vmax=1)
        ax.set_xticks(range(len(corr_df)))
        ax.set_yticks(range(len(corr_df)))
        labels = [f"{c}\n{MARKETS[c].split(' ')[0]}" for c in corr_df.columns]
        ax.set_xticklabels(labels, rotation=90, fontsize=7)
        ax.set_yticklabels(labels, fontsize=7)
        for i in range(len(corr_df)):
            for j in range(len(corr_df)):
                ax.text(j, i, f"{corr_df.values[i, j]:.2f}",
                        ha="center", va="center", fontsize=6,
                        color="black" if abs(corr_df.values[i, j]) < 0.5 else "white")
        ax.set_title(f"Trend-sleeve return correlations (ex-BTC)\n"
                     f"effective_n={r_ex['effective_n']:.1f}, ENB={r_ex['enb']:.1f} of {r_ex['n']}",
                     fontsize=10)
        fig.colorbar(im, ax=ax, shrink=0.8)
        fig.tight_layout()
        heatmap_path = out_dir / "diversification_heatmap.png"
        fig.savefig(heatmap_path, dpi=130)
        print(f"\n[plot] heatmap -> {heatmap_path}")
    except Exception as exc:
        print(f"[plot] skipped heatmap: {exc}")

    # Markdown summary.
    md = []
    md.append("# Diversification test — cross-asset futures trend book")
    md.append("")
    md.append(f"- Data: yfinance daily continuous front-month, "
              f"{closes.index.min().date()} -> {closes.index.max().date()} "
              "(NOT roll-adjusted; valid for correlation structure only).")
    md.append(f"- Signal per market: vol-scaled 12-month TSMOM sleeve return.")
    md.append("")
    md.append("| Basket | Markets | Mean pairwise corr | effective_n (LdP) | Meucci ENB |")
    md.append("|---|---|---|---|---|")
    for r in (r_ex, r_full):
        md.append(f"| {r['label']} | {r['n']} | {r['mean_off_corr']:+.3f} | "
                  f"{r['effective_n']:.2f} | {r['enb']:.2f} |")
    md.append("")
    md.append("**Read:** effective-N / ENB near the market count = well diversified; "
              "collapse toward ~2 = RH's objection holds.")
    md.append("")
    if "ES=F" in sleeve.columns and "NQ=F" in sleeve.columns:
        md.append(f"- Equity-correlation trap: ES vs NQ trend-sleeve corr = **{es_nq:+.3f}** "
                  "(the 4 equity-index micros are ~1 bet, not 4).")
    md.append("")
    md.append("Heatmap: `diversification_heatmap.png`.")
    (out_dir / "diversification_results.md").write_text("\n".join(md), encoding="utf-8")
    print(f"[report] -> {out_dir / 'diversification_results.md'}")
    print("\nDONE")


if __name__ == "__main__":
    main()
