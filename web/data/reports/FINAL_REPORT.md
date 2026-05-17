# Final Strategy Scorecards

Top-K survivors of the 186-variant expanded brute-force search, evaluated against the 12-gate DEPLOYMENT_FRAMEWORK.md (v1.0 Advisory).

## Summary Table

| Rank | Strategy | Tier | Recommendation | WF Sharpe | OOS Sharpe | PBO | DSR-eff | CAGR(OOS) | Max DD | Pass/Eval |
|---:|---|:---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | rotation | **F** | NO GO (recommended) | 1.19 | 0.29 | 0.00 | 0.66 | +4.15% | -47.8% | 8/15 |
| 2 | rotation | **F** | NO GO (recommended) | 1.18 | 0.29 | 0.00 | 0.66 | +4.15% | -47.8% | 9/15 |
| 3 | rotation | **F** | NO GO (recommended) | 1.15 | 0.46 | 0.00 | 0.74 | +8.89% | -37.4% | 10/15 |
| 4 | rotation | **F** | NO GO (recommended) | 1.11 | 0.43 | 0.00 | 0.73 | +8.79% | -40.9% | 10/15 |
| 5 | rotation | **F** | NO GO (recommended) | 1.11 | 0.46 | 0.00 | 0.74 | +8.89% | -37.4% | 10/15 |
| 6 | rotation | **F** | NO GO (recommended) | 1.10 | 0.43 | 0.00 | 0.73 | +8.79% | -40.9% | 10/14 |
| 7 | rotation | **F** | NO GO (recommended) | 1.10 | 0.45 | 0.00 | 0.74 | +8.75% | -38.9% | 10/14 |
| 8 | rotation | **F** | NO GO (recommended) | 1.05 | 0.45 | 0.00 | 0.74 | +8.75% | -38.9% | 10/14 |
| 9 | clenow_som | **F** | NO GO (recommended) | 0.93 | 0.92 | 0.00 | 0.91 | +15.72% | -18.7% | 9/14 |
| 10 | clenow_som | **F** | NO GO (recommended) | 0.85 | 0.84 | 0.00 | 0.88 | +17.91% | -31.6% | 10/14 |

---

## Per-Variant Scorecards

# Scorecard - rotation_rank_1
Params: {"adv_floor": 500000.0, "ascending": false, "max_period_vol": 0.4, "min_period_vol": 0.01, "min_slope": 20.0, "price_floor": 0.2, "rebal_freq": "M", "slope_lookback_long": 90, "slope_lookback_short": 30, "top_n": 20, "vol_period": 90, "weight_cap": 0.1}

**Tier**: F   |   **Recommendation**: NO GO (recommended)

## Gates

  [PASS] Gate 1 - PBO                         value=0.0000  threshold=0.3
  [PASS] Gate 2 - Deflated Sharpe (eff-N)     value=0.6612  threshold=0.65
  [FAIL] Gate 3 - OOS Sharpe (net)            value=0.2925  threshold=0.3
  [FAIL] Gate 4 - Param stability             value=-0.3261  threshold=-0.25
  [PASS] Gate 5 - Fold-Sharpe CoV             value=0.7986  threshold=1.0
  [FAIL] Gate 6 - Slippage drag               value=0.5418  threshold=0.3
  [FAIL] Gate 7 - Avg order vs broker floor   value=1.2727  threshold=4.0
  [FAIL] Gate 8 - CAGR vs hurdle              value=0.0415  threshold=0.08499999999999999
  [PASS] Gate 9 - Max drawdown                value=-0.4776  threshold=-0.6
  [PASS] Gate 10 - % positive months          value=0.6000  threshold=0.5
  [PASS] Gate 11 - Reproducibility            value=1.0000  threshold=1.0
  [-] Gate 12 - Paper trading >= 30d       value=-  threshold=30
  [FAIL] Diag - Sharpe haircut (forward est)  value=0.2925  threshold=0.3
  [FAIL] Diag - MinBTL years required         value=31.6127  threshold=2.1218343600273784
  [PASS] Diag - IS-OOS rank correlation       value=0.7543  threshold=0.2
  [PASS] Diag - Reality Check p-value         value=0.0000  threshold=0.05

Summary: 8 / 15 gates passed.

---

# Scorecard - rotation_rank_2
Params: {"adv_floor": 500000.0, "ascending": false, "max_period_vol": 0.4, "min_period_vol": 0.01, "min_slope": 10.0, "price_floor": 0.2, "rebal_freq": "M", "slope_lookback_long": 90, "slope_lookback_short": 30, "top_n": 20, "vol_period": 90, "weight_cap": 0.1}

**Tier**: F   |   **Recommendation**: NO GO (recommended)

## Gates

  [PASS] Gate 1 - PBO                         value=0.0000  threshold=0.3
  [PASS] Gate 2 - Deflated Sharpe (eff-N)     value=0.6612  threshold=0.65
  [FAIL] Gate 3 - OOS Sharpe (net)            value=0.2925  threshold=0.3
  [PASS] Gate 4 - Param stability             value=-0.2412  threshold=-0.25
  [PASS] Gate 5 - Fold-Sharpe CoV             value=0.8145  threshold=1.0
  [FAIL] Gate 6 - Slippage drag               value=0.5418  threshold=0.3
  [FAIL] Gate 7 - Avg order vs broker floor   value=1.2727  threshold=4.0
  [FAIL] Gate 8 - CAGR vs hurdle              value=0.0415  threshold=0.08499999999999999
  [PASS] Gate 9 - Max drawdown                value=-0.4776  threshold=-0.6
  [PASS] Gate 10 - % positive months          value=0.6000  threshold=0.5
  [PASS] Gate 11 - Reproducibility            value=1.0000  threshold=1.0
  [-] Gate 12 - Paper trading >= 30d       value=-  threshold=30
  [FAIL] Diag - Sharpe haircut (forward est)  value=0.2925  threshold=0.3
  [FAIL] Diag - MinBTL years required         value=31.6127  threshold=2.1218343600273784
  [PASS] Diag - IS-OOS rank correlation       value=0.7543  threshold=0.2
  [PASS] Diag - Reality Check p-value         value=0.0000  threshold=0.05

Summary: 9 / 15 gates passed.

---

# Scorecard - rotation_rank_3
Params: {"adv_floor": 500000.0, "ascending": false, "max_period_vol": 0.4, "min_period_vol": 0.01, "min_slope": 20.0, "price_floor": 0.2, "rebal_freq": "M", "slope_lookback_long": 60, "slope_lookback_short": 30, "top_n": 30, "vol_period": 90, "weight_cap": 0.1}

**Tier**: F   |   **Recommendation**: NO GO (recommended)

## Gates

  [PASS] Gate 1 - PBO                         value=0.0000  threshold=0.3
  [PASS] Gate 2 - Deflated Sharpe (eff-N)     value=0.7444  threshold=0.65
  [PASS] Gate 3 - OOS Sharpe (net)            value=0.4644  threshold=0.3
  [FAIL] Gate 4 - Param stability             value=-0.3876  threshold=-0.25
  [PASS] Gate 5 - Fold-Sharpe CoV             value=0.7475  threshold=1.0
  [FAIL] Gate 6 - Slippage drag               value=0.3470  threshold=0.3
  [FAIL] Gate 7 - Avg order vs broker floor   value=0.8044  threshold=4.0
  [PASS] Gate 8 - CAGR vs hurdle              value=0.0889  threshold=0.08499999999999999
  [PASS] Gate 9 - Max drawdown                value=-0.3744  threshold=-0.6
  [PASS] Gate 10 - % positive months          value=0.6400  threshold=0.5
  [PASS] Gate 11 - Reproducibility            value=1.0000  threshold=1.0
  [-] Gate 12 - Paper trading >= 30d       value=-  threshold=30
  [PASS] Diag - Sharpe haircut (forward est)  value=0.4644  threshold=0.3
  [FAIL] Diag - MinBTL years required         value=12.5439  threshold=2.1218343600273784
  [PASS] Diag - IS-OOS rank correlation       value=0.7543  threshold=0.2
  [PASS] Diag - Reality Check p-value         value=0.0000  threshold=0.05

Summary: 10 / 15 gates passed.

---

# Scorecard - rotation_rank_4
Params: {"adv_floor": 500000.0, "ascending": false, "max_period_vol": 0.4, "min_period_vol": 0.01, "min_slope": 20.0, "price_floor": 0.2, "rebal_freq": "M", "slope_lookback_long": 60, "slope_lookback_short": 30, "top_n": 20, "vol_period": 90, "weight_cap": 0.1}

**Tier**: F   |   **Recommendation**: NO GO (recommended)

## Gates

  [PASS] Gate 1 - PBO                         value=0.0000  threshold=0.3
  [PASS] Gate 2 - Deflated Sharpe (eff-N)     value=0.7303  threshold=0.65
  [PASS] Gate 3 - OOS Sharpe (net)            value=0.4347  threshold=0.3
  [FAIL] Gate 4 - Param stability             value=-0.3468  threshold=-0.25
  [PASS] Gate 5 - Fold-Sharpe CoV             value=0.7544  threshold=1.0
  [FAIL] Gate 6 - Slippage drag               value=0.3538  threshold=0.3
  [FAIL] Gate 7 - Avg order vs broker floor   value=1.2873  threshold=4.0
  [PASS] Gate 8 - CAGR vs hurdle              value=0.0879  threshold=0.08499999999999999
  [PASS] Gate 9 - Max drawdown                value=-0.4093  threshold=-0.6
  [PASS] Gate 10 - % positive months          value=0.5200  threshold=0.5
  [PASS] Gate 11 - Reproducibility            value=1.0000  threshold=1.0
  [-] Gate 12 - Paper trading >= 30d       value=-  threshold=30
  [PASS] Diag - Sharpe haircut (forward est)  value=0.4347  threshold=0.3
  [FAIL] Diag - MinBTL years required         value=14.3177  threshold=2.1218343600273784
  [PASS] Diag - IS-OOS rank correlation       value=0.7543  threshold=0.2
  [PASS] Diag - Reality Check p-value         value=0.0000  threshold=0.05

Summary: 10 / 15 gates passed.

---

# Scorecard - rotation_rank_5
Params: {"adv_floor": 500000.0, "ascending": false, "max_period_vol": 0.4, "min_period_vol": 0.01, "min_slope": 10.0, "price_floor": 0.2, "rebal_freq": "M", "slope_lookback_long": 60, "slope_lookback_short": 30, "top_n": 30, "vol_period": 90, "weight_cap": 0.1}

**Tier**: F   |   **Recommendation**: NO GO (recommended)

## Gates

  [PASS] Gate 1 - PBO                         value=0.0000  threshold=0.3
  [PASS] Gate 2 - Deflated Sharpe (eff-N)     value=0.7444  threshold=0.65
  [PASS] Gate 3 - OOS Sharpe (net)            value=0.4644  threshold=0.3
  [FAIL] Gate 4 - Param stability             value=-0.3888  threshold=-0.25
  [PASS] Gate 5 - Fold-Sharpe CoV             value=0.7976  threshold=1.0
  [FAIL] Gate 6 - Slippage drag               value=0.3470  threshold=0.3
  [FAIL] Gate 7 - Avg order vs broker floor   value=0.8044  threshold=4.0
  [PASS] Gate 8 - CAGR vs hurdle              value=0.0889  threshold=0.08499999999999999
  [PASS] Gate 9 - Max drawdown                value=-0.3744  threshold=-0.6
  [PASS] Gate 10 - % positive months          value=0.6400  threshold=0.5
  [PASS] Gate 11 - Reproducibility            value=1.0000  threshold=1.0
  [-] Gate 12 - Paper trading >= 30d       value=-  threshold=30
  [PASS] Diag - Sharpe haircut (forward est)  value=0.4644  threshold=0.3
  [FAIL] Diag - MinBTL years required         value=12.5439  threshold=2.1218343600273784
  [PASS] Diag - IS-OOS rank correlation       value=0.7543  threshold=0.2
  [PASS] Diag - Reality Check p-value         value=0.0000  threshold=0.05

Summary: 10 / 15 gates passed.

---

# Scorecard - rotation_rank_6
Params: {"adv_floor": 500000.0, "ascending": false, "max_period_vol": 0.4, "min_period_vol": 0.01, "min_slope": 10.0, "price_floor": 0.2, "rebal_freq": "M", "slope_lookback_long": 60, "slope_lookback_short": 30, "top_n": 20, "vol_period": 90, "weight_cap": 0.1}

**Tier**: F   |   **Recommendation**: NO GO (recommended)

## Gates

  [PASS] Gate 1 - PBO                         value=0.0000  threshold=0.3
  [PASS] Gate 2 - Deflated Sharpe (eff-N)     value=0.7303  threshold=0.65
  [PASS] Gate 3 - OOS Sharpe (net)            value=0.4347  threshold=0.3
  [-] Gate 4 - Param stability             value=-  threshold=-0.25
  [PASS] Gate 5 - Fold-Sharpe CoV             value=0.7580  threshold=1.0
  [FAIL] Gate 6 - Slippage drag               value=0.3538  threshold=0.3
  [FAIL] Gate 7 - Avg order vs broker floor   value=1.2873  threshold=4.0
  [PASS] Gate 8 - CAGR vs hurdle              value=0.0879  threshold=0.08499999999999999
  [PASS] Gate 9 - Max drawdown                value=-0.4093  threshold=-0.6
  [PASS] Gate 10 - % positive months          value=0.5200  threshold=0.5
  [PASS] Gate 11 - Reproducibility            value=1.0000  threshold=1.0
  [-] Gate 12 - Paper trading >= 30d       value=-  threshold=30
  [PASS] Diag - Sharpe haircut (forward est)  value=0.4347  threshold=0.3
  [FAIL] Diag - MinBTL years required         value=14.3177  threshold=2.1218343600273784
  [PASS] Diag - IS-OOS rank correlation       value=0.7543  threshold=0.2
  [PASS] Diag - Reality Check p-value         value=0.0000  threshold=0.05

Summary: 10 / 14 gates passed.

---

# Scorecard - rotation_rank_7
Params: {"adv_floor": 500000.0, "ascending": false, "max_period_vol": 0.4, "min_period_vol": 0.01, "min_slope": 20.0, "price_floor": 0.2, "rebal_freq": "M", "slope_lookback_long": 90, "slope_lookback_short": 30, "top_n": 30, "vol_period": 90, "weight_cap": 0.1}

**Tier**: F   |   **Recommendation**: NO GO (recommended)

## Gates

  [PASS] Gate 1 - PBO                         value=0.0000  threshold=0.3
  [PASS] Gate 2 - Deflated Sharpe (eff-N)     value=0.7358  threshold=0.65
  [PASS] Gate 3 - OOS Sharpe (net)            value=0.4469  threshold=0.3
  [-] Gate 4 - Param stability             value=-  threshold=-0.25
  [PASS] Gate 5 - Fold-Sharpe CoV             value=0.8116  threshold=1.0
  [FAIL] Gate 6 - Slippage drag               value=0.3668  threshold=0.3
  [FAIL] Gate 7 - Avg order vs broker floor   value=0.8680  threshold=4.0
  [PASS] Gate 8 - CAGR vs hurdle              value=0.0875  threshold=0.08499999999999999
  [PASS] Gate 9 - Max drawdown                value=-0.3895  threshold=-0.6
  [PASS] Gate 10 - % positive months          value=0.6800  threshold=0.5
  [PASS] Gate 11 - Reproducibility            value=1.0000  threshold=1.0
  [-] Gate 12 - Paper trading >= 30d       value=-  threshold=30
  [PASS] Diag - Sharpe haircut (forward est)  value=0.4469  threshold=0.3
  [FAIL] Diag - MinBTL years required         value=13.5440  threshold=2.1218343600273784
  [PASS] Diag - IS-OOS rank correlation       value=0.7543  threshold=0.2
  [PASS] Diag - Reality Check p-value         value=0.0000  threshold=0.05

Summary: 10 / 14 gates passed.

---

# Scorecard - rotation_rank_8
Params: {"adv_floor": 500000.0, "ascending": false, "max_period_vol": 0.4, "min_period_vol": 0.01, "min_slope": 10.0, "price_floor": 0.2, "rebal_freq": "M", "slope_lookback_long": 90, "slope_lookback_short": 30, "top_n": 30, "vol_period": 90, "weight_cap": 0.1}

**Tier**: F   |   **Recommendation**: NO GO (recommended)

## Gates

  [PASS] Gate 1 - PBO                         value=0.0000  threshold=0.3
  [PASS] Gate 2 - Deflated Sharpe (eff-N)     value=0.7358  threshold=0.65
  [PASS] Gate 3 - OOS Sharpe (net)            value=0.4469  threshold=0.3
  [-] Gate 4 - Param stability             value=-  threshold=-0.25
  [PASS] Gate 5 - Fold-Sharpe CoV             value=0.8936  threshold=1.0
  [FAIL] Gate 6 - Slippage drag               value=0.3668  threshold=0.3
  [FAIL] Gate 7 - Avg order vs broker floor   value=0.8680  threshold=4.0
  [PASS] Gate 8 - CAGR vs hurdle              value=0.0875  threshold=0.08499999999999999
  [PASS] Gate 9 - Max drawdown                value=-0.3895  threshold=-0.6
  [PASS] Gate 10 - % positive months          value=0.6800  threshold=0.5
  [PASS] Gate 11 - Reproducibility            value=1.0000  threshold=1.0
  [-] Gate 12 - Paper trading >= 30d       value=-  threshold=30
  [PASS] Diag - Sharpe haircut (forward est)  value=0.4469  threshold=0.3
  [FAIL] Diag - MinBTL years required         value=13.5440  threshold=2.1218343600273784
  [PASS] Diag - IS-OOS rank correlation       value=0.7543  threshold=0.2
  [PASS] Diag - Reality Check p-value         value=0.0000  threshold=0.05

Summary: 10 / 14 gates passed.

---

# Scorecard - clenow_som_rank_9
Params: {"adv_floor": 500000.0, "ascending": false, "atr_window": 20, "lookback": 60, "max_gap": 0.15, "price_floor": 0.2, "rebal_freq": "M", "regime_ma": 200, "top_n": 30, "trend_ma": 100, "use_regime": true}

**Tier**: F   |   **Recommendation**: NO GO (recommended)

## Gates

  [PASS] Gate 1 - PBO                         value=0.0000  threshold=0.3
  [PASS] Gate 2 - Deflated Sharpe (eff-N)     value=0.9051  threshold=0.65
  [PASS] Gate 3 - OOS Sharpe (net)            value=0.9238  threshold=0.3
  [-] Gate 4 - Param stability             value=-  threshold=-0.25
  [FAIL] Gate 5 - Fold-Sharpe CoV             value=1.1108  threshold=1.0
  [PASS] Gate 6 - Slippage drag               value=0.1692  threshold=0.3
  [FAIL] Gate 7 - Avg order vs broker floor   value=1.0967  threshold=4.0
  [PASS] Gate 8 - CAGR vs hurdle              value=0.1572  threshold=0.08499999999999999
  [PASS] Gate 9 - Max drawdown                value=-0.1875  threshold=-0.6
  [FAIL] Gate 10 - % positive months          value=0.4800  threshold=0.5
  [PASS] Gate 11 - Reproducibility            value=1.0000  threshold=1.0
  [-] Gate 12 - Paper trading >= 30d       value=-  threshold=30
  [PASS] Diag - Sharpe haircut (forward est)  value=0.9238  threshold=0.3
  [FAIL] Diag - MinBTL years required         value=3.1704  threshold=2.1218343600273784
  [PASS] Diag - IS-OOS rank correlation       value=0.7543  threshold=0.2
  [PASS] Diag - Reality Check p-value         value=0.0000  threshold=0.05

Summary: 9 / 14 gates passed.

---

# Scorecard - clenow_som_rank_10
Params: {"adv_floor": 500000.0, "ascending": false, "atr_window": 20, "lookback": 60, "max_gap": 0.15, "price_floor": 0.2, "rebal_freq": "M", "regime_ma": 200, "top_n": 30, "trend_ma": 100, "use_regime": false}

**Tier**: F   |   **Recommendation**: NO GO (recommended)

## Gates

  [PASS] Gate 1 - PBO                         value=0.0000  threshold=0.3
  [PASS] Gate 2 - Deflated Sharpe (eff-N)     value=0.8825  threshold=0.65
  [PASS] Gate 3 - OOS Sharpe (net)            value=0.8437  threshold=0.3
  [-] Gate 4 - Param stability             value=-  threshold=-0.25
  [FAIL] Gate 5 - Fold-Sharpe CoV             value=1.1517  threshold=1.0
  [PASS] Gate 6 - Slippage drag               value=0.2413  threshold=0.3
  [FAIL] Gate 7 - Avg order vs broker floor   value=1.0804  threshold=4.0
  [PASS] Gate 8 - CAGR vs hurdle              value=0.1791  threshold=0.08499999999999999
  [PASS] Gate 9 - Max drawdown                value=-0.3160  threshold=-0.6
  [PASS] Gate 10 - % positive months          value=0.5200  threshold=0.5
  [PASS] Gate 11 - Reproducibility            value=1.0000  threshold=1.0
  [-] Gate 12 - Paper trading >= 30d       value=-  threshold=30
  [PASS] Diag - Sharpe haircut (forward est)  value=0.8437  threshold=0.3
  [FAIL] Diag - MinBTL years required         value=3.8009  threshold=2.1218343600273784
  [PASS] Diag - IS-OOS rank correlation       value=0.7543  threshold=0.2
  [PASS] Diag - Reality Check p-value         value=0.0000  threshold=0.05

Summary: 10 / 14 gates passed.

---

