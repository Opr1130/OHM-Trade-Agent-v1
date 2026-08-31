from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.api import routes
from app.models.signal import RiskPlan, TradingSignal
from app.scanner.models import MarketSnapshot
from app.services import (
    active_trade_registry,
    chief_alert_notifier,
    notification_policy,
    order_intent_registry,
    pending_setup_notifier,
    pending_setup_registry,
    telegram_callback_listener,
    trade_outcome_registry,
)
from app.services.active_trade_registry import ActiveTrade
from app.services.confirm_entry import confirm_trade_id
from app.services.entry_exit_advisor import EntryExitPlan, build_entry_exit_plan
from app.services.order_intent_registry import OrderIntent
from app.services.pending_setup_monitor import PendingSetupMonitorResult


@pytest.fixture
def registry_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pending_setup_registry,
        "PENDING_FILE",
        tmp_path / "pending.json",
    )
    monkeypatch.setattr(
        active_trade_registry,
        "TRADE_FILE",
        tmp_path / "active.json",
    )
    monkeypatch.setattr(
        order_intent_registry,
        "ORDER_FILE",
        tmp_path / "order_intents.json",
    )
    monkeypatch.setattr(
        order_intent_registry,
        "LOCK_FILE",
        tmp_path / ".order_intents.lock",
    )
    monkeypatch.setattr(
        trade_outcome_registry,
        "OUTCOME_FILE",
        tmp_path / "trade_outcomes.json",
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "STATE_FILE",
        tmp_path / "alert_state.json",
    )
    monkeypatch.setattr(
        notification_policy,
        "STATE_FILE",
        tmp_path / "notification_policy.json",
    )
    monkeypatch.setattr(
        notification_policy,
        "LOCK_FILE",
        tmp_path / ".notification_policy.lock",
    )
    monkeypatch.setattr(
        pending_setup_notifier,
        "STATE_FILE",
        tmp_path / "pending_alert_state.json",
    )
    monkeypatch.setattr(
        pending_setup_notifier,
        "RETRY_FILE",
        tmp_path / "pending_terminal_alert_outbox.json",
    )


def _snapshot(price: float, ema20: float) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTC/USD",
        last_price=price,
        ema20=ema20,
        ema50=95.0,
        ema200=90.0,
        rsi=50.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.5,
        technical_score=90,
        trend="bullish",
    )


def _plan() -> EntryExitPlan:
    return EntryExitPlan(
        symbol="BTC/USD",
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
        reason="Valid entry",
    )


def _setup(symbol: str = "BTC/USD") -> pending_setup_registry.PendingSetup:
    return pending_setup_registry.PendingSetup(
        symbol=symbol,
        entry_low=99.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        risk_level="low",
        confidence=90,
        confirmation_price=100.0,
    )


def _callback_settings(monkeypatch):
    settings = SimpleNamespace(
        telegram_bot_token="token",
        telegram_chat_id="42",
    )
    monkeypatch.setattr(telegram_callback_listener, "get_settings", lambda: settings)
    monkeypatch.setattr(
        telegram_callback_listener,
        "answer_callback_query",
        lambda *args: None,
    )
    monkeypatch.setattr(
        telegram_callback_listener,
        "send_telegram_message",
        lambda *args, **kwargs: True,
    )


def _callback(action: str, trade_id: str) -> dict:
    return {
        "callback_query": {
            "id": f"callback-{action}",
            "data": f"trade_{action}:{trade_id}",
            "message": {"chat": {"id": 42}},
        }
    }


def test_below_ema20_is_wait_and_never_valid_now():
    result = build_entry_exit_plan(_snapshot(price=99.0, ema20=100.0), "low")
    assert result.entry_style == "wait"
    assert result.valid_now is False
    assert result.reason == "Price is below EMA20; wait for trend recovery."


def test_actionable_setup_is_persisted_without_confirmation_buttons(
    registry_files,
    monkeypatch,
):
    observed = {}

    def send(*args, **kwargs):
        setup = pending_setup_registry.get_pending_setups()[0]
        observed["trade_id"] = setup.trade_id
        observed["has_reply_markup"] = "reply_markup" in kwargs
        return SimpleNamespace(delivered=True, message_id=101)

    monkeypatch.setattr(chief_alert_notifier, "send_tracked_telegram", send)
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        lambda **kwargs: None,
    )
    assert chief_alert_notifier.send_trade_plan(
        {
            "confidence": 90,
            "decision": "alert",
            "economic_qualified": True,
            "action_gate_evaluated": True,
            "action_gate_allowed": True,
        },
        _plan(),
        "summary",
        "token",
        "chat",
    )
    assert observed["trade_id"].startswith("OHM-BTC/USD-")
    assert observed["has_reply_markup"] is False


def test_movement_plan_becomes_actionable_only_after_final_intelligence_gate(
    registry_files,
    monkeypatch,
):
    movement = {
        "stage": "CONFIRMED",
        "direction": "LONG",
        "actionable": False,
        "signal_class": "PRICE_MOVEMENT",
        "subtype": "VOLATILITY_EXPANSION",
    }
    candidate = {
        "confidence": 90,
        "decision": "alert",
        "economic_qualified": True,
        "action_gate_evaluated": True,
        "action_gate_allowed": False,
        "price_movement": movement,
    }
    monkeypatch.setattr(chief_alert_notifier, "should_send_trade_plan", lambda *args: True)

    assert not chief_alert_notifier.send_trade_plan(
        candidate,
        _plan(),
        "summary",
        "token",
        "chat",
    )
    assert candidate["price_movement"]["actionable"] is False
    assert "entry" not in candidate["price_movement"]

    candidate["action_gate_allowed"] = True
    candidate["recommended_capital"] = 250.0
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "send_tracked_telegram",
        lambda *args, **kwargs: SimpleNamespace(delivered=True, message_id=102),
    )
    assert chief_alert_notifier.send_trade_plan(
        candidate,
        _plan(),
        "summary",
        "token",
        "chat",
    )
    assert candidate["price_movement"]["actionable"] is True
    assert candidate["price_movement"]["entry"]["zone_low"] == pytest.approx(99.0)
    assert candidate["price_movement"]["exits"]["target_2"] == pytest.approx(115.0)


def test_production_alert_registers_buttonless_kraken_reconciliation_intent(
    registry_files,
    monkeypatch,
):
    sent = {}

    monkeypatch.setattr(chief_alert_notifier, "reconciliation_enabled", lambda: True)
    monkeypatch.setattr(chief_alert_notifier, "reconciliation_mode", lambda: "apply")
    monkeypatch.setattr(
        chief_alert_notifier,
        "send_tracked_telegram",
        lambda *args, **kwargs: sent.update(kwargs) or SimpleNamespace(delivered=True, message_id=103),
    )
    candidate = {
        "confidence": 90,
        "decision": "alert",
        "economic_qualified": True,
        "action_gate_evaluated": True,
        "action_gate_allowed": True,
        "recommended_capital": 250.0,
        "signal_id": "SIG-BTC-1",
        "journey_id": "JOURNEY-BTC-1",
    }

    assert chief_alert_notifier.send_trade_plan(
        candidate,
        _plan(),
        "summary",
        "token",
        "chat",
    )

    setup = pending_setup_registry.get_pending_setups()[0]
    intent = order_intent_registry.get_order_intent(setup.trade_id)
    assert intent is not None
    assert intent.trade_id == setup.trade_id
    assert intent.limit_price == pytest.approx(100.0)
    assert intent.capital == pytest.approx(250.0)
    assert intent.entry_action == "ENTER_NOW"
    assert intent.source == "ohm_actionable_signal"
    assert "reply_markup" not in sent
    assert sent["signal_id"] == "SIG-BTC-1"
    assert sent["journey_id"] == "JOURNEY-BTC-1"


def test_place_limit_reuses_pending_trade_id_for_reconciliation(
    registry_files,
    monkeypatch,
):
    setup = pending_setup_registry.add_pending_setup(_setup())
    plan = replace(_plan(), valid_now=False, entry_style="wait_for_pullback")

    monkeypatch.setattr(chief_alert_notifier, "reconciliation_enabled", lambda: True)
    monkeypatch.setattr(chief_alert_notifier, "reconciliation_mode", lambda: "apply")
    monkeypatch.setattr(
        chief_alert_notifier,
        "send_tracked_telegram",
        lambda *args, **kwargs: SimpleNamespace(delivered=True, message_id=104),
    )
    candidate = {
        "confidence": 90,
        "decision": "alert",
        "economic_qualified": True,
        "action_gate_evaluated": True,
        "action_gate_allowed": True,
        "recommended_capital": 200.0,
        "trade_id": setup.trade_id,
    }

    assert chief_alert_notifier.send_trade_plan(
        candidate,
        plan,
        "summary",
        "token",
        "chat",
    )
    intent = order_intent_registry.get_order_intent(setup.trade_id)
    assert intent is not None
    assert intent.limit_price == pytest.approx(plan.entry_low)
    assert intent.entry_action == "PLACE_LIMIT"


def test_trade_filled_moves_pending_setup_to_active(registry_files):
    setup = pending_setup_registry.add_pending_setup(_setup())
    trade = confirm_trade_id(setup.trade_id)
    assert trade.trade_id == setup.trade_id
    assert active_trade_registry.get_trade(setup.symbol) == trade
    assert pending_setup_registry.get_pending_setups() == []


def test_skip_terminalizes_setup_and_prevents_activation(registry_files, monkeypatch):
    _callback_settings(monkeypatch)
    setup = pending_setup_registry.add_pending_setup(_setup())
    telegram_callback_listener.process_callback(_callback("skip", setup.trade_id))
    assert pending_setup_registry.get_pending_setups() == []
    with pytest.raises(ValueError, match="No confirmable pending setup"):
        confirm_trade_id(setup.trade_id)
    assert active_trade_registry.get_trade(setup.symbol) is None


@pytest.mark.parametrize(
    ("status", "stored_status"),
    [("INVALIDATED", "invalidated"), ("TOO_EXTENDED", "too_extended")],
)
def test_terminal_market_status_cannot_resurrect(
    status,
    stored_status,
    registry_files,
    monkeypatch,
):
    setup = pending_setup_registry.add_pending_setup(_setup())
    monkeypatch.setattr(pending_setup_notifier, "should_emit", lambda **kwargs: True)
    monkeypatch.setattr(pending_setup_notifier, "record_emitted", lambda **kwargs: None)
    monkeypatch.setattr(
        pending_setup_notifier,
        "send_tracked_telegram",
        lambda *args, **kwargs: SimpleNamespace(delivered=True, message_id=105),
    )
    result = PendingSetupMonitorResult(setup.symbol, status, 102.0, "terminal")
    assert pending_setup_notifier.send_pending_setup_update(setup, result, "token", "chat")
    assert pending_setup_registry.get_pending_setups() == []
    raw = pending_setup_registry._load_raw()
    row = raw.get(setup.trade_id) or raw.get(setup.symbol)
    assert row["status"] == stored_status
    with pytest.raises(ValueError, match="No confirmable pending setup"):
        confirm_trade_id(setup.trade_id)


def test_terminal_setup_message_requires_manual_kraken_order_cancellation():
    setup = _setup()
    result = PendingSetupMonitorResult(
        setup.symbol,
        "INVALIDATED",
        95.0,
        "setup failed",
    )

    message = pending_setup_notifier.format_pending_setup_message(setup, result)

    assert "Cancel any open Kraken order" in message
    assert "read-only Kraken key" in message


def test_pending_invalidation_cancels_linked_reconciliation_intent(
    registry_files,
):
    setup = pending_setup_registry.add_pending_setup(_setup())
    order_intent_registry.register_order_intent(
        OrderIntent(
            symbol=setup.symbol,
            direction=setup.direction,
            limit_price=setup.entry_low,
            capital=100.0,
            stop_price=setup.stop_price,
            target_1=setup.target_1,
            target_2=setup.target_2,
            trade_id=setup.trade_id,
            source="ohm_actionable_signal",
        )
    )

    assert pending_setup_registry.terminalize_pending_setup(
        setup.trade_id,
        "invalidated",
    )
    assert order_intent_registry.get_order_intent(setup.trade_id).status == "CANCELLED"


def test_pending_invalidation_does_not_split_state_when_intent_cancel_fails(
    registry_files,
    monkeypatch,
):
    setup = pending_setup_registry.add_pending_setup(_setup())
    order_intent_registry.register_order_intent(
        OrderIntent(
            symbol=setup.symbol,
            direction=setup.direction,
            limit_price=setup.entry_low,
            capital=100.0,
            stop_price=setup.stop_price,
            target_1=setup.target_1,
            target_2=setup.target_2,
            trade_id=setup.trade_id,
            source="ohm_actionable_signal",
        )
    )
    monkeypatch.setattr(
        order_intent_registry,
        "cancel_order_intent",
        lambda trade_id: (_ for _ in ()).throw(OSError("registry unavailable")),
    )

    assert pending_setup_registry.terminalize_pending_setup(
        setup.trade_id,
        "invalidated",
    ) is False
    assert pending_setup_registry.get_pending_setups()[0].trade_id == setup.trade_id
    assert order_intent_registry.get_order_intent(setup.trade_id).status == "LIMIT_PLACED"


def test_verified_order_fill_retires_pending_setup_without_terminalizing_trade(
    registry_files,
):
    setup = pending_setup_registry.add_pending_setup(_setup())
    order_intent_registry.register_order_intent(
        OrderIntent(
            symbol=setup.symbol,
            direction=setup.direction,
            limit_price=100.0,
            capital=100.0,
            stop_price=setup.stop_price,
            target_1=setup.target_1,
            target_2=setup.target_2,
            risk_level=setup.risk_level,
            trade_id=setup.trade_id,
            source="ohm_actionable_signal",
        )
    )

    trade = order_intent_registry.mark_order_filled(
        setup.trade_id,
        fill_price=100.0,
        actual_entry_fee=0.2,
        entry_price_source="kraken_reconciliation_fill",
    )

    assert trade.trade_id == setup.trade_id
    assert trade.risk_level == setup.risk_level
    assert pending_setup_registry.get_pending_setups() == []
    outcome = trade_outcome_registry.get_outcomes()[0]
    assert outcome["entered_trade"] is True
    assert outcome["terminal_status"] is None
    assert outcome["entry_price_source"] == "kraken_reconciliation_fill"


def test_duplicate_sequential_filled_callbacks_are_idempotent(
    registry_files,
    monkeypatch,
):
    _callback_settings(monkeypatch)
    setup = pending_setup_registry.add_pending_setup(_setup())
    update = _callback("filled", setup.trade_id)
    telegram_callback_listener.process_callback(update)
    first = active_trade_registry.get_trade(setup.symbol)
    telegram_callback_listener.process_callback(update)
    second = active_trade_registry.get_trade(setup.symbol)
    assert first == second
    assert len(active_trade_registry.get_active_trades()) == 1


def test_concurrent_filled_callbacks_create_one_active_record(registry_files):
    setup = pending_setup_registry.add_pending_setup(_setup())
    with ThreadPoolExecutor(max_workers=2) as executor:
        trades = list(executor.map(lambda _: confirm_trade_id(setup.trade_id), range(2)))
    assert trades[0] == trades[1]
    assert len(active_trade_registry.get_active_trades()) == 1


def test_active_trade_cannot_be_silently_overwritten(registry_files):
    original = ActiveTrade(
        symbol="BTCUSD",
        entry_price=100.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        risk_level="low",
        trade_id="OHM-BTC-1",
    )
    active_trade_registry.add_trade(original)
    replacement = ActiveTrade(
        symbol="BTCUSD",
        entry_price=101.0,
        stop_price=90.0,
        target_1=120.0,
        target_2=130.0,
        risk_level="medium",
        trade_id="OHM-BTC-2",
    )
    with pytest.raises(ValueError, match="active trade already exists"):
        active_trade_registry.add_trade(replacement)
    assert active_trade_registry.get_trade("BTCUSD").trade_id == "OHM-BTC-1"
    assert active_trade_registry.get_trade("BTCUSD").stop_price == 95.0


def test_exact_same_trade_retry_is_idempotent(registry_files):
    trade = ActiveTrade(
        symbol="BTCUSD",
        entry_price=100.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        risk_level="low",
        trade_id="OHM-BTC-SAME",
    )
    active_trade_registry.add_trade(trade)
    active_trade_registry.add_trade(trade)
    assert len(active_trade_registry.get_active_trades()) == 1
    assert active_trade_registry.get_trade("BTCUSD").trade_id == "OHM-BTC-SAME"


def test_orphaned_filled_intent_is_recovered(registry_files):
    intent = order_intent_registry.register_order_intent(
        OrderIntent(
            symbol="ETHUSD",
            direction="LONG",
            limit_price=2000.0,
            capital=250.0,
            stop_price=1900.0,
            target_1=2200.0,
            target_2=2300.0,
            trade_id="OHM-ETH-RECOVER",
        )
    )
    with order_intent_registry.registry_lock(order_intent_registry.LOCK_FILE):
        data = order_intent_registry._load()
        row = data[intent.trade_id]
        row["status"] = "FILLED"
        row["filled_at"] = "2026-08-13T12:00:00+00:00"
        row["updated_at"] = row["filled_at"]
        row["fill_price"] = 1995.0
        row["actual_entry_fee"] = 0.5
        order_intent_registry._save(data)

    assert active_trade_registry.get_trade("ETHUSD") is None
    recovered, failures = order_intent_registry.recover_orphaned_filled_intents()
    assert recovered == ("OHM-ETH-RECOVER",)
    assert failures == ()
    trade = active_trade_registry.get_trade("ETHUSD")
    assert trade is not None
    assert trade.trade_id == "OHM-ETH-RECOVER"
    assert trade.entry_price == 1995.0
    assert trade.actual_entry_fee == 0.5


def test_crash_after_filled_persistence_self_heals(registry_files, monkeypatch):
    pending_setup_registry.add_pending_setup(
        pending_setup_registry.PendingSetup(
            symbol="SOLUSD",
            entry_low=149.0,
            entry_high=150.0,
            chase_limit=151.0,
            stop_price=140.0,
            target_1=165.0,
            target_2=175.0,
            risk_level="low",
            confidence=90,
            trade_id="OHM-SOL-CRASH",
        )
    )
    intent = order_intent_registry.register_order_intent(
        OrderIntent(
            symbol="SOLUSD",
            direction="LONG",
            limit_price=150.0,
            capital=200.0,
            stop_price=140.0,
            target_1=165.0,
            target_2=175.0,
            trade_id="OHM-SOL-CRASH",
        )
    )
    original_add_trade = order_intent_registry.add_trade

    def crash_once(_trade):
        raise RuntimeError("simulated process crash window")

    monkeypatch.setattr(order_intent_registry, "add_trade", crash_once)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        order_intent_registry.mark_order_filled(intent.trade_id, fill_price=149.5, actual_entry_fee=0.4)

    stored = order_intent_registry.get_order_intent(intent.trade_id)
    assert stored.status == "FILLED"
    assert active_trade_registry.get_trade("SOLUSD") is None
    assert pending_setup_registry.terminalize_pending_setup(
        intent.trade_id,
        "invalidated",
    ) is False
    assert pending_setup_registry.get_pending_setups() == []

    monkeypatch.setattr(order_intent_registry, "add_trade", original_add_trade)
    recovered, failures = order_intent_registry.recover_orphaned_filled_intents()
    assert recovered == ("OHM-SOL-CRASH",)
    assert failures == ()
    assert active_trade_registry.get_trade("SOLUSD").trade_id == "OHM-SOL-CRASH"


def test_generic_tradingview_webhook_has_no_confirmation_buttons(monkeypatch):
    settings = SimpleNamespace(
        webhook_secret="secret-secret",
        account_equity=2_000,
        risk_per_trade_pct=0.35,
        ai_enabled=False,
        min_alert_score=80,
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "score_signal", lambda signal: (90, ["qualified"]))
    monkeypatch.setattr(
        routes,
        "build_risk_plan",
        lambda *args: RiskPlan(
            risk_dollars=7.0,
            position_size=1.0,
            reward_to_risk=3.0,
            allowed=True,
        ),
    )
    monkeypatch.setattr(routes, "append_signal", lambda *args: None)
    sent = {}
    monkeypatch.setattr(
        routes,
        "send_tracked_telegram",
        lambda *args, **kwargs: sent.update(kwargs) or SimpleNamespace(delivered=True, message_id=106),
    )
    signal = TradingSignal(
        symbol="BTC/USD",
        asset_class="crypto",
        price=100.0,
        stop_price=95.0,
        target_price=115.0,
        rsi=50.0,
        volume_ratio=1.5,
        ema_fast=100.0,
        ema_slow=95.0,
        breakout=True,
    )
    decision = routes.tradingview_webhook(signal, "secret-secret")
    assert decision.action == "alert"
    assert "reply_markup" not in sent