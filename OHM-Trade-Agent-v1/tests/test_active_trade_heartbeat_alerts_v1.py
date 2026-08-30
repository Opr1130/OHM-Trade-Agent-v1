from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.active_trade_registry import ActiveTrade
from app.services import notification_policy
from app.services import trade_monitor_notifier as notifier
from app.services.trade_monitor import TradeMonitorResult


def _trade() -> ActiveTrade:
    return ActiveTrade(
        symbol="BTCUSD",
        entry_price=100.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=120.0,
        risk_level="medium",
        status="active",
        trade_id="T-BTC-1",
        direction="LONG",
    )


def _result(action: str = "HOLD") -> TradeMonitorResult:
    return TradeMonitorResult(
        symbol="BTCUSD",
        action=action,
        current_price=103.0,
        unrealized_pct=3.0,
        reasons=["Trade structure remains healthy"],
    )


def test_active_trade_same_action_is_suppressed_only_until_heartbeat(monkeypatch):
    now = datetime.now(timezone.utc)
    state = {
        "BTCUSD": {
            "action": "HOLD",
            "updated_at": now.isoformat(),
            "message_id": 1,
        }
    }
    monkeypatch.setattr(notifier, "_load_state", lambda: state)
    suppressions = []
    monkeypatch.setattr(
        notifier,
        "record_telegram_suppression",
        lambda **kwargs: suppressions.append(kwargs),
    )
    monkeypatch.setattr(
        notifier,
        "send_tracked_telegram",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("same-action heartbeat must not send before due")
        ),
    )

    assert notifier.send_monitor_update(_trade(), _result(), "token", "chat") is False
    assert suppressions[-1]["reason"] == "SAME_ACTION_HEARTBEAT_NOT_DUE"


def test_active_trade_same_action_reemits_after_heartbeat(monkeypatch):
    now = datetime.now(timezone.utc)
    state = {
        "BTCUSD": {
            "action": "HOLD",
            "updated_at": (now - timedelta(minutes=31)).isoformat(),
            "message_id": 1,
        }
    }
    monkeypatch.setattr(notifier, "_load_state", lambda: state)
    monkeypatch.setattr(notifier, "should_emit", lambda **kwargs: True)
    sent = []
    monkeypatch.setattr(
        notifier,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(kwargs)
        or SimpleNamespace(delivered=True, message_id=2),
    )
    saved = []
    monkeypatch.setattr(notifier, "_save_state", lambda value: saved.append(dict(value)))
    monkeypatch.setattr(notifier, "record_emitted", lambda **kwargs: None)

    assert notifier.send_monitor_update(_trade(), _result(), "token", "chat") is True
    assert sent
    assert sent[0]["alert_family"] == "ACTIVE_TRADE"
    assert sent[0]["event_type"] == "HOLD"
    assert sent[0]["fingerprint"].startswith("LONG:HOLD:")
    assert saved[-1]["BTCUSD"]["action"] == "HOLD"
    assert saved[-1]["BTCUSD"]["price"] == 103.0


def test_active_position_states_bypass_noncritical_attention_budget():
    assert "HOLD" in notification_policy.CRITICAL_EVENTS
    assert "WARNING" in notification_policy.CRITICAL_EVENTS


def test_active_trade_heartbeat_cadence_prioritizes_risk():
    assert notifier.HEARTBEAT_SECONDS["EXIT_NOW"] < notifier.HEARTBEAT_SECONDS["TAKE_PROFIT"]
    assert notifier.HEARTBEAT_SECONDS["TAKE_PROFIT"] < notifier.HEARTBEAT_SECONDS["WARNING"]
    assert notifier.HEARTBEAT_SECONDS["WARNING"] < notifier.HEARTBEAT_SECONDS["HOLD"]
