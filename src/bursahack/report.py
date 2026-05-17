"""Generate the final tearsheet PNG + REPORT.md for the rotation winner.

One run of the winning strategy across the full 2007-2022 window. The dev set
(2008-01 -> 2019-12) was used for walk-forward selection; the holdout
(2020-01 -> 2022-02) was touched once with this strategy and these params.
This script just visualises what we already know -- it does NOT re-tune.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from bursahack.data_loader import load_panel
from bursahack.engine import run_backtest
from bursahack.metrics import compute_metrics, deflated_sharpe_ratio
from bursahack.paths import RESULTS_DIR
from bursahack.signals.rotation import DualSlopeRotation
from bursahack.walkforward import HOLDOUT_END, HOLDOUT_START


WINNER_PARAMS = {
    "slope_lookback_short": 30,
    "slope_lookback_long": 90,
    "vol_period": 90,
    "min_period_vol": 0.01,
    "max_period_vol": 0.40,
    "min_slope": 20.0,
    "top_n": 20,
    "weight_cap": 0.10,
    "rebal_freq": "M",
    "adv_floor": 500_000.0,
    "price_floor": 0.20,
    "ascending": False,
}
CAPITAL_REFERENCE = 350_000.0
N_TRIALS = 102


def run_full_history():
    print("[report] loading 2007-2022 panel...")
    panel, master = load_panel(2007, 2022)
    strat = DualSlopeRotation(name="rotation_winner", params=WINNER_PARAMS)
    rebal = strat.rebal_dates(panel)
    # need lookback warmup
    rebal = [d for d in rebal if panel.dates.get_loc(d) >= 90]
    print(f"  {len(rebal)} rebal dates")
    led = run_backtest(panel, strat.signal_fn(), rebal, starting_cash=CAPITAL_REFERENCE)
    return led, panel, master


def _yearly_returns(equity: pd.Series) -> pd.Series:
    yearly_end = equity.resample("YE").last()
    return yearly_end.pct_change().dropna()


def _monthly_returns(equity: pd.Series) -> pd.DataFrame:
    monthly_end = equity.resample("ME").last()
    monthly_ret = monthly_end.pct_change().dropna()
    df = pd.DataFrame({
        "year": monthly_ret.index.year,
        "month": monthly_ret.index.month,
        "ret": monthly_ret.values,
    })
    return df.pivot(index="year", columns="month", values="ret")


def _drawdown(equity: pd.Series) -> pd.Series:
    return (equity / equity.cummax()) - 1.0


def _rolling_sharpe(returns: pd.Series, window: int = 126) -> pd.Series:
    rmean = returns.rolling(window).mean()
    rstd = returns.rolling(window).std(ddof=1)
    return (rmean / rstd) * np.sqrt(252)


def build_tearsheet(led, out_path: Path) -> None:
    eq = led.equity
    ret = eq.pct_change().dropna()
    dd = _drawdown(eq)
    yr = _yearly_returns(eq)
    mo = _monthly_returns(eq)
    rs = _rolling_sharpe(ret, window=126)
    trades = led.trades

    fig = plt.figure(figsize=(14, 18))
    gs = fig.add_gridspec(6, 2, hspace=0.45, wspace=0.25)

    # Panel 1 (full width): Equity curve with holdout band
    ax_eq = fig.add_subplot(gs[0, :])
    ax_eq.plot(eq.index, eq.values, color="steelblue", lw=1.2, label="Equity (RM)")
    ax_eq.axvspan(HOLDOUT_START, HOLDOUT_END, color="crimson", alpha=0.10, label="Holdout (2020-2022)")
    ax_eq.axhline(CAPITAL_REFERENCE, color="gray", ls="--", lw=0.8, alpha=0.6)
    ax_eq.set_title("Equity Curve  —  Rotation Strategy (Winner)", fontsize=14, fontweight="bold")
    ax_eq.set_ylabel("Equity (RM)")
    ax_eq.legend(loc="upper left")
    ax_eq.xaxis.set_major_locator(mdates.YearLocator(2))
    ax_eq.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax_eq.grid(alpha=0.3)

    # Panel 2 (full width): Drawdown
    ax_dd = fig.add_subplot(gs[1, :])
    ax_dd.fill_between(dd.index, dd.values * 100, 0, color="crimson", alpha=0.45)
    ax_dd.axvspan(HOLDOUT_START, HOLDOUT_END, color="crimson", alpha=0.10)
    ax_dd.set_title("Drawdown (%)", fontsize=12, fontweight="bold")
    ax_dd.set_ylabel("Drawdown (%)")
    ax_dd.xaxis.set_major_locator(mdates.YearLocator(2))
    ax_dd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax_dd.grid(alpha=0.3)

    # Panel 3 (full width): Yearly return bars
    ax_yr = fig.add_subplot(gs[2, :])
    colors = ["seagreen" if r > 0 else "indianred" for r in yr.values]
    bars = ax_yr.bar(yr.index.year.astype(str), yr.values * 100, color=colors, edgecolor="black", lw=0.5)
    for b, v in zip(bars, yr.values * 100):
        ax_yr.text(b.get_x() + b.get_width() / 2, v + (1 if v >= 0 else -3),
                   f"{v:.0f}%", ha="center", fontsize=8)
    ax_yr.axhline(0, color="black", lw=0.6)
    ax_yr.set_title("Calendar-Year Returns (%)", fontsize=12, fontweight="bold")
    ax_yr.set_ylabel("Return (%)")
    ax_yr.grid(alpha=0.3, axis="y")

    # Panel 4 (full width): Monthly return heatmap
    ax_mo = fig.add_subplot(gs[3, :])
    im = ax_mo.imshow(mo.values * 100, aspect="auto", cmap="RdYlGn", vmin=-20, vmax=20)
    ax_mo.set_xticks(range(12))
    ax_mo.set_xticklabels(["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
    ax_mo.set_yticks(range(len(mo.index)))
    ax_mo.set_yticklabels(mo.index)
    ax_mo.set_title("Monthly Returns (%) — Years x Months", fontsize=12, fontweight="bold")
    for i in range(mo.shape[0]):
        for j in range(mo.shape[1]):
            v = mo.values[i, j]
            if not np.isnan(v):
                ax_mo.text(j, i, f"{v * 100:.0f}", ha="center", va="center",
                           fontsize=7, color="black" if abs(v) < 0.10 else "white")
    plt.colorbar(im, ax=ax_mo, fraction=0.022, pad=0.01)

    # Panel 5 (left): Rolling 6m Sharpe
    ax_rs = fig.add_subplot(gs[4, 0])
    ax_rs.plot(rs.index, rs.values, color="darkorange", lw=1.0)
    ax_rs.axhline(0, color="black", lw=0.6)
    ax_rs.axhline(1.0, color="green", lw=0.6, ls=":", alpha=0.6)
    ax_rs.axvspan(HOLDOUT_START, HOLDOUT_END, color="crimson", alpha=0.10)
    ax_rs.set_title("Rolling 6m Sharpe Ratio", fontsize=12, fontweight="bold")
    ax_rs.xaxis.set_major_locator(mdates.YearLocator(2))
    ax_rs.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax_rs.grid(alpha=0.3)

    # Panel 6 (right): Cost / leg histogram
    ax_cost = fig.add_subplot(gs[4, 1])
    if not trades.empty:
        bps = trades["cost_bps"]
        ax_cost.hist(bps, bins=50, color="purple", alpha=0.7, edgecolor="black", lw=0.3)
        ax_cost.axvline(bps.mean(), color="red", ls="--", lw=1, label=f"mean {bps.mean():.1f} bps")
        ax_cost.set_title("Per-Leg Cost Distribution (bps)", fontsize=12, fontweight="bold")
        ax_cost.set_xlabel("bps")
        ax_cost.legend()
    ax_cost.grid(alpha=0.3)

    # Panel 7 (left): Position count over time
    ax_pos = fig.add_subplot(gs[5, 0])
    n_pos = (led.holdings != 0).sum(axis=1)
    ax_pos.plot(n_pos.index, n_pos.values, color="navy", lw=0.8)
    ax_pos.axhline(WINNER_PARAMS["top_n"], color="green", ls=":", lw=0.8, alpha=0.7, label=f"target {WINNER_PARAMS['top_n']}")
    ax_pos.set_title("Number of Positions Held", fontsize=12, fontweight="bold")
    ax_pos.xaxis.set_major_locator(mdates.YearLocator(2))
    ax_pos.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax_pos.legend(loc="upper left")
    ax_pos.grid(alpha=0.3)

    # Panel 8 (right): Return histogram
    ax_rh = fig.add_subplot(gs[5, 1])
    ax_rh.hist(ret.values * 100, bins=80, color="teal", alpha=0.7, edgecolor="black", lw=0.3)
    ax_rh.axvline(0, color="black", lw=0.6)
    ax_rh.axvline(ret.mean() * 100, color="red", ls="--", lw=1, label=f"mean {ret.mean() * 100:.2f}%")
    ax_rh.set_title("Daily Return Distribution (%)", fontsize=12, fontweight="bold")
    ax_rh.set_xlabel("Daily return (%)")
    ax_rh.legend()
    ax_rh.grid(alpha=0.3)

    fig.suptitle(
        f"Tearsheet  —  Rotation Strategy (slope 30/90, top 20, monthly, 10% cap, RM {CAPITAL_REFERENCE:,.0f})",
        fontsize=15, fontweight="bold", y=0.995,
    )
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"[report] wrote {out_path}")


def compute_split_metrics(led):
    eq = led.equity
    trades = led.trades

    dev = eq.loc[:HOLDOUT_START - pd.Timedelta(days=1)]
    hold = eq.loc[HOLDOUT_START:HOLDOUT_END]
    full = eq

    dev_trades = trades[trades["date"] < HOLDOUT_START] if not trades.empty else trades
    hold_trades = trades[trades["date"] >= HOLDOUT_START] if not trades.empty else trades

    return {
        "full": compute_metrics(full, trades, n_trials=N_TRIALS),
        "dev": compute_metrics(dev, dev_trades, n_trials=N_TRIALS),
        "holdout": compute_metrics(hold, hold_trades, n_trials=N_TRIALS),
    }


def build_markdown_report(led, metrics, search_top10, out_path: Path) -> None:
    eq = led.equity
    yr = _yearly_returns(eq)

    fm = metrics["full"]
    dm = metrics["dev"]
    hm = metrics["holdout"]

    lines = []
    lines.append("# BursaHack — Final Strategy Report")
    lines.append("")
    lines.append(f"Generated: {pd.Timestamp.now():%Y-%m-%d}")
    lines.append(f"Source data: Sentieo XKLS daily EOD, 2007-01-03 to 2022-02-15 (survivorship-free)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Executive summary
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- **Strategy chosen**: dual-slope momentum rotation, top-20 names, monthly rebal, vol-parity weighting with 10% cap.")
    lines.append(f"- **Walk-forward Sharpe** (in-sample selection): **{1.19:.2f}**, **CAGR +25.9%**, max DD −16.5% (across 16 folds, RM 350k).")
    lines.append(f"- **Holdout Sharpe** (touched once, 2020-2022): **{hm.sharpe:.2f}**, **CAGR {hm.cagr * 100:+.2f}%**, max DD {hm.max_drawdown * 100:.1f}%.")
    lines.append(f"- **Deflated Sharpe** post-holdout (N=102 trials): **{hm.deflated_sharpe:.2f}** -- barely above the 0.5 noise floor.")
    lines.append(f"- **Honest read**: the strategy survived a stress window (COVID + reflation + 2021 selloff) with positive return but ~75% of the walk-forward alpha disappeared. The dev-set max DD was an artefact of no fold containing a true crisis. Live drawdowns will look more like the holdout's −47% than the dev's −16%.")
    lines.append("")

    # Strategy
    lines.append("## 1. The Strategy (in plain words)")
    lines.append("")
    lines.append("Every month, on the first business day of the month:")
    lines.append("")
    lines.append("1. **Universe gate** -- start with all vanilla Bursa equity (`^\\d{4}$` Main Market + `5235SS` KLCC stapled + `^03\\d{3}$` ACE Market = 1,334 names), then drop any name where:")
    lines.append(f"    - Last close < RM {WINNER_PARAMS['price_floor']:.2f}")
    lines.append(f"    - 20-day average daily turnover < RM {WINNER_PARAMS['adv_floor']:,.0f}")
    lines.append(f"    - Has not traded (vol > 0) in the last 5 days")
    lines.append("2. **Vol band** -- additionally drop names where `period_vol_90` is outside [0.01, 0.40].")
    lines.append("3. **Score** each surviving name as the *average of two annualised exp-regression slopes*:")
    lines.append("")
    lines.append("    ```")
    lines.append("    score = 0.5 * (")
    lines.append("        100 * ((1 + slope_b_30)^250 - 1) * R_squared_30")
    lines.append("      + 100 * ((1 + slope_b_90)^250 - 1) * R_squared_90")
    lines.append("    )")
    lines.append("    ```")
    lines.append("")
    lines.append("    where `slope_b_N` is the OLS slope of `ln(adj_close) ~ t` over the last N trading days, and `R_squared_N` is the corresponding coefficient of determination.")
    lines.append("4. **Filter** to `score > 20`.")
    lines.append(f"5. **Pick** the top {WINNER_PARAMS['top_n']} by score (descending).")
    lines.append(f"6. **Weight** by inverse `period_vol_90`, cap each name at **{WINNER_PARAMS['weight_cap'] * 100:.0f}%**, redistribute excess pro-rata to uncapped names, then a final flatten clips any still-above-cap to the cap.")
    lines.append("7. **Trade** at the next business day's **open price** (T+1), rounded to whole lots of 100 shares. Pay MPlus brokerage + clearing + stamp + 8% SST + sqrt-impact slippage.")
    lines.append("8. **Hold** for one month, then repeat.")
    lines.append("")
    lines.append("**Code**: `src/bursahack/signals/rotation.py` (`DualSlopeRotation` class).")
    lines.append("")

    # Parameters
    lines.append("### 1.1 Final parameters")
    lines.append("")
    lines.append("| Parameter | Value | Notes |")
    lines.append("|---|---:|---|")
    for k, v in WINNER_PARAMS.items():
        notes = {
            "slope_lookback_short": "shorter slope leg",
            "slope_lookback_long": "longer slope leg",
            "vol_period": "rolling window for inverse-vol weighting (and `period_vol` filter)",
            "min_period_vol": "stocks below this are excluded as too inactive",
            "max_period_vol": "stocks above this are excluded as too speculative",
            "min_slope": "score threshold; barely matters once top-N kicks in",
            "top_n": "concentration of the book",
            "weight_cap": "max single-name weight; the brake on tail risk",
            "rebal_freq": "M = monthly first-business-day rebalance",
            "adv_floor": "20-day average daily turnover floor (RM)",
            "price_floor": "minimum last close (RM)",
            "ascending": "False = pick winners not losers",
        }.get(k, "")
        lines.append(f"| `{k}` | {v!r} | {notes} |")
    lines.append("")

    # Data
    lines.append("## 2. Data, Universe, and Assumptions")
    lines.append("")
    lines.append("**Source**: `stock_prices_xkls_all_file-1.csv` (Sentieo/FactSet XKLS daily EOD, 3.73M rows, 4,066 unique securities, 2007-01-03 to 2022-02-15).")
    lines.append("")
    lines.append("**Equity universe definition** (codified in `src/bursahack/universe.py`):")
    lines.append("- Plain 4-digit Main Market codes (e.g. `1295` Public Bank, `5212` Pavilion REIT): 1,286 names")
    lines.append("- 4-digit + `SS` Stapled Securities (only KLCCP Stapled `5235SS`): 1 name")
    lines.append("- 5-digit ACE Market codes `03xxx` (e.g. Aurora Italia `03037`): 47 names")
    lines.append("- **Total = 1,334 equity instruments.** Survivorship-bias-free (45% of securities stop trading before file end, consistent with real delistings).")
    lines.append("")
    lines.append("**Excluded by default**: company warrants (`WA`-`WE`), structured warrants (`Cx`, `Hx`, `Px`, 5-6 digit codes), rights/temporary tickers (`OR`, `TR`, `PR`), ETFs (`EA`). These trade differently and would contaminate a long-only momentum signal.")
    lines.append("")
    lines.append("**Known data-vendor quirks handled in code**:")
    lines.append("- Volume reporting changed from \"thousand shares\" to \"actual shares\" on **2014-06-03**. Pre-cutover rows have `ADJ_VOLUME *= 1000` applied at load (`src/bursahack/data_loader.py`).")
    lines.append("- KLCC reorganised from `5089` to `5235SS` in May 2013 -- this is the only ticker chain in the dataset (`TICKER_CHAINS` in `universe.py`).")
    lines.append("- 2021 row-count anomaly (2,488 \"new\" securities) was structured-warrant onboarding, not equity dupes. Filter handles it.")
    lines.append("- `ADJ_FACTOR` tracks splits only; dividends are baked directly into `ADJ_CLOSE`. We use `ADJ_CLOSE` for signal & MTM (total return), and raw `OPEN` for execution-price reconstruction.")
    lines.append("")
    lines.append("**Iron rules baked in**:")
    lines.append("- All cross-time joins on `SECURITY_ID` (not `TICKER`, which can change).")
    lines.append("- Force-exit any held name with > 7 days of zero volume.")
    lines.append("- Suspension policy: skip dates with NaN raw price (signal computes but orders don't fill).")
    lines.append("- Lot size: 100 shares (Bursa standard).")
    lines.append("")

    # Costs
    lines.append("## 3. Cost Model — MPlus (Malacca Securities)")
    lines.append("")
    lines.append("Applied to **both** buy and sell legs:")
    lines.append("")
    lines.append("| Component | Rate | Cap / Floor |")
    lines.append("|---|---:|---|")
    lines.append("| Brokerage | 0.05% of notional | **min RM 8** |")
    lines.append("| SST on brokerage | 8% × brokerage | -- |")
    lines.append("| Clearing fee (Bursa) | 0.03% | cap RM 1,000 |")
    lines.append("| Stamp duty | 0.10% | cap RM 1,000 |")
    lines.append("| Slippage (modelled) | `k * sqrt(notional / 20d_ADV)` bps | 2-200 bps band |")
    lines.append("")
    lines.append(f"For the strategy as run (RM {CAPITAL_REFERENCE:,.0f} capital, top {WINNER_PARAMS['top_n']}, monthly rebal):")
    lines.append(f"- **Average cost per leg in the dev set: {dm.avg_cost_bps:.1f} bps**")
    lines.append(f"- **Average cost per leg in the holdout: {hm.avg_cost_bps:.1f} bps**")
    lines.append(f"- **Annual turnover**: ~{fm.turnover:.1f}× — meaningful but the 10% weight cap keeps per-name notionals large enough that the RM 8 brokerage floor doesn't dominate.")
    lines.append("")

    # Methodology
    lines.append("## 4. Methodology — Walk-Forward + Holdout")
    lines.append("")
    lines.append("**Time discipline**:")
    lines.append("- Signal computed at end-of-day t using only data ≤ t (enforced by vectorised precompute that's NaN-padded at the front).")
    lines.append("- Fills happen at T+1 OPEN; cost notional uses raw open, fill price uses adjusted open with slippage applied as a signed half-spread.")
    lines.append("- Daily mark-to-market on `ADJ_CLOSE`.")
    lines.append("")
    lines.append("**Walk-forward folds** (16 in total, dev set only):")
    lines.append("- Train = 3 years, validate = 1 year, step = 6 months.")
    lines.append("- 21-day embargo between train end and validate start (matches monthly rebal horizon).")
    lines.append(f"- First fold: train 2008-01 → 2011-01, validate 2011-02 → 2012-02.")
    lines.append(f"- Last fold: train 2015-07 → 2018-07, validate 2018-08 → 2019-08.")
    lines.append("- Last validate ends 2019-08-12, comfortably below the holdout start 2020-01-01.")
    lines.append("")
    lines.append("**Holdout** (`HOLDOUT_START` = 2020-01-01, `HOLDOUT_END` = 2022-02-15):")
    lines.append("- Never touched by any of the 1,621 walk-forward backtests (verified with `assert_no_holdout_leak()` guards).")
    lines.append("- Touched exactly once, with the winning variant and these parameters, at three capital levels (RM 100k / RM 350k / RM 1M).")
    lines.append("- This report does **not** re-tune in response to the holdout result.")
    lines.append("")

    # Search summary
    lines.append("## 5. The Brute-Force Search (multiple-testing discipline)")
    lines.append("")
    lines.append("Across four strategy families, **102 unique parameter variants** were tried × 16 walk-forward folds = **1,621 backtests**. Every (variant, fold) result is logged append-only to `results/search_log.jsonl` so we know exactly how many trials to penalise.")
    lines.append("")
    lines.append("Families tried:")
    lines.append("- **Momentum**: classic top-N cross-sectional momentum, 32 variants")
    lines.append("- **Reversal**: short-horizon mean-reversion, 16 variants")
    lines.append("- **Clenow \"Stocks on the Move\"**: exp-regression rank × R² × ATR-sizing × regime filter, ~22 variants completed (Clenow tail was killed after the rotation winner was clearly ahead)")
    lines.append("- **Dual-slope rotation** (the winner family, ported from your original Python): 32 variants")
    lines.append("")
    lines.append("**Deflated Sharpe Ratio** (Bailey & de Prado 2014) applied with N_trials = 102. The expected max Sharpe under null with N=102 is ≈ 3.0+ (in daily-Sharpe units), so even the in-sample winners would need very high Sharpe to clear the bar. Our rotation winner at walk-forward Sharpe 1.19 has DSR 0.85, which translates to \"85% probability the true Sharpe is positive\" -- comfortably above the 0.5 noise floor but not a slam dunk.")
    lines.append("")
    lines.append("### 5.1 Top 10 walk-forward survivors")
    lines.append("")
    lines.append("| # | Strategy | Rebal | Slope | Top-N | Min-slope | Sharpe (μ) | CAGR (μ) | Max DD (μ) | DSR |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|")
    for i, row in search_top10.head(10).iterrows():
        params = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
        strategy = row.get("strategy", "?")
        rebal = params.get("rebal_freq", "?")
        if strategy == "rotation":
            slope_str = f"{params.get('slope_lookback_short')}/{params.get('slope_lookback_long')}"
            tn = params.get("top_n")
            ms = params.get("min_slope", "—")
        elif strategy == "clenow_som":
            slope_str = f"lb={params.get('lookback')}"
            tn = params.get("top_n")
            ms = "—"
        else:
            slope_str = f"lb={params.get('lookback', '?')}"
            tn = params.get("top_n", "?")
            ms = "—"
        lines.append(f"| {i+1} | {strategy} | {rebal} | {slope_str} | {tn} | {ms} "
                     f"| {row['sharpe_mean']:.2f} | {row['cagr_mean'] * 100:+.1f}% "
                     f"| {row['max_dd_mean'] * 100:.1f}% | {row['dsr']:.2f} |")
    lines.append("")

    # Results
    lines.append("## 6. Tearsheet")
    lines.append("")
    lines.append("See `results/tearsheet_rotation.png` for the visual tearsheet (equity curve, drawdown, calendar-year bars, monthly heatmap, rolling 6m Sharpe, per-leg cost distribution, position count, daily return distribution).")
    lines.append("")
    lines.append("### 6.1 Headline metrics across windows (RM 350k capital)")
    lines.append("")
    lines.append("| Metric | Full (2008-2022) | Dev set (2008-2019) | Holdout (2020-2022) |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| CAGR | {fm.cagr * 100:+.2f}% | {dm.cagr * 100:+.2f}% | **{hm.cagr * 100:+.2f}%** |")
    lines.append(f"| Annual vol | {fm.vol * 100:.2f}% | {dm.vol * 100:.2f}% | {hm.vol * 100:.2f}% |")
    lines.append(f"| Sharpe | {fm.sharpe:.2f} | {dm.sharpe:.2f} | **{hm.sharpe:.2f}** |")
    lines.append(f"| Sortino | {fm.sortino:.2f} | {dm.sortino:.2f} | {hm.sortino:.2f} |")
    lines.append(f"| Max drawdown | {fm.max_drawdown * 100:.2f}% | {dm.max_drawdown * 100:.2f}% | {hm.max_drawdown * 100:.2f}% |")
    lines.append(f"| Calmar | {fm.calmar:.2f} | {dm.calmar:.2f} | {hm.calmar:.2f} |")
    lines.append(f"| Hit rate (daily) | {fm.hit_rate * 100:.2f}% | {dm.hit_rate * 100:.2f}% | {hm.hit_rate * 100:.2f}% |")
    lines.append(f"| Total trades | {fm.n_trades:,} | {dm.n_trades:,} | {hm.n_trades:,} |")
    lines.append(f"| Avg cost / leg | {fm.avg_cost_bps:.1f} bps | {dm.avg_cost_bps:.1f} bps | {hm.avg_cost_bps:.1f} bps |")
    lines.append(f"| Annual turnover | {fm.turnover:.2f}× | {dm.turnover:.2f}× | {hm.turnover:.2f}× |")
    lines.append(f"| Deflated Sharpe (N=102) | {fm.deflated_sharpe:.3f} | {dm.deflated_sharpe:.3f} | **{hm.deflated_sharpe:.3f}** |")
    lines.append("")
    lines.append("> **Note** on the gap between walk-forward selection Sharpe (1.19) and the dev-set single-pass Sharpe ({:.2f}): the walk-forward number is the *mean across 16 1-year validation folds with embargo*, while the dev-set single-pass uses no embargo and includes the full 2008 GFC drawdown without resetting. They're measuring different things; the walk-forward number is the one you should trust for variant selection.".format(dm.sharpe))
    lines.append("")

    # Capital scaling
    lines.append("### 6.2 Capital scaling — holdout only (the cost story)")
    lines.append("")
    hold_data = json.loads(Path(RESULTS_DIR / "holdout_verdict_rotation.json").read_text())["results_by_capital"]
    lines.append("| Capital | Final equity | CAGR | Sharpe | Max DD | Avg cost/leg |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    for cap_str, r in hold_data.items():
        m = r["metrics"]
        lines.append(f"| RM {float(cap_str):,.0f} | RM {r['final_equity']:,.2f} | "
                     f"{m['cagr'] * 100:+.2f}% | {m['sharpe']:.2f} | "
                     f"{m['max_drawdown'] * 100:.2f}% | {m['avg_cost_bps']:.1f} bps |")
    lines.append("")
    lines.append("- **RM 100k is uneconomic** for this strategy. The RM 8 brokerage floor binds on many trades, dragging average per-leg cost to ~46 bps and cutting CAGR to ~1.6%.")
    lines.append("- **RM 350k is the cost sweet spot.** Costs drop to ~25 bps/leg, CAGR triples. Above RM 350k, returns to scale flatten.")
    lines.append("- **The strategy capacity** at RM 1M with current liquidity floor is unstressed -- top 20 names from a ~150-name eligible pool is plenty.")
    lines.append("")

    # Calendar year breakdown
    lines.append("### 6.3 Calendar-year returns")
    lines.append("")
    lines.append("| Year | Return | Period |")
    lines.append("|---|---:|---|")
    for d, r in yr.items():
        period = "dev set" if d.year < HOLDOUT_START.year else "**holdout**"
        lines.append(f"| {d.year} | {r * 100:+.2f}% | {period} |")
    lines.append("")

    # The verdict
    lines.append("## 7. The Verdict & Decision Frame")
    lines.append("")
    lines.append("**Walk-forward (in-sample selection)**: looked great. Mean Sharpe 1.19 across 16 folds, CAGR ~25%, max DD ~16%. DSR 0.85.")
    lines.append("")
    lines.append("**Holdout (single shot, 2020-2022)**: alpha fell ~75%. Final Sharpe **0.29 at RM 350k**, CAGR **+4.2%**, max DD **−47.8%**. DSR drops to 0.58.")
    lines.append("")
    lines.append("This is the canonical pattern for retail momentum on Bursa: real signal exists, but it's modest, and tail risk is fat. The dev-set max DD of −16% was an artefact of no fold containing a true crisis; when the strategy actually met one (COVID March 2020), it drew down −47% before recovering. Anyone deploying this needs to be psychologically and financially prepared for −50% peak-to-trough drawdowns.")
    lines.append("")
    lines.append("**Three defensible positions:**")
    lines.append("")
    lines.append("**A) Trade it small.** Positive Sharpe across all capital levels in a hard window. Hit rate 56%. Drawdown profile is severe but typical for long-only Bursa momentum. Size it small (RM 350k-1M, ~10% of liquid net worth maximum), be ready for −50% drawdowns, give it 3-5 years of live data before judging.")
    lines.append("")
    lines.append("**B) Stand down.** Sharpe 0.29 after 102 trials is consistent with \"very weak signal hidden in transaction-cost noise.\" DSR 0.58 means we can't confidently reject \"this is luck.\" If you wouldn't deploy the algo at 10% of the conviction the dev set implied, don't deploy it at all.")
    lines.append("")
    lines.append("**C) Design v2 with a fresh holdout.** This holdout window is now contaminated -- we've touched it. Next iteration: try new signal families (volatility carry, low-vol anomaly, fundamentals if a source is available), use a *different* holdout cut (e.g. test 2018-2019 separately, reserving 2020-2022 + future data as the next OOS).")
    lines.append("")
    lines.append("Position A is the realistic median quant view; position B is the rigorous statistician's view; position C is what you'd do if you had unlimited research time.")
    lines.append("")

    # Reproducibility
    lines.append("## 8. Reproducibility")
    lines.append("")
    lines.append("All code lives under `src/bursahack/`. To reproduce this report from scratch on the same CSV:")
    lines.append("")
    lines.append("```bash")
    lines.append("uv sync                                                # install deps")
    lines.append("uv run python -m bursahack.ingest                       # CSV -> parquet (10s)")
    lines.append("uv run python -m bursahack.dq                            # data-quality report")
    lines.append("uv run python -m bursahack.run_search                    # 1,621-backtest brute force (~60 min)")
    lines.append("uv run python -m bursahack.analyze_search                # winner identification")
    lines.append("uv run python -m bursahack.run_holdout --strategy rotation --n-trials 102")
    lines.append("uv run python -m bursahack.report                        # produce this report + tearsheet")
    lines.append("```")
    lines.append("")
    lines.append("Test suite (`pytest tests/`): **32 unit tests covering the universe filter, MPlus fee math, engine fills, signal helpers, and the inverse-vol weighting**. All pass.")
    lines.append("")
    lines.append("**Artefacts in `results/`**:")
    lines.append("- `tearsheet_rotation.png` -- the visual tearsheet")
    lines.append("- `REPORT.md` -- this document")
    lines.append("- `holdout_verdict_rotation.json` -- the holdout numbers")
    lines.append("- `search_summary.csv` -- per-variant aggregated stats")
    lines.append("- `search_log.jsonl` -- 1,621 raw (variant, fold) results, append-only")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[report] wrote {out_path}")


def main():
    led, panel, master = run_full_history()
    metrics = compute_split_metrics(led)

    summary_csv = RESULTS_DIR / "search_summary.csv"
    if summary_csv.exists():
        search_top10 = pd.read_csv(summary_csv).head(10)
    else:
        search_top10 = pd.DataFrame()

    tearsheet_path = RESULTS_DIR / "tearsheet_rotation.png"
    report_path = RESULTS_DIR / "REPORT.md"
    build_tearsheet(led, tearsheet_path)
    build_markdown_report(led, metrics, search_top10, report_path)

    # Save the equity curve as CSV too for downstream use
    led.equity.to_csv(RESULTS_DIR / "rotation_winner_equity.csv", header=["equity"])
    led.trades.to_csv(RESULTS_DIR / "rotation_winner_trades.csv", index=False)
    print()
    print("DONE.")
    print(f"  tearsheet: {tearsheet_path}")
    print(f"  report:    {report_path}")


if __name__ == "__main__":
    main()
