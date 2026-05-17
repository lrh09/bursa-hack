"""Standalone, investor-grade chart figures for the PDF proposal."""
from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from bursahack.walkforward import HOLDOUT_END, HOLDOUT_START

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.labelsize": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linestyle": "--",
})

NAVY = "#0B2349"
BLUE = "#2C5F8D"
GREEN = "#1F7A4B"
RED = "#A8332B"
GOLD = "#C49A2A"
GRAY = "#666666"


def equity_curve_panel(equity: pd.Series, out_path: Path, capital_ref: float) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(9, 5), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    ax = axes[0]
    ax.plot(equity.index, equity.values, color=NAVY, lw=1.5)
    ax.axvspan(HOLDOUT_START, HOLDOUT_END, color=RED, alpha=0.08, label="Holdout (out-of-sample)")
    ax.axhline(capital_ref, color=GRAY, ls="--", lw=0.7, alpha=0.5)
    ax.set_title(f"Equity Curve  —  Initial Capital RM {capital_ref:,.0f}")
    ax.set_ylabel("Portfolio Value (RM)")
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    dd = (equity / equity.cummax() - 1.0) * 100
    ax2 = axes[1]
    ax2.fill_between(dd.index, dd.values, 0, color=RED, alpha=0.4)
    ax2.axvspan(HOLDOUT_START, HOLDOUT_END, color=RED, alpha=0.08)
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_title("")
    ax2.xaxis.set_major_locator(mdates.YearLocator(2))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def calendar_year_chart(equity: pd.Series, out_path: Path) -> None:
    yearly = equity.resample("YE").last().pct_change().dropna()
    years = yearly.index.year
    vals = yearly.values * 100

    fig, ax = plt.subplots(figsize=(8, 4))
    colors = [GREEN if v > 0 else RED for v in vals]
    bars = ax.bar(years.astype(str), vals, color=colors, edgecolor="black", lw=0.4, alpha=0.85)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + (1.2 if v >= 0 else -3.5),
                f"{v:+.0f}%", ha="center", fontsize=8, fontweight="bold")
    # Mark holdout years
    for i, yr in enumerate(years):
        if yr >= HOLDOUT_START.year:
            ax.get_xticklabels()[i].set_color(RED)
            ax.get_xticklabels()[i].set_fontweight("bold")
    ax.axhline(0, color="black", lw=0.6)
    ax.set_title("Calendar-Year Returns  —  Red labels indicate out-of-sample holdout years")
    ax.set_ylabel("Return (%)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def is_vs_oos_chart(equity: pd.Series, out_path: Path) -> None:
    """Side-by-side: dev set equity vs holdout equity, both rebased to 100."""
    dev = equity.loc[:HOLDOUT_START - pd.Timedelta(days=1)]
    hold = equity.loc[HOLDOUT_START:HOLDOUT_END]
    dev_r = 100.0 * dev / dev.iloc[0]
    hold_r = 100.0 * hold / hold.iloc[0]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    ax = axes[0]
    ax.plot(dev_r.index, dev_r.values, color=BLUE, lw=1.2)
    ax.axhline(100, color=GRAY, ls="--", lw=0.7, alpha=0.6)
    ax.set_title("In-Sample (Dev Set 2008-2019)\nbacktest-rebalance under walk-forward selection")
    ax.set_ylabel("Equity (rebased to 100)")
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    dev_cagr = (dev.iloc[-1] / dev.iloc[0]) ** (365.25 / (dev.index[-1] - dev.index[0]).days) - 1
    ax.text(0.04, 0.95, f"CAGR: {dev_cagr * 100:+.1f}%", transform=ax.transAxes,
            fontsize=11, fontweight="bold", color=BLUE, va="top")

    ax2 = axes[1]
    ax2.plot(hold_r.index, hold_r.values, color=RED, lw=1.2)
    ax2.axhline(100, color=GRAY, ls="--", lw=0.7, alpha=0.6)
    ax2.set_title("Out-of-Sample (Holdout 2020-2022)\ntouched ONCE, no re-tuning")
    ax2.set_ylabel("Equity (rebased to 100)")
    ax2.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    hold_cagr = (hold.iloc[-1] / hold.iloc[0]) ** (365.25 / (hold.index[-1] - hold.index[0]).days) - 1
    ax2.text(0.04, 0.95, f"CAGR: {hold_cagr * 100:+.1f}%", transform=ax2.transAxes,
             fontsize=11, fontweight="bold", color=RED, va="top")
    for label in ax2.get_xticklabels():
        label.set_rotation(30)
        label.set_ha("right")

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def monthly_heatmap(equity: pd.Series, out_path: Path) -> None:
    monthly = equity.resample("ME").last().pct_change().dropna()
    df = pd.DataFrame({
        "year": monthly.index.year,
        "month": monthly.index.month,
        "ret": monthly.values * 100,
    }).pivot(index="year", columns="month", values="ret")

    fig, ax = plt.subplots(figsize=(10, 6))
    im = ax.imshow(df.values, aspect="auto", cmap="RdYlGn", vmin=-15, vmax=15)
    ax.set_xticks(range(12))
    ax.set_xticklabels(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.set_yticks(range(len(df.index)))
    ax.set_yticklabels(df.index)
    ax.set_title("Monthly Returns Heatmap (%)")
    for i in range(df.shape[0]):
        for j in range(df.shape[1]):
            v = df.values[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:+.1f}", ha="center", va="center",
                        fontsize=7, color="black" if abs(v) < 8 else "white", fontweight="bold")
    cbar = plt.colorbar(im, ax=ax, fraction=0.02, pad=0.01)
    cbar.set_label("Return (%)", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
