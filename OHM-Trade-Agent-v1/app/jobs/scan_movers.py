from datetime import datetime, timezone
import logging

from app.core.config import get_settings
from app.exchanges.kraken_identity import canonicalize_asset, split_canonical_pair
from app.opip.decision.identity import opip_scan_id
from app.opip.decision.screening import (
    ScannerType,
    ScreeningEvaluation,
    ScreeningOutcome,
)
from app.opip.decision.store import (
    append_screening_evaluations,
    opip_funnel_telemetry_enabled,
    retention_capacity_health,
)
from app.opip.identity import resolve_venue_instrument_identity
from app.services.alert_governor import (
    evaluate_opportunity_alert,
    record_opportunity_alert,
    release_opportunity_alert_reservation,
)
from app.services.asset_display_identity import display_market_label
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
from app.services.intelligence_journey import record_watch_observation
from app.services.movement_discovery_learning_capture import capture_movement_detections
from app.services.movement_discovery_v2 import scan_early_movers
from app.services.opportunity_monitor_queue import CandidateObservation, upsert_candidate
from app.services.signal_scoring import (
    SignalQualityCandidate,
    SignalQualityConfig,
    main_feed_candidates,
    volume_growth_proxy_label,
)
from app.services.telegram_delivery import (
    edit_tracked_telegram,
    link_delivery_to_journey,
    record_telegram_suppression,
    send_tracked_telegram,
)


logger = logging.getLogger(__name__)


def _screening_capture_callback(*, rows, observed_at, scan_id):
    """Build a measurement-only Early Watch callback."""
    def capture(payload) -> None:
        raw = str(payload.get("raw_identifier") or "")
        try:
            rows.append(
                ScreeningEvaluation(
                    observed_at=observed_at,
                    scan_id=scan_id,
                    scanner_type=ScannerType.EARLY_WATCH,
                    venue_instrument=resolve_venue_instrument_identity(
                        raw,
                        canonicalize_asset=canonicalize_asset,
                        split_canonical_pair=split_canonical_pair,
                        resolved_at_utc=observed_at,
                    ),
                    outcome=ScreeningOutcome(str(payload["outcome"])),
                    long_score=payload.get("long_score"),
                    short_score=payload.get("short_score"),
                    advanced_direction=payload.get("advanced_direction"),
                    reason=payload.get("reason"),
                    metadata=payload.get("metadata"),
                ).to_dict()
            )
        except Exception as exc:
            logger.warning(
                "O'Pip screening capture failed open scanner_type=EARLY_WATCH "
                "scan_id=%s raw=%s operation=build_screening_evaluation error=%s",
                scan_id,
                raw or "UNKNOWN",
                type(exc).__name__,
            )

    return capture


def _transition_key(signal) -> str:
    return ":".join(
        (
            str(signal.stage),
            str(signal.entry_recommendation),
            str(signal.momentum_state),
            "EXTENDED" if signal.extended_move else "NOT_EXTENDED",
        )
    )


def _deliver_existing_card_update(
    *,
    settings,
    decision,
    message: str,
    identity: str,
    alert_family: str,
    event_type: str,
    fingerprint: str,
    symbol: str,
) -> tuple[str, int | None]:
    """Deliver an existing opportunity update without hiding meaningful changes."""
    if decision.reason == "MEANINGFUL_TRANSITION":
        delivery = send_tracked_telegram(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            message=message,
            identity=identity,
            alert_family=alert_family,
            event_type=event_type,
            fingerprint=fingerprint,
            symbol=symbol,
            success_status="TRANSITION_PUSHED",
        )
        if not delivery.delivered:
            return "TRANSITION_PUSH_FAILED", None
        return "TRANSITION_PUSHED", delivery.message_id

    delivery = edit_tracked_telegram(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        message_id=decision.message_id,
        message=message,
        identity=identity,
        alert_family=alert_family,
        event_type=event_type,
        fingerprint=fingerprint,
        symbol=symbol,
    )
    if delivery.delivered:
        return "EDITED", delivery.message_id
    return "EDIT_FAILED", None


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
    return (
        f"🚀 EARLY WATCH — {display_market_label(signal.symbol)} — {signal.stage}\n"
        f"Price: {float(getattr(signal, 'reference_price', 0.0)):.8g} | TF: {getattr(signal, 'detection_timeframe', '1H')}\n"
        f"Momentum: 1h {signal.momentum_1h_pct:+.2f}% | 6h {signal.momentum_6h_pct:+.2f}% | {signal.momentum_state}\n"
        f"Potential*: +{low}% to +{high}% | Confidence*: {signal.continuation_confidence}%\n"
        f"Risk*: {risk}% | Downside scenario*: up to -{downside}%\n"
        f"Why now: {_best_signal_reason(signal)}\n"
        f"Entry context: {signal.entry_recommendation}\n"
        "Action: WATCH ONLY — no entry is authorized"
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
        f"🌐 OHM BROAD WATCH — {display_market_label(transition.symbol)}\n"
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


def _signal_quality_card(candidate: SignalQualityCandidate, *, reference_price: float | None = None) -> str:
    """Render a Signal Quality v1 card.

    Every score carries a heuristic marker and the action line states plainly
    that no entry is authorised. ACTIONABLE_REVIEW is the top advisory stage,
    not a trade instruction.
    """
    pattern = str(candidate.pattern or "UNCLASSIFIED").replace("_", " ").title()
    stage = str(candidate.stage).replace("_", " ")
    return (
        f"🚀 EARLY WATCH — {display_market_label(candidate.symbol)}\n"
        + (f"Price: {float(reference_price):.8g}\n" if isinstance(reference_price, (int, float)) and float(reference_price) > 0 else "")
        + f"Stage: {stage}\n"
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
                _signal_quality_card(
                    candidate,
                    reference_price=(getattr(full_market, "signal_quality_reference_prices", {}) or {}).get(candidate.symbol),
                ),
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



def _enqueue_wave9_monitoring(full_market, signals, *, observed_at: datetime) -> tuple[int, int]:
    """Feed Wave 9's silent monitoring queue from existing discovery evidence."""
    queued = 0
    failures = 0

    if full_market is not None:
        reference_prices = getattr(full_market, "signal_quality_reference_prices", {}) or {}
        for candidate in getattr(full_market, "signal_quality_candidates", ()) or ():
            try:
                if bool(getattr(candidate, "suppressed", False)):
                    continue
                upsert_candidate(
                    CandidateObservation(
                        symbol=str(candidate.symbol),
                        direction=str(getattr(candidate, "direction", "LONG") or "LONG"),
                        source="FULL_MARKET_RELATIVE_STRENGTH_VOLUME",
                        observed_at=observed_at,
                        price=reference_prices.get(candidate.symbol),
                        relative_strength_percentile=float(
                            getattr(candidate, "relative_strength_percentile", 0.0)
                        ),
                        volume_acceleration_score=float(
                            getattr(candidate, "volume_acceleration_score", 0.0)
                        ),
                        liquidity_usd=float(
                            getattr(candidate, "liquidity_24h_usd_approx", 0.0)
                        ),
                        priority_score=float(
                            getattr(candidate, "opportunity_score", 0.0)
                        ),
                    )
                )
                queued += 1
            except Exception:
                failures += 1

    for signal in signals or ():
        try:
            # Deep early-mover evidence adds a second independent discovery
            # source. The queue merges by market/direction and remains silent.
            volume_score = max(0.0, min(100.0, float(signal.relative_volume) * 25.0))
            upsert_candidate(
                CandidateObservation(
                    symbol=str(signal.symbol),
                    direction=str(signal.direction or "LONG"),
                    source="EARLY_MOVER_PARTICIPATION",
                    observed_at=observed_at,
                    price=float(getattr(signal, "reference_price", 0.0) or 0.0),
                    relative_strength_percentile=None,
                    volume_acceleration_score=volume_score,
                    liquidity_usd=float(signal.liquidity_24h_usd_approx),
                    priority_score=float(
                        max(signal.discovery_score, signal.entry_quality)
                    ),
                )
            )
            queued += 1
        except Exception:
            failures += 1

    return queued, failures


def _watch_telegram_enabled(settings) -> bool:
    return bool(
        not bool(getattr(settings, "opip_actionable_only_alerts", False))
        and str(getattr(settings, "price_movement_mode", "shadow")).lower() == "alert"
        and settings.telegram_enabled
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    )


def _early_watch_telegram_enabled(settings) -> bool:
    """Allow only explicit, advisory Early Watch Telegram under Wave 9.

    This is intentionally independent from the legacy broad-watch transport.
    OPIP_ACTIONABLE_ONLY_ALERTS may stay enabled while an operator opts into
    deep-qualified EARLY_MOVER cards. These cards remain WATCH ONLY and never
    grant entry, paper, or exchange authority.
    """
    return bool(
        bool(getattr(settings, "opip_early_watch_alerts_enabled", False))
        and settings.telegram_enabled
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    )


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

    screening_enabled = opip_funnel_telemetry_enabled()
    screening_rows: list[dict] = []
    screening_scan_id = opip_scan_id(
        cohort_id="EARLY_WATCH",
        decision_at=decision_at,
    )
    if screening_enabled:
        screening_callback = _screening_capture_callback(
            rows=screening_rows,
            observed_at=decision_at,
            scan_id=screening_scan_id,
        )
        coarse, signals = scan_early_movers(
            on_coarse_evaluated=screening_callback,
            on_evaluated=screening_callback,
            scan_id=screening_scan_id,
        )
        append_screening_evaluations(screening_rows, enabled=True)
        observed_universe = next(
            (
                int(metadata["universe_count"])
                for row in screening_rows
                if isinstance((metadata := row.get("metadata")), dict)
                and metadata.get("universe_count") is not None
            ),
            None,
        )
        try:
            capacity = retention_capacity_health(
                observed_early_watch_universe=observed_universe,
            )
            logger.info("O'Pip Stage 0 retention capacity health: %s", capacity.as_dict())
        except Exception as exc:
            logger.warning(
                "O'Pip retention capacity health failed open: %s",
                type(exc).__name__,
            )
    else:
        # Keep the historical call signature on the default-dark path.
        coarse, signals = scan_early_movers()

    try:
        queue_added, queue_failures = _enqueue_wave9_monitoring(
            full_market,
            signals,
            observed_at=decision_at,
        )
        print(
            "Wave 9 silent monitoring queue:",
            f"observations={queue_added}",
            f"failures={queue_failures}",
        )
    except Exception as exc:
        print("Wave 9 silent monitoring queue: fail-soft", type(exc).__name__)

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
    transition_pushes = 0
    transition_push_failures = 0
    early_mover_delivery: dict[str, tuple[str, bool]] = {}
    broad_watch_delivery: dict[str, tuple[str, bool]] = {}
    early_watch_transport_enabled = _early_watch_telegram_enabled(settings)
    broad_watch_transport_enabled = _watch_telegram_enabled(settings)
    if early_watch_transport_enabled or broad_watch_transport_enabled:
        repeat_cooldown = max(
            int(getattr(settings, "price_movement_alert_cooldown_seconds", 21600)),
            21600,
        )
        eligible_signals = (
            [item for item in signals if item.alert_eligible]
            if early_watch_transport_enabled
            else []
        )
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
                early_mover_delivery[signal.symbol.upper()] = ("SUPPRESSED", False)
                record_telegram_suppression(
                    identity=identity,
                    alert_family="EARLY_MOVER",
                    event_type=signal.stage,
                    fingerprint=transition_key,
                    reason=decision.reason,
                    symbol=signal.symbol,
                    generated_at=decision_at,
                )
                print(f"Alert governor suppressed {signal.symbol}: {decision.reason}")
                continue

            message = _compact_card(signal)
            if decision.action == "EDIT" and decision.message_id is not None:
                delivery_action, delivered_message_id = _deliver_existing_card_update(
                    settings=settings,
                    decision=decision,
                    message=message,
                    identity=identity,
                    alert_family="EARLY_MOVER",
                    event_type=signal.stage,
                    fingerprint=transition_key,
                    symbol=signal.symbol,
                )
                if delivered_message_id is not None:
                    record_opportunity_alert(
                        identity=identity,
                        transition_key=transition_key,
                        message_id=delivered_message_id,
                        created_new=False,
                    )
                    edited += 1
                    if delivery_action == "TRANSITION_PUSHED":
                        transition_pushes += 1
                    early_mover_delivery[signal.symbol.upper()] = (delivery_action, True)
                else:
                    if delivery_action == "TRANSITION_PUSH_FAILED":
                        transition_push_failures += 1
                    early_mover_delivery[signal.symbol.upper()] = (delivery_action, False)
                    print(
                        f"Telegram update failed for {signal.symbol}: "
                        f"{delivery_action}; prior canonical card retained for retry."
                    )
                continue

            try:
                delivery = send_tracked_telegram(
                    bot_token=settings.telegram_bot_token,
                    chat_id=settings.telegram_chat_id,
                    message=message,
                    identity=identity,
                    alert_family="EARLY_MOVER",
                    event_type=signal.stage,
                    fingerprint=transition_key,
                    symbol=signal.symbol,
                    generated_at=decision_at,
                )
            except Exception as exc:
                release_opportunity_alert_reservation(decision.reservation_token)
                early_mover_delivery[signal.symbol.upper()] = ("CREATE_FAILED", False)
                print(
                    f"Telegram create failed for {signal.symbol}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            if delivery.delivered and delivery.message_id is not None:
                record_opportunity_alert(
                    identity=identity,
                    transition_key=transition_key,
                    message_id=delivery.message_id,
                    created_new=True,
                    reservation_token=decision.reservation_token,
                )
                created += 1
                early_mover_delivery[signal.symbol.upper()] = ("CREATED", True)
            else:
                release_opportunity_alert_reservation(decision.reservation_token)
                early_mover_delivery[signal.symbol.upper()] = ("CREATE_FAILED", False)

        broad_feed = (
            _broad_watch_feed(
                full_market,
                settings=settings,
                excluded_symbols=deep_alert_symbols,
            )
            if broad_watch_transport_enabled
            else []
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
                broad_watch_delivery[symbol.upper()] = ("SUPPRESSED", False)
                record_telegram_suppression(
                    identity=identity,
                    alert_family="BROAD_WATCH",
                    event_type=transition_key.split(":", 1)[0],
                    fingerprint=transition_key,
                    reason=decision.reason,
                    symbol=symbol,
                    generated_at=decision_at,
                )
                print(f"Broad-watch governor suppressed {symbol}: {decision.reason}")
                continue

            if decision.action == "EDIT" and decision.message_id is not None:
                delivery_action, delivered_message_id = _deliver_existing_card_update(
                    settings=settings,
                    decision=decision,
                    message=message,
                    identity=identity,
                    alert_family="BROAD_WATCH",
                    event_type=transition_key.split(":", 1)[0],
                    fingerprint=transition_key,
                    symbol=symbol,
                )
                if delivered_message_id is not None:
                    record_opportunity_alert(
                        identity=identity,
                        transition_key=transition_key,
                        message_id=delivered_message_id,
                        created_new=False,
                        state_file=FULL_MARKET_ALERT_STATE_FILE,
                    )
                    broad_edited += 1
                    if delivery_action == "TRANSITION_PUSHED":
                        transition_pushes += 1
                    broad_watch_delivery[symbol.upper()] = (delivery_action, True)
                else:
                    if delivery_action == "TRANSITION_PUSH_FAILED":
                        transition_push_failures += 1
                    broad_watch_delivery[symbol.upper()] = (delivery_action, False)
                    print(
                        f"Telegram broad-watch update failed for {symbol}: "
                        f"{delivery_action}; prior canonical card retained for retry."
                    )
                continue

            try:
                delivery = send_tracked_telegram(
                    bot_token=settings.telegram_bot_token,
                    chat_id=settings.telegram_chat_id,
                    message=message,
                    identity=identity,
                    alert_family="BROAD_WATCH",
                    event_type=transition_key.split(":", 1)[0],
                    fingerprint=transition_key,
                    symbol=symbol,
                    generated_at=decision_at,
                )
            except Exception as exc:
                release_opportunity_alert_reservation(
                    decision.reservation_token,
                    state_file=FULL_MARKET_ALERT_STATE_FILE,
                )
                broad_watch_delivery[symbol.upper()] = ("CREATE_FAILED", False)
                print(
                    f"Telegram broad-watch create failed for {symbol}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            if delivery.delivered and delivery.message_id is not None:
                record_opportunity_alert(
                    identity=identity,
                    transition_key=transition_key,
                    message_id=delivery.message_id,
                    created_new=True,
                    reservation_token=decision.reservation_token,
                    state_file=FULL_MARKET_ALERT_STATE_FILE,
                )
                broad_created += 1
                broad_watch_delivery[symbol.upper()] = ("CREATED", True)
            else:
                release_opportunity_alert_reservation(
                    decision.reservation_token,
                    state_file=FULL_MARKET_ALERT_STATE_FILE,
                )
                broad_watch_delivery[symbol.upper()] = ("CREATE_FAILED", False)

    # Intelligence-journey persistence is deliberately post-alert and
    # measurement-only. It links early evidence to later qualified signals and
    # Freqtrade dry-run outcomes without participating in the live decision.
    try:
        if full_market is not None and getattr(full_market, "signal_quality_enabled", False):
            learning_config = SignalQualityConfig.from_settings(settings)
            learning_candidates = main_feed_candidates(
                full_market.signal_quality_candidates,
                config=learning_config,
            )
            for candidate in learning_candidates:
                delivery_action, delivered = broad_watch_delivery.get(
                    candidate.symbol.upper(),
                    ("NOT_DELIVERED_OR_EXCLUDED", False),
                )
                journey_id = record_watch_observation(
                    symbol=candidate.symbol,
                    observed_at=decision_at,
                    watch_type="EARLY_WATCH",
                    payload={
                        "stage": candidate.stage,
                        "pattern": candidate.pattern,
                        "tradeability_score": candidate.tradeability_score,
                        "pattern_strength_score": candidate.pattern_strength_score,
                        "volume_acceleration_score": candidate.volume_acceleration_score,
                        "persistence_score": candidate.persistence_score,
                        "relative_strength_score": candidate.relative_strength_score,
                        "explosion_potential_score": candidate.explosion_potential_score,
                        "opportunity_score": candidate.opportunity_score,
                        "exhaustion_penalty": candidate.exhaustion_penalty,
                        "exhaustion_band": candidate.exhaustion_band,
                        "liquidity_24h_usd_approx": candidate.liquidity_24h_usd_approx,
                        "persistence_scans": candidate.persistence_scans,
                        "relative_strength_percentile": candidate.relative_strength_percentile,
                        "reasons": list(candidate.reasons),
                        "telegram_alert_eligible": True,
                    },
                    delivery_action=delivery_action,
                    delivered=delivered,
                )
                link_delivery_to_journey(
                    identity=f"FULL_MARKET_WATCH:{candidate.symbol}",
                    alert_family="BROAD_WATCH",
                    event_type=candidate.stage,
                    fingerprint=_signal_quality_transition_key(candidate),
                    journey_id=journey_id,
                )
        for signal in signals:
            if not signal.alert_eligible:
                continue
            delivery_action, delivered = early_mover_delivery.get(
                signal.symbol.upper(),
                ("NOT_DELIVERED", False),
            )
            journey_id = record_watch_observation(
                symbol=signal.symbol,
                observed_at=decision_at,
                watch_type="EARLY_MOVER",
                payload={
                    "stage": signal.stage,
                    "pattern": "MOVEMENT_DISCOVERY",
                    "discovery_score": signal.discovery_score,
                    "continuation_confidence": signal.continuation_confidence,
                    "entry_quality": signal.entry_quality,
                    "entry_recommendation": signal.entry_recommendation,
                    "momentum_state": signal.momentum_state,
                    "momentum_1h_pct": signal.momentum_1h_pct,
                    "momentum_6h_pct": signal.momentum_6h_pct,
                    "momentum_24h_pct": signal.momentum_24h_pct,
                    "relative_volume": signal.relative_volume,
                    "liquidity_24h_usd_approx": signal.liquidity_24h_usd_approx,
                    "extended_move": signal.extended_move,
                    "telegram_alert_eligible": True,
                },
                delivery_action=delivery_action,
                delivered=delivered,
            )
            link_delivery_to_journey(
                identity=f"EARLY_MOVER:{signal.symbol}",
                alert_family="EARLY_MOVER",
                event_type=signal.stage,
                fingerprint=_transition_key(signal),
                journey_id=journey_id,
            )
    except Exception as exc:
        print("Intelligence journey watch capture: fail-soft", type(exc).__name__)

    # External OHLC work is deliberately deferred until every alert-critical
    # operation above has completed. It still uses the immutable decision_at.
    _maybe_record_phase3b_shadow(
        full_market, settings, decision_at=decision_at
    )

    print("Early-mover Telegram cards created:", created)
    print("Early-mover Telegram cards edited/transitioned:", edited)
    print("Meaningful-transition push notifications sent:", transition_pushes)
    print("Meaningful-transition push notification failures:", transition_push_failures)
    print("Alert-governor opportunity updates suppressed:", suppressed)
    print("Broad-market watch cards created:", broad_created)
    print("Broad-market watch cards edited:", broad_edited)
    print("Broad-market watch updates suppressed:", broad_suppressed)
