from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import get_settings
from app.services.active_trade_registry import get_active_trades
from app.services.asset_display_identity import display_market_label
from app.services.emergency_alert_notifier import send_emergency_alert
from app.services.emergency_move_detector import detect_emergency_move
from app.services.kraken_position_verification import KrakenPositionVerifier
from app.services.notification_policy import record_emitted, should_emit
from app.services.telegram_delivery import record_telegram_suppression, send_tracked_telegram
from app.services.trade_monitor import monitor_trade
from app.services.trade_monitor_notifier import send_monitor_update
from app.services.trade_outcome_registry import update_active_observation


@dataclass
class MonitorRunSummary:
    active_trades: int
    checked: int
    monitor_notifications_sent: int
    emergency_notifications_sent: int
    positions_verified: int
    positions_absent: int
    positions_unavailable: int
    failures: list[str]


def _notify_monitor_degraded(*, settings, reason: str, identity: str = "ACTIVE_TRADE_MONITOR") -> bool:
    """Make loss of the monitoring safety net visible to the operator.

    One message per hour is allowed while degradation persists. This is a
    lifecycle-critical operational alert and does not consume opportunity-card
    attention budget.
    """
    now = datetime.now(timezone.utc)
    hour_bucket = now.strftime("%Y%m%dT%H")
    fingerprint = f"{hour_bucket}:{reason}"
    if not should_emit(
        identity=identity,
        event_type="MONITOR_DEGRADED",
        fingerprint=fingerprint,
        cooldown_seconds=3600,
        now=now,
    ):
        record_telegram_suppression(
            identity=identity,
            alert_family="MONITOR_DEGRADED",
            event_type="MONITOR_DEGRADED",
            fingerprint=fingerprint,
            reason="NOTIFICATION_POLICY",
            generated_at=now,
        )
        return False
    message = (
        "🚨 OHM MONITORING DEGRADED\n"
        f"Reason: {reason}\n"
        "Protection: stop/target/emergency monitoring may be incomplete\n"
        "Action: VERIFY KRAKEN READ-ONLY CONNECTIVITY / POSITION STATE\n"
        "No order was placed or changed."
    )
    delivery = send_tracked_telegram(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        message=message,
        identity=identity,
        alert_family="MONITOR_DEGRADED",
        event_type="MONITOR_DEGRADED",
        fingerprint=fingerprint,
        generated_at=now,
    )
    if delivery.delivered:
        record_emitted(
            identity=identity,
            event_type="MONITOR_DEGRADED",
            fingerprint=fingerprint,
            now=now,
        )
    return delivery.delivered


def run_active_trade_monitor() -> MonitorRunSummary:
    settings = get_settings()
    try:
        trades = get_active_trades()
    except Exception as exc:
        reason = f"active trade registry unavailable: {exc}"
        _notify_monitor_degraded(settings=settings, reason=reason)
        return MonitorRunSummary(
            active_trades=0,
            checked=0,
            monitor_notifications_sent=0,
            emergency_notifications_sent=0,
            positions_verified=0,
            positions_absent=0,
            positions_unavailable=0,
            failures=[reason],
        )

    checked = 0
    monitor_notifications_sent = 0
    emergency_notifications_sent = 0
    positions_verified = 0
    positions_absent = 0
    positions_unavailable = 0
    failures: list[str] = []

    if not trades:
        return MonitorRunSummary(
            active_trades=0,
            checked=0,
            monitor_notifications_sent=0,
            emergency_notifications_sent=0,
            positions_verified=0,
            positions_absent=0,
            positions_unavailable=0,
            failures=[],
        )

    verifier = KrakenPositionVerifier()
    try:
        verifier.refresh()
    except Exception as exc:
        positions_unavailable = len(trades)
        reason = f"Kraken position verification refresh failed for {len(trades)} active trade(s): {exc}"
        failures.append(reason)
        _notify_monitor_degraded(settings=settings, reason=reason)
        return MonitorRunSummary(
            active_trades=len(trades),
            checked=0,
            monitor_notifications_sent=0,
            emergency_notifications_sent=0,
            positions_verified=0,
            positions_absent=0,
            positions_unavailable=positions_unavailable,
            failures=failures,
        )

    degraded_symbols: list[str] = []
    for trade in trades:
        try:
            verification = verifier.verify(trade)
            if verification.status == "ABSENT":
                positions_absent += 1
                degraded_symbols.append(f"{trade.symbol}:ABSENT")
                failures.append(
                    f"{trade.symbol}: active registry entry skipped; {verification.reason}"
                )
                continue
            if not verification.verified:
                positions_unavailable += 1
                degraded_symbols.append(f"{trade.symbol}:UNAVAILABLE")
                failures.append(
                    f"{trade.symbol}: position verification unavailable; {verification.reason}"
                )
                continue
            positions_verified += 1

            monitor_result = monitor_trade(trade)
            update_active_observation(
                trade,
                monitor_result.current_price,
            )

            if send_monitor_update(
                trade=trade,
                result=monitor_result,
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            ):
                monitor_notifications_sent += 1

            emergency_result = detect_emergency_move(trade)

            if send_emergency_alert(
                trade=trade,
                result=emergency_result,
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            ):
                emergency_notifications_sent += 1

            checked += 1

        except Exception as exc:
            degraded_symbols.append(f"{trade.symbol}:ERROR")
            failures.append(f"{trade.symbol}: {exc}")

    if degraded_symbols:
        reason = (
            f"{len(degraded_symbols)} active trade(s) not fully protected: "
            + ", ".join(degraded_symbols[:8])
        )
        _notify_monitor_degraded(settings=settings, reason=reason)

    return MonitorRunSummary(
        active_trades=len(trades),
        checked=checked,
        monitor_notifications_sent=monitor_notifications_sent,
        emergency_notifications_sent=emergency_notifications_sent,
        positions_verified=positions_verified,
        positions_absent=positions_absent,
        positions_unavailable=positions_unavailable,
        failures=failures,
    )
