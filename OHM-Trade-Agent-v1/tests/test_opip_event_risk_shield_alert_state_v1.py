from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from app.opip.events.contract import EventSeverity
from app.opip.risk.alert_state import AlertStateManager, aggregate_exposure_risk
from app.opip.risk.contract import ExposureFamily, ExposureState, RiskState
from app.opip.risk.notifier import dispatch_risk_advisory

from tests.test_opip_event_risk_shield_observer_v1 import NOW, _exposure

# Construct assessments by using the real observer fixture path would duplicate
# storage concerns. This local helper clones a valid generated shape minimally.
from app.opip.events.contract import EventClass, EventType
from app.opip.risk.contract import Direction, EvidenceConfidence, Relevance, RiskAssessment


def _assessment(
    *, exposure_id="X", family=ExposureFamily.REAL_ADVISORY,
    state=ExposureState.ACTIVE, risk=RiskState.PROTECT_REVIEW,
    severity=EventSeverity.HIGH, event="e1", visible=None,
):
    visible = visible or NOW - timedelta(minutes=5)
    return RiskAssessment(
        assessment_id=f"A:{exposure_id}:{event}:{risk.value}:{severity.value}",
        event_id=event,
        effective_event_id=event,
        decision_at_utc=NOW,
        exposure_id=exposure_id,
        exposure_family=family,
        exposure_state=state,
        pending=state is ExposureState.PENDING,
        direction=Direction.LONG,
        event_class=EventClass.NEWS,
        event_type=EventType.NEWS_SECURITY,
        event_severity=severity,
        relevance=Relevance.DIRECT_ASSET,
        risk_state=risk,
        risk_score=0.8,
        policy_version="opip-event-risk-policy-v1",
        input_evidence_hash=f"hash:{event}:{severity.value}",
        created_at_utc=NOW,
        canonical_asset_id="solana",
        event_age_seconds=300,
        freshness_seconds=300,
        evidence_age_seconds=300,
        ingestion_lag_seconds=0,
        event_source_time_utc=visible,
        event_decision_visible_at_utc=visible,
        event_expires_at_utc=NOW + timedelta(hours=1),
        evidence_confidence=EvidenceConfidence.NORMAL,
    )


def test_highest_current_state_wins_not_last_row():
    exposure = _exposure(exposure_id="X")
    exit_row = _assessment(exposure_id="X", risk=RiskState.EXIT_REVIEW, severity=EventSeverity.CRITICAL, event="old-high")
    watch_row = _assessment(exposure_id="X", risk=RiskState.WATCH, severity=EventSeverity.MEDIUM, event="new-low", visible=NOW - timedelta(minutes=1))
    aggregate = aggregate_exposure_risk(exposure=exposure, assessments=(exit_row, watch_row), decision_at=NOW)
    assert aggregate.risk_state is RiskState.EXIT_REVIEW
    assert aggregate.selected_assessment is exit_row


def test_same_state_is_suppressed_and_escalation_is_candidate(tmp_path):
    exposure = _exposure(exposure_id="X")
    manager = AlertStateManager(family=ExposureFamily.REAL_ADVISORY, root=tmp_path)
    first_a = _assessment(exposure_id="X", risk=RiskState.PROTECT_REVIEW, event="e1")
    first_agg = aggregate_exposure_risk(exposure=exposure, assessments=(first_a,), decision_at=NOW)
    first = manager.evaluate(exposure=exposure, aggregate=first_agg, decision_at=NOW, coverage_complete=True)
    assert first.should_notify is True
    assert manager.commit(decision=first, aggregate=first_agg, decision_at=NOW)

    same = manager.evaluate(exposure=exposure, aggregate=first_agg, decision_at=NOW, coverage_complete=True)
    assert same.transition == "UNCHANGED"
    assert same.should_notify is False

    exit_a = _assessment(exposure_id="X", risk=RiskState.EXIT_REVIEW, severity=EventSeverity.CRITICAL, event="e2")
    exit_agg = aggregate_exposure_risk(exposure=exposure, assessments=(exit_a,), decision_at=NOW)
    escalated = manager.evaluate(exposure=exposure, aggregate=exit_agg, decision_at=NOW, coverage_complete=True)
    assert escalated.transition == "ESCALATION"
    assert escalated.should_notify is True


def test_same_state_higher_severity_new_event_is_candidate(tmp_path):
    exposure = _exposure(exposure_id="X")
    manager = AlertStateManager(family=ExposureFamily.REAL_ADVISORY, root=tmp_path)
    low = _assessment(exposure_id="X", risk=RiskState.PROTECT_REVIEW, severity=EventSeverity.HIGH, event="e1")
    agg1 = aggregate_exposure_risk(exposure=exposure, assessments=(low,), decision_at=NOW)
    d1 = manager.evaluate(exposure=exposure, aggregate=agg1, decision_at=NOW, coverage_complete=True)
    manager.commit(decision=d1, aggregate=agg1, decision_at=NOW)

    high = _assessment(exposure_id="X", risk=RiskState.PROTECT_REVIEW, severity=EventSeverity.CRITICAL, event="e2")
    agg2 = aggregate_exposure_risk(exposure=exposure, assessments=(high,), decision_at=NOW)
    d2 = manager.evaluate(exposure=exposure, aggregate=agg2, decision_at=NOW, coverage_complete=True)
    assert d2.transition == "MATERIAL_REVISION_OR_EVENT"
    assert d2.should_notify is True


def test_incomplete_coverage_freezes_deescalation(tmp_path):
    exposure = _exposure(exposure_id="X")
    manager = AlertStateManager(family=ExposureFamily.REAL_ADVISORY, root=tmp_path)
    high = _assessment(exposure_id="X", risk=RiskState.EXIT_REVIEW, severity=EventSeverity.CRITICAL)
    agg = aggregate_exposure_risk(exposure=exposure, assessments=(high,), decision_at=NOW)
    first = manager.evaluate(exposure=exposure, aggregate=agg, decision_at=NOW, coverage_complete=True)
    manager.commit(decision=first, aggregate=agg, decision_at=NOW)

    none = aggregate_exposure_risk(exposure=exposure, assessments=(), decision_at=NOW + timedelta(minutes=1))
    frozen = manager.evaluate(exposure=exposure, aggregate=none, decision_at=NOW + timedelta(minutes=1), coverage_complete=False)
    assert frozen.current_state is RiskState.EXIT_REVIEW
    assert frozen.state_frozen is True
    assert frozen.should_notify is False

    complete = manager.evaluate(exposure=exposure, aggregate=none, decision_at=NOW + timedelta(minutes=1), coverage_complete=True)
    assert complete.current_state is RiskState.NONE
    assert complete.transition == "DEESCALATION"
    assert complete.should_notify is False


def test_paper_never_becomes_notification_candidate(tmp_path):
    exposure = _exposure(exposure_id="PX", family=ExposureFamily.PAPER)
    assessment = _assessment(exposure_id="PX", family=ExposureFamily.PAPER, risk=RiskState.EXIT_REVIEW, severity=EventSeverity.CRITICAL)
    aggregate = aggregate_exposure_risk(exposure=exposure, assessments=(assessment,), decision_at=NOW)
    manager = AlertStateManager(family=ExposureFamily.PAPER, root=tmp_path)
    decision = manager.evaluate(exposure=exposure, aggregate=aggregate, decision_at=NOW, coverage_complete=True)
    assert decision.should_notify is False


def test_notifier_is_inert_for_paper_and_unconfigured_telegram(tmp_path):
    paper = _exposure(exposure_id="PX", family=ExposureFamily.PAPER)
    a_paper = _assessment(exposure_id="PX", family=ExposureFamily.PAPER, risk=RiskState.EXIT_REVIEW, severity=EventSeverity.CRITICAL)
    m_paper = AlertStateManager(family=ExposureFamily.PAPER, root=tmp_path)
    agg_paper = aggregate_exposure_risk(exposure=paper, assessments=(a_paper,), decision_at=NOW)
    d_paper = m_paper.evaluate(exposure=paper, aggregate=agg_paper, decision_at=NOW, coverage_complete=True)
    r = dispatch_risk_advisory(
        exposure=paper, assessment=a_paper, decision=d_paper,
        telegram_enabled=True, bot_token="x", chat_id="y", generated_at=NOW,
    )
    assert r.eligible is False and r.delivered is False

    real = _exposure(exposure_id="RX")
    a_real = _assessment(exposure_id="RX", risk=RiskState.EXIT_REVIEW, severity=EventSeverity.CRITICAL)
    m_real = AlertStateManager(family=ExposureFamily.REAL_ADVISORY, root=tmp_path / "real")
    agg_real = aggregate_exposure_risk(exposure=real, assessments=(a_real,), decision_at=NOW)
    d_real = m_real.evaluate(exposure=real, aggregate=agg_real, decision_at=NOW, coverage_complete=True)
    r2 = dispatch_risk_advisory(
        exposure=real, assessment=a_real, decision=d_real,
        telegram_enabled=False, bot_token=None, chat_id=None, generated_at=NOW,
    )
    assert r2.eligible is True and r2.delivered is False
    assert r2.status == "TELEGRAM_DISABLED_OR_UNCONFIGURED"
