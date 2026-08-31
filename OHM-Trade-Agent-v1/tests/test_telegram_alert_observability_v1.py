from types import SimpleNamespace

from app.jobs import scan_opportunities as scan


def _settings(**overrides):
    values = {
        "telegram_enabled": True,
        "telegram_bot_token": "token",
        "telegram_chat_id": "chat",
        "price_movement_mode": "alert",
        "price_movement_alert_cooldown_seconds": 21600,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_movement_delivery_is_not_attempted_in_shadow(monkeypatch):
    called = []
    monkeypatch.setattr(
        scan,
        "send_price_movement_update",
        lambda *args, **kwargs: called.append(True) or True,
    )
    sent, failed = scan._send_movement_notification(
        {"symbol": "BTCUSD", "stage": "WATCH"},
        _settings(price_movement_mode="shadow"),
    )
    assert (sent, failed) == (False, False)
    assert called == []


def test_movement_delivery_success_is_countable(monkeypatch):
    monkeypatch.setattr(scan, "send_price_movement_update", lambda *a, **k: True)
    assert scan._send_movement_notification(
        {"symbol": "BTCUSD", "stage": "WATCH"},
        _settings(),
    ) == (True, False)


def test_movement_delivery_exception_is_surfaced_as_failure(monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(scan, "send_price_movement_update", fail)
    with caplog.at_level("ERROR"):
        sent, failed = scan._send_movement_notification(
            {"symbol": "BTCUSD", "stage": "READY"},
            _settings(),
        )
    assert (sent, failed) == (False, True)
    assert "Price movement Telegram delivery failed" in caplog.text


def test_telegram_delivery_ready_requires_enable_token_and_chat():
    assert scan._telegram_delivery_ready(_settings()) is True
    assert scan._telegram_delivery_ready(_settings(telegram_enabled=False)) is False
    assert scan._telegram_delivery_ready(_settings(telegram_bot_token=None)) is False
    assert scan._telegram_delivery_ready(_settings(telegram_chat_id=None)) is False
