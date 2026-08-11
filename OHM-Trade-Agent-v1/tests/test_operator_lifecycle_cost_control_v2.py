from datetime import datetime, timezone

import pytest

from app.services.active_trade_registry import ActiveTrade
from app.services.fee_pnl import calculate_fee_aware_pnl, net_edge_is_sufficient
from app.services.order_intent_registry import OrderIntent


def _utc(year, month, day, hour):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def test_fee_aware_long_can_be_net_loss_despite_gross_profit():
    result = calculate_fee_aware_pnl(
        direction="LONG",
        entry_price=100,
        current_or_exit_price=100.10,
        capital=1000,
        estimated_entry_fee_rate=0.004,
        estimated_exit_fee_rate=0.004,
    )
    assert result.gross_pnl == pytest.approx(1.0)
    assert result.total_costs > result.gross_pnl
    assert result.net_pnl < 0
    assert result.net_pnl == pytest.approx(-7.004, abs=0.02)


def test_fee_aware_short_direction_is_correct():
    result = calculate_fee_aware_pnl(
        direction="SHORT",
        entry_price=100,
        current_or_exit_price=95,
        capital=1000,
        leverage=2,
        actual_entry_fee=2,
        actual_exit_fee=2,
        financing_fee=1,
    )
    assert result.gross_pnl == pytest.approx(100)
    assert result.total_costs == pytest.approx(5)
    assert result.net_pnl == pytest.approx(95)


def test_minimum_net_edge_gate_counts_all_friction():
    assert not net_edge_is_sufficient(
        gross_target_move_pct=1.0,
        round_trip_fee_pct=0.5,
        execution_drag_pct=0.2,
        financing_cost_pct=0.1,
        minimum_net_edge_pct=0.5,
    )
    assert net_edge_is_sufficient(
        gross_target_move_pct=2.0,
        round_trip_fee_pct=0.5,
        execution_drag_pct=0.2,
        financing_cost_pct=0.1,
        minimum_net_edge_pct=0.5,
    )


def test_order_intent_register_update_cancel(tmp_path, monkeypatch):
    import app.services.order_intent_registry as registry

    monkeypatch.setattr(registry, "ORDER_FILE", tmp_path / "orders.json")
    monkeypatch.setattr(registry, "LOCK_FILE", tmp_path / ".orders.lock")

    intent = registry.register_order_intent(
        OrderIntent(
            symbol="solusd",
            direction="long",
            limit_price=140,
            capital=2000,
            stop_price=135,
            target_1=145,
            target_2=150,
        )
    )
    assert intent.symbol == "SOLUSD"
    assert intent.status == "LIMIT_PLACED"
    assert len(registry.get_live_order_intents()) == 1

    updated = registry.update_limit_order(intent.trade_id, limit_price=139.5, capital=1800)
    assert updated.limit_price == 139.5
    assert updated.capital == 1800

    cancelled = registry.cancel_order_intent(intent.trade_id)
    assert cancelled.status == "CANCELLED"
    assert registry.get_live_order_intents() == []


def test_duplicate_live_order_for_same_symbol_direction_is_rejected(tmp_path, monkeypatch):
    import app.services.order_intent_registry as registry

    monkeypatch.setattr(registry, "ORDER_FILE", tmp_path / "orders.json")
    monkeypatch.setattr(registry, "LOCK_FILE", tmp_path / ".orders.lock")
    first = OrderIntent(symbol="BTCUSD", direction="LONG", limit_price=100, capital=1000)
    registry.register_order_intent(first)
    with pytest.raises(ValueError, match="already exists"):
        registry.register_order_intent(OrderIntent(symbol="BTCUSD", direction="LONG", limit_price=99, capital=1000))


def test_manual_limit_fill_creates_active_trade(tmp_path, monkeypatch):
    import app.services.order_intent_registry as registry
    import app.services.trade_outcome_registry as outcomes

    monkeypatch.setattr(registry, "ORDER_FILE", tmp_path / "orders.json")
    monkeypatch.setattr(registry, "LOCK_FILE", tmp_path / ".orders.lock")
    captured = []
    monkeypatch.setattr(registry, "add_trade", lambda trade: captured.append(trade))
    monkeypatch.setattr(outcomes, "mark_trade_entered", lambda trade, entry_price_source: {})

    intent = registry.register_order_intent(
        OrderIntent(
            symbol="ETHUSD",
            direction="SHORT",
            limit_price=4000,
            capital=1500,
            stop_price=4100,
            target_1=3900,
            target_2=3800,
            margin_leverage=2,
        )
    )
    trade = registry.mark_order_filled(intent.trade_id, fill_price=3995, actual_entry_fee=3.5)
    assert trade.direction == "SHORT"
    assert trade.capital == 1500
    assert trade.actual_entry_fee == 3.5
    assert len(captured) == 1
    assert registry.get_live_order_intents() == []


def test_operator_auto_quiet_hours_suppresses_search(tmp_path, monkeypatch):
    import app.services.operator_control as control
    import app.services.order_intent_registry as orders

    monkeypatch.setattr(control, "STATE_FILE", tmp_path / "operator.json")
    monkeypatch.setattr(control, "LOCK_FILE", tmp_path / ".operator.lock")
    monkeypatch.setattr(control, "get_active_trades", lambda: [])
    monkeypatch.setattr(control, "get_pending_setups", lambda: [])
    monkeypatch.setattr(orders, "get_live_order_intents", lambda: [])
    # 04:00 UTC is midnight EDT in August.
    decision = control.get_operator_decision(_utc(2026, 8, 11, 4))
    assert decision.quiet_hours is True
    assert decision.effective_mode == "MONITOR"
    assert decision.search_allowed is False


def test_operator_capacity_switches_to_monitor(tmp_path, monkeypatch):
    import app.services.operator_control as control
    import app.services.order_intent_registry as orders

    monkeypatch.setattr(control, "STATE_FILE", tmp_path / "operator.json")
    monkeypatch.setattr(control, "LOCK_FILE", tmp_path / ".operator.lock")
    monkeypatch.setattr(control, "get_active_trades", lambda: [object(), object()])
    monkeypatch.setattr(control, "get_pending_setups", lambda: [])
    monkeypatch.setattr(orders, "get_live_order_intents", lambda: [object()])
    decision = control.get_operator_decision(_utc(2026, 8, 11, 16))
    assert decision.occupied_slots == 3
    assert decision.effective_mode == "MONITOR"
    assert "capacity" in decision.reason


def test_operator_two_slots_throttles_search(tmp_path, monkeypatch):
    import app.services.operator_control as control
    import app.services.order_intent_registry as orders

    monkeypatch.setattr(control, "STATE_FILE", tmp_path / "operator.json")
    monkeypatch.setattr(control, "LOCK_FILE", tmp_path / ".operator.lock")
    monkeypatch.setattr(control, "get_active_trades", lambda: [object(), object()])
    monkeypatch.setattr(control, "get_pending_setups", lambda: [])
    monkeypatch.setattr(orders, "get_live_order_intents", lambda: [])
    decision = control.get_operator_decision(_utc(2026, 8, 11, 16))
    assert decision.effective_mode == "SEARCH"
    assert decision.search_interval_seconds == 900


def test_operator_manual_maintenance_override(tmp_path, monkeypatch):
    import app.services.operator_control as control
    import app.services.order_intent_registry as orders

    monkeypatch.setattr(control, "STATE_FILE", tmp_path / "operator.json")
    monkeypatch.setattr(control, "LOCK_FILE", tmp_path / ".operator.lock")
    monkeypatch.setattr(control, "get_active_trades", lambda: [])
    monkeypatch.setattr(control, "get_pending_setups", lambda: [])
    monkeypatch.setattr(orders, "get_live_order_intents", lambda: [])
    control.set_override_mode("maintenance")
    decision = control.get_operator_decision(_utc(2026, 8, 11, 16))
    assert decision.override_mode == "MAINTENANCE"
    assert decision.effective_mode == "MAINTENANCE"


def test_notification_policy_suppresses_same_fingerprint(tmp_path, monkeypatch):
    import app.services.notification_policy as policy

    monkeypatch.setattr(policy, "STATE_FILE", tmp_path / "notifications.json")
    monkeypatch.setattr(policy, "LOCK_FILE", tmp_path / ".notifications.lock")
    now = _utc(2026, 8, 11, 16)
    assert policy.should_emit(identity="LONG:SOLUSD", event_type="OPPORTUNITY", fingerprint="a", now=now)
    policy.record_emitted(identity="LONG:SOLUSD", event_type="OPPORTUNITY", fingerprint="a", now=now)
    assert not policy.should_emit(identity="LONG:SOLUSD", event_type="OPPORTUNITY", fingerprint="a", now=now)
    # Even a changed non-critical opportunity remains suppressed inside cooldown.
    assert not policy.should_emit(identity="LONG:SOLUSD", event_type="OPPORTUNITY", fingerprint="b", now=now)


def test_active_trade_new_fields_are_backward_compatible():
    trade = ActiveTrade(
        symbol="BTCUSD",
        entry_price=100,
        stop_price=95,
        target_1=105,
        target_2=110,
        risk_level="low",
    )
    assert trade.capital is None
    assert trade.actual_entry_fee is None
    assert trade.financing_fee == 0.0
