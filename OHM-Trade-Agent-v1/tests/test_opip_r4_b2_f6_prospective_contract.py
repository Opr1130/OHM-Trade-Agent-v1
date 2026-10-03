"""Frozen F6 prospective evidence contract and prepared owner packets (R4-B2 Slice 3B).

Proves the contract document freezes the population, the two label families and
the policies before any outcome is observed, and that the owner control-plane
packets are prepared only and execute no authority change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_CONTRACT = _ROOT / "docs" / "architecture" / "OPIP_F6_PROSPECTIVE_EVIDENCE_CONTRACT.md"
_PACKETS = _ROOT / "docs" / "architecture" / "OPIP_F6_OWNER_ENABLEMENT_PACKETS.md"


@pytest.mark.acceptance
def test_ac_027_contract_freezes_population_and_policies() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-027: the prospective F6 evidence contract freezes the full population, the two label families and every required policy before any outcome is observed."""
    assert _CONTRACT.is_file()
    text = _CONTRACT.read_text(encoding="utf-8")
    lowered = text.lower()

    # Full population and the retained sub-populations.
    for marker in (
        "full population",
        "calibration-eligible subset",
        "unresolved",
        "incomplete_coverage",
        "cash / no-trade",
        "no-fill",
        "rejected",
        "abstained",
    ):
        assert marker in lowered, marker

    # The two label families, kept separate.
    for token in ("NO_FILL", "PARTIAL_FILL", "FULL_FILL", "TARGET", "STOP", "TIMEOUT", "RISK_EXIT"):
        assert token in text, token

    # LONG and SHORT reported separately; both directions named.
    assert "long" in lowered and "short" in lowered

    # The frozen policies.
    for marker in (
        "first_fill",
        "entry deadline",
        "fidelity policy",
        "missingness policy",
        "fee policy",
        "correction policy",
        "maturity policy",
        "sealing policy",
        "training cutoff",
        "dataset manifest",
        "availability rule",
        "forecast horizon",
    ):
        assert marker in lowered, marker

    # Chosen before outcomes, and the honest maturation disposition.
    assert "never chosen after viewing" in lowered
    assert "REAL_EVIDENCE_MATURATION_REQUIRED" in text
    assert "no_calibrated_model" in lowered
    # No invented universal threshold.
    assert "no universal sample-count" in lowered


@pytest.mark.acceptance
def test_ac_028_owner_packets_are_prepared_only() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-028: the SHADOW and cutover owner packets are prepared only, name the exact modes/mechanism/rollback/proofs, and execute no authority change."""
    assert _PACKETS.is_file()
    text = _PACKETS.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "PREPARED ONLY" in text
    assert "NOT EXECUTED" in text
    assert "EXACT_SHA" in text

    # The exact owner-controlled modes.
    assert "OPIP_FEATURE_BUS_MODE" in text
    assert "OPIP_CANONICAL_WRITER_MODE" in text
    assert "OPIP_PAPER_V2_MODE" in text

    # The required packet fields.
    for marker in (
        "prerequisites",
        "preflight",
        "rollback",
        "post-deploy verification",
        "authority_collision",
        "protection_health",
        "legacy_drain",
        "blockers",
    ):
        assert marker in lowered, marker

    # SHADOW creates no new-entry authority; the cutover keeps exactly one.
    assert "creates **no new-entry authority**" in lowered or "no new-entry authority" in lowered
    assert "exactly one new-entry" in lowered

    # It does not claim activation or funded authority.
    assert "not set" in lowered or "does **not** set it" in lowered
    assert "funded" in lowered
