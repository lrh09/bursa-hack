"""build_data.py - emit web/data/*.json from results/.

Reads:
  results/final_scorecards.csv          (top-K with gate values)
  results/search_summary.csv            (fold-aggregated, all 186 variants)
  results/search_log.jsonl              (2,966 per-fold backtests)
  results/holdout_verdict_rotation.json (rotation OOS metrics at 3 capitals)
  results/rotation_winner_equity.csv    (rotation equity, RM 350k, 2007-2022)
  results/rotation_winner_trades.csv    (rotation per-trade audit log)
  results/clenow_winner_equity*.csv     (clenow equity, 3 capitals, 2007-2022)
  results/clenow_winner_trades.csv      (clenow per-trade audit log)
  results/SCORECARD_rotation.md         (raw scorecard with diagnostics)
  results/FINAL_REPORT.md
  results/REPORT.md
  DEPLOYMENT_FRAMEWORK.md               (gate definitions + thresholds)

Emits everything under web/data/. Idempotent; safe to re-run.
"""
from __future__ import annotations

import csv
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"
OUT = REPO / "web" / "data"

# -------------------------------------------------------------------- defs --

GATE_DEFS: dict[int, dict] = {
    1: {
        "name": "PBO",
        "fullname": "Probability of Backtest Overfitting",
        "blurb": "Bailey & Lopez de Prado's combinatorial test for selection bias. Quantifies the chance that the winning variant looks good only by luck across many trials.",
        "threshold_text": "<= 0.30",
        "good_when": "lt",
    },
    2: {
        "name": "Deflated Sharpe (eff-N)",
        "fullname": "Effective-N deflated Sharpe ratio",
        "blurb": "Sharpe deflated for the effective number of independent trials. Survives both multiple testing and non-normal returns.",
        "threshold_text": ">= 0.65",
        "good_when": "gte",
    },
    3: {
        "name": "OOS Sharpe (net)",
        "fullname": "Out-of-sample net Sharpe",
        "blurb": "Sharpe on the 2020-2022 holdout window, net of all fees and slippage. Touched once and only once.",
        "threshold_text": ">= 0.30",
        "good_when": "gte",
    },
    4: {
        "name": "Param stability",
        "fullname": "Parameter neighbourhood stability",
        "blurb": "How much Sharpe degrades when you wiggle the params 1 step in every direction. Cliff edges = fragile alpha.",
        "threshold_text": "within +/- 25%",
        "good_when": "abs_le",
    },
    5: {
        "name": "Fold-Sharpe CoV",
        "fullname": "Coefficient of variation across walk-forward folds",
        "blurb": "Sharpe varies fold to fold. If the CoV is too high the strategy is winning some folds badly and losing others - not a stable edge.",
        "threshold_text": "<= 1.0",
        "good_when": "lte",
    },
    6: {
        "name": "Slippage drag",
        "fullname": "Share of gross Sharpe lost to costs",
        "blurb": "Bursa Malaysia's retail-bracket fees (MPlus 0.05% + 1.08% SST + 0.03% clearing + 0.10% stamp + sqrt-impact slippage) eat into gross alpha. If costs eat more than 30% of gross, the strategy isn't built for retail capital.",
        "threshold_text": "<= 30%",
        "good_when": "lte",
    },
    7: {
        "name": "Avg order vs broker floor",
        "fullname": "Average leg notional vs the RM 8 minimum broker fee",
        "blurb": "MPlus charges max(RM 8, 0.05%) per leg. If your average order isn't big enough to dilute the RM 8 floor by 4x, the floor dominates and bleeds the strategy dry.",
        "threshold_text": ">= 4x",
        "good_when": "gte",
    },
    8: {
        "name": "CAGR vs hurdle",
        "fullname": "OOS CAGR versus the 8.5% hurdle",
        "blurb": "Internal hurdle: KLCI total return + a quant-edge premium + a margin for live-trading degradation. Below the hurdle, just buy the index.",
        "threshold_text": ">= 8.5%",
        "good_when": "gte",
    },
    9: {
        "name": "Max drawdown",
        "fullname": "Worst peak-to-trough drawdown over the OOS window",
        "blurb": "If max DD is worse than -60%, no retail investor stays in the strategy. The number is the binding psychological constraint.",
        "threshold_text": ">= -60%",
        "good_when": "gte",
    },
    10: {
        "name": "% positive months",
        "fullname": "Share of months with positive return",
        "blurb": "Sharpe can hide its scarring under one or two great months. The hit-rate floor protects against alpha concentration.",
        "threshold_text": ">= 50%",
        "good_when": "gte",
    },
    11: {
        "name": "Reproducibility",
        "fullname": "Determinism + version pinning",
        "blurb": "Strategy must reproduce bit-for-bit on a clean checkout with the same data snapshot. Anything less is unfit for capital.",
        "threshold_text": "= 1.0",
        "good_when": "eq",
    },
    12: {
        "name": "Paper trading",
        "fullname": "Live paper-trading evidence",
        "blurb": "30+ days of live paper trading on the production broker connection. Catches data-feed bugs, slippage that wasn't in the model, calendar gotchas, and operational fragility.",
        "threshold_text": ">= 30 days",
        "good_when": "pending",
    },
}

DIAG_DEFS: dict[str, dict] = {
    "Sharpe haircut (forward est)": {
        "blurb": "Forward Sharpe estimate after applying the standard 30-50% haircut for live-trading degradation.",
    },
    "MinBTL years required": {
        "blurb": "Minimum back-test length (Bailey) for the Sharpe to be statistically distinguishable from zero given the observed variance. Higher = less data than the strategy needs.",
    },
    "IS-OOS rank correlation": {
        "blurb": "If the variants that win in-sample also tend to win out-of-sample, the search itself is informative (not overfitting on noise).",
    },
    "Reality Check p-value": {
        "blurb": "White's Bootstrap Reality Check. p < 0.05 means the best-of-K alpha is unlikely to be luck alone.",
    },
}

KILL_TRIGGERS = [
    {
        "id": "K1",
        "name": "Sharpe regression",
        "trigger": "Live Sharpe over any rolling 6 months falls below 0.0",
        "action": "Pause new entries; review whether the OOS Sharpe estimate was over-confident.",
    },
    {
        "id": "K2",
        "name": "Drawdown breach",
        "trigger": "Live drawdown exceeds the worst OOS DD by more than 25%",
        "action": "Full stop; suspend capital allocation pending diagnostic.",
    },
    {
        "id": "K3",
        "name": "Cost-model breach",
        "trigger": "Realised cost-per-leg exceeds the model's prediction by more than 50%",
        "action": "Investigate broker-fee changes, ADV decay, or model bug.",
    },
    {
        "id": "K4",
        "name": "Universe shrinkage",
        "trigger": "Eligible universe falls below 50 names on any rebal date",
        "action": "Pause until liquidity returns; do NOT relax the floor.",
    },
    {
        "id": "K5",
        "name": "Calendar / data feed anomaly",
        "trigger": "Two consecutive rebal dates where signal disagreed with adjudicator manual check",
        "action": "Full stop; data-feed audit.",
    },
    {
        "id": "K6",
        "name": "Operational fault",
        "trigger": "Any order rejected by the broker on settle/cash/limits grounds for two days running",
        "action": "Pause; investigate operational pipeline.",
    },
]


def load_jsonl(p: Path) -> list[dict]:
    with p.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_csv(p: Path) -> list[dict]:
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def equity_with_drawdown(eq_csv: Path) -> dict:
    """Read a 2-col CSV (date, equity) and return {dates, equity, drawdown, returns}."""
    dates: list[str] = []
    eq: list[float] = []
    with eq_csv.open(encoding="utf-8") as f:
        rdr = csv.reader(f)
        next(rdr, None)  # header
        for row in rdr:
            if not row or not row[0]:
                continue
            dates.append(row[0])
            eq.append(float(row[1]))
    peak = eq[0]
    dd: list[float] = []
    for v in eq:
        peak = max(peak, v)
        dd.append((v - peak) / peak)
    returns: list[float] = [0.0]
    for i in range(1, len(eq)):
        prev = eq[i - 1]
        returns.append((eq[i] - prev) / prev if prev else 0.0)
    return {"dates": dates, "equity": eq, "drawdown": dd, "returns": returns}


def monthly_returns_grid(equity: dict) -> dict:
    """Build year x month grid of monthly returns from a daily equity series."""
    by_month: dict[tuple[int, int], list[float]] = {}
    last_value: dict[tuple[int, int], float] = {}
    first_value: dict[tuple[int, int], float] = {}
    for date_str, val in zip(equity["dates"], equity["equity"]):
        y, m, _ = date_str.split("-")
        key = (int(y), int(m))
        if key not in first_value:
            first_value[key] = val
        last_value[key] = val
    cells: list[dict] = []
    for (y, m), first in first_value.items():
        last = last_value[(y, m)]
        ret = (last - first) / first if first else 0.0
        cells.append({"year": y, "month": m, "ret": ret})
    cells.sort(key=lambda c: (c["year"], c["month"]))
    years = sorted({c["year"] for c in cells})
    return {"years": years, "cells": cells}


def calendar_year_returns(equity: dict) -> list[dict]:
    by_year: dict[int, list[tuple[str, float]]] = {}
    for date_str, val in zip(equity["dates"], equity["equity"]):
        y = int(date_str.split("-")[0])
        by_year.setdefault(y, []).append((date_str, val))
    out: list[dict] = []
    for y in sorted(by_year):
        rows = by_year[y]
        first = rows[0][1]
        last = rows[-1][1]
        out.append({"year": y, "ret": (last - first) / first if first else 0.0})
    return out


def rolling_sharpe(returns: list[float], window: int = 126) -> list[dict]:
    """Trailing 6m (126d) annualised Sharpe."""
    out: list[dict] = []
    for i, r in enumerate(returns):
        if i < window:
            continue
        seg = returns[i - window: i]
        mean = sum(seg) / window
        var = sum((x - mean) ** 2 for x in seg) / (window - 1)
        sd = math.sqrt(var) if var > 0 else 0.0
        sharpe = (mean / sd) * math.sqrt(252) if sd > 0 else 0.0
        out.append({"i": i, "sharpe": sharpe})
    return out


# -------------------------------------------------------------- scorecards --

GATE_LINE_RE = re.compile(r"\[(PASS|FAIL|-)\] Gate (\d+) - (.+?)\s+value=(\S+)\s+threshold=(\S+)")
DIAG_LINE_RE = re.compile(r"\[(PASS|FAIL|-)\] Diag - (.+?)\s+value=(\S+)\s+threshold=(\S+)")


def parse_scorecard_md(p: Path) -> tuple[list[dict], list[dict]]:
    """Returns (gates, diagnostics) from a Scorecard markdown."""
    gates: list[dict] = []
    diags: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        m = GATE_LINE_RE.search(line)
        if m:
            status, num, name, val, thr = m.groups()
            num_i = int(num)
            defn = GATE_DEFS.get(num_i, {})
            gates.append({
                "n": num_i,
                "name": defn.get("name", name.strip()),
                "fullname": defn.get("fullname", name.strip()),
                "blurb": defn.get("blurb"),
                "value": None if val == "-" else float(val),
                "threshold": None if thr == "-" else float(thr),
                "threshold_text": defn.get("threshold_text"),
                "status": status if status != "-" else "PENDING",
            })
            continue
        m = DIAG_LINE_RE.search(line)
        if m:
            status, name, val, thr = m.groups()
            diags.append({
                "name": name.strip(),
                "blurb": DIAG_DEFS.get(name.strip(), {}).get("blurb"),
                "value": None if val == "-" else float(val),
                "threshold": None if thr == "-" else float(thr),
                "status": status if status != "-" else "PENDING",
            })
    return gates, diags


def gates_from_scorecards_row(row: dict) -> list[dict]:
    """When a variant doesn't have a scorecard.md, synthesise gates from the
    final_scorecards.csv row + thresholds."""
    def status(val: float | None, thr: float | None, good: str) -> str:
        if val is None or thr is None:
            return "PENDING"
        if good == "lt":
            return "PASS" if val < thr else "FAIL"
        if good == "lte":
            return "PASS" if val <= thr else "FAIL"
        if good == "gte":
            return "PASS" if val >= thr else "FAIL"
        if good == "abs_le":
            return "PASS" if abs(val) <= thr else "FAIL"
        if good == "eq":
            return "PASS" if val == thr else "FAIL"
        return "PENDING"

    def num(x: str) -> float | None:
        if x is None or x == "":
            return None
        try:
            return float(x)
        except ValueError:
            return None

    pbo = num(row.get("pbo"))
    dsr = num(row.get("dsr_eff"))
    oos = num(row.get("oos_sharpe"))
    cov = num(row.get("cov"))
    slip = num(row.get("slip_drag"))
    order = num(row.get("order_mult"))
    cagr = num(row.get("cagr_oos"))
    max_dd = num(row.get("max_dd"))
    hit = num(row.get("monthly_hit"))
    stab = num(row.get("param_stab"))

    raw = [
        (1, pbo, 0.30, "lt"),
        (2, dsr, 0.65, "gte"),
        (3, oos, 0.30, "gte"),
        (4, stab, 0.25, "abs_le"),
        (5, cov, 1.0, "lte"),
        (6, slip, 0.30, "lte"),
        (7, order, 4.0, "gte"),
        (8, cagr, 0.085, "gte"),
        (9, max_dd, -0.60, "gte"),
        (10, hit, 0.50, "gte"),
        (11, 1.0, 1.0, "eq"),
        (12, None, 30.0, "pending"),
    ]
    gates = []
    for n, v, t, g in raw:
        defn = GATE_DEFS[n]
        gates.append({
            "n": n,
            "name": defn["name"],
            "fullname": defn["fullname"],
            "blurb": defn["blurb"],
            "value": v,
            "threshold": t,
            "threshold_text": defn["threshold_text"],
            "status": status(v, t, g),
        })
    return gates


# -------------------------------------------------------- per-strategy build --

def build_strategy_bundle(
    *,
    slug: str,
    label: str,
    family: str,
    rank: int,
    params: dict,
    scorecards_row: dict,
    summary_row: dict | None,
    equity_csvs: dict[str, Path],
    trades_csv: Path | None,
    scorecard_md: Path | None,
    holdout_json: Path | None,
    narrative: str,
) -> dict:
    bundle: dict = {
        "slug": slug,
        "label": label,
        "family": family,
        "rank": rank,
        "params": params,
        "tier": scorecards_row.get("tier"),
        "recommendation": scorecards_row.get("rec"),
        "params_hash": scorecards_row.get("params_hash"),
        "narrative": narrative,
    }

    # Headline metrics
    def num(x: str) -> float | None:
        if x is None or x == "":
            return None
        try:
            return float(x)
        except (ValueError, TypeError):
            return None

    bundle["metrics"] = {
        "wf_sharpe": num(scorecards_row.get("wf_sharpe")),
        "wf_sharpe_std": num(scorecards_row.get("wf_sharpe_std")),
        "oos_sharpe": num(scorecards_row.get("oos_sharpe")),
        "cagr_oos": num(scorecards_row.get("cagr_oos")),
        "max_dd": num(scorecards_row.get("max_dd")),
        "pbo": num(scorecards_row.get("pbo")),
        "dsr_eff": num(scorecards_row.get("dsr_eff")),
        "param_stab": num(scorecards_row.get("param_stab")),
        "cov": num(scorecards_row.get("cov")),
        "slip_drag": num(scorecards_row.get("slip_drag")),
        "order_mult": num(scorecards_row.get("order_mult")),
        "monthly_hit": num(scorecards_row.get("monthly_hit")),
        "n_pass": int(scorecards_row["n_pass"]) if scorecards_row.get("n_pass") else None,
        "n_eval": int(scorecards_row["n_eval"]) if scorecards_row.get("n_eval") else None,
    }
    if summary_row:
        bundle["metrics"].update({
            "cost_bps_mean": num(summary_row.get("cost_bps_mean")),
            "turnover_mean": num(summary_row.get("turnover_mean")),
            "sharpe_min": num(summary_row.get("sharpe_min")),
            "sharpe_max": num(summary_row.get("sharpe_max")),
            "t_stat": num(summary_row.get("t_stat")),
            "n_folds": int(summary_row["n_folds"]) if summary_row.get("n_folds") else None,
        })

    # Gates
    if scorecard_md and scorecard_md.exists():
        gates, diags = parse_scorecard_md(scorecard_md)
    else:
        gates = gates_from_scorecards_row(scorecards_row)
        diags = []
    bundle["gates"] = gates
    bundle["diagnostics"] = diags
    bundle["kill_triggers"] = KILL_TRIGGERS

    # Equity curves
    equity_data: dict[str, dict] = {}
    for cap, p in equity_csvs.items():
        if p.exists():
            eq = equity_with_drawdown(p)
            equity_data[cap] = eq
    bundle["equity_capitals"] = list(equity_data.keys())
    bundle["equity"] = equity_data  # full data in-bundle for now

    # Derived charts data
    if "350k" in equity_data:
        eq = equity_data["350k"]
        bundle["calendar_year_returns"] = calendar_year_returns(eq)
        bundle["monthly_grid"] = monthly_returns_grid(eq)
        bundle["rolling_sharpe"] = rolling_sharpe(eq["returns"], window=126)

    # Holdout verdict (if file present)
    if holdout_json and holdout_json.exists():
        bundle["holdout_verdict"] = json.loads(holdout_json.read_text(encoding="utf-8"))

    # Trades preview (first 200) - full dataset goes to trades/<slug>.json
    if trades_csv and trades_csv.exists():
        with trades_csv.open(encoding="utf-8") as f:
            rdr = csv.DictReader(f)
            trades = [r for r in rdr]
        bundle["trade_count"] = len(trades)
        bundle["trades_preview"] = trades[:200]

    return bundle


# --------------------------------------------------------------- variants --

def build_variants() -> tuple[list[dict], list[dict]]:
    """Return (variant_summaries, variant_details_with_gates)."""
    summary_rows = load_csv(RESULTS / "search_summary.csv")
    scorecard_rows = load_csv(RESULTS / "final_scorecards.csv")
    scorecard_by_hash = {r["params_hash"]: r for r in scorecard_rows}

    variants: list[dict] = []
    details: list[dict] = []
    for row in summary_rows:
        h = row["params_hash"]
        try:
            params = json.loads(row.get("params", "{}"))
        except json.JSONDecodeError:
            params = {}
        sc_row = scorecard_by_hash.get(h, {})
        summary = {
            "params_hash": h,
            "strategy": row["strategy"],
            "params": params,
            "capital": float(row.get("capital") or 350000),
            "sharpe_mean": float(row.get("sharpe_mean") or 0),
            "sharpe_std": float(row.get("sharpe_std") or 0) if row.get("sharpe_std") else None,
            "sharpe_min": float(row.get("sharpe_min") or 0) if row.get("sharpe_min") else None,
            "sharpe_max": float(row.get("sharpe_max") or 0) if row.get("sharpe_max") else None,
            "cagr_mean": float(row.get("cagr_mean") or 0),
            "max_dd_mean": float(row.get("max_dd_mean") or 0),
            "cost_bps_mean": float(row.get("cost_bps_mean") or 0),
            "turnover_mean": float(row.get("turnover_mean") or 0),
            "n_folds": int(row.get("n_folds") or 0),
            "t_stat": float(row.get("t_stat") or 0) if row.get("t_stat") else None,
            "dsr": float(row.get("dsr") or 0) if row.get("dsr") else None,
            "in_top_k": h in scorecard_by_hash,
            "rank": int(sc_row["rank"]) if sc_row.get("rank") else None,
            "tier": sc_row.get("tier"),
            "oos_sharpe": float(sc_row["oos_sharpe"]) if sc_row.get("oos_sharpe") else None,
        }
        variants.append(summary)

        # Per-variant detail bundle
        if h in scorecard_by_hash:
            details.append({
                "params_hash": h,
                "summary": summary,
                "scorecard_row": sc_row,
                "gates": gates_from_scorecards_row(sc_row),
            })
        else:
            details.append({
                "params_hash": h,
                "summary": summary,
                "gates": [],
            })
    return variants, details


def build_folds(search_log: list[dict]) -> list[dict]:
    """One row per (strategy, params_hash, fold) with IS/OOS metrics."""
    out: list[dict] = []
    for r in search_log:
        m = r.get("metrics") or {}
        out.append({
            "strategy": r["name"],
            "params_hash": r["params_hash"],
            "fold_idx": r["fold_idx"],
            "train_start": r["fold_train_start"],
            "validate_start": r["fold_validate_start"],
            "validate_end": r["fold_validate_end"],
            "capital": r["capital"],
            "sharpe": m.get("sharpe"),
            "cagr": m.get("cagr"),
            "max_drawdown": m.get("max_drawdown"),
            "hit_rate": m.get("hit_rate"),
            "n_trades": m.get("n_trades"),
            "skip_reason": r.get("skip_reason"),
        })
    return out


# ---------------------------------------------------------------- main --

def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for sub in ("strategies", "variants", "equity", "trades", "reports", "folds"):
        (OUT / sub).mkdir(exist_ok=True)

    final_scorecards = load_csv(RESULTS / "final_scorecards.csv")
    summary_rows = load_csv(RESULTS / "search_summary.csv")
    summary_by_hash = {r["params_hash"]: r for r in summary_rows}

    # Resolve the two headline strategies by rank
    rotation_row = next((r for r in final_scorecards if int(r["rank"]) == 1), None)
    clenow_row = next((r for r in final_scorecards if int(r["rank"]) == 9), None)
    assert rotation_row is not None, "rotation rank-1 not found"
    assert clenow_row is not None, "clenow rank-9 not found"

    rotation_params = json.loads(summary_by_hash[rotation_row["params_hash"]]["params"])
    clenow_params = json.loads(summary_by_hash[clenow_row["params_hash"]]["params"])

    rotation_bundle = build_strategy_bundle(
        slug="rotation_rank_1",
        label="Bursa Momentum Rotation (winner)",
        family="rotation",
        rank=1,
        params=rotation_params,
        scorecards_row=rotation_row,
        summary_row=summary_by_hash.get(rotation_row["params_hash"]),
        equity_csvs={
            "100k": RESULTS / "rotation_winner_equity_100k.csv",
            "350k": RESULTS / "rotation_winner_equity_350k.csv",
            "1M":   RESULTS / "rotation_winner_equity_1M.csv",
        },
        trades_csv=RESULTS / "rotation_winner_trades.csv",
        scorecard_md=RESULTS / "SCORECARD_rotation.md",
        holdout_json=RESULTS / "holdout_verdict_rotation.json",
        narrative=(
            "Dual-slope cross-sectional momentum. Every month, all eligible Bursa stocks are "
            "ranked by a composite slope score (30-day plus 90-day annualised log slope) and "
            "the top 20 are bought equal-weighted (subject to a 10% single-name cap). "
            "Walk-forward Sharpe across 16 folds was a strong 1.19, but the OOS holdout "
            "(2020-2022) printed only 0.29 - the strategy's alpha is real but small relative "
            "to Bursa's retail cost structure. Tier F."
        ),
    )

    clenow_bundle = build_strategy_bundle(
        slug="clenow_som_rank_9",
        label="Clenow Stocks-on-the-Move (lookback 60, regime-on)",
        family="clenow_som",
        rank=9,
        params=clenow_params,
        scorecards_row=clenow_row,
        summary_row=summary_by_hash.get(clenow_row["params_hash"]),
        equity_csvs={
            "100k": RESULTS / "clenow_winner_equity_100k.csv",
            "350k": RESULTS / "clenow_winner_equity_350k.csv",
            "1M": RESULTS / "clenow_winner_equity_1M.csv",
        },
        trades_csv=RESULTS / "clenow_winner_trades.csv",
        scorecard_md=None,
        holdout_json=None,
        narrative=(
            "Andreas Clenow's Stocks on the Move, adapted to Bursa. Monthly rebalance, 60-day "
            "regression-slope x R-squared ranking, top 30 names sized inverse-ATR, gated by an "
            "internal market regime filter (proxy basket > 200-day MA). Walk-forward Sharpe 0.93, "
            "OOS Sharpe 0.92 - the strongest survivor in the search. Fails on fold-CoV (1.11) "
            "and on order size (avg leg vs broker floor 1.1x). With ~RM 1M capital, both would "
            "likely flip. Tier F today, the closest of any variant to passing."
        ),
    )

    # Attach IS-only param-stability summary (closes the gap noted in
    # project_bursahack_2026_05_18: "Param stability not run on this variant").
    # Iron rule: 2020-2022 holdout not re-touched; evaluation window = IS 2008-2019.
    stab_path = RESULTS / "clenow_is_stability.json"
    if stab_path.exists():
        stab = json.loads(stab_path.read_text(encoding="utf-8"))
        clenow_bundle["is_stability"] = stab

    # Also drop the raw stability JSON next to the report bundles for
    # deep-linking / debugging.
    if stab_path.exists():
        shutil.copy2(stab_path, OUT / "clenow_is_stability.json")

    # Write strategy bundles. Separate full equity into equity/ files to keep
    # the strategy bundle responsive on dial-up.
    for bundle in (rotation_bundle, clenow_bundle):
        for cap, eq in (bundle.get("equity") or {}).items():
            (OUT / "equity" / f"{bundle['slug']}_{cap}.json").write_text(json.dumps(eq))
        # Slim copy without equity for the page bundle
        slim = dict(bundle)
        slim.pop("equity", None)
        # Trades go to their own file
        trades_preview = slim.pop("trades_preview", None)
        slim["has_trades"] = trades_preview is not None
        (OUT / "strategies" / f"{bundle['slug']}.json").write_text(json.dumps(slim))

    # Trades full data
    for slug, name in [("rotation_rank_1", "rotation"), ("clenow_som_rank_9", "clenow")]:
        trades_csv = RESULTS / f"{name}_winner_trades.csv"
        if trades_csv.exists():
            with trades_csv.open(encoding="utf-8") as f:
                trades = list(csv.DictReader(f))
            (OUT / "trades" / f"{slug}.json").write_text(json.dumps(trades))

    # Variants
    variants, details = build_variants()
    (OUT / "search_summary.json").write_text(json.dumps(variants))
    for d in details:
        (OUT / "variants" / f"{d['params_hash']}.json").write_text(json.dumps(d))

    # Scorecards
    (OUT / "final_scorecards.json").write_text(json.dumps(final_scorecards))

    # Folds
    search_log = load_jsonl(RESULTS / "search_log.jsonl")
    folds = build_folds(search_log)
    (OUT / "folds" / "all.json").write_text(json.dumps(folds))

    # Per-strategy folds (slimmer files for the FE)
    for strat_name in {f["strategy"] for f in folds}:
        sub = [f for f in folds if f["strategy"] == strat_name]
        (OUT / "folds" / f"{strat_name}.json").write_text(json.dumps(sub))

    # Reports (copy markdown verbatim; FE renders them)
    for rep in ("FINAL_REPORT.md", "REPORT.md", "SCORECARD_rotation.md"):
        src = RESULTS / rep
        if src.exists():
            shutil.copy2(src, OUT / "reports" / rep)
    framework = REPO / "DEPLOYMENT_FRAMEWORK.md"
    if framework.exists():
        shutil.copy2(framework, OUT / "reports" / "DEPLOYMENT_FRAMEWORK.md")

    # Manifest
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "strategies": [
            {
                "slug": rotation_bundle["slug"],
                "label": rotation_bundle["label"],
                "family": rotation_bundle["family"],
                "tier": rotation_bundle["tier"],
                "oos_sharpe": rotation_bundle["metrics"]["oos_sharpe"],
            },
            {
                "slug": clenow_bundle["slug"],
                "label": clenow_bundle["label"],
                "family": clenow_bundle["family"],
                "tier": clenow_bundle["tier"],
                "oos_sharpe": clenow_bundle["metrics"]["oos_sharpe"],
            },
        ],
        "variants_count": len(variants),
        "folds_count": len(folds),
        "search_log_rows": len(search_log),
        "kill_triggers": KILL_TRIGGERS,
        "iron": {
            "holdout_window": ["2020-01-01", "2022-02-15"],
            "holdout_touch_count": 1,
            "no_live_trading": True,
            "data_panel": ["2007-01-03", "2022-02-15"],
            "data_source": "Sentieo/FactSet XKLS, survivorship-free",
        },
        "families": sorted({v["strategy"] for v in variants}),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"[build_data] wrote {len(list(OUT.rglob('*')))} files under web/data/")
    print(f"[build_data] strategies: rotation rank-1 + clenow rank-9")
    print(f"[build_data] variants: {len(variants)}  folds: {len(folds)}")


if __name__ == "__main__":
    main()
