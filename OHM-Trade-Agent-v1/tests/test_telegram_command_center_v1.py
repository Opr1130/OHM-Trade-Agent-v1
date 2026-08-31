from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.scanner.models import MarketSnapshot
from app.services import telegram_callback_listener as listener
from app.services import telegram_command_center as commands
from app.services import telegram_market_insights as insights
from app.services.registry_io import RegistryCorruptionError


class FakePairsClient:
    def __init__(self, pairs):
        self.pairs = pairs

    def get_asset_pairs(self):
        return self.pairs


def _details(base, quote, altname, wsname):
    return {
        "base": base,
        "quote": quote,
        "altname": altname,
        "wsname": wsname,
        "status": "online",
    }


def _snapshot(**overrides):
    values = dict(
        symbol="VVVUSD",
        last_price=16.42,
        ema20=16.0,
        ema50=15.0,
        ema200=12.0,
        rsi=58.0,
        macd_line=0.2,
        macd_signal=0.1,
        macd_histogram=0.1,
        atr=0.8,
        atr_pct=4.8,
        volume_ratio=2.0,
        technical_score=80,
        trend="bullish",
        movement_data_status="AVAILABLE",
        bollinger_bandwidth_percentile=55.0,
        atr_percentile=60.0,
        movement_volume_ratio=2.0,
        confirmed_close=16.42,
        confirmed_price_change_1h_pct=-4.9,
        prior_24h_range_high=17.8,
        prior_24h_range_low=14.1,
        recent_24h_high=17.8,
        recent_24h_low=14.1,
        momentum_6h_pct=4.5,
        momentum_24h_pct=12.0,
        momentum_72h_pct=18.0,
        distance_to_24h_high_pct=8.4,
        distance_to_24h_low_pct=14.1,
        rolling_24h_upside_median_pct=3.0,
        rolling_24h_upside_p75_pct=6.0,
        rolling_24h_downside_median_pct=2.0,
        rolling_24h_downside_p75_pct=5.0,
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def _identity(symbol="VVV", quote="USD"):
    return insights.MarketIdentity(
        base_asset=symbol,
        quote_asset=quote,
        pair_id=f"{symbol}{quote}",
        altname=f"{symbol}{quote}",
        display_pair=f"{symbol}{quote}",
        ws_symbol=f"{symbol}/{quote}",
    )


def _ticker(last=16.43, bid=16.42, ask=16.44, volume=100_000.0):
    return {
        "last": last,
        "bid": bid,
        "ask": ask,
        "volume_24h": volume,
    }


def _insight(**overrides):
    values = dict(
        symbol="VVV",
        pair="VVVUSD",
        current_price=10.0,
        state="IGNITION",
        phase_score=35,
        risk="NORMAL",
        action="WATCH",
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
    values.update(overrides)
    return insights.MarketInsight(**values)


def _settings(**overrides):
    values = dict(
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="123",
        telegram_command_user_id=None,
        telegram_command_rate_limit_per_minute=12,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_parse_command_supports_bot_suffix_and_symbol_prefix():
    assert commands.parse_command("/scan@OHMBot $vvv") == ("scan", ("$vvv",))


def test_parse_non_command_is_ignored():
    assert commands.parse_command("scan VVV") is None


def test_parse_command_rejects_excessive_length():
    with pytest.raises(commands.TelegramCommandError):
        commands.parse_command("/scan " + "A" * 200)


def test_resolve_market_prefers_usd_for_bare_asset():
    client = FakePairsClient(
        {
            "VVVUSDT": _details("VVV", "USDT", "VVVUSDT", "VVV/USDT"),
            "VVVUSD": _details("VVV", "ZUSD", "VVVUSD", "VVV/USD"),
        }
    )
    result = insights.resolve_market("vvv", client)
    assert result.display_pair == "VVVUSD"


def test_resolve_market_preserves_explicit_usdt():
    client = FakePairsClient(
        {
            "VVVUSDT": _details("VVV", "USDT", "VVVUSDT", "VVV/USDT"),
            "VVVUSD": _details("VVV", "ZUSD", "VVVUSD", "VVV/USD"),
        }
    )
    assert insights.resolve_market("VVV/USDT", client).quote_asset == "USDT"


def test_resolve_market_canonicalizes_xbt_to_btc():
    client = FakePairsClient(
        {"XXBTZUSD": _details("XXBT", "ZUSD", "XBTUSD", "XBT/USD")}
    )
    result = insights.resolve_market("xbt", client)
    assert result.base_asset == "BTC"
    assert result.display_pair == "BTCUSD"


@pytest.mark.parametrize("symbol", ["XTZ", "REZ"])
def test_resolve_market_does_not_strip_modern_z_suffix(symbol):
    client = FakePairsClient(
        {f"{symbol}USD": _details(symbol, "ZUSD", f"{symbol}USD", f"{symbol}/USD")}
    )
    assert insights.resolve_market(symbol, client).base_asset == symbol


def test_resolve_market_rejects_unknown_asset():
    with pytest.raises(insights.MarketResolutionError):
        insights.resolve_market("NOPE", FakePairsClient({}))


def test_vvv_style_reversal_is_flagged_wait():
    result = insights.build_market_insight(_identity(), _ticker(), _snapshot(), None)
    assert result.state == "REVERSAL_RISK"
    assert result.action == "WAIT"
    assert result.risk == "HIGH"


def test_reacceleration_overlay_is_shadow_advisory_not_buy():
    snapshot = _snapshot(
        confirmed_price_change_1h_pct=1.4,
        momentum_6h_pct=3.2,
        momentum_24h_pct=8.0,
        distance_to_24h_high_pct=2.0,
        movement_volume_ratio=1.6,
        volume_ratio=1.6,
    )
    result = insights.build_market_insight(_identity(), _ticker(), snapshot, None)
    assert result.state == "REACCELERATION_CANDIDATE"
    assert result.action == "WATCH_REACCELERATION"
    assert "BUY" not in result.action


@pytest.mark.parametrize(
    "ticker",
    [
        _ticker(last=float("nan")),
        _ticker(bid=float("inf")),
        _ticker(bid=20.0, ask=19.0),
        _ticker(volume=0.0),
    ],
)
def test_nonfinite_or_crossed_ticker_fails_closed(ticker):
    with pytest.raises(insights.MarketInsightUnavailable):
        insights.build_market_insight(_identity(), ticker, _snapshot(), None)


def test_scan_message_labels_ranges_as_historical_not_forecasts():
    message = insights.format_market_insight(_insight())
    assert "Potential (historical 24h)" in message
    assert "Historical ranges are not forecasts" in message
    assert "not probability" in message


def test_watch_change_requires_material_signature_or_two_percent_price_move():
    old = commands._watch_row(_insight(current_price=100.0), 10)
    assert not commands._is_material_watch_change(old, _insight(current_price=101.9))
    assert commands._is_material_watch_change(old, _insight(current_price=102.01))
    assert commands._is_material_watch_change(old, _insight(current_price=100.1, state="REVERSAL_RISK", action="WAIT"))


def test_watch_registry_round_trip_and_delete(tmp_path):
    path = tmp_path / "watch.json"
    commands._put_watch("VVV", commands._watch_row(_insight(), 42), path)
    assert commands.get_watches(path)["VVV"]["message_id"] == 42
    assert commands._delete_watch("VVV", path)
    assert commands.get_watches(path) == {}


def test_corrupt_watch_registry_fails_loud_and_quarantines(tmp_path):
    path = tmp_path / "watch.json"
    path.write_text('{"watches":{"VVV":{"last_price":NaN}}}', encoding="utf-8")
    with pytest.raises(RegistryCorruptionError):
        commands.get_watches(path)
    assert not path.exists()
    assert list(tmp_path.glob("watch.json.corrupt-*"))


def test_stale_far_external_order_is_flagged_and_full_id_is_redacted():
    now = commands.time.time()
    open_orders = {
        "OABCDEFGHIJK12345678": {
            "descr": {"pair": "INITUSD", "type": "sell", "ordertype": "limit", "price": "0.53"},
            "opentm": now - 200 * 86400,
        }
    }

    class Client:
        def get_ticker(self, pair):
            return {"last": 0.0552}

    text = commands.format_orders_report(open_orders, [], Client())
    assert "STALE — EXIT REVIEW" in text
    assert "EXTERNAL" in text
    assert "OABCDEFGHIJK12345678" not in text
    assert "12345678" in text


def test_near_limit_order_is_flagged_near_limit():
    open_orders = {
        "OID": {
            "descr": {"pair": "CAPUSD", "type": "sell", "ordertype": "limit", "price": "10.2"},
            "opentm": commands.time.time(),
        }
    }

    class Client:
        def get_ticker(self, pair):
            return {"last": 10.0}

    assert "NEAR LIMIT" in commands.format_orders_report(open_orders, [], Client())


def test_balance_aggregation_canonicalizes_aliases_and_marks_bad_rows():
    balances, invalid = commands._aggregate_balances({"XXBT": 1.0, "XBT": 2.0, "ETH": float("nan")})
    assert balances["BTC"] == 3.0
    assert invalid is True


def test_private_client_asserts_read_only_before_use(monkeypatch):
    events = []

    class Private:
        enabled = True

        def assert_read_only(self):
            events.append("assert")

    monkeypatch.setattr(commands, "KrakenPrivateClient", Private)
    commands._private_client()
    assert events == ["assert"]


def test_listener_authorization_honors_optional_user_id():
    settings = _settings(telegram_command_user_id="777")
    assert listener._authorized_actor(chat_id="123", sender_id="777", settings=settings)
    assert not listener._authorized_actor(chat_id="123", sender_id="778", settings=settings)
    assert not listener._authorized_actor(chat_id="999", sender_id="777", settings=settings)


def test_unauthorized_command_is_not_dispatched(monkeypatch):
    called = []
    monkeypatch.setattr(listener, "get_settings", lambda: _settings(telegram_command_user_id="777"))
    monkeypatch.setattr(listener, "process_command_message", lambda *a, **k: called.append(True))
    listener.process_message({"message": {"text": "/scan VVV", "chat": {"id": 123}, "from": {"id": 778}}})
    assert called == []


def test_non_command_message_is_ignored_before_rate_limit(monkeypatch):
    monkeypatch.setattr(listener, "get_settings", lambda: _settings())
    monkeypatch.setattr(listener, "_command_rate_allowed", lambda settings: (_ for _ in ()).throw(AssertionError("rate called")))
    listener.process_message({"message": {"text": "hello", "chat": {"id": 123}, "from": {"id": 123}}})


def test_rate_limit_blocks_command_dispatch(monkeypatch):
    sent = []
    called = []
    monkeypatch.setattr(listener, "get_settings", lambda: _settings())
    monkeypatch.setattr(listener, "_command_rate_allowed", lambda settings: False)
    monkeypatch.setattr(listener, "send_telegram_message", lambda *args: sent.append(args[-1]) or True)
    monkeypatch.setattr(listener, "process_command_message", lambda *a, **k: called.append(True))
    listener.process_message({"message": {"text": "/scan VVV", "chat": {"id": 123}, "from": {"id": 123}}})
    assert called == []
    assert any("RATE LIMIT" in item for item in sent)


def test_help_command_does_not_call_market_or_private_data(monkeypatch):
    sent = []
    monkeypatch.setattr(commands, "send_telegram_message", lambda *args: sent.append(args[-1]) or True)
    monkeypatch.setattr(commands, "analyze_market", lambda *a, **k: (_ for _ in ()).throw(AssertionError("market called")))
    assert commands.process_command_message({"message": {"text": "/help"}}, settings=_settings())
    assert "OHM TELEGRAM COMMANDS" in sent[0]


def test_scan_failure_returns_safe_message(monkeypatch):
    sent = []
    monkeypatch.setattr(commands, "send_telegram_message", lambda *args: sent.append(args[-1]) or True)
    monkeypatch.setattr(commands, "analyze_market", lambda *a, **k: (_ for _ in ()).throw(insights.MarketInsightUnavailable("timeout")))
    commands.process_command_message({"message": {"text": "/scan VVV"}}, settings=_settings())
    assert any("SCAN" in item and "timeout" in item and "OHM SCAN" not in item for item in sent)


def test_too_long_command_returns_error_instead_of_crashing_listener(monkeypatch):
    sent = []
    monkeypatch.setattr(commands, "send_telegram_message", lambda *args: sent.append(args[-1]) or True)
    commands.process_command_message(
        {"message": {"text": "/scan " + "A" * 200}},
        settings=_settings(),
    )
    assert any("too long" in item.lower() for item in sent)


def test_watch_does_not_persist_if_telegram_send_fails(monkeypatch):
    monkeypatch.setattr(commands, "analyze_market", lambda symbol: _insight())
    monkeypatch.setattr(commands, "get_watches", lambda: {})
    monkeypatch.setattr(commands, "send_telegram_message_with_id", lambda *a, **k: None)
    persisted = []
    monkeypatch.setattr(commands, "_put_watch", lambda *a, **k: persisted.append(True))
    commands._handle_watch(_settings(), "VVV")
    assert persisted == []


def test_existing_watch_edits_same_message_and_keeps_id(monkeypatch):
    insight = _insight()
    existing = {"VVV": commands._watch_row(insight, 77)}
    monkeypatch.setattr(commands, "analyze_market", lambda symbol: insight)
    monkeypatch.setattr(commands, "get_watches", lambda: existing)
    monkeypatch.setattr(commands, "edit_telegram_message", lambda *a, **k: True)
    monkeypatch.setattr(commands, "send_telegram_message_with_id", lambda *a, **k: (_ for _ in ()).throw(AssertionError("new send")))
    saved = []
    monkeypatch.setattr(commands, "_put_watch", lambda symbol, row: saved.append(row))
    monkeypatch.setattr(commands, "_send", lambda *a, **k: True)
    commands._handle_watch(_settings(), "VVV")
    assert saved[0]["message_id"] == 77


def test_refresh_watch_does_not_edit_on_small_nonmaterial_change(monkeypatch):
    old = commands._watch_row(_insight(current_price=100.0), 9)
    old["last_checked_at"] = "2000-01-01T00:00:00+00:00"
    monkeypatch.setattr(commands, "get_watches", lambda: {"VVV": old})
    monkeypatch.setattr(commands, "analyze_market", lambda symbol: _insight(current_price=101.0))
    monkeypatch.setattr(commands, "send_tracked_telegram", lambda *a, **k: (_ for _ in ()).throw(AssertionError("send")))
    monkeypatch.setattr(commands, "_put_watch", lambda *a, **k: None)
    assert commands.refresh_market_watches(_settings(), force=True) == 0


def test_refresh_watch_pushes_fresh_card_on_state_change(monkeypatch):
    old = commands._watch_row(_insight(current_price=100.0), 9)
    calls = []
    monkeypatch.setattr(commands, "get_watches", lambda: {"VVV": old})
    monkeypatch.setattr(commands, "analyze_market", lambda symbol: _insight(current_price=100.1, state="REVERSAL_RISK", action="WAIT", risk="HIGH"))
    monkeypatch.setattr(
        commands,
        "send_tracked_telegram",
        lambda *a, **k: calls.append(k) or SimpleNamespace(delivered=True, message_id=88),
    )
    monkeypatch.setattr(commands, "_put_watch", lambda *a, **k: None)
    assert commands.refresh_market_watches(_settings(), force=True) == 1
    assert calls[0]["success_status"] == "TRANSITION_PUSHED"


def test_third_consecutive_watch_failure_sends_single_degraded_alert(monkeypatch):
    row = commands._watch_row(_insight(), 9, failures=2)
    monkeypatch.setattr(commands, "get_watches", lambda: {"VVV": row})
    monkeypatch.setattr(commands, "analyze_market", lambda symbol: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(commands, "_put_watch", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(commands, "_send", lambda settings, text: sent.append(text) or True)
    commands.refresh_market_watches(_settings(), force=True)
    assert len(sent) == 1
    assert "three consecutive" in sent[0]


def test_config_accepts_optional_telegram_user_boundary(monkeypatch):
    from app.core.config import Settings

    settings = Settings(
        webhook_secret="123456789012",
        telegram_command_user_id="777",
        telegram_command_rate_limit_per_minute=5,
    )
    assert settings.telegram_command_user_id == "777"
    assert settings.telegram_command_rate_limit_per_minute == 5
