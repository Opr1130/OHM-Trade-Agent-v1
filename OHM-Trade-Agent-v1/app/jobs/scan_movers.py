from app.core.config import get_settings
from app.services.alert_governor import evaluate_opportunity_alert, record_opportunity_alert
from app.services.movement_discovery_learning_capture import capture_movement_detections
from app.services.movement_discovery_v2 import scan_early_movers, send_early_mover_update


def _transition_key(signal) -> str:
    return ":".join(
        (
            str(signal.stage),
            str(signal.entry_recommendation),
            str(signal.momentum_state),
            "EXTENDED" if signal.extended_move else "NOT_EXTENDED",
        )
    )


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
        print("Movement learning detections captured:", capture_movement_detections(signals, coarse))
    except Exception as exc:
        print("Movement learning capture: fail-soft", type(exc).__name__)

    notifications = 0
    governed_suppressed = 0
    if (
        str(getattr(settings, "price_movement_mode", "shadow")).lower() == "alert"
        and settings.telegram_enabled
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    ):
        repeat_cooldown = max(
            int(getattr(settings, "price_movement_alert_cooldown_seconds", 21600)),
            21600,
        )
        for signal in [item for item in signals if item.alert_eligible][:10]:
            transition_key = _transition_key(signal)
            identity = f"EARLY_MOVER:{signal.symbol}"
            decision = evaluate_opportunity_alert(
                identity=identity,
                transition_key=transition_key,
                repeat_cooldown_seconds=repeat_cooldown,
                transition_cooldown_seconds=3600,
                max_immediate_alerts_24h=8,
            )
            if not decision.allow_immediate:
                governed_suppressed += 1
                print(
                    f"Alert governor suppressed {signal.symbol}: {decision.reason} "
                    f"transition={transition_key}"
                )
                continue
            # Governor owns opportunity timing. Keep legacy notification-policy
            # fingerprint dedupe, but do not apply a second cooldown that would
            # block a genuinely meaningful state transition.
            if send_early_mover_update(
                signal,
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
                cooldown_seconds=0,
            ):
                record_opportunity_alert(
                    identity=identity,
                    transition_key=transition_key,
                )
                notifications += 1
    print("Early-mover Telegram notifications sent:", notifications)
    print("Alert-governor opportunity notifications suppressed:", governed_suppressed)


if __name__ == "__main__":
    main()
