"""Isolated V2 acquisition must use the core's active advisory policy, not a default.

The scanner never inherits funded, AI, Telegram or strategy-admin credentials.
Promotion rejects stale policy evidence as a durable reason before qualification.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.core import config as core_config
from app.core import strategy_policy
from app.scanner import market_scanner
from app.services import scan_result_promotion as promotion

INCREMENT = "ATDD-OPIP-V2-ADVISORY-PARITY-M01"
APP_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_core_and_worker_share_advisory_selector_without_credentials():
    """ATDD-OPIP-V2-ADVISORY-PARITY-M01/AC-001: Same flag, isolated reader."""
    services = yaml.safe_load((APP_ROOT / "docker-compose.yml").read_text())["services"]
    core = services["ohm-trade-agent"]
    worker = services["opip-scan-worker"]
    selector = "${OPIP_ADVISORY_PROFILE_ENABLED:-false}"

    assert core["environment"]["OPIP_ADVISORY_PROFILE_ENABLED"] == selector
    assert worker["environment"]["OPIP_ADVISORY_PROFILE_ENABLED"] == selector
    assert core["environment"]["OPIP_PRODUCTION_SCORING_VERSION"] == "v2"
    assert worker["environment"]["OPIP_PRODUCTION_SCORING_VERSION"] == "v2"
    assert worker["profiles"] == ["scanner"]
    assert "env_file" not in worker
    assert worker["read_only"] is True
    assert worker["mem_limit"] == "224m"
    assert not set(worker["environment"]) & {
        "KRAKEN_API_KEY", "KRAKEN_API_SECRET", "KRKN_API_KEY",
        "TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
        "STRATEGY_ADMIN_SECRET", "LIVE_TRADING_KEY",
    }
    mounts = worker["volumes"]
    assert "./data/opip/strategy-admin:/app/data/opip/strategy-admin:ro" in mounts
    assert not any(
        "/app/data/opip/strategy-admin" in mount and not mount.endswith(":ro")
        for mount in mounts
    )
    assert not any(
        mount.startswith("./data:/app/data") for mount in mounts
    )
    assert "OPIP_PAPER_V2_MODE" not in worker["environment"]


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_worker_uses_the_active_policy_threshold_and_exact_digest(monkeypatch, tmp_path):
    """ATDD-OPIP-V2-ADVISORY-PARITY-M01/AC-002: The same policy bytes govern V2."""
    document = json.loads(strategy_policy.DEFAULT_POLICY_PATH.read_text())
    document["scoring"]["v2_threshold"] = 77
    active = tmp_path / "active.json"
    active.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    expected = strategy_policy.load_active_strategy_policy(path=active)

    monkeypatch.setattr(
        core_config, "get_settings",
        lambda: SimpleNamespace(
            opip_advisory_profile_enabled=True,
            opip_production_scoring_version="v2",
        ),
    )
    monkeypatch.setattr(strategy_policy, "load_active_strategy_policy", lambda: expected)
    market_scanner._read_v2_bundle.cache_clear()
    try:
        profile, digest = market_scanner._read_v2_bundle()
        assert profile.threshold == 77
        assert profile.mode == "production"
        assert digest == expected.digest
    finally:
        market_scanner._read_v2_bundle.cache_clear()


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_promotion_rejects_stale_v2_policy_as_distinct_reason(monkeypatch):
    """ATDD-OPIP-V2-ADVISORY-PARITY-M01/AC-003: Never qualify stale policy evidence."""
    expected = "a" * 64
    monkeypatch.setattr(
        strategy_policy,
        "load_active_strategy_policy",
        lambda: SimpleNamespace(digest=expected),
    )
    settings = SimpleNamespace(
        opip_production_scoring_version="v2",
        opip_advisory_profile_enabled=True,
    )
    fresh = SimpleNamespace(v2_feature_status="READY", v2_profile_digest=expected)
    stale = SimpleNamespace(v2_feature_status="READY", v2_profile_digest="b" * 64)
    promotion._require_v2_evidence(settings, [fresh])
    with pytest.raises(promotion.PromotionRejected) as exc:
        promotion._require_v2_evidence(settings, [fresh, stale])
    assert exc.value.reason == "V2_SCORING_POLICY_DIGEST_MISMATCH"


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_invalid_active_policy_never_falls_back_silently(monkeypatch):
    """ATDD-OPIP-V2-ADVISORY-PARITY-M01/AC-003: Invalid policy is a rejection."""
    def unreadable():
        raise ValueError("INVALID_STRATEGY_POLICY_SCHEMA")

    monkeypatch.setattr(strategy_policy, "load_active_strategy_policy", unreadable)
    settings = SimpleNamespace(
        opip_production_scoring_version="v2",
        opip_advisory_profile_enabled=True,
    )
    ready = SimpleNamespace(v2_feature_status="READY", v2_profile_digest="a" * 64)
    with pytest.raises(promotion.PromotionRejected) as exc:
        promotion._require_v2_evidence(settings, [ready])
    assert exc.value.reason == "V2_ACTIVE_POLICY_UNAVAILABLE"


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_v1_authority_and_inactive_advisory_keep_existing_behavior(monkeypatch):
    """ATDD-OPIP-V2-ADVISORY-PARITY-M01/AC-003: Do not require policy in other modes."""
    def forbidden():
        raise AssertionError("No active-policy read when V2 advisory is inactive")

    monkeypatch.setattr(strategy_policy, "load_active_strategy_policy", forbidden)
    ready = SimpleNamespace(v2_feature_status="READY", v2_profile_digest=None)
    promotion._require_v2_evidence(
        SimpleNamespace(opip_production_scoring_version="v1", opip_advisory_profile_enabled=True),
        [ready],
    )
    promotion._require_v2_evidence(
        SimpleNamespace(opip_production_scoring_version="v2", opip_advisory_profile_enabled=False),
        [ready],
    )
