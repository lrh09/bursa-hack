# `configs/options/` — book YAMLs for the options toolkit

Book YAMLs are the input to the options analytics CLI. Each describes an account
(NLV, margin regime, tax status) and a set of option *structures* (multi-leg
positions). The CLI loads one, runs the full desk-grade analysis, and writes a
self-contained HTML tearsheet plus PNG charts.

## Run

```bash
# Bundled TSLA demo (the 360/460 x8 worked example, baked into bookio):
python scripts/run_options_analysis.py --demo

# A book file in this directory:
python scripts/run_options_analysis.py configs/options/tsla_book_2026-06-18.yaml
python scripts/run_options_analysis.py configs/options/example_book.yaml

# Offline (no network — fixtures / cached snapshots only):
python scripts/run_options_analysis.py configs/options/example_book.yaml --offline

# Real-world odds instead of risk-neutral:
python scripts/run_options_analysis.py configs/options/tsla_book_2026-06-18.yaml --measure rw --mu 0.10
```

Outputs:

- Charts (PNG)  -> `results/options/`
- HTML report   -> `web/public/<name>_options_report.html`
- Console       -> per-position cards, book net-greeks, income posture, margin,
                   and MC<->analytic reconciliation pass/fail badges.

The CLI exits non-zero if any `reconcile` gate fails or the book fails validation.

## Files

| File | What it is |
|---|---|
| `tsla_book_2026-06-18.yaml` | RH's real TSLA book (2026-06-18). 220C/380C longs, three bull-call spreads, two risk-reversal+wing combos, two short-call income legs. Spot/sigma pinned to MS_A (S=389.80, sigma=0.46); NLV 525,898 / ExcessLiq 84,000 broker-reported. |
| `example_book.yaml` | Generic single-name iron condor on placeholder ticker ACME. Proves the toolkit is underlying-agnostic; small Reg-T account. |

## Schema (§4 of the build contract)

```yaml
asof: 2026-06-18T16:00:00-04:00          # ISO-8601 with tz; expiries must be >= asof
defaults: { r: 0.045, q: 0.0, multiplier: 100, currency: USD }
account:
  tax_status: nra                        # 'us_person' | 'nra' | 'other'
  residence: MY
  broker: IBKR
  account_type: portfolio_margin         # 'cash' | 'reg_t_margin' | 'portfolio_margin'
  cash: 0
  netliq: 525898                         # broker NLV — the ONE source for NLV (do not recompute)
  excess_liquidity: 84000                # optional broker cushion (context vs model estimate)
underlyings:
  - symbol: TSLA
    spot: 389.80
    sigma: 0.46                          # flat fallback IV -> MarketState.sigma
    beta: 1.8                            # to SPY (for beta-weighted delta)
    hv_series_ref: null
scenarios:                               # optional; CLI defaults applied if omitted
  spots_pct: [-0.20, -0.10, -0.05, 0, 0.05, 0.10, 0.20]
  iv_shifts: [-10, -5, 0, 5, 10]
  days: [0, 7, 30]
structures:
  - name: <human label>
    type: bull_call_spread               # OPTIONAL; auto-detected via classify() if omitted
    legs:
      - { symbol: TSLA, right: C, strike: 360, expiry: 2027-06-18, qty:  8, entry_price: 91.96 }
      - { symbol: TSLA, right: C, strike: 460, expiry: 2027-06-18, qty: -8, entry_price: 53.00 }
catalysts:
  - { date: 2026-07-23, label: TSLA Q2 earnings, confirmed: false }
```

### Field conventions (non-negotiable — see contract §0 / §2)

- `right` is the enum **VALUE**: `C` (call), `P` (put), `S` (stock), `X` (cash).
  `load_book` constructs via `Right(row["right"])`.
- `qty` is **signed**: `+` long, `-` short. Contracts for options; shares for stock.
- `entry_price` is the **per-share** fill premium *magnitude* (>= 0). The sign of the
  cash flow comes from `qty` — never put a sign on `entry_price`.
- `multiplier` defaults to 100, set **per-leg** (`mult`) for mini/adjusted contracts.
- `expiry` is a date `YYYY-MM-DD`; must be on/after `asof`. `strike > 0`; `qty != 0`.
- `sigma` on an underlying is the flat fallback IV used when a leg has no per-leg IV;
  it is `MarketState.sigma`. Pin it deliberately — `q = 0` is the default but a
  required *thought* for dividend names.
- `type` is optional; omit it and `classify()` auto-detects the structure. When present
  it is a hint; `classify(builder(...)).name` round-trips by contract.

`entry_price: 0.0` means "fill price unknown" — realized P&L / breakeven framing that
relies on `net_cost_entry` will be relative to a zero-cost basis for those legs. Supply
real fills when you want true realized P&L.
