"""Acceptance coverage for the private strategy draft editor.

Draft writes never become effective strategy or trading authorization.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import dashboard
from app.core.strategy_policy import load_strategy_policy
from app.services.strategy_admin_drafts import (
    StrategyDraftConflict, read_policy_draft, save_policy_draft,
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
