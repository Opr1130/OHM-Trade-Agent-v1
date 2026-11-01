"""Focused tests for versioned, non-authoritative strategy policy."""
import json
from pathlib import Path

import pytest

from app.core.strategy_policy import load_strategy_policy


def test_proposal_loads_without_granting_execution_authority():
    p = load_strategy_policy()
    assert p.scoring["production"] == "v1"
    assert p.scoring["target_production"] == "v2"
    assert p.advisory_economics["min_net_profit_usd"] == 0
    assert len(p.digest) == 64


@pytest.mark.parametrize("field,value", [
    ("production", "v2"),
    ("max_candidates", 99),
    ("v1_threshold", 75),
])
def test_unapproved_production_change_is_rejected(tmp_path: Path, field, value):
    src = Path(__file__).parents[1] / "app" / "core" / "strategy_policy.json"
    doc = json.loads(src.read_text())
    doc["scoring"][field] = value
    dest = tmp_path / "invalid.json"
    dest.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        load_strategy_policy(dest)


def test_auto_trading_cannot_be_enabled_through_profile(tmp_path: Path):
    src = Path(__file__).parents[1] / "app" / "core" / "strategy_policy.json"
    doc = json.loads(src.read_text())
    doc["protection"]["allow_funded_orders"] = True
    dest = tmp_path / "invalid.json"
    dest.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="TRADING_PERMISSION_CONFIG_FORBIDDEN"):
        load_strategy_policy(dest)
