# BursaHack — Final Strategy Report

Generated: 2026-05-17
Source data: Sentieo XKLS daily EOD, 2007-01-03 to 2022-02-15 (survivorship-free)

---

## Executive Summary

- **Strategy chosen**: dual-slope momentum rotation, top-20 names, monthly rebal, vol-parity weighting with 10% cap.
- **Walk-forward Sharpe** (in-sample selection): **1.19**, **CAGR +25.9%**, max DD −16.5% (across 16 folds, RM 350k).
- **Holdout Sharpe** (touched once, 2020-2022): **0.30**, **CAGR +4.36%**, max DD -48.0%.
- **Deflated Sharpe** post-holdout (N=102 trials): **0.58** -- barely above the 0.5 noise floor.
- **Honest read**: the strategy survived a stress window (COVID + reflation + 2021 selloff) with positive return but ~75% of the walk-forward alpha disappeared. The dev-set max DD was an artefact of no fold containing a true crisis. Live drawdowns will look more like the holdout's −47% than the dev's −16%.

## 1. The Strategy (in plain words)

Every month, on the first business day of the month:

1. **Universe gate** -- start with all vanilla Bursa equity (`^\d{4}$` Main Market + `5235SS` KLCC stapled + `^03\d{3}$` ACE Market = 1,334 names), then drop any name where:
    - Last close < RM 0.20
    - 20-day average daily turnover < RM 500,000
    - Has not traded (vol > 0) in the last 5 days
2. **Vol band** -- additionally drop names where `period_vol_90` is outside [0.01, 0.40].
3. **Score** each surviving name as the *average of two annualised exp-regression slopes*:

    ```
    score = 0.5 * (
        100 * ((1 + slope_b_30)^250 - 1) * R_squared_30
      + 100 * ((1 + slope_b_90)^250 - 1) * R_squared_90
    )
    ```

    where `slope_b_N` is the OLS slope of `ln(adj_close) ~ t` over the last N trading days, and `R_squared_N` is the corresponding coefficient of determination.
4. **Filter** to `score > 20`.
5. **Pick** the top 20 by score (descending).
6. **Weight** by inverse `period_vol_90`, cap each name at **10%**, redistribute excess pro-rata to uncapped names, then a final flatten clips any still-above-cap to the cap.
7. **Trade** at the next business day's **open price** (T+1), rounded to whole lots of 100 shares. Pay MPlus brokerage + clearing + stamp + 8% SST + sqrt-impact slippage.
8. **Hold** for one month, then repeat.

**Code**: `src/bursahack/signals/rotation.py` (`DualSlopeRotation` class).

### 1.1 Final parameters

| Parameter | Value | Notes |
|---|---:|---|
| `slope_lookback_short` | 30 | shorter slope leg |
| `slope_lookback_long` | 90 | longer slope leg |
| `vol_period` | 90 | rolling window for inverse-vol weighting (and `period_vol` filter) |
| `min_period_vol` | 0.01 | stocks below this are excluded as too inactive |
| `max_period_vol` | 0.4 | stocks above this are excluded as too speculative |
| `min_slope` | 20.0 | score threshold; barely matters once top-N kicks in |
| `top_n` | 20 | concentration of the book |
| `weight_cap` | 0.1 | max single-name weight; the brake on tail risk |
| `rebal_freq` | 'M' | M = monthly first-business-day rebalance |
| `adv_floor` | 500000.0 | 20-day average daily turnover floor (RM) |
| `price_floor` | 0.2 | minimum last close (RM) |
| `ascending` | False | False = pick winners not losers |

## 2. Data, Universe, and Assumptions

**Source**: `stock_prices_xkls_all_file-1.csv` (Sentieo/FactSet XKLS daily EOD, 3.73M rows, 4,066 unique securities, 2007-01-03 to 2022-02-15).

**Equity universe definition** (codified in `src/bursahack/universe.py`):
- Plain 4-digit Main Market codes (e.g. `1295` Public Bank, `5212` Pavilion REIT): 1,286 names
- 4-digit + `SS` Stapled Securities (only KLCCP Stapled `5235SS`): 1 name
- 5-digit ACE Market codes `03xxx` (e.g. Aurora Italia `03037`): 47 names
- **Total = 1,334 equity instruments.** Survivorship-bias-free (45% of securities stop trading before file end, consistent with real delistings).

**Excluded by default**: company warrants (`WA`-`WE`), structured warrants (`Cx`, `Hx`, `Px`, 5-6 digit codes), rights/temporary tickers (`OR`, `TR`, `PR`), ETFs (`EA`). These trade differently and would contaminate a long-only momentum signal.

**Known data-vendor quirks handled in code**:
- Volume reporting changed from "thousand shares" to "actual shares" on **2014-06-03**. Pre-cutover rows have `ADJ_VOLUME *= 1000` applied at load (`src/bursahack/data_loader.py`).
- KLCC reorganised from `5089` to `5235SS` in May 2013 -- this is the only ticker chain in the dataset (`TICKER_CHAINS` in `universe.py`).
- 2021 row-count anomaly (2,488 "new" securities) was structured-warrant onboarding, not equity dupes. Filter handles it.
- `ADJ_FACTOR` tracks splits only; dividends are baked directly into `ADJ_CLOSE`. We use `ADJ_CLOSE` for signal & MTM (total return), and raw `OPEN` for execution-price reconstruction.

**Iron rules baked in**:
- All cross-time joins on `SECURITY_ID` (not `TICKER`, which can change).
- Force-exit any held name with > 7 days of zero volume.
- Suspension policy: skip dates with NaN raw price (signal computes but orders don't fill).
- Lot size: 100 shares (Bursa standard).

## 3. Cost Model — MPlus (Malacca Securities)

Applied to **both** buy and sell legs:

| Component | Rate | Cap / Floor |
|---|---:|---|
| Brokerage | 0.05% of notional | **min RM 8** |
| SST on brokerage | 8% × brokerage | -- |
| Clearing fee (Bursa) | 0.03% | cap RM 1,000 |
| Stamp duty | 0.10% | cap RM 1,000 |
| Slippage (modelled) | `k * sqrt(notional / 20d_ADV)` bps | 2-200 bps band |

For the strategy as run (RM 350,000 capital, top 20, monthly rebal):
- **Average cost per leg in the dev set: 27.5 bps**
- **Average cost per leg in the holdout: 23.0 bps**
- **Annual turnover**: ~13.1× — meaningful but the 10% weight cap keeps per-name notionals large enough that the RM 8 brokerage floor doesn't dominate.

## 4. Methodology — Walk-Forward + Holdout

**Time discipline**:
- Signal computed at end-of-day t using only data ≤ t (enforced by vectorised precompute that's NaN-padded at the front).
- Fills happen at T+1 OPEN; cost notional uses raw open, fill price uses adjusted open with slippage applied as a signed half-spread.
- Daily mark-to-market on `ADJ_CLOSE`.

**Walk-forward folds** (16 in total, dev set only):
- Train = 3 years, validate = 1 year, step = 6 months.
- 21-day embargo between train end and validate start (matches monthly rebal horizon).
- First fold: train 2008-01 → 2011-01, validate 2011-02 → 2012-02.
- Last fold: train 2015-07 → 2018-07, validate 2018-08 → 2019-08.
- Last validate ends 2019-08-12, comfortably below the holdout start 2020-01-01.

**Holdout** (`HOLDOUT_START` = 2020-01-01, `HOLDOUT_END` = 2022-02-15):
- Never touched by any of the 1,621 walk-forward backtests (verified with `assert_no_holdout_leak()` guards).
- Touched exactly once, with the winning variant and these parameters, at three capital levels (RM 100k / RM 350k / RM 1M).
- This report does **not** re-tune in response to the holdout result.

## 5. The Brute-Force Search (multiple-testing discipline)

Across four strategy families, **102 unique parameter variants** were tried × 16 walk-forward folds = **1,621 backtests**. Every (variant, fold) result is logged append-only to `results/search_log.jsonl` so we know exactly how many trials to penalise.

Families tried:
- **Momentum**: classic top-N cross-sectional momentum, 32 variants
- **Reversal**: short-horizon mean-reversion, 16 variants
- **Clenow "Stocks on the Move"**: exp-regression rank × R² × ATR-sizing × regime filter, ~22 variants completed (Clenow tail was killed after the rotation winner was clearly ahead)
- **Dual-slope rotation** (the winner family, ported from your original Python): 32 variants

**Deflated Sharpe Ratio** (Bailey & de Prado 2014) applied with N_trials = 102. The expected max Sharpe under null with N=102 is ≈ 3.0+ (in daily-Sharpe units), so even the in-sample winners would need very high Sharpe to clear the bar. Our rotation winner at walk-forward Sharpe 1.19 has DSR 0.85, which translates to "85% probability the true Sharpe is positive" -- comfortably above the 0.5 noise floor but not a slam dunk.

### 5.1 Top 10 walk-forward survivors

| # | Strategy | Rebal | Slope | Top-N | Min-slope | Sharpe (μ) | CAGR (μ) | Max DD (μ) | DSR |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | rotation | M | 30/90 | 20 | 20.0 | 1.19 | +25.9% | -16.5% | 0.85 |
| 2 | rotation | M | 30/90 | 20 | 10.0 | 1.18 | +25.7% | -16.2% | 0.85 |
| 3 | rotation | M | 30/60 | 30 | 20.0 | 1.15 | +21.5% | -16.1% | 0.84 |
| 4 | rotation | M | 30/60 | 20 | 20.0 | 1.11 | +25.2% | -17.4% | 0.83 |
| 5 | rotation | M | 30/60 | 30 | 10.0 | 1.11 | +20.4% | -15.7% | 0.83 |
| 6 | rotation | M | 30/60 | 20 | 10.0 | 1.10 | +24.8% | -17.0% | 0.83 |
| 7 | rotation | M | 30/90 | 30 | 20.0 | 1.10 | +20.7% | -16.2% | 0.83 |
| 8 | rotation | M | 30/90 | 30 | 10.0 | 1.05 | +19.9% | -16.1% | 0.82 |
| 9 | clenow_som | M | lb=60 | 30 | — | 0.93 | +15.6% | -15.0% | 0.78 |
| 10 | clenow_som | M | lb=60 | 30 | — | 0.85 | +14.8% | -22.1% | 0.76 |

## 6. Tearsheet

See `results/tearsheet_rotation.png` for the visual tearsheet (equity curve, drawdown, calendar-year bars, monthly heatmap, rolling 6m Sharpe, per-leg cost distribution, position count, daily return distribution).

### 6.1 Headline metrics across windows (RM 350k capital)

| Metric | Full (2008-2022) | Dev set (2008-2019) | Holdout (2020-2022) |
|---|---:|---:|---:|
| CAGR | +17.29% | +19.45% | **+4.36%** |
| Annual vol | 27.56% | 26.79% | 31.88% |
| Sharpe | 0.73 | 0.81 | **0.30** |
| Sortino | 0.84 | 0.95 | 0.34 |
| Max drawdown | -48.02% | -47.96% | -48.02% |
| Calmar | 0.36 | 0.41 | 0.09 |
| Hit rate (daily) | 54.29% | 53.99% | 56.05% |
| Total trades | 5,551 | 4,666 | 885 |
| Avg cost / leg | 26.8 bps | 27.5 bps | 23.0 bps |
| Annual turnover | 13.11× | 13.61× | 12.09× |
| Deflated Sharpe (N=102) | 0.985 | 0.989 | **0.578** |

> **Note** on the gap between walk-forward selection Sharpe (1.19) and the dev-set single-pass Sharpe (0.81): the walk-forward number is the *mean across 16 1-year validation folds with embargo*, while the dev-set single-pass uses no embargo and includes the full 2008 GFC drawdown without resetting. They're measuring different things; the walk-forward number is the one you should trust for variant selection.

### 6.2 Capital scaling — holdout only (the cost story)

| Capital | Final equity | CAGR | Sharpe | Max DD | Avg cost/leg |
|---:|---:|---:|---:|---:|---:|
| RM 100,000 | RM 103,392.71 | +1.58% | 0.21 | -47.35% | 45.7 bps |
| RM 350,000 | RM 381,515.82 | +4.15% | 0.29 | -47.76% | 24.9 bps |
| RM 1,000,000 | RM 1,091,100.51 | +4.19% | 0.29 | -47.93% | 23.2 bps |

- **RM 100k is uneconomic** for this strategy. The RM 8 brokerage floor binds on many trades, dragging average per-leg cost to ~46 bps and cutting CAGR to ~1.6%.
- **RM 350k is the cost sweet spot.** Costs drop to ~25 bps/leg, CAGR triples. Above RM 350k, returns to scale flatten.
- **The strategy capacity** at RM 1M with current liquidity floor is unstressed -- top 20 names from a ~150-name eligible pool is plenty.

### 6.3 Calendar-year returns

| Year | Return | Period |
|---|---:|---|
| 2008 | -34.09% | dev set |
| 2009 | +87.24% | dev set |
| 2010 | +24.27% | dev set |
| 2011 | -6.66% | dev set |
| 2012 | +5.71% | dev set |
| 2013 | +67.20% | dev set |
| 2014 | +25.07% | dev set |
| 2015 | +49.14% | dev set |
| 2016 | +3.16% | dev set |
| 2017 | +76.92% | dev set |
| 2018 | -7.48% | dev set |
| 2019 | +25.64% | dev set |
| 2020 | +17.72% | **holdout** |
| 2021 | -12.28% | **holdout** |
| 2022 | +7.21% | **holdout** |

## 7. The Verdict & Decision Frame

**Walk-forward (in-sample selection)**: looked great. Mean Sharpe 1.19 across 16 folds, CAGR ~25%, max DD ~16%. DSR 0.85.

**Holdout (single shot, 2020-2022)**: alpha fell ~75%. Final Sharpe **0.29 at RM 350k**, CAGR **+4.2%**, max DD **−47.8%**. DSR drops to 0.58.

This is the canonical pattern for retail momentum on Bursa: real signal exists, but it's modest, and tail risk is fat. The dev-set max DD of −16% was an artefact of no fold containing a true crisis; when the strategy actually met one (COVID March 2020), it drew down −47% before recovering. Anyone deploying this needs to be psychologically and financially prepared for −50% peak-to-trough drawdowns.

**Three defensible positions:**

**A) Trade it small.** Positive Sharpe across all capital levels in a hard window. Hit rate 56%. Drawdown profile is severe but typical for long-only Bursa momentum. Size it small (RM 350k-1M, ~10% of liquid net worth maximum), be ready for −50% drawdowns, give it 3-5 years of live data before judging.

**B) Stand down.** Sharpe 0.29 after 102 trials is consistent with "very weak signal hidden in transaction-cost noise." DSR 0.58 means we can't confidently reject "this is luck." If you wouldn't deploy the algo at 10% of the conviction the dev set implied, don't deploy it at all.

**C) Design v2 with a fresh holdout.** This holdout window is now contaminated -- we've touched it. Next iteration: try new signal families (volatility carry, low-vol anomaly, fundamentals if a source is available), use a *different* holdout cut (e.g. test 2018-2019 separately, reserving 2020-2022 + future data as the next OOS).

Position A is the realistic median quant view; position B is the rigorous statistician's view; position C is what you'd do if you had unlimited research time.

## 8. Reproducibility

All code lives under `src/bursahack/`. To reproduce this report from scratch on the same CSV:

```bash
uv sync                                                # install deps
uv run python -m bursahack.ingest                       # CSV -> parquet (10s)
uv run python -m bursahack.dq                            # data-quality report
uv run python -m bursahack.run_search                    # 1,621-backtest brute force (~60 min)
uv run python -m bursahack.analyze_search                # winner identification
uv run python -m bursahack.run_holdout --strategy rotation --n-trials 102
uv run python -m bursahack.report                        # produce this report + tearsheet
```

Test suite (`pytest tests/`): **32 unit tests covering the universe filter, MPlus fee math, engine fills, signal helpers, and the inverse-vol weighting**. All pass.

**Artefacts in `results/`**:
- `tearsheet_rotation.png` -- the visual tearsheet
- `REPORT.md` -- this document
- `holdout_verdict_rotation.json` -- the holdout numbers
- `search_summary.csv` -- per-variant aggregated stats
- `search_log.jsonl` -- 1,621 raw (variant, fold) results, append-only
