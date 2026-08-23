from __future__ import annotations

import queue
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


def _command_update():
    return {
        "update_id": 1,
        "message": {
            "text": "/scan VVV",
            "chat": {"id": 123},
            "from": {"id": 123},
        },
    }


def test_authorized_command_is_queued_not_executed_on_poll_thread(monkeypatch):
    queued = []
    executed = []
    monkeypatch.setattr(listener, "get_settings", lambda: _settings())
    monkeypatch.setattr(listener, "_command_rate_allowed", lambda settings: True)
    monkeypatch.setattr(listener, "_queue_command", lambda update, settings: queued.append(update) or True)
    monkeypatch.setattr(listener, "process_command_message", lambda *a, **k: executed.append(True))

    listener.process_message(_command_update())

    assert len(queued) == 1
    assert executed == []


def test_full_command_queue_returns_busy_without_blocking(monkeypatch):
    tiny_queue = queue.Queue(maxsize=1)
    tiny_queue.put(({}, _settings()))
    sent = []
    monkeypatch.setattr(listener, "_command_queue", tiny_queue)
    monkeypatch.setattr(
        listener,
        "send_telegram_message",
        lambda *args: sent.append(args[-1]) or True,
    )

    assert listener._queue_command(_command_update(), _settings()) is False
    assert any("COMMAND BUSY" in text for text in sent)


def test_process_update_prioritizes_callback_before_message(monkeypatch):
    order = []
    monkeypatch.setattr(listener, "process_callback", lambda update: order.append("callback"))
    monkeypatch.setattr(listener, "process_message", lambda update: order.append("message"))

    listener.process_update({"callback_query": {}, "message": {}})

    assert order == ["callback", "message"]


def test_command_worker_processes_queue_item_and_marks_task_done(monkeypatch):
    work_queue = queue.Queue(maxsize=2)
    work_queue.put((_command_update(), _settings()))
    seen = []
    monkeypatch.setattr(listener, "_command_queue", work_queue)

    listener._stop_event.clear()

    def run(update, settings):
        seen.append(update["message"]["text"])
        listener._stop_event.set()

    monkeypatch.setattr(listener, "process_command_message", run)
    listener.telegram_command_worker_loop()

    assert seen == ["/scan VVV"]
    assert work_queue.unfinished_tasks == 0
    listener._stop_event.clear()


def test_drain_command_queue_discards_stale_restart_work(monkeypatch):
    work_queue = queue.Queue(maxsize=3)
    work_queue.put((_command_update(), _settings()))
    work_queue.put((_command_update(), _settings()))
    monkeypatch.setattr(listener, "_command_queue", work_queue)

    listener._drain_command_queue()

    assert work_queue.empty()
    assert work_queue.unfinished_tasks == 0
