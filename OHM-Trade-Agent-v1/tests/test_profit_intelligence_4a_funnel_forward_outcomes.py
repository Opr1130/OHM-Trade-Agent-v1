"""Profit Intelligence 4A tests: qualification funnel and forward outcomes.

These assert *behaviour and truthfulness*, not implementation shape:

* the canonical decision/reason taxonomy is published unchanged and a derived
  disposition never contradicts the class it came from, failing closed to
  ``UNKNOWN``;
* a bucket this evidence family cannot produce is declared, never fabricated as
  a measured zero;
* replayed evidence cannot inflate a population, and terminal dispositions
  reconcile to the population that entered the funnel;
* forward outcomes keep decision-time facts and future facts strictly separate,
  a forward window is anchored after the sealed cutoff, and incomplete or
  unavailable windows are never written as a ``0`` return;
* the funnel/forward-outcome join is evidence-only and never classifies a
  missed profitable opportunity;
* no HTTP surface, writer coupling or trading authority is introduced.

No network, no exchange and no wall-clock dependency: every fixture is
deterministic.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.opip.canonical.models import PaperV2Ledger
from app.opip.cockpit.ledger import build_ledger
from app.opip.decision.models import (
    DecisionOutcome,
    ReasonClass,
    ReasonCode,
)
from app.opip.profit_intelligence import (
    DISPOSITIONS_WITHOUT_CANONICAL_PRODUCER,
    FORWARD_OUTCOME_PROJECTION_VERSION,
    MISSED_OPPORTUNITY_DISPOSITION,
    MISSED_OPPORTUNITY_UNBLOCK,
    QUALIFICATION_FUNNEL_PROJECTION_VERSION,
    ForwardOutcomeJoinStatus,
    ForwardOutcomeSource,
    QualificationDisposition,
    build_forward_outcome_projection,
    build_profit_intelligence_overview,
    build_qualification_funnel_projection,
    disposition_for,
    forward_window_is_point_in_time,
    horizon_duration_seconds,
    join_funnel_record_to_forward_outcomes,
    read_forward_outcome_projection,
    read_qualification_funnel_projection,
    unavailable_forward_outcomes,
    unavailable_profit_intelligence,
    unavailable_qualification_funnel,
)

_NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
_REFERENCE_ISO = _NOW.isoformat()


def _empty_ledger():
    """A healthy empty canonical ledger, enough to exercise the envelope shape."""
    return build_ledger(PaperV2Ledger(status="OK", entries=()))


# ---------------------------------------------------------------------------
# Qualification funnel: disposition derivation
# ---------------------------------------------------------------------------


def _funnel_row(
    *,
    decision: str,
    reason_code: str | None = None,
    reason_class: str | None = None,
    gate: str | None = None,
    scan_id: str = "OPIPS:a",
    candidate_id: str = "OPIPC:b",
    episode_id: str | None = "EP:1",
    fingerprint: str = "GPF:aaa",
    app_schema: int | None = 1,
) -> dict:
    return {
        "record_type": "OPIP_QUALIFICATION_FUNNEL",
        "scan_id": scan_id,
        "cohort_id": "C1",
        "decision_at_utc": _REFERENCE_ISO,
        "schema_version": app_schema,
        "strategy_version": "OPIP-STRATEGY-V1",
        "intelligence_version": "OPIP-INTELLIGENCE-V1",
        "gate_policy_version": "OPIP-GATE-POLICY-V1",
        "gate_policy_fingerprint": fingerprint,
        "candidate_id": candidate_id,
        "episode_id": episode_id,
        "signal_id": "SIG:1",
        "asset": "BTC",
        "pair": "BTCUSD",
        "market_type": "SPOT",
        "direction": "LONG",
        "decided_at": _REFERENCE_ISO,
        "decision": decision,
        "first_terminal_gate": gate,
        "terminal_reason_code": reason_code,
        "terminal_reason_class": reason_class,
        "terminal_reason": "terminal reason text",
        "deepest_gate": gate,
        "counterfactual_eligible": decision == DecisionOutcome.REJECTED.value,
        "gate_results": [
            {
                "gate": gate,
                "status": "FAIL",
                "reason_code": reason_code,
                "reason_class": reason_class,
                "reason": "gate reason",
                "measured_value": 1.0,
                "threshold": 2.0,
                "threshold_distance": -0.5,
                "evaluated_at": _REFERENCE_ISO,
            }
        ]
        if gate
        else [],
    }


def test_qualified_row_maps_to_qualified():
    row = _funnel_row(decision=DecisionOutcome.QUALIFIED.value)
    assert disposition_for(row["decision"], None, None) is (
        QualificationDisposition.QUALIFIED
    )


def test_policy_rejection_maps_to_rejected_and_preserves_reason():
    row = _funnel_row(
        decision=DecisionOutcome.REJECTED.value,
        reason_code=ReasonCode.TARGET_ATTAINABILITY_FAILED.value,
        reason_class=ReasonClass.POLICY.value,
        gate="TARGET_QUALITY",
    )
    projection = build_qualification_funnel_projection([row], generated_at=_NOW)
    record = projection.records[0]
    assert record.disposition is QualificationDisposition.REJECTED
    # The canonical facts are published unchanged, not replaced by the derived one.
    assert record.decision == DecisionOutcome.REJECTED.value
    assert record.terminal_reason_code == ReasonCode.TARGET_ATTAINABILITY_FAILED.value
    assert record.terminal_reason_class == ReasonClass.POLICY.value


def test_model_stop_maps_to_filtered():
    assert disposition_for(
        DecisionOutcome.REJECTED.value,
        ReasonCode.AI_DECISION_WATCH.value,
        ReasonClass.MODEL.value,
    ) is QualificationDisposition.FILTERED


def test_capacity_block_is_not_a_rejection():
    assert disposition_for(
        DecisionOutcome.REJECTED.value,
        ReasonCode.NO_CAPITAL.value,
        ReasonClass.POLICY.value,
    ) is QualificationDisposition.CAPACITY_BLOCKED
    assert disposition_for(
        DecisionOutcome.REJECTED.value,
        ReasonCode.AI_BUDGET_LIMIT.value,
        ReasonClass.BUDGET.value,
    ) is QualificationDisposition.CAPACITY_BLOCKED


def test_operational_failure_maps_to_technically_unavailable():
    assert disposition_for(
        DecisionOutcome.OPERATIONAL_FAILURE.value,
        ReasonCode.AI_SERVICE_UNAVAILABLE.value,
        ReasonClass.OPERATIONAL.value,
    ) is QualificationDisposition.TECHNICALLY_UNAVAILABLE


def test_incomplete_maps_to_incomplete_not_rejected():
    assert disposition_for(
        DecisionOutcome.INCOMPLETE.value,
        ReasonCode.FUNNEL_INCOMPLETE.value,
        ReasonClass.OPERATIONAL.value,
    ) is QualificationDisposition.INCOMPLETE


def test_unmapped_reason_with_informational_class_fails_closed_to_unknown():
    # A REJECTED row cannot canonically be INFORMATIONAL; the projection must not
    # fold it into a neighbour bucket.
    assert disposition_for(
        DecisionOutcome.REJECTED.value,
        "NOT_A_CANONICAL_REASON",
        ReasonClass.INFORMATIONAL.value,
    ) is QualificationDisposition.UNKNOWN


def test_unknown_decision_fails_closed_to_unknown():
    assert disposition_for("NOT_A_DECISION", None, None) is (
        QualificationDisposition.UNKNOWN
    )
    assert disposition_for(
        DecisionOutcome.COUNTERFACTUAL_ELIGIBLE.value, None, None
    ) is QualificationDisposition.UNKNOWN


def test_risk_blocked_and_execution_failed_are_declared_not_produced():
    declared = set(DISPOSITIONS_WITHOUT_CANONICAL_PRODUCER)
    assert QualificationDisposition.RISK_BLOCKED in declared
    assert QualificationDisposition.EXECUTION_FAILED in declared
    # No canonical reason may ever yield a bucket this family cannot produce.
    produced = {
        disposition_for(
            outcome.value, code.value, code_class.value
        )
        for outcome in DecisionOutcome
        for code in ReasonCode
        for code_class in ReasonClass
    }
    assert QualificationDisposition.RISK_BLOCKED not in produced
    assert QualificationDisposition.EXECUTION_FAILED not in produced


# ---------------------------------------------------------------------------
# Qualification funnel: projection, conservation, idempotency
# ---------------------------------------------------------------------------


def _normal_population() -> list[dict]:
    return [
        _funnel_row(
            decision=DecisionOutcome.QUALIFIED.value,
            scan_id="S1",
            candidate_id="C1",
        ),
        _funnel_row(
            decision=DecisionOutcome.REJECTED.value,
            reason_code=ReasonCode.ECONOMIC_GATE_FAILED.value,
            reason_class=ReasonClass.POLICY.value,
            gate="ECONOMIC_QUALITY",
            scan_id="S1",
            candidate_id="C2",
        ),
        _funnel_row(
            decision=DecisionOutcome.OPERATIONAL_FAILURE.value,
            reason_code=ReasonCode.SNAPSHOT_MISSING.value,
            reason_class=ReasonClass.OPERATIONAL.value,
            gate="EXECUTION_VALIDATION",
            scan_id="S1",
            candidate_id="C3",
        ),
        _funnel_row(
            decision=DecisionOutcome.INCOMPLETE.value,
            reason_code=ReasonCode.FUNNEL_INCOMPLETE.value,
            reason_class=ReasonClass.OPERATIONAL.value,
            scan_id="S1",
            candidate_id="C4",
        ),
    ]


def test_funnel_conservation_holds_over_a_normal_population():
    projection = build_qualification_funnel_projection(
        _normal_population(), generated_at=_NOW
    )
    conservation = projection.conservation
    assert conservation.holds is True
    assert conservation.entered == 4
    assert conservation.qualified == 1
    assert conservation.rejected == 1
    assert conservation.operational_failure == 1
    assert conservation.incomplete == 1
    assert conservation.unattributed == 0


def test_funnel_bucket_counts_are_observed_dispositions_only():
    projection = build_qualification_funnel_projection(
        _normal_population(), generated_at=_NOW
    )
    assert projection.bucket_counts == {
        "QUALIFIED": 1,
        "REJECTED": 1,
        "TECHNICALLY_UNAVAILABLE": 1,
        "INCOMPLETE": 1,
    }


def test_replayed_rows_cannot_inflate_the_population():
    row = _funnel_row(
        decision=DecisionOutcome.QUALIFIED.value, scan_id="S1", candidate_id="C1"
    )
    projection = build_qualification_funnel_projection(
        [row, dict(row), dict(row)], generated_at=_NOW
    )
    assert len(projection.records) == 1
    assert projection.duplicate_rows_ignored == 2
    assert projection.conservation.entered == 1


def test_version_and_identity_are_preserved():
    projection = build_qualification_funnel_projection(
        _normal_population(), generated_at=_NOW
    )
    record = projection.records[0]
    assert record.candidate_id
    assert record.episode_id == "EP:1"
    assert record.strategy_version == "OPIP-STRATEGY-V1"
    assert record.gate_policy_version == "OPIP-GATE-POLICY-V1"
    assert record.gate_policy_fingerprint == "GPF:aaa"
    assert record.source_schema_version == 1
    assert projection.producer_version == "GPF:aaa"


def test_mixed_producer_fingerprints_are_reported_not_collapsed():
    rows = [
        _funnel_row(
            decision=DecisionOutcome.QUALIFIED.value,
            scan_id="S1",
            candidate_id="C1",
            fingerprint="GPF:aaa",
        ),
        _funnel_row(
            decision=DecisionOutcome.QUALIFIED.value,
            scan_id="S1",
            candidate_id="C2",
            fingerprint="GPF:bbb",
        ),
    ]
    projection = build_qualification_funnel_projection(rows, generated_at=_NOW)
    assert projection.producer_version == "MIXED[2]"


def test_row_without_identity_is_unavailable_and_breaks_conservation():
    row = _funnel_row(
        decision=DecisionOutcome.QUALIFIED.value, scan_id="S1", candidate_id="C1"
    )
    malformed = dict(row)
    malformed["candidate_id"] = None
    malformed["scan_id"] = None
    projection = build_qualification_funnel_projection(
        [row, malformed], generated_at=_NOW
    )
    unattributed = [
        record
        for record in projection.records
        if record.availability.value == "UNAVAILABLE"
    ]
    assert unattributed
    assert projection.conservation.holds is False


def test_terminal_gate_attribution_and_reason_counts():
    projection = build_qualification_funnel_projection(
        _normal_population(), generated_at=_NOW
    )
    assert projection.gate_counts.get("ECONOMIC_QUALITY") == 1
    assert (
        projection.reason_code_counts.get(ReasonCode.ECONOMIC_GATE_FAILED.value) == 1
    )


def test_projection_declares_versions_and_scope():
    projection = build_qualification_funnel_projection(
        _normal_population(), generated_at=_NOW
    )
    payload = projection.to_dict()
    assert payload["projection_version"] == QUALIFICATION_FUNNEL_PROJECTION_VERSION
    assert payload["scope"]
    assert payload["population_semantics"]
    assert payload["availability_semantics"]
    assert payload["source_provenance"]
    json.dumps(payload, allow_nan=False, sort_keys=True)


def test_unavailable_funnel_is_never_an_empty_healthy_result():
    projection = unavailable_qualification_funnel("STORE_UNREADABLE", generated_at=_NOW)
    assert projection.records == ()
    assert projection.bucket_counts == {}
    assert projection.trust.is_healthy is False


def test_funnel_projection_is_deterministic():
    rows = _normal_population()
    first = build_qualification_funnel_projection(rows, generated_at=_NOW).to_dict()
    second = build_qualification_funnel_projection(rows, generated_at=_NOW).to_dict()
    assert first == second


def test_funnel_projection_requires_timezone_aware_time():
    with pytest.raises(ValueError):
        build_qualification_funnel_projection(
            _normal_population(), generated_at=datetime(2026, 9, 20, 12, 0)
        )


# ---------------------------------------------------------------------------
# Forward outcomes: point-in-time and availability
# ---------------------------------------------------------------------------


def _phase3c_row(
    *,
    snapshot_id: str = "SNAP:1",
    episode_id: str | None = "EP:1",
    observed: bool = True,
    return_pct: float | None = 1.5,
    window_complete: bool = True,
    reference_at: str = _REFERENCE_ISO,
    schema_version: object = 2,
) -> dict:
    return {
        "label_schema_version": schema_version,
        "snapshot_id": snapshot_id,
        "symbol": "BTC",
        "reference_at": reference_at,
        "reference_price": 100.0,
        "canonical_episode_id": episode_id,
        "signal_episode_id": "SIG:1",
        "move_episode_id": None,
        "horizon_returns_pct": {"1h": return_pct, "4h": 2.0},
        "horizon_observed": {"1h": observed, "4h": True},
        "mfe_pct": 3.0,
        "mae_pct": -0.5,
        "window_complete": window_complete,
        "maturation_status": (
            "MATURE_24H" if window_complete else "PARTIAL_FORWARD_WINDOW"
        ),
        "outcome_source": "PROVISIONAL_EVENT_SAMPLED_FULL_MARKET_OBSERVATIONS",
    }


def _discovery_row(
    *,
    observation_id: str = "OBS:1",
    direction: str = "LONG",
) -> dict:
    horizon = {
        "horizon_observed": True,
        "window_complete": True,
        "maturation_status": "COMPLETE_HORIZON",
        "horizon_return_pct": 0.75,
        "mfe_pct": 1.5,
        "mae_pct": -0.25,
    }
    return {
        "schema_version": 1,
        "label_schema_version": 1,
        "outcome_definition": "DISCOVERY_OUTCOME_V1",
        "observation_id": observation_id,
        "observed_at": _REFERENCE_ISO,
        "reference_at": _REFERENCE_ISO,
        "reference_price": 100.0,
        "production_preferred_direction": direction,
        "direction": direction,
        "venue_instrument_id": "KRAKEN:BTCUSD",
        "canonical_underlying_asset": "BTC",
        "maturation_status": "COMPLETE_HORIZON",
        "window_complete": True,
        "mfe_pct": 3.0,
        "mae_pct": -1.0,
        "horizons": {"1h": dict(horizon), "12h": dict(horizon)},
    }


def test_point_in_time_window_must_be_anchored_after_the_cutoff():
    assert forward_window_is_point_in_time(
        reference_at=_NOW,
        window_start=_NOW,
        window_end=_NOW + timedelta(hours=1),
    )
    assert not forward_window_is_point_in_time(
        reference_at=_NOW,
        window_start=_NOW,
        window_end=_NOW,
    )
    assert not forward_window_is_point_in_time(
        reference_at=_NOW,
        window_start=_NOW - timedelta(hours=1),
        window_end=_NOW + timedelta(hours=1),
    )
    assert not forward_window_is_point_in_time(
        reference_at=None, window_start=_NOW, window_end=_NOW + timedelta(hours=1)
    )


def test_horizon_duration_uses_canonical_labels_without_renaming():
    assert horizon_duration_seconds("60m") == 3600
    assert horizon_duration_seconds("1h") == 3600
    assert horizon_duration_seconds("24h") == 86400
    assert horizon_duration_seconds("7h") is None


def test_phase3c_horizons_carry_their_window_and_return():
    projection = build_forward_outcome_projection(
        [_phase3c_row()], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    record = projection.records[0]
    hour = next(h for h in record.horizons if h.horizon_id == "1h")
    assert hour.window_start == _NOW
    assert hour.window_end == _NOW + timedelta(hours=1)
    assert hour.point_in_time is True
    assert hour.return_pct == 1.5
    assert hour.availability.value == "KNOWN"


def test_incomplete_window_stays_incomplete_and_is_derived_not_known():
    projection = build_forward_outcome_projection(
        [_phase3c_row(window_complete=False)],
        source=ForwardOutcomeSource.PHASE3C,
        generated_at=_NOW,
    )
    record = projection.records[0]
    assert record.window_complete is False
    hour = next(h for h in record.horizons if h.horizon_id == "1h")
    assert hour.window_complete is False
    assert hour.availability.value == "DERIVED"


def test_unavailable_market_data_is_never_a_zero_return():
    projection = build_forward_outcome_projection(
        [_phase3c_row(observed=False, return_pct=None)],
        source=ForwardOutcomeSource.PHASE3C,
        generated_at=_NOW,
    )
    hour = next(h for h in projection.records[0].horizons if h.horizon_id == "1h")
    assert hour.observed is False
    assert hour.return_pct is None
    assert hour.availability.value == "UNAVAILABLE"


def test_unavailable_horizon_carries_no_numeric_value():
    # A value present in the row but marked unobserved must not be published as a
    # number on an UNAVAILABLE horizon.
    row = _phase3c_row()
    row["horizon_returns_pct"] = {"1h": 5.0}
    row["horizon_observed"] = {"1h": False}
    projection = build_forward_outcome_projection(
        [row], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    hour = next(h for h in projection.records[0].horizons if h.horizon_id == "1h")
    assert hour.availability.value == "UNAVAILABLE"
    assert hour.return_pct is None
    assert hour.observed is False


def test_funnel_window_filters_dated_rows_and_keeps_undated_ones():
    inside = _funnel_row(
        decision=DecisionOutcome.QUALIFIED.value, scan_id="S1", candidate_id="C1"
    )
    outside = _funnel_row(
        decision=DecisionOutcome.QUALIFIED.value, scan_id="S1", candidate_id="C2"
    )
    outside["decided_at"] = (_NOW - timedelta(days=10)).isoformat()
    outside["decision_at_utc"] = outside["decided_at"]
    undated = _funnel_row(
        decision=DecisionOutcome.QUALIFIED.value, scan_id="S1", candidate_id="C3"
    )
    undated["decided_at"] = None
    undated["decision_at_utc"] = None
    projection = build_qualification_funnel_projection(
        [inside, outside, undated],
        generated_at=_NOW,
        window_start=_NOW - timedelta(hours=24),
        window_end=_NOW,
    )
    kept = {record.candidate_id for record in projection.records}
    assert kept == {"C1", "C3"}


def test_gate_boolean_measurement_is_not_coerced_to_a_number():
    row = _funnel_row(
        decision=DecisionOutcome.REJECTED.value,
        reason_code=ReasonCode.ECONOMIC_GATE_FAILED.value,
        reason_class=ReasonClass.POLICY.value,
        gate="ECONOMIC_QUALITY",
    )
    row["gate_results"][0]["measured_value"] = True
    projection = build_qualification_funnel_projection([row], generated_at=_NOW)
    assert projection.records[0].gates[0].measured_value is None


def test_unknown_horizon_label_fails_closed_to_unavailable():
    row = _phase3c_row()
    row["horizon_returns_pct"] = {"7h": 1.0}
    row["horizon_observed"] = {"7h": True}
    projection = build_forward_outcome_projection(
        [row], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    horizon = projection.records[0].horizons[0]
    assert horizon.horizon_id == "7h"
    assert horizon.horizon_seconds is None
    assert horizon.window_end is None
    assert horizon.point_in_time is False
    assert horizon.availability.value == "UNAVAILABLE"


def test_missing_reference_at_is_unavailable_and_not_point_in_time():
    row = _phase3c_row(reference_at="")
    projection = build_forward_outcome_projection(
        [row], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    record = projection.records[0]
    assert record.availability.value == "UNAVAILABLE"
    assert all(horizon.point_in_time is False for horizon in record.horizons)


def test_phase3c_excursions_are_record_level_not_misattributed_per_horizon():
    projection = build_forward_outcome_projection(
        [_phase3c_row()], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    record = projection.records[0]
    assert record.mfe_pct == 3.0
    assert record.mae_pct == -0.5
    assert all(horizon.mfe_pct is None for horizon in record.horizons)


def test_discovery_horizons_carry_per_horizon_excursions():
    from app.opip.discovery.constants import DISCOVERY_PRIMARY_HORIZON

    projection = build_forward_outcome_projection(
        [_discovery_row()], source=ForwardOutcomeSource.DISCOVERY, generated_at=_NOW
    )
    record = projection.records[0]
    assert record.direction == "LONG"
    assert record.venue_instrument_id == "KRAKEN:BTCUSD"
    # primary_horizon is not stamped by the producer row; it resolves from the
    # canonical constant rather than being dropped.
    assert record.primary_horizon == DISCOVERY_PRIMARY_HORIZON
    assert {horizon.horizon_id for horizon in record.horizons} == {"1h", "12h"}
    assert all(horizon.mfe_pct == 1.5 for horizon in record.horizons)


def test_outcome_calculation_version_is_preserved_and_mixed_reported():
    single = build_forward_outcome_projection(
        [_phase3c_row()], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    assert single.producer_version == "2"

    mixed = build_forward_outcome_projection(
        [_phase3c_row(snapshot_id="S1"), _phase3c_row(snapshot_id="S2", schema_version=1)],
        source=ForwardOutcomeSource.PHASE3C,
        generated_at=_NOW,
    )
    assert mixed.producer_version.startswith("MIXED")


def test_forward_duplicate_identity_does_not_inflate_the_population():
    row = _phase3c_row()
    projection = build_forward_outcome_projection(
        [row, dict(row)], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    assert len(projection.records) == 1
    assert projection.duplicate_rows_ignored == 1


def test_forward_later_revision_wins():
    # Append-only producers write increasing revisions; the projection must keep
    # the latest, not the earliest, so it does not publish the least-mature row.
    immature = _phase3c_row(
        snapshot_id="S1", window_complete=False, observed=False, return_pct=None
    )
    mature = _phase3c_row(
        snapshot_id="S1", window_complete=True, observed=True, return_pct=2.0
    )
    projection = build_forward_outcome_projection(
        [immature, mature], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    assert len(projection.records) == 1
    assert projection.records[0].window_complete is True
    hour = next(
        h for h in projection.records[0].horizons if h.horizon_id == "1h"
    )
    assert hour.return_pct == 2.0


def test_forward_later_unreadable_revision_does_not_erase_a_readable_one():
    readable = _phase3c_row(snapshot_id="S1", window_complete=True)
    unreadable = _phase3c_row(snapshot_id="S1")
    unreadable["reference_price"] = None
    projection = build_forward_outcome_projection(
        [readable, unreadable], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    assert projection.records[0].availability.value == "KNOWN"
    assert projection.records[0].window_complete is True


def test_forward_higher_revision_wins_regardless_of_read_order():
    low = _phase3c_row(snapshot_id="S1", window_complete=False, return_pct=None)
    low["outcome_revision"] = 1
    high = _phase3c_row(snapshot_id="S1", window_complete=True, return_pct=2.0)
    high["outcome_revision"] = 2
    projection = build_forward_outcome_projection(
        [high, low], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    assert projection.records[0].outcome_revision == 2
    assert projection.records[0].window_complete is True


def test_forward_projection_does_not_mutate_decision_time_context():
    projection = build_forward_outcome_projection(
        [_phase3c_row()], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    record = projection.records[0]
    assert record.reference_at == _NOW
    assert record.reference_price == 100.0
    # Future facts live on the horizons, never overwriting the cutoff facts.
    assert record.to_dict()["reference_at"] == "2026-09-20T12:00:00Z"


def test_forward_sources_are_never_merged():
    phase3c = build_forward_outcome_projection(
        [_phase3c_row()], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    discovery = build_forward_outcome_projection(
        [_discovery_row()], source=ForwardOutcomeSource.DISCOVERY, generated_at=_NOW
    )
    assert phase3c.source is ForwardOutcomeSource.PHASE3C
    assert discovery.source is ForwardOutcomeSource.DISCOVERY
    assert phase3c.records[0].identity_kind == "snapshot_id"
    assert discovery.records[0].identity_kind == "observation_id"


def test_forward_projection_declares_versions_and_scope():
    payload = build_forward_outcome_projection(
        [_phase3c_row()], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    ).to_dict()
    assert payload["projection_version"] == FORWARD_OUTCOME_PROJECTION_VERSION
    assert payload["scope"]
    assert payload["availability_semantics"]
    json.dumps(payload, allow_nan=False, sort_keys=True)


def test_unsupported_forward_source_is_rejected():
    with pytest.raises(ValueError):
        build_forward_outcome_projection(
            [], source="NOT_A_SOURCE", generated_at=_NOW
        )


def test_unavailable_forward_projection_is_never_healthy():
    projection = unavailable_forward_outcomes(
        "UNREADABLE", source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    assert projection.records == ()
    assert projection.completion is None
    assert projection.trust.is_healthy is False


# ---------------------------------------------------------------------------
# Evidence-only join
# ---------------------------------------------------------------------------


def _funnel_record(episode_id: str | None = "EP:1"):
    projection = build_qualification_funnel_projection(
        [
            _funnel_row(
                decision=DecisionOutcome.QUALIFIED.value, episode_id=episode_id
            )
        ],
        generated_at=_NOW,
    )
    return projection.records[0]


def test_join_matches_on_canonical_episode_identity():
    forward = build_forward_outcome_projection(
        [_phase3c_row(episode_id="EP:1")],
        source=ForwardOutcomeSource.PHASE3C,
        generated_at=_NOW,
    )
    result = join_funnel_record_to_forward_outcomes(
        _funnel_record("EP:1"), [forward]
    )
    assert result.status is ForwardOutcomeJoinStatus.MATCHED
    assert len(result.matches) == 1
    # Evidence only: the join never claims executable profit.
    assert result.consumption == "EVIDENCE_ONLY"
    assert result.classification == "MISSED_PROFIT_CLASSIFICATION_UNAVAILABLE"


def test_join_reports_no_match_without_inventing_one():
    forward = build_forward_outcome_projection(
        [_phase3c_row(episode_id="EP:OTHER")],
        source=ForwardOutcomeSource.PHASE3C,
        generated_at=_NOW,
    )
    result = join_funnel_record_to_forward_outcomes(_funnel_record("EP:1"), [forward])
    assert result.status is ForwardOutcomeJoinStatus.NO_MATCH


def test_join_refuses_an_ambiguous_match():
    forward = build_forward_outcome_projection(
        [
            _phase3c_row(snapshot_id="S1", episode_id="EP:1"),
            _phase3c_row(snapshot_id="S2", episode_id="EP:1"),
        ],
        source=ForwardOutcomeSource.PHASE3C,
        generated_at=_NOW,
    )
    result = join_funnel_record_to_forward_outcomes(_funnel_record("EP:1"), [forward])
    assert result.status is ForwardOutcomeJoinStatus.AMBIGUOUS
    assert len(result.matches) == 2


def test_join_is_identity_unavailable_without_an_episode():
    forward = build_forward_outcome_projection(
        [_phase3c_row(episode_id="EP:1")],
        source=ForwardOutcomeSource.PHASE3C,
        generated_at=_NOW,
    )
    result = join_funnel_record_to_forward_outcomes(_funnel_record(None), [forward])
    assert result.status is ForwardOutcomeJoinStatus.IDENTITY_UNAVAILABLE


def test_join_is_source_unavailable_without_a_projection():
    result = join_funnel_record_to_forward_outcomes(_funnel_record("EP:1"), [])
    assert result.status is ForwardOutcomeJoinStatus.SOURCE_UNAVAILABLE


def test_discovery_records_never_match_without_an_episode_bridge():
    discovery = build_forward_outcome_projection(
        [_discovery_row()], source=ForwardOutcomeSource.DISCOVERY, generated_at=_NOW
    )
    result = join_funnel_record_to_forward_outcomes(_funnel_record("EP:1"), [discovery])
    assert result.status is ForwardOutcomeJoinStatus.NO_MATCH


def test_join_serialises_without_nan():
    forward = build_forward_outcome_projection(
        [_phase3c_row()], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    payload = join_funnel_record_to_forward_outcomes(
        _funnel_record("EP:1"), [forward]
    ).to_dict()
    json.dumps(payload, allow_nan=False, sort_keys=True)


# ---------------------------------------------------------------------------
# Overview consumption
# ---------------------------------------------------------------------------


def test_overview_carries_injected_projections_read_only():
    funnel = build_qualification_funnel_projection(
        _normal_population(), generated_at=_NOW
    )
    forward = build_forward_outcome_projection(
        [_phase3c_row()], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    overview = build_profit_intelligence_overview(
        _empty_ledger(), qualification_funnel=funnel, forward_outcomes=[forward]
    )
    payload = overview.to_dict()
    assert payload["qualification_funnel"]["projection_version"] == (
        QUALIFICATION_FUNNEL_PROJECTION_VERSION
    )
    assert payload["forward_outcomes"][0]["projection_version"] == (
        FORWARD_OUTCOME_PROJECTION_VERSION
    )


def test_overview_without_projections_reports_absence_not_zero():
    payload = build_profit_intelligence_overview(_empty_ledger()).to_dict()
    assert payload["qualification_funnel"] is None
    assert payload["forward_outcomes"] == []


def test_healthy_and_unavailable_overviews_keep_the_same_shape():
    healthy = build_profit_intelligence_overview(_empty_ledger()).to_dict()
    broken = unavailable_profit_intelligence("LEDGER_NOT_HEALTHY").to_dict()
    assert set(healthy) == set(broken)


def test_overview_publishes_missed_opportunity_unblock_evidence():
    payload = build_profit_intelligence_overview(_empty_ledger()).to_dict()
    missed = payload["missed_opportunity"]
    assert missed["disposition"] == MISSED_OPPORTUNITY_DISPOSITION
    unblock = missed["unblock_evidence"]
    assert unblock["missing_ingredients"]
    assert unblock["required_producer"]
    json.dumps(payload, allow_nan=False, sort_keys=True)


def test_unblock_evidence_declares_the_blocked_disposition():
    payload = MISSED_OPPORTUNITY_UNBLOCK.to_dict()
    assert payload["disposition"] == "BLOCKED_AT_FROZEN_BOUNDARY"


# ---------------------------------------------------------------------------
# Read paths: evidence on disk, and absent evidence fails closed
# ---------------------------------------------------------------------------


def test_read_phase3c_projection_from_disk(tmp_path):
    path = tmp_path / "phase3c.jsonl"
    path.write_text(
        json.dumps(_phase3c_row()) + "\n", encoding="utf-8"
    )
    projection = read_forward_outcome_projection(
        source=ForwardOutcomeSource.PHASE3C, path=path, generated_at=_NOW
    )
    assert len(projection.records) == 1
    assert projection.trust.is_healthy is True


def test_read_discovery_projection_from_disk(tmp_path):
    path = tmp_path / "discovery.jsonl"
    path.write_text(
        json.dumps(_discovery_row()) + "\n", encoding="utf-8"
    )
    projection = read_forward_outcome_projection(
        source=ForwardOutcomeSource.DISCOVERY, path=path, generated_at=_NOW
    )
    assert len(projection.records) == 1
    assert projection.records[0].identity_kind == "observation_id"


def test_absent_forward_evidence_is_unavailable_not_healthy_empty(tmp_path):
    missing = tmp_path / "absent.jsonl"
    for source in (ForwardOutcomeSource.PHASE3C, ForwardOutcomeSource.DISCOVERY):
        projection = read_forward_outcome_projection(
            source=source, path=missing, generated_at=_NOW
        )
        assert projection.records == ()
        assert projection.trust.is_healthy is False


def test_read_funnel_projection_from_disk(tmp_path):
    path = tmp_path / "funnel.jsonl"
    path.write_text(
        "\n".join(json.dumps(row) for row in _normal_population()) + "\n",
        encoding="utf-8",
    )
    projection = read_qualification_funnel_projection(
        funnel_events_path=path, generated_at=_NOW
    )
    assert projection.conservation.entered == 4
    assert projection.trust.is_healthy is True


def test_absent_funnel_evidence_is_unavailable_not_healthy_empty(tmp_path):
    projection = read_qualification_funnel_projection(
        funnel_events_path=tmp_path / "absent.jsonl", generated_at=_NOW
    )
    assert projection.records == ()
    assert projection.bucket_counts == {}
    assert projection.trust.is_healthy is False


def test_unreadable_forward_record_degrades_trust_not_silently_complete():
    row = _phase3c_row()
    row["reference_price"] = None
    projection = build_forward_outcome_projection(
        [row], source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    assert projection.records[0].availability.value == "UNAVAILABLE"
    assert projection.trust.completeness.value == "INCOMPLETE"
    assert projection.trust.is_healthy is False
    assert "FORWARD_RECORDS_UNAVAILABLE" in projection.trust.reasons


def test_join_with_an_unreadable_source_reports_source_unavailable_not_no_match():
    broken = unavailable_forward_outcomes(
        "ABSENT", source=ForwardOutcomeSource.PHASE3C, generated_at=_NOW
    )
    result = join_funnel_record_to_forward_outcomes(_funnel_record("EP:1"), [broken])
    assert result.status is ForwardOutcomeJoinStatus.SOURCE_UNAVAILABLE


def test_join_with_no_readable_source_reports_source_unavailable():
    broken = unavailable_forward_outcomes(
        "ABSENT", source=ForwardOutcomeSource.DISCOVERY, generated_at=_NOW
    )
    result = join_funnel_record_to_forward_outcomes(_funnel_record("EP:1"), [broken])
    assert result.status is ForwardOutcomeJoinStatus.SOURCE_UNAVAILABLE


def test_join_partial_unreadable_source_is_not_no_match():
    healthy_no_match = build_forward_outcome_projection(
        [_phase3c_row(episode_id="EP:OTHER")],
        source=ForwardOutcomeSource.PHASE3C,
        generated_at=_NOW,
    )
    broken = unavailable_forward_outcomes(
        "ABSENT", source=ForwardOutcomeSource.DISCOVERY, generated_at=_NOW
    )
    result = join_funnel_record_to_forward_outcomes(
        _funnel_record("EP:1"), [healthy_no_match, broken]
    )
    assert result.status is ForwardOutcomeJoinStatus.SOURCE_UNAVAILABLE


def test_join_match_in_a_healthy_source_still_wins():
    healthy_match = build_forward_outcome_projection(
        [_phase3c_row(episode_id="EP:1")],
        source=ForwardOutcomeSource.PHASE3C,
        generated_at=_NOW,
    )
    broken = unavailable_forward_outcomes(
        "ABSENT", source=ForwardOutcomeSource.DISCOVERY, generated_at=_NOW
    )
    result = join_funnel_record_to_forward_outcomes(
        _funnel_record("EP:1"), [healthy_match, broken]
    )
    assert result.status is ForwardOutcomeJoinStatus.MATCHED


def test_distinct_unreadable_rows_do_not_collapse_into_one_replay():
    first = _funnel_row(decision=DecisionOutcome.QUALIFIED.value)
    first["scan_id"] = None
    first["candidate_id"] = None
    second = dict(first)
    projection = build_qualification_funnel_projection(
        [first, second], generated_at=_NOW
    )
    assert len(projection.records) == 2
    assert projection.duplicate_rows_ignored == 0
    assert projection.conservation.unattributed == 2
    assert projection.conservation.holds is False


def test_row_missing_one_identity_component_stays_addressable():
    # scan_id present, candidate_id missing: still an unreadable row, so two
    # distinct rows must not collapse into one "replay".
    first = _funnel_row(
        decision=DecisionOutcome.QUALIFIED.value, scan_id="S1", candidate_id="C1"
    )
    first["candidate_id"] = None
    second = dict(first)
    projection = build_qualification_funnel_projection(
        [first, second], generated_at=_NOW
    )
    assert len(projection.records) == 2
    assert projection.duplicate_rows_ignored == 0
    assert projection.conservation.unattributed == 2
    assert projection.conservation.holds is False


def test_same_identity_replay_still_deduplicates_when_decision_is_missing():
    # Identity is present but the decision is unreadable: the two rows are the
    # same candidate, so they are a genuine replay and must de-duplicate.
    row = _funnel_row(
        decision=DecisionOutcome.QUALIFIED.value, scan_id="S1", candidate_id="C1"
    )
    row["decision"] = None
    projection = build_qualification_funnel_projection(
        [row, dict(row)], generated_at=_NOW
    )
    assert len(projection.records) == 1
    assert projection.duplicate_rows_ignored == 1
    assert projection.conservation.unattributed == 1


def test_disposition_cause_names_match_the_preregistered_cause_classes():
    from app.opip.profit_intelligence import MissedOpportunityCause

    buckets = {member.value for member in QualificationDisposition}
    for cause in MissedOpportunityCause:
        assert cause.value in buckets, f"cause {cause.value} missing a bucket"


def test_join_searches_a_readable_but_incomplete_source():
    # A readable source with an unmatured window is still readable, so a real
    # match must be found rather than suppressed as unavailable.
    incomplete = build_forward_outcome_projection(
        [_phase3c_row(window_complete=False, episode_id="EP:1")],
        source=ForwardOutcomeSource.PHASE3C,
        generated_at=_NOW,
    )
    assert incomplete.trust.is_healthy is False  # INCOMPLETE, not COMPLETE
    result = join_funnel_record_to_forward_outcomes(_funnel_record("EP:1"), [incomplete])
    assert result.status is ForwardOutcomeJoinStatus.MATCHED


def test_join_preserves_ambiguity_across_a_readable_incomplete_source():
    incomplete = build_forward_outcome_projection(
        [
            _phase3c_row(snapshot_id="S1", episode_id="EP:1", window_complete=False),
            _phase3c_row(snapshot_id="S2", episode_id="EP:1", window_complete=False),
        ],
        source=ForwardOutcomeSource.PHASE3C,
        generated_at=_NOW,
    )
    result = join_funnel_record_to_forward_outcomes(_funnel_record("EP:1"), [incomplete])
    assert result.status is ForwardOutcomeJoinStatus.AMBIGUOUS


def test_present_but_unparseable_funnel_file_is_unavailable(tmp_path):
    path = tmp_path / "funnel.jsonl"
    path.write_text("this is not json\nneither is this\n", encoding="utf-8")
    projection = read_qualification_funnel_projection(
        funnel_events_path=path, generated_at=_NOW
    )
    assert projection.records == ()
    assert projection.trust.is_healthy is False


def test_present_but_unparseable_forward_file_is_unavailable(tmp_path):
    path = tmp_path / "fwd.jsonl"
    path.write_text("garbage\n", encoding="utf-8")
    for source in (ForwardOutcomeSource.PHASE3C, ForwardOutcomeSource.DISCOVERY):
        projection = read_forward_outcome_projection(
            source=source, path=path, generated_at=_NOW
        )
        assert projection.records == ()
        assert projection.trust.is_healthy is False


def test_later_unreadable_replay_does_not_erase_a_readable_decision():
    readable = _funnel_row(
        decision=DecisionOutcome.QUALIFIED.value, scan_id="S1", candidate_id="C1"
    )
    unreadable = dict(readable)
    unreadable["decision"] = None
    projection = build_qualification_funnel_projection(
        [readable, unreadable], generated_at=_NOW
    )
    assert len(projection.records) == 1
    assert projection.records[0].availability.value == "KNOWN"
    assert projection.records[0].decision == DecisionOutcome.QUALIFIED.value
    # Reverse order (readable arrives last) also keeps the readable record.
    rev = build_qualification_funnel_projection(
        [unreadable, readable], generated_at=_NOW
    )
    assert rev.records[0].availability.value == "KNOWN"
