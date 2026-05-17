# BursaHack

Bursa Malaysia historical backtest harness.

- **Universe:** Bursa Malaysia (XKLS), survivorship-bias-free EOD prices.
- **Goal:** strategy-agnostic harness for brute-force strategy search, with walk-forward IS/OOS and a held-out final OOS window.
- **Costs:** M+ Online (Malacca Securities) fee model — 0.05% / RM 8 min brokerage, 0.03% clearing, 0.10% stamp duty, 8% SST on brokerage; applied per leg.

## Layout

```
src/bursahack/      pipeline modules (ingest, clean, universe, signals, costs, engine, walkforward, search)
notebooks/          interactive reports (data quality, sandbox, walk-forward results)
tests/              unit tests with synthetic fixtures
data/               raw CSV (gitignored) + parquet partitions (gitignored)
results/            backtest output (gitignored)
```

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\activate     # Windows
pip install -e .
python -m bursahack.ingest  # CSV -> parquet
```

## Data discipline

- **Holdout 2020-2021 is touched ONCE** at the end of strategy search. Don't peek.
- Every variant tried in `search.py` is logged for the multiple-testing penalty (Deflated Sharpe).
- Use `ADJ_*` columns for signals/returns; raw `CLOSE` is for executable-price reconstruction only.
- Join everything on `SECURITY_ID`, not `TICKER` (tickers may change over time).
