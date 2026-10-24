"""Tradevisor actionable alert formatting and delivery policy.

Advisory only. This module formats an already-decided recommendation and
delivers it through the existing Telegram transport and notification policy. It
never decides qualification, never sizes a position, never places an order, and
cannot turn a non-actionable decision into a message.

Delivery is fail-closed on every ambiguity:

* a non-actionable decision is never formatted;
* an expired recommendation is never delivered (ATDD-42);
* a recommendation whose material opportunity the native path already alerted
  is suppressed, so source labelling cannot double-notify (ATDD-48);
* the existing notification policy owns dedup/reservation, and the existing
  delivery ledger owns transport telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.notification_policy import (
    DEFAULT_COOLDOWN_SECONDS,
    confirm_emit,
    release_emit,
    reserve_emit,
    should_emit,
)
from app.services.registry_io import RegistryIOError, load_json
from app.services.telegram_delivery import send_tracked_telegram
from app.services.tradevisor_evaluation import (
    ACTIONABLE_DECISIONS,
    DECISION_SET_LIMIT_ENTRY,
    TradevisorDecision,
)

#: The required heading. Operators filter on it; do not localize it.
TRADEVISOR_ALERT_TITLE = "TRADEVISOR ALERTS"
#: Distinct delivery family so Tradevisor sends are separable in the ledger.
TRADEVISOR_ALERT_FAMILY = "TRADEVISOR_SIGNAL"
#: Lifecycle-critical policy class: an actionable entry recommendation must not
#: be starved by the generic attention budget that protects protection alerts.
TRADEVISOR_EVENT_TYPE = "ACTIONABLE_TRADE"

STATUS_DELIVERED = "DELIVERED"
STATUS_NOT_ACTIONABLE = "NOT_ACTIONABLE"
STATUS_EXPIRED = "EXPIRED"
STATUS_TELEGRAM_DISABLED = "TELEGRAM_DISABLED"
STATUS_SUPPRESSED_NATIVE_DUPLICATE = "SUPPRESSED_NATIVE_DUPLICATE"
STATUS_SUPPRESSED_POLICY = "SUPPRESSED_POLICY"
STATUS_SEND_FAILED = "SEND_FAILED"

#: How long a native actionable alert for the same market suppresses a
#: Tradevisor one. Mirrors the notification policy's own non-critical cooldown.
NATIVE_DUPLICATE_WINDOW_SECONDS = DEFAULT_COOLDOWN_SECONDS


@dataclass(frozen=True)
class TradevisorNotificationOutcome:
    status: str
    reason: str | None = None
    delivered: bool = False
    message: str | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "delivered": self.delivered,
            "alert_family": TRADEVISOR_ALERT_FAMILY,
            "trading_authority_changed": False,
        }


def _price(value: float | None) -> str:
    if value is None:
        return "n/a"
    magnitude = abs(float(value))
    if magnitude >= 1000:
        return f"{float(value):,.2f}"
    return f"{float(value):,.8f}".rstrip("0").rstrip(".")


def _usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):,.2f}"


def _scaled_net_profits(decision: TradevisorDecision) -> tuple[float, float]:
    """Scale envelope-level net profit to the capital actually recommended.

    Both gross profit and modeled costs are linear in notional, so scaling the
    net figure is exact rather than approximate.
    """
    economics = decision.economics
    allocation = decision.allocation
    if economics is None or allocation is None:
        return 0.0, 0.0
    envelope = float(economics.recommended_capital or 0.0)
    if envelope <= 0:
        return 0.0, 0.0
    scale = float(allocation.recommended_capital) / envelope
    return (
        round(float(economics.target_1_net_profit) * scale, 2),
        round(float(economics.target_2_net_profit) * scale, 2),
    )


def _timeframe_label(minutes: int) -> str:
    if minutes % 60 == 0:
        hours = minutes // 60
        return "1H" if hours == 1 else f"{hours}H"
    return f"{minutes}m"


def _evidence_line(decision: TradevisorDecision) -> str:
    """State exactly which evidence is present and which is missing.

    A recommendation must never imply more corroboration than it has. The
    native 1H structure and the execution book are always present here; the
    4H regime and a trusted forecast model are unavailable at this baseline, so
    they are named rather than silently omitted.
    """
    present = ["native 1H structure", "Kraken execution book"]
    missing: list[str] = []
    evidence = decision.evidence or {}
    if str(evidence.get("regime_4h") or "UNAVAILABLE") == "UNAVAILABLE":
        missing.append("4H regime")
    if str(evidence.get("f6_forecast_status") or "").startswith("UNAVAILABLE"):
        missing.append("trusted forecast")
    line = f"Evidence: {', '.join(present)}"
    if missing:
        line += f"; unavailable: {', '.join(missing)}"
    return line


def format_tradevisor_alert(decision: TradevisorDecision) -> str:
    """Render the required actionable layout. Never called for non-actionable."""
    plan = decision.plan
    economics = decision.economics
    allocation = decision.allocation
    if decision.decision not in ACTIONABLE_DECISIONS or plan is None:
        raise ValueError("only actionable decisions may be formatted")
    if allocation is None or economics is None:
        raise ValueError("actionable decision is missing capital or economic evidence")

    net_t1, net_t2 = _scaled_net_profits(decision)
    limit_entry = decision.decision == DECISION_SET_LIMIT_ENTRY
    expires = (
        decision.expires_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if decision.expires_at
        else "n/a"
    )
    action = "SET LIMIT ENTRY" if limit_entry else "ENTER NOW"
    direction_label = "LONG" if decision.signal == "BUY" else "NON_ACTIONABLE"
    regime = decision.market_regime or "UNAVAILABLE"

    lines = [
        TRADEVISOR_ALERT_TITLE,
        f"{decision.instrument} — {decision.horizon or 'UNCLASSIFIED'} "
        f"{decision.signal} ({direction_label})",
        "",
        f"Action: {action}",
        "",
        f"Signal: Tradevisor V2 {decision.signal}",
        "O'Pip: QUALIFIED",
        f"Source: {decision.source_exchange} {_timeframe_label(decision.timeframe_minutes)}",
        f"Execution venue: Kraken ({decision.market})",
        "",
        f"Current price: {_price(decision.current_price)}",
        f"Preferred entry: {_price(plan.entry_low)} - {_price(plan.entry_high)}",
    ]
    if limit_entry:
        # A pullback plan's executable price is the retest level at the bottom
        # of the R4 zone; recommending the current (extended) price would be
        # advice to chase, which the kernel is explicitly avoiding.
        lines.append(f"Limit buy: {_price(plan.entry_low)}")
    lines.extend(
        [
            f"Stop: {_price(plan.stop_price)}",
            f"Target 1: {_price(plan.target_1)}",
            f"Target 2: {_price(plan.target_2)}",
            "",
            f"Capital: {_usd(allocation.recommended_capital)}",
            f"Planned loss: {_usd(allocation.risk_dollars)}",
            f"Estimated net profit T1: {_usd(net_t1)}",
            f"Estimated net profit T2: {_usd(net_t2)}",
            f"Reward/Risk: {float(plan.reward_to_risk_2):.2f}:1",
            "",
            f"Do not chase above: {_price(plan.chase_limit)}",
            f"Expires: {expires}",
            "",
            f"Why now: {decision.reason}",
            f"Market regime: {regime} (native 1H structure)",
            _evidence_line(decision),
            "O'Pip qualification: attainability + economic + capital gates passed",
            "Estimates are after modeled fees/slippage, not guaranteed profits.",
            "Execution: Human approval required",
        ]
    )
    return "\n".join(lines)


def _normalize_market(value: object) -> str:
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def _parse_utc(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def native_alert_recently_emitted(
    market: str,
    *,
    now: datetime,
    window_seconds: int = NATIVE_DUPLICATE_WINDOW_SECONDS,
) -> bool:
    """True when the native path already alerted this market's actionable trade.

    Cross-source duplicate suppression: a corroborated opportunity must produce
    one material alert, not one per evidence source. Any state-read failure is
    treated as "not emitted" so the Tradevisor alert is not silently lost; the
    notification policy reservation still prevents duplicates on its own.
    """
    target = _normalize_market(market)
    if not target:
        return False
    try:
        from app.services import notification_policy

        state = load_json(notification_policy.STATE_FILE)
    except (OSError, TimeoutError, RegistryIOError, ValueError, TypeError):
        return False
    if not isinstance(state, dict):
        return False
    for key, entry in state.items():
        text = str(key)
        if not text.endswith(f":{TRADEVISOR_EVENT_TYPE}"):
            continue
        if not isinstance(entry, dict):
            continue
        identity = text[: -len(f":{TRADEVISOR_EVENT_TYPE}")]
        emitted_market = identity.split(":", 1)[-1] if ":" in identity else identity
        if _normalize_market(emitted_market) != target:
            continue
        sent_at = _parse_utc(entry.get("sent_at"))
        if sent_at is not None and (now - sent_at).total_seconds() < window_seconds:
            return True
    return False


def notify_tradevisor_decision(
    decision: TradevisorDecision,
    *,
    settings,
    now: datetime | None = None,
    sender=None,
) -> TradevisorNotificationOutcome:
    """Deliver one recommendation if, and only if, every gate permits it."""
    now = now or datetime.now(timezone.utc)
    if decision.decision not in ACTIONABLE_DECISIONS:
        return TradevisorNotificationOutcome(
            status=STATUS_NOT_ACTIONABLE,
            reason=f"decision {decision.decision} is not an actionable entry",
        )
    if decision.plan is None or decision.allocation is None or decision.economics is None:
        return TradevisorNotificationOutcome(
            status=STATUS_NOT_ACTIONABLE,
            reason="actionable decision is missing a plan, capital, or economic evidence",
        )
    if decision.expires_at is not None and now > decision.expires_at:
        return TradevisorNotificationOutcome(
            status=STATUS_EXPIRED,
            reason="the entry plan expired before delivery",
        )

    bot_token = getattr(settings, "telegram_bot_token", None)
    chat_id = getattr(settings, "telegram_chat_id", None)
    if not (bool(getattr(settings, "telegram_enabled", False)) and bot_token and chat_id):
        return TradevisorNotificationOutcome(
            status=STATUS_TELEGRAM_DISABLED,
            reason="Telegram delivery is not enabled or configured",
        )

    if native_alert_recently_emitted(decision.market, now=now):
        return TradevisorNotificationOutcome(
            status=STATUS_SUPPRESSED_NATIVE_DUPLICATE,
            reason="the native path already alerted this market's actionable trade",
        )

    message = format_tradevisor_alert(decision)
    plan = decision.plan
    identity = f"TRADEVISOR:LONG:{decision.market}"
    fingerprint = ":".join(
        (
            str(decision.dedup_id),
            decision.decision,
            f"{float(plan.entry_high):.8f}",
            f"{float(plan.stop_price):.8f}",
            f"{float(plan.target_1):.8f}",
        )
    )

    if not should_emit(
        identity=identity,
        event_type=TRADEVISOR_EVENT_TYPE,
        fingerprint=fingerprint,
    ):
        return TradevisorNotificationOutcome(
            status=STATUS_SUPPRESSED_POLICY,
            reason="notification policy already emitted this recommendation",
            message=message,
        )

    reservation = reserve_emit(
        identity=identity,
        event_type=TRADEVISOR_EVENT_TYPE,
        fingerprint=fingerprint,
    )
    if reservation is None:
        return TradevisorNotificationOutcome(
            status=STATUS_SUPPRESSED_POLICY,
            reason="another worker holds the notification reservation",
            message=message,
        )

    deliver = sender or send_tracked_telegram
    delivered = False
    try:
        result = deliver(
            bot_token=bot_token,
            chat_id=chat_id,
            message=message,
            identity=identity,
            alert_family=TRADEVISOR_ALERT_FAMILY,
            event_type=TRADEVISOR_EVENT_TYPE,
            fingerprint=fingerprint,
            symbol=decision.market,
            pair=decision.instrument,
            signal_id=decision.dedup_id,
        )
        delivered = bool(getattr(result, "delivered", False))
    finally:
        if delivered:
            confirm_emit(
                identity=identity,
                event_type=TRADEVISOR_EVENT_TYPE,
                fingerprint=fingerprint,
                reservation_token=reservation,
            )
        else:
            # Release immediately so recovery does not wait for the lease TTL.
            release_emit(
                identity=identity,
                event_type=TRADEVISOR_EVENT_TYPE,
                reservation_token=reservation,
            )

    if not delivered:
        return TradevisorNotificationOutcome(
            status=STATUS_SEND_FAILED,
            reason="Telegram transport did not accept the message",
            message=message,
        )
    return TradevisorNotificationOutcome(
        status=STATUS_DELIVERED, delivered=True, message=message
    )

