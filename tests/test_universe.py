"""Universe-filter tests — locks in the rules derived from the DQ pass."""
from __future__ import annotations

import pandas as pd

from bursahack.universe import equity_mask, is_equity


def test_main_market_equity_included():
    assert is_equity("1295")       # Public Bank
    assert is_equity("5089")       # KLCC Property (legacy)
    assert is_equity("5212")       # Pavilion REIT
    assert is_equity("8311:MK")    # Pesona Metro w/ :MK suffix


def test_stapled_included():
    assert is_equity("5235SS")     # KLCCP Stapled


def test_ace_market_included():
    assert is_equity("03001")      # Cloudaron
    assert is_equity("03039")      # RTS Technology
    assert is_equity("03046:MK")


def test_warrants_excluded():
    assert not is_equity("7285WA")
    assert not is_equity("0185WA")
    assert not is_equity("1694WC")
    assert not is_equity("4456CG")
    assert not is_equity("5027CB")
    assert not is_equity("0208CM")


def test_temp_rights_excluded():
    assert not is_equity("7070OR")  # rights temp
    assert not is_equity("5121TR")  # restructure temp
    assert not is_equity("6556PR")  # rights temp


def test_etfs_excluded():
    assert not is_equity("0835EA")  # Kenanga inverse ETF
    assert not is_equity("0837EA")  # TradePlus REITs ETF


def test_56_digit_structured_excluded():
    assert not is_equity("058110")
    assert not is_equity("129568")
    assert not is_equity("5288C4")
    assert not is_equity("0651MP")


def test_equity_mask_vectorised():
    s = pd.Series(["1295", "5235SS", "03001", "7285WA", "5288C4", "0835EA", "058110"])
    mask = equity_mask(s)
    assert mask.tolist() == [True, True, True, False, False, False, False]


def test_filter_equity_against_master():
    """Hard count: applying the rule to the real master produces 1,334 securities."""
    from pathlib import Path

    from bursahack.paths import MASTER_PARQUET

    if not Path(MASTER_PARQUET).exists():
        return  # skip in environments without the ingested data
    m = pd.read_parquet(MASTER_PARQUET)
    n_equity = int(equity_mask(m["ticker"]).sum())
    assert n_equity == 1334, f"expected 1,334 equity tickers, got {n_equity}"
