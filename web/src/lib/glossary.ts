// Hover-glossary entries. Used by <GlossaryTerm term="...">.
// Add terms here liberally; the <dfn> tooltip lights them up anywhere in copy.

export const GLOSSARY = {
  Sharpe: {
    short: "Sharpe ratio",
    long: "Annualised excess return divided by annualised volatility. The classic risk-adjusted return metric. Sharpe > 1 is considered strong; > 2 is exceptional. We report Sharpe both in-sample (training folds) and out-of-sample (the 2020-2022 holdout).",
  },
  Sortino: {
    short: "Sortino ratio",
    long: "Like Sharpe, but only penalises downside volatility. More forgiving of upside spikes. Useful when returns are asymmetric.",
  },
  CAGR: {
    short: "Compound annual growth rate",
    long: "Annualised geometric growth of equity over the period. Smooths out year-to-year variance into a single 'compounding rate'. Compare to the 8.5% Bursa hurdle.",
  },
  DSR: {
    short: "Deflated Sharpe Ratio",
    long: "Bailey & López de Prado. Adjusts the observed Sharpe for the number of independent trials and the non-normality of returns. Threshold: 0.65 means 'the Sharpe is statistically distinguishable from zero after accounting for the search'.",
  },
  PBO: {
    short: "Probability of Backtest Overfit",
    long: "Combinatorial test for selection bias. PBO measures, across all train/validate splits, how often the variant picked by training underperforms the median variant when evaluated out-of-sample.",
  },
  MinBTL: {
    short: "Minimum Backtest Length",
    long: "How many years of data the strategy would need for its Sharpe to be statistically distinguishable from zero. If MinBTL > observed period, the data is insufficient evidence.",
  },
  MaxDD: {
    short: "Maximum drawdown",
    long: "Worst peak-to-trough drop. Binds the worst-case psychological pain — if max DD is below -60% no retail investor stays in.",
  },
  Slippage: {
    short: "Slippage",
    long: "Difference between the model price and the realised fill, caused by market impact and bid-ask spreads. We model it as a square-root impact function of ADV participation.",
  },
  ADV: {
    short: "Average daily volume (in RM)",
    long: "Rolling 20-day mean of (close × volume) per security. Liquidity filter — we screen out names below RM 500k ADV.",
  },
  Holdout: {
    short: "Out-of-sample holdout",
    long: "The 2020-01 to 2022-02 window, walled off from parameter selection. Touched exactly once for the final verdict. If a strategy needs another touch, the holdout is burned and a new window must be cut.",
  },
  "Walk-forward": {
    short: "Walk-forward validation",
    long: "Rolling-origin train+validate split. Train on years t...t+3, validate on t+3...t+4, step forward 6 months, repeat. Captures temporal drift better than a single train/test split.",
  },
  "Reality Check": {
    short: "White's Reality Check",
    long: "Bootstrap test for whether the best variant in a search beat the in-sample mean by more than luck. p < 0.05 is convincing evidence of real edge.",
  },
  "Hit rate": {
    short: "Hit rate (monthly)",
    long: "Share of months with positive return. Catches strategies whose Sharpe is concentrated in a few big months — the floor is 50%.",
  },
  Turnover: {
    short: "Annual turnover",
    long: "Σ |trade notional| ÷ portfolio value per year. A turnover of 12× means the portfolio fully rotates monthly. Higher turnover → higher costs.",
  },
  Tier: {
    short: "Deployment-readiness tier",
    long: "A through F. A/B are deployable, C/D are borderline, F is research-only. See the Methodology page for the gating matrix.",
  },
} as const;
