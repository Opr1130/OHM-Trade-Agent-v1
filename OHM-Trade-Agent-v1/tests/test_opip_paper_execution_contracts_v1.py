from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    ENGINE_ROLE_BY_ENGINE,
    EvaluationPopulation,
    ExecutionState,
    PAPER_ECONOMIC_MODEL_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
    PAPER_PROTECTION_MODEL_VERSION,
    PaperEngineRole,
    PaperExecutionLineage,
    PositionState,
    ProtectionState,
    QualifiedOpportunityDisposition,
    TemporalBasis,
    TemporalEvidence,
    TemporalPrecision,
    TerminalReconciliationState,
    protection_trigger_implies_exit,
)
from app.opip.contracts.paper_outcome import (
    ENGINE_FREQTRADE_DRY_RUN,
    ENGINE_OHM_PAPER_SIM,
)


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 18, hour, minute, tzinfo=timezone.utc)


def test_engine_roles_freeze_primary_reference_and_legacy_populations():
    assert ENGINE_ROLE_BY_ENGINE == {
        ENGINE_OPIP_PAPER_V2: PaperEngineRole.PRIMARY,
        ENGINE_FREQTRADE_DRY_RUN: PaperEngineRole.REFERENCE,
        ENGINE_OHM_PAPER_SIM: PaperEngineRole.LEGACY,
    }


def test_opip_v2_lineage_is_normalized_and_versioned():
    lineage = PaperExecutionLineage(
        decision_context_id="ctx-1",
        paper_trade_id="paper-1",
        engine=ENGINE_OPIP_PAPER_V2,
        quote_currency="USD",
    )

    assert lineage.engine_role is PaperEngineRole.PRIMARY
    assert lineage.as_dict() == {
        "schema_version": 1,
        "decision_context_id": "ctx-1",
        "paper_trade_id": "paper-1",
        "engine": ENGINE_OPIP_PAPER_V2,
        "engine_role": "PRIMARY",
        "quote_currency": "USD",
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }


@pytest.mark.parametrize("field_name", ["decision_context_id", "paper_trade_id"])
def test_v2_lineage_requires_exact_root_identifiers(field_name):
    values = {
        "decision_context_id": "ctx-1",
        "paper_trade_id": "paper-1",
        "engine": ENGINE_OPIP_PAPER_V2,
        "quote_currency": "USD",
    }
    values[field_name] = "  "

    with pytest.raises(ValueError, match=field_name):
        PaperExecutionLineage(**values)


def test_v2_lineage_rejects_unknown_quote_currency():
    with pytest.raises(ValueError, match="quote currency"):
        PaperExecutionLineage(
            decision_context_id="ctx-1",
            paper_trade_id="paper-1",
            engine=ENGINE_OPIP_PAPER_V2,
            quote_currency="USD_EQUIVALENT",
        )


def test_v2_lineage_rejects_silent_model_reinterpretation():
    with pytest.raises(ValueError, match="frozen v2"):
        PaperExecutionLineage(
            decision_context_id="ctx-1",
            paper_trade_id="paper-1",
            engine=ENGINE_OPIP_PAPER_V2,
            quote_currency="USD",
            execution_model_version="future-model",
        )


def test_exact_temporal_evidence_preserves_only_proven_point_time():
    evidence = TemporalEvidence(
        precision=TemporalPrecision.EXACT,
        basis=TemporalBasis.SOURCE_REPORTED,
        occurred_at=_utc(12, 1),
    )

    assert evidence.as_dict() == {
        "precision": "EXACT",
        "basis": "SOURCE_REPORTED",
        "occurred_at": "2026-09-18T12:01:00Z",
    }


def test_bounded_temporal_evidence_preserves_intrabar_uncertainty():
    evidence = TemporalEvidence(
        precision=TemporalPrecision.BOUNDED,
        basis=TemporalBasis.MODEL_ASSIGNED,
        window_start=_utc(12, 0),
        window_end=_utc(12, 15),
    )

    assert evidence.as_dict() == {
        "precision": "BOUNDED",
        "basis": "MODEL_ASSIGNED",
        "window_start": "2026-09-18T12:00:00Z",
        "window_end": "2026-09-18T12:15:00Z",
    }


def test_bounded_temporal_evidence_rejects_impossible_order():
    with pytest.raises(ValueError, match="window_end"):
        TemporalEvidence(
            precision=TemporalPrecision.BOUNDED,
            basis=TemporalBasis.LOCALLY_OBSERVED,
            window_start=_utc(12, 15),
            window_end=_utc(12, 0),
        )


def test_unknown_temporal_evidence_requires_reason_and_no_fake_timestamp():
    evidence = TemporalEvidence(
        precision=TemporalPrecision.UNKNOWN,
        basis=TemporalBasis.LOCALLY_OBSERVED,
        reason="market observation unavailable",
    )
    assert evidence.as_dict()["reason"] == "market observation unavailable"

    with pytest.raises(ValueError, match="cannot carry timestamps"):
        TemporalEvidence(
            precision=TemporalPrecision.UNKNOWN,
            basis=TemporalBasis.LOCALLY_OBSERVED,
            occurred_at=_utc(12, 0),
            reason="unknown",
        )


def test_temporal_evidence_rejects_naive_datetime():
    with pytest.raises(ValueError):
        TemporalEvidence(
            precision=TemporalPrecision.EXACT,
            basis=TemporalBasis.LOCALLY_OBSERVED,
            occurred_at=datetime(2026, 9, 18, 12, 0),
        )


def test_qualified_opportunity_disposition_is_closed_vocabulary():
    assert {item.value for item in QualifiedOpportunityDisposition} == {
        "ADMITTED",
        "CAPACITY_REJECTED",
        "ALREADY_TRACKED",
        "CAPITAL_REJECTED",
        "NOT_ACTIONABLE",
        "NO_FILL_EXPIRED",
        "DO_NOT_CHASE",
        "CANCELLED",
        "UNSUPPORTED",
        "UNRESOLVED",
    }


def test_evaluation_populations_never_alias():
    assert {item.value for item in EvaluationPopulation} == {
        "QUALIFIED_INTENT",
        "ACTUAL_REALIZED",
        "COUNTERFACTUAL_POLICY",
        "HINDSIGHT_DIAGNOSTIC",
    }


def test_execution_position_protection_and_terminal_states_are_explicit():
    assert {item.value for item in ExecutionState} == {
        "INTENT_RECORDED",
        "ACCEPTED",
        "WORKING",
        "PARTIALLY_FILLED",
        "FILLED",
        "REJECTED",
        "CANCELLED",
        "EXPIRED",
    }
    assert {item.value for item in PositionState} == {
        "NO_POSITION",
        "OPEN",
        "REDUCING",
        "FLAT",
    }
    assert {item.value for item in ProtectionState} == {
        "PLANNED",
        "ACTIVE",
        "DEGRADED",
        "TRIGGERED",
    }
    assert {item.value for item in TerminalReconciliationState} == {
        "OPEN",
        "FLAT_AWAITING_RECONCILIATION",
        "FINAL_VERIFIED",
        "UNRESOLVED_EVIDENCE",
    }


def test_protection_trigger_is_not_an_exit():
    assert protection_trigger_implies_exit() is False
