from __future__ import annotations

from typing import Any

from app.services.asset_display_identity import display_market_label
from app.services.compact_alerts import (
    downside_scenario_pct,
    explosion_band,
    format_watch_alert,
    heuristic_risk_score,
    one_line_reason,
)
from app.services.notification_policy import record_emitted, should_emit
from app.services.price_movement_radar import EXPIRED, READY, WATCH
from app.services.telegram_delivery import (
    record_telegram_not_eligible,
    record_telegram_suppression,
    send_tracked_telegram,
)


NON_ACTIONABLE_STAGES = {WATCH, READY, EXPIRED}


def _movement_potential_band(
    signal: dict[str, Any],
    confidence: float,
) -> tuple[float, float, bool]:
    """Return the presentation band and whether it is a generic fallback.

    Explicit move estimates are preserved only when they form a valid positive
    range. Missing, zero, negative, non-finite, or inverted values fall back to
    the shared heuristic scenario band. The caller must disclose that fallback
    so a generic scenario is never mistaken for a market-derived estimate.
    """
    try:
        raw_low = float(signal.get("expected_move_low_pct"))
    except (TypeError, ValueError):
        raw_low = 0.0
    try:
        raw_high = float(signal.get("expected_move_high_pct"))
    except (TypeError, ValueError):
        raw_high = 0.0

    if raw_low > 0.0 and raw_high >= raw_low and raw_low < float("inf") and raw_high < float("inf"):
        return raw_low, raw_high, False
    low, high = explosion_band(confidence)
    return float(low), float(high), True


def format_price_movement_message(signal: dict[str, Any]) -> str:
    stage = str(signal.get("stage") or "UNKNOWN").upper()
    symbol = str(signal.get("symbol") or "UNKNOWN").upper()
    if stage == EXPIRED:
        return (
            f"⚪ WATCH EXPIRED — {display_market_label(symbol)}\n"
            "Reason: Setup expired without directional confirmation\n"
            "Action: NO TRADE"
        )

    confidence = float(signal.get("readiness_score") or 0.0)
    risk = heuristic_risk_score(confidence)
    low, high, fallback = _movement_potential_band(signal, confidence)
    reason = one_line_reason(*(signal.get("reasons") or []))
    action = "REVIEW" if stage == READY else "MONITOR ONLY — no entry is authorized"
    message = format_watch_alert(
        symbol=symbol,
        potential_low_pct=low,
        potential_high_pct=high,
        confidence_pct=confidence,
        risk_pct=risk,
        downside_pct=downside_scenario_pct(risk),
        reason=reason,
        action=action,
        title="MOVEMENT WATCH",
    )
    if fallback:
        plain = f"Potential: +{low:.0f}% to +{high:.0f}%"
        disclosed = plain + " (heuristic fallback; not market-derived)"
        message = message.replace(plain, disclosed, 1)
    price = signal.get("current_price") or signal.get("reference_price")
    timeframe = signal.get("detection_timeframe") or signal.get("timeframe")
    context: list[str] = []
    if isinstance(price, (int, float)) and float(price) > 0:
        context.append(f"Price: {float(price):.8g}")
    if timeframe:
        context.append(f"TF: {timeframe}")
    if context:
        lines = message.splitlines()
        message = "\n".join([lines[0], " | ".join(context), *lines[1:]])
    return message


def send_price_movement_update(
    signal: dict[str, Any],
    *,
    bot_token: str,
    chat_id: str,
    cooldown_seconds: int = 6 * 60 * 60,
) -> bool:
    stage = str(signal.get("stage") or "").upper()
    symbol = str(signal.get("symbol") or "UNKNOWN").upper()
    identity = f"PRICE_MOVEMENT:{symbol}"
    fingerprint = str(signal.get("fingerprint") or f"{stage}:{signal.get('readiness_score', 0)}")
    if stage not in NON_ACTIONABLE_STAGES:
        record_telegram_not_eligible(
            identity=identity,
            alert_family="PRICE_MOVEMENT",
            event_type=stage or "UNKNOWN",
            fingerprint=fingerprint,
            reason="UNSUPPORTED_STAGE",
            symbol=symbol,
        )
        return False
    if not should_emit(
        identity=identity,
        event_type=stage,
        fingerprint=fingerprint,
        cooldown_seconds=cooldown_seconds,
    ):
        record_telegram_suppression(
            identity=identity,
            alert_family="PRICE_MOVEMENT",
            event_type=stage,
            fingerprint=fingerprint,
            reason="NOTIFICATION_POLICY",
            symbol=symbol,
        )
        return False
    delivery = send_tracked_telegram(
        bot_token=bot_token,
        chat_id=chat_id,
        message=format_price_movement_message(signal),
        identity=identity,
        alert_family="PRICE_MOVEMENT",
        event_type=stage,
        fingerprint=fingerprint,
        symbol=symbol,
    )
    if delivery.delivered:
        record_emitted(
            identity=identity,
            event_type=stage,
            fingerprint=fingerprint,
        )
    return delivery.delivered
