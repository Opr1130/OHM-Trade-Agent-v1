from __future__ import annotations

import inspect

from app.services import (
    chief_alert_notifier,
    notification_policy,
    qualified_alert_outbox,
)
from app.services.entry_exit_advisor import EntryExitPlan


def _plan():
    return EntryExitPlan(
        symbol="SOLUSD",
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=99.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="qualified",
        direction="LONG",
    )


def test_actionable_trade_is_lifecycle_critical():
    assert "ACTIONABLE_TRADE" in notification_policy.CRITICAL_EVENTS


def test_actionable_trade_bypasses_generic_attention_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notification_policy,
        "STATE_FILE",
        tmp_path / "notification_state.json",
    )
    monkeypatch.setattr(
        notification_policy,
        "LOCK_FILE",
        tmp_path / ".notification_state.lock",
    )
    monkeypatch.setattr(
        notification_policy,
        "allow_new_noncritical",
        lambda **kwargs: False,
    )

    assert notification_policy.should_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="fp",
    )
    reservation = notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="fp",
    )
    assert reservation is not None


def test_convenience_dedup_failure_cannot_drop_actionable_trade(monkeypatch):
    monkeypatch.setattr(
        chief_alert_notifier,
        "_load_state",
        lambda: (_ for _ in ()).throw(OSError("dedup unavailable")),
    )
    seen = {}
    monkeypatch.setattr(
        chief_alert_notifier,
        "should_emit",
        lambda **kwargs: seen.update(kwargs) or True,
    )

    assert chief_alert_notifier.should_send_trade_plan(
        {"direction": "LONG"},
        _plan(),
    )
    assert seen["event_type"] == "ACTIONABLE_TRADE"


def test_retry_outbox_uses_actionable_policy_class():
    source = inspect.getsource(qualified_alert_outbox._retry_one)
    assert 'event_type="ACTIONABLE_TRADE"' in source
