import json
import logging
import re
from functools import lru_cache

import httpx

from app.models.signal import SignalDecision, TradingSignal
from app.services.coingecko import CoinGeckoAPIError, CoinGeckoClient


logger = logging.getLogger(__name__)

_ALERT_HEADLINE_MARKERS = (
    "OHM TRADE",
    "OHM SCAN",
    "OHM WATCH",
    "OHM ENTRY",
    "OHM EXIT",
    "OHM RISK",
    "EMERGENCY RISK ALERT",
    "OHM EMERGENCY",
)
_QUOTE_SUFFIXES = ("USDT", "USD")


def _price_pct(target: float, reference: float, side: str) -> float:
    if reference <= 0:
        return 0.0
    if side.upper() == "SHORT":
        return max(0.0, (1.0 - target / reference) * 100.0)
    return max(0.0, (target / reference - 1.0) * 100.0)


def _base_symbol(value: str) -> str | None:
    token = str(value or "").strip().upper().replace("/", "").replace("-", "")
    if not re.fullmatch(r"[A-Z0-9]{2,30}", token):
        return None
    for quote in _QUOTE_SUFFIXES:
        if token.endswith(quote) and len(token) > len(quote):
            return token[: -len(quote)]
    return token


@lru_cache(maxsize=512)
def _resolved_coin_name(symbol: str) -> str | None:
    """Resolve a display name only when CoinGecko has one unambiguous symbol match.

    Tickers are not globally unique. If a symbol maps to zero or multiple assets,
    OHM deliberately returns None rather than guessing the project's identity.
    """
    base = _base_symbol(symbol)
    if not base:
        return None
    try:
        rows = CoinGeckoClient(timeout_seconds=4.0).get_markets_by_symbols([base])
    except CoinGeckoAPIError:
        return None
    exact = [
        row for row in rows
        if str(row.get("symbol") or "").strip().upper() == base
        and str(row.get("name") or "").strip()
    ]
    if len(exact) != 1:
        return None
    return str(exact[0]["name"]).strip()


def _identity_label(token: str) -> str:
    base = _base_symbol(token)
    if not base:
        return token
    name = _resolved_coin_name(base)
    if name:
        return f"{name} ({base})"
    return f"{base} (coin name unresolved)"


def _enrich_alert_identity(message: str) -> str:
    """Add human-readable coin identity to alert headlines without guessing.

    This is intentionally limited to alert-like headlines. Account reports such
    as OHM POSITIONS or OHM ORDERS are not rewritten here because their body can
    contain many assets and is formatted by its own report code.
    """
    lines = message.splitlines()
    if not lines:
        return message
    headline = lines[0]
    upper = headline.upper()
    if not any(marker in upper for marker in _ALERT_HEADLINE_MARKERS):
        return message

    # Already enriched by an upstream formatter.
    if re.search(r"\([A-Z0-9]{2,20}\)\s*(?:—|-)", headline):
        return message

    # Standard alert headlines end with an asset or pair after an em dash.
    match = re.search(r"(?P<prefix>.*?\s—\s)(?P<token>[A-Z0-9]{2,30})(?P<tail>\s*)$", headline)
    if not match:
        return message
    token = match.group("token")
    label = _identity_label(token)
    if label == token:
        return message

    pair = token if any(token.endswith(q) and len(token) > len(q) for q in _QUOTE_SUFFIXES) else None
    replacement = f"{match.group('prefix')}{label}"
    if pair:
        replacement += f" — {pair}"
    replacement += match.group("tail")
    lines[0] = replacement
    return "\n".join(lines)


def format_trade_alert(signal: TradingSignal, decision: SignalDecision) -> str:
    side = signal.side.upper()
    potential = _price_pct(float(signal.target_price), float(signal.price), side)
    downside = _price_pct(float(signal.stop_price), float(signal.price), "SHORT" if side == "LONG" else "LONG")
    confidence = float(decision.final_score)
    risk_pct = max(10.0, min(90.0, 100.0 - confidence + downside * 2.0))
    reason = " ".join(str(decision.summary or "Qualified OHM trade setup").split())[:140]
    return (
        f"🚨 OHM TRADE — {decision.symbol}\n"
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
    stop = _number(_line_value(message, "Stop"))
    target1 = _number(_line_value(message, "Target 1"))
    target2 = _number(_line_value(message, "Target 2"))

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
                    reason = candidate[:140]
                    break
            break

    headline = lines[0].upper() if lines else ""
    action = "ENTER NOW" if "ENTER NOW" in headline else "SET LIMIT / WAIT"
    return (
        f"🔥 OHM TRADE — {market}\n"
        f"Potential: +{low:.1f}% to +{high:.1f}%\n"
        f"Confidence*: {confidence:.0f}%\n"
        f"Risk: {risk_label}\n"
        f"Downside to stop: {downside:.1f}%\n"
        f"Reason: {reason}\n"
        f"Action: {action}\n"
        "*Heuristic confidence, not probability."
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


def _prepare_message(message: str) -> str:
    return _enrich_alert_identity(_compact_legacy_chief(message))


def send_telegram_message_with_id(
    bot_token: str,
    chat_id: str,
    message: str,
    reply_markup: dict | None = None,
) -> int | None:
    message = _prepare_message(message)
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
    message = _prepare_message(message)
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
    message = _prepare_message(message)
    payload = {"chat_id": chat_id, "text": message}
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)
    return _telegram_post(bot_token, "sendMessage", payload) is not None
