# Harness gap analysis — what the BursaHack platform needs to trade CME futures

**Date:** 2026-05-29
**Question:** the BursaHack harness was built for Bursa Malaysia 1-minute equities. What ports directly to a CME-futures trend-following business, and what must be built new?
**Bottom line:** the **statistical layer ports verbatim** (that's the expensive, valuable part). The **execution/data/cost layer is Bursa-and-intraday-specific** and needs a new `futures/` package. Critically, **daily trend-following uses a DIFFERENT, simpler engine** than the intraday fill-loop — we don't retrofit the intraday engine, we write a small daily one beside it.

---

## A. Ports directly — reuse verbatim (the crown jewels)

| Module | Why it ports | Change needed |
|---|---|---|
| `intraday/cpcv.py` | Combinatorial purged CV operates on an abstract index axis (sessions). Works on daily futures sessions unchanged. | None |
| `intraday/diagnostics.py` | DSR, PBO, stationary bootstrap CI, `effective_n` all operate on plain return arrays. Asset-agnostic. | None. **`effective_n` is literally the tool for the diversification test.** |
| `intraday/registry.py` | `SignalFrame` schema (ts, code, side, stop, target, exit_at) + `@register_strategy` generalize cleanly. `code` becomes the contract symbol. | None |
| `intraday/cache.py` | Content-hash result cache keyed on hashes — asset-agnostic. | None (point it at a futures results root) |
| `orchestrate.py` *pattern* | Sweep → cache → run-log loop is reusable. | Bakes in `Universe` + intraday features + Bursa-calendar holdout. Needs a futures variant or a generalization (see C). |

**This is weeks of saved work.** The luck-vs-alpha machinery — the whole reason this is rigorous and not gambling — is done and asset-neutral.

---

## B. Must be built new — Bursa/intraday-specific, doesn't port

| Module | Why it doesn't port | Futures replacement |
|---|---|---|
| `intraday/calendar.py` | Hardcoded `XKLS`, `KL_OFFSET_HOURS=8`, phantom lunch break 12:30–14:30, `session_minutes=360`. | For **daily** trend we barely need intraday session logic. Use `exchange_calendars` CME calendar (`"CMES"`/`"us_futures"`) just for session dates. No lunch-break / phantom logic. |
| `intraday/loader.py` | Hardcoded `exchange="XKLS"`, KL-offset filtering, phantom-bar drop, and the hive store IS Bursa 1m data. | New futures loader over **daily continuous-contract** data + a **contract-spec registry** (symbol → point value, tick size, currency, sector). Snapshot-hash/manifest pattern ports. |
| `intraday/universe.py` | Liquidity-screened top-N monthly rebalance — meaningless for futures. | **Bypass.** A futures "universe" is a hand-picked fixed basket (e.g. MES, ZN, ZB, 6E, 6J, GC, SI, CL, ZC, ZS, ZW, MBT). A static list, not a liquidity screen. |
| `costs.py` (Bursa) | Per-notional **bps** model: brokerage + SST + clearing + stamp duty. Wrong shape for futures. | New `FuturesFeeSchedule`: **fixed commission per contract** + exchange fee + half-spread in ticks × tick value. Cost scales with *contracts*, not notional-bps. |
| `intraday/impact.py` | Kissell-Glantz sqrt market-impact + Rogers-Satchell σ. | At retail micro size vs CME volume, **impact ≈ 0**. Drop it; cost = commission + half-spread. (σ still needed — for vol-targeting, not impact.) |
| `intraday/sizing.py` | Share-based, ADV participation cap, RM min-notional gate. | New **`VolTargetFuturesSizer`**: `contracts = round( (target_vol_fraction × equity) / (point_value × ATR_price) )`. No participation cap (you're tiny). Integer contracts. This is the CTA standard. |
| `intraday/engine.py` | **The big one.** Intraday: next-bar-open fill *within a session*, **session-close flatten (no overnight carry)**, per-trade round-trip accounting. | **Trend-following HOLDS POSITIONS FOR DAYS–WEEKS.** Session-close-flatten is fundamentally wrong. Need a new **daily mark-to-market engine**: position changes only on signal flips, daily P&L = `position × point_value × (close_t − close_{t-1})`, contract-**roll handling** (close expiring, open next, adjust), continuous equity curve. *Simpler* than the intraday loop in most ways. |

---

## C. Proposed new package layout

```
src/bursahack/futures/
├── __init__.py
├── contracts.py     # ContractSpec registry: symbol -> point_value, tick, ccy, sector, exchange
├── loader.py        # daily continuous-contract loader (+ roll handling); snapshot-hash like Bursa
├── costs.py         # FuturesFeeSchedule (commission/contract + exchange fee + spread ticks)
├── sizing.py        # VolTargetFuturesSizer (ATR / realized-vol targeting -> integer contracts)
├── engine_daily.py  # daily mark-to-market, multi-day holds, roll-aware, continuous equity
├── orchestrate.py   # sweep loop reusing cache.py; static basket instead of Universe
└── signals/
    ├── tsmom.py     # time-series momentum (Moskowitz-Ooi-Pedersen): sign of trailing N-month return
    └── ma_cross.py  # moving-average crossover (e.g. 50/200, or faster) — breakout variant TBD
```

Reused unchanged from `intraday/`: `cpcv.py`, `diagnostics.py`, `registry.py`, `cache.py`.

**Effort estimate:** contracts + costs + sizer ≈ 200 LOC; daily loader + roll ≈ 200 LOC; daily engine ≈ 250 LOC; 2 trend strategies ≈ 150 LOC. Stats layer ≈ 0 (reuse). ~800 LOC total for a working futures trend backtester — vs ~3,000 already built for Bursa.

---

## D. Data — and the cheap first step

- **Final backtest data:** Norgate (daily continuous, roll-adjusted, ~$70–90/mo) or Databento. Needed for the real go/no-go.
- **Diversification test (NOW, free):** we do NOT need paid data to answer RH's "micros can't diversify" objection. Free daily continuous front-month futures via **yfinance** (`ES=F, NQ=F, ZN=F, ZB=F, 6E=F, 6J=F, GC=F, SI=F, CL=F, ZC=F, ZS=F, ZW=F, BTC=F`) or Stooq is enough to:
  1. compute a trend signal's **returns per market**,
  2. build the **correlation matrix of those returns**,
  3. compute the **effective number of bets** via the existing `diagnostics.effective_n`,
  4. render a correlation heatmap.

  That settles "is a micro/standard futures basket actually diversified?" with a number, before spending a cent. yfinance data isn't roll-adjusted (unusable for a *final* backtest) but is fine for *correlation structure*.

---

## E. The objective shift (from RH, 2026-05-29) and how it lands in the harness

RH's objective is **max long-run CAGR subject to no-ruin**, NOT max-Sharpe / min-DD (see memory `feedback-absolute-return-over-drawdown`). Harness implications:
- **Sizer:** vol-target set toward fractional-Kelly (aggressive but survivable), not minimum-variance. Expose the vol-target as a swept parameter; report CAGR + max-DD per setting.
- **Scorecard ranking:** rank by CAGR / terminal wealth, show DD as the cost. Keep DSR/PBO/bootstrap as the *is-it-real* gate (unchanged), but the *which-variant-wins* sort leans CAGR.
- **Hard guardrail:** the engine/sizer must enforce a drawdown kill-switch + per-market and portfolio vol caps so "tolerate bumps" never becomes "blow up."

---

## F. Recommended sequencing

1. **NOW (cheap, no new engine):** diversification test on free yfinance daily data → effective-N + heatmap. Answers RH's objection with evidence. (~100 LOC standalone script + existing `effective_n`.)
2. Build `futures/contracts.py` + `costs.py` + `sizing.py` (specs, fees, vol-target).
3. Build `futures/loader.py` (daily continuous + roll) — wire to Norgate or a paid daily source once RH approves the spend.
4. Build `futures/engine_daily.py` (daily MTM, multi-day holds, rolls).
5. Build `signals/tsmom.py` + `ma_cross.py`.
6. Run the basket through the **reused** CPCV + DSR + PBO + bootstrap gate; rank by CAGR-with-survival.
7. Forward-test on micros at IBKR before sizing up.
