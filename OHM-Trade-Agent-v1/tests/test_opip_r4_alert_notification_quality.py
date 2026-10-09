"""R4 alert / notification quality gate acceptance tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.api import routes
from app.jobs import scan_opportunities
from app.services import chief_alert_notifier, notification_policy
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.telegram_delivery import record_telegram_suppression


pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R4-alert-notification-quality-gate"
APP = Path(__file__).resolve().parents[1]


def _plan(**overrides) -> EntryExitPlan:
    payload = dict(
        symbol="SOLUSD",
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=99.5,
        entry_high=100.5,
        chase_limit=101.25,
        stop_price=95.0,
        target_1=110.0,
        target_2=120.5,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="qualified structure\nsecond reason\nthird reason\nfourth reason\nfifth reason",
        direction="LONG",
    )
    payload.update(overrides)
    return EntryExitPlan(**payload)


def _rows():
    return [
        {"rank": 1, "quality_score": 90.0, "symbol": "AAA"},
        {"rank": 2, "quality_score": 80.0, "symbol": "BBB"},
        {"rank": 3, "quality_score": 70.0, "symbol": "CCC"},
        {"rank": 4, "quality_score": None, "symbol": "DDD"},
    ]


def test_score_only_path_cannot_send_new_trade():
    """ATDD-R4-alert-notification-quality-gate/AC-001: a local score cannot send NEW TRADE Telegram."""
    source = Path(routes.__file__).read_text(encoding="utf-8")
    assert "send_tracked_telegram" not in source
    assert "SCORE_ONLY_NOT_AUTHORITATIVE_SELECTION" in source
    scan = Path(scan_opportunities.__file__).read_text(encoding="utf-8")
    assert "select_new_trade_window" in scan
    assert "send_trade_plan" in scan


def test_new_trade_window_is_ranked_and_bounded():
    """ATDD-R4-alert-notification-quality-gate/AC-002: delivery follows authoritative order and a bound."""
    chosen, suppressed = notification_policy.select_new_trade_window(
        _rows(),
        max_per_window=2,
        min_quality_score=0,
    )
    assert [row["symbol"] for row in chosen] == ["AAA", "BBB"]
    assert [row["rank"] for row in chosen] == [1, 2]
    assert any(row["symbol"] == "CCC" for row in suppressed)
    assert any("NEW_TRADE_VOLUME_BUDGET" in row["notification_suppression_reason"] for row in suppressed)
    emergency = Path(APP / "app" / "services" / "emergency_alert_notifier.py").read_text(encoding="utf-8")
    assert "select_new_trade_window" not in emergency


def test_notification_threshold_does_not_change_rank_or_score():
    """ATDD-R4-alert-notification-quality-gate/AC-003: the threshold changes notification eligibility only."""
    original = _rows()
    snapshot = [dict(row) for row in original]
    chosen, suppressed = notification_policy.select_new_trade_window(
        original,
        max_per_window=3,
        min_quality_score=85,
    )
    assert original == snapshot
    assert [row["symbol"] for row in chosen] == ["AAA"]
    assert any("NOTIFICATION_QUALITY_THRESHOLD" in row["notification_suppression_reason"] for row in suppressed)
    assert "min_alert_score" not in Path(notification_policy.__file__).read_text(encoding="utf-8")


def test_fingerprint_dedupe_cooldown_and_material_change(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-004: identity, dedupe, cooldown and material change."""
    monkeypatch.setattr(notification_policy, "STATE_FILE", tmp_path / "notification.json")
    monkeypatch.setattr(notification_policy, "LOCK_FILE", tmp_path / ".notification.lock")
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    first = notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="r4-geometry-v1:A",
        now=now,
    )
    second = notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="r4-geometry-v1:A",
        now=now,
    )
    assert first
    assert second is None
    assert notification_policy.confirm_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="r4-geometry-v1:A",
        reservation_token=first,
        now=now,
    )
    assert notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="r4-geometry-v1:A",
        now=now + timedelta(hours=7),
    ) is None
    assert notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="r4-geometry-v1:B",
        now=now + timedelta(minutes=1),
    )
    plan = _plan()
    quiet = {"direction": "LONG", "observed_at": "2026-10-09T00:00:00Z", "profit_rank": 1}
    noisy = {"direction": "LONG", "observed_at": "2026-10-09T00:00:01Z", "profit_rank": 9}
    assert chief_alert_notifier._alert_state_key(quiet, plan) == chief_alert_notifier._alert_state_key(noisy, plan)
    moved = _plan(stop_price=90.0)
    assert chief_alert_notifier._alert_state_key(quiet, plan) != chief_alert_notifier._alert_state_key(quiet, moved)


def test_tracking_failure_does_not_terminalize_trade():
    """ATDD-R4-alert-notification-quality-gate/AC-005: tracking failure stays retryable."""
    source = Path(chief_alert_notifier.__file__).read_text(encoding="utf-8")
    assert "terminalize_pending_setup" not in source
    assert "TRACKING_FAILURE_RETRYABLE" in source
    assert "release_emit" in source


def test_primary_alert_is_decision_first_without_fake_confidence():
    """ATDD-R4-alert-notification-quality-gate/AC-006: primary body is decision-first and evidence-backed."""
    message = chief_alert_notifier.format_primary_new_trade_alert(
        {
            "direction": "LONG",
            "underlying_asset": "SOL",
            "primary_pair": "SOLUSD",
            "reference_price": 100.25,
            "confidence": 84,
            "reason": "whale activity spotted\nreal structure",
            "trade_id": "T-1",
            "journey_id": "J-1",
            "opportunity_rank": 1,
        },
        _plan(),
    )
    assert message.splitlines()[0].startswith("ENTER NOW — ")
    assert "SOL" in message
    assert "SOLUSD" in message
    assert "Reference price: 100.25" in message
    assert "Entry zone: 99.5 - 100.5" in message
    assert "Target: 120.5" in message
    assert "Invalidation: 95" in message
    assert message.count("\n- ") <= 4
    assert "whale" not in message.lower()
    assert "Confidence" not in message
    assert "%" not in message.split("Trace:")[0]
    again = chief_alert_notifier.format_primary_new_trade_alert(
        {
            "direction": "LONG",
            "underlying_asset": "SOL",
            "primary_pair": "SOLUSD",
            "reference_price": 100.25,
            "confidence": 84,
            "reason": "whale activity spotted\nreal structure",
            "trade_id": "T-1",
            "journey_id": "J-1",
            "opportunity_rank": 1,
        },
        _plan(),
    )
    assert message == again


def test_notification_policy_does_not_rebuild_qualification():
    """ATDD-R4-alert-notification-quality-gate/AC-007: paper still sees the same selected rows."""
    rows = _rows()
    chosen, suppressed = notification_policy.select_new_trade_window(
        rows,
        max_per_window=1,
        min_quality_score=0,
    )
    assert len(rows) == 4
    assert len(chosen) + len(suppressed) == 4
    scan = Path(scan_opportunities.__file__).read_text(encoding="utf-8")
    assert "_route_paper_v2_opportunities(\n                ranked_opportunities," in scan
    assert "chosen_notification_ids" in scan


def test_suppressed_candidate_remains_auditable(tmp_path):
    """ATDD-R4-alert-notification-quality-gate/AC-008: suppression is recorded and the row remains."""
    rows = _rows()
    _chosen, suppressed = notification_policy.select_new_trade_window(
        rows,
        max_per_window=1,
        min_quality_score=0,
    )
    assert any(row["symbol"] == "BBB" for row in suppressed)
    event_file = tmp_path / "events.jsonl"
    state_file = tmp_path / "state.json"
    record_telegram_suppression(
        identity="QUALIFIED_OPPORTUNITY:BBB",
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type="NEW_TRADE",
        fingerprint="r4-geometry-v1:BBB",
        reason=suppressed[0]["notification_suppression_reason"],
        symbol="BBB",
        trade_id="BBB",
        state_file=state_file,
        event_file=event_file,
    )
    text = event_file.read_text(encoding="utf-8")
    assert "SUPPRESSED" in text
    assert "r4-alert-quality-v1" in text
    assert len(rows) == 4


def test_telegram_callback_has_no_exchange_order_authority():
    """ATDD-R4-alert-notification-quality-gate/AC-009: Telegram cannot place an exchange order."""
    source = (APP / "app" / "services" / "telegram_callback_listener.py").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "addorder" not in lowered
    assert "place_order" not in lowered
    assert "Kraken execution is NOT enabled." in source


def test_protection_alerts_are_outside_the_new_trade_budget(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-010: protection alerts ignore the NEW TRADE bound."""
    monkeypatch.setattr(notification_policy, "STATE_FILE", tmp_path / "notification.json")
    monkeypatch.setattr(notification_policy, "LOCK_FILE", tmp_path / ".notification.lock")
    monkeypatch.setattr(notification_policy, "allow_new_noncritical", lambda **kwargs: False)
    assert "STOP" in notification_policy.CRITICAL_EVENTS
    assert notification_policy.should_emit(
        identity="ACTIVE_TRADE:STOP",
        event_type="STOP",
        fingerprint="stop:1",
    )
    assert "select_new_trade_window" not in Path(
        APP / "app" / "services" / "trade_monitor_notifier.py"
    ).read_text(encoding="utf-8")
