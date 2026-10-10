"""R5-A Continuous Opportunity Engine acceptance tests.

This module proves the `ATDD-R5-A-continuous-opportunity-engine` increment: the
F1 coverage/availability evidence, the EYE attention layer, the unified
evaluation coordinator, the F4 phase projection, the HORIZON thesis contract and
the single composition engine that wires them to the existing F2 snapshot and F3
IGNITION detector.

It is deliberately adversarial about the boundaries the adopted clauses set:

* attention is not a claim, a notification or an approval, and cannot move
  without an owner-ratified policy;
* a trigger cannot synthesize a favourable snapshot, bypass warm-up or change a
  detector result;
* one evaluation identity is evaluated once, however many times it is delivered;
* an expired claim never resumes, and a failed tactical thesis is never
  relabelled as a long-term investment;
* unsupported horizons abstain to research-only `UNKNOWN`;
* the engine is feature-disabled (`off`), reads no environment variable and
  carries no order, reservation, sizing or allocation authority.

The tests are deterministic and hermetic: no clock, no network, no database and
no environment read. Every instant is explicit, so a burst, a restart and a
backfill replay identically.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sys

import pytest
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.contracts.detector import DetectorState  # noqa: E402
from app.opip.contracts.enums import (  # noqa: E402
    CoverageState,
    Missingness,
    RestartState,
)
from app.opip.contracts.features import FeatureSnapshot  # noqa: E402
from app.opip.contracts.identity import ConsumedInputWatermark  # noqa: E402
from app.opip.contracts.observation import SourceWatermark  # noqa: E402
from app.opip.contracts.opportunity import (  # noqa: E402
    OpportunityDeferral,
    OpportunityLifecycleState,
    OpportunityTerminalReason,
)
from app.opip.contracts.opportunity_phases import (  # noqa: E402
    DOWNSTREAM_ONLY_PHASES,
    PHASE_AUTHORITY,
    PHASE_MAPPINGS,
    OpportunityPhase,
    PhaseContractError,
    advance_episode,
    episode_from_claim,
    episode_phase,
    project_phase,
    requalify_episode,
)
from app.opip.contracts.temporal import AvailabilityStamp  # noqa: E402
from app.opip.contracts.thesis import (  # noqa: E402
    HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION,
    AbstentionReason,
    ClassificationUncertainty,
    HorizonClass,
    HorizonContractError,
    HorizonEvidence,
    HorizonPolicy,
    HorizonRequirements,
    classify_horizon,
    expire_thesis,
    reclassify,
)
from app.opip.detectors import ignition  # noqa: E402
from app.opip.events.attention import (  # noqa: E402
    ATTENTION_ENGINEERING_SCENARIO_POLICY_VERSION,
    ATTENTION_POLICY_VERSION,
    REASON_POLICY_NOT_RATIFIED,
    AttentionContractError,
    AttentionEvidence,
    AttentionHealthOverlay,
    AttentionPolicy,
    AttentionPopulation,
    AttentionReason,
    AttentionState,
    AttentionStateRecord,
    AttentionTriggerLedger,
    advance_attention,
    attention_trigger_identity,
)
from app.opip.events.coverage import (  # noqa: E402
    CoverageContractError,
    GapReason,
    IntervalEvidence,
    reconcile_coverage,
    require_usable_coverage,
)
from app.opip.events.opportunity_engine import (  # noqa: E402
    R5A_ENGINE_AUTHORITY,
    R5A_ENGINE_MODE,
    R5A_ENV_VARIABLES_READ,
    ContinuousOpportunityEngine,
    OpportunityEngineError,
    engine_mode,
    engine_reads_environment,
)
from app.opip.features.engine import (  # noqa: E402
    FEATURE_CALC_VERSION,
    FEATURE_SCHEMA_VERSION,
    FEATURE_VERSION,
    feature_dag_hash,
)
from app.opip.streaming.coordinator import (  # noqa: E402
    CoordinatorContractError,
    CoordinatorLimits,
    EvaluationPath,
    EvaluationRequest,
    RequestDisposition,
    UnifiedEvaluationCoordinator,
    WorkPriority,
    evaluation_identity,
)
from app.services.release_profiles import (  # noqa: E402
    get_release_profiles,
    validate_profile_contract,
)
from tests.atdd_scope import (  # noqa: E402
    check_scope,
    load_acceptance_index,
    load_scope_contracts,
    parse_scope_contract,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-A-continuous-opportunity-engine"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
CONFORMANCE = (
    APP_ROOT / "docs" / "architecture" / "OPIP_R5_A_CONTINUOUS_OPPORTUNITY_ENGINE.md"
)
RUNTIME_ROOTS = (
    APP_ROOT / "app" / "services",
    APP_ROOT / "app" / "jobs",
    APP_ROOT / "app" / "api",
    APP_ROOT / "app" / "opip" / "decision",
    APP_ROOT / "app" / "opip" / "discovery",
    APP_ROOT / "app" / "opip" / "risk",
)
R5A_MODULES = (
    APP_ROOT / "app" / "opip" / "contracts" / "thesis.py",
    APP_ROOT / "app" / "opip" / "contracts" / "opportunity_phases.py",
    APP_ROOT / "app" / "opip" / "events" / "coverage.py",
    APP_ROOT / "app" / "opip" / "events" / "attention.py",
    APP_ROOT / "app" / "opip" / "streaming" / "coordinator.py",
    APP_ROOT / "app" / "opip" / "events" / "opportunity_engine.py",
)
T0 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
GRID = 60


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def intervals(count: int, *, receipt_offset: int = 1, **overrides) -> list:
    items = []
    for index in range(count):
        payload = {
            "interval_start_epoch": int(at(index).timestamp()),
            "receipt_time": at(index + receipt_offset),
        }
        payload.update(overrides)
        items.append(IntervalEvidence(**payload))
    return items


def coverage(
    minutes: int,
    *,
    evaluated: float | None = None,
    count: int | None = None,
    receipt_offset: int = 1,
    source_sequence_prefix: str | None = None,
    instrument: str = "IV:1",
    **kwargs,
):
    watermark = SourceWatermark(
        instrument_version_id=instrument,
        through_utc=at(minutes),
        last_ingestion_order=minutes,
    )
    delivered = intervals(
        minutes if count is None else count, receipt_offset=receipt_offset
    )
    if source_sequence_prefix is not None:
        delivered = [
            IntervalEvidence(
                interval_start_epoch=item.interval_start_epoch,
                receipt_time=item.receipt_time,
                source_sequence=f"{source_sequence_prefix}-{item.interval_start_epoch}",
            )
            for item in delivered
        ]
    return reconcile_coverage(
        instrument_version_id=instrument,
        venue_instrument_id=f"{instrument}-USD",
        source_label="kraken_ohlc",
        reference_data_version="rdv1",
        window_start_utc=T0,
        interval_seconds=GRID,
        evidence=delivered,
        watermark=watermark,
        evaluated_at_utc=at(minutes if evaluated is None else evaluated),
        **kwargs,
    )


def snapshot(
    minutes: float, *, instrument: str = "IV:1", **overrides
) -> FeatureSnapshot:
    values = {
        "trend_state": "UP",
        "return_5m": 0.5,
        "acceleration_5m_vs_15m": 0.1,
        "volume_expansion_5m_vs_20m": 1.5,
        "compression_release_score": 0.4,
    }
    values.update(overrides.pop("values", {}))
    payload = {
        "instrument_version_id": instrument,
        "venue_instrument_id": f"{instrument}-USD",
        "feature_version": FEATURE_VERSION,
        "evaluation_cutoff": at(minutes),
        "evaluated_at_utc": at(minutes),
        "consumed_input_watermark": ConsumedInputWatermark(
            history_epoch=1, local_sequence=int(minutes)
        ),
        "values": values,
        "availability": AvailabilityStamp(
            source_at_utc=at(minutes),
            ingested_at_utc=at(minutes),
            visible_at_utc=at(minutes),
            source_version="rdv1",
        ),
        "missingness": {name: Missingness.PRESENT for name in values},
        "coverage": CoverageState.COMPLETE,
        "restart_state": RestartState.WARM,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_calc_version": FEATURE_CALC_VERSION,
        "feature_dag_hash": feature_dag_hash(),
    }
    payload.update(overrides)
    return FeatureSnapshot(**payload)


def attention_policy(**overrides) -> AttentionPolicy:
    payload = {
        "policy_version": ATTENTION_ENGINEERING_SCENARIO_POLICY_VERSION,
        "debounce_intervals": 1,
        "cooldown_intervals": 2,
        "trigger_expiry_intervals": 5,
        "oscillation_guard_intervals": 1,
        "work_budget_per_cycle": 4,
    }
    payload.update(overrides)
    return AttentionPolicy(**payload)


def coordinator_limits(**overrides) -> CoordinatorLimits:
    payload = {
        "queue_capacity": 8,
        "queue_age_limit_intervals": 10,
        "retry_ceiling": 2,
        "per_instrument_budget_per_cycle": 2,
        "protection_reserved_slots": 1,
        "reconciliation_reserved_slots": 1,
    }
    payload.update(overrides)
    return CoordinatorLimits(**payload)



def horizon_policy(**overrides) -> HorizonPolicy:
    payload = {
        "policy_version": HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION,
        "holding_bounds_seconds": {
            HorizonClass.TACTICAL: (GRID, 86_400),
            HorizonClass.SWING: (86_401, 1_209_600),
            HorizonClass.POSITION: (1_209_601, 7_776_000),
        },
        "requirements": HorizonRequirements(position_min_structural_milestones=2),
    }
    payload.update(overrides)
    return HorizonPolicy(**payload)


def attention_evidence(
    minute: float,
    *,
    requested: AttentionState | None = None,
    progression: tuple = (AttentionReason.RELATIVE_VOLUME_STRENGTHENING,),
    regression: tuple = (),
    **coverage_kwargs,
) -> AttentionEvidence:
    return AttentionEvidence(
        instrument_version_id="IV:1",
        venue_instrument_id="XBTUSD",
        evaluation_time=at(minute),
        evidence_cutoff=at(minute),
        coverage=coverage(int(minute), **coverage_kwargs),
        source_event_ids=(f"EV-{minute}",),
        progression_reasons=progression,
        regression_reasons=regression,
        requested_state=requested,
    )


def ignition_claim(*, cutoff_minutes: float = 7, prior_seconds: int = 60):
    """Produce a real IGNITION claim through the frozen F3 detector."""

    prior = DetectorState(
        instrument_version_id="IV:1",
        venue_instrument_id="IV:1-USD",
        persistence_seconds=prior_seconds,
    )
    claims, next_state = ignition.evaluate(
        snapshot(cutoff_minutes), prior, at(cutoff_minutes)
    )
    assert claims, "the fixture must produce one IGNITION claim"
    return claims[0], next_state


def thesis_evidence(**overrides) -> HorizonEvidence:
    payload = {
        "episode_id": "OPEP:fixture",
        "claim_id": "IGN:fixture",
        "decision_id": "DEC:1",
        "evidence_cutoff": at(10),
        "classified_at": at(10),
        "supporting_evidence": ("structural:fixture",),
        "detector_support": True,
        "expected_holding_seconds": 3_600,
        "time_invalidation": at(600),
    }
    payload.update(overrides)
    return HorizonEvidence(**payload)


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def module_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def request_for(
    *,
    instrument: str = "IV:1",
    cutoff: float = 10,
    at_minute: float = 10,
    expires: float = 20,
    path: EvaluationPath = EvaluationPath.STREAM_EVENT,
    priority: WorkPriority = WorkPriority.DISCOVERY,
    policy: str = ATTENTION_ENGINEERING_SCENARIO_POLICY_VERSION,
    trigger_id: str | None = None,
) -> EvaluationRequest:
    return EvaluationRequest.create(
        path=path,
        priority=priority,
        instrument_version_id=instrument,
        venue_instrument_id=f"{instrument}-USD",
        evaluation_cutoff=at(cutoff),
        requested_at_utc=at(at_minute),
        expires_at_utc=at(expires),
        policy_version=policy,
        attention_trigger_id=trigger_id,
    )



# ---------------------------------------------------------------------------
# AC-001 F1 coverage, availability and gap evidence
# ---------------------------------------------------------------------------


def test_ac_001_complete_window_retains_availability_evidence():
    """ATDD-R5-A-continuous-opportunity-engine/AC-001: what arrived is retained.

    GIVEN a consumed six-interval window that arrived on time
    WHEN coverage is reconciled
    THEN the record is COMPLETE, names its source and reference version, reports
    the usable interval count and is usable at the evaluation instant
    """

    record = coverage(6)
    assert record.coverage_state is CoverageState.COMPLETE
    assert record.expected_intervals == 6
    assert record.usable_intervals == 6
    assert record.missing_intervals == 0
    assert record.gap_reasons == ()
    assert record.usable_at(cutoff=at(6)) is True
    assert record.latest_receipt_time == at(6)
    body = record.to_dict()
    assert body["source_label"] == "kraken_ohlc"
    assert body["reference_data_version"] == "rdv1"
    assert body["coverage_state"] == "COMPLETE"


def test_ac_001_expectations_derive_from_the_consumed_watermark():
    """ATDD-R5-A-continuous-opportunity-engine/AC-001: no clock, no invented work.

    GIVEN a watermark that has consumed three intervals
    WHEN coverage is reconciled over a longer delivered series
    THEN only the consumed intervals are expected and the remainder is recorded
    outside the window rather than silently accepted
    """

    record = reconcile_coverage(
        instrument_version_id="IV:1",
        venue_instrument_id="XBTUSD",
        source_label="kraken_ohlc",
        reference_data_version="rdv1",
        window_start_utc=T0,
        interval_seconds=GRID,
        evidence=intervals(6),
        watermark=SourceWatermark(
            instrument_version_id="IV:1", through_utc=at(3), last_ingestion_order=3
        ),
        evaluated_at_utc=at(6),
    )
    assert record.expected_intervals == 3
    assert record.usable_intervals == 3
    assert GapReason.OUTSIDE_WINDOW.value in record.gap_reasons
    assert record.usable_at(cutoff=at(6)) is False


def test_ac_001_unconsumed_window_is_not_usable_evidence():
    """ATDD-R5-A-continuous-opportunity-engine/AC-001: gaps reconcile to a watermark.

    GIVEN a source whose watermark has consumed nothing
    WHEN coverage is reconciled
    THEN no interval is expected, the shortfall is recorded as WINDOW_NOT_CONSUMED
    and the evidence cannot back an evaluation
    """

    record = reconcile_coverage(
        instrument_version_id="IV:1",
        venue_instrument_id="XBTUSD",
        source_label="kraken_ohlc",
        reference_data_version="rdv1",
        window_start_utc=T0,
        interval_seconds=GRID,
        evidence=intervals(4),
        watermark=SourceWatermark(instrument_version_id="IV:1"),
        evaluated_at_utc=at(4),
    )
    assert record.expected_intervals == 0
    assert record.coverage_state is CoverageState.INCOMPLETE_COVERAGE
    assert GapReason.WINDOW_NOT_CONSUMED.value in record.gap_reasons
    assert record.usable_at(cutoff=at(4)) is False


def test_ac_001_missing_and_invalid_evidence_is_recorded_not_repaired():
    """ATDD-R5-A-continuous-opportunity-engine/AC-001: unknown stays unknown.

    GIVEN a window with three absent intervals and one invalid row
    WHEN coverage is reconciled
    THEN each is recorded as a blocking gap, no value is invented for them and
    the record is INCOMPLETE
    """

    delivered = [
        IntervalEvidence(
            interval_start_epoch=int(at(0).timestamp()), receipt_time=at(1)
        ),
        IntervalEvidence(
            interval_start_epoch=int(at(1).timestamp()), receipt_time=at(2)
        ),
        IntervalEvidence(
            interval_start_epoch=int(at(2).timestamp()),
            receipt_time=at(3),
            payload_valid=False,
            invalid_reason="non_finite_price",
        ),
    ]
    record = reconcile_coverage(
        instrument_version_id="IV:1",
        venue_instrument_id="XBTUSD",
        source_label="kraken_ohlc",
        reference_data_version="rdv1",
        window_start_utc=T0,
        interval_seconds=GRID,
        evidence=delivered,
        watermark=SourceWatermark(
            instrument_version_id="IV:1", through_utc=at(6), last_ingestion_order=6
        ),
        evaluated_at_utc=at(6),
    )
    assert record.expected_intervals == 6
    assert record.usable_intervals == 2
    # Three absent intervals plus one invalid row are all unusable evidence.
    assert record.missing_intervals == 4
    assert GapReason.INVALID_ROW.value in record.gap_reasons
    assert GapReason.MISSING_INTERVAL.value in record.gap_reasons
    assert record.coverage_state is CoverageState.INCOMPLETE_COVERAGE



@pytest.mark.parametrize(
    "reason, kwargs",
    (
        (GapReason.FORMING_INTERVAL, {"interval_forming": True}),
        (
            GapReason.INVALID_ROW,
            {"payload_valid": False, "invalid_reason": "non_positive_price"},
        ),
    ),
)
def test_ac_001_per_row_gap_reasons_are_catalogued(reason, kwargs):
    """ATDD-R5-A-continuous-opportunity-engine/AC-001: each unusable row says why.

    GIVEN one delivered interval with a specific defect
    WHEN coverage is reconciled
    THEN that defect's reason is recorded and the interval is not usable
    """

    delivered = intervals(4)
    delivered[-1] = IntervalEvidence(
        interval_start_epoch=int(at(3).timestamp()), receipt_time=at(4), **kwargs
    )
    record = reconcile_coverage(
        instrument_version_id="IV:1",
        venue_instrument_id="XBTUSD",
        source_label="kraken_ohlc",
        reference_data_version="rdv1",
        window_start_utc=T0,
        interval_seconds=GRID,
        evidence=delivered,
        watermark=SourceWatermark(
            instrument_version_id="IV:1", through_utc=at(4), last_ingestion_order=4
        ),
        evaluated_at_utc=at(4),
    )
    assert reason.value in record.gap_reasons
    assert record.usable_intervals == 3


def test_ac_001_late_and_clock_skewed_arrivals_are_recorded():
    """ATDD-R5-A-continuous-opportunity-engine/AC-001: arrival time is honesty.

    GIVEN evidence that arrives after the evaluation instant and evidence whose
    receipt precedes the interval it carries
    WHEN coverage is reconciled
    THEN LATE_ARRIVAL and CLOCK_SKEW are recorded and neither is usable
    """

    late = coverage(4, receipt_offset=6, evaluated=2)
    assert GapReason.LATE_ARRIVAL.value in late.gap_reasons
    assert late.usable_at(cutoff=at(2)) is False
    skewed = coverage(4, receipt_offset=-2)
    assert GapReason.CLOCK_SKEW.value in skewed.gap_reasons
    assert skewed.usable_at(cutoff=at(4)) is False


def test_ac_001_a_delayed_event_recovers_only_at_a_later_reconciled_cutoff():
    """ATDD-R5-A-continuous-opportunity-engine/AC-001: no retrospective leakage.

    GIVEN intervals delivered after an early evaluation instant
    WHEN the same window is reconciled again at a later instant
    THEN the early record stays unusable while the later record is COMPLETE
    """

    early = coverage(4, receipt_offset=8, evaluated=3)
    assert early.usable_at(cutoff=at(3)) is False
    assert GapReason.LATE_ARRIVAL.value in early.gap_reasons
    later = coverage(4, receipt_offset=8, evaluated=20)
    assert later.coverage_state is CoverageState.COMPLETE
    assert later.usable_at(cutoff=at(20)) is True


def test_ac_001_duplicate_and_out_of_order_arrivals_are_non_blocking():
    """ATDD-R5-A-continuous-opportunity-engine/AC-001: present evidence still counts.

    GIVEN a duplicated interval and a regressing source sequence
    WHEN coverage is reconciled
    THEN both are recorded and the window stays usable
    """

    delivered = intervals(4)
    delivered.append(
        IntervalEvidence(
            interval_start_epoch=int(at(3).timestamp()), receipt_time=at(5)
        )
    )
    record = reconcile_coverage(
        instrument_version_id="IV:1",
        venue_instrument_id="XBTUSD",
        source_label="kraken_ohlc",
        reference_data_version="rdv1",
        window_start_utc=T0,
        interval_seconds=GRID,
        evidence=delivered,
        watermark=SourceWatermark(
            instrument_version_id="IV:1", through_utc=at(4), last_ingestion_order=4
        ),
        evaluated_at_utc=at(5),
    )
    assert GapReason.DUPLICATE_INTERVAL.value in record.gap_reasons
    assert record.coverage_state is CoverageState.COMPLETE

    sequenced = [
        IntervalEvidence(
            interval_start_epoch=int(at(0).timestamp()),
            receipt_time=at(1),
            source_sequence="9",
        ),
        IntervalEvidence(
            interval_start_epoch=int(at(1).timestamp()),
            receipt_time=at(2),
            source_sequence="1",
        ),
    ]
    regressed = reconcile_coverage(
        instrument_version_id="IV:1",
        venue_instrument_id="XBTUSD",
        source_label="kraken_ohlc",
        reference_data_version="rdv1",
        window_start_utc=T0,
        interval_seconds=GRID,
        evidence=sequenced,
        watermark=SourceWatermark(
            instrument_version_id="IV:1", through_utc=at(2), last_ingestion_order=2
        ),
        evaluated_at_utc=at(2),
    )
    assert GapReason.OUT_OF_ORDER_ARRIVAL.value in regressed.gap_reasons
    assert regressed.coverage_state is CoverageState.COMPLETE


@pytest.mark.parametrize(
    "kwargs, reason",
    (
        ({"source_available": False}, GapReason.SOURCE_UNAVAILABLE),
        ({"instrument_supported": False}, GapReason.UNSUPPORTED_INSTRUMENT),
        ({"metadata_changed": True}, GapReason.METADATA_CHANGE),
    ),
)
def test_ac_001_source_and_instrument_failures_block_the_whole_window(kwargs, reason):
    """ATDD-R5-A-continuous-opportunity-engine/AC-001: outages and delistings.

    GIVEN a source outage, an unsupported instrument or a metadata change
    WHEN coverage is reconciled
    THEN the whole window is blocked with that reason and no interval is usable
    """

    record = coverage(4, **kwargs)
    assert reason.value in record.gap_reasons
    assert record.usable_intervals == 0
    assert record.usable_at(cutoff=at(4)) is False
    with pytest.raises(CoverageContractError):
        require_usable_coverage(record, cutoff=at(4))



# ---------------------------------------------------------------------------
# AC-002 EYE states, overlays and separation from detector and lifecycle
# ---------------------------------------------------------------------------


def test_ac_002_attention_states_and_overlays_are_closed_declared_sets():
    """ATDD-R5-A-continuous-opportunity-engine/AC-002: no free-text states.

    GIVEN the declared attention vocabulary
    WHEN the state and overlay value sets are compared
    THEN the states are exactly the five adopted states, the overlays are exactly
    the three declared overlays, the two sets are disjoint and a bounded retreat
    cannot be requested as evidence
    """

    states = {state.value for state in AttentionState}
    overlays = {overlay.value for overlay in AttentionHealthOverlay}
    assert states == {"DORMANT", "WATCH", "DEVELOPING", "FOCUSED", "COOLDOWN"}
    assert overlays == {"HEALTHY", "STALE", "RECOVERING"}
    assert states & overlays == set()
    with pytest.raises(AttentionContractError):
        AttentionEvidence(
            instrument_version_id="IV:1",
            venue_instrument_id="XBTUSD",
            evaluation_time=at(6),
            evidence_cutoff=at(6),
            coverage=coverage(6),
            source_event_ids=("EV",),
            requested_state=AttentionState.COOLDOWN,
        )


def test_ac_002_attention_is_not_a_detector_phase_or_a_lifecycle_state():
    """ATDD-R5-A-continuous-opportunity-engine/AC-002: separate concepts.

    GIVEN the attention, detector, lifecycle and horizon vocabularies
    WHEN their types and value sets are compared
    THEN attention is a distinct five-member vocabulary that shares no token with
    an F4 lifecycle state or a horizon class, and the one word it shares with the
    detector phase (`DORMANT`) is a name coincidence rather than a shared concept
    """

    attention = {state.value for state in AttentionState}
    lifecycle = {state.value for state in OpportunityLifecycleState}
    horizons = {horizon.value for horizon in HorizonClass}
    assert AttentionState is not ignition.DetectorPhase
    assert len(AttentionState) == 5
    assert len(ignition.DetectorPhase) == 2
    assert attention & lifecycle == set()
    assert attention & horizons == set()
    assert AttentionState.DORMANT.value == ignition.DetectorPhase.DORMANT.value
    assert not isinstance(AttentionState.DORMANT, ignition.DetectorPhase)
    trigger_body = advance_attention(
        AttentionStateRecord.initial("IV:1"),
        attention_evidence(6, requested=AttentionState.DEVELOPING),
        attention_policy(),
    ).trigger.to_dict()
    assert "claim_id" not in trigger_body
    assert "episode_id" not in trigger_body


def test_ac_002_health_overlays_are_overlays_and_recovery_is_explicit():
    """ATDD-R5-A-continuous-opportunity-engine/AC-002: STALE/RECOVERING overlays.

    GIVEN a usable window that advances attention, then an unusable window and
    then a recovered window
    WHEN attention is advanced
    THEN the state never moves during the outage, the overlay becomes STALE and
    then RECOVERING, and the recovery is recorded in its own population
    """

    policy = attention_policy()
    state = AttentionStateRecord.initial("IV:1")
    fresh = advance_attention(
        state, attention_evidence(6, requested=AttentionState.DEVELOPING), policy
    )
    assert fresh.current.state is AttentionState.WATCH
    assert fresh.current.health_overlay is AttentionHealthOverlay.HEALTHY
    assert fresh.disposition is AttentionPopulation.ADMITTED

    stale_evidence = AttentionEvidence(
        instrument_version_id="IV:1",
        venue_instrument_id="XBTUSD",
        evaluation_time=at(7),
        evidence_cutoff=at(7),
        coverage=coverage(4, receipt_offset=6, evaluated=3),
        source_event_ids=("EV-7",),
        requested_state=AttentionState.FOCUSED,
    )
    stalled = advance_attention(fresh.current, stale_evidence, policy)
    assert stalled.current.state is AttentionState.WATCH
    assert stalled.current.health_overlay is AttentionHealthOverlay.STALE
    assert stalled.disposition is AttentionPopulation.STALE
    assert "coverage-unusable" in stalled.trigger.reason_codes

    recovered = advance_attention(stalled.current, attention_evidence(8), policy)
    assert recovered.current.health_overlay is AttentionHealthOverlay.RECOVERING
    assert recovered.disposition is AttentionPopulation.RECOVERED
    healthy = advance_attention(recovered.current, attention_evidence(9), policy)
    assert healthy.current.health_overlay is AttentionHealthOverlay.HEALTHY


def test_ac_002_no_policy_means_inert_attention():
    """ATDD-R5-A-continuous-opportunity-engine/AC-002: unratified is inert.

    GIVEN an attention policy whose owner-gated bounds are not configured
    WHEN a strong progression is observed
    THEN no state moves, the trigger records BUDGET_SKIPPED with the
    policy-not-ratified reason and the policy reports its owner input as open
    """

    policy = AttentionPolicy()
    assert policy.configured is False
    assert policy.ratified is False
    assert policy.owner_input_status == "OWNER_DECISION_REQUIRED"
    transition = advance_attention(
        AttentionStateRecord.initial("IV:1"),
        attention_evidence(6, requested=AttentionState.FOCUSED),
        policy,
    )
    assert transition.current.state is AttentionState.DORMANT
    assert transition.disposition is AttentionPopulation.BUDGET_SKIPPED
    assert transition.trigger.reason_codes == ("policy-not-ratified",)
    assert transition.trigger.expires_at_utc is None



# ---------------------------------------------------------------------------
# AC-003 trigger identity, content and forgery resistance
# ---------------------------------------------------------------------------


def test_ac_003_trigger_identity_is_deterministic_and_evidence_bound():
    """ATDD-R5-A-continuous-opportunity-engine/AC-003: one identity per transition.

    GIVEN the same transition derived twice and two altered transitions
    WHEN identity is derived
    THEN the repeats match and each alteration of the cutoff or the source events
    yields a different identity
    """

    def derive(*, cutoff: datetime, events: tuple) -> str:
        return attention_trigger_identity(
            instrument_version_id="IV:1",
            prior_state=AttentionState.DORMANT,
            new_state=AttentionState.WATCH,
            policy_version=ATTENTION_ENGINEERING_SCENARIO_POLICY_VERSION,
            evidence_cutoff=cutoff,
            source_event_ids=events,
        )

    first = derive(cutoff=at(6), events=("EV-6",))
    repeat = derive(cutoff=at(6), events=("EV-6",))
    assert first == repeat
    assert first.startswith("ATRG:")
    assert len(
        {first, derive(cutoff=at(7), events=("EV-6",)), derive(cutoff=at(6), events=("EV-7",))}
    ) == 3


def test_ac_003_trigger_record_carries_every_frozen_key():
    """ATDD-R5-A-continuous-opportunity-engine/AC-003: SC-02 record keys.

    GIVEN an admitted attention transition
    WHEN its trigger record is serialized
    THEN every key the frozen contract names is present, the record carries no
    authority and it expires after it was created
    """

    transition = advance_attention(
        AttentionStateRecord.initial("IV:1"),
        attention_evidence(6, requested=AttentionState.DEVELOPING),
        attention_policy(),
    )
    body = transition.trigger.to_dict()
    for key in (
        "trigger_id",
        "instrument_version_id",
        "venue_instrument_id",
        "prior_state",
        "new_state",
        "policy_version",
        "source_event_ids",
        "event_time_utc",
        "receipt_time_utc",
        "availability_time_utc",
        "request_time_utc",
        "evidence_cutoff",
        "watermark_through_utc",
        "watermark_local_sequence",
        "expires_at_utc",
        "disposition",
        "suppression_detail",
        "reason_codes",
    ):
        assert key in body, key
    assert body["authority"] == "NONE"
    assert transition.trigger.policy_ratified is False
    assert transition.trigger.expires_at_utc > transition.trigger.request_time_utc


def test_ac_003_a_forged_trigger_identity_or_relationship_is_refused():
    """ATDD-R5-A-continuous-opportunity-engine/AC-003: records are evidence-bound.

    GIVEN a valid trigger
    WHEN its identity is rewritten, or a no-progress record claims ADMITTED
    THEN both are refused rather than accepted as valid-looking evidence
    """

    transition = advance_attention(
        AttentionStateRecord.initial("IV:1"),
        attention_evidence(7, requested=AttentionState.DEVELOPING),
        attention_policy(),
    )
    trigger = transition.trigger
    with pytest.raises(AttentionContractError):
        replace(trigger, trigger_id="ATRG:forged")
    with pytest.raises(AttentionContractError):
        replace(
            trigger,
            new_state=trigger.prior_state,
            disposition=AttentionPopulation.ADMITTED,
        )


def test_ac_003_evidence_rejects_unknown_reason_and_a_mismatched_instrument():
    """ATDD-R5-A-continuous-opportunity-engine/AC-003: typed reason vocabulary.

    GIVEN a reason token outside the declared vocabulary and evidence that names
    another instrument
    WHEN the evidence is constructed
    THEN both are refused
    """

    with pytest.raises(AttentionContractError):
        AttentionEvidence(
            instrument_version_id="IV:1",
            venue_instrument_id="XBTUSD",
            evaluation_time=at(6),
            evidence_cutoff=at(6),
            coverage=coverage(6),
            source_event_ids=("EV",),
            progression_reasons=("MOMENTUM_SPIKE",),
        )
    with pytest.raises(AttentionContractError):
        AttentionEvidence(
            instrument_version_id="IV:2",
            venue_instrument_id="XBTUSD",
            evaluation_time=at(6),
            evidence_cutoff=at(6),
            coverage=coverage(6),
            source_event_ids=("EV",),
        )



# ---------------------------------------------------------------------------
# AC-004 EYE bounded work, hysteresis and reconciled populations
# ---------------------------------------------------------------------------


def evidence_for(
    instrument: str,
    minute: float,
    *,
    requested: AttentionState | None = AttentionState.DEVELOPING,
    regression: tuple = (),
) -> AttentionEvidence:
    return AttentionEvidence(
        instrument_version_id=instrument,
        venue_instrument_id=f"{instrument}-USD",
        evaluation_time=at(minute),
        evidence_cutoff=at(minute),
        coverage=coverage(int(minute), instrument=instrument),
        source_event_ids=(f"EV-{instrument}-{minute}",),
        progression_reasons=(AttentionReason.RELATIVE_VOLUME_STRENGTHENING,),
        regression_reasons=regression,
        requested_state=requested,
    )


def admitted_transition(instrument: str = "IV:1", *, minute: float = 6, policy=None):
    active = policy or attention_policy()
    return advance_attention(
        AttentionStateRecord.initial(instrument),
        evidence_for(instrument, minute, requested=AttentionState.DEVELOPING),
        active,
    )


def test_ac_004_debounce_holds_then_steps_exactly_one_rank():
    """ATDD-R5-A-continuous-opportunity-engine/AC-004: bounded, one-step movement.

    GIVEN a policy that requires two qualifying evaluations before a step
    WHEN the same strong evidence repeats
    THEN the first evaluation is suppressed, the second advances exactly one rank
    and the state never jumps two ranks in one evaluation
    """

    policy = attention_policy(debounce_intervals=2)
    first = advance_attention(
        AttentionStateRecord.initial("IV:1"),
        attention_evidence(6, requested=AttentionState.FOCUSED),
        policy,
    )
    assert first.current.state is AttentionState.DORMANT
    assert first.disposition is AttentionPopulation.SUPPRESSED
    assert first.current.qualifying_intervals == 1
    second = advance_attention(
        first.current,
        attention_evidence(7, requested=AttentionState.FOCUSED),
        policy,
    )
    assert second.current.state is AttentionState.WATCH
    assert second.progressed is True
    assert second.disposition is AttentionPopulation.ADMITTED


def test_ac_004_oscillation_guard_holds_a_progression_inside_minimum_dwell():
    """ATDD-R5-A-continuous-opportunity-engine/AC-004: hysteresis against flapping.

    GIVEN a state that just transitioned
    WHEN a further progression is requested inside the owner-gated guard window
    THEN the state is held and the trigger records the oscillation guard
    """

    policy = attention_policy(debounce_intervals=0, oscillation_guard_intervals=3)
    first = advance_attention(
        AttentionStateRecord.initial("IV:1"),
        attention_evidence(6, requested=AttentionState.DEVELOPING),
        policy,
    )
    assert first.current.state is AttentionState.WATCH
    second = advance_attention(
        first.current,
        attention_evidence(7, requested=AttentionState.DEVELOPING),
        policy,
    )
    assert second.current.state is AttentionState.WATCH
    assert second.disposition is AttentionPopulation.SUPPRESSED
    assert "oscillation-guard-hold" in second.trigger.reason_codes


def test_ac_004_regression_enters_a_bounded_cooldown_and_never_restarts_focus():
    """ATDD-R5-A-continuous-opportunity-engine/AC-004: bounded cooldown.

    GIVEN attention that reached FOCUSED and then lost its setup conditions
    WHEN it is evaluated during and after the cooldown
    THEN it retreats to COOLDOWN, holds while the cooldown lasts, and only then
    re-establishes attention at WATCH rather than returning straight to FOCUSED
    """

    policy = attention_policy(debounce_intervals=1, cooldown_intervals=2)
    state = AttentionStateRecord.initial("IV:1")
    for minute, requested in (
        (6, AttentionState.DEVELOPING),
        (7, AttentionState.DEVELOPING),
        (8, AttentionState.FOCUSED),
    ):
        transition = advance_attention(
            state, attention_evidence(minute, requested=requested), policy
        )
        state = transition.current
    assert state.state is AttentionState.FOCUSED

    regressed = advance_attention(
        state,
        attention_evidence(
            9,
            requested=AttentionState.WATCH,
            regression=(AttentionReason.SETUP_CONDITION_LOST,),
        ),
        policy,
    )
    assert regressed.current.state is AttentionState.COOLDOWN
    assert regressed.regressed is True

    held = advance_attention(
        regressed.current,
        attention_evidence(10, requested=AttentionState.FOCUSED),
        policy,
    )
    assert held.current.state is AttentionState.COOLDOWN
    assert held.disposition is AttentionPopulation.SUPPRESSED

    released = advance_attention(
        held.current,
        attention_evidence(12, requested=AttentionState.FOCUSED),
        policy,
    )
    assert released.current.state is AttentionState.WATCH
    assert released.disposition is AttentionPopulation.ADMITTED



def test_ac_004_duplicate_deliveries_coalesce_to_one_evaluation():
    """ATDD-R5-A-continuous-opportunity-engine/AC-004: duplicate convergence.

    GIVEN the same trigger delivered twice for one instrument, policy and cutoff
    WHEN both are submitted
    THEN the first is admitted, the second coalesces and only one request is
    outstanding
    """

    ledger = AttentionTriggerLedger(policy=attention_policy())
    transition = admitted_transition(minute=7)
    assert ledger.submit(transition.trigger, evaluation_time=at(7)) is (
        AttentionPopulation.ADMITTED
    )
    assert ledger.submit(transition.trigger, evaluation_time=at(7)) is (
        AttentionPopulation.COALESCED
    )
    assert len(ledger.outstanding()) == 1


def test_ac_004_cycle_budget_bounds_work_without_dropping_requests():
    """ATDD-R5-A-continuous-opportunity-engine/AC-004: bounded work, no silent loss.

    GIVEN three admitted requests and a cycle budget of one
    WHEN a cycle selects its work
    THEN one request is served, the others are recorded as cycle-skipped and all
    of them remain accounted for and outstanding
    """

    policy = attention_policy(work_budget_per_cycle=1)
    ledger = AttentionTriggerLedger(policy=policy)
    for instrument in ("IV:1", "IV:2", "IV:3"):
        transition = admitted_transition(instrument, minute=6, policy=policy)
        ledger.submit(transition.trigger, evaluation_time=at(6))
    selected = ledger.due(evaluation_time=at(6))
    assert len(selected) == 1
    populations = ledger.populations()
    assert populations.cycle_budget_skipped == 2
    assert populations.outstanding == 3
    assert populations.reconciles is True


def test_ac_004_fairness_serves_every_instrument_before_repeating_one():
    """ATDD-R5-A-continuous-opportunity-engine/AC-004: no per-instrument starvation.

    GIVEN several instruments each holding more than one request
    WHEN a bounded cycle selects work
    THEN the first round covers distinct instruments before any instrument is
    served twice
    """

    policy = attention_policy(debounce_intervals=0, work_budget_per_cycle=4)
    ledger = AttentionTriggerLedger(policy=policy)
    for instrument in ("IV:1", "IV:2"):
        for minute in (6, 7):
            state = AttentionStateRecord.initial(instrument)
            for step in (6, minute):
                transition = advance_attention(
                    state,
                    evidence_for(
                        instrument, step, requested=AttentionState.DEVELOPING
                    ),
                    policy,
                )
                state = transition.current
            if transition.trigger.disposition is AttentionPopulation.ADMITTED:
                ledger.submit(transition.trigger, evaluation_time=at(minute))
    selected = ledger.due(evaluation_time=at(7))
    instruments = [item.instrument_version_id for item in selected]
    assert len(set(instruments[:2])) == 2


def test_ac_004_expired_requests_leave_the_queue_and_are_counted():
    """ATDD-R5-A-continuous-opportunity-engine/AC-004: explicit expiry, no leak.

    GIVEN an admitted request whose expiry passes
    WHEN the next cycle selects work
    THEN nothing is served, the request is counted as EXPIRED and no request
    remains outstanding
    """

    ledger = AttentionTriggerLedger(policy=attention_policy())
    transition = admitted_transition(minute=6)
    assert ledger.submit(transition.trigger, evaluation_time=at(6)) is (
        AttentionPopulation.ADMITTED
    )
    assert ledger.due(evaluation_time=at(12)) == ()
    populations = ledger.populations()
    assert populations.expired == 1
    assert populations.outstanding == 0
    assert populations.reconciles is True


def test_ac_004_populations_reconcile_under_a_mixed_workload():
    """ATDD-R5-A-continuous-opportunity-engine/AC-004: counts reconcile exactly.

    GIVEN an admitted request that is evaluated, a duplicate delivery, a
    suppressed evaluation, a stale evaluation, a failed request and a recovery
    WHEN the populations are read
    THEN every request is in exactly one terminal population or outstanding and
    the totals reconcile
    """

    policy = attention_policy()
    ledger = AttentionTriggerLedger(policy=policy)
    admitted = admitted_transition(minute=6)
    ledger.submit(admitted.trigger, evaluation_time=at(6))
    ledger.submit(admitted.trigger, evaluation_time=at(6))
    assert ledger.acknowledge(admitted.trigger.trigger_id) is True
    assert ledger.acknowledge(admitted.trigger.trigger_id) is False

    suppressed = advance_attention(
        AttentionStateRecord.initial("IV:2"),
        evidence_for("IV:2", 6, requested=None),
        policy,
    )
    ledger.submit(suppressed.trigger, evaluation_time=at(6))

    stale = advance_attention(
        AttentionStateRecord.initial("IV:3"),
        AttentionEvidence(
            instrument_version_id="IV:3",
            venue_instrument_id="IV:3-USD",
            evaluation_time=at(3),
            evidence_cutoff=at(3),
            coverage=coverage(2, receipt_offset=6, evaluated=1, instrument="IV:3"),
            source_event_ids=("EV-IV3",),
            requested_state=AttentionState.FOCUSED,
        ),
        policy,
    )
    ledger.submit(stale.trigger, evaluation_time=at(3))

    failed = admitted_transition("IV:4", minute=6)
    ledger.submit(failed.trigger, evaluation_time=at(6))
    ledger.fail(failed.trigger.trigger_id, reason="snapshot_unavailable")
    ledger.record_recovery(instrument_version_id="IV:5")

    populations = ledger.populations()
    assert populations.evaluated == 1
    assert populations.coalesced == 1
    assert populations.suppressed == 1
    assert populations.stale == 1
    assert populations.failed == 1
    assert populations.recovered == 1
    assert populations.outstanding == 0
    assert populations.reconciles is True
    telemetry = ledger.telemetry().to_dict()
    assert telemetry["failure_reasons"] == {"snapshot_unavailable": 1}




# ---------------------------------------------------------------------------
# AC-005 SCHED identity convergence between the stream and the scheduler
# ---------------------------------------------------------------------------


def test_ac_005_stream_and_scheduler_requests_share_one_identity():
    """ATDD-R5-A-continuous-opportunity-engine/AC-005: one semantic identity.

    GIVEN a stream-triggered request and a scheduler reconciliation request for
    the same instrument, cutoff and policy
    WHEN both are created and submitted
    THEN their evaluation identities match, the second coalesces and only one
    evaluation identity exists in the queue
    """

    stream = request_for(
        path=EvaluationPath.STREAM_EVENT,
        priority=WorkPriority.DISCOVERY,
        at_minute=10,
    )
    scheduler = request_for(
        path=EvaluationPath.SCHEDULER_RECONCILIATION,
        priority=WorkPriority.DISCOVERY,
        at_minute=12,
    )
    assert stream.evaluation_identity == scheduler.evaluation_identity
    assert stream.request_id != scheduler.request_id

    coordinator = UnifiedEvaluationCoordinator(limits=coordinator_limits())
    assert coordinator.submit(stream, evaluation_time=at(10)).disposition is (
        RequestDisposition.ADMITTED
    )
    coalesced = coordinator.submit(scheduler, evaluation_time=at(12))
    assert coalesced.disposition is RequestDisposition.COALESCED
    assert coalesced.evaluation_identity == stream.evaluation_identity
    assert coordinator.backlog() == 1


def test_ac_005_a_different_cutoff_or_policy_is_a_different_evaluation():
    """ATDD-R5-A-continuous-opportunity-engine/AC-005: identity is not a wildcard.

    GIVEN requests that differ only by cutoff, by policy version or by instrument
    WHEN their identities are derived
    THEN each is distinct, so unrelated evaluations cannot coalesce
    """

    identities = {
        evaluation_identity(
            instrument_version_id="IV:1",
            evaluation_cutoff=at(10),
            policy_version=ATTENTION_ENGINEERING_SCENARIO_POLICY_VERSION,
        ),
        evaluation_identity(
            instrument_version_id="IV:1",
            evaluation_cutoff=at(11),
            policy_version=ATTENTION_ENGINEERING_SCENARIO_POLICY_VERSION,
        ),
        evaluation_identity(
            instrument_version_id="IV:1",
            evaluation_cutoff=at(10),
            policy_version=ATTENTION_POLICY_VERSION,
        ),
        evaluation_identity(
            instrument_version_id="IV:2",
            evaluation_cutoff=at(10),
            policy_version=ATTENTION_ENGINEERING_SCENARIO_POLICY_VERSION,
        ),
    }
    assert len(identities) == 4


def test_ac_005_a_duplicate_with_higher_priority_raises_the_queued_priority():
    """ATDD-R5-A-continuous-opportunity-engine/AC-005: convergence keeps the risk.

    GIVEN a queued discovery request
    WHEN the same identity arrives as a reconciliation request
    THEN the request is not duplicated and the queued work is upgraded to the
    more protective class
    """

    coordinator = UnifiedEvaluationCoordinator(limits=coordinator_limits())
    discovery = request_for(
        path=EvaluationPath.STREAM_EVENT,
        priority=WorkPriority.DISCOVERY,
        at_minute=10,
    )
    coordinator.submit(discovery, evaluation_time=at(10))
    recovery = request_for(
        path=EvaluationPath.RECOVERY_BACKFILL,
        priority=WorkPriority.RECONCILIATION,
        at_minute=11,
    )
    outcome = coordinator.submit(recovery, evaluation_time=at(11))
    assert outcome.disposition is RequestDisposition.COALESCED
    assert coordinator.backlog() == 1
    batch = coordinator.next_batch(evaluation_time=at(11))
    assert [item.priority for item in batch] == [WorkPriority.RECONCILIATION]


# ---------------------------------------------------------------------------
# AC-006 SCHED fairness, reserved capacity and bounded retries
# ---------------------------------------------------------------------------


def test_ac_006_protection_is_never_starved_by_discovery():
    """ATDD-R5-A-continuous-opportunity-engine/AC-006: protection outranks profit.

    GIVEN a saturated queue of discovery work
    WHEN a protection request arrives
    THEN it is admitted by shedding discovery visibly and it is served first
    """

    limits = coordinator_limits(queue_capacity=2, per_instrument_budget_per_cycle=2)
    coordinator = UnifiedEvaluationCoordinator(limits=limits)
    for instrument in ("IV:1", "IV:2"):
        outcome = coordinator.submit(
            request_for(instrument=instrument, at_minute=10),
            evaluation_time=at(10),
        )
        assert outcome.disposition is RequestDisposition.ADMITTED
    protection = request_for(
        instrument="IV:9",
        priority=WorkPriority.PROTECTION,
        path=EvaluationPath.RECOVERY_BACKFILL,
        at_minute=10,
    )
    assert coordinator.submit(protection, evaluation_time=at(10)).disposition is (
        RequestDisposition.ADMITTED
    )
    telemetry = coordinator.telemetry()
    assert telemetry.dropped_backpressure == 1
    assert telemetry.preempted == 1
    batch = coordinator.next_batch(evaluation_time=at(10), size=1)
    assert [item.priority for item in batch] == [WorkPriority.PROTECTION]


def test_ac_006_optional_ai_work_is_dropped_first():
    """ATDD-R5-A-continuous-opportunity-engine/AC-006: AI is last priority.

    GIVEN a coordinator whose limits do not admit optional AI work
    WHEN optional AI work is submitted
    THEN it is dropped with a recorded reason and never occupies capacity
    """

    coordinator = UnifiedEvaluationCoordinator(limits=coordinator_limits())
    outcome = coordinator.submit(
        request_for(priority=WorkPriority.OPTIONAL_AI, at_minute=10),
        evaluation_time=at(10),
    )
    assert outcome.disposition is RequestDisposition.DROPPED_BACKPRESSURE
    assert "optional" in outcome.reason
    assert coordinator.backlog() == 0
    assert coordinator.telemetry().dropped_backpressure == 1


def test_ac_006_saturation_sheds_discovery_and_is_reported():
    """ATDD-R5-A-continuous-opportunity-engine/AC-006: visible degradation.

    GIVEN a full queue containing only discovery work
    WHEN further discovery is submitted
    THEN it is shed rather than evicting queued work, and the shed count and the
    backlog high-water mark are reported in telemetry
    """

    limits = coordinator_limits(queue_capacity=3)
    coordinator = UnifiedEvaluationCoordinator(limits=limits)
    for instrument in ("IV:1", "IV:2", "IV:3"):
        assert coordinator.submit(
            request_for(instrument=instrument, at_minute=10),
            evaluation_time=at(10),
        ).disposition is RequestDisposition.ADMITTED
    outcome = coordinator.submit(
        request_for(instrument="IV:4", at_minute=10), evaluation_time=at(10)
    )
    assert outcome.disposition is RequestDisposition.DROPPED_BACKPRESSURE
    telemetry = coordinator.telemetry()
    assert telemetry.max_backlog == 3
    assert telemetry.dropped_backpressure == 1
    assert telemetry.preempted == 0



def test_ac_006_per_instrument_budget_bounds_a_hot_instrument():
    """ATDD-R5-A-continuous-opportunity-engine/AC-006: no hot-symbol monopoly.

    GIVEN five queued requests for one instrument and a per-cycle budget of two
    WHEN a full-size batch is selected
    THEN exactly two of that instrument are served in the cycle
    """

    limits = coordinator_limits(per_instrument_budget_per_cycle=2)
    coordinator = UnifiedEvaluationCoordinator(limits=limits)
    for minute in range(10, 15):
        request = request_for(
            instrument="IV:1", cutoff=minute, at_minute=minute, expires=minute + 30
        )
        coordinator.submit(request, evaluation_time=at(minute))
    batch = coordinator.next_batch(evaluation_time=at(14), size=5)
    assert len(batch) == 2
    assert all(item.instrument_version_id == "IV:1" for item in batch)


def test_ac_006_stale_requests_expire_and_retries_are_bounded():
    """ATDD-R5-A-continuous-opportunity-engine/AC-006: bounded age and retries.

    GIVEN a request older than the age limit and a request that keeps failing
    WHEN the coordinator is driven forward
    THEN the stale request expires, the failing request reaches terminal failure
    after the retry ceiling, and one identity cannot be served twice
    """

    limits = coordinator_limits(
        queue_age_limit_intervals=2, retry_ceiling=2, queue_capacity=4
    )
    coordinator = UnifiedEvaluationCoordinator(limits=limits)
    stale = request_for(instrument="IV:1", at_minute=10, expires=30)
    assert coordinator.submit(stale, evaluation_time=at(10)).disposition is (
        RequestDisposition.ADMITTED
    )
    assert coordinator.next_batch(evaluation_time=at(20)) == ()
    assert coordinator.telemetry().expired == 1

    failing = request_for(instrument="IV:2", at_minute=10, expires=60)
    coordinator.submit(failing, evaluation_time=at(10))
    assert (
        coordinator.fail(
            failing.evaluation_identity,
            reason="snapshot_unavailable",
            evaluation_time=at(10),
        )
        is RequestDisposition.RETRY_SCHEDULED
    )
    assert (
        coordinator.fail(
            failing.evaluation_identity,
            reason="snapshot_unavailable",
            evaluation_time=at(11),
        )
        is RequestDisposition.TERMINAL_FAILURE
    )
    assert coordinator.backlog() == 0

    fresh = request_for(instrument="IV:3", at_minute=10, expires=30)
    coordinator.submit(fresh, evaluation_time=at(10))
    assert (
        coordinator.complete(fresh.evaluation_identity, evaluation_time=at(10)) is True
    )
    assert (
        coordinator.complete(fresh.evaluation_identity, evaluation_time=at(11)) is False
    )
    assert coordinator.telemetry().served == 1
    state = coordinator.export_state()
    assert fresh.evaluation_identity in state["served"]
    assert state["schema_version"] == "evaluation-coordinator-v1"




# ---------------------------------------------------------------------------
# AC-007 F2 and F3 ownership, purity and grid compliance are preserved
# ---------------------------------------------------------------------------


def drive_observe(
    engine: ContinuousOpportunityEngine,
    minute: int,
    requested: AttentionState | None,
    *,
    instrument: str = "IV:1",
):
    return engine.observe(
        instrument_version_id=instrument,
        venue_instrument_id=f"{instrument}-USD",
        source_label="kraken_ohlc",
        reference_data_version="rdv1",
        window_start_utc=T0,
        interval_seconds=GRID,
        evidence=intervals(minute),
        watermark=SourceWatermark(
            instrument_version_id=instrument,
            through_utc=at(minute),
            last_ingestion_order=minute,
        ),
        evaluation_cutoff=at(minute),
        evaluated_at_utc=at(minute),
        source_event_ids=(f"EV-{instrument}-{minute}",),
        progression_reasons=(AttentionReason.RELATIVE_VOLUME_STRENGTHENING,),
        requested_state=requested,
    )


def test_ac_007_the_ignition_detector_stays_pure_and_grid_compliant():
    """ATDD-R5-A-continuous-opportunity-engine/AC-007: F3 is unchanged.

    GIVEN the frozen IGNITION detector module
    WHEN its imports and source are inspected
    THEN it imports no network, database, subprocess, clock or filesystem
    facility and it still evaluates on the one-minute grid
    """

    detector_path = APP_ROOT / "app" / "opip" / "detectors" / "ignition.py"
    source = detector_path.read_text(encoding="utf-8")
    imports = module_imports(detector_path)
    forbidden = {
        "requests",
        "httpx",
        "urllib",
        "socket",
        "sqlite3",
        "subprocess",
        "time",
        "pathlib",
        "os",
        "random",
    }
    assert imports & forbidden == set()
    for pattern in ("datetime.now(", "utcnow(", "time.time(", "os.environ"):
        assert pattern not in source
    assert ignition.EVALUATION_GRID_SECONDS == GRID
    assert ignition.POLICY_VERSION == "ignition-shadow-policy-v1"
    assert ignition.DETECTOR_VERSION == "ignition-detector-v1"


def test_ac_007_detector_replay_is_identical_for_identical_inputs():
    """ATDD-R5-A-continuous-opportunity-engine/AC-007: deterministic detector.

    GIVEN the same sealed snapshot, prior state and explicit instant
    WHEN the detector is evaluated twice
    THEN it produces the same claim identities and the same next state
    """

    first_claim, first_state = ignition_claim()
    second_claim, second_state = ignition_claim()
    assert first_claim.claim_id == second_claim.claim_id
    assert first_claim.idempotency_key == second_claim.idempotency_key
    assert first_state == second_state


def test_ac_007_attention_state_never_reaches_the_detector():
    """ATDD-R5-A-continuous-opportunity-engine/AC-007: attention cannot rewrite F3.

    GIVEN the engine's detector call site and a spy detector
    WHEN the call is inspected structurally and executed
    THEN it receives the canonical snapshot, the detector state and the explicit
    cutoff only, an attention transition carries no claim, and two engines with
    identical attention history produce identical claim evidence
    """

    engine_path = APP_ROOT / "app" / "opip" / "events" / "opportunity_engine.py"
    tree = ast.parse(engine_path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_detector"
    ]
    assert len(calls) == 1
    arguments = calls[0].args
    assert [argument.id for argument in arguments[:2]] == ["snapshot", "prior_state"]
    assert isinstance(arguments[2], ast.Attribute)
    assert arguments[2].attr == "evidence_cutoff"

    calls_seen: list[tuple] = []

    def spy(injected, prior_state, evaluation_time):
        calls_seen.append((injected, prior_state, evaluation_time))
        return ignition.evaluate(injected, prior_state, evaluation_time)

    def build() -> ContinuousOpportunityEngine:
        return ContinuousOpportunityEngine(
            attention_policy=attention_policy(
                debounce_intervals=0, oscillation_guard_intervals=0
            ),
            coordinator_limits=coordinator_limits(),
            detector_evaluate=spy,
        )

    first = build()
    second = build()
    for engine in (first, second):
        drive_observe(engine, 7, AttentionState.FOCUSED)
    assert first.attention_state("IV:1") == second.attention_state("IV:1")
    assert first.attention_state("IV:1").state is AttentionState.WATCH

    first_outcome = first.evaluate_due(
        evaluation_time=at(7), snapshots={"IV:1": snapshot(7)}
    ).served
    second_outcome = second.evaluate_due(
        evaluation_time=at(7), snapshots={"IV:1": snapshot(7)}
    ).served
    assert calls_seen, "the detector must have been called"
    for injected, prior_state, instant in calls_seen:
        assert isinstance(injected, FeatureSnapshot)
        assert prior_state.__class__.__name__ == "DetectorState"
        assert instant == at(7)
        assert "attention" not in injected.values
    assert [
        (item.disposition, item.claim_id, item.detector_phase)
        for item in first_outcome
    ] == [
        (item.disposition, item.claim_id, item.detector_phase)
        for item in second_outcome
    ]
    trigger = first.ledger.policy
    assert trigger is not None
    transition = advance_attention(
        AttentionStateRecord.initial("IV:2"),
        evidence_for("IV:2", 6, requested=AttentionState.DEVELOPING),
        attention_policy(),
    )
    assert not hasattr(transition.trigger, "claim_id")
    assert not hasattr(transition.trigger, "episode_id")


def test_ac_007_engine_refuses_to_synthesize_or_mismatch_a_snapshot():
    """ATDD-R5-A-continuous-opportunity-engine/AC-007: no snapshot synthesis.

    GIVEN due evaluations whose canonical F2 snapshot is absent, belongs to
    another instrument or carries a different cutoff
    WHEN the engine evaluates them
    THEN each fails closed with its own reason and none produces a claim
    """

    engine_path = APP_ROOT / "app" / "opip" / "events" / "opportunity_engine.py"
    tree = ast.parse(engine_path.read_text(encoding="utf-8"))
    constructed = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "FeatureSnapshot"
    ]
    assert constructed == []

    def engine_for(minute: int) -> ContinuousOpportunityEngine:
        engine = ContinuousOpportunityEngine(
            attention_policy=attention_policy(debounce_intervals=0),
            coordinator_limits=coordinator_limits(),
        )
        drive_observe(engine, minute, AttentionState.DEVELOPING)
        return engine

    absent = engine_for(7).evaluate_due(evaluation_time=at(7), snapshots={})
    assert [item.reason for item in absent.served] == ["snapshot_unavailable"]

    wrong_instrument = engine_for(7).evaluate_due(
        evaluation_time=at(7), snapshots={"IV:1": snapshot(7, instrument="IV:9")}
    )
    assert [item.reason for item in wrong_instrument.served] == [
        "snapshot_instrument_mismatch"
    ]

    wrong_cutoff = engine_for(7).evaluate_due(
        evaluation_time=at(7), snapshots={"IV:1": snapshot(8)}
    )
    assert [item.reason for item in wrong_cutoff.served] == [
        "snapshot_cutoff_mismatch"
    ]




# ---------------------------------------------------------------------------
# AC-008 F4: one canonical episode lineage per claim
# ---------------------------------------------------------------------------


def test_ac_008_episode_identity_is_deterministic_and_claim_bound():
    """ATDD-R5-A-continuous-opportunity-engine/AC-008: deterministic lineage.

    GIVEN one IGNITION claim
    WHEN the canonical episode is opened twice
    THEN both opens reproduce the same episode and event identities and the
    episode carries exactly one OPENED event
    """

    claim, _ = ignition_claim()
    first = episode_from_claim(claim, evaluation_time=at(7))
    second = episode_from_claim(claim, evaluation_time=at(7))
    assert first.episode.episode_id == second.episode.episode_id
    assert first.episode.lifecycle_state is OpportunityLifecycleState.ACTIVE
    assert first.changed is True
    assert [event.event_type.value for event in first.events] == ["OPENED"]
    assert first.events[0].event_id == second.events[0].event_id
    assert episode_phase(first.episode, evaluation_time=at(7)) is (
        OpportunityPhase.ELIGIBLE
    )


def test_ac_008_a_duplicate_delivery_never_opens_a_second_episode():
    """ATDD-R5-A-continuous-opportunity-engine/AC-008: no duplicate opportunity.

    GIVEN the same snapshot, prior detector state and cutoff evaluated by two
    engines
    WHEN both produce evidence
    THEN the claim and episode identities are identical and one episode lineage
    is retained
    """

    def run():
        engine = ContinuousOpportunityEngine(
            attention_policy=attention_policy(
                debounce_intervals=0, oscillation_guard_intervals=0
            ),
            coordinator_limits=coordinator_limits(),
        )
        drive_observe(engine, 7, AttentionState.DEVELOPING)
        cycle = engine.evaluate_due(
            evaluation_time=at(7), snapshots={"IV:1": snapshot(7)}
        )
        return engine, cycle

    first_engine, first_cycle = run()
    second_engine, second_cycle = run()
    assert [item.detector_phase for item in first_cycle.served] == [
        item.detector_phase for item in second_cycle.served
    ]
    assert len(first_engine.episodes()) == len(second_engine.episodes())
    assert set(first_engine.episodes()) == set(second_engine.episodes())


def test_ac_008_a_wait_is_bounded_and_an_expired_episode_never_resumes():
    """ATDD-R5-A-continuous-opportunity-engine/AC-008: bounded wait, terminal stays.

    GIVEN an episode opened into a caller-supplied deferral window
    WHEN it is evaluated before and after its deadline
    THEN it reports WAIT, then EXPIRED with a terminal reason, and a later
    evaluation or requalification cannot reopen it
    """

    claim, _ = ignition_claim()
    deferral = OpportunityDeferral(defer_deadline=at(9), validity_deadline=at(20))
    opened = episode_from_claim(claim, evaluation_time=at(7), deferral=deferral)
    assert opened.episode.lifecycle_state is OpportunityLifecycleState.DEFERRED
    assert episode_phase(opened.episode, evaluation_time=at(8)) is (
        OpportunityPhase.WAIT
    )
    assert episode_phase(opened.episode, evaluation_time=at(9)) is (
        OpportunityPhase.EXPIRED
    )

    before = advance_episode(opened.episode, evaluation_time=at(8))
    assert before.changed is False
    expired = advance_episode(opened.episode, evaluation_time=at(9))
    assert expired.changed is True
    assert expired.episode.lifecycle_state is OpportunityLifecycleState.TERMINAL
    assert expired.episode.terminal_reason is OpportunityTerminalReason.EXPIRED
    assert [event.event_type.value for event in expired.events] == ["EXPIRED"]

    replay = advance_episode(expired.episode, evaluation_time=at(30))
    assert replay.changed is False
    assert replay.episode.episode_id == expired.episode.episode_id
    assert episode_phase(expired.episode, evaluation_time=at(30)) is (
        OpportunityPhase.EXPIRED
    )
    with pytest.raises(PhaseContractError):
        requalify_episode(
            expired.episode,
            evaluation_time=at(30),
            new_decision_id="DEC:later",
            evidence_cutoff=at(30),
        )


def test_ac_008_requalification_links_a_new_decision_without_resetting_the_clock():
    """ATDD-R5-A-continuous-opportunity-engine/AC-008: linked requalification.

    GIVEN an open episode
    WHEN a fresh decision requalifies it
    THEN the episode identity and claim lineage are preserved, the link names the
    new decision, and a non-monotonic or unchanged-decision requalification is
    refused
    """

    claim, _ = ignition_claim()
    opened = episode_from_claim(claim, evaluation_time=at(7))
    updated, link = requalify_episode(
        opened.episode,
        evaluation_time=at(12),
        new_decision_id="DEC:requalify",
        evidence_cutoff=at(11),
    )
    assert updated.episode_id == opened.episode.episode_id
    assert updated.source_claim_id == opened.episode.source_claim_id
    assert updated.last_evaluation_time == at(12)
    assert link.new_decision_id == "DEC:requalify"
    assert link.episode_id == opened.episode.episode_id
    assert link.downstream_authority == PHASE_AUTHORITY

    with pytest.raises(PhaseContractError):
        requalify_episode(
            opened.episode,
            evaluation_time=at(6),
            new_decision_id="DEC:older",
            evidence_cutoff=at(6),
        )
    with pytest.raises(PhaseContractError):
        requalify_episode(
            opened.episode,
            evaluation_time=at(12),
            new_decision_id="DEC:requalify",
            evidence_cutoff=at(11),
            prior_decision_id="DEC:requalify",
        )


def test_ac_008_phase_projection_maps_onto_existing_enums_without_authority():
    """ATDD-R5-A-continuous-opportunity-engine/AC-008: mapping, not a new machine.

    GIVEN the R5-A logical phases
    WHEN each is projected
    THEN every phase has one declared row, the canonical states are the existing
    F4 enums, the downstream-only phases are refused and no row carries authority
    """

    assert set(PHASE_MAPPINGS) == set(OpportunityPhase)
    for phase, mapping in PHASE_MAPPINGS.items():
        assert project_phase(phase) is mapping
        assert project_phase(phase.value) is mapping
        assert mapping.downstream_authority == PHASE_AUTHORITY
        if mapping.has_canonical_record:
            assert isinstance(mapping.f4_state, OpportunityLifecycleState)
        else:
            assert mapping.f4_state is None and mapping.terminal_reason is None
    assert PHASE_MAPPINGS[OpportunityPhase.ELIGIBLE].f4_state is (
        OpportunityLifecycleState.ACTIVE
    )
    assert PHASE_MAPPINGS[OpportunityPhase.WAIT].f4_state is (
        OpportunityLifecycleState.DEFERRED
    )
    assert PHASE_MAPPINGS[OpportunityPhase.WAIT].requires_deferral_deadline is True
    assert PHASE_MAPPINGS[OpportunityPhase.EXPIRED].terminal_reason is (
        OpportunityTerminalReason.EXPIRED
    )
    for token in DOWNSTREAM_ONLY_PHASES:
        with pytest.raises(PhaseContractError):
            project_phase(token)
    with pytest.raises(PhaseContractError):
        project_phase("UNKNOWN_PHASE")


# ---------------------------------------------------------------------------
# AC-009 HORIZON: versioned classification with honest abstention
# ---------------------------------------------------------------------------


def test_ac_009_an_unconfigured_horizon_policy_abstains():
    """ATDD-R5-A-continuous-opportunity-engine/AC-009: no invented boundaries.

    GIVEN the unratified horizon policy
    WHEN a thesis is classified
    THEN the result is research-only UNKNOWN with POLICY_NOT_CONFIGURED, it
    carries no holding range, and it declares zero allocation authority
    """

    policy = HorizonPolicy()
    assert policy.policy_version == "horizon-shadow-policy-v1"
    assert policy.configured is False
    thesis = classify_horizon(thesis_evidence(), policy)
    assert thesis.horizon_class is HorizonClass.UNKNOWN
    assert thesis.abstention_reason is AbstentionReason.POLICY_NOT_CONFIGURED
    assert thesis.research_only is True
    assert thesis.expected_holding_seconds_min is None
    assert thesis.expected_holding_seconds_max is None
    assert thesis.allocation_authority == "NONE"
    assert thesis.classifier_ratified is False


def test_ac_009_missing_or_contradictory_evidence_abstains():
    """ATDD-R5-A-continuous-opportunity-engine/AC-009: missingness is not favourable.

    GIVEN evidence without an expected holding range and evidence that carries a
    contradiction
    WHEN each is classified under an engineering-scenario policy
    THEN the first abstains for missing holding evidence and the second abstains
    as contradictory with high uncertainty
    """

    policy = horizon_policy()
    missing = classify_horizon(
        thesis_evidence(expected_holding_seconds=None), policy
    )
    assert missing.abstention_reason is AbstentionReason.MISSING_HOLDING_EVIDENCE
    contradictory = classify_horizon(
        thesis_evidence(contradicting_evidence=("liquidity:collapse",)), policy
    )
    assert contradictory.abstention_reason is AbstentionReason.CONTRADICTORY_EVIDENCE
    assert contradictory.uncertainty is ClassificationUncertainty.HIGH


def test_ac_009_position_requires_structural_evidence_not_ignition_alone():
    """ATDD-R5-A-continuous-opportunity-engine/AC-009: IGNITION is not a POSITION.

    GIVEN a long expected holding supported only by the detector
    WHEN it is classified under an engineering-scenario policy
    THEN it abstains for insufficient structural evidence, and only real
    structural support with liquidity and enough milestones yields POSITION
    """

    policy = horizon_policy()
    ignition_only = classify_horizon(
        thesis_evidence(
            expected_holding_seconds=2_000_000,
            detector_support=True,
            structural_support=False,
            structural_milestones=0,
            liquidity_support=True,
        ),
        policy,
    )
    assert ignition_only.horizon_class is HorizonClass.UNKNOWN
    assert ignition_only.abstention_reason is (
        AbstentionReason.INSUFFICIENT_STRUCTURAL_EVIDENCE
    )
    assert ignition_only.expected_holding_seconds_min is None

    supported = classify_horizon(
        thesis_evidence(
            expected_holding_seconds=2_000_000,
            detector_support=True,
            structural_support=True,
            structural_milestones=3,
            liquidity_support=True,
        ),
        policy,
    )
    assert supported.horizon_class is HorizonClass.POSITION
    assert supported.abstention_reason is AbstentionReason.NONE
    assert supported.expected_holding_seconds_min == 1_209_601
    assert supported.expected_holding_seconds_max == 7_776_000
    assert supported.allocation_authority == "NONE"
    assert supported.research_only is False

    thin_liquidity = classify_horizon(
        thesis_evidence(
            expected_holding_seconds=2_000_000,
            structural_support=True,
            structural_milestones=3,
            liquidity_support=False,
        ),
        policy,
    )
    assert thin_liquidity.abstention_reason is AbstentionReason.MISSING_LIQUIDITY_SUPPORT

    # Claimed milestones without real structural support must not count: this
    # case is refused by the structural gate alone, not by the milestone count.
    unsupported_structure = classify_horizon(
        thesis_evidence(
            expected_holding_seconds=2_000_000,
            detector_support=True,
            structural_support=False,
            structural_milestones=4,
            liquidity_support=True,
        ),
        policy,
    )
    assert unsupported_structure.abstention_reason is (
        AbstentionReason.INSUFFICIENT_STRUCTURAL_EVIDENCE
    )
    assert unsupported_structure.expected_holding_seconds_min is None

    gap_policy = HorizonPolicy(
        policy_version=HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION,
        holding_bounds_seconds={
            HorizonClass.TACTICAL: (GRID, 100),
            HorizonClass.SWING: (500, 1_000),
        },
        requirements=HorizonRequirements(position_min_structural_milestones=2),
    )
    unsupported_band = classify_horizon(
        thesis_evidence(expected_holding_seconds=200), gap_policy
    )
    assert unsupported_band.abstention_reason is AbstentionReason.UNSUPPORTED_HORIZON


def test_ac_009_horizon_bands_are_validated_and_may_not_overlap():
    """ATDD-R5-A-continuous-opportunity-engine/AC-009: owner bands are checked.

    GIVEN bands that overlap, invert, bound UNKNOWN or carry a non-integer value
    WHEN the policy is constructed
    THEN each is refused, and a policy with no bands simply abstains
    """

    with pytest.raises(HorizonContractError):
        HorizonPolicy(
            policy_version=HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION,
            holding_bounds_seconds={
                HorizonClass.TACTICAL: (GRID, 100),
                HorizonClass.SWING: (100, 200),
            },
        )
    with pytest.raises(HorizonContractError):
        HorizonPolicy(
            policy_version=HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION,
            holding_bounds_seconds={HorizonClass.TACTICAL: (200, 100)},
        )
    with pytest.raises(HorizonContractError):
        HorizonPolicy(
            policy_version=HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION,
            holding_bounds_seconds={HorizonClass.UNKNOWN: (GRID, 100)},
        )
    with pytest.raises(HorizonContractError):
        HorizonPolicy(
            policy_version=HORIZON_ENGINEERING_SCENARIO_POLICY_VERSION,
            holding_bounds_seconds={HorizonClass.TACTICAL: (1.5, 100)},
        )
    with pytest.raises(HorizonContractError):
        HorizonPolicy(policy_version="horizon-ratified-policy-v9")
    assert HorizonPolicy().configured is False
    assert HorizonPolicy().owner_input_status == "OWNER_DECISION_REQUIRED"


def test_ac_009_a_thesis_revision_is_immutable_and_a_relabel_is_refused():
    """ATDD-R5-A-continuous-opportunity-engine/AC-009: never relabel by convenience.

    GIVEN a tactical thesis that has expired
    WHEN a later classification tries to reuse it as a long-term thesis without
    new structural evidence
    THEN a new linked revision abstains with the relabel refusal reason, the new
    revision supersedes the old identity, the original revision is unchanged and
    a reclassification cannot reuse the same decision
    """

    policy = horizon_policy()
    tactical = classify_horizon(thesis_evidence(), policy)
    assert tactical.horizon_class is HorizonClass.TACTICAL
    expired = expire_thesis(tactical, now=at(900))
    assert expired.lifecycle_state.value == "EXPIRED"

    relabelled = reclassify(
        expired,
        thesis_evidence(
            decision_id="DEC:position",
            evidence_cutoff=at(1000),
            classified_at=at(1000),
            expected_holding_seconds=2_000_000,
            structural_support=False,
            detector_support=True,
            time_invalidation=at(5000),
        ),
        policy,
        new_decision_id="DEC:position",
    )
    assert relabelled.horizon_class is HorizonClass.UNKNOWN
    assert relabelled.abstention_reason is (
        AbstentionReason.RELABEL_REFUSED_PENDING_STRUCTURAL_EVIDENCE
    )
    assert relabelled.supersedes == tactical.thesis_id
    assert relabelled.thesis_version == 2
    assert relabelled.thesis_id != tactical.thesis_id
    # the original revision is frozen, not rewritten
    assert tactical.horizon_class is HorizonClass.TACTICAL
    assert tactical.supersedes is None
    assert tactical.lifecycle_state.value == "ACTIVE"

    with pytest.raises(HorizonContractError):
        reclassify(
            tactical,
            thesis_evidence(
                decision_id="DEC:1",
                evidence_cutoff=at(20),
                classified_at=at(20),
            ),
            policy,
            new_decision_id="DEC:1",
        )
    with pytest.raises(HorizonContractError):
        expire_thesis(tactical, now=at(11))




# ---------------------------------------------------------------------------
# AC-010 Zero trading authority and isolation from runtime and release controls
# ---------------------------------------------------------------------------

FORBIDDEN_AUTHORITY_TOKENS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "position_size",
    "reserve_capital",
    "allocate(",
    "kraken",
    "api_secret",
    "api_key",
)

FORBIDDEN_PRIMITIVES = {
    "requests",
    "httpx",
    "urllib",
    "socket",
    "sqlite3",
    "subprocess",
    "os",
    "time",
    "random",
    "pathlib",
    "asyncio",
}


def test_ac_010_the_new_modules_carry_no_execution_or_credential_surface():
    """ATDD-R5-A-continuous-opportunity-engine/AC-010: zero authority by construction.

    GIVEN the R5-A module set
    WHEN each module's imports and source are inspected
    THEN none imports a network, database, subprocess, filesystem, clock or
    randomness facility and none names an order, sizing, reservation or
    credential surface
    """

    for module in R5A_MODULES:
        assert module.is_file(), module
        source = module.read_text(encoding="utf-8").lower()
        imports = module_imports(module)
        assert imports & FORBIDDEN_PRIMITIVES == set(), module.name
        for token in FORBIDDEN_AUTHORITY_TOKENS:
            assert token not in source, f"{module.name}: {token}"


def test_ac_010_no_runtime_root_imports_the_new_modules():
    """ATDD-R5-A-continuous-opportunity-engine/AC-010: not wired into runtime.

    GIVEN the runtime roots that own trading, discovery, decision and risk paths
    WHEN their imports are inspected
    THEN none imports an R5-A module, so the engine cannot act on production
    state
    """

    targets = (
        "app.opip.events.attention",
        "app.opip.events.coverage",
        "app.opip.events.opportunity_engine",
        "app.opip.streaming.coordinator",
        "app.opip.contracts.thesis",
        "app.opip.contracts.opportunity_phases",
    )
    offenders: list[str] = []
    for root in RUNTIME_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            hits = sorted(name for name in module_imports(path) if name in targets)
            if hits:
                offenders.append(f"{path.name}: {hits}")
    assert offenders == []


def test_ac_010_release_profile_safeguards_are_unchanged():
    """ATDD-R5-A-continuous-opportunity-engine/AC-010: posture preserved.

    GIVEN the repository release-profile contract and Compose literals
    WHEN they are inspected
    THEN EVIDENCE_SHADOW remains the active shadow profile, TARGET_PAPER remains
    blocked, paper-v2 stays off, and the new increment adds no mode key
    """

    profiles = get_release_profiles()
    assert set(profiles) == {"SAFE_BASELINE", "EVIDENCE_SHADOW", "TARGET_PAPER"}
    assert validate_profile_contract("EVIDENCE_SHADOW") == (True, [])
    valid, reasons = validate_profile_contract("TARGET_PAPER")
    assert valid is False
    assert reasons and "blocked" in reasons[0]
    allowed = profiles["EVIDENCE_SHADOW"]["allowed_modes"]
    assert allowed["OPIP_FEATURE_BUS_MODE"] == "shadow"
    assert allowed["OPIP_CANONICAL_WRITER_MODE"] == "shadow"
    assert allowed["OPIP_TARGET_SPINE_MODE"] == "shadow"
    assert allowed["OPIP_PAPER_V2_MODE"] == "off"
    assert allowed["OPIP_COMMITTEE_MODE"] == "off"
    assert not any(key.startswith("OPIP_R5") for key in allowed)

    compose = yaml.safe_load(
        (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    service_environment = compose["services"]["ohm-trade-agent"]["environment"]
    assert service_environment["OPIP_PAPER_V2_MODE"] == "off"
    assert service_environment["OPIP_FEATURE_BUS_MODE"] == "shadow"

def test_ac_010_the_r3_isolation_allowlists_were_widened_without_weakening_them():
    """ATDD-R5-A-continuous-opportunity-engine/AC-010: widen-only test scoping.

    GIVEN the two frozen R3 isolation suites that scan every module under `app/`
    WHEN their allowlists are read after this increment's authorized change
    THEN the two R5-A consumers are named as the only additions and every
    substantive isolation assertion of those suites is still present, so no
    check was removed or relaxed to make this increment pass
    """

    f3_source = (APP_ROOT / "tests" / "test_opip_r3_f3_ignition_detector.py").read_text(
        encoding="utf-8"
    )
    f4_source = (
        APP_ROOT / "tests" / "test_opip_r3_f4_opportunity_lifecycle.py"
    ).read_text(encoding="utf-8")
    for source in (f3_source, f4_source):
        assert "opportunity_engine.py" in source
        assert "opportunity_phases.py" in source
        assert 'OPIP_FEATURE_BUS_MODE: "shadow"' in source
        assert "FORBIDDEN_MODULE_PREFIXES" in source
        assert "AUTHORITY_TOKENS" in source
    assert "run_cycle" in f4_source
    assert '"opip.detectors" not in text' in f3_source
    assert '"contracts.opportunity" not in text' in f4_source
    assert '"contracts.detector" not in text' in f3_source




# ---------------------------------------------------------------------------
# AC-011 The engine is feature-disabled with no hidden activation path
# ---------------------------------------------------------------------------


def test_ac_011_the_engine_mode_is_off_and_reads_no_environment():
    """ATDD-R5-A-continuous-opportunity-engine/AC-011: no hidden activation.

    GIVEN the compiled engine mode and the R5-A module sources
    WHEN they are inspected
    THEN the mode is the literal `off`, the component declares that it reads no
    environment variable, and no module reads the environment
    """

    assert R5A_ENGINE_MODE == "off"
    assert engine_mode() == "off"
    assert R5A_ENV_VARIABLES_READ == ()
    assert engine_reads_environment() is False
    assert R5A_ENGINE_AUTHORITY == "NONE"
    for module in R5A_MODULES:
        source = module.read_text(encoding="utf-8")
        for pattern in ("os.environ", "os.getenv", "getenv("):
            assert pattern not in source, module.name
        assert "OPIP_" not in source, module.name


def test_ac_011_an_activation_attempt_is_refused():
    """ATDD-R5-A-continuous-opportunity-engine/AC-011: no activation path.

    GIVEN a caller that requests the declared shadow mode or an invented mode
    WHEN the engine is constructed
    THEN both are refused, so activation remains an owner release act
    """

    for mode in ("shadow", "active", "on"):
        with pytest.raises(OpportunityEngineError):
            ContinuousOpportunityEngine(
                attention_policy=attention_policy(),
                coordinator_limits=coordinator_limits(),
                mode=mode,
            )
    with pytest.raises(CoordinatorContractError):
        ContinuousOpportunityEngine(
            attention_policy=attention_policy(),
            coordinator_limits=coordinator_limits(protection_reserved_slots=99),
        )


def test_ac_011_an_unratified_policy_keeps_the_engine_inert():
    """ATDD-R5-A-continuous-opportunity-engine/AC-011: conservative when unconfigured.

    GIVEN an engine built with unratified attention and horizon policies
    WHEN the full cycle runs over real observations
    THEN no attention state advances, no request is admitted, no claim or episode
    exists, and the report states that both policies are unratified
    """

    engine = ContinuousOpportunityEngine(
        attention_policy=AttentionPolicy(),
        coordinator_limits=coordinator_limits(),
    )
    cycle = drive_observe(engine, 6, AttentionState.FOCUSED)
    assert cycle.submission is None
    assert cycle.transition.current.state is AttentionState.DORMANT
    assert engine.evaluate_due(evaluation_time=at(7), snapshots={}).served == ()
    report = engine.cycle_report()
    assert report["mode"] == "off"
    assert report["authority"] == "NONE"
    assert report["attention_policy_ratified"] is False
    assert report["horizon_policy_configured"] is False
    assert report["counts"]["claims_emitted"] == 0
    assert report["episodes"] == []
    assert report["populations"]["eligible"] == 1
    assert report["populations"]["budget_skipped"] == 1
    assert report["populations"]["outstanding"] == 0



# ---------------------------------------------------------------------------
# AC-012 Point-in-time integrity and deterministic replay
# ---------------------------------------------------------------------------


def test_ac_012_visibility_is_receipt_time_not_source_time():
    """ATDD-R5-A-continuous-opportunity-engine/AC-012: receipt defines visibility.

    GIVEN reconciled coverage
    WHEN its availability stamp is read
    THEN visibility equals the latest receipt, never the venue source time, so a
    delayed event cannot be treated as current information
    """

    record = coverage(4, receipt_offset=3)
    assert record.availability is not None
    assert record.availability.visible_at_utc == record.latest_receipt_time
    assert record.latest_receipt_time > record.latest_source_event_time
    assert record.usable_at(cutoff=at(4)) is False
    assert record.usable_at(cutoff=at(20)) is False
    later = coverage(4, receipt_offset=3, evaluated=20)
    assert later.usable_at(cutoff=at(20)) is True


def test_ac_012_evidence_permutation_reproduces_the_same_coverage_record():
    """ATDD-R5-A-continuous-opportunity-engine/AC-012: order independence.

    GIVEN the same interval evidence delivered in a different order
    WHEN coverage is reconciled
    THEN the coverage identity and the gap identities are identical
    """

    delivered = intervals(5)
    shuffled = [delivered[3], delivered[0], delivered[4], delivered[1], delivered[2]]
    watermark = SourceWatermark(
        instrument_version_id="IV:1", through_utc=at(5), last_ingestion_order=5
    )
    kwargs = {
        "instrument_version_id": "IV:1",
        "venue_instrument_id": "XBTUSD",
        "source_label": "kraken_ohlc",
        "reference_data_version": "rdv1",
        "window_start_utc": T0,
        "interval_seconds": GRID,
        "watermark": watermark,
        "evaluated_at_utc": at(5),
    }
    in_order = reconcile_coverage(evidence=delivered, **kwargs)
    reordered = reconcile_coverage(evidence=shuffled, **kwargs)
    assert in_order.coverage_id == reordered.coverage_id
    assert in_order.to_dict() == reordered.to_dict()
    assert in_order.dispositions == reordered.dispositions

    broken = reconcile_coverage(
        evidence=[delivered[0], delivered[1]], **kwargs
    )
    assert broken.coverage_id != in_order.coverage_id
    assert [gap.gap_id for gap in broken.gaps] == [
        gap.gap_id for gap in reconcile_coverage(
            evidence=[delivered[1], delivered[0]], **kwargs
        ).gaps
    ]


def test_ac_012_the_new_modules_hold_no_hidden_clock():
    """ATDD-R5-A-continuous-opportunity-engine/AC-012: explicit instants only.

    GIVEN the R5-A module set
    WHEN their sources are inspected
    THEN none calls a clock or a random source, so a replay cannot depend on when
    it runs
    """

    for module in R5A_MODULES:
        source = module.read_text(encoding="utf-8")
        for pattern in (
            "datetime.now(",
            "datetime.utcnow(",
            "time.time(",
            "monotonic(",
            "random.",
            "uuid4(",
        ):
            assert pattern not in source, f"{module.name}: {pattern}"


def test_ac_012_a_full_cycle_replays_identically():
    """ATDD-R5-A-continuous-opportunity-engine/AC-012: deterministic composition.

    GIVEN two engines driven with the same observations, policy and snapshots
    WHEN both complete a cycle
    THEN their reports, populations and episode theses are identical
    """

    def run() -> dict:
        engine = ContinuousOpportunityEngine(
            attention_policy=attention_policy(
                debounce_intervals=0, oscillation_guard_intervals=0
            ),
            coordinator_limits=coordinator_limits(),
            horizon_policy=horizon_policy(),
        )
        drive_observe(engine, 6, AttentionState.DEVELOPING)
        engine.evaluate_due(evaluation_time=at(6), snapshots={"IV:1": snapshot(6)})
        drive_observe(engine, 7, AttentionState.FOCUSED)
        engine.evaluate_due(evaluation_time=at(7), snapshots={"IV:1": snapshot(7)})
        return engine.cycle_report()

    first = run()
    second = run()
    assert first == second
    assert first["theses"], "the cycle must have produced a thesis record"
    assert {thesis["allocation_authority"] for thesis in first["theses"]} == {"NONE"}




# ---------------------------------------------------------------------------
# AC-013 Restart, recovery and fail-closed version handling
# ---------------------------------------------------------------------------


def test_ac_013_a_replayed_engine_reproduces_the_same_request_identities():
    """ATDD-R5-A-continuous-opportunity-engine/AC-013: restart-safe identities.

    GIVEN observations whose attention transitions were admitted by one engine
    WHEN a fresh engine replays exactly the same observations
    THEN the trigger and evaluation identities are identical, so a restart
    cannot create a second evaluation identity
    """

    def replay():
        engine = ContinuousOpportunityEngine(
            attention_policy=attention_policy(
                debounce_intervals=0, oscillation_guard_intervals=0
            ),
            coordinator_limits=coordinator_limits(),
        )
        cycle = drive_observe(engine, 6, AttentionState.DEVELOPING)
        return engine, cycle

    first_engine, first_cycle = replay()
    second_engine, second_cycle = replay()
    assert first_cycle.transition.trigger.trigger_id == (
        second_cycle.transition.trigger.trigger_id
    )
    assert first_cycle.request.request_id == second_cycle.request.request_id
    assert first_cycle.request.evaluation_identity == (
        second_cycle.request.evaluation_identity
    )
    assert first_cycle.submission.disposition is second_cycle.submission.disposition


def test_ac_013_a_restart_after_evaluation_before_acknowledgement_is_idempotent():
    """ATDD-R5-A-continuous-opportunity-engine/AC-013: crash between compute and commit.

    GIVEN an evaluation that completed but was not acknowledged
    WHEN the transition is replayed and then acknowledged twice
    THEN the second acknowledgement is refused, no second transition is produced
    and no further evaluation remains due
    """

    ledger = AttentionTriggerLedger(policy=attention_policy())
    transition = admitted_transition(minute=6)
    ledger.submit(transition.trigger, evaluation_time=at(6))
    assert len(ledger.due(evaluation_time=at(6))) == 1

    replay = AttentionTriggerLedger(policy=attention_policy())
    replay.submit(transition.trigger, evaluation_time=at(6))
    assert [item.trigger_id for item in replay.due(evaluation_time=at(6))] == [
        item.trigger_id for item in ledger.due(evaluation_time=at(6))
    ]
    assert replay.acknowledge(transition.trigger.trigger_id) is True
    assert replay.acknowledge(transition.trigger.trigger_id) is False
    assert replay.due(evaluation_time=at(6)) == ()
    populations = replay.populations()
    assert populations.evaluated == 1
    assert populations.outstanding == 0
    assert populations.reconciles is True


def test_ac_013_an_unknown_policy_version_fails_closed():
    """ATDD-R5-A-continuous-opportunity-engine/AC-013: unknown versions are refused.

    GIVEN an attention policy, a horizon policy and a trigger identity carrying
    an undeclared version token
    WHEN each is constructed
    THEN each is refused rather than silently substituted
    """

    with pytest.raises(AttentionContractError):
        AttentionPolicy(policy_version="attention-ratified-policy-v9")
    with pytest.raises(HorizonContractError):
        HorizonPolicy(policy_version="horizon-ratified-policy-v9")
    with pytest.raises(AttentionContractError):
        attention_trigger_identity(
            instrument_version_id="IV:1",
            prior_state=AttentionState.DORMANT,
            new_state=AttentionState.WATCH,
            policy_version="attention-ratified-policy-v9",
            evidence_cutoff=at(6),
            source_event_ids=("EV",),
        )


def test_ac_013_a_backfill_cannot_rewrite_a_prior_coverage_record():
    """ATDD-R5-A-continuous-opportunity-engine/AC-013: append, never rewrite.

    GIVEN an early coverage record
    WHEN the same window is reconciled again and a later window is reconciled
    THEN the early record is reproduced identically, the later record is a
    distinct record with a later watermark, and the early record is unchanged
    """

    early = coverage(4)
    snapshot_of_early = early.to_dict()
    replay = coverage(4)
    assert replay.coverage_id == early.coverage_id
    assert replay.to_dict() == snapshot_of_early

    later = coverage(6)
    assert later.coverage_id != early.coverage_id
    assert later.watermark_through_utc == at(6)
    assert later.expected_intervals == 6
    assert early.to_dict() == snapshot_of_early
    assert early.to_dict()["watermark_through_utc"] != (
        later.to_dict()["watermark_through_utc"]
    )



# ---------------------------------------------------------------------------
# AC-014 Edge-case and failure-mode matrix
# ---------------------------------------------------------------------------


def _engine_with(**overrides):
    return ContinuousOpportunityEngine(
        attention_policy=attention_policy(
            debounce_intervals=0, oscillation_guard_intervals=0, **overrides
        ),
        coordinator_limits=coordinator_limits(),
    )


def _warm_up_incomplete() -> str:
    engine = _engine_with()
    drive_observe(engine, 7, AttentionState.DEVELOPING)
    cycle = engine.evaluate_due(
        evaluation_time=at(7),
        snapshots={
            "IV:1": snapshot(7, restart_state=RestartState.INSUFFICIENT_HISTORY)
        },
    )
    return cycle.served[0].disposition


def _missing_required_feature() -> str:
    engine = _engine_with()
    drive_observe(engine, 7, AttentionState.DEVELOPING)
    incomplete = snapshot(7)
    values = dict(incomplete.values)
    values.pop("compression_release_score")
    missingness = dict(incomplete.missingness)
    missingness["compression_release_score"] = Missingness.MISSING
    degraded = replace(incomplete, values=values, missingness=missingness)
    cycle = engine.evaluate_due(evaluation_time=at(7), snapshots={"IV:1": degraded})
    return cycle.served[0].disposition


def _invalid_market_row() -> str:
    delivered = intervals(3)
    delivered[-1] = IntervalEvidence(
        interval_start_epoch=int(at(2).timestamp()),
        receipt_time=at(3),
        payload_valid=False,
        invalid_reason="non_finite_price",
    )
    record = reconcile_coverage(
        instrument_version_id="IV:1",
        venue_instrument_id="IV:1-USD",
        source_label="kraken_ohlc",
        reference_data_version="rdv1",
        window_start_utc=T0,
        interval_seconds=GRID,
        evidence=delivered,
        watermark=SourceWatermark(
            instrument_version_id="IV:1", through_utc=at(3), last_ingestion_order=3
        ),
        evaluated_at_utc=at(3),
    )
    return record.coverage_state.value


def _unsupported_instrument() -> str:
    return coverage(4, instrument_supported=False).gap_reasons[0]


def _clock_skew_and_disagreement() -> str:
    return coverage(4, receipt_offset=-2).gap_reasons[0]


def _duplicate_and_out_of_order() -> str:
    delivered = intervals(3)
    delivered.append(
        IntervalEvidence(
            interval_start_epoch=int(at(2).timestamp()), receipt_time=at(4)
        )
    )
    record = reconcile_coverage(
        instrument_version_id="IV:1",
        venue_instrument_id="IV:1-USD",
        source_label="kraken_ohlc",
        reference_data_version="rdv1",
        window_start_utc=T0,
        interval_seconds=GRID,
        evidence=delivered,
        watermark=SourceWatermark(
            instrument_version_id="IV:1", through_utc=at(3), last_ingestion_order=3
        ),
        evaluated_at_utc=at(4),
    )
    return record.coverage_state.value


def _trigger_storm_with_protection_due() -> tuple:
    limits = coordinator_limits(queue_capacity=3, per_instrument_budget_per_cycle=1)
    coordinator = UnifiedEvaluationCoordinator(limits=limits)
    for minute in (10, 11, 12):
        coordinator.submit(
            request_for(instrument="IV:1", cutoff=minute, at_minute=minute, expires=99),
            evaluation_time=at(minute),
        )
    protection = request_for(
        instrument="IV:2",
        priority=WorkPriority.PROTECTION,
        path=EvaluationPath.RECOVERY_BACKFILL,
        at_minute=12,
    )
    admitted = coordinator.submit(protection, evaluation_time=at(12)).disposition
    batch = coordinator.next_batch(evaluation_time=at(12), size=2)
    return (admitted.value, [item.priority.value for item in batch])


def _detector_state_conflict() -> str:
    engine = _engine_with()
    drive_observe(engine, 7, AttentionState.DEVELOPING)
    conflict = DetectorState(
        instrument_version_id="IV:OLD",
        venue_instrument_id="IV:OLD-USD",
        persistence_seconds=60,
    )
    cycle = engine.evaluate_due(
        evaluation_time=at(7),
        snapshots={"IV:1": snapshot(7)},
        detector_states={"IV:1": conflict},
    )
    return cycle.served[0].reason.split(":")[0]


def _claim_expiry_before_evaluation() -> int:
    ledger = AttentionTriggerLedger(policy=attention_policy())
    transition = admitted_transition(minute=6)
    ledger.submit(transition.trigger, evaluation_time=at(6))
    ledger.due(evaluation_time=at(30))
    return ledger.populations().expired


def _reobserved_claim_keeps_one_episode() -> bool:
    claim, _ = ignition_claim()
    first = episode_from_claim(claim, evaluation_time=at(7))
    reobserved = episode_from_claim(claim, evaluation_time=at(8))
    return first.episode.episode_id == reobserved.episode.episode_id


def _horizon_expiry_and_missing_calibration() -> tuple:
    policy = horizon_policy()
    thesis = classify_horizon(thesis_evidence(), policy)
    return (
        expire_thesis(thesis, now=at(900)).lifecycle_state.value,
        thesis.forecast_reference,
    )


def _ai_and_notification_are_absent() -> bool:
    banned = ("telegram", "openai", "llm", "anthropic", "notify")
    for module in R5A_MODULES:
        source = module.read_text(encoding="utf-8").lower()
        for token in banned:
            if token in source:
                return False
    return True


def _concurrent_thesis_revisions() -> int:
    policy = horizon_policy()
    first = classify_horizon(thesis_evidence(), policy)
    second = reclassify(
        first,
        thesis_evidence(
            decision_id="DEC:2",
            evidence_cutoff=at(20),
            classified_at=at(20),
            expected_holding_seconds=200_000,
        ),
        policy,
        new_decision_id="DEC:2",
    )
    third = reclassify(
        second,
        thesis_evidence(
            decision_id="DEC:3",
            evidence_cutoff=at(30),
            classified_at=at(30),
            expected_holding_seconds=2_000_000,
            structural_support=True,
            structural_milestones=3,
            liquidity_support=True,
        ),
        policy,
        new_decision_id="DEC:3",
    )
    return len({first.thesis_id, second.thesis_id, third.thesis_id})


EDGE_CASE_SCENARIOS = (
    ("warm-up incomplete blocks a favourable decision", _warm_up_incomplete, "NO_CLAIM"),
    ("a missing required feature blocks a claim", _missing_required_feature, "NO_CLAIM"),
    ("an invalid market row is not repaired", _invalid_market_row, "INCOMPLETE_COVERAGE"),
    (
        "an unsupported instrument blocks the window",
        _unsupported_instrument,
        "UNSUPPORTED_INSTRUMENT",
    ),
    ("clock skew is recorded", _clock_skew_and_disagreement, "CLOCK_SKEW"),
    (
        "duplicate deliveries stay non-blocking",
        _duplicate_and_out_of_order,
        "COMPLETE",
    ),
    (
        "a trigger storm never starves protection",
        _trigger_storm_with_protection_due,
        ("ADMITTED", ["PROTECTION", "DISCOVERY"]),
    ),
    (
        "a conflicting detector state fails closed",
        _detector_state_conflict,
        "detector_contract_violation",
    ),
    (
        "a claim that expires before evaluation is expired",
        _claim_expiry_before_evaluation,
        1,
    ),
    ("re-observing a claim keeps one episode", _reobserved_claim_keeps_one_episode, True),
    (
        "horizon expiry is observed and calibration stays unknown",
        _horizon_expiry_and_missing_calibration,
        ("EXPIRED", None),
    ),
    ("no AI or notification path exists", _ai_and_notification_are_absent, True),
    ("thesis revisions are distinct records", _concurrent_thesis_revisions, 3),
)


@pytest.mark.parametrize(
    "description, scenario, expected",
    EDGE_CASE_SCENARIOS,
    ids=[item[0] for item in EDGE_CASE_SCENARIOS],
)
def test_ac_014_edge_case_matrix_fails_closed_as_documented(
    description, scenario, expected
):
    """ATDD-R5-A-continuous-opportunity-engine/AC-014: failure modes are tested.

    GIVEN one documented failure mode from the R5-A matrix
    WHEN the scenario runs against the engine, the coverage reconciler, the
    coordinator or the horizon contract
    THEN the deterministic outcome matches the documented expectation, so no
    failure mode silently produces favourable evidence
    """

    observed = scenario()
    assert observed == expected, f"{description}: {observed!r} != {expected!r}"



# ---------------------------------------------------------------------------
# AC-015 Scope conformance, conformance record and engineering load evidence
# ---------------------------------------------------------------------------


def test_ac_015_the_increment_scope_contract_is_self_consistent():
    """ATDD-R5-A-continuous-opportunity-engine/AC-015: contract matches its tests.

    GIVEN this increment's scope contract and the repository acceptance index
    WHEN the contract is parsed and checked
    THEN its increment identifier matches, its declared traceability equals the
    acceptance tests that cite it, every criterion has an implementation-map
    entry and the scope check reports no reason
    """

    assert CONTRACT.is_file()
    contract = parse_scope_contract(CONTRACT.read_text(encoding="utf-8"))
    assert contract.increment == INCREMENT
    assert set(contract.acceptance_criteria) == {
        f"AC-{index:03d}" for index in range(1, 16)
    }
    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.reasons == ()
    assert result.status == "PASS"
    for ac_id in contract.acceptance_criteria:
        assert contract.traceability.get(ac_id), ac_id
        assert contract.implementation_map.get(ac_id), ac_id
    increments = [item.increment for item in load_scope_contracts(
        APP_ROOT / "docs" / "atdd" / "scope-contracts"
    )]
    assert len(increments) == len(set(increments))


def test_ac_015_the_conformance_record_documents_traceability_and_blockers():
    """ATDD-R5-A-continuous-opportunity-engine/AC-015: evidence and blockers recorded.

    GIVEN the R5-A conformance record
    WHEN it is inspected
    THEN it names the increment, every component, the reuse verdicts, the open
    owner inputs, the feature-disabled mode, the unchanged production posture,
    the measured engineering load scenario and the unproven runtime effectiveness
    """

    assert CONFORMANCE.is_file()
    body = CONFORMANCE.read_text(encoding="utf-8")
    for phrase in (
        INCREMENT,
        "F1",
        "EYE",
        "F2",
        "SCHED",
        "F3",
        "F4",
        "HORIZON",
        "OQ-03",
        "OQ-04",
        "R5A_ENGINE_MODE",
        "off",
        "TARGET_PAPER",
        "EVIDENCE_SHADOW",
        "NOT_PROVEN",
        "Engineering load scenario",
        "SC-01",
        "SC-02",
        "SC-03",
    ):
        assert phrase in body, phrase


def test_ac_015_an_engineering_load_scenario_is_bounded_and_protection_first():
    """ATDD-R5-A-continuous-opportunity-engine/AC-015: labelled synthetic load.

    GIVEN a labelled engineering burst of discovery requests for many instruments
    with a protection request due at the same instant
    WHEN a bounded cycle is selected twice
    THEN the backlog never exceeds the declared capacity, the protection work is
    served first, the discovery population is shed visibly, the counts reconcile,
    the measured logical delay stays inside the labelled scenario budget and both
    runs are identical
    """

    instruments = tuple(f"IV:{index:03d}" for index in range(40))
    limits = coordinator_limits(
        queue_capacity=64,
        queue_age_limit_intervals=10,
        retry_ceiling=2,
        per_instrument_budget_per_cycle=2,
        protection_reserved_slots=2,
        reconciliation_reserved_slots=2,
    )

    def run() -> tuple[dict, float, int]:
        coordinator = UnifiedEvaluationCoordinator(limits=limits)
        for instrument in instruments:
            for slot in range(4):
                coordinator.submit(
                    request_for(
                        instrument=instrument,
                        cutoff=10 + slot,
                        at_minute=10 + slot,
                        expires=90,
                    ),
                    evaluation_time=at(10 + slot),
                )
        protection = request_for(
            instrument="IV:PROTECT",
            priority=WorkPriority.PROTECTION,
            path=EvaluationPath.RECOVERY_BACKFILL,
            at_minute=13,
            expires=90,
        )
        coordinator.submit(protection, evaluation_time=at(13))
        batch = coordinator.next_batch(evaluation_time=at(14), size=20)
        delays = sorted(
            (at(14) - item.requested_at_utc).total_seconds() / 60.0 for item in batch
        )
        p95 = delays[min(len(delays) - 1, int(0.95 * len(delays)))] if delays else 0.0
        return coordinator.telemetry().to_dict(), p95, len(batch)

    first_telemetry, first_p95, served = run()
    second_telemetry, second_p95, second_served = run()
    assert first_telemetry == second_telemetry
    assert first_p95 == second_p95
    assert served == second_served == 20
    assert first_telemetry["max_backlog"] == limits.queue_capacity
    assert first_telemetry["dropped_backpressure"] >= 96
    assert first_telemetry["preempted"] >= 1
    assert first_telemetry["protected_slots_used"] >= 1
    # Labelled engineering scenario budget: an admitted request is served within
    # four evaluation intervals of its cut-off under this burst.
    assert first_p95 <= 4.0
    protection_batch = UnifiedEvaluationCoordinator(limits=limits)
    for instrument in instruments[:3]:
        protection_batch.submit(
            request_for(instrument=instrument, at_minute=10, expires=90),
            evaluation_time=at(10),
        )
    protection_batch.submit(
        request_for(
            instrument="IV:PROTECT",
            priority=WorkPriority.PROTECTION,
            path=EvaluationPath.RECOVERY_BACKFILL,
            at_minute=10,
            expires=90,
        ),
        evaluation_time=at(10),
    )
    selected = protection_batch.next_batch(evaluation_time=at(10), size=2)
    assert selected[0].priority is WorkPriority.PROTECTION

