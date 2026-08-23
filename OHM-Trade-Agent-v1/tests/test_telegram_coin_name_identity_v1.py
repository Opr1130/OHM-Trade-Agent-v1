from app.services import telegram_notifier as notifier


def setup_function() -> None:
    notifier._resolved_coin_name.cache_clear()


def test_scan_pair_headline_includes_name_symbol_and_pair(monkeypatch):
    monkeypatch.setattr(notifier, "_resolved_coin_name", lambda symbol: "Venice Token" if symbol == "VVV" else None)

    message = "🔎 OHM SCAN — VVVUSD\nState: EARLY_EXPANSION"

    assert notifier._enrich_alert_identity(message).splitlines()[0] == (
        "🔎 OHM SCAN — Venice Token (VVV) — VVVUSD"
    )


def test_trade_symbol_headline_includes_name_and_symbol(monkeypatch):
    monkeypatch.setattr(notifier, "_resolved_coin_name", lambda symbol: "Bitcoin" if symbol == "BTC" else None)

    message = "🚨 OHM TRADE — BTC\nAction: WATCH"

    assert notifier._enrich_alert_identity(message).splitlines()[0] == (
        "🚨 OHM TRADE — Bitcoin (BTC)"
    )


def test_ambiguous_or_unresolved_symbol_is_never_guessed(monkeypatch):
    monkeypatch.setattr(notifier, "_resolved_coin_name", lambda symbol: None)

    message = "🔎 OHM SCAN — VVVUSD\nState: EARLY_EXPANSION"

    assert notifier._enrich_alert_identity(message).splitlines()[0] == (
        "🔎 OHM SCAN — VVV (coin name unresolved) — VVVUSD"
    )


def test_non_alert_account_report_is_not_rewritten(monkeypatch):
    monkeypatch.setattr(notifier, "_resolved_coin_name", lambda symbol: "Kraken")

    message = "💼 OHM POSITIONS — KRAKEN READ-ONLY\nVVV: 100 | ~$1,749.60 | VVVUSD"

    assert notifier._enrich_alert_identity(message) == message


def test_pre_enriched_alert_is_idempotent(monkeypatch):
    monkeypatch.setattr(notifier, "_resolved_coin_name", lambda symbol: "Wrong Name")

    message = "🔎 OHM SCAN — Venice Token (VVV) — VVVUSD\nState: EARLY_EXPANSION"

    assert notifier._enrich_alert_identity(message) == message
