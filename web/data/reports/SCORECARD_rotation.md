# Scorecard - Bursa Momentum Rotation
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

Summary: 6 / 11 gates passed.