from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import telegram_command_center as commands
from app.services.registry_io import RegistryCorruptionError
from app.services.telegram_market_insights import MarketInsight


def _settings():
    return SimpleNamespace(
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="123",
        telegram_command_user_id=None,
        telegram_command_rate_limit_per_minute=12,
    )


def _insight(*, price=100.0, state="IGNITION", action="WATCH", risk="NORMAL"):
    return MarketInsight(
        symbol="VVV",
        pair="VVVUSD",
        current_price=price,
        state=state,
        phase_score=35,
        risk=risk,
        action=action,
        tradeability="GOOD",
        momentum_1h_pct=1.0,
        momentum_6h_pct=2.0,
        momentum_24h_pct=4.0,
        relative_volume=1.5,
        spread_bps=10.0,
        distance_to_24h_high_pct=2.0,
        historical_upside_median_pct=3.0,
        historical_upside_p75_pct=6.0,
        historical_downside_median_pct=2.0,
        historical_downside_p75_pct=4.0,
        flow_bias="NEUTRAL",
        flow_strength=1,
        reason="test",
        warnings=(),
        rsi=55.0,
        atr_pct=3.0,
        trend="BULLISH",
    )


def test_nonmaterial_refresh_preserves_last_delivered_price_baseline(monkeypatch):
    old = commands._watch_row(_insight(price=100.0), 9)
    old["last_checked_at"] = "2000-01-01T00:00:00+00:00"
    saved = []
    monkeypatch.setattr(commands, "get_watches", lambda: {"VVV": old})
    monkeypatch.setattr(commands, "analyze_market", lambda symbol: _insight(price=101.5))
    monkeypatch.setattr(commands, "send_tracked_telegram", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected send")))
    monkeypatch.setattr(commands, "_put_watch", lambda symbol, row: saved.append(dict(row)))

    assert commands.refresh_market_watches(_settings(), force=True) == 0
    assert saved[-1]["last_price"] == 100.0
    assert saved[-1]["state"] == "IGNITION"
    assert saved[-1]["last_checked_at"] != old["last_checked_at"]


def test_failed_material_delivery_preserves_delivered_state_for_retry(monkeypatch):
    old = commands._watch_row(_insight(price=100.0), 9)
    old["last_checked_at"] = "2000-01-01T00:00:00+00:00"
    saved = []
    monkeypatch.setattr(commands, "get_watches", lambda: {"VVV": old})
    monkeypatch.setattr(
        commands,
        "analyze_market",
        lambda symbol: _insight(price=103.0, state="REVERSAL_RISK", action="WAIT", risk="HIGH"),
    )
    monkeypatch.setattr(
        commands,
        "send_tracked_telegram",
        lambda *a, **k: SimpleNamespace(delivered=False, message_id=None),
    )
    monkeypatch.setattr(commands, "_put_watch", lambda symbol, row: saved.append(dict(row)))

    assert commands.refresh_market_watches(_settings(), force=True) == 0
    assert saved[-1]["last_price"] == 100.0
    assert saved[-1]["state"] == "IGNITION"
    assert saved[-1]["action"] == "WATCH"


def test_successful_material_delivery_advances_delivered_state(monkeypatch):
    old = commands._watch_row(_insight(price=100.0), 9)
    saved = []
    monkeypatch.setattr(commands, "get_watches", lambda: {"VVV": old})
    monkeypatch.setattr(
        commands,
        "analyze_market",
        lambda symbol: _insight(price=103.0, state="REVERSAL_RISK", action="WAIT", risk="HIGH"),
    )
    monkeypatch.setattr(
        commands,
        "send_tracked_telegram",
        lambda *a, **k: SimpleNamespace(delivered=True, message_id=77),
    )
    monkeypatch.setattr(commands, "_put_watch", lambda symbol, row: saved.append(dict(row)))

    assert commands.refresh_market_watches(_settings(), force=True) == 1
    assert saved[-1]["last_price"] == 103.0
    assert saved[-1]["state"] == "REVERSAL_RISK"


def test_corrupt_watch_registry_emits_degraded_alert(monkeypatch):
    sent = []
    error = RegistryCorruptionError(
        Path("/app/data/telegram_market_watches.json"),
        Path("/app/data/telegram_market_watches.json.corrupt-test"),
        "watch registry corrupt",
    )
    monkeypatch.setattr(
        commands,
        "get_watches",
        lambda: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(commands, "_send", lambda settings, text: sent.append(text) or True)

    assert commands.refresh_market_watches(_settings(), force=True) == 0
    assert len(sent) == 1
    assert "WATCH DEGRADED" in sent[0]


def test_material_watch_transition_always_uses_fresh_push(monkeypatch):
    old = commands._watch_row(_insight(price=100.0), 9)
    saved = []
    calls = []
    monkeypatch.setattr(commands, "get_watches", lambda: {"VVV": old})
    monkeypatch.setattr(commands, "analyze_market", lambda symbol: _insight(price=103.0))
    monkeypatch.setattr(
        commands,
        "send_tracked_telegram",
        lambda *a, **k: calls.append(k) or SimpleNamespace(delivered=True, message_id=88),
    )
    monkeypatch.setattr(commands, "_put_watch", lambda symbol, row: saved.append(dict(row)))

    assert commands.refresh_market_watches(_settings(), force=True) == 1
    assert calls[0]["success_status"] == "TRANSITION_PUSHED"
    assert saved[-1]["message_id"] == 88
    assert saved[-1]["last_price"] == 103.0
