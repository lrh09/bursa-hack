# BursaHack — Deployment Framework

**Version**: 1.0 (Advisory)
**Date adopted**: 2026-05-17
**Status**: ADVISORY scorecard. Decisions sit with the operator (RH). The framework produces a structured recommendation; it does not auto-execute capital actions.

---

## 0. Purpose

This document defines the **scorecard, sizing recommendations**, and **risk-event protocol** for any quantitative trading strategy produced by this repository. It is pre-committed in writing before any live capital is deployed so that the *numerical thresholds* are settled in advance — even though the operator retains discretion over the final go/no-go call.

The discipline this protects against is the well-documented retail behaviour of seeing the equity curve, falling in love, and rationalising. By computing every diagnostic against pre-written thresholds, the operator at least gets honest information rather than a flattering version of it.

**Disposition**: the framework outputs a *recommendation* (GO / CONDITIONAL / NO GO) with a transparent justification. The operator's role is to *make the decision* in light of that recommendation. Overrides are permitted but should be logged with reasoning so the override history is auditable.

This applies to every strategy: the current rotation winner gets scored the same way as any future variant produced by a brute-force search. There is no grandfathering.

---

## 1. Phase 1 — Pre-Deployment Scorecard

Each strategy is evaluated against 12 gates. Each gate returns a `(value, pass: bool, threshold)` triple. The aggregate recommendation tiers:

- **GO**: all 12 gates pass.
- **CONDITIONAL**: 10–11 gates pass; statistical core (Gates 1, 2, 3) all pass.
- **NO GO (recommended)**: any statistical core gate (1, 2, or 3) fails, OR ≤ 9 gates pass overall.

The operator may deploy against a CONDITIONAL or NO GO recommendation, but should document the rationale and reduce sizing per §2.

### 1.1 Statistical gates

| # | Metric | Threshold | Source / Rationale |
|---|---|---|---|
| 1 | **Probability of Backtest Overfitting (PBO)** | `< 0.30` | Bailey, Borwein, López de Prado (2017). PBO measures the probability that the in-sample-best variant fails to be the out-of-sample-best. PBO = 0.5 → search was pure noise. PBO < 0.3 implies ≥ 70% confidence the IS ranking survives in OOS. |
| 2 | **Deflated Sharpe Ratio (effective-N corrected)** | `> 0.65` | Bailey & López de Prado (2014). The probability that the true Sharpe > 0 after the multiple-testing penalty. Effective-N corrects for inter-variant correlation. Below 0.65 = coin-flip after honest deflation. |
| 3 | **OOS Sharpe (net of costs, RM 350k capital)** | `> 0.30` | Empirical Bursa-retail floor. Below this and the strategy pays the broker more than the operator. |

### 1.2 Robustness gates

| # | Metric | Threshold | Source / Rationale |
|---|---|---|---|
| 4 | **Parameter-neighbourhood stability** | OOS Sharpe within ±25% across ±15% perturbation of every parameter, one at a time | Knife-edge solutions are the canonical overfitting signature. We want a smooth plateau around the chosen point, not a sharp peak. |
| 5 | **Fold-Sharpe coefficient of variation** | `σ/μ < 1.0` across walk-forward folds | High cross-fold variance = period-specific luck rather than persistent signal. CoV ≥ 1 means standard deviation matches or exceeds the mean — too noisy to bet on. |

### 1.3 Capacity gates

| # | Metric | Threshold | Source / Rationale |
|---|---|---|---|
| 6 | **Slippage drag at deployed capital** | `< 30% of gross alpha` | If costs eat half the edge, the edge is too thin to survive any unfavourable cost shock. |
| 7 | **Order size vs broker floor** | Average order ≥ 4× the brokerage minimum's breakeven | At RM 8 min and 0.05% rate, breakeven order is RM 16,000. So average ticket ≥ RM 64,000. |

### 1.4 Out-of-Sample performance gates

| # | Metric | Threshold | Source / Rationale |
|---|---|---|---|
| 8 | **Holdout CAGR** | `> max(EPF 5y avg, KLCI 5y total return CAGR) + 3 pp` | Strategy must beat the passive alternative by a margin large enough to justify the additional drawdown and operational complexity. |
| 9 | **Holdout maximum drawdown** | `< 60%` | Survivability ceiling. On RM 350k that is RM 210k of paper loss. Above this requires explicit risk-tolerance justification. |
| 10 | **Holdout proportion of positive months** | `≥ 50%` | Smooth-ish ride. A strategy that loses 11 months a year and makes everything back in December may have a positive CAGR but is unholdable behaviourally. |

### 1.5 Operational gates

| # | Metric | Threshold | Source / Rationale |
|---|---|---|---|
| 11 | **Bit-exact reproducibility** | Backtest results reproducible from frozen code (git SHA) + frozen data | Pipeline correctness. |
| 12 | **Paper trading for 1 calendar month minimum** | ≥ 30 days live shadow operation matching backtest expectations | Catches data-pipeline regressions, broker-API issues, weekend/holiday edge cases. |

---

## 2. Phase 2 — Sizing Recommendations

The sizing table is a recommendation, not an enforcement. Operator may deploy at a higher or lower percentage with documented rationale.

| Trust tier | Condition | Recommended max % of investable net worth |
|---|---|---|
| **A** | GO + PBO < 0.15 | 25% |
| **B** | GO + 0.15 ≤ PBO < 0.30 | 15% |
| **C** | CONDITIONAL | 5–10% |
| **F** | NO GO | 0% recommended; if deployed, classify as exploratory / paper-only |

### 2.1 Absolute caps (recommended)

- **Single-strategy maximum** ≤ 50% of total quantitative capital allocation.
- **Minimum absolute deployment** = max(RM 250,000, capital where modelled cost per leg < 35 bps).
- **Maximum single-name weight** = 10% of strategy NAV (enforced in the strategy code itself).
- **Maximum gross exposure** = 100% (long-only, no leverage in v1).

### 2.2 Capital-source recommendations

- Capital deployed to a strategy is **not borrowed**.
- Capital deployed should be **money the operator can functionally lose** without altering lifestyle, dependant support, or fixed obligations for ≥ 5 years.
- Drawdown survivability check: max sustainable paper loss = portfolio max DD × deployed RM. If that number cannot be lived with, the deployment is too large.

---

## 3. Phase 3 — Risk-Event Protocol (post-deployment)

The conditions below are advisory triggers for review and recommended action. The operator retains decision authority but the rationale for any override should be logged.

| # | Trigger | Threshold (this strategy) | Recommended action |
|---|---|---|---|
| K1 | Live drawdown exceeds OOS max DD × 1.2 | -57% | **REVIEW**: 1-week trading pause. Re-run full diagnostics on the live data accumulated to date. |
| K2 | Live drawdown exceeds OOS max DD × 1.5 | -72% | **SUSPEND**: liquidate to cash. Full re-validation against this framework before any resumption. |
| K3 | Live 12-month rolling Sharpe < OOS Sharpe × 0.5 for 2 consecutive quarters | < 0.15 for 6 months | **SUSPEND**: signal is degrading, not just noisy. |
| K4 | Live cost/leg > 2× modelled cost for 1 quarter | > 50 bps | **OPERATIONAL REVIEW**: broker fee structure or execution venue is broken. |
| K5 | Strategy holds < 50% of target N positions for 3 consecutive months | < 10 of 20 names | **UNIVERSE REVIEW**: liquidity filter mis-tuned or market structure shifted. |
| K6 | Cumulative live total return < 0 after 24 months of operation | — | **HARD STOP recommended**: the OOS edge did not survive forward into live trading. |

### 3.1 Trigger semantics

- Triggers are checked at month-end after rebalance has completed.
- Multiple triggers firing together do not aggregate; the strongest recommended action applies.
- Recommendations are not mandates. Operator can override. **Operator must log the override and rationale**, which becomes part of the strategy's deployment record.

---

## 4. Diagnostics that produce the gate numbers

Each gate is computed by a deterministic function in `src/bursahack/overfitting_diagnostics.py`. The scorecard is regenerated whenever the strategy or its inputs change.

| Gate | Function | Inputs |
|---|---|---|
| 1 (PBO) | `pbo_score(per_variant_per_fold_sharpe)` | Full search-log per-fold Sharpe matrix |
| 2 (DSR-eff) | `deflated_sharpe_effective_n(...)` | Observed Sharpe, returns, all-variant fold-Sharpe correlations |
| 3 (OOS Sharpe) | `compute_metrics(...).sharpe` | Holdout equity curve |
| 4 (param stab) | `parameter_neighbourhood(...)` | Strategy class + base params + perturbation grid |
| 5 (fold CoV) | `fold_sharpe_dispersion(...)` | Per-fold Sharpe array |
| 6 (slip drag) | `slippage_drag(trades, gross_returns)` | Trade log + counterfactual no-cost backtest |
| 7 (order size) | `avg_order_size(trades, fee_cfg)` | Trade log + fee model |
| 8 (CAGR vs hurdle) | `compute_metrics(...).cagr` | Holdout equity curve + benchmark |
| 9 (max DD) | `compute_metrics(...).max_drawdown` | Holdout equity curve |
| 10 (% positive months) | `monthly_hit_rate(...)` | Holdout equity curve |
| 11 (reproducibility) | Manual: re-run, diff | Git SHA + frozen parquet |
| 12 (paper trading) | Manual: ≥ 30 days live shadow | Real-time data pipeline |

---

## 5. Authority

- The framework is owned by **RH** (the operator).
- The framework's role is to produce a structured, transparent recommendation. The operator's role is to make decisions in light of that recommendation.
- Overrides are permitted. Override decisions should be logged with rationale (date, gate that was overridden, why) in the strategy's deployment record so the override history is auditable.
- The numerical thresholds themselves are not changed in the moment — see §6 for amendment process.

### 5.1 Subagent and AI-tooling discipline

- Any Claude-spawned subagent or automation tool that touches strategy evaluation reads this document and produces scorecards using the *current version's* thresholds.
- Subagents are not permitted to modify the framework. They may propose amendments via PR-equivalent (a markdown diff written to `proposed_amendments/`).
- Subagents must not auto-execute capital actions. All scorecards surface to the operator for decision.

---

## 6. Amendment process

The framework may be amended under the following process:

1. **Cooling-off period**: a proposed amendment must sit unimplemented for **30 calendar days** before taking effect. This guards against mid-drawdown rationalisation.
2. **Written rationale**: every amendment requires a written justification committed to git.
3. **No backward-applied amendments**: changes apply to strategies *deployed after* the amendment's effective date.
4. **Version bump**: amendments produce a new minor version.
5. **No emergency amendments during a drawdown.** If a kill trigger fires, the rule on the books fires.

### 6.1 Permitted amendment scopes

- Threshold values (e.g., PBO 0.30 → 0.25)
- New gates
- Removal of gates (requires rationale that addresses *why this gate was wrong*, not "this gate is inconvenient")
- Removal of triggers (strongly discouraged; requires the cooling-off period and a "what would have happened historically" walkthrough)

---

## 7. Strategy-level deployment record

For every strategy ever considered for deployment, a record is created at `deployment_records/<strategy_name>_<date>.md` containing:

1. Strategy code SHA (git commit hash)
2. Data snapshot SHA / file mtimes
3. Framework version applied
4. Scorecard for all 12 gates (pass / fail + value + threshold)
5. Trust tier recommendation (A / B / C / F)
6. Operator decision and rationale (especially if differs from recommendation)
7. Capital deployed (if deployed)
8. Kill-trigger thresholds in absolute terms (e.g., "K1 fires below NAV = RM 150,150")
9. Override log (any deviations from the framework, with reasoning)

---

## 8. Version log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-05-17 | Initial adoption as advisory framework. Recommendation-tier output; operator retains decision authority with logged-override discipline. |

---

## 9. Operator acknowledgement

By committing this document to git, the operator (RH) acknowledges:

- These thresholds are pre-committed for use in evaluating every strategy from this repository.
- Decisions are the operator's; the framework provides structured, honest information to inform them.
- Override decisions, when made, are logged in the relevant deployment record so the framework's recommendation history can be audited against actual outcomes over time.

`Acknowledged`: _RH_ — countersign by amending this line in a subsequent commit.
