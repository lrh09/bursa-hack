"""Config: _extends resolves; child overrides parent."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bursahack.intraday.config import load_config


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    p = tmp_path / name
    p.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return p


def test_load_defaults(tmp_path: Path) -> None:
    cfg = load_config(Path("configs/intraday/defaults.yaml"))
    assert cfg.universe.top_n == 100
    assert cfg.data.exchange == "XKLS"
    assert len(cfg.fees) == 2  # mplus + institutional


def test_extends_child_wins(tmp_path: Path) -> None:
    base = _write(tmp_path, "base.yaml", {
        "name": "base",
        "data": {
            "data_root": "data/intraday",
            "snapshot_start": "2020-09-24",
            "snapshot_end": "2021-10-04",
            "exchange": "XKLS",
        },
        "universe": {
            "name": "top100_60d_adv", "top_n": 100, "lookback_days": 60,
            "rebalance_freq": "M", "min_session_minutes": 100, "exchange": "XKLS",
        },
    })
    child = _write(tmp_path, "child.yaml", {
        "_extends": "base.yaml",
        "name": "child",
        "universe": {"top_n": 50},
    })
    cfg = load_config(child)
    assert cfg.name == "child"
    assert cfg.universe.top_n == 50            # child override
    assert cfg.universe.lookback_days == 60    # inherited
    assert cfg.data.exchange == "XKLS"         # inherited


def test_extends_chain_circular_raises(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("_extends: b.yaml\nname: a\n", encoding="utf-8")
    b.write_text("_extends: a.yaml\nname: b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="circular"):
        load_config(a)
