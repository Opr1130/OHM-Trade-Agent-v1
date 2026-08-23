from __future__ import annotations

from types import SimpleNamespace

from app.services import telegram_callback_listener as listener


def _settings():
    return SimpleNamespace(
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="123",
        telegram_command_user_id=None,
        telegram_command_rate_limit_per_minute=12,
    )


def _update(text: str):
    return {
        "message": {
            "text": text,
            "chat": {"id": 123},
            "from": {"id": 123},
        }
    }


def test_slash_asset_shorthand_normalizes_to_scan():
    assert listener._normalize_mobile_command_text("/CAP") == "/scan CAP"
    assert listener._normalize_mobile_command_text("/cap") == "/scan CAP"


def test_plain_scan_phrase_normalizes_to_scan():
    assert listener._normalize_mobile_command_text("scan CAP") == "/scan CAP"
    assert listener._normalize_mobile_command_text("Scan CAP") == "/scan CAP"


def test_reserved_commands_are_preserved():
    assert listener._normalize_mobile_command_text("/help") == "/help"
    assert listener._normalize_mobile_command_text("/orders") == "/orders"
    assert listener._normalize_mobile_command_text("/scan CAP") == "/scan CAP"


def test_plain_chat_message_is_still_ignored():
    assert listener._normalize_mobile_command_text("hello CAP") is None
    assert listener._normalize_mobile_command_text("CAP looks good") is None


def test_malformed_or_multiword_slash_shorthand_is_not_rewritten():
    assert listener._normalize_mobile_command_text("/CAP extra") == "/CAP extra"
    assert listener._normalize_mobile_command_text("/../../etc/passwd") == "/../../etc/passwd"


def test_listener_accepts_plain_scan_phrase(monkeypatch):
    queued = []
    monkeypatch.setattr(listener, "get_settings", lambda: _settings())
    monkeypatch.setattr(listener, "_command_rate_allowed", lambda settings: True)
    monkeypatch.setattr(listener, "_queue_command", lambda update, settings: queued.append(update) or True)

    listener.process_message(_update("Scan CAP"))

    assert len(queued) == 1
    assert queued[0]["message"]["text"] == "/scan CAP"


def test_listener_accepts_slash_asset_shorthand(monkeypatch):
    queued = []
    monkeypatch.setattr(listener, "get_settings", lambda: _settings())
    monkeypatch.setattr(listener, "_command_rate_allowed", lambda settings: True)
    monkeypatch.setattr(listener, "_queue_command", lambda update, settings: queued.append(update) or True)

    listener.process_message(_update("/CAP"))

    assert len(queued) == 1
    assert queued[0]["message"]["text"] == "/scan CAP"


def test_listener_ignores_unrelated_plain_message_before_rate_limit(monkeypatch):
    monkeypatch.setattr(listener, "get_settings", lambda: _settings())
    monkeypatch.setattr(listener, "_command_rate_allowed", lambda settings: (_ for _ in ()).throw(AssertionError("rate called")))

    listener.process_message(_update("CAP looks good"))
