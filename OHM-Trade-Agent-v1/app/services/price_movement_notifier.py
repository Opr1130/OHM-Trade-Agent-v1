from __future__ import annotations

from typing import Any

from app.services.compact_alerts import (
    downside_scenario_pct,
    format_watch_alert,
    heuristic_risk_score,
    one_line_reason,
)
from app.services.notification_policy import record_emitted, should_emit
from app.services.price_movement_radar import EXPIRED, READY, WATCH
from app.services.telegram_notifier import send_telegram_message


NON_ACTIONABLE_STAGES = {WATCH, READY, EXPIRED}


def format_price_movement_message(signal: dict[str, Any]) -> str:
    stage = str(signal.get("stage") or "UNKNOWN").upper()
    symbol = str(signal.get("symbol") or "UNKNOWN").upper()
    if stage == EXPIRED:
        return (
            f"⚪ OHM WATCH EXPIRED — {symbol}\n"
            "Reason: Setup expired without directional confirmation\n"
            "Action: NO TRADE"
        )

    confidence = float(signal.get("readiness_score") or 0.0)
    risk = heuristic_risk_score(confidence)
    low = max(0.0, float(signal.get("expected_move_low_pct") or 0.0))
    high = max(low, float(signal.get("expected_move_high_pct") or low))
    reason = one_line_reason(*(signal.get("reasons") or []))
    action = "REVIEW" if stage == READY else "WATCH ONLY"
    return format_watch_alert(
        symbol=symbol,
        potential_low_pct=low,
        potential_high_pct=high,
        confidence_pct=confidence,
        risk_pct=risk,
        downside_pct=downside_scenario_pct(risk),
        reason=reason,
        action=action,
        title="OHM MOVEMENT WATCH",
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
