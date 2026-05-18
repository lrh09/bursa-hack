"""Intraday strategy families.

Each family lives in its own module. Importing this package side-effect-
registers every family in `bursahack.intraday.registry.STRATEGY_REGISTRY` --
adding a new family means adding one import line below.
"""
from __future__ import annotations

from bursahack.intraday.signals import orb  # noqa: F401  -- registers ORBStrategy

__all__ = ["orb"]
