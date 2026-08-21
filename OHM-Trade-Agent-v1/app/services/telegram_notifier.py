import json
import logging

import httpx

from app.models.signal import SignalDecision, TradingSignal


logger = logging.getLogger(__name__)


def format_trade_alert(signal: TradingSignal, decision: SignalDecision) -> str:
    return (
        "🚨 OHM TRADE ALERT\n\n"
        f"Symbol: {decision.symbol}\n"
        f"Asset: {signal.asset_class.upper()}\n"
        f"Side: {signal.side.upper()}\n"
        f"Timeframe: {signal.timeframe}\n\n"
        f"Entry: {signal.price}\n"
        f"Stop: {signal.stop_price}\n"
        f"Target: {signal.target_price}\n"
        f"Reward/Risk: {decision.risk.reward_to_risk:.2f}:1\n"
        f"Position Size: {decision.risk.position_size:.6f}\n"
        f"Risk Dollars: ${decision.risk.risk_dollars:.2f}\n\n"
        f"Technical Score: {decision.deterministic_score}/100\n"
        f"AI Score: "
        f"{decision.ai_score if decision.ai_score is not None else 'N/A'}\n"
        f"Final Score: {decision.final_score}/100\n"
        f"Action: {decision.action.upper()}\n\n"
        f"Analysis:\n{decision.summary}"
    )


def build_trade_confirmation_buttons(trade_id: str) -> dict:
    """Build local lifecycle buttons; these never place a Kraken order."""
    return {
        "inline_keyboard": [
            [{"text": "✅ TRADE FILLED", "callback_data": f"trade_filled:{trade_id}"}],
            [{"text": "❌ SKIP TRADE", "callback_data": f"trade_skip:{trade_id}"}],
        ]
    }


def _telegram_post(bot_token: str, method: str, payload: dict) -> dict | None:
    """Call Telegram without ever logging the token-bearing request URL."""
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    try:
        response = httpx.post(url, data=payload, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) and data.get("ok") is True else None
    except (ValueError, httpx.HTTPStatusError, httpx.HTTPError) as exc:
        status = None
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
            status = exc.response.status_code
        logger.error(
            "Telegram notification failed endpoint=%s status=%s error=%s",
            method,
            status or "unknown",
            type(exc).__name__,
        )
        return None


def send_telegram_message_with_id(
    bot_token: str,
    chat_id: str,
    message: str,
    reply_markup: dict | None = None,
) -> int | None:
    payload = {"chat_id": chat_id, "text": message}
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)
    data = _telegram_post(bot_token, "sendMessage", payload)
    if data is None:
        return None
    result = data.get("result") or {}
    try:
        return int(result.get("message_id"))
    except (TypeError, ValueError):
        return None


def edit_telegram_message(
    bot_token: str,
    chat_id: str,
    message_id: int,
    message: str,
    reply_markup: dict | None = None,
) -> bool:
    payload = {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "text": message,
    }
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)
    return _telegram_post(bot_token, "editMessageText", payload) is not None


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    message: str,
    reply_markup: dict | None = None,
) -> bool:
    return send_telegram_message_with_id(
        bot_token,
        chat_id,
        message,
        reply_markup=reply_markup,
    ) is not None
