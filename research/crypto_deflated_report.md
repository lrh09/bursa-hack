# Crypto trend — honest deflated, rule-based backtest

Generated 2026-05-29. Data: yfinance daily spot, 2018-01-01–2026-05-29.

**Method (no data-mining shortcuts):** point-in-time top-K-by-dollar-volume universe (no survivorship, no curation); full 2018+ period; Deflated Sharpe over the ENTIRE 36-config grid; PBO + CPCV OOS.

## Honest deflated verdict

- Grid searched: **36 configs** (effective trials 12.7)
- Best config: `med/xs30/K40/gate`
- Raw: **CAGR 21.7%, maxDD -26.1%, Sharpe 0.97**
- **Deflated Sharpe (DSR over the whole grid): 0.899**
- Bootstrap Sharpe 95% CI: [0.22, 1.69]
- PBO (overfit probability): **0.361**
- CPCV OOS Sharpe: mean 0.95, 27/28 folds positive

## Full grid (by Sharpe)

| config | CAGR | maxDD | Sharpe | Calmar |
|---|---|---|---|---|
| med/xs30/K40/gate | 21.7% | -26.1% | 0.97 | 0.83 |
| combo/xs30/K40/gate | 16.4% | -24.0% | 0.94 | 0.68 |
| combo/xs30/K25/gate | 15.9% | -23.4% | 0.94 | 0.68 |
| med/xs30/K25/gate | 20.7% | -26.7% | 0.93 | 0.78 |
| med/xs90/K40/gate | 18.8% | -25.5% | 0.87 | 0.74 |
| med/xs90/K25/gate | 18.3% | -24.1% | 0.86 | 0.76 |
| combo/xs90/K25/gate | 13.4% | -21.7% | 0.84 | 0.61 |
| combo/xs90/K40/gate | 13.5% | -23.4% | 0.83 | 0.58 |
| combo/xs30/K15/gate | 13.9% | -26.2% | 0.82 | 0.53 |
| med/xs30/K15/gate | 17.8% | -32.3% | 0.81 | 0.55 |
| med/xs90/K15/gate | 16.8% | -28.5% | 0.79 | 0.59 |
| combo/xs90/K15/gate | 12.2% | -25.3% | 0.76 | 0.48 |
| med/xs30/K40/nogate | 16.6% | -40.0% | 0.67 | 0.42 |
| med/xs30/K25/nogate | 15.5% | -38.9% | 0.64 | 0.40 |
| med/xs90/K40/nogate | 14.7% | -40.1% | 0.62 | 0.37 |
| combo/xs30/K40/nogate | 12.1% | -40.0% | 0.59 | 0.30 |
| med/xs90/K25/nogate | 13.6% | -40.1% | 0.58 | 0.34 |
| combo/xs30/K25/nogate | 11.6% | -38.2% | 0.57 | 0.30 |
| med/xs90/K15/nogate | 12.7% | -41.9% | 0.55 | 0.30 |
| med/xs30/K15/nogate | 12.7% | -44.8% | 0.55 | 0.28 |
| combo/xs90/K40/nogate | 9.5% | -33.9% | 0.50 | 0.28 |
| combo/xs90/K25/nogate | 9.2% | -32.2% | 0.48 | 0.28 |
| combo/xs30/K15/nogate | 8.3% | -43.3% | 0.45 | 0.19 |
| combo/xs90/K15/nogate | 7.9% | -36.6% | 0.43 | 0.21 |
| slow/xs30/K15/gate | 5.2% | -49.7% | 0.35 | 0.11 |
| slow/xs90/K15/gate | 4.0% | -52.7% | 0.29 | 0.08 |
| slow/xs30/K25/gate | 2.9% | -56.0% | 0.24 | 0.05 |
| slow/xs30/K15/nogate | 2.2% | -60.2% | 0.21 | 0.04 |
| slow/xs90/K15/nogate | 1.7% | -62.6% | 0.20 | 0.03 |
| slow/xs30/K40/gate | 1.3% | -63.8% | 0.17 | 0.02 |
| slow/xs30/K25/nogate | 0.8% | -62.6% | 0.17 | 0.01 |
| slow/xs90/K25/gate | 0.7% | -55.7% | 0.14 | 0.01 |
| slow/xs30/K40/nogate | -0.2% | -64.8% | 0.13 | -0.00 |
| slow/xs90/K25/nogate | -1.1% | -65.8% | 0.09 | -0.02 |
| slow/xs90/K40/gate | -1.1% | -62.7% | 0.06 | -0.02 |
| slow/xs90/K40/nogate | -2.3% | -65.8% | 0.05 | -0.04 |

## Honest caveats
- Backtest derived from the same data it's measured on; only forward paper-trading is a true out-of-sample test.
- yfinance spot, ~10y history dominated by the 2020-21 bull; crypto DDs are deep.
- Shorts require perps / CME crypto futures (MBT/MET). Funding/borrow not modelled here.
- Deflated over THIS grid only; broader human search across the project isn't fully captured.