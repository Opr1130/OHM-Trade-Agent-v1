from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.core.runtime_environment import conservative_runtime
from app.exchanges.kraken_identity import balance_quantity_for_asset, canonicalize_pair
from app.indicators.technical import volume_ratio
from app.jobs import run_cycle, scan_opportunities
from app.services import (
    active_trade_registry,
    order_intent_registry,
    pending_setup_registry,
    trade_outcome_registry,
)
from app.services.economic_quality_gate import PRODUCTION_MAX_CAPITAL_FRACTION
from app.services.pending_setup_registry import PendingSetup
from app.services.target_attainability import _conservative_runtime
import app.services.confirm_entry as confirm_entry_module
import app.services.price_movement_learning as price_movement_learning


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("XTZUSD", "XTZUSD"),
        ("REZUSD", "REZUSD"),
        ("XTZUSDT", "XTZUSDT"),
        ("XMLNZUSD", "MLNUSD"),
        ("XXRPZUSD", "XRPUSD"),
        ("XXDGZUSD", "DOGEUSD"),
    ],
)
def test_kraken_identity_does_not_missplit_modern_z_ending_assets(raw, expected):
    assert canonicalize_pair(raw) == expected


def test_kraken_balance_can_resolve_modern_xtz_asset():
    assert balance_quantity_for_asset({"XTZ": 500.0}, "XTZ") == 500.0


def test_volume_ratio_uses_last_completed_bar_as_numerator():
    volumes = [1.0] * 20 + [10.0]
    assert volume_ratio(volumes, 20) == 10.0


def test_runtime_safety_uses_settings_environment_and_defaults_conservative(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret-long-enough")
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        assert PRODUCTION_MAX_CAPITAL_FRACTION == pytest.approx(0.20)
        assert _conservative_runtime() is True
    finally:
        get_settings.cache_clear()


def test_missing_app_env_defaults_to_conservative_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_ENV", raising=False)
    assert conservative_runtime() is True


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_scan_alert_economic_gate_uses_twenty_percent_capital_envelope(monkeypatch, direction):
    calls: list[dict] = []

    def fake_economic_quality(plan, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(qualified=True)

    monkeypatch.setattr(scan_opportunities, "evaluate_economic_quality", fake_economic_quality)
    snapshot = SimpleNamespace(trade_direction=direction)

    scan_opportunities._economic_quality(SimpleNamespace(), snapshot, 10_000.0)

    assert len(calls) == 1
    assert calls[0]["available_capital"] == pytest.approx(10_000.0)
    assert calls[0]["max_capital_fraction"] == pytest.approx(PRODUCTION_MAX_CAPITAL_FRACTION)
    if direction == "SHORT":
        assert calls[0]["direction"] == "SHORT"


class _TickerOnlyClient:
    def get_ohlc(self, *args, **kwargs):
        raise RuntimeError("historical OHLC unavailable")

    def get_ticker(self, symbol):
        return {"last": 150.0}

    def get_tickers(self, symbols):
        return {symbols[0]: {"last": 150.0}}


def test_exact_horizon_never_falls_back_to_future_ticker_price():
    observed = datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc)
    due = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    labelled = datetime(2026, 8, 22, 12, 4, tzinfo=timezone.utc)

    price = price_movement_learning._price_at_horizon(
        _TickerOnlyClient(),
        "BTCUSD",
        observed,
        due,
        labelled_at=labelled,
    )

    assert price is None


def _setup(trade_id: str) -> PendingSetup:
    return PendingSetup(
        symbol="BTCUSD",
        entry_low=99.0,
        entry_high=101.0,
        chase_limit=102.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=120.0,
        risk_level="low",
        confidence=80,
        trade_id=trade_id,
        confirmation_price=100.0,
        direction="LONG",
    )


def test_symbol_confirmation_refuses_ambiguity_when_multiple_setups_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(pending_setup_registry, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(active_trade_registry, "TRADE_FILE", tmp_path / "active.json")
    monkeypatch.setattr(confirm_entry_module, "mark_trade_entered", lambda *args, **kwargs: None)
    pending_setup_registry.add_pending_setup(_setup("T-A"))
    pending_setup_registry.add_pending_setup(_setup("T-B"))

    with pytest.raises(ValueError, match="confirm by trade_id"):
        confirm_entry_module.confirm_entry("BTCUSD", 100.0)


def test_terminal_tombstone_does_not_clobber_different_legacy_waiting_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(pending_setup_registry, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(order_intent_registry, "get_order_intent", lambda trade_id: None)
    monkeypatch.setattr(trade_outcome_registry, "terminalize_setup_outcome", lambda *args, **kwargs: None)
    monkeypatch.setattr(pending_setup_registry, "trace_candidate_event", lambda *args, **kwargs: None)

    legacy = _setup("T-LEGACY")
    modern = _setup("T-NEW")
    pending_setup_registry._save_raw(
        {
            "BTCUSD": asdict(legacy),
            "T-NEW": asdict(modern),
        }
    )

    assert pending_setup_registry.terminalize_pending_setup("T-NEW", "skipped") is True
    raw = pending_setup_registry._load_raw()

    assert raw["BTCUSD"]["trade_id"] == "T-LEGACY"
    assert raw["BTCUSD"]["status"] == "waiting"
    assert raw["T-NEW"]["status"] == "skipped"


def test_unified_cycle_runs_active_monitor_when_operator_state_is_corrupt(monkeypatch):
    reconciliation = SimpleNamespace(
        status="OK",
        mode="observe",
        active_checked=0,
        order_intents_checked=0,
        open_orders_seen=0,
        fills_seen=0,
        would_close=(),
        closed=(),
        would_fill=(),
        filled=(),
        reason="",
    )
    external_review = SimpleNamespace(
        status="OK",
        unmatched_orders_seen=0,
        new_reviews=0,
        notifications_sent=0,
        reason="",
    )
    monkeypatch.setattr(run_cycle, "reconcile_kraken_account", lambda: reconciliation)
    monkeypatch.setattr(run_cycle, "review_external_open_orders", lambda: external_review)
    monkeypatch.setattr(
        run_cycle,
        "run_learning_cycle",
        lambda: {
            "status": "OK",
            "paid_ai_calls": 0,
            "shadow": {},
            "price_movement": {},
            "profile_refreshed": False,
            "profile_status": "OK",
        },
    )
    monkeypatch.setattr(run_cycle, "get_operator_decision", lambda: (_ for _ in ()).throw(RuntimeError("corrupt registry")))
    monitor_calls: list[bool] = []
    degraded: list[str] = []
    monkeypatch.setattr(run_cycle, "monitor_active_main", lambda: monitor_calls.append(True))
    monkeypatch.setattr(run_cycle, "_notify_monitor_degraded", lambda **kwargs: degraded.append(kwargs["reason"]))
    monkeypatch.setattr(run_cycle, "get_settings", lambda: SimpleNamespace())

    run_cycle._run_cycle_once()

    assert monitor_calls == [True]
    assert degraded and "operator/capacity state unavailable" in degraded[0]
