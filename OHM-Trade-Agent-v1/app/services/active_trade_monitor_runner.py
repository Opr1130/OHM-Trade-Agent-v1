from dataclasses import dataclass

from app.core.config import get_settings
from app.services.active_trade_registry import get_active_trades
from app.services.emergency_alert_notifier import send_emergency_alert
from app.services.emergency_move_detector import detect_emergency_move
from app.services.kraken_position_verification import KrakenPositionVerifier
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


def run_active_trade_monitor() -> MonitorRunSummary:
    settings = get_settings()
    try:
        trades = get_active_trades()
    except Exception as exc:
        return MonitorRunSummary(
            active_trades=0,
            checked=0,
            monitor_notifications_sent=0,
            emergency_notifications_sent=0,
            positions_verified=0,
            positions_absent=0,
            positions_unavailable=0,
            failures=[f"active trade registry unavailable: {exc}"],
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
    verifier.refresh()

    for trade in trades:
        try:
            verification = verifier.verify(trade)
            if verification.status == "ABSENT":
                positions_absent += 1
                failures.append(
                    f"{trade.symbol}: active registry entry skipped; {verification.reason}"
                )
                continue
            if not verification.verified:
                positions_unavailable += 1
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
            failures.append(f"{trade.symbol}: {exc}")

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
