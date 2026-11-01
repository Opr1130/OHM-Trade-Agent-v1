"""Acceptance coverage for the private strategy draft editor.

Draft writes never become effective strategy or trading authorization.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import dashboard
from app.core.strategy_policy import load_strategy_policy, load_active_strategy_policy
from app.services.strategy_admin_drafts import (
    StrategyDraftConflict, read_policy_draft, save_policy_draft,
    activate_policy_draft, rollback_active_policy, read_active_policy_status,
)


@pytest.mark.acceptance
def test_advisory_draft_saves_atomically_without_runtime_activation(tmp_path: Path):
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-005: saved draft is non-authoritative."""
    original = read_policy_draft(tmp_path)
    assert original["status"] == "REFERENCE_ONLY"
    proposed = original["document"]
    proposed["scoring"]["v2_threshold"] = 78
    proposed["advisory_economics"]["min_net_profit_usd"] = 0
    saved = save_policy_draft(proposed, expected_digest=original["digest"], directory=tmp_path)
    assert saved["active_runtime_changed"] is False
    assert saved["status"] == "DRAFT_SAVED"
    refreshed = read_policy_draft(tmp_path)
    assert refreshed["digest"] == saved["digest"]
    assert refreshed["document"]["scoring"]["v2_threshold"] == 78
    assert (tmp_path / "audit.jsonl").is_file()
    assert load_strategy_policy().scoring["production"] == "v1"


@pytest.mark.acceptance
def test_protected_trading_authority_cannot_be_changed_by_draft(tmp_path: Path):
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-005: admin cannot grant trade permission."""
    current = read_policy_draft(tmp_path)
    proposed = current["document"]
    proposed["protection"]["allow_funded_orders"] = True
    with pytest.raises(ValueError, match="PROTECTED_SETTING"):
        save_policy_draft(proposed, expected_digest=current["digest"], directory=tmp_path)
    assert not (tmp_path / "draft.json").exists()


@pytest.mark.acceptance
def test_stale_expected_digest_is_rejected(tmp_path: Path):
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-005: concurrent edits require refresh."""
    old = read_policy_draft(tmp_path)
    proposed = old["document"]
    proposed["scoring"]["v2_threshold"] = 78
    save_policy_draft(proposed, expected_digest=old["digest"], directory=tmp_path)
    proposed["scoring"]["v2_threshold"] = 79
    with pytest.raises(StrategyDraftConflict):
        save_policy_draft(proposed, expected_digest=old["digest"], directory=tmp_path)


@pytest.mark.acceptance
def test_separate_admin_credential_required(monkeypatch):
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-005: admin page has separate write boundary."""
    monkeypatch.setattr(
        dashboard, "get_settings",
        lambda: SimpleNamespace(webhook_secret="operator-test-secret", strategy_admin_secret=None),
    )
    with pytest.raises(HTTPException) as exc:
        dashboard._require_strategy_admin("operator-test-secret", "anything")
    assert exc.value.status_code == 404


@pytest.mark.acceptance
def test_only_allowed_economic_and_v2_fields_are_editable(tmp_path: Path):
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-005: V1 and candidate caps are protected."""
    original = read_policy_draft(tmp_path)
    proposed = original["document"]
    proposed["scoring"]["v1_threshold"] = 74
    with pytest.raises(ValueError, match="PROTECTED_SETTING"):
        save_policy_draft(proposed, expected_digest=original["digest"], directory=tmp_path)


def test_invalid_proposed_section_is_validation_error(tmp_path: Path):
    current = read_policy_draft(tmp_path)
    proposed = json.loads(json.dumps(current["document"]))
    proposed["scoring"] = None
    with pytest.raises(ValueError, match="INVALID_STRATEGY_DRAFT_SECTION"):
        save_policy_draft(proposed, expected_digest=current["digest"], directory=tmp_path)


@pytest.mark.acceptance
def test_advisory_activation_and_rollback_are_atomic_and_versioned(tmp_path: Path):
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-008: active profile is reversible."""
    original = read_policy_draft(tmp_path)
    changed = original["document"]
    changed["scoring"]["v2_threshold"] = 79
    saved = save_policy_draft(changed, expected_digest=original["digest"], directory=tmp_path)
    assert read_active_policy_status(tmp_path)["status"] == "BUNDLED_DEFAULT"
    activated = activate_policy_draft(expected_digest=saved["digest"], directory=tmp_path)
    assert activated["funded_trading_activated"] is False
    assert read_active_policy_status(tmp_path)["digest"] == saved["digest"]
    assert load_active_strategy_policy(tmp_path / "active.json").scoring["v2_threshold"] == 79
    with pytest.raises(StrategyDraftConflict):
        rollback_active_policy(expected_digest="0"*64, directory=tmp_path)
    restored = rollback_active_policy(expected_digest=activated["digest"], directory=tmp_path)
    assert restored["digest"] == original["digest"]
    assert load_active_strategy_policy(tmp_path / "active.json").scoring["v2_threshold"] == 75
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "ACTIVATE_ADVISORY_PROFILE_INTENT" in audit
    assert "ROLLBACK_ADVISORY_PROFILE_INTENT" in audit


@pytest.mark.acceptance
def test_activation_refuses_drafts_without_saved_version(tmp_path: Path):
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-008: no implicit draft activation."""
    original = read_policy_draft(tmp_path)
    with pytest.raises(ValueError, match="NO_SAVED_DRAFT"):
        activate_policy_draft(expected_digest=original["digest"], directory=tmp_path)


@pytest.mark.acceptance
def test_unapproved_activation_endpoint_is_disabled(monkeypatch):
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-008: active edit is separately gated."""
    monkeypatch.setattr(
        dashboard, "get_settings",
        lambda: SimpleNamespace(
            strategy_admin_activation_enabled=False,
            opip_advisory_profile_enabled=True,
        ),
    )
    with pytest.raises(HTTPException) as exc:
        dashboard._require_strategy_activation()
    assert exc.value.status_code == 404
