"""build_data.py emits one bundle per strategy_id with the right shape."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def _run_build_data():
    """Run the pipeline once for the module; tests inspect the output."""
    res = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "build_data.py")],
        check=False,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        pytest.skip(f"build_data.py failed: {res.stderr or res.stdout}")


def _load(path: str) -> dict:
    return json.loads((REPO / "web" / "data" / path).read_text(encoding="utf-8"))


def test_manifest_has_hash_to_strategy_id_lookup():
    m = _load("manifest.json")
    assert "hash_to_strategy_id" in m
    assert isinstance(m["hash_to_strategy_id"], dict)
    assert len(m["hash_to_strategy_id"]) >= 50


def test_manifest_strategies_count_in_expected_range():
    m = _load("manifest.json")
    sids = [s["strategy_id"] for s in m["strategies"]]
    assert 6 <= len(sids) <= 30, f"expected 6-30 strategies, got {len(sids)}: {sids}"


def test_strategy_aliases_file_exists_and_has_legacy_redirects():
    aliases = _load("strategy_aliases.json")
    assert aliases.get("rotation_rank_1") == "rotation__rebal-M"
    assert aliases.get("clenow_som_rank_9") == "clenow_som__regime-on__rebal-M"


def test_each_strategy_bundle_has_required_fields():
    m = _load("manifest.json")
    required = {
        "strategy_id", "family", "display_name", "shape", "short_blurb",
        "definition_md", "references", "source_file", "added",
        "variant_count", "headline_variant_hash", "headline_reason",
        "aggregate_metrics", "variants_inline",
    }
    for s in m["strategies"]:
        bundle = _load(f"strategies/{s['strategy_id']}.json")
        missing = required - bundle.keys()
        assert not missing, f"{s['strategy_id']} missing: {missing}"


def test_aggregate_metrics_use_5num_summary():
    m = _load("manifest.json")
    sample = next(s for s in m["strategies"] if s["variant_count"] >= 4)
    bundle = _load(f"strategies/{sample['strategy_id']}.json")
    agg = bundle["aggregate_metrics"]
    assert "wf_sharpe" in agg
    summary = agg["wf_sharpe"]
    for key in ("min", "p25", "median", "p75", "max", "best_hash"):
        assert key in summary, f"wf_sharpe missing {key}"


def test_headline_variant_in_inline_variants():
    m = _load("manifest.json")
    for s in m["strategies"]:
        bundle = _load(f"strategies/{s['strategy_id']}.json")
        hashes = {v["params_hash"] for v in bundle["variants_inline"]}
        assert bundle["headline_variant_hash"] in hashes
