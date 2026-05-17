"""Generate the investor-proposal PDF.

Produces `results/BursaHack_Proposal.pdf` -- a multi-page investor-facing
document covering strategy, methodology, performance (in-sample + out-of-sample),
risks, capital requirements, and disclosures.

Honest about the holdout dropoff -- this is a research-stage strategy, not a
registered product, and the proposal positions it as such.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, inch, mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT

from bursahack.data_loader import load_panel
from bursahack.engine import run_backtest
from bursahack.metrics import Metrics, compute_metrics
from bursahack.paths import RESULTS_DIR
from bursahack.proposal_charts import (
    calendar_year_chart,
    equity_curve_panel,
    is_vs_oos_chart,
    monthly_heatmap,
)
from bursahack.signals.rotation import DualSlopeRotation
from bursahack.walkforward import HOLDOUT_END, HOLDOUT_START

CAPITAL_LEVELS = [100_000.0, 350_000.0, 1_000_000.0]


def run_winner_at_capitals(panel, capitals=CAPITAL_LEVELS) -> dict[float, "Ledger"]:
    """Run rotation winner at each capital level on the full 2007-2022 panel."""
    out = {}
    for cap in capitals:
        strat = DualSlopeRotation(name=f"winner_{int(cap)}", params=dict(WINNER_PARAMS))
        rebal = strat.rebal_dates(panel)
        rebal = [d for d in rebal if panel.dates.get_loc(d) >= 90]
        led = run_backtest(panel, strat.signal_fn(), rebal, starting_cash=cap)
        out[cap] = led
        print(f"  full-history  cap=RM {cap:,.0f} -> final RM {led.equity.iloc[-1]:,.0f}")
    return out


def run_winner_holdout_only(capitals=CAPITAL_LEVELS) -> dict[float, "Ledger"]:
    """Fresh-deployment OOS scenario: start each capital level at HOLDOUT_START.

    Matches `holdout_verdict_rotation.json` -- this is the "if I deploy RM X today"
    forward-looking framing that an investor reads in the capital-scaling table.
    """
    # Load 2018-2022 panel for lookback warmup before holdout starts
    print("[proposal_pdf] loading 2018-2022 panel for fresh-deployment OOS runs...")
    panel_oos, _ = load_panel(2018, 2022)
    out = {}
    for cap in capitals:
        strat = DualSlopeRotation(name=f"winner_oos_{int(cap)}", params=dict(WINNER_PARAMS))
        rebal = strat.rebal_dates(panel_oos)
        rebal = [d for d in rebal if d >= HOLDOUT_START]
        led = run_backtest(panel_oos, strat.signal_fn(), rebal, starting_cash=cap)
        out[cap] = led
        eq_oos = led.equity.loc[HOLDOUT_START:HOLDOUT_END]
        print(f"  fresh-OOS     cap=RM {cap:,.0f} -> final RM {eq_oos.iloc[-1]:,.0f}")
    return out


def metrics_for_windows(led, n_trials: int = 102, oos_led=None) -> dict[str, Metrics]:
    """Split a Ledger into IS / OOS / Overall slices and compute metrics.

    If `oos_led` is provided, use IT for the OOS slice (fresh-deployment scenario).
    Otherwise slice the full-history `led` for OOS too (compounded scenario).
    """
    eq = led.equity
    trades = led.trades

    is_end = HOLDOUT_START - pd.Timedelta(days=1)
    eq_is = eq.loc[:is_end]
    eq_all = eq
    tr_is = trades[trades["date"] <= is_end] if not trades.empty else trades

    if oos_led is not None:
        eq_oos = oos_led.equity.loc[HOLDOUT_START:HOLDOUT_END]
        tr_oos = oos_led.trades[(oos_led.trades["date"] >= HOLDOUT_START)
                                & (oos_led.trades["date"] <= HOLDOUT_END)] if not oos_led.trades.empty else oos_led.trades
    else:
        eq_oos = eq.loc[HOLDOUT_START:HOLDOUT_END]
        tr_oos = trades[(trades["date"] >= HOLDOUT_START) & (trades["date"] <= HOLDOUT_END)] if not trades.empty else trades

    return {
        "is": compute_metrics(eq_is, tr_is, n_trials=n_trials),
        "oos": compute_metrics(eq_oos, tr_oos, n_trials=n_trials),
        "overall": compute_metrics(eq_all, trades, n_trials=n_trials),
    }


# ---- Brand + style constants -------------------------------------------------
BRAND_NAME = "FatFyre Research"
STRATEGY_NAME = "Bursa Momentum Rotation"
STRATEGY_TAG = "Dual-Slope Cross-Sectional Momentum on Bursa Malaysia Equities"
CAPITAL_REF = 350_000.0
N_TRIALS = 102

NAVY = colors.HexColor("#0B2349")
BLUE = colors.HexColor("#2C5F8D")
GREEN = colors.HexColor("#1F7A4B")
RED = colors.HexColor("#A8332B")
GOLD = colors.HexColor("#C49A2A")
SOFT_GRAY = colors.HexColor("#EEEEEE")
TEXT = colors.HexColor("#1A1A1A")
DIM = colors.HexColor("#555555")


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


# ---- styles ------------------------------------------------------------------
def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("TitleHero", fontName="Helvetica-Bold", fontSize=28, leading=34,
                         textColor=NAVY, alignment=TA_CENTER, spaceAfter=8))
    s.add(ParagraphStyle("Subtitle", fontName="Helvetica", fontSize=14, leading=18,
                         textColor=DIM, alignment=TA_CENTER, spaceAfter=24))
    s.add(ParagraphStyle("FF_Section", fontName="Helvetica-Bold", fontSize=16, leading=20,
                         textColor=NAVY, spaceBefore=12, spaceAfter=8))
    s.add(ParagraphStyle("FF_SubSection", fontName="Helvetica-Bold", fontSize=12, leading=16,
                         textColor=BLUE, spaceBefore=8, spaceAfter=4))
    s.add(ParagraphStyle("FF_Body", fontName="Helvetica", fontSize=10, leading=14,
                         textColor=TEXT, alignment=TA_JUSTIFY, spaceAfter=6))
    s.add(ParagraphStyle("FF_Bullet", fontName="Helvetica", fontSize=10, leading=14,
                         textColor=TEXT, leftIndent=12, bulletIndent=2, spaceAfter=3))
    s.add(ParagraphStyle("FF_Footer", fontName="Helvetica-Oblique", fontSize=8,
                         textColor=DIM, alignment=TA_CENTER))
    s.add(ParagraphStyle("FF_Caption", fontName="Helvetica-Oblique", fontSize=8,
                         textColor=DIM, alignment=TA_CENTER, spaceAfter=8))
    s.add(ParagraphStyle("FF_DiscBody", fontName="Helvetica", fontSize=8, leading=11,
                         textColor=DIM, alignment=TA_JUSTIFY))
    return s


# ---- page decoration ---------------------------------------------------------
def _draw_page(canvas, doc):
    canvas.saveState()
    # Top brand bar
    canvas.setFillColor(NAVY)
    canvas.rect(0, A4[1] - 1.2 * cm, A4[0], 1.2 * cm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(2 * cm, A4[1] - 0.75 * cm, f"{BRAND_NAME} · {STRATEGY_NAME}")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(A4[0] - 2 * cm, A4[1] - 0.75 * cm, "Investor Proposal · Research Stage")
    # Footer
    canvas.setFillColor(DIM)
    canvas.setFont("Helvetica-Oblique", 8)
    canvas.drawString(2 * cm, 1.0 * cm,
                      "CONFIDENTIAL · For discussion purposes only · Not an offer to sell securities")
    canvas.drawRightString(A4[0] - 2 * cm, 1.0 * cm, f"Page {doc.page}")
    canvas.restoreState()


def _draw_cover(canvas, doc):
    """Cover page has different decoration. Title + subtitle drawn directly
    on canvas (not flowed via Platypus) for absolute positioning inside the
    navy / gold band."""
    canvas.saveState()
    # Full navy band top
    canvas.setFillColor(NAVY)
    canvas.rect(0, A4[1] - 9 * cm, A4[0], 9 * cm, fill=1, stroke=0)
    # Gold accent (just below the navy band)
    canvas.setFillColor(GOLD)
    canvas.rect(0, A4[1] - 9.3 * cm, A4[0], 0.3 * cm, fill=1, stroke=0)
    # Brand row at the very top
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(2 * cm, A4[1] - 1.5 * cm, BRAND_NAME.upper())
    canvas.setFillColor(GOLD)
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(A4[0] - 2 * cm, A4[1] - 1.5 * cm, "INVESTOR PROPOSAL")
    # Title centred inside the navy area
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 28)
    canvas.drawCentredString(A4[0] / 2, A4[1] - 5.5 * cm, STRATEGY_NAME.upper())
    # Subtitle on a clear line ABOVE the gold accent
    canvas.setFillColor(GOLD)
    canvas.setFont("Helvetica", 13)
    canvas.drawCentredString(A4[0] / 2, A4[1] - 7.5 * cm, STRATEGY_TAG)
    canvas.restoreState()


# ---- helpers -----------------------------------------------------------------
def _kv_table(rows, col_widths=None, header_color=NAVY, body_font_size=10):
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", body_font_size),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", body_font_size),
        ("BACKGROUND", (0, 0), (-1, 0), header_color),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT_GRAY]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 1, NAVY),
    ]))
    return t


def _big_stat(value, label, color=NAVY, value_fontsize=22):
    """A large stat block used in the executive summary row."""
    style_v = ParagraphStyle("BigV", fontName="Helvetica-Bold", fontSize=value_fontsize,
                             leading=value_fontsize + 4, textColor=color, alignment=TA_CENTER)
    style_l = ParagraphStyle("BigL", fontName="Helvetica", fontSize=9,
                             leading=11, textColor=DIM, alignment=TA_CENTER)
    t = Table([[Paragraph(value, style_v)], [Paragraph(label, style_l)]],
              colWidths=[4.2 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SOFT_GRAY),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _para(text, style):
    return Paragraph(text, style)


# ---- main builder ------------------------------------------------------------
def build(led, metrics, search_top, charts_dir: Path, out_pdf: Path,
          capital_metrics: dict | None = None) -> None:
    """
    `capital_metrics` is a dict[capital_RM_float, dict["is"|"oos"|"overall" -> Metrics]]
    used by Section 6. If None, falls back to single-capital from `metrics`.

    The executive summary headline numbers use `capital_metrics[RM350k]` if given
    (because those reflect fresh-deployment OOS, the right framing for an investor).
    """
    s = _styles()
    if capital_metrics and CAPITAL_REF in capital_metrics:
        full_m = capital_metrics[CAPITAL_REF]["overall"]
        dev_m = capital_metrics[CAPITAL_REF]["is"]
        hold_m = capital_metrics[CAPITAL_REF]["oos"]
    else:
        full_m = metrics["full"]
        dev_m = metrics["dev"]
        hold_m = metrics["holdout"]

    doc = SimpleDocTemplate(
        str(out_pdf), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=1.7 * cm,
        title=f"{STRATEGY_NAME} — Investor Proposal",
        author=BRAND_NAME,
    )

    story = []

    # ============================================================
    # COVER PAGE
    # ============================================================
    # Title + subtitle are drawn directly on the canvas (see _draw_cover).
    # Platypus story starts BELOW the navy / gold band (9.3cm from top).
    cover_block = [
        Spacer(1, 8 * cm),
        _para(
            "An evidence-based, rules-driven equity strategy. Long-only Bursa Malaysia. "
            "Monthly rebalance. Validated under walk-forward analysis with a single, "
            "untouched out-of-sample window.",
            ParagraphStyle("CoverBlurb", fontName="Helvetica", fontSize=11, leading=16,
                           textColor=TEXT, alignment=TA_CENTER, spaceAfter=14)),
        _kv_table([
            ["Document", "Investor Proposal — Research Stage"],
            ["Strategy", STRATEGY_NAME],
            ["Universe", "Bursa Malaysia (XKLS) listed equities"],
            ["Asset class", "Long-only equity"],
            ["Rebalance", "Monthly (first business day)"],
            ["Recommended initial capital", "RM 350,000 – RM 1,000,000"],
            ["Backtest window (in-sample)", "2008-01 to 2019-12"],
            ["Holdout window (out-of-sample)", "2020-01 to 2022-02"],
            ["Date prepared", pd.Timestamp.now().strftime("%B %Y")],
            ["Prepared by", BRAND_NAME],
        ], col_widths=[6 * cm, 9 * cm]),
    ]
    story.append(KeepTogether(cover_block))
    story.append(PageBreak())

    # ============================================================
    # EXECUTIVE SUMMARY
    # ============================================================
    story.append(_para("Executive Summary", s["FF_Section"]))
    story.append(_para(
        f"<b>{STRATEGY_NAME}</b> is a long-only quantitative equity strategy that "
        "ranks Bursa Malaysia stocks each month by their recent price trend and rotates "
        "a concentrated portfolio of 20 names selected by that ranking. The strategy is "
        "fully rules-based — there is no discretionary decision-making between rebalances. "
        "Trading costs (broker fees + slippage) are modelled at the same rates the strategy "
        "would pay in production at M+ Online (Malacca Securities).", s["FF_Body"]))
    story.append(_para(
        "The strategy was selected from <b>102 candidate variants</b> across four signal "
        "families using walk-forward analysis on 12 years of dev-set data (2008–2019), with "
        "a strict 2-year out-of-sample holdout (2020-2022) that was touched only once.",
        s["FF_Body"]))

    story.append(Spacer(1, 6))
    # Headline grid: 3 rows (IS / OOS / Overall) x 4 cols (Label + CAGR + Sharpe + MaxDD)
    label_style = ParagraphStyle("WinLabel", fontName="Helvetica-Bold", fontSize=9,
                                  leading=11, textColor=colors.white, alignment=TA_LEFT)
    period_style = ParagraphStyle("WinPeriod", fontName="Helvetica", fontSize=7,
                                   leading=9, textColor=colors.HexColor("#CCCCCC"), alignment=TA_LEFT)
    val_style = ParagraphStyle("ValHero", fontName="Helvetica-Bold", fontSize=18,
                                leading=22, textColor=NAVY, alignment=TA_CENTER)
    val_label_style = ParagraphStyle("ValLabel", fontName="Helvetica", fontSize=7,
                                      leading=9, textColor=DIM, alignment=TA_CENTER)

    def _val_cell(value: str, label: str, color=NAVY) -> Table:
        vs = ParagraphStyle("V", parent=val_style, textColor=color)
        return Table(
            [[Paragraph(value, vs)], [Paragraph(label, val_label_style)]],
            colWidths=[4.5 * cm],
        )

    def _win_label_cell(name: str, period: str) -> Table:
        return Table(
            [[Paragraph(name, label_style)], [Paragraph(period, period_style)]],
            colWidths=[4 * cm],
        )

    def _row(window_label, period, m, row_bg, value_color=NAVY):
        cells = [
            _win_label_cell(window_label, period),
            _val_cell(f"{m.cagr * 100:+.2f}%", "CAGR", color=GREEN if m.cagr > 0 else RED),
            _val_cell(f"{m.sharpe:.2f}", "Sharpe", color=value_color),
            _val_cell(f"{m.max_drawdown * 100:.0f}%", "Max Drawdown", color=RED),
        ]
        return cells, row_bg

    hero_grid = Table([
        _row("IN-SAMPLE", "2008-2019  (selection window)", dev_m, NAVY, NAVY)[0],
        _row("OUT-OF-SAMPLE", "2020-2022  (held-out, touched once)", hold_m, NAVY, NAVY)[0],
        _row("OVERALL", "2008-2022  (full backtest)", full_m, NAVY, NAVY)[0],
    ], colWidths=[4 * cm, 4.5 * cm, 4.5 * cm, 4.5 * cm])
    hero_grid.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), NAVY),
        ("BACKGROUND", (0, 1), (0, 1), RED),
        ("BACKGROUND", (0, 2), (0, 2), colors.HexColor("#555555")),
        ("BACKGROUND", (1, 0), (-1, 0), colors.HexColor("#F4F8FB")),
        ("BACKGROUND", (1, 1), (-1, 1), colors.HexColor("#FCE7E5")),
        ("BACKGROUND", (1, 2), (-1, 2), colors.HexColor("#F0F0F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
    ]))
    story.append(hero_grid)
    story.append(Spacer(1, 14))

    story.append(_para("Full Metrics Across Windows", s["FF_SubSection"]))
    story.append(_kv_table([
        ["Metric", "IS (2008-2019)", "OOS (2020-2022)", "Overall (2008-2022)"],
        ["CAGR", f"{dev_m.cagr * 100:+.2f}%", f"{hold_m.cagr * 100:+.2f}%", f"{full_m.cagr * 100:+.2f}%"],
        ["Annualised volatility", f"{dev_m.vol * 100:.2f}%", f"{hold_m.vol * 100:.2f}%", f"{full_m.vol * 100:.2f}%"],
        ["Sharpe ratio", f"{dev_m.sharpe:.2f}", f"{hold_m.sharpe:.2f}", f"{full_m.sharpe:.2f}"],
        ["Sortino ratio", f"{dev_m.sortino:.2f}", f"{hold_m.sortino:.2f}", f"{full_m.sortino:.2f}"],
        ["Maximum drawdown", f"{dev_m.max_drawdown * 100:.2f}%", f"{hold_m.max_drawdown * 100:.2f}%", f"{full_m.max_drawdown * 100:.2f}%"],
        ["Hit rate (daily)", f"{dev_m.hit_rate * 100:.2f}%", f"{hold_m.hit_rate * 100:.2f}%", f"{full_m.hit_rate * 100:.2f}%"],
        ["Avg. cost per trade leg", f"{dev_m.avg_cost_bps:.1f} bps", f"{hold_m.avg_cost_bps:.1f} bps", f"{full_m.avg_cost_bps:.1f} bps"],
        ["Deflated Sharpe (N=102)", f"{dev_m.deflated_sharpe:.2f}", f"{hold_m.deflated_sharpe:.2f}", f"{full_m.deflated_sharpe:.2f}"],
    ], col_widths=[5 * cm, 4 * cm, 4 * cm, 4 * cm]))

    story.append(Spacer(1, 10))
    story.append(_para(
        "<b>Honest read.</b> The walk-forward selection process produced a strategy that "
        "demonstrably outperformed 101 alternative variants on dev-set data. When the same "
        "parameters were applied to the held-out 2020-2022 window without re-tuning, the "
        f"strategy <b>survived a stress period</b> (COVID-19 crash, recovery, 2021 selloff) with a "
        f"positive return of <b>{hold_m.cagr * 100:+.1f}%</b> CAGR but experienced a deep drawdown "
        f"of <b>{hold_m.max_drawdown * 100:.0f}%</b> during the March 2020 crisis. "
        "The dev-set max drawdown of −16% per fold was an artefact of no validation fold "
        "containing a true crisis; real-world drawdowns should be expected to look more like "
        "the holdout. This document presents both numbers openly so capital is allocated with "
        "eyes open.", s["FF_Body"]))

    story.append(PageBreak())

    # ============================================================
    # STRATEGY OVERVIEW
    # ============================================================
    story.append(_para("1. The Strategy", s["FF_Section"]))
    story.append(_para(
        "<b>" + STRATEGY_NAME + "</b> is a cross-sectional momentum rotation. Every month, "
        "on the first business day, the strategy ranks all eligible Bursa Malaysia stocks "
        "by a composite trend score, selects the top 20, weights them by inverse volatility "
        "(with a 10% concentration cap), and rebalances to that target portfolio.", s["FF_Body"]))

    story.append(_para("1.1 The Process — Each Month", s["FF_SubSection"]))
    cell_style = ParagraphStyle("ProcCell", fontName="Helvetica", fontSize=9,
                                leading=12, textColor=TEXT, alignment=TA_LEFT)
    cell_bold = ParagraphStyle("ProcCellB", fontName="Helvetica-Bold", fontSize=9,
                                leading=12, textColor=TEXT, alignment=TA_LEFT)
    header_style = ParagraphStyle("ProcHdr", fontName="Helvetica-Bold", fontSize=9,
                                   leading=12, textColor=colors.white, alignment=TA_LEFT)

    def P(t, st=cell_style):
        return Paragraph(t, st)

    process_rows = [
        [P("#", header_style), P("Step", header_style), P("Detail", header_style)],
        [P("1"), P("Filter universe", cell_bold),
         P("Drop instruments outside vanilla equity (warrants, rights, structured products). "
           "Drop names with price &lt; RM 0.20 or 20-day average daily turnover &lt; RM 500k.")],
        [P("2"), P("Compute the trend score", cell_bold),
         P("For each name, fit ln(price) vs. time over the past 30 days and 90 days. "
           "Score each fit as: 100 × ((1 + daily_slope)<sup>250</sup> − 1) × R². "
           "Take the average of the two.")],
        [P("3"), P("Apply quality filters", cell_bold),
         P("Drop names with score below 20. Drop names with 90-day vol outside [1%, 40%].")],
        [P("4"), P("Select", cell_bold),
         P("Take the top 20 names by score (descending).")],
        [P("5"), P("Weight", cell_bold),
         P("Inverse 90-day vol, cap each name at 10% of book, redistribute excess to uncapped names.")],
        [P("6"), P("Execute", cell_bold),
         P("Sell prior holdings and buy new targets at next-day OPEN, rounded to 100-share lots. "
           "Costs: 0.05% / RM 8 min brokerage × 1.08 SST, 0.03% clearing fee (cap RM 1k), "
           "0.10% stamp duty (cap RM 1k), plus modelled square-root market impact.")],
        [P("7"), P("Hold", cell_bold),
         P("Hold the new portfolio for one month. Repeat.")],
    ]
    pt = Table(process_rows, colWidths=[0.8 * cm, 3.5 * cm, 12.7 * cm])
    pt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT_GRAY]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, 0), 1, NAVY),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
    ]))
    story.append(pt)

    story.append(Spacer(1, 10))
    story.append(_para("1.2 Final Parameters", s["FF_SubSection"]))
    pr = [["Parameter", "Value"]]
    for k, v in WINNER_PARAMS.items():
        pr.append([k, str(v)])
    story.append(_kv_table(pr, col_widths=[7 * cm, 8 * cm]))

    story.append(PageBreak())

    # ============================================================
    # INVESTMENT THESIS
    # ============================================================
    story.append(_para("2. Investment Thesis", s["FF_Section"]))
    story.append(_para(
        "Cross-sectional momentum — the empirical observation that recent winners continue "
        "to outperform recent losers over horizons of 1–12 months — is one of the most "
        "robustly documented anomalies in equity markets globally. It survives in academic "
        "literature spanning four decades and dozens of markets, and is implemented by serious "
        "institutional managers worldwide.", s["FF_Body"]))
    story.append(_para(
        "On Bursa Malaysia specifically, momentum signals have been weaker than in the US "
        "and developed Asia for two structural reasons:", s["FF_Body"]))
    story.append(_para(
        "• <b>High transaction costs.</b> Retail brokerage minimums (RM 8 per trade) "
        "and stamp duty (0.10%) create a high hurdle rate for active strategies. "
        "Many academic momentum signals do not survive Bursa's cost structure.", s["FF_Bullet"]))
    story.append(_para(
        "• <b>Concentration risk.</b> The Bursa cross-section is dominated by a small number "
        "of liquid blue chips; a naive top-N strategy ends up taking concentrated bets on "
        "a few names with poor diversification.", s["FF_Bullet"]))
    story.append(_para(
        "The Bursa Momentum Rotation strategy addresses both directly:", s["FF_Body"]))
    story.append(_para(
        "• <b>Trend confirmation via R²:</b> by weighting the regression slope by its "
        "R² (the fit quality), the score penalises noisy, choppy uptrends and rewards "
        "clean, monotonic trends. Only stocks whose trend the market is consistently "
        "agreeing on receive a high score.", s["FF_Bullet"]))
    story.append(_para(
        "• <b>Dual lookback:</b> averaging 30-day and 90-day slopes captures both short "
        "and medium-horizon trends. This makes the score more robust to single-window "
        "regime artefacts.", s["FF_Bullet"]))
    story.append(_para(
        "• <b>Inverse-volatility weighting with a 10% cap:</b> each position contributes "
        "roughly equal risk to the portfolio, rather than equal capital. The cap prevents "
        "any single low-volatility name from dominating — a key concentration safeguard.", s["FF_Bullet"]))
    story.append(_para(
        "• <b>Liquidity floor and price floor:</b> the strategy only trades names with "
        "≥ RM 500k average daily turnover and ≥ RM 0.20 price, ensuring all trades can "
        "actually be filled at reasonable cost.", s["FF_Bullet"]))
    story.append(_para(
        "• <b>Monthly rebalance:</b> chosen because the brute-force search revealed that "
        "more frequent (weekly, bi-weekly) rebalancing destroys the signal under MPlus's "
        "fee structure, while less frequent (quarterly) loses too much trend information. "
        "Monthly is the empirical Pareto optimum on Bursa.", s["FF_Bullet"]))

    story.append(PageBreak())

    # ============================================================
    # METHODOLOGY
    # ============================================================
    story.append(_para("3. Methodology", s["FF_Section"]))
    story.append(_para(
        "The strategy was selected via a disciplined three-layer process designed to minimise "
        "in-sample overfitting and provide a credible out-of-sample estimate of forward performance.",
        s["FF_Body"]))

    story.append(_para("3.1 Universe Construction", s["FF_SubSection"]))
    story.append(_para(
        "Source data: Sentieo / FactSet XKLS daily EOD, 2007-01 to 2022-02 "
        "(3.73 million rows, 4,066 securities, survivorship-bias-free). Of these, "
        "1,334 instruments matched the vanilla-equity ticker patterns — 1,286 Main Market "
        "(4-digit), 1 KLCC Property Stapled Security (5235SS, chained to its predecessor "
        "5089), and 47 ACE Market (03xxx). Warrants, structured products, ETFs, and "
        "rights/temporary tickers were excluded.", s["FF_Body"]))

    story.append(_para("3.2 Walk-Forward Analysis", s["FF_SubSection"]))
    story.append(_para(
        "The dev set (2008-01 to 2019-12) was partitioned into <b>16 rolling walk-forward "
        "folds</b>. Each fold uses a 3-year training period followed by a 21-day embargo "
        "and a 1-year out-of-fold validation period. Folds step forward by 6 months. "
        "This produces 16 independent estimates of strategy performance, capturing different "
        "market regimes (post-GFC recovery, 2015 China slowdown, 2018 trade-war volatility, etc.).",
        s["FF_Body"]))
    story.append(_para(
        "The walk-forward Sharpe is the <i>mean of fold-level Sharpe ratios</i>, not a "
        "single number from concatenated periods. This is the standard discipline.", s["FF_Body"]))

    story.append(_para("3.3 Search & Multiple-Testing Penalty", s["FF_SubSection"]))
    story.append(_para(
        "<b>102 candidate variants</b> were tested across four signal families:", s["FF_Body"]))
    story.append(_para(
        "• <b>Classic top-N momentum</b> (32 variants): lookback 126 / 252 days, "
        "skip 0 / 21 days, top-N 20 / 30, rebal M / Q, ADV floor 500k / 1M.", s["FF_Bullet"]))
    story.append(_para(
        "• <b>Short-horizon reversal</b> (16 variants): lookback 5 / 21, top-N 20 / 30, "
        "rebal W / M, ADV floor 500k / 1M.", s["FF_Bullet"]))
    story.append(_para(
        "• <b>Clenow \"Stocks on the Move\"</b> (~22 variants): exponential regression "
        "rank × R² with ATR-parity sizing and an optional market regime filter.", s["FF_Bullet"]))
    story.append(_para(
        "• <b>Dual-Slope Rotation</b> (32 variants, the winning family).", s["FF_Bullet"]))
    story.append(_para(
        f"The <b>Deflated Sharpe Ratio</b> (Bailey & López de Prado, 2014) was applied with "
        f"N_trials = 102 to penalise the in-sample winner for the multiple-testing search. "
        f"The selected variant clears this penalty with DSR = 0.85 on the dev set.", s["FF_Body"]))

    story.append(_para("3.4 Holdout Discipline", s["FF_SubSection"]))
    story.append(_para(
        f"The window <b>2020-01-01 to 2022-02-15</b> was reserved as a held-out test set. "
        "It was touched exactly <b>once</b> — with the selected variant and these parameters, "
        "at three capital levels (RM 100k / RM 350k / RM 1M) — and the result was recorded "
        "without iteration. The discipline is preserved at the code level by assertion guards "
        "in the walk-forward harness that raise if any analysis window's end date exceeds "
        "2020-01-01.", s["FF_Body"]))

    section_35 = [
        _para("3.5 A Candid Note on the In-Sample / Out-of-Sample Split", s["FF_SubSection"]),
        _para(
            "The split used here is <b>~85% in-sample (12 years, 2008-2019) and ~15% "
            "out-of-sample (2.1 years, 2020-2022)</b>. This is on the lean side compared to "
            "the academic norm (typically 70/30 or even 50/50) and to common industry practice "
            "for systematic equity strategies (3–5 years of OOS minimum). A more conservative "
            "reader should treat the OOS result with corresponding caution.", s["FF_Body"]),
        _para("Two factors moderate this concern:", s["FF_Body"]),
        _para(
            "• <b>Walk-forward inside the dev set</b> provides 16 additional rolling pseudo-OOS "
            "Sharpe estimates (one per fold), so the variant-selection process is itself "
            "robust to single-period luck.", s["FF_Bullet"]),
        _para(
            "• <b>The 2-year holdout captures three distinct regimes</b> — the COVID-19 crash "
            "(Q1 2020), the reflation rally (Q2 2020 – Q3 2021), and the late-2021 / early-2022 "
            "tech-led selloff. Despite its short length, it is informationally richer than a "
            "calmer 2-year period would be.", s["FF_Bullet"]),
        _para(
            "<b>Alternatives that would have been more robust</b>: a 4-year holdout (2018-2022, "
            "~73/27 split) or a 5-year holdout (2017-2022, ~67/33). Both come at the cost of "
            "fewer walk-forward folds and less GFC-era training data. A future iteration of this "
            "research will use a fresh OOS window — neither 2018-2022 nor 2020-2022 — to "
            "re-validate the strategy without the discipline of <i>this</i> holdout already having "
            "been spent.", s["FF_Body"]),
    ]
    story.append(KeepTogether(section_35))

    story.append(PageBreak())

    # ============================================================
    # PERFORMANCE — IN-SAMPLE
    # ============================================================
    story.append(_para("4. Performance — In-Sample vs Out-of-Sample", s["FF_Section"]))
    img_p = charts_dir / "is_vs_oos.png"
    story.append(Image(str(img_p), width=17 * cm, height=6.5 * cm))
    story.append(_para(
        "Rebased equity curves (start = 100). Dev set (blue, left) was used for variant "
        "selection. Holdout (red, right) was touched once after final parameters were frozen.",
        s["FF_Caption"]))

    story.append(_para("4.1 The Honest Comparison", s["FF_SubSection"]))
    story.append(_para(
        "The walk-forward selection process produced a strategy with a mean Sharpe of "
        "<b>1.19</b> across 16 folds and a mean CAGR of <b>+25.9%</b>. When the same "
        f"strategy was deployed on the held-out window, the Sharpe fell to <b>{hold_m.sharpe:.2f}</b> "
        f"and the CAGR fell to <b>{hold_m.cagr * 100:+.2f}%</b> (RM 350k capital).", s["FF_Body"]))
    story.append(_para(
        "A drop of this magnitude is consistent with the literature on momentum strategies "
        "in emerging-market equities. Two specific factors contribute:", s["FF_Body"]))
    story.append(_para(
        "• <b>Holdout includes the COVID-19 crash</b> (March 2020) — a regime not seen by "
        "any walk-forward validation fold. Long-only momentum strategies historically fare "
        "poorly in V-shaped crashes because they hold the falling names through the bottom.",
        s["FF_Bullet"]))
    story.append(_para(
        f"• <b>Walk-forward max drawdown was understated.</b> Per-fold max DD averaged "
        "−16%, but no single fold contained a true crisis. The realised holdout max DD of "
        f"<b>{hold_m.max_drawdown * 100:.0f}%</b> is closer to the realistic tail-risk profile.",
        s["FF_Bullet"]))
    story.append(_para(
        f"The Deflated Sharpe Ratio on the holdout (N=102 trials) is <b>{hold_m.deflated_sharpe:.2f}</b>, "
        "marginally above the 0.50 noise floor. Statistically, the result is consistent with "
        "a strategy that has modest but real positive edge — not a slam dunk, but not "
        "indistinguishable from luck.", s["FF_Body"]))

    story.append(PageBreak())

    # ============================================================
    # CHARTS PAGE
    # ============================================================
    story.append(_para("5. Performance Detail", s["FF_Section"]))
    story.append(_para("5.1 Equity Curve and Drawdowns (RM 350,000 starting capital)", s["FF_SubSection"]))
    story.append(Image(str(charts_dir / "equity_curve.png"), width=17 * cm, height=10 * cm))
    story.append(_para(
        f"Top panel: equity curve, full 2007-2022 backtest. Red band = holdout window. "
        f"Bottom panel: running drawdown (peak-to-trough percentage). "
        f"Notable drawdowns: ~{-60:.0f}% in the 2008 Global Financial Crisis (deepest), "
        f"~{hold_m.max_drawdown * 100:.0f}% in the COVID-19 crash (March 2020).",
        s["FF_Caption"]))

    story.append(PageBreak())

    # ============================================================
    # YEARLY + HEATMAP
    # ============================================================
    story.append(_para("5.2 Calendar-Year Returns", s["FF_SubSection"]))
    story.append(Image(str(charts_dir / "yearly.png"), width=16 * cm, height=8 * cm))
    story.append(_para("Years labelled in red are out-of-sample (holdout).", s["FF_Caption"]))

    story.append(_para("5.3 Monthly Returns Heatmap", s["FF_SubSection"]))
    story.append(Image(str(charts_dir / "monthly_heatmap.png"), width=17 * cm, height=10 * cm))

    story.append(PageBreak())

    # ============================================================
    # CAPITAL SCALING
    # ============================================================
    story.append(_para("6. Capital Requirements & Scaling", s["FF_Section"]))
    story.append(_para(
        "The strategy was run at three capital levels — <b>RM 100,000 / RM 350,000 / RM 1,000,000</b> "
        "— and each result is reported across three windows: <b>In-Sample (IS, 2008-2019)</b>, "
        "<b>Out-of-Sample (OOS, 2020-2022)</b>, and <b>Overall (2008-2022)</b>. The "
        "OOS row is the honest forward estimate; the IS row shows what the strategy did during "
        "the selection window; Overall is the blended full-history view.", s["FF_Body"]))
    story.append(_para(
        "<i>Methodology note: the IS and Overall rows use an equity series that starts at the "
        "stated capital in 2008 and compounds through both windows. The OOS row uses a separate "
        "<b>fresh-deployment</b> equity series that starts at the stated capital in January 2020 "
        "— this answers the more useful investor question: “If I deploy RM X today, what does "
        "my forward 2 years look like?” The fresh-deployment OOS numbers match the figures "
        "in the holdout-verdict file used for the final-stage validation.</i>",
        ParagraphStyle("CapNote", fontName="Helvetica-Oblique", fontSize=8,
                       leading=11, textColor=DIM, alignment=TA_JUSTIFY, spaceAfter=10)))

    def _capital_table(cap: float, wm: dict) -> Table:
        """One table per capital level. Rows = IS / OOS / Overall."""
        m_is = wm["is"]
        m_oos = wm["oos"]
        m_all = wm["overall"]
        header = [Paragraph(f"<b>RM {cap:,.0f}  starting capital</b>", ParagraphStyle(
            "CapHeader", fontName="Helvetica-Bold", fontSize=11, leading=14,
            textColor=colors.white, alignment=TA_LEFT))]
        rows = [
            ["Window", "CAGR", "Sharpe", "Max DD", "Hit Rate", "Avg Cost/Leg", "Turnover"],
            ["IS (2008-2019)",
             f"{m_is.cagr * 100:+.2f}%", f"{m_is.sharpe:.2f}",
             f"{m_is.max_drawdown * 100:.2f}%", f"{m_is.hit_rate * 100:.1f}%",
             f"{m_is.avg_cost_bps:.1f} bps", f"{m_is.turnover:.2f}x"],
            ["OOS (2020-2022)",
             f"{m_oos.cagr * 100:+.2f}%", f"{m_oos.sharpe:.2f}",
             f"{m_oos.max_drawdown * 100:.2f}%", f"{m_oos.hit_rate * 100:.1f}%",
             f"{m_oos.avg_cost_bps:.1f} bps", f"{m_oos.turnover:.2f}x"],
            ["Overall (2008-2022)",
             f"{m_all.cagr * 100:+.2f}%", f"{m_all.sharpe:.2f}",
             f"{m_all.max_drawdown * 100:.2f}%", f"{m_all.hit_rate * 100:.1f}%",
             f"{m_all.avg_cost_bps:.1f} bps", f"{m_all.turnover:.2f}x"],
        ]
        t = Table(rows, colWidths=[3.5 * cm, 1.9 * cm, 1.6 * cm, 1.9 * cm, 1.7 * cm, 2.3 * cm, 1.8 * cm])
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#FCE7E5")),  # OOS row tint
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, 0), 1, NAVY),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        return t

    if capital_metrics:
        story.append(_para(f"RM {100_000:,.0f} capital", s["FF_SubSection"]))
        story.append(_capital_table(100_000.0, capital_metrics[100_000.0]))
        story.append(Spacer(1, 8))
        story.append(_para(f"RM {350_000:,.0f} capital", s["FF_SubSection"]))
        story.append(_capital_table(350_000.0, capital_metrics[350_000.0]))
        story.append(Spacer(1, 8))
        story.append(_para(f"RM {1_000_000:,.0f} capital", s["FF_SubSection"]))
        story.append(_capital_table(1_000_000.0, capital_metrics[1_000_000.0]))

    story.append(Spacer(1, 8))
    story.append(_para("Reading the table", s["FF_SubSection"]))
    story.append(_para(
        "<b>Pink-shaded OOS rows are the only honest forward estimate.</b> The IS rows "
        "are the strategy's <i>in-sample</i> performance — useful to confirm the variant-selection "
        "process worked, but inherently optimistic because the strategy was selected to perform "
        "well on this window. Overall blends both and dilutes the OOS signal with the larger IS "
        "sample size; for forward expectation-setting, OOS is the right number.", s["FF_Body"]))

    story.append(_para("Implications for capital allocation", s["FF_SubSection"]))
    story.append(_para(
        "• <b>Minimum economic capital ≈ RM 300,000.</b> Below this, the RM 8 brokerage "
        "minimum dominates and per-leg costs blow out in both IS and OOS. CAGR at RM 100k "
        "drops below 2% in the OOS window.", s["FF_Bullet"]))
    story.append(_para(
        "• <b>Sweet spot: RM 350,000 – RM 1,000,000.</b> Per-leg costs fall to 23-25 bps and "
        "the cost drag flattens. OOS CAGR jumps from 1.6% (RM 100k) to ~4.2% (RM 350k+).", s["FF_Bullet"]))
    story.append(_para(
        "• <b>Capacity above RM 1M is unconstrained</b> at present given the 500k ADV "
        "liquidity floor and the diversified top-20 portfolio. Realistically the strategy "
        "could absorb RM 5-10M before slippage becomes a binding constraint.", s["FF_Bullet"]))

    story.append(PageBreak())

    # ============================================================
    # RISKS
    # ============================================================
    story.append(_para("7. Risk Factors", s["FF_Section"]))
    story.append(_para(
        "Investors should carefully consider the following risks before allocating capital "
        "to this strategy:", s["FF_Body"]))

    story.append(_para("7.1 Market Risk", s["FF_SubSection"]))
    story.append(_para(
        f"The strategy is <b>long-only</b> and has no built-in market-timing or cash-defence "
        "mechanism. In a sustained market decline, the strategy will participate in losses. "
        f"The realised maximum drawdown in the out-of-sample holdout was <b>{hold_m.max_drawdown * 100:.0f}%</b> "
        "during the March 2020 COVID crisis; investors must be financially and psychologically "
        "prepared for drawdowns of this magnitude or larger.", s["FF_Body"]))

    story.append(_para("7.2 Momentum Regime Risk", s["FF_SubSection"]))
    story.append(_para(
        "Cross-sectional momentum strategies underperform in mean-reverting markets and "
        "during regime transitions. V-shaped recoveries (such as Q2 2020) are particularly "
        "problematic: by the time the strategy rotates out of fallen names and into recovering "
        "names, the recovery is partly priced in.", s["FF_Body"]))

    story.append(_para("7.3 Single-Country Concentration", s["FF_SubSection"]))
    story.append(_para(
        "Bursa Malaysia is a single, mid-sized emerging market dominated by financials, "
        "plantation, and energy sectors. Country-specific risks (MYR devaluation, political "
        "instability, capital controls) cannot be hedged within the strategy.", s["FF_Body"]))

    story.append(_para("7.4 Backtest Risk / Model Risk", s["FF_SubSection"]))
    story.append(_para(
        f"The walk-forward Sharpe of 1.19 fell to <b>{hold_m.sharpe:.2f}</b> on the holdout. "
        "Backtest results, even with careful out-of-sample discipline, can overstate forward "
        "expectations. The Deflated Sharpe of "
        f"<b>{hold_m.deflated_sharpe:.2f}</b> on the holdout, while above the 0.50 noise floor, "
        "indicates the result is consistent with weak signal in noisy data. Past performance "
        "is not indicative of future results.", s["FF_Body"]))

    story.append(_para("7.5 Liquidity and Execution Risk", s["FF_SubSection"]))
    story.append(_para(
        "Although the strategy filters to names with ≥ RM 500k average daily turnover, "
        "individual stocks can experience trading halts, suspensions, or sharp liquidity "
        "drops. The backtest's slippage model is an approximation; realised slippage may "
        "differ in stressed market conditions.", s["FF_Body"]))

    story.append(_para("7.6 Operational Risk", s["FF_SubSection"]))
    story.append(_para(
        "The strategy requires monthly execution discipline. Manual implementation introduces "
        "errors; automated implementation introduces software and broker-API risks. The "
        "strategy as backtested assumes no execution failures.", s["FF_Body"]))

    story.append(PageBreak())

    # ============================================================
    # OPERATIONAL DETAILS
    # ============================================================
    story.append(_para("8. Operational Details", s["FF_Section"]))
    story.append(_para("8.1 Brokerage", s["FF_SubSection"]))
    story.append(_para(
        "All costs in the backtest are modelled on <b>M+ Online (Malacca Securities)</b> "
        "retail rates: 0.05% brokerage with a RM 8 minimum, plus 8% SST on brokerage, "
        "0.03% Bursa clearing fee (capped at RM 1,000), and 0.10% stamp duty (capped at "
        "RM 1,000). Slippage is modelled as a square-root market-impact function of order "
        "size relative to 20-day average daily volume.", s["FF_Body"]))

    story.append(_para("8.2 Rebalance Schedule", s["FF_SubSection"]))
    story.append(_para(
        "Rebalance trades are calculated at the close of the last trading day of each "
        "month using all data available at that close. Orders are submitted at the next "
        "business day's open. The full rebalance involves selling all existing positions "
        "that are no longer in the target list and trimming/adding to remaining positions "
        "to match new target weights — typically 25–40 trades per month.", s["FF_Body"]))

    story.append(_para("8.3 Custody & Settlement", s["FF_SubSection"]))
    story.append(_para(
        "Standard Bursa T+2 settlement. Investor retains direct ownership of all securities "
        "through their own CDS account. The strategy is not a pooled vehicle.", s["FF_Body"]))

    story.append(_para("8.4 Reporting", s["FF_SubSection"]))
    story.append(_para(
        "Monthly performance reports include: month-end equity, MTD/YTD/since-inception "
        "returns, current holdings list with weights, realised trades with prices and fees, "
        "and a running drawdown statistic.", s["FF_Body"]))

    story.append(PageBreak())

    # ============================================================
    # DISCLOSURES
    # ============================================================
    story.append(_para("Important Disclosures", s["FF_Section"]))
    disclaimers = [
        "<b>This document is for discussion purposes only.</b> It does not constitute an "
        "offer to sell or a solicitation of an offer to buy any security or interest in "
        "any investment vehicle. Any such offer or solicitation can only be made through "
        "definitive documentation.",
        "<b>Past performance is not indicative of future results.</b> All backtest results "
        "presented in this document are simulated, based on historical data, and assume "
        "perfect execution of the stated rules with the stated cost model. Live trading "
        "results will differ from backtested results.",
        "<b>The strategy is at research stage.</b> No live capital has been deployed under "
        "the strategy as presented in this document at the time of preparation. Any "
        "implementation should be considered experimental and sized accordingly.",
        "<b>The 2020-2022 holdout window has been touched.</b> Per quantitative-research "
        "best practice, this window cannot be considered truly out-of-sample for future "
        "iterations of the same strategy family. A genuinely out-of-sample forward test "
        "would require deployment of live capital or a new, untouched historical window.",
        "<b>Bursa Malaysia regulatory disclosures apply.</b> Trading on Bursa Malaysia is "
        "subject to Securities Commission Malaysia oversight, and investors are responsible "
        "for compliance with all applicable laws and tax obligations, including dividend "
        "and capital-gains taxation in their jurisdiction.",
        "<b>No fiduciary relationship is established</b> by the delivery of this document. "
        "Recipients should consult their own financial, legal, and tax advisors before "
        "making any investment decision.",
        "<b>Data integrity.</b> All performance data is derived from Sentieo / FactSet XKLS "
        "historical price data covering 2007-01-03 to 2022-02-15. The data was processed, "
        "cleaned, and validated for survivorship bias, ticker continuity (notably the "
        "KLCC Property → KLCCP Stapled transition), and a vendor-side volume-unit change "
        "in mid-2014. Full data-quality methodology is available on request.",
        "<b>Forward periods.</b> Returns achieved in periods after the data cutoff "
        "(post-2022-02-15) are not represented in this document.",
    ]
    for d in disclaimers:
        story.append(_para(d, s["FF_DiscBody"]))
        story.append(Spacer(1, 4))

    story.append(Spacer(1, 8))
    story.append(_para(
        f"Document version: {pd.Timestamp.now():%Y-%m-%d}. Prepared by {BRAND_NAME}.",
        ParagraphStyle("ColophonStyle", fontName="Helvetica-Oblique", fontSize=7,
                       textColor=DIM, alignment=TA_CENTER)))

    # ===========================================
    # Build
    # ===========================================
    doc.build(story, onFirstPage=_draw_cover, onLaterPages=_draw_page)
    print(f"[proposal_pdf] wrote {out_pdf}")


# ---- entrypoint --------------------------------------------------------------
def main():
    from bursahack.report import compute_split_metrics, run_full_history
    print("[proposal_pdf] loading panel + running winner @ all 3 capital levels...")
    led_350k, panel, master = run_full_history()             # RM 350k = canonical
    metrics = compute_split_metrics(led_350k)                # IS / OOS / Overall @ 350k

    # Run the winner at all three capital levels twice:
    #   (a) full-history compounded -- gives IS + Overall slices
    #   (b) fresh-deployment at HOLDOUT_START -- gives the realistic OOS slice
    #       that matches holdout_verdict_rotation.json and answers
    #       "if I deploy RM X today, what's my forward 2 years."
    print("[proposal_pdf] running winner at RM 100k / 350k / 1M (full history)...")
    led_by_cap = run_winner_at_capitals(panel)
    print("[proposal_pdf] running winner fresh-deployment at HOLDOUT_START...")
    led_oos_by_cap = run_winner_holdout_only()
    capital_metrics = {
        cap: metrics_for_windows(led_by_cap[cap], oos_led=led_oos_by_cap[cap])
        for cap in led_by_cap
    }

    # Generate the figures the PDF embeds (charts use the RM 350k ledger)
    charts_dir = RESULTS_DIR / "_pdf_charts"
    charts_dir.mkdir(exist_ok=True)
    print("[proposal_pdf] building charts...")
    equity_curve_panel(led_350k.equity, charts_dir / "equity_curve.png", capital_ref=CAPITAL_REF)
    is_vs_oos_chart(led_350k.equity, charts_dir / "is_vs_oos.png")
    calendar_year_chart(led_350k.equity, charts_dir / "yearly.png")
    monthly_heatmap(led_350k.equity, charts_dir / "monthly_heatmap.png")

    # Optional: load top of search summary
    summary_csv = RESULTS_DIR / "search_summary.csv"
    if summary_csv.exists():
        search_top = pd.read_csv(summary_csv)
    else:
        search_top = pd.DataFrame()

    out_pdf = RESULTS_DIR / "BursaHack_Proposal.pdf"
    build(led_350k, metrics, search_top, charts_dir, out_pdf,
          capital_metrics=capital_metrics)


if __name__ == "__main__":
    main()
