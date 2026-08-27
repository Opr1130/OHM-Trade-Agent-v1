from __future__ import annotations

from types import SimpleNamespace

from app.services import telegram_callback_listener as listener
from app.services import telegram_command_center as commands
from app.services.paper_trade_control import PaperTradeControl
from app.services.paper_trade_models import PaperAccountSummary


def settings(**overrides):
    values = dict(
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="123",
        telegram_command_user_id="777",
        telegram_command_rate_limit_per_minute=12,
        paper_trade_starting_equity=10_000.0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def summary():
    return PaperAccountSummary(
        starting_equity=10_000.0,
        realized_net_pnl=125.5,
        closed_equity=10_125.5,
        reserved_capital=1_004.0,
        available_capital=9_121.5,
        pending_entries=1,
        open_positions=0,
        closed_trades=4,
        cancelled_setups=2,
        unresolved_trades=1,
    )


def test_paper_status_reports_simulation_only_and_does_not_mutate(monkeypatch):
    sent = []
    mutations = []
    monkeypatch.setattr(
        commands,
        "get_paper_trade_control",
        lambda: PaperTradeControl(
            enabled=True,
            updated_at="2026-08-27T22:00:00+00:00",
            updated_by="TEST",
            status="OK",
        ),
    )
    monkeypatch.setattr(commands, "account_summary", lambda equity: summary())
    monkeypatch.setattr(
        commands,
        "set_paper_trade_enabled",
        lambda *a, **k: mutations.append((a, k)),
    )
    monkeypatch.setattr(commands, "_send", lambda s, text: sent.append(text) or True)

    assert commands.process_command_message(
        {"message": {"text": "/paper status"}},
        settings=settings(),
    )

    assert mutations == []
    assert "PAPER TRADE V1" in sent[0]
    assert "Mode: ON" in sent[0]
    assert "Exchange writes: DISABLED BY ARCHITECTURE" in sent[0]
    assert "Closed/Unresolved: 4/1" in sent[0]


def test_paper_on_changes_only_paper_control(monkeypatch):
    sent = []
    calls = []
    monkeypatch.setattr(
        commands,
        "set_paper_trade_enabled",
        lambda enabled, **kwargs: (
            calls.append((enabled, kwargs)),
            PaperTradeControl(
                enabled=enabled,
                updated_at="2026-08-27T22:00:00+00:00",
                updated_by=kwargs["updated_by"],
                status="OK",
            ),
        )[1],
    )
    monkeypatch.setattr(commands, "_send", lambda s, text: sent.append(text) or True)

    commands.process_command_message(
        {"message": {"text": "/paper on"}},
        settings=settings(),
    )

    assert calls == [
        (
            True,
            {"updated_by": "TELEGRAM_AUTHORIZED_OPERATOR"},
        )
    ]
    assert "PAPER TRADE V1 — ON" in sent[0]
    assert "Kraken execution authority: NONE" in sent[0]


def test_paper_off_changes_only_paper_control(monkeypatch):
    sent = []
    calls = []
    monkeypatch.setattr(
        commands,
        "set_paper_trade_enabled",
        lambda enabled, **kwargs: (
            calls.append((enabled, kwargs)),
            PaperTradeControl(
                enabled=enabled,
                updated_at="2026-08-27T22:00:00+00:00",
                updated_by=kwargs["updated_by"],
                status="OK",
            ),
        )[1],
    )
    monkeypatch.setattr(commands, "_send", lambda s, text: sent.append(text) or True)

    commands.process_command_message(
        {"message": {"text": "/paper off"}},
        settings=settings(),
    )

    assert calls[0][0] is False
    assert "PAPER TRADE V1 — OFF" in sent[0]
    assert "already-open paper positions continue" in sent[0]
    assert "Kraken execution authority: NONE" in sent[0]


def test_paper_invalid_action_fails_without_mutation(monkeypatch):
    sent = []
    calls = []
    monkeypatch.setattr(
        commands,
        "set_paper_trade_enabled",
        lambda *a, **k: calls.append(True),
    )
    monkeypatch.setattr(commands, "_send", lambda s, text: sent.append(text) or True)

    commands.process_command_message(
        {"message": {"text": "/paper maybe"}},
        settings=settings(),
    )

    assert calls == []
    assert "Usage: /paper status|on|off" in sent[0]


def test_listener_reserves_paper_command_and_preserves_it():
    assert "paper" in listener._RESERVED_COMMANDS
    assert listener._normalize_mobile_command_text("/paper on") == "/paper on"


def test_unauthorized_actor_cannot_queue_paper_control(monkeypatch):
    queued = []
    monkeypatch.setattr(listener, "get_settings", lambda: settings())
    monkeypatch.setattr(
        listener,
        "_queue_command",
        lambda update, config: queued.append(update) or True,
    )
    monkeypatch.setattr(listener, "_command_rate_allowed", lambda config: True)

    listener.process_message(
        {
            "message": {
                "text": "/paper on",
                "chat": {"id": 123},
                "from": {"id": 778},
            }
        }
    )

    assert queued == []


def test_authorized_actor_can_queue_paper_control(monkeypatch):
    queued = []
    monkeypatch.setattr(listener, "get_settings", lambda: settings())
    monkeypatch.setattr(
        listener,
        "_queue_command",
        lambda update, config: queued.append(update) or True,
    )
    monkeypatch.setattr(listener, "_command_rate_allowed", lambda config: True)

    listener.process_message(
        {
            "message": {
                "text": "/paper on",
                "chat": {"id": 123},
                "from": {"id": 777},
            }
        }
    )

    assert len(queued) == 1
    assert queued[0]["message"]["text"] == "/paper on"
