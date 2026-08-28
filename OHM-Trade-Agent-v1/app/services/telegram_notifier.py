import json
import logging
import re

import httpx

from app.models.signal import SignalDecision, TradingSignal
from app.services.asset_display_identity import display_market_label


logger = logging.getLogger(__name__)


def _price_pct(target: float, reference: float, side: str) -> float:
    if reference <= 0:
        return 0.0
    if side.upper() == "SHORT":
        return max(0.0, (1.0 - target / reference) * 100.0)
    return max(0.0, (target / reference - 1.0) * 100.0)


def format_trade_alert(signal: TradingSignal, decision: SignalDecision) -> str:
    side = signal.side.upper()
    potential = _price_pct(float(signal.target_price), float(signal.price), side)
    downside = _price_pct(float(signal.stop_price), float(signal.price), "SHORT" if side == "LONG" else "LONG")
    confidence = float(decision.final_score)
    risk_pct = max(10.0, min(90.0, 100.0 - confidence + downside * 2.0))
    reason = " ".join(str(decision.summary or "Qualified OHM trade setup").split())[:140]
    return (
        f"🚨 OHM TRADE — {display_market_label(decision.symbol)}\n"
        f"Potential: +{potential:.1f}%\n"
        f"Confidence*: {confidence:.0f}%\n"
        f"Risk*: {risk_pct:.0f}%\n"
        f"Downside to stop: {downside:.1f}%\n"
        f"Reason: {reason}\n"
        f"Action: {decision.action.upper()}\n"
        "*Heuristic score, not probability."
    )


def build_trade_confirmation_buttons(trade_id: str) -> dict:
    """Build local lifecycle buttons; these never place a Kraken order."""
    return {
        "inline_keyboard": [
            [{"text": "✅ TRADE FILLED", "callback_data": f"trade_filled:{trade_id}"}],
            [{"text": "❌ SKIP TRADE", "callback_data": f"trade_skip:{trade_id}"}],
        ]
    }


def _line_value(message: str, label: str) -> str | None:
    prefix = f"{label}:"
    for line in message.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _number(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group(0)) if match else None


def _compact_legacy_chief(message: str) -> str:
    if "OHM CHIEF" not in message:
        return message

    market = _line_value(message, "Market") or _line_value(message, "Asset") or "UNKNOWN"
    direction = (_line_value(message, "Direction") or "LONG").upper()
    confidence = _number(_line_value(message, "AI Confidence")) or 0.0
    risk_label = (_line_value(message, "Risk") or "UNKNOWN").upper()
    entry_zone = _line_value(message, "Entry Zone")
    chase = _line_value(message, "Do Not Chase Above") or _line_value(message, "Do Not Chase Below")
    stop = _number(_line_value(message, "Stop"))
    target1 = _number(_line_value(message, "Target 1"))
    target2 = _number(_line_value(message, "Target 2"))
    capital = _number(_line_value(message, "Recommended Capital"))
    net_edge = _number(_line_value(message, "Projected Net Edge"))

    entry_ref = None
    if entry_zone:
        nums = re.findall(r"\d+(?:\.\d+)?", entry_zone.replace(",", ""))
        if nums:
            vals = [float(item) for item in nums[:2]]
            entry_ref = sum(vals) / len(vals)

    low = high = 0.0
    downside = 0.0
    if entry_ref and entry_ref > 0:
        potential = [
            _price_pct(value, entry_ref, direction)
            for value in (target1, target2)
            if value is not None
        ]
        if potential:
            low, high = min(potential), max(potential)
        if stop is not None:
            downside = (
                max(0.0, (stop / entry_ref - 1.0) * 100.0)
                if direction == "SHORT"
                else max(0.0, (1.0 - stop / entry_ref) * 100.0)
            )

    lines = message.splitlines()
    reason = "Qualified OHM trade setup"
    for index, line in enumerate(lines):
        if line.strip() == "Reason:":
            for candidate in lines[index + 1:index + 4]:
                candidate = " ".join(candidate.split()).strip()
                if candidate:
                    reason = candidate[:160]
                    break
            break

    headline = lines[0].upper() if lines else ""
    action = "ENTER NOW" if "ENTER NOW" in headline else "SET LIMIT / WAIT"
    entry_text = entry_zone or "N/A"
    chase_text = chase or "N/A"
    stop_text = f"{stop:.8g}" if stop is not None else "N/A"
    target_text = (
        f"{target1:.8g} / {target2:.8g}"
        if target1 is not None and target2 is not None
        else "N/A"
    )
    economic = ""
    if capital is not None or net_edge is not None:
        cap_text = f"${capital:,.0f}" if capital is not None else "N/A"
        edge_text = f"{net_edge:.2f}%" if net_edge is not None else "N/A"
        economic = f"Capital: {cap_text} | Projected net edge: {edge_text}\n"

    return (
        f"🔥 OHM QUALIFIED OPPORTUNITY — {market}\n"
        f"Action: {action}\n"
        f"Entry: {entry_text}\n"
        f"Do not chase: {chase_text}\n"
        f"Stop: {stop_text} | Downside: {downside:.1f}%\n"
        f"T1 / T2: {target_text} | Potential: +{low:.1f}% to +{high:.1f}%\n"
        f"{economic}"
        f"Confidence*: {confidence:.0f}% | Risk: {risk_label}\n"
        f"Why now: {reason}\n"
        "*Heuristic confidence, not probability; human approval remains required."
    )


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
    message = _compact_legacy_chief(message)
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
    message = _compact_legacy_chief(message)
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
    message = _compact_legacy_chief(message)
    payload = {"chat_id": chat_id, "text": message}
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)
    return _telegram_post(bot_token, "sendMessage", payload) is not None
