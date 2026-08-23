from app.services import telegram_notifier as notifier


def setup_function() -> None:
    notifier._coin_market_rows.cache_clear()


def test_scan_pair_headline_includes_name_symbol_and_pair(monkeypatch):
    monkeypatch.setattr(
        notifier,
        "_resolved_coin_name",
        lambda symbol, kraken_price=None: "Venice Token" if symbol == "VVV" else None,
    )

    message = "🔎 OHM SCAN — VVVUSD\nPrice: 17.391\nState: EARLY_EXPANSION"

    assert notifier._enrich_alert_identity(message).splitlines()[0] == (
        "🔎 OHM SCAN — Venice Token (VVV) — VVVUSD"
    )


def test_trade_symbol_headline_includes_name_and_symbol(monkeypatch):
    monkeypatch.setattr(
        notifier,
        "_resolved_coin_name",
        lambda symbol, kraken_price=None: "Bitcoin" if symbol == "BTC" else None,
    )

    message = "🚨 OHM TRADE — BTC\nPrice: 116000\nAction: WATCH"

    assert notifier._enrich_alert_identity(message).splitlines()[0] == (
        "🚨 OHM TRADE — Bitcoin (BTC)"
    )


def test_duplicate_ticker_uses_price_only_when_one_candidate_is_clear(monkeypatch):
    monkeypatch.setattr(
        notifier,
        "_coin_market_rows",
        lambda symbol: (
            ("Venice Token", 17.40),
            ("Other VVV", 1.25),
            ("VVV Legacy", 0.08),
        ),
    )

    assert notifier._resolved_coin_name("VVV", 17.391) == "Venice Token"


def test_duplicate_ticker_stays_unresolved_when_two_prices_are_plausible(monkeypatch):
    monkeypatch.setattr(
        notifier,
        "_coin_market_rows",
        lambda symbol: (
            ("Candidate A", 17.40),
            ("Candidate B", 17.55),
        ),
    )

    assert notifier._resolved_coin_name("VVV", 17.391) is None


def test_ambiguous_or_unresolved_symbol_is_never_guessed(monkeypatch):
    monkeypatch.setattr(notifier, "_resolved_coin_name", lambda symbol, kraken_price=None: None)

    message = "🔎 OHM SCAN — VVVUSD\nPrice: 17.391\nState: EARLY_EXPANSION"

    assert notifier._enrich_alert_identity(message).splitlines()[0] == (
        "🔎 OHM SCAN — VVV (coin name unresolved) — VVVUSD"
    )


def test_non_alert_account_report_is_not_rewritten(monkeypatch):
    monkeypatch.setattr(
        notifier,
        "_resolved_coin_name",
        lambda symbol, kraken_price=None: "Kraken",
    )

    message = "💼 OHM POSITIONS — KRAKEN READ-ONLY\nVVV: 100 | ~$1,749.60 | VVVUSD"

    assert notifier._enrich_alert_identity(message) == message


def test_pre_enriched_alert_is_idempotent(monkeypatch):
    monkeypatch.setattr(
        notifier,
        "_resolved_coin_name",
        lambda symbol, kraken_price=None: "Wrong Name",
    )

    message = "🔎 OHM SCAN — Venice Token (VVV) — VVVUSD\nPrice: 17.391\nState: EARLY_EXPANSION"

    assert notifier._enrich_alert_identity(message) == message
