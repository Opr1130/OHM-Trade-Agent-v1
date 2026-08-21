from app.core.config import get_settings
from app.services.alert_governor import evaluate_opportunity_alert, record_opportunity_alert
from app.services.movement_discovery_learning_capture import capture_movement_detections
from app.services.movement_discovery_v2 import scan_early_movers
from app.services.telegram_notifier import edit_telegram_message, send_telegram_message_with_id


def _transition_key(signal) -> str:
    return ":".join(
        (
            str(signal.stage),
            str(signal.entry_recommendation),
            str(signal.momentum_state),
            "EXTENDED" if signal.extended_move else "NOT_EXTENDED",
        )
    )


def _compact_reasons(signal) -> list[str]:
    reasons: list[str] = []
    if str(signal.momentum_state).upper() == "ACCELERATING":
        reasons.append("Momentum accelerating")
    elif signal.momentum_1h_pct >= 1.0:
        reasons.append(f"1h momentum +{signal.momentum_1h_pct:.1f}%")

    if signal.relative_volume >= 1.5:
        reasons.append(f"Volume expanding {signal.relative_volume:.1f}x")
    elif signal.relative_volume < 0.5:
        reasons.append(f"Volume still light {signal.relative_volume:.1f}x")

    if signal.distance_to_24h_high_pct <= 1.5:
        reasons.append("Price holding near 24h high")

    if signal.extended_move:
        reasons.append("Move already extended")

    if signal.liquidity_24h_usd_approx < 250_000:
        reasons.append("Liquidity needs caution")

    return reasons[:3] or ["Qualified multi-factor movement state"]


def _compact_card(signal) -> str:
    why = " | ".join(_compact_reasons(signal))
    return (
        f"🚀 OHM {signal.symbol} — {signal.stage}\n"
        f"1h {signal.momentum_1h_pct:+.2f}% | 6h {signal.momentum_6h_pct:+.2f}% | Vol {signal.relative_volume:.2f}x\n"
        f"Momentum: {signal.momentum_state}\n"
        f"Entry: {signal.entry_recommendation}\n"
        f"Why: {why}\n"
        f"Quality: {signal.entry_quality}/100 | Continuation: {signal.continuation_confidence}/100*\n"
        "*Heuristic, not probability. MONITOR ONLY."
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

    created = 0
    edited = 0
    suppressed = 0
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
                max_new_cards_24h=8,
            )
            if decision.action == "SUPPRESS":
                suppressed += 1
                print(f"Alert governor suppressed {signal.symbol}: {decision.reason}")
                continue

            message = _compact_card(signal)
            if decision.action == "EDIT" and decision.message_id is not None:
                if edit_telegram_message(
                    settings.telegram_bot_token,
                    settings.telegram_chat_id,
                    decision.message_id,
                    message,
                ):
                    record_opportunity_alert(
                        identity=identity,
                        transition_key=transition_key,
                        message_id=decision.message_id,
                        created_new=False,
                    )
                    edited += 1
                else:
                    print(f"Telegram edit failed for {signal.symbol}; existing card retained for retry.")
                continue

            message_id = send_telegram_message_with_id(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                message,
            )
            if message_id is not None:
                record_opportunity_alert(
                    identity=identity,
                    transition_key=transition_key,
                    message_id=message_id,
                    created_new=True,
                )
                created += 1

    print("Early-mover Telegram cards created:", created)
    print("Early-mover Telegram cards edited:", edited)
    print("Alert-governor opportunity updates suppressed:", suppressed)


if __name__ == "__main__":
    main()
