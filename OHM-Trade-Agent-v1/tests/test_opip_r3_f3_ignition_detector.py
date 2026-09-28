"""R3 F3 IGNITION detector runtime: contract-stage acceptance skeletons.

``ATDD-R3-F3-ignition-detector`` freezes the acceptance criteria for the pure
IGNITION ``evaluate(FeatureSnapshot, DetectorState, evaluation_time)`` detector.

The detector implementation is a later, OWNER-approved implementation
increment. Every criterion below is therefore an explicit, non-executing
skeleton: each test skips with the same stated reason and no skeleton fabricates
a detector result or asserts behaviour that is not implemented yet. The
implementation increment completes these bodies in place; it does not replace
this file.
"""

from __future__ import annotations

import pytest

_DEFERRED = (
    "Contract-stage skeleton for ATDD-R3-F3-ignition-detector: the F3 IGNITION "
    "detector runtime is a later, OWNER-approved implementation increment."
)


@pytest.mark.acceptance
def test_ac_001_evaluation_is_pure() -> None:
    """ATDD-R3-F3-ignition-detector/AC-001: evaluate() performs no I/O and reads no hidden state."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_002_repeated_evaluation_is_deterministic() -> None:
    """ATDD-R3-F3-ignition-detector/AC-002: identical inputs replay to equivalent claims and state."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_003_explicit_evaluation_time_uses_declared_grid() -> None:
    """ATDD-R3-F3-ignition-detector/AC-003: the 60-second grid sets cadence, not longer features."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_004_transition_owned_by_detector_not_storage() -> None:
    """ATDD-R3-F3-ignition-detector/AC-004: the detector owns transitions and performs no persistence."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_005_material_gap_resets_persistence() -> None:
    """ATDD-R3-F3-ignition-detector/AC-005: persistence does not accrue across a material gap."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_006_missing_or_invalid_evidence_is_not_favourable() -> None:
    """ATDD-R3-F3-ignition-detector/AC-006: missing or invalid evidence fails closed."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_007_transition_identity_is_not_wall_clock() -> None:
    """ATDD-R3-F3-ignition-detector/AC-007: claim identity derives from the transition, not the clock."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_008_claims_do_not_create_opportunity_lifecycles() -> None:
    """ATDD-R3-F3-ignition-detector/AC-008: claims create no episode, deadline or terminal reason."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_009_supplied_prior_state_continues_deterministically() -> None:
    """ATDD-R3-F3-ignition-detector/AC-009: a supplied prior DetectorState continues without defaulting."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_010_shadow_isolation_grants_no_authority() -> None:
    """ATDD-R3-F3-ignition-detector/AC-010: no run_cycle wiring, Feature Bus activation or authority change."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_011_evaluation_is_bound_to_the_sealed_snapshot_identity() -> None:
    """ATDD-R3-F3-ignition-detector/AC-011: claims bind to the sealed snapshot identity and content hash."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_012_detector_policy_version_is_carried_and_checked() -> None:
    """ATDD-R3-F3-ignition-detector/AC-012: the detector/policy version is carried and mismatches fail closed."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_013_instrument_identity_must_match_prior_state() -> None:
    """ATDD-R3-F3-ignition-detector/AC-013: an instrument identity mismatch fails closed."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_014_evaluation_time_is_explicit_and_valid() -> None:
    """ATDD-R3-F3-ignition-detector/AC-014: evaluation_time is explicit, grid-valid and never read from a clock."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_015_persistence_and_reset_state_is_typed() -> None:
    """ATDD-R3-F3-ignition-detector/AC-015: phase, persistence-timer and reset values are typed exactly."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_016_no_claim_evaluation_is_valid_and_deterministic() -> None:
    """ATDD-R3-F3-ignition-detector/AC-016: a no-claim evaluation is valid, empty and deterministic."""
    pytest.skip(_DEFERRED)
