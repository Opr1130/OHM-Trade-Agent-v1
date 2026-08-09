from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.api import routes
from app.models.signal import RiskPlan, TradingSignal
from app.scanner.models import MarketSnapshot
from app.services import (
    active_trade_registry,
    chief_alert_notifier,
    pending_setup_notifier,
    pending_setup_registry,
    telegram_callback_listener,
)
from app.services.confirm_entry import confirm_trade_id
from app.services.entry_exit_advisor import EntryExitPlan, build_entry_exit_plan
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
        chief_alert_notifier,
        "STATE_FILE",
        tmp_path / "alert_state.json",
    )
    monkeypatch.setattr(
        pending_setup_notifier,
        "STATE_FILE",
        tmp_path / "pending_alert_state.json",
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


def test_actionable_setup_is_persisted_before_confirmation_button(
    registry_files,
    monkeypatch,
):
    observed = {}

    def send(*args, **kwargs):
        setup = pending_setup_registry.get_pending_setups()[0]
        callback = kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        observed["matches"] = callback == f"trade_filled:{setup.trade_id}"
        return True

    monkeypatch.setattr(chief_alert_notifier, "send_telegram_message", send)
    assert chief_alert_notifier.send_trade_plan(
        {"confidence": 90, "decision": "alert"},
        _plan(),
        "summary",
        "token",
        "chat",
    )
    assert observed["matches"]


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
    monkeypatch.setattr(
        pending_setup_notifier,
        "send_telegram_message",
        lambda *args, **kwargs: False,
    )
    result = PendingSetupMonitorResult(setup.symbol, status, 102.0, "terminal")
    pending_setup_notifier.send_pending_setup_update(setup, result, "token", "chat")
    assert pending_setup_registry.get_pending_setups() == []
    assert pending_setup_registry._load_raw()[setup.symbol]["status"] == stored_status
    with pytest.raises(ValueError, match="No confirmable pending setup"):
        confirm_trade_id(setup.trade_id)


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
        "send_telegram_message",
        lambda *args, **kwargs: sent.update(kwargs) or True,
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
