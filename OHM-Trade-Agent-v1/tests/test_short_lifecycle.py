import json
from types import SimpleNamespace

import pytest

from app.services import active_trade_registry, pending_setup_registry, trade_outcome_registry
from app.services.active_trade_registry import ActiveTrade
from app.services.confirm_entry import confirm_entry
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.pending_setup_registry import PendingSetup, add_pending_setup
from app.services.trade_outcome_registry import (
    mark_trade_entered,
    record_recommendation,
    update_active_observation,
)


@pytest.fixture
def registries(tmp_path, monkeypatch):
    pending = tmp_path / "pending.json"
    active = tmp_path / "active.json"
    outcomes = tmp_path / "outcomes.json"
    lock = tmp_path / ".trade_registry.lock"
    monkeypatch.setattr(pending_setup_registry, "PENDING_FILE", pending)
    monkeypatch.setattr(active_trade_registry, "TRADE_FILE", active)
    monkeypatch.setattr(trade_outcome_registry, "OUTCOME_FILE", outcomes)
    monkeypatch.setattr(trade_outcome_registry, "registry_lock_file", lambda: tmp_path / ".outcome.lock")
    monkeypatch.setattr(pending_setup_registry, "registry_lock_file", lambda: lock)
    monkeypatch.setattr(active_trade_registry, "registry_lock_file", lambda: lock)
    return pending, active, outcomes


def short_plan():
    return EntryExitPlan(
        symbol="BTCUSD",
        valid_now=True,
        entry_style="rebound_or_retest",
        entry_low=100.0,
        entry_high=101.0,
        chase_limit=98.0,
        stop_price=105.0,
        target_1=92.0,
        target_2=88.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="test",
        direction="SHORT",
    )


def test_short_recommendation_captures_direction_and_leverage(registries):
    record = record_recommendation(
        trade_id="OHM-BTC-1",
        candidate={"direction": "SHORT", "margin_leverage": 2.0, "confidence": 90},
        plan=short_plan(),
        action="ENTER_NOW",
    )
    assert record["direction"] == "SHORT"
    assert record["margin_leverage"] == 2.0
    assert record["entered_trade"] is False


def test_short_pending_to_active_preserves_direction(registries):
    setup = add_pending_setup(PendingSetup(
        symbol="BTCUSD",
        entry_low=100,
        entry_high=101,
        chase_limit=98,
        stop_price=105,
        target_1=92,
        target_2=88,
        risk_level="low",
        confidence=90,
        direction="SHORT",
        margin_leverage=2.0,
    ))
    record_recommendation(
        trade_id=setup.trade_id,
        candidate={"direction": "SHORT", "margin_leverage": 2.0, "confidence": 90},
        plan=short_plan(),
        action="ENTER_NOW",
    )
    trade = confirm_entry("BTCUSD", 100.0)
    assert trade.direction == "SHORT"
    assert trade.margin_leverage == 2.0
    outcome = trade_outcome_registry.get_outcomes()[0]
    assert outcome["entered_trade"] is True
    assert outcome["direction"] == "SHORT"


def test_short_fill_below_chase_limit_is_rejected(registries):
    add_pending_setup(PendingSetup(
        symbol="BTCUSD", entry_low=100, entry_high=101, chase_limit=98,
        stop_price=105, target_1=92, target_2=88, risk_level="low",
        confidence=90, direction="SHORT", margin_leverage=2.0,
    ))
    with pytest.raises(ValueError, match="below short chase limit"):
        confirm_entry("BTCUSD", 97.0)


def test_short_fill_at_stop_is_rejected(registries):
    add_pending_setup(PendingSetup(
        symbol="BTCUSD", entry_low=100, entry_high=101, chase_limit=98,
        stop_price=105, target_1=92, target_2=88, risk_level="low",
        confidence=90, direction="SHORT", margin_leverage=2.0,
    ))
    with pytest.raises(ValueError, match="at or above"):
        confirm_entry("BTCUSD", 105.0)


def test_short_outcome_mfe_mae_and_hits_are_direction_aware(registries):
    trade = ActiveTrade(
        symbol="BTCUSD",
        entry_price=100,
        stop_price=105,
        target_1=92,
        target_2=88,
        risk_level="low",
        trade_id="OHM-BTC-2",
        direction="SHORT",
        margin_leverage=2.0,
    )
    mark_trade_entered(trade, entry_price_source="manual_actual_fill")
    r1 = update_active_observation(trade, 94.0, observed_at="2026-01-01T00:00:00+00:00")
    assert r1["mfe_pct"] == 6.0
    assert r1["mae_pct"] == 0.0
    assert not r1["target_1_observed"]

    r2 = update_active_observation(trade, 91.0, observed_at="2026-01-01T01:00:00+00:00")
    assert r2["target_1_observed"]
    assert r2["target_1_first_observed_at"] == "2026-01-01T01:00:00+00:00"

    r3 = update_active_observation(trade, 106.0, observed_at="2026-01-01T02:00:00+00:00")
    assert r3["mae_pct"] == 6.0
    assert r3["stop_observed"]


def test_legacy_active_trade_loads_as_long(registries):
    _, active_path, _ = registries
    active_path.write_text(json.dumps({
        "BTCUSD": {
            "symbol": "BTCUSD", "entry_price": 100, "stop_price": 95,
            "target_1": 110, "target_2": 120, "risk_level": "low",
            "status": "active", "opened_at": "", "trade_id": "",
        }
    }))
    trade = active_trade_registry.get_trade("BTCUSD")
    assert trade is not None
    assert trade.direction == "LONG"
    assert trade.margin_leverage == 1.0
