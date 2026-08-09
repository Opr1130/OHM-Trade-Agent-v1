import logging
import threading
import time

import httpx

from app.core.config import get_settings
from app.services.telegram_notifier import send_telegram_message

logger = logging.getLogger(__name__)

_stop_event = threading.Event()


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


def process_callback(update: dict) -> None:
    settings = get_settings()

    callback = update.get("callback_query")
    if not callback:
        return

    callback_id = callback.get("id")
    callback_data = callback.get("data", "")

    message = callback.get("message", {})
    chat = message.get("chat", {})
    callback_chat_id = str(chat.get("id", ""))

    if not (
        settings.telegram_bot_token
        and settings.telegram_chat_id
    ):
        logger.warning(
            "Telegram callback ignored because Telegram settings are missing"
        )
        return

    #
    # SECURITY:
    # Only accept button presses from the configured OHM Telegram chat.
    #
    if callback_chat_id != str(settings.telegram_chat_id):
        logger.warning(
            "Unauthorized Telegram callback from chat_id=%s",
            callback_chat_id,
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
                "Status: FILLED\n\n"
                "OHM recorded your confirmation.\n"
                "Kraken execution is NOT enabled."
            ),
        )

        return

    if callback_data.startswith("trade_skip:"):
        trade_id = callback_data.split(":", 1)[1]

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


def telegram_callback_loop() -> None:
    settings = get_settings()

    if not settings.telegram_enabled:
        logger.info("Telegram callback listener disabled")
        return

    if not settings.telegram_bot_token:
        logger.warning(
            "Telegram callback listener cannot start: bot token missing"
        )
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{settings.telegram_bot_token}/getUpdates"
    )

    offset = None

    logger.info("OHM Telegram callback listener started")

    while not _stop_event.is_set():
        params = {
            "timeout": 25,
            "allowed_updates": '["callback_query"]',
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

            if not payload.get("ok"):
                logger.error(
                    "Telegram getUpdates returned error: %s",
                    payload,
                )
                time.sleep(5)
                continue

            for update in payload.get("result", []):
                update_id = update.get("update_id")

                if update_id is not None:
                    offset = update_id + 1

                try:
                    process_callback(update)

                except Exception:
                    logger.exception(
                        "Failed processing Telegram callback update"
                    )

        except httpx.HTTPError:
            logger.exception(
                "Telegram callback polling failed"
            )
            time.sleep(5)

        except Exception:
            logger.exception(
                "Unexpected Telegram callback listener error"
            )
            time.sleep(5)


def start_telegram_callback_listener() -> None:
    thread = threading.Thread(
        target=telegram_callback_loop,
        name="ohm-telegram-callback-listener",
        daemon=True,
    )

    thread.start()


def stop_telegram_callback_listener() -> None:
    _stop_event.set()
