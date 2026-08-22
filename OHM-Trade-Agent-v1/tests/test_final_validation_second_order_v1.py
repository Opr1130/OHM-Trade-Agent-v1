from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import active_trade_registry, pending_setup_registry, profitability_learning, shadow_learning
from app.services.active_trade_registry import ActiveTrade
from app.services.kraken_position_verification import KrakenPositionVerifier
from app.services.pending_setup_registry import PendingSetup
from app.services.registry_io import RegistryCorruptionError, load_json
from app.services.trade_monitor import _require_finite_trade_geometry
import app.services.confirm_entry as confirm_entry_module
import app.services.price_movement_learning as price_movement_learning


class _Candle:
    def __init__(self, timestamp: float, close: float):
        self.timestamp = timestamp
        self.close = close


def _setup(trade_id: str, confirmation_price: float) -> PendingSetup:
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
        confirmation_price=confirmation_price,
        direction="LONG",
    )


def test_second_same_symbol_fill_cannot_overwrite_existing_active_trade(tmp_path, monkeypatch):
    monkeypatch.setattr(pending_setup_registry, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(active_trade_registry, "TRADE_FILE", tmp_path / "active.json")
    monkeypatch.setattr(confirm_entry_module, "mark_trade_entered", lambda *args, **kwargs: None)

    pending_setup_registry.add_pending_setup(_setup("T-ONE", 100.0))
    pending_setup_registry.add_pending_setup(_setup("T-TWO", 100.5))

    first = confirm_entry_module.confirm_trade_id("T-ONE")
    assert first.trade_id == "T-ONE"

    with pytest.raises(ValueError, match="active trade already exists"):
        confirm_entry_module.confirm_trade_id("T-TWO")

    active = active_trade_registry.get_trade("BTCUSD")
    assert active is not None
    assert active.trade_id == "T-ONE"
    assert pending_setup_registry.get_pending_setup_by_trade_id("T-TWO") is not None


@pytest.mark.parametrize(
    "trade",
    [
        ActiveTrade("BTCUSD", 100.0, 90.0, 95.0, 110.0, "low", trade_id="BAD-T1", direction="LONG"),
        ActiveTrade("BTCUSD", 100.0, 90.0, 110.0, 105.0, "low", trade_id="BAD-T2", direction="LONG"),
        ActiveTrade("BTCUSD", 100.0, 110.0, 105.0, 90.0, "low", trade_id="BAD-SHORT", direction="SHORT"),
        ActiveTrade("BTCUSD", 100.0, 90.0, 110.0, 120.0, "low", trade_id="BAD-DIR", direction="SIDEWAYS"),
        ActiveTrade("BTCUSD", 100.0, 90.0, 110.0, 120.0, "low", trade_id="BAD-LEV", direction="LONG", margin_leverage=0.0),
    ],
)
def test_active_trade_monitor_rejects_invalid_persisted_trade_geometry(trade):
    with pytest.raises(ValueError):
        _require_finite_trade_geometry(trade)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_registry_loader_rejects_legacy_nonfinite_json_state(tmp_path, token):
    path = tmp_path / "legacy.json"
    path.write_text('{"value": ' + token + '}', encoding="utf-8")
    with pytest.raises(RegistryCorruptionError):
        load_json(path)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_live_learned_multiplier_fails_neutral_on_nonfinite_legacy_weight(tmp_path, monkeypatch, token):
    profile = tmp_path / "profile.json"
    profile.write_text('{"weights": {"direction:LONG": ' + token + '}}', encoding="utf-8")
    monkeypatch.setattr(profitability_learning, "PROFILE_FILE", profile)
    monkeypatch.setattr(profitability_learning, "LOCK_FILE", tmp_path / ".profile.lock")
    assert profitability_learning.learned_multiplier(direction="LONG", regime=None) == 1.0


def test_profitability_learning_excludes_nonfinite_financial_outcomes():
    rows = [
        {
            "entered_trade": True,
            "terminal_status": "closed",
            "net_pnl": float("nan"),
            "direction": "LONG",
            "market_regime": "BULLISH",
        },
        {
            "entered_trade": True,
            "terminal_status": "closed",
            "net_pnl": 2.0,
            "direction": "LONG",
            "market_regime": "BULLISH",
        },
    ]
    filtered = profitability_learning._financial_trade_rows(rows)
    assert len(filtered) == 1
    assert filtered[0]["net_pnl"] == 2.0


def test_shadow_horizon_price_never_uses_candle_that_closes_after_due_time():
    due = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    candles = [
        _Candle(datetime(2026, 8, 22, 11, 55, tzinfo=timezone.utc).timestamp(), 105.0),
        _Candle(datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc).timestamp(), 150.0),
    ]
    assert shadow_learning._horizon_price(candles, due) == 105.0


class _OHLCClient:
    def __init__(self, candles):
        self.candles = candles

    def get_ohlc(self, *args, **kwargs):
        return self.candles


def test_price_movement_horizon_price_never_uses_bar_closing_after_due_time():
    observed = datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc)
    due = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    candles = [
        _Candle(datetime(2026, 8, 22, 11, 45, tzinfo=timezone.utc).timestamp(), 105.0),
        _Candle(datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc).timestamp(), 150.0),
    ]
    client = _OHLCClient(candles)
    price = price_movement_learning._price_at_horizon(
        client,
        "BTCUSD",
        observed,
        due,
        labelled_at=datetime(2026, 8, 22, 12, 30, tzinfo=timezone.utc),
    )
    assert price == 105.0


@pytest.mark.parametrize("quantity", [float("nan"), float("inf"), float("-inf")])
def test_spot_position_verification_fails_closed_on_nonfinite_private_balance(quantity):
    verifier = KrakenPositionVerifier()
    verifier._balances = {"XXDG": quantity}
    verifier._positions = {}
    trade = ActiveTrade(
        "DOGEUSD",
        0.20,
        0.18,
        0.24,
        0.28,
        "low",
        trade_id="DOGE-LONG",
        direction="LONG",
    )
    result = verifier.verify(trade)
    assert result.status == "UNAVAILABLE"


@pytest.mark.parametrize("volume", ["NaN", "Infinity", "-Infinity"])
def test_short_position_verification_fails_closed_on_nonfinite_open_position(volume):
    verifier = KrakenPositionVerifier()
    verifier._balances = {}
    verifier._positions = {
        "P1": {
            "pair": "XXRPZUSD",
            "type": "sell",
            "vol": volume,
            "vol_closed": "0",
        }
    }
    trade = ActiveTrade(
        "XRPUSD",
        0.60,
        0.66,
        0.54,
        0.48,
        "medium",
        trade_id="XRP-SHORT",
        direction="SHORT",
    )
    result = verifier.verify(trade)
    assert result.status == "UNAVAILABLE"
