"""Short-horizon mean-reversion.

Identical eligibility / sizing to momentum. The signal is the SAME return-over-
lookback formula but `ascending=True` so the engine picks the LOSERS.

Default params reflect the literature on short-horizon reversal: 5-21 day
lookback, no skip, weekly or monthly rebalance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from bursahack.engine import PricePanel
from bursahack.signals.momentum import Momentum


@dataclass
class Reversal(Momentum):
    """Short-horizon reversal: pick the biggest recent losers."""
    name: str = "reversal"
    params: dict[str, Any] = field(default_factory=lambda: {
        "lookback": 21,
        "skip": 0,
        "top_n": 20,
        "rebal_freq": "M",
        "adv_floor": 500_000.0,
        "price_floor": 0.20,
        "ascending": True,    # pick losers
    })
