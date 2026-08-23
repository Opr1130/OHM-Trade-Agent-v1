import json
import logging
import queue
import threading
import time
from collections import deque

import httpx

from app.core.config import get_settings
from app.services.confirm_entry import confirm_trade_id
from app.services.pending_setup_registry import terminalize_pending_setup
from app.services.telegram_command_center import process_command_message, refresh_market_watches
from app.services.telegram_notifier import send_telegram_message

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_thread_lock = threading.Lock()
_rate_lock = threading.Lock()
_listener_thread: threading.Thread | None = None
_watch_thread: threading.Thread | None = None
_command_thread: threading.Thread | None = None
_command_times: deque[float] = deque()
_COMMAND_QUEUE_MAX = 4
_command_queue: queue.Queue[tuple[dict, object]] = queue.Queue(maxsize=_COMMAND_QUEUE_MAX)
_RESERVED_COMMANDS = {
    "start",
    "help",
    "scan",
    "why",
    "watch",
    "unwatch",
    "watchlist",
    "orders",
    "positions",
    "market",
}


def answer_callback_query(
    bot_token: str,
    callback_query_id: str,
    text: str,
) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"

    try:
        response = httpx.post(
            url,
            data={
                "callback_query_id": callback_query_id,
                "text": text,
            },
            timeout=10.0,
        )
        response.raise_for_status()

    except httpx.HTTPError:
        logger.exception("Failed to answer Telegram callback query")


def _authorized_actor(*, chat_id: str, sender_id: str, settings) -> bool:
    if chat_id != str(settings.telegram_chat_id or ""):
        return False
    configured_user = str(getattr(settings, "telegram_command_user_id", None) or "").strip()
    if configured_user and sender_id != configured_user:
        return False
    return True


def _command_rate_allowed(settings) -> bool:
    limit = int(getattr(settings, "telegram_command_rate_limit_per_minute", 12) or 12)
    limit = max(1, min(60, limit))
    now = time.monotonic()
    with _rate_lock:
        while _command_times and now - _command_times[0] >= 60.0:
            _command_times.popleft()
        if len(_command_times) >= limit:
            return False
        _command_times.append(now)
        return True


def _normalize_mobile_command_text(text: str) -> str | None:
    """Normalize a small set of mobile-friendly command forms.

    Formal slash commands are preserved. A single-token slash form such as
    /CAP is treated as shorthand for /scan CAP. Plain `scan CAP` is accepted
    case-insensitively. All other ordinary chat text remains ignored.
    """
    raw = str(text or "").strip()
    if not raw:
        return None

    if raw.startswith("/"):
        parts = raw.split()
        token = parts[0][1:]
        command_token = token.split("@", 1)[0]
        command = command_token.casefold()
        if command in _RESERVED_COMMANDS:
            return raw
        if (
            len(parts) == 1
            and command_token
            and 1 <= len(command_token) <= 20
            and command_token.isalnum()
        ):
            return f"/scan {command_token.upper()}"
        return raw

    parts = raw.split()
    if len(parts) == 2 and parts[0].casefold() == "scan":
        return f"/scan {parts[1]}"
    return None


def process_callback(update: dict) -> None:
    settings = get_settings()

    callback = update.get("callback_query")
    if not callback:
        return

    callback_id = callback.get("id")
    callback_data = callback.get("data", "")

    message = callback.get("message", {})
    chat = message.get("chat", {})
    sender = callback.get("from", {})
    callback_chat_id = str(chat.get("id", ""))
    callback_sender_id = str(sender.get("id", ""))

    if not (
        settings.telegram_bot_token
        and settings.telegram_chat_id
    ):
        logger.warning(
            "Telegram callback ignored because Telegram settings are missing"
        )
        return

    # SECURITY: only the configured OHM chat may mutate local lifecycle state.
    # An optional TELEGRAM_COMMAND_USER_ID further restricts group-chat use.
    if not _authorized_actor(
        chat_id=callback_chat_id,
        sender_id=callback_sender_id,
        settings=settings,
    ):
        logger.warning(
            "Unauthorized Telegram callback chat_id=%s sender_id=%s",
            callback_chat_id,
            callback_sender_id,
        )

        if callback_id:
            answer_callback_query(
                settings.telegram_bot_token,
                callback_id,
                "Unauthorized",
            )

        return

    if callback_data.startswith("trade_filled:"):
        trade_id = callback_data.split(":", 1)[1]

        try:
            trade = confirm_trade_id(trade_id)
        except ValueError as exc:
            if callback_id:
                answer_callback_query(
                    settings.telegram_bot_token,
                    callback_id,
                    str(exc),
                )
            return

        logger.info(
            "OHM TRADE CONFIRMED: %s",
            trade_id,
        )

        if callback_id:
            answer_callback_query(
                settings.telegram_bot_token,
                callback_id,
                "✅ Trade marked as filled",
            )

        send_telegram_message(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            (
                "✅ OHM TRADE CONFIRMED\n\n"
                f"Trade ID: {trade_id}\n"
                f"Symbol: {trade.symbol}\n"
                "Status: FILLED\n\n"
                "OHM recorded your confirmation.\n"
                "Kraken execution is NOT enabled."
            ),
        )

        return

    if callback_data.startswith("trade_skip:"):
        trade_id = callback_data.split(":", 1)[1]

        terminalize_pending_setup(trade_id, "skipped")

        logger.info(
            "OHM TRADE SKIPPED: %s",
            trade_id,
        )

        if callback_id:
            answer_callback_query(
                settings.telegram_bot_token,
                callback_id,
                "❌ Trade skipped",
            )

        send_telegram_message(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            (
                "❌ OHM TRADE SKIPPED\n\n"
                f"Trade ID: {trade_id}\n"
                "Status: SKIPPED"
            ),
        )

        return

    logger.warning(
        "Unknown Telegram callback_data=%s",
        callback_data,
    )

    if callback_id:
        answer_callback_query(
            settings.telegram_bot_token,
            callback_id,
            "Unknown OHM action",
        )


def _queue_command(update: dict, settings) -> bool:
    try:
        _command_queue.put_nowait((dict(update), settings))
        return True
    except queue.Full:
        send_telegram_message(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            "⚠️ OHM COMMAND BUSY — several requests are already queued. Try again shortly.",
        )
        return False


def process_message(update: dict) -> None:
    settings = get_settings()
    message = update.get("message")
    if not isinstance(message, dict):
        return
    text = str(message.get("text") or "").strip()
    normalized = _normalize_mobile_command_text(text)
    if normalized is None:
        return
    if normalized != text:
        message = dict(message)
        message["text"] = normalized
        update = dict(update)
        update["message"] = message
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}
    chat_id = str(chat.get("id", ""))
    sender_id = str(sender.get("id", ""))
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    if not _authorized_actor(chat_id=chat_id, sender_id=sender_id, settings=settings):
        logger.warning(
            "Unauthorized Telegram command chat_id=%s sender_id=%s",
            chat_id,
            sender_id,
        )
        return
    if not _command_rate_allowed(settings):
        send_telegram_message(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            "⚠️ OHM COMMAND RATE LIMIT — wait a moment before requesting another scan.",
        )
        return
    _queue_command(update, settings)


def process_update(update: dict) -> None:
    # Callback handling remains synchronous/high-priority. Commands are only
    # enqueued here, so a slow Kraken market scan can never delay a lifecycle
    # button press or the next Telegram getUpdates poll.
    if "callback_query" in update:
        process_callback(update)
    if "message" in update:
        process_message(update)


def telegram_command_worker_loop() -> None:
    logger.info("OHM Telegram command worker started")
    while not _stop_event.is_set():
        try:
            update, settings = _command_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            process_command_message(update, settings=settings)
        except Exception:
            logger.exception("Telegram command worker failed safely")
        finally:
            _command_queue.task_done()


def telegram_callback_loop() -> None:
    settings = get_settings()

    if not settings.telegram_enabled:
        logger.info("Telegram callback/command listener disabled")
        return

    if not settings.telegram_bot_token:
        logger.warning(
            "Telegram callback/command listener cannot start: bot token missing"
        )
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{settings.telegram_bot_token}/getUpdates"
    )

    offset = None

    logger.info("OHM Telegram callback/command listener started")

    while not _stop_event.is_set():
        params = {
            "timeout": 25,
            "allowed_updates": json.dumps(["callback_query", "message"]),
        }

        if offset is not None:
            params["offset"] = offset

        try:
            response = httpx.get(
                url,
                params=params,
                timeout=35.0,
            )

            response.raise_for_status()

            payload = response.json()

            if not isinstance(payload, dict) or not payload.get("ok"):
                logger.error("Telegram getUpdates returned a non-OK response")
                _stop_event.wait(5)
                continue

            results = payload.get("result", [])
            if not isinstance(results, list):
                logger.error("Telegram getUpdates returned invalid result shape")
                _stop_event.wait(5)
                continue

            for update in results:
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")

                if update_id is not None:
                    try:
                        offset = int(update_id) + 1
                    except (TypeError, ValueError):
                        logger.warning("Telegram update had invalid update_id")

                try:
                    process_update(update)

                except Exception:
                    logger.exception(
                        "Failed processing Telegram update"
                    )

        except httpx.HTTPError:
            logger.exception(
                "Telegram polling failed"
            )
            _stop_event.wait(5)

        except Exception:
            logger.exception(
                "Unexpected Telegram listener error"
            )
            _stop_event.wait(5)


def telegram_watch_loop() -> None:
    settings = get_settings()
    if not settings.telegram_enabled or not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    logger.info("OHM Telegram market-watch refresher started")
    while not _stop_event.wait(30):
        try:
            refresh_market_watches(settings=settings)
        except Exception:
            # Watch refresh is informational/advisory and must never terminate
            # the primary Telegram command/callback listener.
            logger.exception("Telegram market-watch refresh failed safely")


def _drain_command_queue() -> None:
    while True:
        try:
            _command_queue.get_nowait()
        except queue.Empty:
            return
        else:
            _command_queue.task_done()


def start_telegram_callback_listener() -> None:
    global _listener_thread, _watch_thread, _command_thread
    with _thread_lock:
        if any(
            thread is not None and thread.is_alive()
            for thread in (_listener_thread, _watch_thread, _command_thread)
        ):
            logger.info("Telegram listener already running")
            return
        _drain_command_queue()
        _stop_event.clear()
        _listener_thread = threading.Thread(
            target=telegram_callback_loop,
            name="ohm-telegram-callback-command-listener",
            daemon=True,
        )
        _command_thread = threading.Thread(
            target=telegram_command_worker_loop,
            name="ohm-telegram-command-worker",
            daemon=True,
        )
        _watch_thread = threading.Thread(
            target=telegram_watch_loop,
            name="ohm-telegram-market-watch-refresher",
            daemon=True,
        )
        _listener_thread.start()
        _command_thread.start()
        _watch_thread.start()


def stop_telegram_callback_listener() -> None:
    _stop_event.set()
