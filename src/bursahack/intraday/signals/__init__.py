"""Intraday strategy families.

Each family lives in its own module. Importing this package side-effect-
registers every family in `bursahack.intraday.registry.STRATEGY_REGISTRY` --
adding a new family means adding one import line below.
"""
from __future__ import annotations

from bursahack.intraday.signals import gap_continuation  # noqa: F401  -- registers GapContinuationStrategy
from bursahack.intraday.signals import lmsw  # noqa: F401  -- registers LMSWStrategy
from bursahack.intraday.signals import nr7_orb  # noqa: F401  -- registers NR7ORBStrategy
from bursahack.intraday.signals import orb  # noqa: F401  -- registers ORBStrategy
from bursahack.intraday.signals import vwap_reclaim  # noqa: F401  -- registers VWAPReclaimStrategy

__all__ = ["gap_continuation", "lmsw", "nr7_orb", "orb", "vwap_reclaim"]
