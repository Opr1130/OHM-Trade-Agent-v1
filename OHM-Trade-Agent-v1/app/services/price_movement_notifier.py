from __future__ import annotations

from typing import Any

from app.services.notification_policy import record_emitted, should_emit
from app.services.price_movement_radar import EXPIRED, READY, WATCH
from app.services.telegram_notifier import send_telegram_message


NON_ACTIONABLE_STAGES = {WATCH, READY, EXPIRED}


def format_price_movement_message(signal: dict[str, Any]) -> str:
    stage = str(signal.get("stage") or "UNKNOWN").upper()
    if stage == EXPIRED:
        return (
            "⚪ OHM PRICE MOVEMENT — WATCH EXPIRED\n\n"
            f"Market: {signal.get('symbol', 'UNKNOWN')}\n"
            "Action: NO TRADE\n"
            "The volatility-expansion setup expired without directional confirmation."
        )

    icon = "⚡" if stage == READY else "🔎"
    components = signal.get("components") or {}
    reasons = "\n".join(
        f"• {reason}" for reason in (signal.get("reasons") or [])[:6]
    )
    return (
        f"{icon} OHM PRICE MOVEMENT — {stage}\n\n"
        f"Market: {signal.get('symbol', 'UNKNOWN')}\n"
        f"Class: {signal.get('signal_class', 'PRICE_MOVEMENT')} / "
        f"{signal.get('subtype', 'VOLATILITY_EXPANSION')}\n"
        f"Direction: {signal.get('direction', 'UNCONFIRMED')}\n"
        f"Readiness: {signal.get('readiness_score', 0)}/100 "
        "(deterministic score, not probability)\n"
        f"Detection: {signal.get('detection_timeframe', 'N/A')} | "
        f"Confirmation: {signal.get('confirmation_timeframe', 'N/A')} | "
        f"Regime: {signal.get('regime_timeframe', '4H')}\n"
        f"Expected Window: {signal.get('expected_window_primary', 'N/A')} "
        f"then {signal.get('expected_window_secondary', 'N/A')}\n"
        f"Expected Expansion: {signal.get('expected_move_low_atr', 0)}-"
        f"{signal.get('expected_move_high_atr', 0)} ATR "
        f"({signal.get('expected_move_low_pct', 0)}%-"
        f"{signal.get('expected_move_high_pct', 0)}%)\n"
        f"Expires: {signal.get('expires_at', 'N/A')}\n\n"
        "Score Components:\n"
        f"• Compression: {components.get('compression', 0)}/30\n"
        f"• Leverage: {components.get('leverage', 0)}/25\n"
        f"• Participation: {components.get('participation', 0)}/20\n"
        f"• Liquidation Fuel: {components.get('liquidation_fuel', 0)}/15\n"
        f"• Order-Book Fragility: {components.get('order_book_fragility', 0)}/10\n\n"
        f"Evidence:\n{reasons or '• Evidence is incomplete'}\n\n"
        "Action: MONITOR ONLY — no entry is authorized until direction is confirmed and all OHM trade gates pass."
    )


def send_price_movement_update(
    signal: dict[str, Any],
    *,
    bot_token: str,
    chat_id: str,
    cooldown_seconds: int = 6 * 60 * 60,
) -> bool:
    stage = str(signal.get("stage") or "").upper()
    if stage not in NON_ACTIONABLE_STAGES:
        return False
    symbol = str(signal.get("symbol") or "UNKNOWN").upper()
    fingerprint = str(signal.get("fingerprint") or f"{stage}:{signal.get('readiness_score', 0)}")
    if not should_emit(
        identity=f"PRICE_MOVEMENT:{symbol}",
        event_type=stage,
        fingerprint=fingerprint,
        cooldown_seconds=cooldown_seconds,
    ):
        return False
    sent = send_telegram_message(
        bot_token,
        chat_id,
        format_price_movement_message(signal),
    )
    if sent:
        record_emitted(
            identity=f"PRICE_MOVEMENT:{symbol}",
            event_type=stage,
            fingerprint=fingerprint,
        )
    return sent
