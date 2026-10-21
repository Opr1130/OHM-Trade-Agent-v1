"""Sanitised Telegram error logging.

Telegram's Bot API embeds the bot token in the request path
(``https://api.telegram.org/bot<token>/<method>``). An ``httpx`` exception
message contains that URL, so ``logger.exception(...)`` — which renders the
traceback and the exception text — writes the secret to disk. That is how the
production token reached the Docker log.

These helpers reduce an error to a bounded, non-secret category and provide a
defence-in-depth filter that redacts any bot token that reaches a log record by
another route.

Never render ``str(exc)``, ``repr(exc)`` or ``exc.request`` for an ``httpx``
error: all three can carry the token-bearing URL.
"""

from __future__ import annotations

import logging
import re
from typing import Any

#: ``bot<digits>:<secret>`` as it appears in a Telegram Bot API URL.
TOKEN_PATTERN = re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}")
REDACTED = "bot<REDACTED>"

#: Longest diagnostic token we will emit. Keeps log lines bounded.
_CATEGORY_LIMIT = 80


def redact(value: Any) -> str:
    """Return ``value`` as text with every bot token replaced."""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:  # noqa: BLE001 - a broken __str__ must not break logging
        return "<unrenderable>"
    return TOKEN_PATTERN.sub(REDACTED, text)


def safe_error(exc: BaseException) -> str:
    """A bounded, non-secret description of ``exc``.

    Emits the exception class name and, when the error carries one, the HTTP
    status code. Never includes the exception message, the request object or the
    request URL.
    """
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return f"{name} status={status}"[:_CATEGORY_LIMIT]
    return name[:_CATEGORY_LIMIT]


def _redact_value(value: Any) -> Any:
    """Redact a log message or argument, preserving its type wherever possible.

    Strings are redacted in place. Exceptions and any other object are only
    replaced when their *rendered* form actually carries a token, so a numeric
    or boolean argument keeps its type and ``%d``/``%f`` formatting still works.
    A non-string message (for example ``logger.error(exc)``) is covered too: the
    formatter would otherwise call ``str()`` on it after this filter runs, which
    is exactly how a token-bearing exception escaped redaction.
    """
    if isinstance(value, str):
        return redact(value) if TOKEN_PATTERN.search(value) else value
    # Fast path: these can never carry a token and must keep their type.
    if value is None or isinstance(value, (bool, int, float)):
        return value
    try:
        rendered = str(value)
    except Exception:  # noqa: BLE001 - a broken __str__ must not break logging
        return value
    return redact(rendered) if TOKEN_PATTERN.search(rendered) else value


class RedactionFilter(logging.Filter):
    """Redact bot tokens from any log record passing through the attached logger.

    Defence in depth only: the primary control is never logging raw ``httpx``
    exceptions. This filter cannot scrub a traceback rendered from
    ``exc_info``, which is why ``logger.exception`` must not be used on these
    paths.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - stdlib name
        try:
            record.msg = _redact_value(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        key: _redact_value(value) for key, value in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(_redact_value(value) for value in record.args)
        except Exception:  # noqa: BLE001 - logging must never raise
            return True
        return True


def install_redaction_filter(logger: logging.Logger) -> None:
    """Attach the redaction filter once, idempotently."""
    if not any(isinstance(existing, RedactionFilter) for existing in logger.filters):
        logger.addFilter(RedactionFilter())
