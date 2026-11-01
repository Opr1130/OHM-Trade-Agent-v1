"""Acceptance contracts for configurable, observational V2 scoring."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.strategy_policy import load_strategy_policy
from app.scanner.technical_score_v2 import V2Features, load_v2_config, score_long_v2
from app.services.technical_scoring_comparison import build_scoring_comparison


@pytest.mark.acceptance
def test_strategy_profile_does_not_change_live_trading_authority():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-001: existing authority is unchanged."""
    policy = load_strategy_policy()
    assert policy.scoring["production"] == "v1"
    assert policy.scoring["target_production"] == "v2"
    assert policy.advisory_economics["min_net_profit_usd"] == 0
    assert load_v2_config().mode == "shadow"


@pytest.mark.acceptance
def test_v2_requires_complete_point_in_time_feature_evidence():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-002: incomplete features never qualify."""
    snapshot = SimpleNamespace(
        last_price=101, ema20=100, ema50=95, ema200=90, atr=1.5, rsi=55,
        volume_ratio=2, macd_line=2, macd_signal=1,
    )
    features = V2Features(None, None, None, None, None)
    result = score_long_v2(snapshot, features, load_v2_config())
    assert result.status == "INSUFFICIENT_FEATURES"
    assert not result.threshold_met


@pytest.mark.acceptance
def test_comparison_is_observational_and_has_no_qualified_outcome_claim():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-003: comparison is evidence only."""
    from datetime import datetime, timezone

    row = build_scoring_comparison([], [], scan_id="acceptance", observed_at=datetime.now(timezone.utc))
    assert row["model_authority"] == "V1_PRODUCTION_V2_OBSERVATION"
    assert row["outcome_status"] == "NOT_MATURED"
    assert row["v2_top"] == []


@pytest.mark.acceptance
def test_active_increment_scope_contract_resolves():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-004: owner-approved scope is mapped."""
    root = Path(__file__).resolve().parents[1] / "docs" / "atdd"
    active = (root / "ACTIVE_INCREMENT").read_text(encoding="utf-8").strip()
    assert active == "ATDD-OPIP-CONFIGURABLE-SCORING-v1"
    assert (root / "scope-contracts" / (active + ".md")).is_file()
