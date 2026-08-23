from __future__ import annotations

from types import SimpleNamespace

from app.services import telegram_command_center as commands


def _settings():
    return SimpleNamespace(
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="123",
        telegram_command_user_id=None,
        telegram_command_rate_limit_per_minute=12,
    )


def _insight(symbol: str = "VVV", price: float = 10.0):
    from app.services.telegram_market_insights import MarketInsight

    return MarketInsight(
        symbol=symbol,
        pair=f"{symbol}USD",
        current_price=price,
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


def test_unwatch_uses_local_identity_and_does_not_require_kraken(monkeypatch):
    removed = []
    sent = []
    monkeypatch.setattr(
        commands,
        "resolve_market",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Kraken resolver called")),
    )
    monkeypatch.setattr(commands, "_delete_watch", lambda symbol: removed.append(symbol) or True)
    monkeypatch.setattr(commands, "_send", lambda settings, text: sent.append(text) or True)

    commands._handle_unwatch(_settings(), "xbt/usd")

    assert removed == ["BTC"]
    assert "BTC removed" in sent[0]


def test_automatic_watch_refresh_processes_at_most_two_due_symbols(monkeypatch):
    rows = {}
    for index, symbol in enumerate(("AAA", "BBB", "CCC", "DDD"), start=1):
        row = commands._watch_row(_insight(symbol, 10.0 + index), index)
        row["last_checked_at"] = "2000-01-01T00:00:00+00:00"
        rows[symbol] = row

    analyzed = []
    monkeypatch.setattr(commands, "get_watches", lambda: rows)
    monkeypatch.setattr(
        commands,
        "analyze_market",
        lambda symbol: analyzed.append(symbol) or _insight(symbol, 20.0),
    )
    monkeypatch.setattr(commands, "edit_telegram_message", lambda *a, **k: True)
    monkeypatch.setattr(commands, "_put_watch", lambda *a, **k: None)

    commands.refresh_market_watches(_settings(), force=False)

    assert analyzed == ["AAA", "BBB"]


def test_forced_watch_refresh_can_cover_all_symbols_for_operator_checks(monkeypatch):
    rows = {}
    for index, symbol in enumerate(("AAA", "BBB", "CCC"), start=1):
        rows[symbol] = commands._watch_row(_insight(symbol), index)

    analyzed = []
    monkeypatch.setattr(commands, "get_watches", lambda: rows)
    monkeypatch.setattr(
        commands,
        "analyze_market",
        lambda symbol: analyzed.append(symbol) or _insight(symbol),
    )
    monkeypatch.setattr(commands, "_put_watch", lambda *a, **k: None)

    commands.refresh_market_watches(_settings(), force=True)

    assert analyzed == ["AAA", "BBB", "CCC"]


def test_positions_fetches_asset_pair_directory_only_once(monkeypatch):
    events = []

    class Private:
        enabled = True

        def assert_read_only(self):
            events.append("readonly")

        def get_balance(self):
            return {"CAP": 10.0, "INIT": 20.0, "WCT": 30.0}

        def get_open_positions(self):
            return {}

    class Public:
        def __init__(self, *args, **kwargs):
            pass

        def get_asset_pairs(self):
            events.append("pairs")
            return {
                "CAPUSD": {"base": "CAP", "quote": "ZUSD", "altname": "CAPUSD", "wsname": "CAP/USD", "status": "online"},
                "INITUSD": {"base": "INIT", "quote": "ZUSD", "altname": "INITUSD", "wsname": "INIT/USD", "status": "online"},
                "WCTUSD": {"base": "WCT", "quote": "ZUSD", "altname": "WCTUSD", "wsname": "WCT/USD", "status": "online"},
            }

        def get_ticker(self, pair):
            return {"last": {"CAPUSD": 1.0, "INITUSD": 2.0, "WCTUSD": 3.0}[pair]}

    monkeypatch.setattr(commands, "KrakenPrivateClient", Private)
    monkeypatch.setattr(commands, "KrakenClient", Public)
    monkeypatch.setattr(commands, "get_active_trades", lambda: [])

    report = commands.positions_report()

    assert events.count("readonly") == 1
    assert events.count("pairs") == 1
    assert "CAP:" in report and "INIT:" in report and "WCT:" in report


def test_local_unwatch_rejects_malformed_symbol_without_registry_mutation(monkeypatch):
    mutated = []
    sent = []
    monkeypatch.setattr(commands, "_delete_watch", lambda symbol: mutated.append(symbol) or True)
    monkeypatch.setattr(commands, "_send", lambda settings, text: sent.append(text) or True)

    commands._handle_unwatch(_settings(), "../../etc/passwd")

    assert mutated == []
    assert "Invalid coin symbol" in sent[0]
