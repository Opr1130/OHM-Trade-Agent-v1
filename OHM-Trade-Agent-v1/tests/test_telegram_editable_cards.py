from types import SimpleNamespace

from app.jobs.scan_movers import _compact_card, _deliver_existing_card_update
from app.services import telegram_notifier


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_send_with_id_returns_telegram_message_id(monkeypatch):
    calls = []

    def post(url, data, timeout):
        calls.append((url, data, timeout))
        return FakeResponse({"ok": True, "result": {"message_id": 4321}})

    monkeypatch.setattr(telegram_notifier.httpx, "post", post)

    message_id = telegram_notifier.send_telegram_message_with_id("secret", "chat", "hello")

    assert message_id == 4321
    assert calls[0][0].endswith("/sendMessage")
    assert "secret" in calls[0][0]


def test_edit_message_uses_edit_message_text(monkeypatch):
    calls = []

    def post(url, data, timeout):
        calls.append((url, data, timeout))
        return FakeResponse({"ok": True, "result": {"message_id": 4321}})

    monkeypatch.setattr(telegram_notifier.httpx, "post", post)

    assert telegram_notifier.edit_telegram_message("secret", "chat", 4321, "updated") is True
    assert calls[0][0].endswith("/editMessageText")
    assert calls[0][1]["message_id"] == 4321
    assert calls[0][1]["text"] == "updated"


def test_legacy_boolean_sender_does_not_require_message_id(monkeypatch):
    monkeypatch.setattr(
        telegram_notifier.httpx,
        "post",
        lambda *args, **kwargs: FakeResponse({"ok": True, "result": True}),
    )

    assert telegram_notifier.send_telegram_message("secret", "chat", "hello") is True


def test_compact_mover_card_is_short_and_decision_focused():
    signal = SimpleNamespace(
        symbol="METUSD",
        stage="EARLY_EXPANSION",
        momentum_1h_pct=1.8,
        momentum_6h_pct=3.4,
        relative_volume=1.73,
        momentum_state="ACCELERATING",
        entry_recommendation="WAIT_FOR_PULLBACK",
        entry_quality=68,
        continuation_confidence=72,
        distance_to_24h_high_pct=1.0,
        extended_move=False,
        liquidity_24h_usd_approx=500000.0,
    )

    card = _compact_card(signal)

    assert "METUSD — EARLY_EXPANSION" in card
    assert "Entry: WAIT_FOR_PULLBACK" in card
    assert "Momentum accelerating" in card
    assert "Volume expanding" in card
    assert len(card.splitlines()) <= 7
    assert "Evidence:" not in card



def _telegram_settings():
    return SimpleNamespace(
        telegram_bot_token="secret",
        telegram_chat_id="chat",
    )


def test_meaningful_transition_sends_fresh_push_instead_of_silent_edit(monkeypatch):
    calls = {"send": 0, "edit": 0}

    def send(*args, **kwargs):
        calls["send"] += 1
        return 9001

    def edit(*args, **kwargs):
        calls["edit"] += 1
        return True

    monkeypatch.setattr("app.jobs.scan_movers.send_telegram_message_with_id", send)
    monkeypatch.setattr("app.jobs.scan_movers.edit_telegram_message", edit)

    decision = SimpleNamespace(
        reason="MEANINGFUL_TRANSITION",
        message_id=4321,
    )
    action, message_id = _deliver_existing_card_update(
        settings=_telegram_settings(),
        decision=decision,
        message="updated card",
    )

    assert action == "TRANSITION_PUSHED"
    assert message_id == 9001
    assert calls == {"send": 1, "edit": 0}


def test_periodic_refresh_keeps_editing_existing_card_without_new_push(monkeypatch):
    calls = {"send": 0, "edit": 0}

    def send(*args, **kwargs):
        calls["send"] += 1
        return 9002

    def edit(*args, **kwargs):
        calls["edit"] += 1
        return True

    monkeypatch.setattr("app.jobs.scan_movers.send_telegram_message_with_id", send)
    monkeypatch.setattr("app.jobs.scan_movers.edit_telegram_message", edit)

    decision = SimpleNamespace(
        reason="PERIODIC_REFRESH",
        message_id=4321,
    )
    action, message_id = _deliver_existing_card_update(
        settings=_telegram_settings(),
        decision=decision,
        message="refreshed card",
    )

    assert action == "EDITED"
    assert message_id == 4321
    assert calls == {"send": 0, "edit": 1}


def test_failed_meaningful_transition_push_keeps_old_canonical_state_retryable(monkeypatch):
    monkeypatch.setattr(
        "app.jobs.scan_movers.send_telegram_message_with_id",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.jobs.scan_movers.edit_telegram_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("edit must not run")),
    )

    decision = SimpleNamespace(
        reason="MEANINGFUL_TRANSITION",
        message_id=4321,
    )
    action, message_id = _deliver_existing_card_update(
        settings=_telegram_settings(),
        decision=decision,
        message="updated card",
    )

    assert action == "TRANSITION_PUSH_FAILED"
    assert message_id is None
