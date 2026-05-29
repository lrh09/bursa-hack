# Diversification test — cross-asset futures trend book

- Data: yfinance daily continuous front-month, 2011-05-31 -> 2026-05-29 (NOT roll-adjusted; valid for correlation structure only).
- Signal per market: vol-scaled 12-month TSMOM sleeve return.

| Basket | Markets | Mean pairwise corr | effective_n (LdP) | Meucci ENB |
|---|---|---|---|---|
| 13-market basket (ex-BTC, long window) | 13 | +0.059 | 12.24 | 11.07 |
| Full 14-market basket (incl. BTC, short window) | 14 | +0.057 | 13.20 | 11.66 |

**Read:** effective-N / ENB near the market count = well diversified; collapse toward ~2 = RH's objection holds.

- Equity-correlation trap: ES vs NQ trend-sleeve corr = **+0.794** (the 4 equity-index micros are ~1 bet, not 4).

Heatmap: `diversification_heatmap.png`.