"""Sequence 3 foundation — PIT assessment, T0 evidence and alert governance."""

from __future__ import annotations

import ast
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
from app.opip.events.provider_health import (
    ProviderHealthSnapshot,
    ProviderHealthState,
)
from app.opip.events.storage import EventStore
from app.opip.risk import observer as observer_module
from app.opip.risk.attribution import T0Attribution
from app.opip.risk.config import EventRiskShieldConfig
from app.opip.risk.contract import (
    Direction,
    ExposureFamily,
    ExposureState,
    ExposureView,
    Relevance,
    RiskState,
)
from app.opip.risk.observer import (
    build_event_index,
    run_event_risk_shield,
    run_event_risk_shield_safe,
    select_visible_events,
)
from app.opip.risk.storage import RiskAssessmentStore, T0AttributionStore


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
RISK_PACKAGE = Path("app/opip/risk")


def _identity(
    *,
    status: MappingStatus = MappingStatus.UNIQUE,
    canonical_id: str = "solana",
    learned_at: datetime | None = None,
) -> EventIdentity:
    if status == MappingStatus.UNIQUE:
        return EventIdentity(
            source_symbol="SOL",
            source_name="Solana",
            canonical_asset_id=canonical_id,
            canonical_asset_name="Solana",
            mapping_status=status,
            mapping_confidence=1.0,
            identity_learned_at_utc=learned_at or (NOW - timedelta(days=30)),
        )
    return EventIdentity(
        source_symbol="SOL",
        source_name="Solana",
        mapping_status=status,
    )


def _event(
    *,
    key: str = "e1",
    identity: EventIdentity | None = None,
    event_type: EventType = EventType.NEWS_SECURITY,
    severity: EventSeverity = EventSeverity.CRITICAL,
    ingest: datetime | None = None,
    metadata: dict | None = None,
    dedupe: str | None = None,
) -> OPipEvent:
    ingest_at = ingest or (NOW - timedelta(minutes=10))
    payload_hash = stable_payload_hash({"k": key, "s": severity.value})
    dedupe_key = dedupe or f"TEST:{key}"
    return OPipEvent(
        event_id=stable_event_id(dedupe_key, payload_hash),
        dedupe_key=dedupe_key,
        provider="TEST",
        provider_event_id=key,
        event_class=EventClass.NEWS,
        payload_hash=payload_hash,
        source_event_time_utc=ingest_at - timedelta(minutes=1),
        ingest_time_utc=ingest_at,
        normalized_at_utc=ingest_at + timedelta(seconds=1),
        identity=identity if identity is not None else _identity(),
        headline="headline",
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
    exposure_id: str = "OHM-SOL-1",
    family: ExposureFamily = ExposureFamily.REAL_ADVISORY,
    state: ExposureState = ExposureState.ACTIVE,
    direction: Direction = Direction.LONG,
    canonical_id: str | None = "solana",
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
        entry_price=100.0,
        opened_at_utc=NOW - timedelta(hours=3),
    )


def _store(tmp_path: Path) -> EventStore:
    root = tmp_path / "events"
    return EventStore(
        event_file=root / "events.jsonl",
        archive_dir=root / "archive",
        dead_letter_file=root / "dead.jsonl",
        lock_file=root / ".events.lock",
        max_bytes=1024 * 1024,
        keep_lines=1000,
    )


def _healthy(provider: str = "TEST") -> ProviderHealthSnapshot:
    return ProviderHealthSnapshot(
        provider=provider,
        state=ProviderHealthState.HEALTHY,
        checked_at_utc=NOW - timedelta(minutes=1),
        configured=True,
        expected_interval_seconds=600,
        last_success_at_utc=NOW - timedelta(minutes=1),
    )


def _run(tmp_path, *, events, exposures, **kwargs):
    store = _store(tmp_path)
    for event, persisted_at in events:
        store.append(event, persisted_at=persisted_at)
    params = dict(
        decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True),
        event_store=store,
        exposure_provider=lambda _at: exposures,
        health_resolver=lambda provider, at: _healthy(provider),
        storage_root=tmp_path / "risk",
    )
    params.update(kwargs)
    return run_event_risk_shield(**params), store


# ------------------------------------------------------- PIT event selection


def test_event_persisted_after_decision_at_is_excluded(tmp_path):
    store = _store(tmp_path)
    store.append(_event(key="late"), persisted_at=NOW + timedelta(minutes=5))
    selected, _ = select_visible_events(
        event_store=store,
        decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True),
    )
    assert selected == ()


def test_event_with_earlier_source_time_but_later_ingest_is_excluded(tmp_path):
    store = _store(tmp_path)
    old_source = _event(key="slow", ingest=NOW + timedelta(minutes=1))
    store.append(old_source, persisted_at=NOW + timedelta(minutes=2))
    selected, _ = select_visible_events(
        event_store=store,
        decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True),
    )
    assert selected == ()


def test_future_revision_does_not_leak_into_historical_decision(tmp_path):
    store = _store(tmp_path)
    first = _event(key="v1", severity=EventSeverity.LOW, dedupe="TEST:same")
    store.append(first, persisted_at=NOW - timedelta(minutes=5))
    later = _event(
        key="v2",
        severity=EventSeverity.CRITICAL,
        dedupe="TEST:same",
        ingest=NOW + timedelta(minutes=4),
    )
    store.append(later, persisted_at=NOW + timedelta(minutes=5))

    selected, _ = select_visible_events(
        event_store=store,
        decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True),
    )
    assert [event.severity for event in selected] == [EventSeverity.LOW]


def test_lookback_window_is_configurable_and_bounds_selection(tmp_path):
    store = _store(tmp_path)
    old_ingest = NOW - timedelta(hours=20)
    store.append(
        _event(key="old", ingest=old_ingest),
        persisted_at=old_ingest + timedelta(seconds=2),
    )
    recent_ingest = NOW - timedelta(minutes=30)
    store.append(
        _event(key="recent", ingest=recent_ingest),
        persisted_at=recent_ingest + timedelta(seconds=2),
    )

    narrow, _ = select_visible_events(
        event_store=store,
        decision_at=NOW,
        config=EventRiskShieldConfig(lookback_seconds=6 * 3600),
    )
    wide, _ = select_visible_events(
        event_store=store,
        decision_at=NOW,
        config=EventRiskShieldConfig(lookback_seconds=24 * 3600),
    )
    assert len(narrow) == 1
    assert len(wide) == 2


def test_event_count_ceiling_truncates_deterministically(tmp_path):
    store = _store(tmp_path)
    for index in range(5):
        ingest_at = NOW - timedelta(minutes=index + 1)
        store.append(
            _event(key=f"e{index}", ingest=ingest_at),
            persisted_at=ingest_at + timedelta(seconds=2),
        )
    first, truncated_first = select_visible_events(
        event_store=store,
        decision_at=NOW,
        config=EventRiskShieldConfig(max_events=2),
    )
    second, truncated_second = select_visible_events(
        event_store=store,
        decision_at=NOW,
        config=EventRiskShieldConfig(max_events=2),
    )
    assert len(first) == 2
    assert truncated_first is True and truncated_second is True
    assert [item.event_id for item in first] == [item.event_id for item in second]


def test_expired_event_is_not_selected(tmp_path):
    store = _store(tmp_path)
    payload_hash = stable_payload_hash({"k": "exp"})
    event = OPipEvent(
        event_id=stable_event_id("TEST:exp", payload_hash),
        dedupe_key="TEST:exp",
        provider="TEST",
        provider_event_id="exp",
        event_class=EventClass.NEWS,
        payload_hash=payload_hash,
        source_event_time_utc=NOW - timedelta(hours=2),
        ingest_time_utc=NOW - timedelta(hours=2),
        normalized_at_utc=NOW - timedelta(hours=2),
        identity=_identity(),
        headline="expired",
        event_type=EventType.NEWS_SECURITY,
        severity=EventSeverity.CRITICAL,
        provenance=EventProvenance(
            provider="TEST",
            provider_event_id="exp",
            provider_asset_id=None,
            source_reference="test",
            source_sequence=None,
            canonical_payload_hash=payload_hash,
            source_payload_hash=payload_hash,
        ),
        expires_at_utc=NOW - timedelta(minutes=1),
    )
    store.append(event, persisted_at=NOW - timedelta(hours=2))
    selected, _ = select_visible_events(
        event_store=store,
        decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True),
    )
    assert selected == ()


def test_equal_timestamps_order_deterministically(tmp_path):
    store = _store(tmp_path)
    stamp = NOW - timedelta(minutes=5)
    for index in range(3):
        store.append(
            _event(key=f"tie{index}", ingest=stamp),
            persisted_at=stamp + timedelta(seconds=2),
        )
    runs = [
        [event.event_id for event in select_visible_events(
            event_store=store,
            decision_at=NOW,
            config=EventRiskShieldConfig(enabled=True),
        )[0]]
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2]


# ------------------------------------------------------------ identity guard


def test_unknown_identity_never_becomes_market_wide(tmp_path):
    result, _ = _run(
        tmp_path,
        events=[(_event(identity=_identity(status=MappingStatus.UNKNOWN)),
                 NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    assert result.assessments_generated == 0
    assert result.assessments_stored == 0


def test_event_with_no_instrument_does_not_become_market_wide(tmp_path):
    """A CryptoPanic-style item with no resolvable instrument stays inert."""
    bare = _event(identity=EventIdentity(mapping_status=MappingStatus.UNKNOWN))
    index = build_event_index([bare])
    assert index.scoped == []
    assert index.candidates_for(_exposure()) == []


def test_market_wide_requires_explicit_trusted_scope(tmp_path):
    declared = _event(
        identity=_identity(status=MappingStatus.UNKNOWN),
        event_type=EventType.NEWS_REGULATORY,
        severity=EventSeverity.HIGH,
        metadata={"opip_market_scope": "MARKET_WIDE"},
    )
    index = build_event_index([declared])
    assert index.scoped == [declared]
    assert index.candidates_for(_exposure()) == [declared]


def test_ambiguous_identity_cannot_attach_to_exposure(tmp_path):
    result, _ = _run(
        tmp_path,
        events=[(_event(identity=_identity(status=MappingStatus.AMBIGUOUS)),
                 NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    assert result.assessments_generated == 0


def test_known_canonical_asset_still_passes_structured_relevance(tmp_path):
    """A UNIQUE event on a different asset must not attach by familiarity."""
    other = _event(identity=_identity(canonical_id="ethereum"))
    index = build_event_index([other])
    assert index.candidates_for(_exposure(canonical_id="solana")) == []


# ------------------------------------------------------ assessment behaviour


def test_direct_critical_event_generates_and_persists_assessment(tmp_path):
    result, _ = _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    assert result.assessments_generated == 1
    assert result.assessments_stored == 1
    assert result.assessments[0].risk_state is RiskState.EXIT_REVIEW
    assert result.assessments[0].relevance is Relevance.DIRECT_ASSET
    assert 0.0 <= result.assessments[0].risk_score <= 1.0


def test_unchanged_cycle_never_stores_a_second_equivalent_assessment(tmp_path):
    store = _store(tmp_path)
    store.append(_event(), persisted_at=NOW - timedelta(minutes=5))
    params = dict(
        config=EventRiskShieldConfig(enabled=True),
        event_store=store,
        exposure_provider=lambda _at: (_exposure(),),
        health_resolver=lambda provider, at: _healthy(provider),
        storage_root=tmp_path / "risk",
    )
    first = run_event_risk_shield(decision_at=NOW, **params)
    second = run_event_risk_shield(decision_at=NOW + timedelta(minutes=5), **params)

    assert first.assessments_stored == 1
    assert second.assessments_stored == 0
    assert second.duplicates_suppressed == 1


def test_same_inputs_and_policy_version_reproduce_same_assessment(tmp_path):
    result_a, _ = _run(
        tmp_path / "a",
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    result_b, _ = _run(
        tmp_path / "b",
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    assert (
        result_a.assessments[0].input_evidence_hash
        == result_b.assessments[0].input_evidence_hash
    )
    assert (
        result_a.assessments[0].assessment_id == result_b.assessments[0].assessment_id
    )
    assert result_a.assessments[0].risk_state is result_b.assessments[0].risk_state


def test_revision_with_higher_severity_creates_a_new_assessment(tmp_path):
    store = _store(tmp_path)
    store.append(
        _event(
            key="v1",
            severity=EventSeverity.MEDIUM,
            dedupe="TEST:same",
            ingest=NOW - timedelta(minutes=10),
        ),
        persisted_at=NOW - timedelta(minutes=9, seconds=58),
    )
    params = dict(
        config=EventRiskShieldConfig(enabled=True),
        event_store=store,
        exposure_provider=lambda _at: (_exposure(),),
        health_resolver=lambda provider, at: _healthy(provider),
        storage_root=tmp_path / "risk",
    )
    first = run_event_risk_shield(decision_at=NOW, **params)
    store.append(
        _event(
            key="v2",
            severity=EventSeverity.CRITICAL,
            dedupe="TEST:same",
            ingest=NOW + timedelta(minutes=1),
        ),
        persisted_at=NOW + timedelta(minutes=1, seconds=2),
    )
    second = run_event_risk_shield(decision_at=NOW + timedelta(minutes=2), **params)

    assert first.assessments[0].risk_state is RiskState.WATCH
    assert second.assessments_stored == 1
    assert second.assessments[0].risk_state is RiskState.EXIT_REVIEW
    assert second.assessments[0].event_revision_of is not None


def test_none_state_is_not_persisted_as_noise(tmp_path):
    result, _ = _run(
        tmp_path,
        events=[(_event(event_type=EventType.LISTING, severity=EventSeverity.HIGH),
                 NOW - timedelta(minutes=5))],
        exposures=(_exposure(direction=Direction.LONG),),
    )
    assert result.assessments_generated == 0


def test_real_advisory_and_paper_pending_remain_isolated(tmp_path):
    result, _ = _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(
            _exposure(exposure_id="real-pending", state=ExposureState.PENDING),
            _exposure(
                exposure_id="paper-pending",
                family=ExposureFamily.PAPER,
                state=ExposureState.PENDING,
            ),
        ),
    )
    root = tmp_path / "risk"
    real = RiskAssessmentStore(family=ExposureFamily.REAL_ADVISORY, root=root)
    paper = RiskAssessmentStore(family=ExposureFamily.PAPER, root=root)

    real_ids = {item.exposure_id for item in real.iter_assessments()}
    paper_ids = {item.exposure_id for item in paper.iter_assessments()}
    assert real_ids == {"real-pending"}
    assert paper_ids == {"paper-pending"}
    assert real.data_file != paper.data_file
    assert result.assessments_stored == 2


def test_assessment_store_rejects_cross_family_writes(tmp_path):
    result, _ = _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    paper_store = RiskAssessmentStore(
        family=ExposureFamily.PAPER,
        root=tmp_path / "risk",
    )
    with pytest.raises(ValueError):
        paper_store.append(result.assessments[0])


def test_exposure_ceiling_is_enforced(tmp_path):
    exposures = tuple(
        _exposure(exposure_id=f"e{index}") for index in range(10)
    )
    result, _ = _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=exposures,
        config=EventRiskShieldConfig(max_exposures=3),
    )
    assert result.exposures_considered == 3
    assert result.exposures_truncated is True


# ------------------------------------------------------------ provider health


def test_future_provider_health_cannot_leak_backward(tmp_path, monkeypatch):
    from app.opip.events import provider_health as health_module

    future_snapshot = ProviderHealthSnapshot(
        provider="TEST",
        state=ProviderHealthState.HEALTHY,
        checked_at_utc=NOW + timedelta(hours=1),
        configured=True,
        expected_interval_seconds=600,
    )

    class _Store:
        def read(self, provider, *, as_of=None, stale_multiplier=3):
            return future_snapshot

    resolver = observer_module._default_health_resolver(_Store())
    assert resolver("TEST", NOW) is None
    assert health_module.ProviderHealthState.HEALTHY is ProviderHealthState.HEALTHY


def test_provider_health_recorded_but_never_lowers_state(tmp_path):
    degraded = ProviderHealthSnapshot(
        provider="TEST",
        state=ProviderHealthState.UNAVAILABLE,
        checked_at_utc=NOW - timedelta(minutes=1),
        configured=True,
        expected_interval_seconds=600,
    )
    result, _ = _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
        health_resolver=lambda provider, at: degraded,
    )
    assessment = result.assessments[0]
    assert assessment.risk_state is RiskState.EXIT_REVIEW
    assert assessment.provider_health_state == ProviderHealthState.UNAVAILABLE.value
    assert assessment.evidence_confidence.value == "UNAVAILABLE"


def test_event_staleness_and_provider_staleness_are_separate(tmp_path):
    """A stale event caps escalation; a stale provider does not."""
    stale_provider = ProviderHealthSnapshot(
        provider="TEST",
        state=ProviderHealthState.STALE,
        checked_at_utc=NOW - timedelta(minutes=1),
        configured=True,
        expected_interval_seconds=600,
    )
    fresh_event_stale_provider, _ = _run(
        tmp_path / "a",
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
        health_resolver=lambda provider, at: stale_provider,
    )
    old_ingest = NOW - timedelta(hours=30)
    stale_event_healthy_provider, _ = _run(
        tmp_path / "b",
        events=[(
            _event(ingest=old_ingest),
            old_ingest + timedelta(seconds=2),
        )],
        exposures=(_exposure(),),
        config=EventRiskShieldConfig(lookback_seconds=48 * 3600),
    )
    assert fresh_event_stale_provider.assessments[0].risk_state is RiskState.EXIT_REVIEW
    assert stale_event_healthy_provider.assessments[0].risk_state is RiskState.WATCH


# --------------------------------------------------------------- T0 records


def test_t0_record_contains_the_exposure_snapshot_used_by_the_policy(tmp_path):
    result, _ = _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    t0_store = T0AttributionStore(
        family=ExposureFamily.REAL_ADVISORY,
        root=tmp_path / "risk",
    )
    records = list(t0_store.iter_records())
    assert len(records) == 1
    record = records[0]
    assessment = result.assessments[0]
    assert record.assessment_id == assessment.assessment_id
    assert record.exposure_snapshot == {
        "exposure_id": "OHM-SOL-1",
        "exposure_family": "REAL_ADVISORY",
        "exposure_state": "ACTIVE",
        "direction": "LONG",
        "pending": False,
        "status": "active",
        "canonical_asset_id": "solana",
        "entry_price": 100.0,
    }
    assert record.input_evidence_hash == assessment.input_evidence_hash
    assert record.entry_price == 100.0
    assert record.position_age_seconds == 3 * 3600
    assert record.event_visibility["decision_visible_at_utc"] is not None
    assert record.provider_health_snapshot["state"] == "HEALTHY"


def test_t0_record_captures_market_context_without_new_provider_calls(tmp_path):
    context = {"funding_rate": 0.01, "open_interest": 1234.0, "regime": "RISK_OFF"}
    _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
        market_context=context,
        price_lookup=lambda symbol: 92.5,
    )
    record = list(
        T0AttributionStore(
            family=ExposureFamily.REAL_ADVISORY,
            root=tmp_path / "risk",
        ).iter_records()
    )[0]
    assert record.market_context == context
    assert record.current_price_at_t0 == 92.5


def test_t0_record_round_trips(tmp_path):
    _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    record = list(
        T0AttributionStore(
            family=ExposureFamily.REAL_ADVISORY,
            root=tmp_path / "risk",
        ).iter_records()
    )[0]
    assert T0Attribution.from_dict(record.to_dict()) == record


def test_t0_requires_an_exposure_snapshot():
    with pytest.raises(ValueError):
        T0Attribution(
            attribution_id="a",
            assessment_id="a",
            decision_at_utc=NOW,
            event_id="e",
            effective_event_id="e",
            exposure_id="x",
            exposure_family=ExposureFamily.REAL_ADVISORY,
            exposure_state=ExposureState.ACTIVE,
            pending=False,
            direction=Direction.LONG,
            risk_state=RiskState.WATCH,
            policy_version="v",
            input_evidence_hash="h",
            created_at_utc=NOW,
        )


def test_build_3_2_records_that_no_notification_was_evaluated(tmp_path):
    _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    record = list(
        T0AttributionStore(
            family=ExposureFamily.REAL_ADVISORY,
            root=tmp_path / "risk",
        ).iter_records()
    )[0]
    assert record.notification_decision in {"ELIGIBLE_BUILD_3_3", "SUPPRESSED:MEANINGFUL_RISK_ESCALATION", "SUPPRESSED:STATE_ESCALATION_RECORDED"}
    assert record.notification_status == "NOT_DISPATCHED_BUILD_3_3"


# ------------------------------------------------------------------- replay


def test_replay_uses_t0_snapshot_and_never_the_live_registry(tmp_path, monkeypatch):
    """Historical replay must not read today's mutable exposure registries."""
    _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    root = tmp_path / "risk"
    t0_store = T0AttributionStore(family=ExposureFamily.REAL_ADVISORY, root=root)
    historical = t0_store.records_at_or_before(through=NOW)
    assert len(historical) == 1

    from app.services import active_trade_registry, pending_setup_registry

    def _forbidden(*args, **kwargs):
        raise AssertionError("replay must not read live exposure registries")

    monkeypatch.setattr(active_trade_registry, "get_active_trades", _forbidden)
    monkeypatch.setattr(pending_setup_registry, "get_pending_setups", _forbidden)

    snapshot = historical[0].exposure_snapshot
    rebuilt = ExposureView(
        exposure_id=snapshot["exposure_id"],
        exposure_family=ExposureFamily(snapshot["exposure_family"]),
        exposure_state=ExposureState(snapshot["exposure_state"]),
        source_registry="t0_replay",
        symbol="SOLUSD",
        base_asset="SOL",
        direction=Direction(snapshot["direction"]),
        status=snapshot["status"],
        snapshot_at_utc=NOW,
        canonical_asset_id=snapshot["canonical_asset_id"],
        entry_price=snapshot["entry_price"],
    )
    store = _store(tmp_path / "replay_events")
    store.append(_event(), persisted_at=NOW - timedelta(minutes=5))
    replayed = run_event_risk_shield(
        decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True),
        event_store=store,
        exposure_provider=lambda _at: (rebuilt,),
        health_resolver=lambda provider, at: _healthy(provider),
        storage_root=tmp_path / "replay_risk",
        persist=False,
    )
    assert replayed.assessments[0].input_evidence_hash == (
        historical[0].input_evidence_hash
    )


def test_assessment_store_replay_is_bounded_by_decision_time(tmp_path):
    store = _store(tmp_path)
    store.append(_event(), persisted_at=NOW - timedelta(minutes=5))
    params = dict(
        config=EventRiskShieldConfig(enabled=True),
        event_store=store,
        exposure_provider=lambda _at: (_exposure(),),
        health_resolver=lambda provider, at: _healthy(provider),
        storage_root=tmp_path / "risk",
    )
    run_event_risk_shield(decision_at=NOW, **params)
    risk_store = RiskAssessmentStore(
        family=ExposureFamily.REAL_ADVISORY,
        root=tmp_path / "risk",
    )
    assert len(risk_store.replay(through=NOW)) == 1
    assert len(risk_store.replay(through=NOW - timedelta(hours=1))) == 0


def test_store_survives_restart_and_reloads_durable_state(tmp_path):
    store = _store(tmp_path)
    store.append(_event(), persisted_at=NOW - timedelta(minutes=5))
    params = dict(
        config=EventRiskShieldConfig(enabled=True),
        event_store=store,
        exposure_provider=lambda _at: (_exposure(),),
        health_resolver=lambda provider, at: _healthy(provider),
        storage_root=tmp_path / "risk",
    )
    run_event_risk_shield(decision_at=NOW, **params)

    # A fresh process sees the durable state and suppresses the duplicate.
    reopened = run_event_risk_shield(
        decision_at=NOW + timedelta(minutes=1),
        **params,
    )
    assert reopened.assessments_stored == 0
    assert reopened.duplicates_suppressed == 1

    fresh_store = RiskAssessmentStore(
        family=ExposureFamily.REAL_ADVISORY,
        root=tmp_path / "risk",
    )
    assert len(list(fresh_store.iter_assessments())) == 1
    assert set(fresh_store.latest_by_exposure()) == {"OHM-SOL-1"}


def test_truncated_assessment_tail_is_repaired_not_lost(tmp_path):
    risk_store = RiskAssessmentStore(
        family=ExposureFamily.REAL_ADVISORY,
        root=tmp_path / "risk",
    )
    result, _ = _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    data_file = risk_store.data_file
    raw = data_file.read_bytes()
    data_file.write_bytes(raw.rstrip(b"\n"))

    reopened = RiskAssessmentStore(
        family=ExposureFamily.REAL_ADVISORY,
        root=tmp_path / "risk",
    )
    reopened.append(result.assessments[0])
    assert len(list(reopened.iter_assessments())) == 1


# -------------------------------------------------------- failure isolation


def test_shield_failure_is_contained_and_reported(tmp_path, capsys):
    def _explode(_at):
        raise RuntimeError("exposure source down")

    result = run_event_risk_shield_safe(
        decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True),
        event_store=_store(tmp_path),
        exposure_provider=_explode,
        storage_root=tmp_path / "risk",
    )
    assert result.available is False
    assert "RuntimeError" in (result.error or "")
    assert "existing production protection unaffected" in capsys.readouterr().out


def test_disabled_shield_is_inert(tmp_path):
    result = run_event_risk_shield(
        decision_at=NOW,
        config=EventRiskShieldConfig(enabled=False),
        event_store=_store(tmp_path),
        exposure_provider=lambda _at: (_exposure(),),
        storage_root=tmp_path / "risk",
    )
    assert result.available is False
    assert result.assessments_generated == 0


def test_observer_reports_bounded_observability_metrics(tmp_path):
    result, _ = _run(
        tmp_path,
        events=[(_event(), NOW - timedelta(minutes=5))],
        exposures=(_exposure(),),
    )
    payload = result.as_dict()
    for key in (
        "decision_at_utc",
        "events_considered",
        "exposures_considered",
        "assessments_stored",
        "duplicates_suppressed",
        "actionable_states",
        "notifications_sent",
        "policy_version",
    ):
        assert key in payload
    assert payload["notifications_sent"] == 0


# ------------------------------------------------------------ safety boundary


def test_build_3_3_notification_transport_is_not_wired_into_observer():
    source = (RISK_PACKAGE / "observer.py").read_text(encoding="utf-8").lower()
    assert "telegram_delivery" not in source
    assert "dispatch_risk_advisory" not in source
    assert "notifications_sent = 0" in source


def test_no_exchange_or_execution_authority_under_risk_package():
    banned_prefixes = ("app.exchanges",)
    banned_modules = {
        "app.services.confirm_entry",
        "app.services.register_trade",
        "app.services.order_intent_registry",
        "app.services.kraken_transport",
        "app.services.trade_cli",
    }
    for path in sorted(RISK_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.startswith(banned_prefixes), (
                    f"{path} imports exchange module {name}"
                )
                assert name not in banned_modules, (
                    f"{path} imports execution module {name}"
                )


def test_no_ai_or_model_calls_in_build_3_2():
    """No module may import an AI SDK or model-calling helper.

    Prose describing the AI/LLM safety invariant (e.g. in policy.py's
    docstring) is expected and must not trip this check; only actual imports
    of an AI-calling module are forbidden.
    """
    banned_modules = {
        "openai",
        "anthropic",
        "app.services.ai_reviewer",
        "app.services.openai_usage_telemetry",
    }
    for path in sorted(RISK_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name not in banned_modules and not any(
                    name.startswith(f"{module}.") for module in banned_modules
                ), f"{path} imports AI module {name}"


def test_deterministic_modules_never_read_wall_clock():
    """Policy and relevance receive time explicitly; they never sample it."""
    for name in ("policy.py", "relevance.py"):
        source = (RISK_PACKAGE / name).read_text(encoding="utf-8")
        assert "datetime.now" not in source
        assert "utcnow" not in source
        assert "time.time" not in source


def test_risk_package_does_not_write_to_lifecycle_registries():
    forbidden = (
        "add_trade",
        "close_trade",
        "terminalize_pending_setup",
        "mark_pending_setup_entered",
        "save_lifecycle",
        "create_lifecycle",
    )
    for path in sorted(RISK_PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path} references lifecycle writer {token}"


def test_shield_reuses_the_shared_archive_primitive():
    """Exactly one archive implementation exists across O'Pip sequences."""
    storage_source = (RISK_PACKAGE / "storage.py").read_text(encoding="utf-8")
    assert "BoundedJsonlArchive" in storage_source
    assert "gzip" not in storage_source
    assert "sha256" not in storage_source
