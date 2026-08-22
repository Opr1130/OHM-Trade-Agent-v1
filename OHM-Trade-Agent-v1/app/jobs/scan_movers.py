from app.core.config import get_settings
from app.services.alert_governor import evaluate_opportunity_alert, record_opportunity_alert
from app.services.compact_alerts import (
    downside_scenario_pct,
    explosion_band,
    format_watch_alert,
    heuristic_risk_score,
    one_line_reason,
)
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


def _best_signal_reason(signal) -> str:
    reasons: list[str] = []
    if str(signal.momentum_state).upper() == "ACCELERATING":
        reasons.append("Momentum accelerating")
    if signal.relative_volume >= 1.5:
        reasons.append(f"Volume expanding {signal.relative_volume:.1f}x")
    if signal.distance_to_24h_high_pct <= 1.5:
        reasons.append("holding near the 24h high")
    if signal.extended_move:
        reasons.append("move already extended")
    if signal.liquidity_24h_usd_approx < 250_000:
        reasons.append("liquidity needs caution")
    fallback_reasons = getattr(signal, "reasons", ()) or ()
    return one_line_reason(" + ".join(reasons), *fallback_reasons)


def _compact_card(signal) -> str:
    low, high = explosion_band(signal.continuation_confidence, extended=signal.extended_move)
    risk = heuristic_risk_score(
        signal.continuation_confidence,
        liquidity_usd=signal.liquidity_24h_usd_approx,
        extended=signal.extended_move,
    )
    downside = downside_scenario_pct(risk)
    action = (
        "REVIEW ENTRY"
        if str(signal.entry_recommendation).upper() == "BREAKOUT_ENTRY_POSSIBLE"
        else "WATCH FOR PULLBACK"
    )
    return (
        f"🚀 OHM OPPORTUNITY — {signal.symbol} — {signal.stage}\n"
        f"Potential: +{low}% to +{high}%\n"
        f"Confidence*: {signal.continuation_confidence}% | Risk*: {risk}%\n"
        f"Downside if wrong*: up to -{downside}%\n"
        f"Reason: {_best_signal_reason(signal)}\n"
        f"Entry: {signal.entry_recommendation} | Action: {action}\n"
        "*Heuristic scenario scores, not probabilities."
    )


def _observation_reason(transition: MarketTransition) -> str:
    pattern = str(transition.pattern).replace("_", " ").title()
    return one_line_reason(
        f"{pattern} with {transition.price_change_since_prior_pct:+.1f}% acceleration since prior scan"
    )


def _observation_card(transition: MarketTransition) -> str:
    low, high = explosion_band(transition.score)
    risk = heuristic_risk_score(
        transition.score,
        liquidity_usd=transition.liquidity_24h_usd_approx,
    )
    downside = downside_scenario_pct(risk)
    action = "REVIEW ENTRY" if transition.alert_tier == "DEEP_REVIEW" else "WATCH ONLY"
    return format_watch_alert(
        symbol=transition.symbol,
        potential_low_pct=low,
        potential_high_pct=high,
        confidence_pct=transition.score,
        risk_pct=risk,
        downside_pct=downside,
        reason=_observation_reason(transition),
        action=action,
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
