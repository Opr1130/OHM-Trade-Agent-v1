"""O'Pip Event Risk Shield observer — BUILD 3.2R + BUILD 3.3.

The observer is evidence/protection advisory only. It has no exchange write
path. It performs one manifest-bounded PIT event read, builds an in-cycle
index, evaluates deterministic event risk, persists replayable T0 evidence,
and governs exposure-level risk transitions. BUILD 3.3 produces notification
*candidates* only; production dispatch/integration remains a later step.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from app.opip.events.contract import (
    EventSeverity,
    EventType,
    MappingStatus,
    OPipEvent,
    require_utc,
    utc_iso,
)
from app.opip.events.identity import ASSET_IDENTITY_REGISTRY, normalize_identity_text
from app.opip.events.provider_health import (
    PROVIDER_HEALTH_FILE,
    ProviderHealthSnapshot,
    ProviderHealthState,
    ProviderHealthStore,
)
from app.opip.events.storage import EventStore
from app.opip.risk import relevance as relevance_module
from app.opip.risk.alert_state import (
    AlertStateManager,
    AlertTransitionDecision,
    aggregate_exposure_risk,
)
from app.opip.risk.attribution import T0Attribution
from app.opip.risk.config import EventRiskShieldConfig
from app.opip.risk.contract import (
    ExposureFamily,
    ExposureView,
    POLICY_VERSION,
    Relevance,
    RiskAssessment,
    RiskState,
    build_assessment_id,
    build_input_evidence_hash,
)
from app.opip.risk.exposure_matcher import (
    ExposureCollectionResult,
    VerifierFactory,
    collect_exposures_with_status,
)
from app.opip.risk.policy import PolicyInputs, PolicyOutcome, evaluate
from app.opip.risk.storage import RISK_DIR, RiskAssessmentStore, T0AttributionStore

logger = logging.getLogger(__name__)
SHIELD_UNAVAILABLE_MESSAGE = (
    "O'Pip Event Risk Shield unavailable; existing production protection unaffected"
)
ExposureProvider = Callable[[datetime], Sequence[ExposureView]]
HealthResolver = Callable[[str, datetime], ProviderHealthSnapshot | None]
PriceLookup = Callable[[str], float | None]


@dataclass(frozen=True)
class VisibleEventSelection:
    events: tuple[OPipEvent, ...]
    events_truncated: bool
    raw_rows_truncated: bool
    archive_segments_scanned: int
    archive_segments_truncated: bool
    coverage_complete: bool
    warnings: tuple[str, ...] = ()


@dataclass
class ShieldCycleResult:
    decision_at_utc: datetime
    available: bool = True
    coverage_complete: bool = True
    events_considered: int = 0
    events_truncated: bool = False
    raw_rows_truncated: bool = False
    archive_segments_scanned: int = 0
    archive_segments_truncated: bool = False
    exposures_considered: int = 0
    exposures_truncated: bool = False
    exposure_source_status: dict[str, str] = field(default_factory=dict)
    assessments_generated: int = 0
    assessments_stored: int = 0
    duplicates_suppressed: int = 0
    t0_records_written: int = 0
    actionable_states: int = 0
    notification_candidates: int = 0
    notifications_sent: int = 0  # BUILD 3.3 does not dispatch.
    error: str | None = None
    assessments: tuple[RiskAssessment, ...] = ()
    alert_decisions: tuple[AlertTransitionDecision, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_at_utc": utc_iso(self.decision_at_utc),
            "available": self.available,
            "coverage_complete": self.coverage_complete,
            "events_considered": self.events_considered,
            "events_truncated": self.events_truncated,
            "raw_rows_truncated": self.raw_rows_truncated,
            "archive_segments_scanned": self.archive_segments_scanned,
            "archive_segments_truncated": self.archive_segments_truncated,
            "exposures_considered": self.exposures_considered,
            "exposures_truncated": self.exposures_truncated,
            "exposure_source_status": dict(self.exposure_source_status),
            "assessments_generated": self.assessments_generated,
            "assessments_stored": self.assessments_stored,
            "duplicates_suppressed": self.duplicates_suppressed,
            "t0_records_written": self.t0_records_written,
            "actionable_states": self.actionable_states,
            "notification_candidates": self.notification_candidates,
            "notifications_sent": self.notifications_sent,
            "policy_version": POLICY_VERSION,
            "error": self.error,
            "warnings": list(self.warnings),
        }


@dataclass
class _EventIndex:
    by_canonical_asset: dict[str, list[OPipEvent]] = field(default_factory=lambda: defaultdict(list))
    scoped: list[OPipEvent] = field(default_factory=list)
    by_venue: dict[str, list[OPipEvent]] = field(default_factory=lambda: defaultdict(list))
    total: int = 0

    def candidates_for(self, exposure: ExposureView) -> list[OPipEvent]:
        seen: set[str] = set(); result: list[OPipEvent] = []
        buckets: Iterable[list[OPipEvent]] = (
            self.by_canonical_asset.get(normalize_identity_text(exposure.canonical_asset_id or ""), []),
            self.scoped,
            self.by_venue.get(normalize_identity_text(exposure.venue or ""), []),
        )
        for bucket in buckets:
            for event in bucket:
                if event.event_id not in seen:
                    seen.add(event.event_id); result.append(event)
        return result


@dataclass(frozen=True)
class _AssessmentContext:
    assessment: RiskAssessment
    exposure: ExposureView
    event: OPipEvent
    health_snapshot: ProviderHealthSnapshot | None


def _is_point_in_time_visible(event: OPipEvent, decision_at: datetime) -> bool:
    return bool(
        event.persisted_at_utc is not None
        and event.decision_visible_at_utc is not None
        and event.persisted_at_utc <= decision_at
        and event.decision_visible_at_utc <= decision_at
    )


def select_visible_events_with_coverage(
    *, event_store: EventStore, decision_at: datetime, config: EventRiskShieldConfig
) -> VisibleEventSelection:
    cutoff = require_utc(decision_at, field_name="decision_at")
    window_start = cutoff - timedelta(seconds=int(config.lookback_seconds))
    read = event_store.read_visible_window(
        start=window_start,
        through=cutoff,
        max_archive_segments=int(config.max_archive_segments),
        max_rows=int(config.max_raw_events),
    )
    latest_by_dedupe: dict[str, OPipEvent] = {}
    for event in read.events:
        if not _is_point_in_time_visible(event, cutoff):
            continue
        # Read order is chronological archive->HOT, so later visible revision
        # deterministically replaces an earlier revision for the same key.
        latest_by_dedupe[event.dedupe_key] = event
    windowed = [
        event for event in latest_by_dedupe.values()
        if event.decision_visible_at_utc is not None
        and event.decision_visible_at_utc >= window_start
        and (event.expires_at_utc is None or event.expires_at_utc >= cutoff)
    ]
    windowed.sort(
        key=lambda item: (
            item.decision_visible_at_utc,
            item.source_event_time_utc,
            item.provider,
            item.event_id,
        ),
        reverse=True,
    )
    truncated = len(windowed) > int(config.max_events)
    warnings = list(read.warnings)
    if truncated:
        warnings.append("EVENT_CEILING_REACHED")
    return VisibleEventSelection(
        events=tuple(windowed[: int(config.max_events)]),
        events_truncated=truncated,
        raw_rows_truncated=read.rows_truncated,
        archive_segments_scanned=read.archive_segments_scanned,
        archive_segments_truncated=read.archive_segments_truncated,
        coverage_complete=read.coverage_complete and not truncated,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def select_visible_events(
    *, event_store: EventStore, decision_at: datetime, config: EventRiskShieldConfig
) -> tuple[tuple[OPipEvent, ...], bool]:
    """Compatibility wrapper for BUILD 3.2 callers/tests."""
    selected = select_visible_events_with_coverage(
        event_store=event_store, decision_at=decision_at, config=config
    )
    return selected.events, selected.events_truncated


def build_event_index(events: Sequence[OPipEvent]) -> _EventIndex:
    index = _EventIndex(total=len(events))
    for event in events:
        if event.identity.mapping_status == MappingStatus.UNIQUE:
            key = normalize_identity_text(event.identity.canonical_asset_id or "")
            if key:
                index.by_canonical_asset[key].append(event)
        scope = relevance_module._declared_scope(event)
        if scope in {relevance_module.MARKET_WIDE_SCOPE, relevance_module.MACRO_SCOPE}:
            index.scoped.append(event)
        venue = normalize_identity_text(event.identity.venue or "")
        if venue:
            index.by_venue[venue].append(event)
    return index


def _default_health_resolver(store: ProviderHealthStore) -> HealthResolver:
    def resolve(provider: str, decision_at: datetime) -> ProviderHealthSnapshot | None:
        snapshot = store.read(provider, as_of=decision_at)
        if snapshot is None or snapshot.checked_at_utc > decision_at:
            return None
        return snapshot
    return resolve


def _event_age_seconds(event: OPipEvent, decision_at: datetime) -> float:
    return max(0.0, (decision_at - event.source_event_time_utc).total_seconds())


def _evidence_age_seconds(event: OPipEvent, decision_at: datetime) -> float | None:
    if event.decision_visible_at_utc is None:
        return None
    return max(0.0, (decision_at - event.decision_visible_at_utc).total_seconds())


def _ingestion_lag_seconds(event: OPipEvent) -> float | None:
    visible = event.decision_visible_at_utc
    if visible is None:
        return None
    return max(0.0, (visible - event.source_event_time_utc).total_seconds())


def _position_age_seconds(exposure: ExposureView, decision_at: datetime) -> float | None:
    if exposure.opened_at_utc is None:
        return None
    return max(0.0, (decision_at - exposure.opened_at_utc).total_seconds())


def assess_exposure_event(
    *, event: OPipEvent, exposure: ExposureView, decision_at: datetime,
    config: EventRiskShieldConfig, health_snapshot: ProviderHealthSnapshot | None,
    created_at: datetime,
) -> RiskAssessment:
    resolved_relevance = relevance_module.classify(event, exposure)
    event_age = _event_age_seconds(event, decision_at)
    evidence_age = _evidence_age_seconds(event, decision_at)
    lag = _ingestion_lag_seconds(event)
    health_state = health_snapshot.state if health_snapshot is not None else None
    inputs = PolicyInputs(
        exposure=exposure, relevance=resolved_relevance,
        event_type=event.event_type, event_severity=event.severity,
        freshness_seconds=event_age,
        provider_health_state=health_state,
        stale_event_seconds=int(config.stale_event_seconds),
    )
    outcome = evaluate(inputs)
    warnings = list(outcome.warnings)
    identity_warning = relevance_module.identity_warning(event)
    if identity_warning and resolved_relevance is Relevance.UNRELATED:
        warnings.append(identity_warning)
    evidence_hash = build_input_evidence_hash(
        exposure_snapshot=exposure.evidence_snapshot(),
        effective_event_id=event.event_id,
        event_severity=event.severity,
        event_type=event.event_type,
        relevance=resolved_relevance,
        stale_event=inputs.stale,
        provider_health_state=health_state.value if health_state else None,
        policy_version=outcome.policy_version,
    )
    return RiskAssessment(
        assessment_id=build_assessment_id(
            exposure_id=exposure.exposure_id,
            effective_event_id=event.event_id,
            input_evidence_hash=evidence_hash,
        ),
        event_id=event.revision_of or event.event_id,
        effective_event_id=event.event_id,
        decision_at_utc=decision_at,
        exposure_id=exposure.exposure_id,
        exposure_family=exposure.exposure_family,
        exposure_state=exposure.exposure_state,
        pending=exposure.pending,
        direction=exposure.direction,
        event_class=event.event_class,
        event_type=event.event_type,
        event_severity=event.severity,
        relevance=resolved_relevance,
        risk_state=outcome.risk_state,
        risk_score=outcome.risk_score,
        policy_version=outcome.policy_version,
        input_evidence_hash=evidence_hash,
        created_at_utc=created_at,
        canonical_asset_id=exposure.canonical_asset_id,
        event_revision_of=event.revision_of,
        freshness_seconds=event_age,
        event_age_seconds=event_age,
        evidence_age_seconds=evidence_age,
        ingestion_lag_seconds=lag,
        event_source_time_utc=event.source_event_time_utc,
        event_decision_visible_at_utc=event.decision_visible_at_utc,
        event_expires_at_utc=event.expires_at_utc,
        provider=event.provider,
        provider_health_state=health_state.value if health_state else None,
        evidence_confidence=outcome.evidence_confidence,
        reasons=outcome.reasons,
        deterministic_rules_triggered=outcome.rules_triggered,
        supporting_evidence=(
            f"event:{event.event_id}", f"provider:{event.provider}",
            f"exposure:{exposure.source_registry}",
        ),
        warnings=tuple(warnings),
    )


def _policy_snapshot(
    *, assessment: RiskAssessment, exposure: ExposureView, config: EventRiskShieldConfig
) -> dict[str, Any]:
    return {
        "exposure": exposure.evidence_snapshot(),
        "effective_event_id": assessment.effective_event_id,
        "event_type": assessment.event_type.value,
        "event_severity": assessment.event_severity.value,
        "relevance": assessment.relevance.value,
        "event_age_seconds": assessment.event_age_seconds,
        "stale_event_seconds": int(config.stale_event_seconds),
        "stale_event": (
            assessment.event_age_seconds is None
            or assessment.event_age_seconds > int(config.stale_event_seconds)
        ),
        "provider_health_state": assessment.provider_health_state,
        "policy_version": assessment.policy_version,
    }


def build_t0_record(
    *, assessment: RiskAssessment, exposure: ExposureView, event: OPipEvent,
    health_snapshot: ProviderHealthSnapshot | None, market_context: dict[str, Any] | None,
    current_price: float | None, decision_at: datetime, created_at: datetime,
    config: EventRiskShieldConfig,
    notification_decision: str = "NOT_EVALUATED",
    notification_status: str = "NONE",
) -> T0Attribution:
    return T0Attribution(
        attribution_id=assessment.assessment_id,
        assessment_id=assessment.assessment_id,
        decision_at_utc=decision_at,
        event_id=assessment.event_id,
        effective_event_id=assessment.effective_event_id,
        exposure_id=exposure.exposure_id,
        exposure_family=exposure.exposure_family,
        exposure_state=exposure.exposure_state,
        pending=exposure.pending,
        direction=exposure.direction,
        risk_state=assessment.risk_state,
        policy_version=assessment.policy_version,
        input_evidence_hash=assessment.input_evidence_hash,
        created_at_utc=created_at,
        exposure_snapshot=exposure.full_snapshot(),
        provider_health_snapshot=(health_snapshot.to_dict() if health_snapshot else {}),
        event_visibility={
            "source_event_time_utc": utc_iso(event.source_event_time_utc),
            "ingest_time_utc": utc_iso(event.ingest_time_utc),
            "normalized_at_utc": utc_iso(event.normalized_at_utc),
            "persisted_at_utc": utc_iso(event.persisted_at_utc),
            "decision_visible_at_utc": utc_iso(event.decision_visible_at_utc),
            "expires_at_utc": utc_iso(event.expires_at_utc),
            "severity": event.severity.value,
            "event_type": event.event_type.value,
            "revision_of": event.revision_of,
        },
        policy_input_snapshot=_policy_snapshot(
            assessment=assessment, exposure=exposure, config=config
        ),
        market_context=dict(market_context or {}),
        deterministic_rules_triggered=assessment.deterministic_rules_triggered,
        entry_price=exposure.entry_price,
        current_price_at_t0=current_price,
        position_age_seconds=_position_age_seconds(exposure, decision_at),
        event_revision_of=event.revision_of,
        notification_decision=notification_decision,
        notification_status=notification_status,
    )


def replay_policy_from_t0(record: T0Attribution) -> PolicyOutcome:
    """Replay deterministic policy using only immutable T0 evidence.

    No EventStore, provider-health file or mutable exposure registry is read.
    """
    snap = dict(record.policy_input_snapshot)
    if snap.get("legacy_unreplayable"):
        raise ValueError("T0 record predates replayable policy-input capture")
    exposure = ExposureView.from_snapshot(dict(record.exposure_snapshot))
    if str(snap.get("policy_version")) != record.policy_version:
        raise ValueError("T0 policy version mismatch")
    event_type = EventType(str(snap["event_type"]))
    severity = EventSeverity(str(snap["event_severity"]))
    relevance = Relevance(str(snap["relevance"]))
    health_raw = snap.get("provider_health_state")
    health = ProviderHealthState(str(health_raw)) if health_raw else None
    age = snap.get("event_age_seconds")
    inputs = PolicyInputs(
        exposure=exposure,
        relevance=relevance,
        event_type=event_type,
        event_severity=severity,
        freshness_seconds=float(age) if age is not None else None,
        provider_health_state=health,
        stale_event_seconds=int(snap["stale_event_seconds"]),
    )
    rebuilt_hash = build_input_evidence_hash(
        exposure_snapshot=exposure.evidence_snapshot(),
        effective_event_id=str(snap["effective_event_id"]),
        event_severity=severity,
        event_type=event_type,
        relevance=relevance,
        stale_event=inputs.stale,
        provider_health_state=health.value if health else None,
        policy_version=record.policy_version,
    )
    if rebuilt_hash != record.input_evidence_hash:
        raise ValueError("T0 deterministic input hash mismatch")
    return evaluate(inputs)


def _transition_t0_labels(
    decision: AlertTransitionDecision | None, *, selected: bool
) -> tuple[str, str]:
    if not selected or decision is None:
        return "NOT_SELECTED_EXPOSURE_AGGREGATE", "NOT_DISPATCHED_BUILD_3_3"
    if decision.should_notify:
        return "ELIGIBLE_BUILD_3_3", "NOT_DISPATCHED_BUILD_3_3"
    return f"SUPPRESSED:{decision.notification_reason}", "NOT_DISPATCHED_BUILD_3_3"


def run_event_risk_shield(
    *, decision_at: datetime, config: EventRiskShieldConfig | None = None,
    event_store: EventStore | None = None,
    exposure_provider: ExposureProvider | None = None,
    health_resolver: HealthResolver | None = None,
    price_lookup: PriceLookup | None = None,
    market_context: dict[str, Any] | None = None,
    storage_root: Path = RISK_DIR,
    exposure_coverage_complete: bool = True,
    exposure_source_status: dict[str, str] | None = None,
    exposure_warnings: Sequence[str] = (),
    persist: bool = True,
) -> ShieldCycleResult:
    """Run with explicit decision-time collaborators.

    This entry point never falls back to today's exposure/provider state. Live
    operation must use ``run_live_event_risk_shield``; historical replay uses
    ``replay_policy_from_t0``.
    """
    cutoff = require_utc(decision_at, field_name="decision_at")
    settings = config or EventRiskShieldConfig.from_env()
    result = ShieldCycleResult(decision_at_utc=cutoff)
    if not settings.enabled:
        result.available = False; result.error = "DISABLED_BY_CONFIGURATION"; return result
    if exposure_provider is None:
        raise ValueError("explicit exposure_provider required; use run_live_event_risk_shield for live state")
    if health_resolver is None:
        raise ValueError("explicit health_resolver required; use run_live_event_risk_shield for live state")

    selection = select_visible_events_with_coverage(
        event_store=event_store or EventStore(), decision_at=cutoff, config=settings
    )
    result.events_considered = len(selection.events)
    result.events_truncated = selection.events_truncated
    result.raw_rows_truncated = selection.raw_rows_truncated
    result.archive_segments_scanned = selection.archive_segments_scanned
    result.archive_segments_truncated = selection.archive_segments_truncated
    warnings = list(selection.warnings) + [str(x) for x in exposure_warnings]
    index = build_event_index(selection.events)

    exposures = tuple(exposure_provider(cutoff))
    if len(exposures) > int(settings.max_exposures):
        exposures = exposures[: int(settings.max_exposures)]
        result.exposures_truncated = True
        warnings.append("EXPOSURE_CEILING_REACHED")
    result.exposures_considered = len(exposures)
    result.exposure_source_status = dict(exposure_source_status or {"custom": "OK"})

    coverage_complete = (
        selection.coverage_complete
        and bool(exposure_coverage_complete)
        and not result.exposures_truncated
    )

    contexts: list[_AssessmentContext] = []
    ceiling_hit = False
    for exposure in exposures:
        for event in index.candidates_for(exposure):
            if len(contexts) >= int(settings.max_assessments_per_cycle):
                ceiling_hit = True; break
            health_snapshot = health_resolver(event.provider, cutoff)
            assessment = assess_exposure_event(
                event=event, exposure=exposure, decision_at=cutoff, config=settings,
                health_snapshot=health_snapshot, created_at=cutoff,
            )
            if assessment.risk_state is RiskState.NONE:
                continue
            contexts.append(_AssessmentContext(assessment, exposure, event, health_snapshot))
            if assessment.is_actionable_review:
                result.actionable_states += 1
        if ceiling_hit:
            break
    if ceiling_hit:
        coverage_complete = False; warnings.append("ASSESSMENT_CEILING_REACHED")

    generated = [ctx.assessment for ctx in contexts]
    result.assessments_generated = len(generated)
    result.assessments = tuple(generated)

    by_exposure: dict[tuple[ExposureFamily, str], list[RiskAssessment]] = defaultdict(list)
    context_by_assessment = {ctx.assessment.assessment_id: ctx for ctx in contexts}
    for assessment in generated:
        by_exposure[(assessment.exposure_family, assessment.exposure_id)].append(assessment)

    # BUILD 3.3 transition decisions are exposure-level, highest-current-state
    # aggregates. Missing/incomplete coverage can escalate observed risk but
    # can never justify de-escalation.
    decision_rows: list[tuple[ExposureView, Any, AlertTransitionDecision, AlertStateManager]] = []
    for exposure in exposures:
        aggregate = aggregate_exposure_risk(
            exposure=exposure,
            assessments=by_exposure.get((exposure.exposure_family, exposure.exposure_id), ()),
            decision_at=cutoff,
        )
        manager = AlertStateManager(family=exposure.exposure_family, root=storage_root)
        transition = manager.evaluate(
            exposure=exposure, aggregate=aggregate, decision_at=cutoff,
            coverage_complete=coverage_complete,
        )
        decision_rows.append((exposure, aggregate, transition, manager))
    result.alert_decisions = tuple(row[2] for row in decision_rows)
    result.notification_candidates = sum(1 for row in result.alert_decisions if row.should_notify)

    assessment_stores: dict[ExposureFamily, RiskAssessmentStore] = {}
    t0_stores: dict[ExposureFamily, T0AttributionStore] = {}
    def assessment_store(family: ExposureFamily) -> RiskAssessmentStore:
        assessment_stores.setdefault(family, RiskAssessmentStore(family=family, root=storage_root))
        return assessment_stores[family]
    def t0_store(family: ExposureFamily) -> T0AttributionStore:
        t0_stores.setdefault(family, T0AttributionStore(family=family, root=storage_root))
        return t0_stores[family]

    # Track whether the selected aggregate assessment has durable T0 evidence.
    selected_t0_ready: dict[tuple[ExposureFamily, str], bool] = {}
    transition_by_selected = {
        row[1].selected_assessment.assessment_id: row[2]
        for row in decision_rows if row[1].selected_assessment is not None
    }

    if persist:
        for ctx in contexts:
            assessment = ctx.assessment
            key = (assessment.exposure_family, assessment.exposure_id)
            try:
                appended = assessment_store(assessment.exposure_family).append(assessment)
                if appended.stored:
                    result.assessments_stored += 1
                else:
                    result.duplicates_suppressed += 1
            except Exception as exc:
                logger.exception("O'Pip Event Risk assessment persistence failed")
                warnings.append(f"ASSESSMENT_STORAGE_FAILURE:{type(exc).__name__}")
                selected_t0_ready[key] = False
                continue

            transition = transition_by_selected.get(assessment.assessment_id)
            selected = transition is not None
            nd, ns = _transition_t0_labels(transition, selected=selected)
            current_price: float | None = None
            if price_lookup is not None:
                try:
                    current_price = price_lookup(ctx.exposure.symbol)
                except Exception as exc:
                    warnings.append(f"T0_PRICE_LOOKUP_UNAVAILABLE:{type(exc).__name__}")
            record = build_t0_record(
                assessment=assessment, exposure=ctx.exposure, event=ctx.event,
                health_snapshot=ctx.health_snapshot, market_context=market_context,
                current_price=current_price, decision_at=cutoff, created_at=cutoff,
                config=settings, notification_decision=nd, notification_status=ns,
            )
            try:
                stored_t0 = t0_store(assessment.exposure_family).append(record)
                if stored_t0:
                    result.t0_records_written += 1
                if selected:
                    # True whether newly appended or already durable. This is
                    # the crash-healing path when assessment existed but T0 did not.
                    selected_t0_ready[key] = True
            except Exception as exc:
                logger.exception("O'Pip Event Risk T0 persistence failed")
                warnings.append(f"T0_STORAGE_FAILURE:{type(exc).__name__}")
                if selected:
                    selected_t0_ready[key] = False

        # Commit transition state only after selected T0 is durable. Resolution
        # to NONE has no selected assessment/T0 and can commit only with complete
        # coverage (already enforced by the decision's freeze logic).
        for exposure, aggregate, transition, manager in decision_rows:
            key = (exposure.exposure_family, exposure.exposure_id)
            if aggregate.selected_assessment is not None and not selected_t0_ready.get(key, False):
                warnings.append(f"ALERT_STATE_NOT_COMMITTED_T0_MISSING:{exposure.exposure_id}")
                continue
            try:
                if not manager.commit(decision=transition, aggregate=aggregate, decision_at=cutoff):
                    warnings.append(f"ALERT_STATE_CONCURRENT_CHANGE:{exposure.exposure_id}")
            except Exception as exc:
                logger.exception("O'Pip Event Risk alert-state persistence failed")
                warnings.append(f"ALERT_STATE_STORAGE_FAILURE:{type(exc).__name__}")

        # Current-state files are not historical stores. When the exposure
        # snapshot itself is complete, rows for definitively absent exposures
        # can be pruned without touching append-only assessment/T0 evidence.
        for family in ExposureFamily:
            ids = {e.exposure_id for e in exposures if e.exposure_family is family}
            try:
                AlertStateManager(family=family, root=storage_root).prune_absent(
                    active_exposure_ids=ids, coverage_complete=coverage_complete
                )
            except Exception as exc:
                logger.exception("O'Pip Event Risk alert-state pruning failed")
                warnings.append(f"ALERT_STATE_PRUNE_FAILURE:{type(exc).__name__}")

    result.coverage_complete = coverage_complete
    result.warnings = tuple(dict.fromkeys(warnings))
    result.notifications_sent = 0
    return result


def run_live_event_risk_shield(
    *, decision_at: datetime, config: EventRiskShieldConfig | None = None,
    event_store: EventStore | None = None, price_lookup: PriceLookup | None = None,
    market_context: dict[str, Any] | None = None, storage_root: Path = RISK_DIR,
    identity_registry: Path = ASSET_IDENTITY_REGISTRY,
    provider_health_file: Path = PROVIDER_HEALTH_FILE,
    verifier_factory: VerifierFactory | None = None,
    persist: bool = True,
) -> ShieldCycleResult:
    """Live-only wrapper using current registries and Kraken read-only verification."""
    cutoff = require_utc(decision_at, field_name="decision_at")
    settings = config or EventRiskShieldConfig.from_env()
    if not settings.enabled:
        return ShieldCycleResult(
            decision_at_utc=cutoff, available=False, error="DISABLED_BY_CONFIGURATION"
        )
    collection: ExposureCollectionResult = collect_exposures_with_status(
        decision_at=cutoff, identity_registry=identity_registry,
        include_paper=settings.include_paper, verify_active_real=True,
        verifier_factory=verifier_factory,
    )
    health = _default_health_resolver(ProviderHealthStore(provider_health_file))
    return run_event_risk_shield(
        decision_at=cutoff, config=settings, event_store=event_store,
        exposure_provider=lambda _at: collection.exposures,
        health_resolver=health, price_lookup=price_lookup,
        market_context=market_context, storage_root=storage_root,
        exposure_coverage_complete=collection.coverage_complete,
        exposure_source_status=collection.source_status,
        exposure_warnings=collection.warnings,
        persist=persist,
    )


def run_event_risk_shield_safe(**kwargs: Any) -> ShieldCycleResult:
    decision_at = kwargs.get("decision_at")
    try:
        return run_event_risk_shield(**kwargs)
    except Exception as exc:
        logger.exception(SHIELD_UNAVAILABLE_MESSAGE)
        print(f"{SHIELD_UNAVAILABLE_MESSAGE} Reason={type(exc).__name__}")
        fallback = decision_at if isinstance(decision_at, datetime) else None
        return ShieldCycleResult(
            decision_at_utc=fallback or datetime.now(timezone.utc), available=False,
            coverage_complete=False, error=f"{type(exc).__name__}: {exc}"[:300],
        )


def run_live_event_risk_shield_safe(**kwargs: Any) -> ShieldCycleResult:
    decision_at = kwargs.get("decision_at")
    try:
        return run_live_event_risk_shield(**kwargs)
    except Exception as exc:
        logger.exception(SHIELD_UNAVAILABLE_MESSAGE)
        print(f"{SHIELD_UNAVAILABLE_MESSAGE} Reason={type(exc).__name__}")
        fallback = decision_at if isinstance(decision_at, datetime) else None
        return ShieldCycleResult(
            decision_at_utc=fallback or datetime.now(timezone.utc), available=False,
            coverage_complete=False, error=f"{type(exc).__name__}: {exc}"[:300],
        )
