"""Optional O'Pip Event Risk advisory dispatcher (BUILD 3.3).

No caller in the production unified cycle uses this module yet. It converts a
pre-governed REAL_ADVISORY transition candidate into a human-review Telegram
message. It never places, changes, confirms, or cancels an exchange order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.opip.risk.alert_state import AlertTransitionDecision
from app.opip.risk.contract import ExposureFamily, ExposureView, RiskAssessment, RiskState


@dataclass(frozen=True)
class RiskNotificationResult:
    eligible: bool
    delivered: bool
    status: str
    message_id: int | None = None


def render_risk_advisory(
    *, exposure: ExposureView, assessment: RiskAssessment, decision: AlertTransitionDecision
) -> str:
    urgency = {
        RiskState.AVOID_NEW_ENTRY: "AVOID NEW ENTRY REVIEW",
        RiskState.PROTECT_REVIEW: "PROTECTION REVIEW",
        RiskState.EXIT_REVIEW: "URGENT EXIT REVIEW",
    }.get(decision.current_state, "RISK WATCH")
    return (
        f"O'Pip EVENT RISK — {urgency}\n"
        f"Asset: {exposure.symbol}\n"
        f"Direction: {exposure.direction.value}\n"
        f"State: {decision.current_state.value}\n"
        f"Event: {assessment.event_type.value} / {assessment.event_severity.value}\n"
        f"Reason: {'; '.join(assessment.reasons[:2]) or decision.notification_reason}\n"
        "Action: HUMAN REVIEW REQUIRED.\n"
        "O'Pip did not place, change, or close any order."
    )


def dispatch_risk_advisory(
    *,
    exposure: ExposureView,
    assessment: RiskAssessment | None,
    decision: AlertTransitionDecision,
    telegram_enabled: bool,
    bot_token: str | None,
    chat_id: str | None,
    generated_at: datetime,
) -> RiskNotificationResult:
    """Dispatch only an already-governed real-advisory transition candidate."""
    if exposure.exposure_family is not ExposureFamily.REAL_ADVISORY:
        return RiskNotificationResult(False, False, "PAPER_NOT_ELIGIBLE")
    if not decision.should_notify or assessment is None:
        return RiskNotificationResult(False, False, "TRANSITION_NOT_ELIGIBLE")
    if decision.current_state not in {
        RiskState.AVOID_NEW_ENTRY, RiskState.PROTECT_REVIEW, RiskState.EXIT_REVIEW
    }:
        return RiskNotificationResult(False, False, "STATE_NOT_ELIGIBLE")
    if not telegram_enabled or not str(bot_token or "").strip() or not str(chat_id or "").strip():
        return RiskNotificationResult(True, False, "TELEGRAM_DISABLED_OR_UNCONFIGURED")

    # Import only the notification transport at dispatch time. There is no
    # exchange/order/lifecycle-write import anywhere in app.opip.risk.
    from app.services.telegram_delivery import send_tracked_telegram

    delivery = send_tracked_telegram(
        bot_token=str(bot_token),
        chat_id=str(chat_id),
        message=render_risk_advisory(exposure=exposure, assessment=assessment, decision=decision),
        identity=f"OPIP_EVENT_RISK:{exposure.exposure_id}",
        alert_family="OPIP_EVENT_RISK",
        event_type=decision.current_state.value,
        fingerprint=decision.fingerprint,
        symbol=exposure.symbol,
        pair=exposure.symbol,
        trade_id=exposure.exposure_id,
        generated_at=generated_at,
    )
    return RiskNotificationResult(
        eligible=True,
        delivered=bool(delivery.delivered),
        status=str(delivery.status),
        message_id=delivery.message_id,
    )
