"""AC-026 Phase 2: EVIDENCE_SHADOW readiness decision tests.

These tests prove the pure, read-only readiness decision is deterministic,
fails closed, keeps ``VERIFIED_UNMANAGED`` exposure visible and advisory, and
never mutates strict F11 semantics.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services import protection_health as f11
from app.services.evidence_shadow_readiness import (
    ADVISORY_CANDIDATE_RECOVERABLE_INCIDENT,
    ADVISORY_UNMANAGED_EXPOSURE,
    BLOCK_COVERAGE_INCOMPLETE,
    BLOCK_EVIDENCE_MALFORMED,
    BLOCK_EXPOSURE_UNCERTAIN,
    BLOCK_GEOMETRY_INVALID,
    BLOCK_INCIDENT_OPEN,
    BLOCK_INCIDENT_UNKNOWN,
    BLOCK_INCIDENT_UNREADABLE,
    BLOCK_POSITION_VERIFICATION_GAP,
    BLOCK_PRICING_GAP,
    BLOCK_SILENT_HOLDING,
    STATE_BLOCKED,
    STATE_READY,
    evaluate_evidence_shadow_readiness,
)
from app.services.system_incidents import SystemIncidentScope


@dataclass
class _Trade:
    direction: str
    entry_price: float
    stop_price: float


@dataclass
class _Exposure:
    status: str
    symbol: str
    trade: _Trade | None = None


def _managed(symbol: str, *, direction: str = "LONG", entry: float = 100.0, stop: float = 95.0) -> _Exposure:
    return _Exposure(
        status="VERIFIED_MANAGED",
        symbol=symbol,
        trade=_Trade(direction=direction, entry_price=entry, stop_price=stop),
    )


def _unmanaged(symbol: str) -> _Exposure:
    return _Exposure(status="VERIFIED_UNMANAGED", symbol=symbol)


def _incident(scope: str) -> dict:
    return {"scope": scope, "incident_key": f"SYSTEM_HEALTH:{scope}"}


# 1. unmanaged-only + complete observation => READY
def test_unmanaged_only_with_complete_coverage_is_ready():
    result = evaluate_evidence_shadow_readiness(
        [_unmanaged("XBTUSD")],
        coverage_complete=True,
    )
    assert result.ready is True
    assert result.state == STATE_READY
    assert result.blocking_reason_codes == ()
    assert ADVISORY_UNMANAGED_EXPOSURE in result.advisory_reason_codes


# 2. unmanaged exposures remain visible
def test_unmanaged_exposures_remain_visible():
    result = evaluate_evidence_shadow_readiness(
        [_unmanaged("XBTUSD"), _unmanaged("ETHUSD")],
        coverage_complete=True,
    )
    assert result.unmanaged_exposures == ("ETHUSD", "XBTUSD")
    assert result.ready is True


# 3. incomplete coverage => BLOCKED
def test_incomplete_coverage_blocks():
    result = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=False,
    )
    assert result.ready is False
    assert result.state == STATE_BLOCKED
    assert BLOCK_COVERAGE_INCOMPLETE in result.blocking_reason_codes


def test_unproven_coverage_blocks():
    result = evaluate_evidence_shadow_readiness([], coverage_complete=None)
    assert result.ready is False
    assert BLOCK_COVERAGE_INCOMPLETE in result.blocking_reason_codes


# 4. uncertain exposure => BLOCKED
def test_uncertain_exposure_blocks():
    result = evaluate_evidence_shadow_readiness(
        [_Exposure(status="DEGRADED", symbol="XBTUSD")],
        coverage_complete=True,
    )
    assert result.ready is False
    assert BLOCK_EXPOSURE_UNCERTAIN in result.blocking_reason_codes
    assert result.uncertain_exposures == ("XBTUSD",)


# 5. silent managed holding => BLOCKED
def test_silent_managed_holding_blocks():
    result = evaluate_evidence_shadow_readiness(
        [_managed("XBTUSD", stop=0.0)],
        coverage_complete=True,
    )
    assert result.ready is False
    assert BLOCK_SILENT_HOLDING in result.blocking_reason_codes
    assert result.silent_holdings == ("XBTUSD",)


# 6. invalid geometry => BLOCKED
def test_invalid_geometry_blocks():
    # LONG with stop above entry is not a protection plan.
    result = evaluate_evidence_shadow_readiness(
        [_managed("XBTUSD", direction="LONG", entry=100.0, stop=105.0)],
        coverage_complete=True,
    )
    assert result.ready is False
    assert BLOCK_GEOMETRY_INVALID in result.blocking_reason_codes
    assert result.geometry_invalid_exposures == ("XBTUSD",)


# 7. valid pricing-coverage incident can be candidate-recoverable
def test_pricing_incident_candidate_recoverable_with_proven_predicate():
    scope = SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value
    result = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=True,
        open_incidents=[_incident(scope)],
        current_degraded_scopes=frozenset(),
    )
    assert result.ready is True
    assert result.candidate_recoverable_incidents == (f"SYSTEM_HEALTH:{scope}",)
    assert ADVISORY_CANDIDATE_RECOVERABLE_INCIDENT in result.advisory_reason_codes
    assert result.blocking_incidents == ()


# 8. valid position-verification incident can be candidate-recoverable
def test_position_verification_incident_candidate_recoverable_with_proven_predicate():
    scope = SystemIncidentScope.KRAKEN_POSITION_VERIFICATION.value
    result = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=True,
        open_incidents=[_incident(scope)],
        current_degraded_scopes=frozenset(),
    )
    assert result.ready is True
    assert result.candidate_recoverable_incidents == (f"SYSTEM_HEALTH:{scope}",)


# 9. current/unproven coverage failure remains BLOCKED
def test_pricing_incident_with_unproven_current_degradation_blocks():
    scope = SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value
    result = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=True,
        open_incidents=[_incident(scope)],
        current_degraded_scopes=None,
    )
    assert result.ready is False
    assert BLOCK_PRICING_GAP in result.blocking_reason_codes
    assert result.blocking_incidents == (f"SYSTEM_HEALTH:{scope}",)


def test_pricing_incident_with_current_scope_degraded_blocks():
    scope = SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value
    result = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=True,
        open_incidents=[_incident(scope)],
        current_degraded_scopes={scope},
    )
    assert result.ready is False
    assert BLOCK_PRICING_GAP in result.blocking_reason_codes


def test_position_verification_incident_with_current_scope_degraded_blocks():
    scope = SystemIncidentScope.KRAKEN_POSITION_VERIFICATION.value
    result = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=True,
        open_incidents=[_incident(scope)],
        current_degraded_scopes={scope},
    )
    assert result.ready is False
    assert BLOCK_POSITION_VERIFICATION_GAP in result.blocking_reason_codes


def test_coverage_incident_with_incomplete_coverage_blocks():
    scope = SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value
    result = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=False,
        open_incidents=[_incident(scope)],
        current_degraded_scopes=frozenset(),
    )
    assert result.ready is False
    assert BLOCK_PRICING_GAP in result.blocking_reason_codes


# 10. auth/connectivity/rate-limit incident remains BLOCKED
@pytest.mark.parametrize(
    "scope",
    [
        SystemIncidentScope.KRAKEN_READ_ONLY_AUTH.value,
        SystemIncidentScope.KRAKEN_PUBLIC.value,
        SystemIncidentScope.KRAKEN_READ_ONLY.value,
        SystemIncidentScope.KRAKEN_RATE_LIMIT.value,
    ],
)
def test_non_coverage_incidents_block(scope):
    result = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=True,
        open_incidents=[_incident(scope)],
        current_degraded_scopes=frozenset(),
    )
    assert result.ready is False
    assert BLOCK_INCIDENT_OPEN in result.blocking_reason_codes
    assert BLOCK_INCIDENT_UNKNOWN not in result.blocking_reason_codes
    assert result.blocking_incidents == (f"SYSTEM_HEALTH:{scope}",)


# 11. unknown incident remains BLOCKED
def test_unknown_incident_blocks():
    result = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=True,
        open_incidents=[{"scope": "SOMETHING:ELSE"}],
    )
    assert result.ready is False
    assert BLOCK_INCIDENT_UNKNOWN in result.blocking_reason_codes
    assert BLOCK_INCIDENT_OPEN not in result.blocking_reason_codes


def test_incident_without_scope_is_malformed():
    result = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=True,
        open_incidents=[{}],
    )
    assert result.ready is False
    assert BLOCK_EVIDENCE_MALFORMED in result.blocking_reason_codes


# 11b. unreadable incident evidence is never an empty incident set
def test_unreadable_incident_evidence_blocks_and_is_never_empty():
    result = evaluate_evidence_shadow_readiness(
        [],
        coverage_complete=True,
        open_incidents=None,
    )
    assert result.ready is False
    assert result.state == STATE_BLOCKED
    assert BLOCK_INCIDENT_UNREADABLE in result.blocking_reason_codes
    assert BLOCK_INCIDENT_OPEN not in result.blocking_reason_codes
    assert BLOCK_EVIDENCE_MALFORMED not in result.blocking_reason_codes


# 12. decision performs no mutation
def test_decision_performs_no_mutation():
    exposures = [_unmanaged("XBTUSD"), _managed("ETHUSD")]
    incidents = [_incident(SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value)]
    degraded = {SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value}
    exposures_snapshot = list(exposures)
    incidents_snapshot = [dict(i) for i in incidents]
    degraded_snapshot = set(degraded)

    evaluate_evidence_shadow_readiness(
        exposures,
        coverage_complete=True,
        open_incidents=incidents,
        current_degraded_scopes=degraded,
    )

    assert exposures == exposures_snapshot
    assert incidents == incidents_snapshot
    assert degraded == degraded_snapshot


# 13. strict F11 semantics remain untouched
def test_strict_f11_still_blocks_unmanaged_only():
    exposures = [_unmanaged("XBTUSD")]
    f11_result = f11.evaluate_protection_health(
        exposures,
        coverage_complete=True,
        incidents_healthy=True,
    )
    assert f11_result.healthy is False
    assert f11_result.admissions_suspended is True
    assert f11.REASON_UNMANAGED_EXPOSURE in f11_result.reason_codes

    shadow = evaluate_evidence_shadow_readiness(
        exposures,
        coverage_complete=True,
    )
    assert shadow.ready is True


def test_f11_healthy_implies_shadow_ready_for_managed_only():
    exposures = [_managed("XBTUSD")]
    f11_result = f11.evaluate_protection_health(
        exposures,
        coverage_complete=True,
        incidents_healthy=True,
    )
    assert f11_result.healthy is True
    shadow = evaluate_evidence_shadow_readiness(
        exposures,
        coverage_complete=True,
    )
    assert shadow.ready is True
