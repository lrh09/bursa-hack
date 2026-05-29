# Feasibility memo — Systematic trading on a 10k account: CME futures vs B-book CFD

**Date:** 2026-05-29
**Author:** for RH
**Status:** Feasibility study. No code yet. Decision doc before any build.
**Framing (RH's):** This is a *business*, not retail gambling. Throwaway-capital pilot (~10k), modest leverage, reproducible edge, data-feasible, rigorous backtest → live. Survive adversarial broker behaviour. Reproducible expectancy, not one-lucky-trade.

> **Numbers marked ≈ are approximate and change** (margins, data vendor pricing, commissions). Verify current figures before committing capital. Everything statistical is honest-case, not marketing.

---

## 0. The one decision that dominates everything: counterparty structure

Before strategy, before data — **who is on the other side of your fill?**

- **B-book CFD broker** = the broker IS your counterparty. They profit when you lose. They see your positions, stops, margin, liquidation price. "Dirty tactics" (spread-widening into stops, asymmetric slippage, last-look fill rejection, platform latency at the worst moment) are not bugs — they are the revenue model, applied *selectively to winning accounts*. You cannot out-strategy a counterparty who controls execution and can change the rules mid-game.
- **Exchange-traded futures (CME)** = you trade against the *market*, centrally cleared. The broker (FCM) is a pass-through; they earn commission whether you win or lose. No incentive to hunt you. Funds segregated, regulated (CFTC/NFA, SIPC-adjacent protections via the FCM).

**Consequence for the "throwaway → roll → pull plug → next broker" plan:** that plan only exists *because* the B-book venue is adversarial. It is structurally a series of small skirmishes you eventually lose or get cut off from — reproducible but **not scalable**, therefore not a business. Choosing CME eliminates the need for the plan entirely.

**Base rate you are fighting (regulator-mandated disclosures, FCA/ASIC/ESMA): 70–85% of retail CFD accounts lose money.**

---

## 1. Venue tracks compared

### Track A — CME futures (RECOMMENDED), especially **micro** contracts

CME lists **micro** futures across every asset class — this is the key enabler for a 10k account, because the whole point of systematic trend trading is *diversification across many uncorrelated markets*, and micros let a small account hold a basket.

| Asset class | Micro contract | ≈ Overnight margin | Notes |
|---|---|---|---|
| Equity index | MES (S&P), MNQ (Nasdaq), M2K (Russell), MYM (Dow) | ≈ $1.2–2.0k | Deepest liquidity, cleanest data |
| FX | M6E (EUR/USD), M6A (AUD), M6B (GBP), MJY (JPY) | ≈ $200–500 | The "FX" you originally asked about |
| Energy | MCL (WTI crude) | ≈ $1.0–1.5k | Trends strongly |
| Metals | MGC (gold), SIL (silver) | ≈ $1.0–1.5k | Classic trend market |
| Rates | 2YY/5YY/10Y/30Y micro yield | ≈ $300–700 | Bonds — key diversifier |
| Crypto | MBT (Bitcoin), MET (Ether) | ≈ $1.0–2.0k | Optional, high vol |

A 10k account can realistically hold a **diversified 4–8 micro-contract basket** at modest leverage. That is exactly the managed-futures / CTA template (AQR, Winton, Man AHL run this at scale; the micro version is a faithful scale-down).

- **Real volume + real tick data** (CFDs have *no* real volume — a permanent handicap for any volume signal).
- **Counterparty risk ≈ zero** (central clearing, segregated funds).
- **Brokers:** Interactive Brokers (recommended — one API for futures + everything), or futures specialists (AMP, Tradovate, NinjaTrader, Edge Clear).
- **Downside:** US-market hours skew (though many trade nearly 24h), need to handle **contract rolls**, micro commissions ≈ $0.25–0.85/side + exchange fees are a real drag on high-frequency styles (fine for swing/trend).

### Track B — B-book CFD (kept in play per RH, but with eyes open)

- **The only genuine edge against a B-book is latency / stale-quote arbitrage** against their slow price feed — which is precisely what their surveillance targets, and gets accounts frozen/closed in days. It's a smash-and-grab, not a business.
- A slow trend system *can* run at a B-book on throwaway money, but: swap/financing is broker-set and skims carry; spreads widen adversely; and **if you win consistently, you get neutralized.**
- **100% counterparty risk:** broker insolvency or simple refusal-to-pay = total loss. No central clearing, no segregation guarantee at offshore B-books.
- **Synthetic / "volatility" indices (e.g., Deriv): hard avoid.** Those are broker-generated RNG price series with a *published, unfavourable* house edge — literally a casino, no real-world data edge is possible.
- **Legitimate narrow use:** a B-book demo/micro account is a cheap place to **forward-test execution plumbing** with tiny real money before committing at IB. Not a place to scale.

### Verdict on venue
**Build for CME futures (micro + standard mix) at IBKR. Use a B-book account (if at all) only as a throwaway forward-test rig, never as the business.** This inverts the original premise — but it's the inversion that makes it a business instead of a hobby.

**Capital (confirmed 2026-05-29):** up to **100k at IBKR** (RH trusts IBKR). "Micro" is a *position-sizing tool*, not the universe — at 100k you mix micros (where the full contract is too big, e.g. equity index) with standard small-margin contracts (grains, treasuries, FX). You are NOT confined to the micro shelf.

### Diversification — EMPIRICALLY SETTLED (2026-05-29)
RH's objection ("can't diversify with micros") was tested with data, not argued. `research/diversification_test.py` pulled 13–14 futures (15y daily, yfinance), built vol-scaled 12-month TSMOM sleeve returns per market, and measured the correlation structure:
- **Mean pairwise correlation of trend sleeves: +0.06 (≈ zero).**
- **Effective-N: 12.2 of 13 markets** (Lopez de Prado proxy); **Meucci ENB: 11.1 of 13.**
- Equity trap confirmed: ES vs NQ trend-sleeve corr = **+0.79** — the 4 equity-index micros are ~1 bet, not 4. Diversification comes from spanning rates + FX + metals + energy + grains + crypto, NOT from stacking equity micros.
- **Conclusion: a cross-asset futures trend book gives ~11–12 independent bets. The objection is wrong.** (yfinance data is not roll-adjusted — valid for correlation structure, not a final backtest.)

### Strategy verdict (research 2026-05-29, full detail in `futures_strategy_research.md`)
**Diversified, multi-speed, vol-targeted cross-asset TSMOM, ½-Kelly (~10–15% portfolio vol), ~10–15 markets, EOD at IBKR.** Chosen for **positive skew / crisis convexity** (fat tail on the RIGHT; biggest gains arrive when 60/40 is crashing) — which a CAGR-maximizing no-ruin investor values far above its modest Sharpe. Hurst-Ooi-Pedersen: positive in every decade since 1880, 8 of 10 worst 60/40 crises. **Honest expectation: 6–12% net CAGR, 25–40% max DD, with a real risk of a flat DECADE (2011–2020 ≈ 0.4%/yr).** Biggest risk: the edge is modest/lumpy/regime-dependent and arguably just risk-premia harvesting (Huang et al. 2020).

### Harness reuse (full detail in `harness_futures_gap.md`)
Stats layer (CPCV, DSR, PBO, bootstrap, `effective_n`) + cache + registry port verbatim. New `futures/` package needed: contract specs, daily roll-aware loader, futures cost model ($/contract + spread ticks), vol-target sizer (integer contracts), and a **daily mark-to-market engine** (the intraday session-close-flatten engine is the wrong tool for multi-day holds). ~800 LOC new vs ~3,000 reused.

---

## 2. Data sources (must match the execution venue)

**Iron rule: backtest on the same price series you'll execute on.** A backtest on real interbank/exchange data overstates fills at a B-book whose prices are synthetic.

### For CME futures
| Vendor | ≈ Cost | Why |
|---|---|---|
| **Norgate Data** | ≈ $70–90/mo (futures pkg) | **Best retail pick.** Continuous contracts WITH roll adjustment built in — solves the #1 futures-backtest headache. Daily + intraday. |
| Databento (GLBX.MDP3) | pay-per-dataset, $100s historical | Institutional-grade tick/MBO; clean; à la carte. |
| Firstrate Data | one-time ≈ $100–300 | Cheap historical minute/tick, no roll handling (you build it). |
| IQFeed | ≈ $150/mo | Realtime + historical, good for live. |
| IB historical API | "free" w/ account | Convenient for live; limited history depth + pacing limits for bulk backtest. |

### For spot FX (if a pair-specific study is ever wanted)
- Dukascopy (free tick), TrueFX (free), HistData (free minute), Polygon (paid).

---

## 3. Strategy menu — documented edge, honest Sharpe, venue fit

| Strategy | Mechanic / anchor | Honest Sharpe (diversified) | Best venue | Notes |
|---|---|---|---|---|
| **Cross-asset trend-following (TSMOM)** | Moskowitz–Ooi–Pedersen 2012. Markets trend; go long recent winners / short losers across asset classes. | **~0.5–0.8** | CME micros | **The flagship.** Most academically robust, scales DOWN to 10k via micros. Long deep drawdowns (12–24mo underwater is normal). |
| **Carry / term-structure** | Long high-yield / steep-backwardation, short low-yield. | ~0.4–0.7 | CME (roll yield) / FX | Crash-prone (unwinds violently). **Combine with trend** — they diversify each other (classic CTA combo). |
| **Equity-index intraday momentum/ORB** | Crabel/Zarattini. | ~0.3–0.7, regime-dep | MES/MNQ | **Our existing ORB harness ports directly** — MES is liquid + clean. Fastest reuse. |
| **Cross-sectional FX value/momentum** | Basket of pairs. | ~0.4–0.6 | FX futures/spot | Needs breadth (many pairs). |
| **Short-vol / risk-premia (futures spreads)** | Sell insurance. | varies | CME | Advanced; tail risk; not a starter. |

**Honest expectation for a disciplined 10k pilot on diversified trend+carry at modest leverage: ≈ 8–15%/yr at Sharpe ~0.7, with 15–25% max drawdown.** That's ≈ $800–1,500/yr on 10k *with losing stretches that test your nerve.* Anything promising more is selling something. This modesty IS what real edge looks like — same lesson the Bursa scorecard just delivered (the best institutional flicker was Sharpe +0.91, not a moonshot).

---

## 4. Backtest rigor — the big reuse

**We already built the hard part.** The BursaHack harness is asset-agnostic:
- `cpcv.py` — combinatorial purged CV ✓
- `diagnostics.py` — DSR (deflated Sharpe), PBO, stationary block bootstrap CI ✓
- engine, cache, registry, sizing, cost-regime framework ✓

To point it at futures we add an `fx/` (or `futures/`) module:
1. **Continuous-contract loader** (or ingest Norgate's pre-rolled series).
2. **Roll handler** (back-adjusted vs ratio-adjusted; tag roll dates so signals don't trade the roll gap).
3. **Futures cost model** — commission/contract + exchange fees + bid-ask in ticks + slippage; per-contract point values.
4. Reuse the **same gate**: a strategy ships only if it clears **DSR > 0.95 + bootstrap CI lo > 0 + PBO < 0.5** on real data with realistic costs, per-regime, on a held-out window.

This is weeks of saved work. We are not starting from zero.

---

## 5. Risk & business model for a 10k pilot

- **Sizing:** fixed-fractional, ~0.5–1.0% account risk per position; target *portfolio* annualised vol ~10–12% (vol-target each market inverse to its ATR).
- **Hard limits:** daily loss limit (e.g., −2%), drawdown kill-switch (e.g., flatten + pause at −15%), max gross exposure cap. Mechanical, no discretionary override.
- **Counterparty risk:** CME via regulated FCM (IB) → near-zero (central clearing, segregated funds). B-book → 100% (broker refusal/insolvency = total loss). **This single line is the strongest argument for CME.**
- **Capital staging:** validate in backtest → forward-test 1–3 months on micros with real (small) money → only then size up. Never deploy on backtest alone.
- **Tax/entity:** if it's genuinely a business, a separate account/entity and clean P&L records matter (out of scope here, flag for later).

---

## 6. Recommendation & proposed next step

1. **Venue:** CME **micro futures** via Interactive Brokers. Treat any B-book account as a disposable forward-test rig only.
2. **Strategy v1:** cross-asset trend-following on a 4–8 micro basket (+ carry overlay later). Fastest reuse alternative: equity-index ORB on MES (our ORB code ports almost verbatim).
3. **Data:** Norgate (cheapest correct path with roll handling) or Databento for tick-grade.
4. **Backtest:** reuse the BursaHack harness; add `futures/` loader + roll + cost model; run through the existing DSR/PBO/bootstrap gate.
5. **Decision gate:** only strategies clearing the gate on real futures data with realistic costs get real money — and at IB, never a B-book.

**Suggested next build (smallest rigorous step):** validate ONE strategy — diversified micro-futures trend, OR MES intraday ORB (faster reuse) — end-to-end: get data → loader+roll+cost model → run the harness gate → yes/no. That's the cheapest path to a real answer instead of more theory.

---

## Appendix — open questions to resolve before live capital
- Broker account: IB sufficient, or do you want a futures specialist (lower micro commissions)?
- Geography/regulation: which jurisdiction are you opening from? (affects broker access, leverage caps, tax.)
- Time commitment: fully automated (IB API) vs end-of-day manual execution? Trend systems can run EOD — much less infrastructure.
- Is the B-book angle about *leverage/accessibility* specifically? If so, name the constraint and we'll evaluate it directly.
