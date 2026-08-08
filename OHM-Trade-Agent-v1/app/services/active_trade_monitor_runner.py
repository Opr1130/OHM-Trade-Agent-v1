from dataclasses import dataclass

from app.core.config import get_settings
from app.services.active_trade_registry import get_active_trades
from app.services.emergency_alert_notifier import send_emergency_alert
from app.services.emergency_move_detector import detect_emergency_move
from app.services.trade_monitor import monitor_trade
from app.services.trade_monitor_notifier import send_monitor_update


@dataclass
class MonitorRunSummary:
    active_trades: int
    checked: int
    monitor_notifications_sent: int
    emergency_notifications_sent: int
    failures: list[str]


def run_active_trade_monitor() -> MonitorRunSummary:
    settings = get_settings()
    trades = get_active_trades()

    checked = 0
    monitor_notifications_sent = 0
    emergency_notifications_sent = 0
    failures: list[str] = []

    for trade in trades:
        try:
            monitor_result = monitor_trade(trade)

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
        failures=failures,
    )
