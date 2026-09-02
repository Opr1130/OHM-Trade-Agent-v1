from types import SimpleNamespace

from app.jobs import scan_movers, scan_opportunities


def test_actionable_only_suppresses_price_movement_telegram(monkeypatch):
    settings = SimpleNamespace(
        opip_actionable_only_alerts=True,
        price_movement_mode="alert",
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )
    calls = []
    monkeypatch.setattr(
        scan_opportunities,
        "send_price_movement_update",
        lambda *args, **kwargs: calls.append(kwargs) or True,
    )

    sent, failed = scan_opportunities._send_movement_notification(
        {"symbol": "SOLUSD", "stage": "READY"},
        settings,
    )

    assert sent is False
    assert failed is False
    assert calls == []


def test_legacy_movement_alert_path_remains_opt_in(monkeypatch):
    settings = SimpleNamespace(
        opip_actionable_only_alerts=False,
        price_movement_mode="alert",
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="chat",
        price_movement_alert_cooldown_seconds=21600,
    )
    calls = []
    monkeypatch.setattr(
        scan_opportunities,
        "send_price_movement_update",
        lambda *args, **kwargs: calls.append(kwargs) or True,
    )

    sent, failed = scan_opportunities._send_movement_notification(
        {"symbol": "SOLUSD", "stage": "READY"},
        settings,
    )

    assert sent is True
    assert failed is False
    assert len(calls) == 1


def test_scan_movers_actionable_only_blocks_watch_transport():
    settings = SimpleNamespace(
        opip_actionable_only_alerts=True,
        price_movement_mode="alert",
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )
    assert scan_movers._watch_telegram_enabled(settings) is False


def test_scan_movers_legacy_watch_transport_is_explicit_opt_in():
    settings = SimpleNamespace(
        opip_actionable_only_alerts=False,
        price_movement_mode="alert",
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )
    assert scan_movers._watch_telegram_enabled(settings) is True


def test_settings_default_actionable_only():
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["opip_actionable_only_alerts"].default is True

def test_settings_default_explicit_early_watch_channel_is_dark():
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["opip_early_watch_alerts_enabled"].default is False


def test_explicit_early_watch_can_run_with_actionable_only_enabled():
    settings = SimpleNamespace(
        opip_actionable_only_alerts=True,
        opip_early_watch_alerts_enabled=True,
        price_movement_mode="shadow",
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )

    assert scan_movers._early_watch_telegram_enabled(settings) is True
    # The legacy broad-watch transport stays suppressed.
    assert scan_movers._watch_telegram_enabled(settings) is False


def test_explicit_early_watch_requires_telegram_credentials():
    settings = SimpleNamespace(
        opip_actionable_only_alerts=True,
        opip_early_watch_alerts_enabled=True,
        price_movement_mode="shadow",
        telegram_enabled=True,
        telegram_bot_token=None,
        telegram_chat_id="chat",
    )

    assert scan_movers._early_watch_telegram_enabled(settings) is False


def test_early_watch_card_never_authorizes_entry():
    signal = SimpleNamespace(
        symbol="SOLUSD",
        stage="BREAKOUT_CANDIDATE",
        reference_price=100.0,
        detection_timeframe="1H",
        momentum_1h_pct=2.0,
        momentum_6h_pct=4.0,
        momentum_24h_pct=8.0,
        momentum_state="ACCELERATING",
        continuation_confidence=80,
        entry_quality=75,
        entry_recommendation="BREAKOUT_ENTRY_POSSIBLE",
        relative_volume=2.0,
        distance_to_24h_high_pct=1.0,
        liquidity_24h_usd_approx=1_000_000.0,
        extended_move=False,
        reasons=("momentum confirmed",),
    )

    message = scan_movers._compact_card(signal)

    assert "Action: WATCH ONLY — no entry is authorized" in message
    assert "REVIEW ENTRY" not in message

