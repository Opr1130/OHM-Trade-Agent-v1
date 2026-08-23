from __future__ import annotations

from types import SimpleNamespace

from app.services import telegram_callback_listener as listener
from app.services import telegram_command_center as commands


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
    assert commands.parse_command("/CAP") == ("scan", ("CAP",))


def test_plain_scan_phrase_normalizes_to_scan():
    assert commands.parse_command("scan CAP") == ("scan", ("CAP",))
    assert commands.parse_command("Scan CAP") == ("scan", ("CAP",))


def test_reserved_unknown_command_is_not_reinterpreted_as_asset():
    assert commands.parse_command("/help") == ("help", ())
    assert commands.parse_command("/orders") == ("orders", ())


def test_plain_chat_message_is_still_ignored():
    assert commands.parse_command("hello CAP") is None


def test_listener_accepts_plain_scan_phrase(monkeypatch):
    queued = []
    monkeypatch.setattr(listener, "get_settings", lambda: _settings())
    monkeypatch.setattr(listener, "_command_rate_allowed", lambda settings: True)
    monkeypatch.setattr(listener, "_queue_command", lambda update, settings: queued.append(update) or True)

    listener.process_message(_update("Scan CAP"))

    assert len(queued) == 1


def test_listener_ignores_unrelated_plain_message(monkeypatch):
    monkeypatch.setattr(listener, "get_settings", lambda: _settings())
    monkeypatch.setattr(listener, "_command_rate_allowed", lambda settings: (_ for _ in ()).throw(AssertionError("rate called")))

    listener.process_message(_update("CAP looks good"))
