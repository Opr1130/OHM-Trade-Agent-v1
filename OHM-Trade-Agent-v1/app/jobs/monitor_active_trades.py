from app.services.active_trade_monitor_runner import run_active_trade_monitor


def main():
    summary = run_active_trade_monitor()

    print("OHM AI Active Trade Monitor")
    print("Active trades:", summary.active_trades)
    print("Checked:", summary.checked)
    print(
        "Monitor notifications sent:",
        summary.monitor_notifications_sent,
    )
    print(
        "Emergency notifications sent:",
        summary.emergency_notifications_sent,
    )

    if summary.failures:
        print("Failures:")
        for failure in summary.failures:
            print("-", failure)


if __name__ == "__main__":
    main()
