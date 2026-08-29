"""BUILD 3.1 — canonical risk contract, relevance, exposure matcher, policy.

These tests pin the deterministic safety behaviour of the O'Pip Event Risk
Shield. They must never be relaxed to make a later build pass.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.opip.events.contract import (
    EventClass,
    EventIdentity,
    EventProvenance,
    EventSeverity,
    EventType,
    MappingStatus,
    OPipEvent,
    stable_event_id,
    stable_payload_hash,
)
from app.opip.events.provider_health import ProviderHealthState
from app.opip.risk import relevance as relevance_module
from app.opip.risk.contract import (
    Direction,
    EvidenceConfidence,
    ExposureFamily,
    ExposureState,
    ExposureView,
    POLICY_VERSION,
    Relevance,
    RiskAssessment,
    RiskState,
    build_assessment_id,
    build_input_evidence_hash,
    risk_state_rank,
)
from app.opip.risk.policy import (
    DEFAULT_STALE_EVENT_SECONDS,
    PolicyInputs,
    directional_polarity,
    evaluate,
)


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _identity(
    *,
    status: MappingStatus = MappingStatus.UNIQUE,
    canonical_id: str = "solana",
    symbol: str = "SOL",
    venue: str | None = None,
) -> EventIdentity:
    if status == MappingStatus.UNIQUE:
        return EventIdentity(
            source_symbol=symbol,
            source_name="Solana",
            canonical_asset_id=canonical_id,
            canonical_asset_name="Solana",
            mapping_status=status,
            mapping_confidence=1.0,
            identity_learned_at_utc=NOW - timedelta(days=30),
            venue=venue,
        )
    return EventIdentity(
        source_symbol=symbol,
        source_name="Solana",
        mapping_status=status,
        venue=venue,
    )


def _event(
    *,
    identity: EventIdentity | None = None,
    event_type: EventType = EventType.NEWS_SECURITY,
    severity: EventSeverity = EventSeverity.CRITICAL,
    ingest: datetime = NOW,
    key: str = "evt-1",
    metadata: dict | None = None,
) -> OPipEvent:
    payload_hash = stable_payload_hash({"key": key, "type": event_type.value})
    dedupe = f"TEST:{key}"
    return OPipEvent(
        event_id=stable_event_id(dedupe, payload_hash),
        dedupe_key=dedupe,
        provider="TEST",
        provider_event_id=key,
        event_class=EventClass.NEWS,
        payload_hash=payload_hash,
        source_event_time_utc=ingest - timedelta(minutes=5),
        ingest_time_utc=ingest,
        normalized_at_utc=ingest + timedelta(seconds=1),
        identity=identity if identity is not None else _identity(),
        headline="test headline",
        event_type=event_type,
        severity=severity,
        source_metadata=dict(metadata or {}),
        provenance=EventProvenance(
            provider="TEST",
            provider_event_id=key,
            provider_asset_id=None,
            source_reference="test",
            source_sequence=None,
            canonical_payload_hash=payload_hash,
            source_payload_hash=payload_hash,
        ),
    )


def _exposure(
    *,
    family: ExposureFamily = ExposureFamily.REAL_ADVISORY,
    state: ExposureState = ExposureState.ACTIVE,
    direction: Direction = Direction.LONG,
    canonical_id: str | None = "solana",
    venue: str | None = None,
    exposure_id: str = "OHM-SOL-1",
) -> ExposureView:
    return ExposureView(
        exposure_id=exposure_id,
        exposure_family=family,
        exposure_state=state,
        source_registry="test",
        symbol="SOLUSD",
        base_asset="SOL",
        direction=direction,
        status="active",
        snapshot_at_utc=NOW,
        canonical_asset_id=canonical_id,
        canonical_asset_name="Solana" if canonical_id else None,
        identity_status="UNIQUE" if canonical_id else "UNKNOWN",
        venue=venue,
        entry_price=100.0,
    )


def _inputs(
    *,
    event: OPipEvent,
    exposure: ExposureView,
    relevance: Relevance = Relevance.DIRECT_ASSET,
    freshness_seconds: float | None = 60.0,
    health: ProviderHealthState | None = ProviderHealthState.HEALTHY,
) -> PolicyInputs:
    return PolicyInputs(
        exposure=exposure,
        relevance=relevance,
        event_type=event.event_type,
        event_severity=event.severity,
        freshness_seconds=freshness_seconds,
        provider_health_state=health,
    )


# ---------------------------------------------------------------- relevance


def test_unique_direct_asset_event_matches_exposure():
    event = _event()
    assert relevance_module.classify(event, _exposure()) is Relevance.DIRECT_ASSET


def test_unknown_identity_cannot_attach_to_asset_specific_exposure():
    event = _event(identity=_identity(status=MappingStatus.UNKNOWN))
    assert relevance_module.classify(event, _exposure()) is Relevance.UNRELATED
    assert relevance_module.identity_warning(event) == "IDENTITY_UNKNOWN_NOT_ATTACHED"


def test_ambiguous_identity_cannot_attach_to_asset_specific_exposure():
    event = _event(identity=_identity(status=MappingStatus.AMBIGUOUS))
    assert relevance_module.classify(event, _exposure()) is Relevance.UNRELATED
    assert relevance_module.identity_warning(event) == "IDENTITY_AMBIGUOUS_NOT_ATTACHED"


def test_matching_ticker_text_alone_never_attaches():
    """A non-UNIQUE event whose ticker equals the exposure must not attach."""
    event = _event(identity=_identity(status=MappingStatus.UNKNOWN, symbol="SOL"))
    assert relevance_module.matches_exposure_asset(event, _exposure()) is False


def test_exposure_without_canonical_identity_cannot_attach():
    event = _event()
    assert (
        relevance_module.classify(event, _exposure(canonical_id=None))
        is Relevance.UNRELATED
    )


def test_market_wide_scope_requires_explicit_declaration():
    undeclared = _event(identity=_identity(status=MappingStatus.UNKNOWN))
    assert relevance_module.classify(undeclared, _exposure()) is Relevance.UNRELATED

    declared = _event(
        identity=_identity(status=MappingStatus.UNKNOWN),
        metadata={"opip_market_scope": "MARKET_WIDE"},
    )
    assert relevance_module.classify(declared, _exposure()) is Relevance.MARKET_WIDE


def test_direct_asset_outranks_declared_market_scope():
    event = _event(metadata={"opip_market_scope": "MARKET_WIDE"})
    assert relevance_module.classify(event, _exposure()) is Relevance.DIRECT_ASSET


def test_venue_relevance_requires_matching_venue():
    event = _event(
        identity=_identity(status=MappingStatus.UNKNOWN, venue="KRAKEN"),
    )
    assert (
        relevance_module.classify(event, _exposure(venue="KRAKEN")) is Relevance.VENUE
    )
    assert (
        relevance_module.classify(event, _exposure(venue="BINANCE"))
        is Relevance.UNRELATED
    )


# ------------------------------------------------------------------- policy


def test_direct_long_critical_security_event_triggers_exit_review():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.CRITICAL)
    outcome = evaluate(_inputs(event=event, exposure=_exposure()))
    assert outcome.risk_state is RiskState.EXIT_REVIEW
    assert "R100_CRITICAL_SECURITY_DIRECT_ADVERSE" in outcome.rules_triggered
    assert outcome.policy_version == POLICY_VERSION


def test_direct_long_high_security_event_triggers_protect_review():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.HIGH)
    outcome = evaluate(_inputs(event=event, exposure=_exposure()))
    assert outcome.risk_state is RiskState.PROTECT_REVIEW


def test_pending_entry_with_adverse_event_avoids_new_entry_not_exit():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.CRITICAL)
    exposure = _exposure(state=ExposureState.PENDING)
    outcome = evaluate(_inputs(event=event, exposure=exposure))
    assert outcome.risk_state is RiskState.AVOID_NEW_ENTRY
    assert "R110_PENDING_ADVERSE_DIRECT_EVENT" in outcome.rules_triggered


def test_regulatory_delisting_risk_on_long_elevates_protection():
    event = _event(event_type=EventType.NEWS_REGULATORY, severity=EventSeverity.HIGH)
    outcome = evaluate(_inputs(event=event, exposure=_exposure()))
    assert risk_state_rank(outcome.risk_state) >= risk_state_rank(
        RiskState.PROTECT_REVIEW
    )


def test_positive_listing_event_against_short_exposure_protects():
    event = _event(event_type=EventType.LISTING, severity=EventSeverity.HIGH)
    exposure = _exposure(direction=Direction.SHORT)
    outcome = evaluate(_inputs(event=event, exposure=exposure))
    assert outcome.risk_state is RiskState.PROTECT_REVIEW


def test_long_short_asymmetry_for_the_same_event():
    """The same event must not produce the same state for both directions."""
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.CRITICAL)
    long_outcome = evaluate(
        _inputs(event=event, exposure=_exposure(direction=Direction.LONG))
    )
    short_outcome = evaluate(
        _inputs(event=event, exposure=_exposure(direction=Direction.SHORT))
    )
    assert long_outcome.risk_state is RiskState.EXIT_REVIEW
    assert short_outcome.risk_state is not RiskState.EXIT_REVIEW
    assert risk_state_rank(short_outcome.risk_state) < risk_state_rank(
        long_outcome.risk_state
    )


def test_positive_catalyst_on_long_is_not_an_exit_signal():
    event = _event(event_type=EventType.LISTING, severity=EventSeverity.HIGH)
    outcome = evaluate(_inputs(event=event, exposure=_exposure()))
    assert outcome.risk_state is RiskState.NONE


def test_supportive_critical_event_still_flags_volatility_watch():
    event = _event(event_type=EventType.LISTING, severity=EventSeverity.CRITICAL)
    outcome = evaluate(_inputs(event=event, exposure=_exposure()))
    assert outcome.risk_state is RiskState.WATCH


def test_directional_polarity_inverts_with_direction():
    assert (
        directional_polarity(EventType.NEWS_SECURITY, Direction.LONG) == "ADVERSE"
    )
    assert (
        directional_polarity(EventType.NEWS_SECURITY, Direction.SHORT) == "SUPPORTIVE"
    )
    assert directional_polarity(EventType.LISTING, Direction.SHORT) == "ADVERSE"


def test_unrelated_event_produces_no_action():
    event = _event()
    outcome = evaluate(
        _inputs(event=event, exposure=_exposure(), relevance=Relevance.UNRELATED)
    )
    assert outcome.risk_state is RiskState.NONE
    assert "R000_UNRELATED_EVENT" in outcome.rules_triggered


def test_stale_event_cannot_escalate_above_watch():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.CRITICAL)
    outcome = evaluate(
        _inputs(
            event=event,
            exposure=_exposure(),
            freshness_seconds=DEFAULT_STALE_EVENT_SECONDS + 1,
        )
    )
    assert outcome.risk_state is RiskState.WATCH
    assert "R010_STALE_EVENT_ESCALATION_CAPPED" in outcome.rules_triggered


def test_unknown_event_age_is_treated_as_stale_not_as_safe():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.CRITICAL)
    outcome = evaluate(
        _inputs(event=event, exposure=_exposure(), freshness_seconds=None)
    )
    assert outcome.risk_state is RiskState.WATCH


def test_provider_unavailable_does_not_invent_a_clean_bill_of_health():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.CRITICAL)
    outcome = evaluate(
        _inputs(
            event=event,
            exposure=_exposure(),
            health=ProviderHealthState.UNAVAILABLE,
        )
    )
    assert outcome.risk_state is RiskState.EXIT_REVIEW
    assert outcome.evidence_confidence is EvidenceConfidence.UNAVAILABLE
    assert any("UNAVAILABLE" in warning for warning in outcome.warnings)


def test_provider_rate_limited_degrades_confidence_without_lowering_state():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.CRITICAL)
    healthy = evaluate(_inputs(event=event, exposure=_exposure()))
    limited = evaluate(
        _inputs(
            event=event,
            exposure=_exposure(),
            health=ProviderHealthState.RATE_LIMITED,
        )
    )
    assert limited.risk_state is healthy.risk_state
    assert limited.evidence_confidence is EvidenceConfidence.DEGRADED


def test_no_event_provider_state_is_not_a_failure():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.HIGH)
    outcome = evaluate(
        _inputs(event=event, exposure=_exposure(), health=ProviderHealthState.NO_EVENT)
    )
    assert outcome.evidence_confidence is EvidenceConfidence.NORMAL
    assert outcome.warnings == ()


def test_provider_health_can_never_lower_a_deterministic_state():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.CRITICAL)
    baseline = evaluate(_inputs(event=event, exposure=_exposure()))
    for state in ProviderHealthState:
        outcome = evaluate(
            _inputs(event=event, exposure=_exposure(), health=state)
        )
        assert risk_state_rank(outcome.risk_state) >= risk_state_rank(
            baseline.risk_state
        )


def test_market_wide_event_applies_by_explicit_rule_only():
    event = _event(
        identity=_identity(status=MappingStatus.UNKNOWN),
        event_type=EventType.NEWS_REGULATORY,
        severity=EventSeverity.HIGH,
        metadata={"opip_market_scope": "MARKET_WIDE"},
    )
    open_outcome = evaluate(
        _inputs(event=event, exposure=_exposure(), relevance=Relevance.MARKET_WIDE)
    )
    pending_outcome = evaluate(
        _inputs(
            event=event,
            exposure=_exposure(state=ExposureState.PENDING),
            relevance=Relevance.MARKET_WIDE,
        )
    )
    assert open_outcome.risk_state is RiskState.WATCH
    assert pending_outcome.risk_state is RiskState.AVOID_NEW_ENTRY


def test_low_severity_direct_event_does_not_create_advisory_noise():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.LOW)
    outcome = evaluate(_inputs(event=event, exposure=_exposure()))
    assert outcome.risk_state is RiskState.NONE


def test_paper_exposure_uses_the_same_deterministic_policy():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.CRITICAL)
    real = evaluate(_inputs(event=event, exposure=_exposure()))
    paper = evaluate(
        _inputs(
            event=event,
            exposure=_exposure(family=ExposureFamily.PAPER),
        )
    )
    assert paper.risk_state is real.risk_state


def test_policy_is_deterministic_across_repeated_evaluation():
    event = _event(event_type=EventType.NEWS_SECURITY, severity=EventSeverity.HIGH)
    first = evaluate(_inputs(event=event, exposure=_exposure()))
    second = evaluate(_inputs(event=event, exposure=_exposure()))
    assert first == second


def test_policy_module_has_no_execution_or_model_imports():
    import app.opip.risk.policy as policy_module

    source = Path(policy_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "kraken_transport",
        "confirm_entry",
        "register_trade",
        "order_intent_registry",
        "openai",
        "ai_reviewer",
        "requests",
        "httpx",
    ):
        assert forbidden not in source


# ----------------------------------------------------------------- contract


def test_assessment_identity_is_stable_for_unchanged_inputs():
    exposure = _exposure()
    kwargs = dict(
        exposure_snapshot=exposure.evidence_snapshot(),
        effective_event_id="event-a",
        event_severity=EventSeverity.CRITICAL,
        event_type=EventType.NEWS_SECURITY,
        relevance=Relevance.DIRECT_ASSET,
        stale_event=False,
        provider_health_state="HEALTHY",
        policy_version=POLICY_VERSION,
    )
    first = build_input_evidence_hash(**kwargs)
    second = build_input_evidence_hash(**kwargs)
    assert first == second
    assert build_assessment_id(
        exposure_id=exposure.exposure_id,
        effective_event_id="event-a",
        input_evidence_hash=first,
    ) == build_assessment_id(
        exposure_id=exposure.exposure_id,
        effective_event_id="event-a",
        input_evidence_hash=second,
    )


def test_assessment_identity_changes_when_severity_changes():
    exposure = _exposure()
    base = dict(
        exposure_snapshot=exposure.evidence_snapshot(),
        effective_event_id="event-a",
        event_type=EventType.NEWS_SECURITY,
        relevance=Relevance.DIRECT_ASSET,
        stale_event=False,
        provider_health_state="HEALTHY",
        policy_version=POLICY_VERSION,
    )
    high = build_input_evidence_hash(event_severity=EventSeverity.HIGH, **base)
    critical = build_input_evidence_hash(
        event_severity=EventSeverity.CRITICAL, **base
    )
    assert high != critical


def test_evidence_snapshot_excludes_drifting_values():
    snapshot = _exposure().evidence_snapshot()
    assert "snapshot_at_utc" not in snapshot
    assert "freshness_seconds" not in snapshot


def test_risk_assessment_round_trips_through_dict():
    assessment = RiskAssessment(
        assessment_id="a" * 16,
        event_id="event-a",
        effective_event_id="event-a",
        decision_at_utc=NOW,
        exposure_id="OHM-SOL-1",
        exposure_family=ExposureFamily.REAL_ADVISORY,
        exposure_state=ExposureState.ACTIVE,
        pending=False,
        direction=Direction.LONG,
        event_class=EventClass.NEWS,
        event_type=EventType.NEWS_SECURITY,
        event_severity=EventSeverity.CRITICAL,
        relevance=Relevance.DIRECT_ASSET,
        risk_state=RiskState.EXIT_REVIEW,
        risk_score=1.0,
        policy_version=POLICY_VERSION,
        input_evidence_hash="b" * 16,
        created_at_utc=NOW,
        canonical_asset_id="solana",
        reasons=("reason",),
        deterministic_rules_triggered=("R100_CRITICAL_SECURITY_DIRECT_ADVERSE",),
    )
    restored = RiskAssessment.from_dict(assessment.to_dict())
    assert restored == assessment
    assert restored.is_actionable_review is True


def test_risk_assessment_rejects_naive_timestamps():
    with pytest.raises(ValueError):
        RiskAssessment(
            assessment_id="a",
            event_id="e",
            effective_event_id="e",
            decision_at_utc=datetime(2026, 8, 20, 12, 0),
            exposure_id="x",
            exposure_family=ExposureFamily.REAL_ADVISORY,
            exposure_state=ExposureState.ACTIVE,
            pending=False,
            direction=Direction.LONG,
            event_class=EventClass.NEWS,
            event_type=EventType.NEWS_GENERAL,
            event_severity=EventSeverity.INFO,
            relevance=Relevance.DIRECT_ASSET,
            risk_state=RiskState.NONE,
            risk_score=0.0,
            policy_version=POLICY_VERSION,
            input_evidence_hash="h",
            created_at_utc=NOW,
        )


def test_risk_state_ordering_is_monotonic():
    assert (
        risk_state_rank(RiskState.NONE)
        < risk_state_rank(RiskState.WATCH)
        < risk_state_rank(RiskState.AVOID_NEW_ENTRY)
        < risk_state_rank(RiskState.PROTECT_REVIEW)
        < risk_state_rank(RiskState.EXIT_REVIEW)
    )


# --------------------------------------------------------- exposure matcher


def test_exposure_families_and_states_are_not_collapsed(monkeypatch, tmp_path):
    from app.opip.risk import exposure_matcher
    from app.services.active_trade_registry import ActiveTrade
    from app.services.pending_setup_registry import PendingSetup

    monkeypatch.setattr(
        exposure_matcher,
        "active_real_exposures",
        lambda **kwargs: (
            _exposure(),
        ),
    )
    monkeypatch.setattr(
        exposure_matcher,
        "pending_setup_exposures",
        lambda **kwargs: (
            _exposure(state=ExposureState.PENDING),
        ),
    )
    monkeypatch.setattr(
        exposure_matcher,
        "paper_exposures",
        lambda **kwargs: (_exposure(family=ExposureFamily.PAPER),),
    )

    exposures = exposure_matcher.collect_exposures(decision_at=NOW)
    kinds = {(item.exposure_family, item.exposure_state) for item in exposures}
    assert kinds == {
        (ExposureFamily.REAL_ADVISORY, ExposureState.ACTIVE),
        (ExposureFamily.REAL_ADVISORY, ExposureState.PENDING),
        (ExposureFamily.PAPER, ExposureState.ACTIVE),
    }
    assert ActiveTrade is not None and PendingSetup is not None


def test_one_unreadable_exposure_source_does_not_blind_the_others(monkeypatch):
    from app.opip.risk import exposure_matcher

    def _explode(**kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(exposure_matcher, "active_real_exposures", _explode)
    monkeypatch.setattr(
        exposure_matcher,
        "pending_setup_exposures",
        lambda **kwargs: (
            _exposure(state=ExposureState.PENDING),
        ),
    )
    monkeypatch.setattr(exposure_matcher, "paper_exposures", lambda **kwargs: ())

    exposures = exposure_matcher.collect_exposures(decision_at=NOW)
    assert len(exposures) == 1
    assert exposures[0].exposure_state is ExposureState.PENDING
    assert exposures[0].exposure_family is ExposureFamily.REAL_ADVISORY


def test_base_asset_extraction_handles_quotes_and_aliases():
    from app.opip.risk.exposure_matcher import base_asset_of

    assert base_asset_of("SOLUSD") == "SOL"
    assert base_asset_of("SOLUSDT") == "SOL"


def test_identity_learned_after_decision_time_cannot_resolve_exposure(tmp_path):
    from app.opip.risk.exposure_matcher import _resolve_canonical_identity

    registry = tmp_path / "asset_identity_registry.json"
    registry.write_text(
        '{"assets": {"SOL": {"source_id": "solana", "display_name": "Solana", '
        '"learned_at_utc": "2026-08-25T00:00:00+00:00"}}}',
        encoding="utf-8",
    )
    canonical_id, _, status = _resolve_canonical_identity(
        "SOL",
        decision_at=NOW,
        identity_registry=registry,
    )
    assert canonical_id is None
    assert status == MappingStatus.UNKNOWN.value


def test_exposure_matcher_has_no_write_or_execution_authority():
    from app.opip.risk import exposure_matcher

    source = Path(exposure_matcher.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "add_trade",
        "close_trade",
        "confirm_entry",
        "terminalize_pending_setup",
        "mark_pending_setup_entered",
        "save_json_atomic",
        "kraken_transport",
    ):
        assert forbidden not in source
