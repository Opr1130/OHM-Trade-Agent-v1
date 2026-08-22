from app.core.config import get_settings
from app.services.alert_governor import evaluate_opportunity_alert, record_opportunity_alert
from app.services.full_market_observation import (
    ALERT_GOVERNOR_STATE_FILE as FULL_MARKET_ALERT_STATE_FILE,
    MarketTransition,
    process_full_market_observations,
)
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


def _observation_card(transition: MarketTransition) -> str:
    action = "DEEP REVIEW REQUIRED" if transition.alert_tier == "DEEP_REVIEW" else "WATCH ONLY"
    return (
        f"👀 OHM MARKET WATCH — {transition.symbol}\n"
        f"Pattern: {transition.pattern} | Score {transition.score}/100*\n"
        f"Since prior: {transition.price_change_since_prior_pct:+.2f}% | Lift Δ {transition.lift_change_since_prior_pct:+.2f}%\n"
        f"24h-low lift: {transition.lift_from_24h_low_pct:+.2f}% | Near high: {transition.distance_from_24h_high_pct:.2f}%\n"
        f"Liquidity: ${transition.liquidity_24h_usd_approx:,.0f}\n"
        f"Action: {action} — broad-market detection, not trade approval.\n"
        "*Heuristic transition score, not probability."
    )


def main() -> None:
    settings = get_settings()

    try:
        full_market = process_full_market_observations()
    except Exception as exc:
        full_market = None
        print("Full-market observation: fail-soft", type(exc).__name__)

    coarse, signals = scan_early_movers()

    print("===== OHM MOVEMENT DISCOVERY V2.1 + WAVE 5.2 =====")
    if full_market is not None:
        print("Full-market assets observed:", full_market.observed_markets)
        print("Full-market learning events persisted:", full_market.persisted_events)
        print("Broad transition watches detected:", len(full_market.transition_alerts))
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
    broad_created = 0
    broad_edited = 0
    broad_suppressed = 0
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
        eligible_signals = [item for item in signals if item.alert_eligible]
        deep_alert_symbols = {item.symbol.upper() for item in eligible_signals}
        for signal in eligible_signals[:10]:
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

        # Wave 5.2 broad-market alerts are intentionally a separate governor
        # budget so watch-only discoveries cannot consume the opportunity-card
        # budget. Symbols already receiving a deep-qualified card are omitted to
        # prevent duplicate Telegram noise.
        broad_candidates = [] if full_market is None else [
            item for item in full_market.transition_alerts
            if item.symbol.upper() not in deep_alert_symbols
        ]
        for transition in broad_candidates[:4]:
            identity = f"FULL_MARKET_WATCH:{transition.symbol}"
            decision = evaluate_opportunity_alert(
                identity=identity,
                transition_key=transition.transition_key,
                repeat_cooldown_seconds=repeat_cooldown,
                max_new_cards_24h=4,
                state_file=FULL_MARKET_ALERT_STATE_FILE,
            )
            if decision.action == "SUPPRESS":
                broad_suppressed += 1
                print(f"Broad-watch governor suppressed {transition.symbol}: {decision.reason}")
                continue

            message = _observation_card(transition)
            if decision.action == "EDIT" and decision.message_id is not None:
                if edit_telegram_message(
                    settings.telegram_bot_token,
                    settings.telegram_chat_id,
                    decision.message_id,
                    message,
                ):
                    record_opportunity_alert(
                        identity=identity,
                        transition_key=transition.transition_key,
                        message_id=decision.message_id,
                        created_new=False,
                        state_file=FULL_MARKET_ALERT_STATE_FILE,
                    )
                    broad_edited += 1
                else:
                    print(f"Telegram broad-watch edit failed for {transition.symbol}; card retained.")
                continue

            message_id = send_telegram_message_with_id(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                message,
            )
            if message_id is not None:
                record_opportunity_alert(
                    identity=identity,
                    transition_key=transition.transition_key,
                    message_id=message_id,
                    created_new=True,
                    state_file=FULL_MARKET_ALERT_STATE_FILE,
                )
                broad_created += 1

    print("Early-mover Telegram cards created:", created)
    print("Early-mover Telegram cards edited:", edited)
    print("Alert-governor opportunity updates suppressed:", suppressed)
    print("Broad-market watch cards created:", broad_created)
    print("Broad-market watch cards edited:", broad_edited)
    print("Broad-market watch updates suppressed:", broad_suppressed)


if __name__ == "__main__":
    main()
