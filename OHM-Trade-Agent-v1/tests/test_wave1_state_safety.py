from __future__ import annotations

from dataclasses import asdict

import pytest

from app.services import active_trade_registry, order_intent_registry, trade_outcome_registry
from app.services.active_trade_registry import ActiveTrade
from app.services.order_intent_registry import OrderIntent


@pytest.fixture
def state_files(tmp_path, monkeypatch):
    monkeypatch.setattr(active_trade_registry, "TRADE_FILE", tmp_path / "active_trades.json")
    monkeypatch.setattr(order_intent_registry, "ORDER_FILE", tmp_path / "order_intents.json")
    monkeypatch.setattr(order_intent_registry, "LOCK_FILE", tmp_path / ".order_intents.lock")
    monkeypatch.setattr(trade_outcome_registry, "OUTCOME_FILE", tmp_path / "trade_outcomes.json")


def _trade(*, trade_id: str = "T-1", entry_price: float = 100.0) -> ActiveTrade:
    return ActiveTrade(
        symbol="BTCUSD",
        entry_price=entry_price,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        risk_level="manual",
        trade_id=trade_id,
        direction="LONG",
        capital=100.0,
    )


def _intent(*, trade_id: str = "T-1") -> OrderIntent:
    return OrderIntent(
        symbol="BTCUSD",
        direction="LONG",
        limit_price=100.0,
        capital=100.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        trade_id=trade_id,
    )


def test_second_active_trade_same_symbol_cannot_overwrite_original(state_files):
    original = _trade(trade_id="ORIGINAL", entry_price=100.0)
    active_trade_registry.add_trade(original)

    with pytest.raises(ValueError, match="active trade already exists"):
        active_trade_registry.add_trade(_trade(trade_id="SECOND", entry_price=101.0))

    stored = active_trade_registry.get_trade("BTCUSD")
    assert stored is not None
    assert stored.trade_id == "ORIGINAL"
    assert stored.entry_price == 100.0
    assert stored.stop_price == 95.0


def test_exact_same_trade_id_retry_is_idempotent(state_files):
    original = _trade(trade_id="SAME", entry_price=100.0)
    active_trade_registry.add_trade(original)

    retry = _trade(trade_id="SAME", entry_price=101.0)
    active_trade_registry.add_trade(retry)

    stored = active_trade_registry.get_trade("BTCUSD")
    assert stored is not None
    assert stored.trade_id == "SAME"
    assert stored.entry_price == 100.0
    assert len(active_trade_registry.get_active_trades()) == 1


def test_orphaned_filled_intent_is_recovered(state_files):
    intent = _intent(trade_id="RECOVER-ME")
    row = asdict(intent)
    row.update(
        status="FILLED",
        fill_price=99.5,
        filled_at="2026-08-13T12:00:00+00:00",
        updated_at="2026-08-13T12:00:00+00:00",
        actual_entry_fee=0.25,
    )
    order_intent_registry._save({intent.trade_id: row})

    recovered, failures = order_intent_registry.recover_orphaned_filled_intents()

    assert recovered == ("RECOVER-ME",)
    assert failures == ()
    trade = active_trade_registry.get_trade("BTCUSD")
    assert trade is not None
    assert trade.trade_id == "RECOVER-ME"
    assert trade.entry_price == 99.5
    assert trade.actual_entry_fee == 0.25
    assert trade.opened_at == "2026-08-13T12:00:00+00:00"


def test_crash_after_filled_persist_self_heals_on_next_recovery(state_files, monkeypatch):
    intent = order_intent_registry.register_order_intent(_intent(trade_id="CRASH-WINDOW"))

    real_add_trade = order_intent_registry.add_trade

    def crash_once(_trade):
        raise RuntimeError("simulated process crash after FILLED persistence")

    monkeypatch.setattr(order_intent_registry, "add_trade", crash_once)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        order_intent_registry.mark_order_filled(
            intent.trade_id,
            fill_price=100.25,
            actual_entry_fee=0.15,
        )

    persisted = order_intent_registry._load()[intent.trade_id]
    assert persisted["status"] == "FILLED"
    assert persisted["fill_price"] == 100.25
    assert active_trade_registry.get_trade("BTCUSD") is None

    monkeypatch.setattr(order_intent_registry, "add_trade", real_add_trade)
    recovered, failures = order_intent_registry.recover_orphaned_filled_intents()

    assert recovered == ("CRASH-WINDOW",)
    assert failures == ()
    trade = active_trade_registry.get_trade("BTCUSD")
    assert trade is not None
    assert trade.trade_id == "CRASH-WINDOW"
    assert trade.entry_price == 100.25
