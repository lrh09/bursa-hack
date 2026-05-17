"""Cross-sectional momentum.

`score(t) = ADJ_CLOSE[t - skip] / ADJ_CLOSE[t - skip - lookback] - 1`

This is the textbook Jegadeesh-Titman 12-1 momentum signal (default lookback=252,
skip=21) but the lookback/skip are tunable for brute-force search.

Eligibility filter:
  - Member of the default equity universe (universe.is_equity)
  - 20-day ADV >= adv_floor RM
  - Latest ADJ_CLOSE >= price_floor RM
  - Has at least `lookback + skip + 1` past observations
  - Not currently suspended (volume > 0 within last 5 trading days)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from bursahack.engine import PricePanel
from bursahack.signals.base import Strategy
from bursahack.universe import equity_mask


@dataclass
class Momentum(Strategy):
    name: str = "momentum"
    params: dict[str, Any] = field(default_factory=lambda: {
        "lookback": 252,
        "skip": 21,
        "top_n": 20,
        "rebal_freq": "M",      # M=month, Q=quarter, W=week
        "adv_floor": 500_000.0,
        "price_floor": 0.20,
        "ascending": False,     # winners
    })

    # ---- vectorised precompute --------------------------------------------
    def _precompute(self, panel: PricePanel) -> None:
        """Compute score + eligibility matrices once per backtest."""
        lookback = int(self.params["lookback"])
        skip = int(self.params["skip"])
        ac = panel.adj_close
        # score[t] = ac[t-skip] / ac[t-skip-lookback] - 1
        shifted = ac.shift(skip)
        self._score_matrix = shifted.pct_change(lookback)

        adv_floor = float(self.params["adv_floor"])
        price_floor = float(self.params["price_floor"])

        # 20-day rolling ADV in RM
        adv20 = panel.volume_rm.rolling(20, min_periods=1).mean()
        # last-5-day "active" flag (any traded RM in last 5)
        active = (panel.volume_rm.fillna(0) > 0).rolling(5, min_periods=1).max() > 0
        # eligibility per (t, sec): all conditions
        self._elig_matrix = (
            (adv20 >= adv_floor)
            & (ac >= price_floor)
            & active
            & self._score_matrix.notna()
        )
        self._cached_panel_id = id(panel)

    def _ensure_cache(self, panel: PricePanel) -> None:
        if getattr(self, "_cached_panel_id", None) != id(panel):
            self._precompute(panel)

    # ---- Strategy API -----------------------------------------------------
    def eligibility(self, t: pd.Timestamp, panel: PricePanel) -> set[str]:
        self._ensure_cache(panel)
        if t not in self._elig_matrix.index:
            return set()
        row = self._elig_matrix.loc[t]
        return set(row.index[row.fillna(False)])

    def score(self, t: pd.Timestamp, panel: PricePanel, eligible: set[str]) -> pd.Series:
        self._ensure_cache(panel)
        if t not in self._score_matrix.index:
            return pd.Series(dtype=float)
        s = self._score_matrix.loc[t]
        s = s[s.index.isin(eligible)]
        return s.dropna()

    def rebal_dates(self, panel: PricePanel) -> list[pd.Timestamp]:
        freq = str(self.params["rebal_freq"])
        # BMS = business-month-start; BQS = business-quarter-start; W-MON = weekly
        freq_map = {"M": "BMS", "Q": "BQS", "W": "W-MON"}
        rule = freq_map.get(freq, "BMS")
        candidates = pd.date_range(panel.dates.min(), panel.dates.max(), freq=rule)
        dates_set = set(panel.dates)
        # Snap each candidate to the next available trading day in the panel
        snapped = []
        all_dates_sorted = panel.dates.sort_values()
        for c in candidates:
            pos = all_dates_sorted.searchsorted(c)
            if pos < len(all_dates_sorted):
                snapped.append(all_dates_sorted[pos])
        # Dedup and sort
        return sorted(set(snapped))
