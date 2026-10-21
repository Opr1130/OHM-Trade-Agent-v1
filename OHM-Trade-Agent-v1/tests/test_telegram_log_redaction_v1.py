"""Telegram token must never reach application logs.

The production incident: the ``getUpdates`` poller used ``logger.exception`` on
an ``httpx.HTTPStatusError``. The rendered traceback contained the request URL,
and Telegram's URL embeds the bot token, so the credential was written to the
Docker log.

These tests use a synthetic token and drive the real handlers with failing
Telegram calls. They assert the token is absent from captured log output for
every failure class that production can hit.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.services import telegram_callback_listener as listener
from app.services.telegram_log_safety import (
    REDACTED,
    TOKEN_PATTERN,
    RedactionFilter,
    redact,
    safe_error,
)

#: Synthetic, clearly-fake token that still matches the Telegram shape.
TOKEN = "998877665:AAH_syntheticTESTtoken_0123456789abcdef"
#: What the token-bearing URL looks like in a real HTTPX exception message.
URL = f"https://api.telegram.org/bot{TOKEN}/getUpdates"


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", URL)
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status} Conflict' for url '{URL}'",
        request=request,
        response=response,
    )


def _boom(*args, **kwargs):  # noqa: ARG001
    raise AssertionError("test attempted a real network call")


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    monkeypatch.setattr(listener.httpx, "get", _boom, raising=True)


class _OneShotEvent:
    """Lets a single loop iteration run, then stops it (no 5s sleeps)."""

    def __init__(self) -> None:
        self._checks = 0

    def is_set(self) -> bool:
        self._checks += 1
        return self._checks > 1

    def wait(self, timeout: float | None = None) -> bool:  # noqa: ARG002
        return True


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        telegram_enabled=True,
        telegram_bot_token=TOKEN,
        telegram_chat_id="1234567890",
    )


# --- the sanitiser itself --------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        _status_error(403),
        _status_error(409),
        httpx.TimeoutException(f"timed out for {URL}"),
        httpx.ConnectError(f"cannot connect to {URL}"),
        ValueError(f"unexpected failure at {URL}"),
        RuntimeError(f"boom {URL}"),
    ],
)
def test_safe_error_never_contains_the_token(exc: BaseException) -> None:
    rendered = safe_error(exc)
    assert TOKEN not in rendered
    assert "api.telegram.org" not in rendered
    assert type(exc).__name__ in rendered


def test_safe_error_reports_type_and_status_without_url() -> None:
    rendered = safe_error(_status_error(409))
    assert rendered == "HTTPStatusError status=409"
    assert TOKEN not in rendered


def test_redact_replaces_embedded_tokens() -> None:
    assert TOKEN not in redact(f"see {URL} now")
    assert REDACTED in redact(URL)
    assert TOKEN_PATTERN.search(redact(URL)) is None


def test_redaction_filter_scrubs_message_and_arguments() -> None:
    record = logging.LogRecord(
        name="t",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="failed %s",
        args=(URL,),
        exc_info=None,
    )
    RedactionFilter().filter(record)
    assert TOKEN not in record.getMessage()
    assert REDACTED in record.getMessage()


# --- the real handlers must not leak --------------------------------------


def test_polling_failure_logs_without_the_token(monkeypatch, caplog) -> None:
    """The exact production path: getUpdates 409 during the poll loop."""

    def _raise(*args, **kwargs):  # noqa: ARG001
        raise _status_error(409)

    monkeypatch.setattr(listener.httpx, "get", _raise)
    monkeypatch.setattr(listener, "_stop_event", _OneShotEvent())
    monkeypatch.setattr(listener, "get_settings", _settings)

    with caplog.at_level(logging.DEBUG):
        listener.telegram_callback_loop()

    assert TOKEN not in caplog.text
    assert "api.telegram.org" not in caplog.text
    assert "Telegram polling failed" in caplog.text
    assert "status=409" in caplog.text


@pytest.mark.parametrize(
    "exc",
    [
        httpx.TimeoutException(f"timed out for {URL}"),
        httpx.ConnectError(f"cannot connect to {URL}"),
        ValueError(f"unexpected failure at {URL}"),
    ],
)
def test_transport_and_unexpected_failures_log_without_the_token(
    monkeypatch, caplog, exc: BaseException
) -> None:
    def _raise(*args, **kwargs):  # noqa: ARG001
        raise exc

    monkeypatch.setattr(listener.httpx, "get", _raise)
    monkeypatch.setattr(listener, "_stop_event", _OneShotEvent())
    monkeypatch.setattr(listener, "get_settings", _settings)

    with caplog.at_level(logging.DEBUG):
        listener.telegram_callback_loop()

    assert TOKEN not in caplog.text
    assert "api.telegram.org" not in caplog.text


def test_answer_callback_query_logs_without_the_token(monkeypatch, caplog) -> None:
    def _raise(*args, **kwargs):  # noqa: ARG001
        raise _status_error(403)

    monkeypatch.setattr(listener.httpx, "post", _raise)

    with caplog.at_level(logging.DEBUG):
        listener.answer_callback_query(TOKEN, "cb-1", "ok")

    assert TOKEN not in caplog.text
    assert "Failed to answer Telegram callback query" in caplog.text
    assert "status=403" in caplog.text


def test_processing_failure_logs_without_the_token(monkeypatch, caplog) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"ok": True, "result": [{"update_id": 1, "message": {}}]}

    def _get(*args, **kwargs):  # noqa: ARG001
        return _Response()

    def _process(update):  # noqa: ARG001
        raise ValueError(f"handler blew up at {URL}")

    monkeypatch.setattr(listener.httpx, "get", _get)
    monkeypatch.setattr(listener, "process_update", _process)
    monkeypatch.setattr(listener, "_stop_event", _OneShotEvent())
    monkeypatch.setattr(listener, "get_settings", _settings)

    with caplog.at_level(logging.DEBUG):
        listener.telegram_callback_loop()

    assert TOKEN not in caplog.text
    assert "Failed processing Telegram update" in caplog.text


# --- no structural regression ---------------------------------------------


def test_listener_uses_no_traceback_logging() -> None:
    """A reintroduced logger.exception() would bypass every other control."""
    source = Path(listener.__file__).read_text(encoding="utf-8")
    assert "logger.exception(" not in source


# --- ordinary behaviour unchanged -----------------------------------------


def test_authorization_semantics_are_unchanged() -> None:
    settings = SimpleNamespace(telegram_chat_id="1234567890", telegram_command_user_id=None)
    assert listener._authorized_actor(chat_id="1234567890", sender_id="55", settings=settings)

    wrong_chat = SimpleNamespace(telegram_chat_id="999", telegram_command_user_id=None)
    assert not listener._authorized_actor(
        chat_id="1234567890", sender_id="55", settings=wrong_chat
    )

    restricted = SimpleNamespace(telegram_chat_id="1234567890", telegram_command_user_id="55")
    assert listener._authorized_actor(chat_id="1234567890", sender_id="55", settings=restricted)
    assert not listener._authorized_actor(
        chat_id="1234567890", sender_id="77", settings=restricted
    )

