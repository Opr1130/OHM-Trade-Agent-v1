"""Acceptance checks for the O'Pip v6 version stamp (v6 slice A).

The stamp is the identity block that labels O'Pip evidence: which strategy, which
model (explicit ``None`` while no ML build is declared) and which gate policy
actually produced a record. These tests pin the reuse contract of the existing
``app.opip.decision.versioning`` module instead of a second versioning source.
"""

from __future__ import annotations

import pytest

from app.opip.decision.versioning import (
    MODEL_VERSION,
    STRATEGY_VERSION,
    gate_policy_fingerprint,
    version_stamp,
)

REQUIRED_KEYS = ("strategy_version", "model_version", "gate_policy_fingerprint")


@pytest.mark.acceptance
def test_version_stamp_carries_strategy_model_and_gate_fingerprint() -> None:
    """ATDD-OPIP-V6-STAMP-v1/AC-001"""
    stamp = version_stamp()
    assert isinstance(stamp, dict)
    for key in REQUIRED_KEYS:
        assert key in stamp, key
    assert stamp["strategy_version"] == STRATEGY_VERSION
    assert stamp["strategy_version"]
    # No ML build is declared yet, so the model is an explicit null rather than
    # an absent field or an invented label.
    assert stamp["model_version"] == MODEL_VERSION
    assert stamp["model_version"] is None
    assert stamp["gate_policy_fingerprint"] == gate_policy_fingerprint()
    assert stamp["gate_policy_fingerprint"].startswith("GPF:")


@pytest.mark.acceptance
def test_two_stamps_with_unchanged_thresholds_are_equal() -> None:
    """ATDD-OPIP-V6-STAMP-v1/AC-002"""
    first = version_stamp()
    second = version_stamp()
    assert first == second
    assert first["gate_policy_fingerprint"] == gate_policy_fingerprint()

    # Purity: a caller mutating its own copy must not reach the stamp source.
    first["strategy_version"] = "MUTATED-BY-CALLER"
    third = version_stamp()
    assert third["strategy_version"] == STRATEGY_VERSION
    assert third == second
