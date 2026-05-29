# Systematic Futures Strategies for a CAGR-Maximizing, Survival-Constrained Retail Investor

**Research only — no code. Prepared 2026-05-29.**

## 0. The investor's objective, restated precisely

> Maximize long-run geometric growth (CAGR / terminal wealth) over a long horizon, **subject to a hard no-blow-up constraint.** Drawdowns and bumpy rides are explicitly acceptable; Sharpe and smoothness are explicitly *not* the goal. "Good not crazy" returns — no moonshot-or-zero. Must be rules-based, backtestable, and runnable end-of-day at IBKR on CME/CBOT/NYMEX/COMEX micro + standard futures with ≤100k USD.

This is the classic **fractional-Kelly / drawdown-constrained geometric-growth** problem. The right tool is not the highest-Sharpe strategy in isolation; it is the strategy whose return distribution, after position sizing, compounds fastest *without* a left tail that can wipe the account. That combination points strongly — but not blindly — at **diversified cross-asset trend-following (time-series momentum), volatility-targeted, sized at a fraction of Kelly, with an optional carry overlay.** The rest of this note builds and stress-tests that conclusion honestly.

A key framing throughout: for a geometric-growth investor, the relevant penalty is *variance drag* (CAGR ≈ arithmetic mean − ½·variance) and, critically, **left-tail / ruin risk** (a −100% return is unrecoverable; this dominates everything). Trend-following's defining statistical feature — **positive skew / convexity** — is therefore *more* valuable to this investor than to a Sharpe-maximizer, because positive skew means the bad outcomes are bounded-ish and the good outcomes are fat. That is the single most important reason this family fits the objective.

---

## 1. The core strategy family: cross-asset trend-following / time-series momentum (TSMOM)

### The foundational evidence

**Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum," *Journal of Financial Economics* 104(2), 228–250.** The seminal paper. Studying 58 liquid futures/forwards across equity indices, FX, commodities, and bonds (Jan 1985–Dec 2009), they document that an asset's own past 12-month return positively predicts its next-month return. **All 58 instruments showed positive TSMOM returns; 52 of 58 were statistically significant.** A diversified, volatility-scaled TSMOM portfolio delivered large risk-adjusted returns with little exposure to standard asset-pricing factors, and — importantly — performed *best during extreme up and down markets* for equities (the convexity signature). Reported diversified gross Sharpe was roughly ~1.2–1.4 on the 1985–2009 sample at the strategy (pre-fee, pre-realistic-cost) level — high because it predates the post-2010 drought (see §7).
- Paper: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2089463 ; mirror: http://docs.lhpedersen.com/TimeSeriesMomentum.pdf
- Original dataset (AQR): https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data

**Hurst, Ooi & Pedersen (2017), "A Century of Evidence on Trend-Following Investing," *Journal of Portfolio Management* 44(1).** The out-of-sample robustness anchor. They build a TSMOM strategy across **67 markets (29 commodities, 11 equity indices, 15 bonds, 12 currencies), 1880–2016**, combining **1-, 3-, and 12-month** trend signals equally, with positions scaled to a **10% annualized ex-ante volatility target.** Findings most relevant to this investor:
  - **Positive average returns in *every decade* since 1880** — 130+ years, no lost decade until the most recent (see §7).
  - The pre-fee/pre-cost long-run gross Sharpe is ~**0.7–0.8**; net of a representative 2-and-20 hedge-fund fee structure it drops toward ~**0.5**, and a realistic *retail self-run* implementation (no 2/20, but real commissions and slippage) sits somewhere in between — call it a long-run **Sharpe of ~0.5–0.7** as an honest planning number, *not* the headline 1.2 of the short modern sample.
  - **Crisis behavior:** TSMOM delivered positive returns in **8 of the 10 largest drawdowns of a 60/40 stock/bond portfolio** over the century. This is the "crisis alpha" property.
  - Paper: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026 ; AQR: https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing

**Baltas & Kosowski (2013/2020), "Demystifying Time-Series Momentum Strategies."** The implementation-quality paper. Confirms TSMOM but shows naïve implementations bleed returns to **turnover**. Two practical fixes: (a) use a **range-based volatility estimator (Yang–Zhang 2000)** rather than close-to-close — cuts turnover ~17% with no significant performance loss; (b) use a continuous **"TREND" sizing rule** (scale position by the statistical strength/t-stat of the trend) instead of a binary ±1 — cuts turnover ~24%. Combined, >⅓ turnover reduction. Directly relevant to retail net returns.
  - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2140091 ; CME mirror: https://www.cmegroup.com/education/files/demystifiing-time-series-momentum-strategies.pdf

### Verdict on the core family

**Confirmed as the prime candidate**, with one honest caveat carried forward to §7: the *magnitude* of the edge has shrunk versus the 1985–2009 sample, and at least one credible paper argues the statistical case was overstated (Huang et al. 2020). For a CAGR-over-Sharpe investor who can tolerate bumps, the positive skew + century-long persistence + genuine diversification from other holdings still make this the best-evidenced systematic futures family. Nothing else (carry, value, vol-selling) better matches the *survive-the-bumps-while-compounding* objective; vol-selling in particular has the wrong (negative) skew and a real ruin tail, disqualifying it as a *core*.

---

## 2. Variants and the absolute-return tilt (signal speed & construction)

Three mainstream signal constructions, all backtestable on daily continuous-contract data:

1. **Time-series momentum (sign of past return).** Position = sign of trailing N-month return (or a scaled version). N typically 1–12 months. This is the academic baseline.
2. **Moving-average crossover (MAC).** Long if fast MA > slow MA (e.g., 50/200-day, or the EWMA crossovers AQR/Man use: 16/64, 32/128, 64/256 day pairs). Mathematically close to TSMOM; smoother signal, fewer flips.
3. **Channel breakout (Donchian).** Long on a new N-day high, short on a new N-day low (classic Turtle: 20/55-day). The most "absolute-return-tilted" of the three — it sits out of choppy ranges and only commits to confirmed breakouts, producing **fatter right tails and bumpier equity curves**.

### Speed vs. the absolute-return preference

- **Faster trend (shorter lookbacks, ~1–3 months / 20–50 day):** higher turnover and cost sensitivity; **more reactive, captures sharp reversals and crisis moves faster**; historically *higher gross CAGR in trending regimes* but **worse whipsaw in chop** and bumpier. Faster systems are *more* convex (more option-like). For an investor who explicitly tolerates bumps and wants crisis upside, a faster tilt is defensible — *if* costs are controlled (micros + EOD help).
- **Slower trend (12-month / 200-day):** lower turnover, lower cost drag, smoother, but lags entries/exits and gives back more at turning points. Historically slightly lower gross CAGR but better net-of-cost robustness and the classic ~0.7 century Sharpe.
- **Best-practice and what the literature actually recommends:** **combine multiple speeds** (Hurst et al. use equal-weighted 1/3/12-month; Man/AQR blend several EWMA pairs). A speed-diversified blend captures most of the CAGR of the fast sleeve while damping its worst whipsaw. **For this investor, a multi-speed blend tilted slightly faster than the pure 12-month baseline is the sweet spot** — it raises expected CAGR and convexity at the cost of a bumpier ride, which is exactly the trade he says he wants.

Sources: Baltas & Kosowski (above); AQR "You Can't Always Trend When You Want" (§7).

---

## 3. Volatility targeting & sizing for CAGR-with-survival (the Kelly framing)

This is where the objective is actually *implemented*, and it matters more than signal choice.

**Volatility targeting.** Scale each position so each contributes equal *risk*, and scale the whole book to a target portfolio vol (academic standard: ~10% annualized ex-ante; Hurst et al. use 10%). Mechanically: contracts ∝ (target risk) / (instrument's recent ATR or σ × point value). Two benefits that directly serve geometric growth:
- **Variance control = less variance drag.** Since CAGR ≈ mean − ½σ², holding σ stable near the geometric-optimal band lifts compounding versus letting vol run wild.
- **Empirically, vol-targeting raised Sharpe and *reduced* the fat left tail of momentum** (Harvey et al. / Moreira–Muir "Volatility-Managed Portfolios" tradition). It is the single most important survival lever.

**Kelly / geometric-growth framing.** Full Kelly maximizes the *expected log of wealth* (= long-run CAGR) — exactly this investor's objective function. But **full Kelly is brutal in practice**: it produces ~50%+ drawdowns even when the edge is real, and — fatally — it is **acutely sensitive to estimation error.** Real expected returns and covariances are estimated with noise; overbetting relative to the true edge causes geometric return to *fall* and ruin probability to spike. Because trend edges are noisy and non-stationary, the true Kelly fraction is unknowable, so betting "full Kelly on your estimate" is effectively overbetting.

**Therefore fractional Kelly is the standard "aggressive but survivable" setting.** Practitioner consensus and the math both land on **¼ to ½ Kelly**:
- Half-Kelly captures ~**75% of full-Kelly's geometric growth** with roughly *half* the volatility and dramatically smaller drawdowns — a wildly favorable trade for a survival-constrained investor.
- Quarter-Kelly is even more conservative and is a reasonable floor given how much estimation error infects trend signals.

**Concrete mapping to this investor:** a **10–15% annualized portfolio vol target** is the practical analog of ¼–½ Kelly for a diversified trend book. It is "aggressive but survivable": bumpy enough to deliver real CAGR, far enough from full-Kelly that a bad estimate or a regime break dents but does not destroy the account. **Pushing vol target above ~20% is where survival risk genuinely rises** and should be treated as the red line. This is the precise translation of "tolerate bumps, never blow up."

Sources: Kelly/fractional-Kelly practitioner reviews (e.g., https://astuteinvestorscalculus.com/the-kelly-criterion/ ); vol-managed momentum literature; Hurst et al. (10% target).

---

## 4. Diversification across asset classes (how many markets, and why commodities)

**The core finding:** trend-following's edge is *thin per market but consistent across many*, so the **diversification across uncorrelated markets is where most of the risk-adjusted return comes from** — arguably more than signal cleverness. Pairwise correlations of *trend returns* across asset classes are low even when the underlying assets are correlated, because trend signs differ.

- **How many markets?** The marginal diversification benefit is steep at first and flattens. The bulk of the benefit is captured with roughly **10–20 liquid markets spread across all four/five asset classes**; beyond ~30–40 the incremental benefit is small (and large CTAs trade 100+ mostly for capacity, not edge). **For a 100k retail account, ~10–15 markets across 5 buckets is both sufficient and the practical ceiling** given margin and contract granularity. Micros (MES, MNQ, M2K, MYM, MGC, SIL, MCL, M6E, micro Treasuries / Yield futures, MBT) are what make a 5-bucket book *feasible* at this size — without micros, a single ES or CL position can blow the risk budget.

- **Why commodities/grains are the key diversifier (vs. financials).** Equity-index, bond, and (to a lesser degree) FX trends are heavily driven by the *same* macro factor (rates/risk sentiment), so a financials-only trend book is **secretly concentrated** — its sleeves co-move. **Commodities (grains, energy, metals) are driven by independent supply/demand and weather shocks**, with correlation to equities historically swinging between ~−0.5 and +0.4. They provide trends (and crisis-period inflation trends, e.g., 2022) that financials cannot. **A trend book without commodities loses much of the "all-weather, every-decade" property** that makes the century evidence hold. Grains (ZC/ZW/ZS) specifically add a weather-driven, equity-orthogonal trend source.
  - CME on commodity diversification: https://www.cmegroup.com/articles/2025/exploring-diversification-within-commodity-markets.html

**Bucket template (5 asset classes):** Equity index (MES, MNQ) · Rates/Bonds (micro 10Y / ZN, ZF/ZB) · FX (M6E, M6A or 6J) · Metals (MGC, SIL) · Energy (MCL) · Grains (ZC/ZS/ZW — standard, sized carefully) · optional Crypto (MBT/MET) as one small, high-vol satellite sleeve, *not* a core.

---

## 5. Carry as an overlay (combine with trend)

**Koijen, Moskowitz, Pedersen & Vrugt (2018), "Carry," *JFE* 127(2), 197–225.** Generalizes carry beyond FX to *every* futures asset class (equities, bonds, commodities, FX, credit, options). Carry = the return you earn if prices don't move (roll yield / yield differential / dividend-minus-financing). It **predicts returns cross-sectionally and in time series across all asset classes**, and crucially its exposure to (time-series) momentum is **small in every asset class** — i.e., **carry and trend are largely independent return sources.**
  - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2298565 ; NBER: https://www.nber.org/system/files/working_papers/w19325/w19325.pdf

**Honest take for *this* investor:** Adding a carry overlay to trend historically **improves the *combined* Sharpe and smooths the ride more than it raises raw CAGR.** Because carry returns are roughly uncorrelated to trend, blending them lowers portfolio variance — which *helps* geometric growth a bit via reduced variance drag, and lets you size the whole book slightly larger at the same vol target (a modest CAGR uplift). **But carry has a worse skew profile than trend** (carry trades tend to "pick up pennies in front of a steamroller" — negative skew, blow-up in crises), the *opposite* convexity to trend. For a survival-first investor, carry should be a **smaller satellite overlay (e.g., 20–30% of risk), never co-equal with trend**, precisely so it doesn't erode the left-tail protection that trend provides. Net: **worth adding in moderation for the diversification/CAGR-via-lower-variance benefit; do not let it dominate.**

---

## 6. Convexity — why trend is "long volatility / crisis alpha" and why that serves a CAGR investor

Trend-following has a **convex, option-like payoff** — Fung & Hsieh long ago showed CTA returns resemble a portfolio of *lookback straddles*. Greyserman & Kaminski (2014), *Trend Following with Managed Futures: The Search for Crisis Alpha*, formalize the **"CTA smile"**: trend returns are strongest during large market moves *in either direction*, and the strategy carries **positive skewness** — many small losses (whipsaws) punctuated by occasional large gains (sustained trends, often during crises when it goes short equities / long bonds / long commodities).
  - Greyserman & Kaminski: https://www.wiley.com/en-us/Trend+Following+with+Managed+Futures...-p-9781118890974
  - Hamill, Rattray & Van Hemert (Man, 2016), "Trend Following: Equity and Bond Crisis Alpha," 1960–2015: TSMOM performed *particularly strongly in the worst equity and bond environments*. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2831926

**Why this matters specifically for maximizing CAGR over a long horizon:** geometric growth is destroyed by left-tail losses (a −50% needs +100% to recover; −100% is terminal). Trend's **positive skew puts the fat tail on the *right*** and tends to deliver its biggest gains exactly when a buy-and-hold portfolio is being crushed. That (a) protects the compounding base from the deep crisis drawdowns that would otherwise crater terminal wealth, and (b) supplies "crisis alpha" rebalancing fuel. **Positive-skew, long-vol payoffs are worth more to a log-wealth maximizer than their Sharpe suggests** — which is why trend beats higher-Sharpe-but-negative-skew strategies (carry, vol-selling) for *this* objective.

---

## 7. Honest failure modes & the catch (the skeptic's section)

This is real and must not be glossed:

1. **The lost decade (≈2011–2020).** This was genuinely brutal. The **SG Trend Index returned ~0.4% annualized over the decade with a ~22% max drawdown — a MAR near 0.02 and effectively a zero Sharpe.** Two grinding drawdowns (2011–2013 and 2015–2018) as central banks suppressed volatility and markets chopped sideways. Over 2009→ the S&P 500 total return (~471%) dwarfed the SG Trend Index (~70%). **An investor must be psychologically prepared for a *decade* of going roughly nowhere while equities soar.** Trend recovered strongly 2019–2022 (2022 especially, on inflation/rate trends), but the lost decade is the honest base-rate for "how bad does the flat stretch get."
   - https://seekingalpha.com/article/4561044-the-lost-cta-decade-and-the-new-regime-for-strategic-allocations

2. **AQR's diagnosis (and the partial defense).** AQR, "You Can't Always Trend When You Want" (2018/2020): the lost decade was **not** caused by trend losing its ability to convert trends into profit, nor by lost diversification — it was simply that **markets had fewer large directional moves.** Reassuring (the edge mechanism survived) but also a warning: **trend's returns are regime-dependent and you cannot time the regime.** https://www.aqr.com/Insights/Research/Journal-Article/You-Cant-Always-Trend-When-You-Want

3. **Statistical-significance challenge.** Huang, Li, Wang & Zhou (2020), "Time Series Momentum: Is It There?", *JFE* 135(3): asset-by-asset (vs. pooled) regressions find **little reliable in/out-of-sample TSM predictability**; the big pooled t-stat fails proper bootstraps. Their nuance: the *strategy is still profitable*, but its profit is **statistically indistinguishable from simply betting on each asset's historical mean return** (i.e., a long-bias / risk-premium harvest), not from genuine *predictability*. Translation: part of what looked like "momentum alpha" may be plain asset-class risk premia + diversification + vol-targeting. Edge is real-ish but **smaller and less magical than the 2012 paper implied.** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3165284 (rebuttals exist defending TSMOM; the debate is unresolved — treat the edge as *modest*, not *huge*).

4. **Post-publication decay.** General factor-decay concern (McLean & Pontiff) applies; combined with crowding into CTAs, the honest expectation is **lower forward Sharpe (~0.4–0.6) than the backtests**, not the 1.0+ of early samples.

5. **Whipsaw & range markets.** Trend *loses money by design* in choppy, mean-reverting, low-vol markets. Many small losses are the cost of the convex payoff. This is structural, not a bug to fix.

6. **Transaction-cost & turnover sensitivity.** Net returns are *very* sensitive to costs. Faster signals and naïve daily rebalancing can erase the edge for a retail trader. **Mitigations are mandatory:** EOD execution, no-trade buffers / signal hysteresis, range-based vol estimation (Baltas–Kosowski), continuous (not binary) sizing, and **using micro contracts** so rebalancing granularity doesn't force oversized trades on a 100k book. IBKR commissions on micros are low but slippage on grains/energy can bite — size accordingly.

**The bottom line of the catch:** the edge is real, century-robust, and survives out-of-sample, **but it is modest, lumpy, regime-dependent, and capable of disappointing for a full decade.** It is the right tool for this investor *because* he has explicitly opted into bumps and a long horizon — not because it is a reliable smooth compounder. Anyone wanting steady annual gains should not run this.

---

## 8. Ranked shortlist (3–5 implementable strategies)

All assume: continuous-contract daily data (Norgate/Databento/Firstrate), EOD execution at IBKR, micro+standard mix, ~10–15 markets across 5 asset classes, vol-targeted, **¼–½ Kelly ≈ 10–15% portfolio vol target**, no-trade buffers, range-based vol estimator. CAGR/DD ranges below are **honest forward planning estimates for a self-run retail book net of realistic costs** (lower than glossy backtests; reflecting post-decay reality), not promises.

### #1 — Multi-Speed Diversified TSMOM, vol-targeted (THE CORE RECOMMENDATION)
- **Signal:** For each market, blend three lookbacks — fast (~1–2 month / 20–40 day), medium (~3 month / 63 day), slow (~12 month / 252 day) — equal weight, sign or scaled. (Hurst-et-al. construction, retail-sized.)
- **Sizing:** Equal-risk per market via range-based σ; scale book to ~12% vol target; continuous (TREND-style) sizing to cut turnover; no-trade buffer.
- **Markets:** MES/MNQ, micro-10Y/ZN+ZB, M6E/M6A, MGC/SIL, MCL, ZC/ZS/ZW.
- **Expected long-run CAGR:** ~**6–12%** net. **Expected max-DD:** ~**25–40%** (and multi-year flat stretches — see lost decade).
- **Data needs:** daily continuous contracts, ~15 markets, 20+ yr history for validation.
- **Retail feasibility:** **High** — EOD, micros, ~15 positions.
- **Why it fits:** the best-evidenced, most diversified, positive-skew core; speed-blend lifts CAGR/convexity over pure-slow while damping whipsaw; vol target at ¼–½ Kelly = aggressive-but-survivable. Compounds through crises that crater buy-and-hold. **This is the answer.**

### #2 — Faster-Tilted Breakout (Donchian channel), vol-targeted
- **Signal:** Long on new 50-day high / short on new 50-day low (or 20/55 Turtle-style dual channel with a shorter exit channel). Sits out ranges; commits only to confirmed breakouts.
- **Sizing:** identical vol-target/Kelly framework; ~12–15% vol (bumpier, so keep at the lower-Kelly end).
- **Expected CAGR:** ~**7–14%** net (higher upside). **Max-DD:** ~**30–45%** (bumpier, more whipsaw).
- **Retail feasibility:** **High** (EOD; fewer signals = lower turnover than fast TSMOM).
- **Why it fits:** most absolute-return-tilted, fattest right tail / most convex — directly matches "tolerate deep drawdowns for higher terminal wealth." Higher whipsaw is the price.

### #3 — Trend + Carry blend (core trend, carry overlay)
- **Signal:** Strategy #1 as core (~70–75% of risk) + a carry sleeve (~25–30%) — go long high-carry / short low-carry within each asset class (roll yield for commodities, yield-curve carry for bonds, rate differential for FX). Koijen et al. construction.
- **Sizing:** combined book to ~12% vol; carry sleeve capped so its negative skew can't dominate.
- **Expected CAGR:** ~**7–12%** net (modest uplift mainly via lower variance → larger sizing). **Max-DD:** ~**22–35%** (carry smooths normal times but can spike in crises).
- **Retail feasibility:** **Medium** — carry signals add data/operational complexity; still EOD.
- **Why it fits:** adds a near-orthogonal return source that lifts geometric growth via reduced variance drag — *if* kept subordinate so trend's left-tail protection survives.

### #4 — Slow Single-Speed TSMOM (12-month), vol-targeted (the conservative anchor / benchmark)
- **Signal:** sign of trailing 12-month return per market; monthly rebalance.
- **Expected CAGR:** ~**5–9%** net. **Max-DD:** ~**25–35%**.
- **Retail feasibility:** **Very high** — lowest turnover, simplest, cheapest, monthly EOD.
- **Why it fits:** the cleanest, most robust, lowest-cost expression of the century evidence; the right *first* implementation and the benchmark every fancier variant must beat. Slightly lower CAGR is the cost of robustness.

### #5 — Trend core + small Crypto-futures satellite (high-vol return-tilt option)
- **Signal:** Strategy #1 + a *small* (≤5–10% of risk) trend sleeve on MBT/MET micro crypto futures, same trend rules.
- **Expected CAGR:** wide — could add a few points of CAGR in crypto bull-trends; **Max-DD contribution is large and lumpy.**
- **Retail feasibility:** **Medium** — crypto futures are tradable at IBKR but high vol; strict small sizing essential.
- **Why it fits:** crypto is a strongly-trending, high-vol, somewhat-orthogonal market — a deliberate CAGR tilt for a bump-tolerant investor. **Only as a tiny satellite; never core** — its standalone ruin risk is real and violates the no-blow-up constraint if oversized.

---

## 9. One-paragraph synthesis

For an investor whose true objective is **maximize geometric growth subject to never blowing up**, the dominant systematic-futures answer is a **diversified, multi-speed, volatility-targeted cross-asset trend-following book, sized at a fraction of Kelly (≈10–15% portfolio vol), spanning ~10–15 markets across equity/rates/FX/metals/energy/grains, run end-of-day with micros at IBKR, optionally with a subordinate carry overlay.** This is chosen not because it has the highest Sharpe (it doesn't — ~0.5–0.7 honestly, forward) but because its **positive skew / crisis convexity** is exactly what a long-horizon log-wealth maximizer should prize, and its **century-and-a-half of every-decade survival** satisfies the no-ruin constraint. The honest catch: expect **~6–12% net CAGR with 25–40% drawdowns and the possibility of a full *decade* of going nowhere** (2011–2020 happened), and accept that the edge is real but *modest and contested* (Huang et al. 2020), not the magic of the 2012 backtests.

---

## Sources

- Moskowitz, Ooi & Pedersen (2012), Time Series Momentum, JFE — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2089463 ; data https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data
- Hurst, Ooi & Pedersen (2017), A Century of Evidence on Trend-Following, JPM — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026 ; https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing
- Baltas & Kosowski (2013/2020), Demystifying Time-Series Momentum Strategies — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2140091
- Koijen, Moskowitz, Pedersen & Vrugt (2018), Carry, JFE — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2298565 ; https://www.nber.org/system/files/working_papers/w19325/w19325.pdf
- Hamill, Rattray & Van Hemert (Man, 2016), Trend Following: Equity and Bond Crisis Alpha — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2831926
- Greyserman & Kaminski (2014), Trend Following with Managed Futures: The Search for Crisis Alpha (Wiley) — https://www.wiley.com/en-us/Trend+Following+with+Managed+Futures:+The+Search+for+Crisis+Alpha-p-9781118890974
- AQR (2018/2020), You Can't Always Trend When You Want, JPM — https://www.aqr.com/Insights/Research/Journal-Article/You-Cant-Always-Trend-When-You-Want
- Huang, Li, Wang & Zhou (2020), Time Series Momentum: Is It There?, JFE — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3165284
- The Lost CTA Decade (SG Trend Index figures) — https://seekingalpha.com/article/4561044-the-lost-cta-decade-and-the-new-regime-for-strategic-allocations
- CME, Diversification Within Commodity Markets — https://www.cmegroup.com/articles/2025/exploring-diversification-within-commodity-markets.html
- Fractional Kelly / position sizing review — https://astuteinvestorscalculus.com/the-kelly-criterion/
