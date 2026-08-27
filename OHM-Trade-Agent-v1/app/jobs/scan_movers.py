from datetime import datetime, timezone

from app.core.config import get_settings
from app.services.alert_governor import evaluate_opportunity_alert, record_opportunity_alert
from app.services.compact_alerts import (
    downside_scenario_pct,
    explosion_band,
    heuristic_risk_score,
    one_line_reason,
)
from app.services.decision_telemetry import (
    record_decision_telemetry,
    record_phase3b_shadow_for_decision,
)
from app.services.full_market_observation import (
    ALERT_GOVERNOR_STATE_FILE as FULL_MARKET_ALERT_STATE_FILE,
    MarketTransition,
    process_full_market_observations,
)
from app.services.full_market_transition_learning import build_full_market_transition_summary
from app.services.movement_discovery_learning_capture import capture_movement_detections
from app.services.movement_discovery_v2 import scan_early_movers
from app.services.signal_scoring import (
    SignalQualityCandidate,
    SignalQualityConfig,
    main_feed_candidates,
    volume_growth_proxy_label,
)
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


def _maybe_record_decision_telemetry(full_market, settings, *, decision_at) -> None:
    """Phase 3A forward telemetry only; no external OHLC I/O occurs here."""
    if full_market is None:
        return
    try:
        record_decision_telemetry(
            full_market.signal_quality_candidates,
            settings=settings,
            reference_prices=full_market.signal_quality_reference_prices,
            now=decision_at,
        )
    except Exception as exc:
        print("Decision telemetry: fail-soft", type(exc).__name__)


def _maybe_record_phase3b_shadow(full_market, settings, *, decision_at) -> None:
    """Post-alert Phase 3B shadow enrichment using original decision time."""
    if full_market is None:
        return
    try:
        record_phase3b_shadow_for_decision(
            full_market.signal_quality_candidates,
            settings=settings,
            reference_prices=full_market.signal_quality_reference_prices,
            decision_at=decision_at,
            market_observations=full_market.market_observations,
        )
    except Exception as exc:
        print("Phase 3B shadow telemetry: fail-soft", type(exc).__name__)


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
    pattern = str(transition.pattern).replace("_", " ").title()
    action = "DEEP REVIEW" if transition.alert_tier == "DEEP_REVIEW" else "WATCH ONLY"
    return (
        f"🌐 OHM BROAD WATCH — {transition.symbol}\n"
        f"Pattern: {pattern}\n"
        f"Transition score*: {transition.score}/100\n"
        f"Liquidity: ${transition.liquidity_24h_usd_approx:,.0f} / 24h\n"
        f"Reason: {_observation_reason(transition)}\n"
        f"Action: {action} — no entry is authorized\n"
        "*Heuristic transition score, not probability. Prospective outcome calibration is still building."
    )


def _compact_usd(value: float) -> str:
    amount = float(value or 0.0)
    for unit, size in (("B", 1_000_000_000.0), ("M", 1_000_000.0), ("K", 1_000.0)):
        if abs(amount) >= size:
            return f"${amount / size:.1f}{unit}"
    return f"${amount:,.0f}"


def _ordinal(value: float) -> str:
    number = int(round(value))
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _signal_quality_transition_key(candidate: SignalQualityCandidate) -> str:
    """Identity for the alert governor: a card is edited, not re-sent, until
    the assessment genuinely moves."""
    return ":".join(
        (
            str(candidate.stage),
            str(candidate.pattern or "NONE"),
            str(candidate.opportunity_score // 10 * 10),
            str(candidate.exhaustion_band),
        )
    )


def _signal_quality_card(candidate: SignalQualityCandidate) -> str:
    """Render a Signal Quality v1 card.

    Every score carries a heuristic marker and the action line states plainly
    that no entry is authorised. ACTIONABLE_REVIEW is the top advisory stage,
    not a trade instruction.
    """
    pattern = str(candidate.pattern or "UNCLASSIFIED").replace("_", " ").title()
    stage = str(candidate.stage).replace("_", " ")
    return (
        f"🚀 OHM EARLY WATCH — {candidate.symbol}\n"
        f"Stage: {stage}\n"
        f"Pattern: {pattern}\n"
        f"Pattern strength*: {candidate.pattern_strength_score}/100\n"
        f"Tradeability*: {candidate.tradeability_score}/100\n"
        f"Explosion potential*: {candidate.explosion_potential_score}/100\n"
        f"Opportunity score*: {candidate.opportunity_score}/100\n"
        f"Liquidity: {_compact_usd(candidate.liquidity_24h_usd_approx)} / 24h\n"
        f"Relative strength: {_ordinal(candidate.relative_strength_percentile)} percentile\n"
        f"Persistence: {candidate.persistence_scans} consecutive scans\n"
        f"Volume growth proxy: {volume_growth_proxy_label(candidate.volume_acceleration_score)}\n"
        f"Exhaustion: {candidate.exhaustion_band}\n"
        "Action: HUMAN REVIEW ONLY — no entry is authorized\n"
        "*Heuristic scores, not probabilities; Phase 2 calibration pending."
    )


def _print_signal_quality_leaderboard(candidates, *, limit: int = 10) -> None:
    """Log the leaderboard, suppressed rows included.

    Suppressed candidates stay visible here precisely because they are kept out
    of the Telegram feed: the audit trail has to show what the gate rejected
    and why.
    """
    for candidate in candidates[:limit]:
        reasons = ",".join(candidate.reasons) or "-"
        print(
            f"SIGNAL_QUALITY {candidate.symbol}: Stage={candidate.stage} "
            f"Opportunity={candidate.opportunity_score} "
            f"Explosion={candidate.explosion_potential_score} "
            f"Tradeability={candidate.tradeability_score} "
            f"Pattern={candidate.pattern_strength_score} "
            f"RelStrength={candidate.relative_strength_score} "
            f"Persistence={candidate.persistence_scans} "
            f"Exhaustion={candidate.exhaustion_penalty} "
            f"Liquidity=${candidate.liquidity_24h_usd_approx:,.0f} "
            f"Reasons={reasons}"
        )
    for candidate in candidates:
        if candidate.suppressed:
            print(
                f"SIGNAL_QUALITY SUPPRESSED {candidate.symbol}: "
                f"Status: SUPPRESSED Reason: {','.join(candidate.reasons) or 'UNSPECIFIED'}"
            )


def _broad_watch_feed(
    full_market,
    *,
    settings,
    excluded_symbols: set[str],
) -> list[tuple[str, str, str]]:
    """Decide what the main Broad Watch Telegram feed is allowed to send.

    Returns (symbol, transition_key, message) triples.

    The previous implementation sliced ``transition_alerts[:4]`` directly. That
    truncated an unfiltered list, so a handful of thin WATCH_ONLY markets could
    occupy every slot and starve the tier the feed exists to surface. Filtering
    now happens before the cap.

    With Signal Quality v1 enabled, only BREAKOUT_CANDIDATE and
    ACTIONABLE_REVIEW reach the feed (plus EARLY_BUILDING behind its own flag).
    SUPPRESSED never does - it stays in the leaderboard and the logs. While the
    flag is off the legacy path is preserved byte for byte, because Phase 1
    ships dark.
    """
    if full_market is None:
        return []

    if getattr(full_market, "signal_quality_enabled", False):
        config = SignalQualityConfig.from_settings(settings)
        eligible = main_feed_candidates(
            [
                candidate
                for candidate in full_market.signal_quality_candidates
                if candidate.symbol.upper() not in excluded_symbols
            ],
            config=config,
        )
        return [
            (
                candidate.symbol,
                _signal_quality_transition_key(candidate),
                _signal_quality_card(candidate),
            )
            for candidate in eligible
        ]

    legacy = [
        item for item in full_market.transition_alerts
        if item.symbol.upper() not in excluded_symbols
    ]
    return [
        (item.symbol, item.transition_key, _observation_card(item))
        for item in legacy[:4]
    ]


def main() -> None:
    settings = get_settings()

    try:
        full_market = process_full_market_observations()
    except Exception as exc:
        full_market = None
        print("Full-market observation: fail-soft", type(exc).__name__)

    # This is the immutable time at which the full-market decision state exists.
    # Phase 3B may fetch later, but it must filter candles against this time.
    decision_at = datetime.now(timezone.utc)
    _maybe_record_decision_telemetry(
        full_market, settings, decision_at=decision_at
    )

    coarse, signals = scan_early_movers()

    print("===== OHM MOVEMENT DISCOVERY V2.1 + WAVE 5.2 =====")
    if full_market is not None:
        print("Full-market assets observed:", full_market.observed_markets)
        print("Full-market learning events persisted:", full_market.persisted_events)
        print("Broad transition watches detected:", len(full_market.transition_alerts))
        try:
            evidence = build_full_market_transition_summary()
            print(
                "Full-market prospective evidence:",
                evidence["status"],
                f"({evidence['outcome_rows']} outcome rows; min {evidence['minimum_samples_per_bucket']}/bucket)",
            )
        except Exception as exc:
            print("Full-market evidence summary: fail-soft", type(exc).__name__)
        print("Signal Quality v1 enabled:", full_market.signal_quality_enabled)
        if full_market.signal_quality_candidates:
            candidates = full_market.signal_quality_candidates
            print("Signal Quality candidates scored:", len(candidates))
            print(
                "Signal Quality suppressed:",
                sum(1 for item in candidates if item.suppressed),
            )
            _print_signal_quality_leaderboard(candidates)
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

        broad_feed = _broad_watch_feed(
            full_market,
            settings=settings,
            excluded_symbols=deep_alert_symbols,
        )
        for symbol, transition_key, message in broad_feed:
            identity = f"FULL_MARKET_WATCH:{symbol}"
            decision = evaluate_opportunity_alert(
                identity=identity,
                transition_key=transition_key,
                repeat_cooldown_seconds=repeat_cooldown,
                max_new_cards_24h=4,
                state_file=FULL_MARKET_ALERT_STATE_FILE,
            )
            if decision.action == "SUPPRESS":
                broad_suppressed += 1
                print(f"Broad-watch governor suppressed {symbol}: {decision.reason}")
                continue

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
                        state_file=FULL_MARKET_ALERT_STATE_FILE,
                    )
                    broad_edited += 1
                else:
                    print(f"Telegram broad-watch edit failed for {symbol}; card retained.")
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
                    state_file=FULL_MARKET_ALERT_STATE_FILE,
                )
                broad_created += 1

    # External OHLC work is deliberately deferred until every alert-critical
    # operation above has completed. It still uses the immutable decision_at.
    _maybe_record_phase3b_shadow(
        full_market, settings, decision_at=decision_at
    )

    print("Early-mover Telegram cards created:", created)
    print("Early-mover Telegram cards edited:", edited)
    print("Alert-governor opportunity updates suppressed:", suppressed)
    print("Broad-market watch cards created:", broad_created)
    print("Broad-market watch cards edited:", broad_edited)
    print("Broad-market watch updates suppressed:", broad_suppressed)


if __name__ == "__main__":
    main()
