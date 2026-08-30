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


def test_scan_movers_source_has_actionable_only_guard():
    import inspect

    source = inspect.getsource(scan_movers.main)
    guard = 'opip_actionable_only_alerts'
    telegram_send = 'send_tracked_telegram('
    assert guard in source
    assert telegram_send in source


def test_settings_default_actionable_only():
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["opip_actionable_only_alerts"].default is True
