from app.core.config import get_settings
from app.services.movement_discovery_v2 import append_detection_snapshots, scan_early_movers, send_early_mover_update


def main() -> None:
    settings = get_settings()
    coarse, signals = scan_early_movers()

    print("===== OHM MOVEMENT DISCOVERY V2.1 =====")
    print("Full-universe coarse movers:", len(coarse))
    print("Deep-qualified early movers:", len(signals))
    print("Telegram-eligible early movers:", sum(1 for signal in signals if signal.alert_eligible))
    print("Trade authority changed:", False)
    print("Actionable signals:", False)

    for signal in signals[:10]:
        print(
            f"MOVER {signal.symbol}: Stage={signal.stage} Discovery={signal.discovery_score} "
            f"Continuation={signal.continuation_confidence} Entry={signal.entry_quality} "
            f"Recommendation={signal.entry_recommendation} Momentum={signal.momentum_state} "
            f"1h={signal.momentum_1h_pct:+.2f}% 6h={signal.momentum_6h_pct:+.2f}% "
            f"24h={signal.momentum_24h_pct:+.2f}% Vol={signal.relative_volume:.2f}x "
            f"Liquidity=${signal.liquidity_24h_usd_approx:,.0f} Extended={signal.extended_move} "
            f"AlertEligible={signal.alert_eligible}"
        )

    try:
        print("Movement learning detections captured:", append_detection_snapshots(signals))
    except Exception as exc:
        print("Movement learning capture: fail-soft", type(exc).__name__)

    notifications = 0
    if (
        str(getattr(settings, "price_movement_mode", "shadow")).lower() == "alert"
        and settings.telegram_enabled
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    ):
        for signal in [item for item in signals if item.alert_eligible][:5]:
            if send_early_mover_update(
                signal,
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
                cooldown_seconds=min(
                    int(getattr(settings, "price_movement_alert_cooldown_seconds", 21600)),
                    1800,
                ),
            ):
                notifications += 1
    print("Early-mover Telegram notifications sent:", notifications)


if __name__ == "__main__":
    main()
