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


def _result(action: str = "HOLD", *, price: float = 103.0, pnl: float = 3.0, reasons=None) -> TradeMonitorResult:
    return TradeMonitorResult(
        symbol="BTCUSD",
        action=action,
        current_price=price,
        unrealized_pct=pnl,
        reasons=reasons or ["Trade structure remains healthy"],
    )


def test_same_warning_without_material_change_does_not_repeat(monkeypatch):
    now = datetime.now(timezone.utc)
    state = {
        "BTCUSD": {
            "action": "WARNING",
            "updated_at": (now - timedelta(hours=2)).isoformat(),
            "message_id": 1,
            "price": 103.0,
            "pnl_pct": 3.0,
            "reason_signature": "MACD turned bearish|Price lost EMA20",
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
            AssertionError("routine WARNING must not repeat on time alone")
        ),
    )

    result = _result(
        "WARNING",
        price=102.5,
        pnl=2.5,
        reasons=["MACD turned bearish", "Price lost EMA20"],
    )
    assert notifier.send_monitor_update(_trade(), result, "token", "chat") is False
    assert suppressions[-1]["reason"] == "NO_MATERIAL_CHANGE"


def test_warning_realerts_on_material_price_change(monkeypatch):
    now = datetime.now(timezone.utc)
    state = {
        "BTCUSD": {
            "action": "WARNING",
            "updated_at": now.isoformat(),
            "message_id": 1,
            "price": 103.0,
            "pnl_pct": 3.0,
            "reason_signature": "MACD turned bearish|Price lost EMA20",
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
    monkeypatch.setattr(notifier, "_save_state", lambda value: None)
    monkeypatch.setattr(notifier, "record_emitted", lambda **kwargs: None)

    result = _result(
        "WARNING",
        price=100.8,
        pnl=0.8,
        reasons=["MACD turned bearish", "Price lost EMA20"],
    )
    assert notifier.send_monitor_update(_trade(), result, "token", "chat") is True
    assert sent[0]["event_type"] == "WARNING"


def test_warning_realerts_when_reason_changes(monkeypatch):
    state = {
        "BTCUSD": {
            "action": "WARNING",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "message_id": 1,
            "price": 103.0,
            "pnl_pct": 3.0,
            "reason_signature": "MACD turned bearish|Price lost EMA20",
        }
    }
    monkeypatch.setattr(notifier, "_load_state", lambda: state)
    monkeypatch.setattr(notifier, "should_emit", lambda **kwargs: True)
    sent = []
    monkeypatch.setattr(
        notifier,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(kwargs)
        or SimpleNamespace(delivered=True, message_id=3),
    )
    monkeypatch.setattr(notifier, "_save_state", lambda value: None)
    monkeypatch.setattr(notifier, "record_emitted", lambda **kwargs: None)

    result = _result(
        "WARNING",
        price=102.8,
        pnl=2.8,
        reasons=["Heavy selling pressure increasing", "Price lost EMA20"],
    )
    assert notifier.send_monitor_update(_trade(), result, "token", "chat") is True
    assert sent


def test_actionable_states_repeat_until_resolved(monkeypatch):
    now = datetime.now(timezone.utc)
    state = {
        "BTCUSD": {
            "action": "EXIT_NOW",
            "updated_at": (now - timedelta(minutes=6)).isoformat(),
            "message_id": 1,
            "price": 94.0,
            "pnl_pct": -6.0,
            "reason_signature": "Stop price breached",
        }
    }
    monkeypatch.setattr(notifier, "_load_state", lambda: state)
    monkeypatch.setattr(notifier, "should_emit", lambda **kwargs: True)
    sent = []
    monkeypatch.setattr(
        notifier,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(kwargs)
        or SimpleNamespace(delivered=True, message_id=4),
    )
    monkeypatch.setattr(notifier, "_save_state", lambda value: None)
    monkeypatch.setattr(notifier, "record_emitted", lambda **kwargs: None)

    result = _result("EXIT_NOW", price=94.0, pnl=-6.0, reasons=["Stop price breached"])
    assert notifier.send_monitor_update(_trade(), result, "token", "chat") is True
    assert ":REPEAT:" in sent[0]["fingerprint"]


def test_active_position_states_bypass_noncritical_attention_budget():
    assert "HOLD" in notification_policy.CRITICAL_EVENTS
    assert "WARNING" in notification_policy.CRITICAL_EVENTS
