from app.core.config import get_settings
from app.services.pending_setup_monitor import monitor_pending_setup
from app.services.pending_setup_notifier import (
    retry_terminal_pending_notifications,
    send_pending_setup_update,
    terminal_notification_pending,
)
from app.services.pending_setup_registry import expire_due_pending_setups, get_pending_setups


def main():
    settings = get_settings()

    # Drain terminal truth before taking the live pending snapshot. This avoids
    # processing a stale in-memory waiting setup that was just terminalized.
    retry_sent, retry_failures = retry_terminal_pending_notifications(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )
    expired = expire_due_pending_setups()
    setups = get_pending_setups()

    checked = 0
    quarantined = 0
    notifications_sent = retry_sent
    failures: list[str] = []
    if retry_failures:
        failures.append(f"terminal-alert outbox retries pending/failed: {retry_failures}")

    for setup in setups:
        if terminal_notification_pending(setup.trade_id):
            quarantined += 1
            print(
                setup.symbol,
                "QUARANTINED",
                "terminal transition pending; entry monitoring suppressed",
            )
            continue
        try:
            result = monitor_pending_setup(setup)
            checked += 1

            if send_pending_setup_update(
                setup=setup,
                result=result,
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            ):
                notifications_sent += 1

            print(setup.symbol, result.status, result.current_price)
        except Exception as exc:
            failures.append(f"{setup.symbol}: {exc}")

    print()
    print("OHM AI Pending Setup Monitor")
    print("Expired stale setups:", expired)
    print("Pending setups:", len(setups))
    print("Checked:", checked)
    print("Quarantined terminal-transition setups:", quarantined)
    print("Notifications sent:", notifications_sent)
    print("Terminal alert retries sent:", retry_sent)
    print("Terminal alert retries pending/failed:", retry_failures)

    if failures:
        print("Failures:")
        for failure in failures:
            print("-", failure)


if __name__ == "__main__":
    main()
