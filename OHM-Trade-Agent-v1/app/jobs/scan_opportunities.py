import logging
import inspect
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace

from app.core.config import get_settings
from app.exchanges.kraken_identity import canonicalize_asset, split_canonical_pair
from app.opip.decision.screening import (
    ScannerType,
    ScreeningEvaluation,
    ScreeningOutcome,
)
from app.opip.decision.store import append_screening_evaluations
from app.opip.identity import resolve_venue_instrument_identity
from app.opip.decision.observer import build_scan_observer
from app.opip.events.provider_health import ProviderHealthStore
from app.scanner.directional_candidates import MAX_PER_DIRECTION, select_directional_candidates
from app.scanner.global_market_context import load_coingecko_global_context
from app.scanner.margin_eligibility import (
    bound_recovered_longs,
    keep_margin_tradeable_candidates,
    recover_margin_rejected_longs,
    validate_short_margin_eligibility,
)
from app.scanner.market_regime import evaluate_market_regime
from app.scanner.market_scanner import (
    confirm_secondary_markets,
    deep_validate_candidates,
    scan_market,
)
from app.scanner.news_context import validate_finalist_news
from app.scanner.reference_market_validation import validate_finalist_references
from app.scanner.scheduled_catalysts import validate_scheduled_catalysts
from app.scanner.short_execution_quality import short_execution_is_tradeable
from app.scanner.universe import DEFAULT_UNIQUE_ASSET_LIMIT
from app.services.active_trade_registry import get_active_trades
from app.services.capital_efficiency_ranking import rank_capital_efficiency
from app.services.canonical_episode_capture import (
    append_canonical_episode_snapshots,
    canonical_cohort_id,
    canonical_episode_id,
)
from app.services.chief_alert_notifier import send_trade_plan
from app.services.chief_analyst import (
    SHORT_MARGIN_COST_RESERVE_PCT,
    SHORT_MAX_ACCOUNT_RISK_AT_STOP_PCT,
    SHORT_VALIDATION_LEVERAGE,
    review_candidates,
)
from app.services.economic_quality_gate import (
    PRODUCTION_MAX_CAPITAL_FRACTION,
    evaluate_economic_quality,
)
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.entry_watch_queue import enqueue_entry_watch, remove_entry_watch
from app.services.freqtrade_signal_bridge import build_signal_id, to_freqtrade_pair
from app.services.market_intelligence_integration import enrich_finalist_market_intelligence
from app.services.price_movement_learning import (
    get_latest_price_movement,
    record_price_movement,
)
from app.services.price_movement_notifier import send_price_movement_update
from app.services.price_movement_radar import evaluate_price_movement
from app.services.profit_ranking import (
    QualifiedOpportunity,
    RankedOpportunity,
    rank_profit_opportunities,
)
from app.services.recommendation_gate import qualified_alerts
from app.services.shadow_decision_capture import capture_snapshot_decision
from app.services.short_target_attainability import evaluate_short_target_attainability
from app.services.target_attainability import evaluate_target_attainability
from app.services.trade_action_gate import apply_action_gate
from app.services.trade_feature_snapshot import build_trade_feature_snapshot
from app.services.trade_quality_assessor import assess_trade_quality
from app.services.trade_quality_evidence_registry import record_trade_quality_evidence


logger = logging.getLogger(__name__)


# Backwards-compatible test/extension seam. Production default is the new mixed
# directional selector; existing callers that patch select_candidates still work.
select_candidates = select_directional_candidates


def _broad_screening_callback(*, rows, observed_at, scan_id, universe_count):
    """Build the fail-soft, evidence-only Broad Search callback."""
    def capture(snapshot, long_score, short_score, advanced_direction) -> None:
        raw = str(
            getattr(snapshot, "primary_pair", None)
            or getattr(snapshot, "symbol", "")
        )
        try:
            rows.append(
                ScreeningEvaluation(
                    observed_at=observed_at,
                    scan_id=scan_id,
                    scanner_type=ScannerType.BROAD_SEARCH,
                    venue_instrument=resolve_venue_instrument_identity(
                        raw,
                        canonicalize_asset=canonicalize_asset,
                        split_canonical_pair=split_canonical_pair,
                        resolved_at_utc=observed_at,
                    ),
                    outcome=(
                        ScreeningOutcome.ADVANCED
                        if advanced_direction is not None
                        else ScreeningOutcome.BELOW_THRESHOLD
                    ),
                    long_score=long_score,
                    short_score=short_score,
                    advanced_direction=advanced_direction,
                    reason=(
                        "directional technical threshold cleared"
                        if advanced_direction is not None
                        else "neither directional technical score cleared the threshold"
                    ),
                    metadata={"universe_count": int(universe_count)},
                ).to_dict()
            )
        except Exception as exc:
            logger.warning(
                "O'Pip screening capture failed open scanner_type=BROAD_SEARCH "
                "scan_id=%s raw=%s operation=build_screening_evaluation error=%s",
                scan_id,
                raw or "UNKNOWN",
                type(exc).__name__,
            )

    return capture


def _screening_scan_id(observer) -> str:
    """Read the observer identity only for joining measurement rows."""
    return str(observer.funnel.scan_id)


def _record_coingecko_health_fail_open(settings, reference_summary, global_context) -> None:
    """Persist reference-provider health without affecting scan decisions."""
    if not bool(getattr(settings, "opip_event_store_enabled", False)):
        return
    try:
        store = ProviderHealthStore()
        now = datetime.now(timezone.utc)
        interval = int(
            getattr(settings, "opip_event_ingest_interval_seconds", 300)
        )
        source_observed = now
        updated_at = getattr(global_context, "updated_at", None)
        if isinstance(updated_at, int):
            source_observed = datetime.fromtimestamp(
                updated_at,
                tz=timezone.utc,
            )

        global_available = getattr(global_context, "status", None) == "AVAILABLE"
        reference_available = int(getattr(reference_summary, "available", 0) or 0)
        reference_unavailable = int(
            getattr(reference_summary, "unavailable", 0) or 0
        )
        reasons: list[str] = []
        if not global_available:
            reasons.append("global market context unavailable")
        if reference_unavailable:
            reasons.append(
                f"{reference_unavailable} reference candidate(s) unavailable"
            )
        reference_rate_limited = bool(
            getattr(reference_summary, "rate_limited", False)
        )
        global_rate_limited = bool(
            getattr(global_context, "rate_limited", False)
        )
        if reference_rate_limited or global_rate_limited:
            reasons.append("one or more CoinGecko requests were rate limited")

        if global_available or reference_available:
            store.record_context_success(
                provider="COINGECKO",
                checked_at=now,
                expected_interval_seconds=interval,
                request_count=2,
                source_observed_at=source_observed,
                degraded_reason="; ".join(reasons) or None,
            )
        else:
            retry_after_values = [
                value
                for value in (
                    getattr(reference_summary, "retry_after_seconds", None),
                    getattr(global_context, "retry_after_seconds", None),
                )
                if isinstance(value, int)
            ]
            store.record_unavailable(
                provider="COINGECKO",
                checked_at=now,
                expected_interval_seconds=interval,
                request_count=2,
                rate_limited=reference_rate_limited or global_rate_limited,
                retry_after_seconds=(
                    max(retry_after_values)
                    if retry_after_values
                    else None
                ),
                reason="CoinGecko reference and global requests unavailable",
                error_kind=(
                    getattr(reference_summary, "provider_error_kind", None)
                    or getattr(global_context, "provider_error_kind", None)
                    or "CoinGeckoAPIError"
                ),
            )
    except Exception:
        logger.exception("O'Pip CoinGecko health persistence failed open")


def _opip_scan_context(scan, technical_candidates: int) -> dict:
    """Return the scan-level counters the O'Pip funnel summary reports.

    ``technical_candidates`` is the size of the directional shortlist as
    selected, not the number of survivors at the point the summary is written -
    the funnel's own counters already describe attrition, and reporting the
    survivor count here would understate how many candidates were considered.
    """
    return {
        "requested": getattr(scan, "requested", None),
        "analyzed": getattr(scan, "analyzed", None),
        "skipped": getattr(scan, "skipped", None),
        "failed": getattr(scan, "failed", None),
        "technical_candidates": int(technical_candidates),
    }


def _paper_trade_enabled_safe() -> bool:
    """Report paper-engine state for telemetry without ever raising."""
    try:
        from app.services.paper_trade_control import paper_trade_enabled

        return bool(paper_trade_enabled())
    except Exception:
        return False


def _direction_counts(candidates):
    return (
        sum(c.trade_direction == "LONG" for c in candidates),
        sum(c.trade_direction == "SHORT" for c in candidates),
    )


def _capture_native_scan_cohort(scan, *, decision_at):
    """Capture the exact native production scan cohort for O'Pip evidence.

    Measurement-only and fail-soft: this writes to the existing P1 shadow
    outbox when enabled, but cannot change ranking, Telegram, PendingSetup,
    Chief review, or trading authority.
    """
    try:
        written = append_canonical_episode_snapshots(
            scan.snapshots,
            candidates=(),
            decision_at=decision_at,
            signal_quality_enabled=False,
            scan_source="LIVE_OPPORTUNITY_SCAN",
        )
    except Exception as exc:
        logger.warning(
            "O'Pip canonical native capture failed open: %s",
            type(exc).__name__,
        )
        return 0
    print("O'Pip canonical native episodes captured:", written)
    return written


def _maybe_enroll_paper_opportunities(
    ranked_opportunities,
    *,
    scan,
    decision_at,
    settings,
) -> tuple[int, int]:
    """Post-alert paper enrollment; never participates in live decisions."""
    try:
        from app.services.paper_trade_control import paper_trade_enabled
        from app.services.paper_trade_engine import (
            PaperTradeConfig,
            enroll_paper_opportunity,
        )

        if not paper_trade_enabled():
            return 0, 0
        config = PaperTradeConfig.from_settings(settings)
        cohort_id = canonical_cohort_id(
            scan.snapshots,
            decision_at=decision_at,
        )
    except Exception as exc:
        print(
            "Paper Trade enrollment unavailable; production unaffected:",
            f"{type(exc).__name__}: {exc}",
        )
        return 0, 1

    enrolled = 0
    failures = 0
    for ranked in ranked_opportunities:
        opportunity = ranked.opportunity
        snapshot = opportunity.snapshot
        alert = opportunity.alert
        plan = opportunity.plan
        try:
            episode_id = canonical_episode_id(
                scan.snapshots,
                decision_at=decision_at,
                symbol=snapshot.symbol,
            )
            result = enroll_paper_opportunity(
                candidate=alert,
                snapshot=snapshot,
                plan=plan,
                episode_id=episode_id,
                cohort_id=cohort_id,
                decision_at=decision_at,
                config=config,
            )
            print(
                f"PAPER {snapshot.symbol}: Status={result.status} "
                f"Reason={result.reason} "
                f"Id={result.paper_trade_id or 'N/A'}"
            )
            if result.status in {"OPENED", "PENDING"}:
                enrolled += 1
        except Exception as exc:
            failures += 1
            print(
                f"PAPER {snapshot.symbol}: fail-soft "
                f"{type(exc).__name__}: {exc}"
            )
    return enrolled, failures


def _prepare_qualified_lineage(
    ranked_opportunities,
    *,
    scan,
    decision_at,
    settings,
) -> tuple[int, int]:
    """Create immutable signal/journey identity before any qualified Telegram send."""
    try:
        from app.services.freqtrade_signal_bridge import build_signal_id, to_freqtrade_pair
        from app.services.intelligence_journey import link_qualified_signal
        from app.services.paper_trade_control import paper_trade_enabled

        paper_enabled = paper_trade_enabled()
    except Exception as exc:
        print(
            "Qualified signal lineage unavailable; alert delivery remains fail-soft:",
            f"{type(exc).__name__}: {exc}",
        )
        return 0, len(ranked_opportunities)

    prepared = 0
    failures = 0
    for ranked in ranked_opportunities:
        opportunity = ranked.opportunity
        snapshot = opportunity.snapshot
        alert = opportunity.alert
        plan = opportunity.plan
        direction = str(snapshot.trade_direction or "LONG").upper()
        try:
            episode_id = canonical_episode_id(
                scan.snapshots,
                decision_at=decision_at,
                symbol=snapshot.symbol,
            )
            base_asset = str(
                snapshot.underlying_asset
                or alert.get("underlying_asset")
                or snapshot.symbol
            )
            quote_asset = str(
                snapshot.primary_quote_currency
                or alert.get("primary_quote_currency")
                or ("USDT" if str(snapshot.symbol).upper().endswith("USDT") else "USD")
            ).upper()
            pair = to_freqtrade_pair(base_asset, quote_asset)
            signal_id = build_signal_id(
                episode_id=episode_id,
                pair=pair,
                decision_at=decision_at,
                direction=direction,
            )
            journey_id = link_qualified_signal(
                symbol=snapshot.symbol,
                signal_id=signal_id,
                observed_at=decision_at,
                payload={
                    "direction": direction,
                    "profit_rank": ranked.rank,
                    "profit_rank_score": ranked.profit_ranking.total_score,
                    "confidence": int(alert.get("confidence") or 0),
                    "entry_style": plan.entry_style,
                    "valid_now": bool(plan.valid_now),
                    "entry_low": plan.entry_low,
                    "entry_high": plan.entry_high,
                    "chase_limit": plan.chase_limit,
                    "stop_price": plan.stop_price,
                    "target_1": plan.target_1,
                    "target_2": plan.target_2,
                    "technical_score": alert.get("technical_score"),
                    "market_regime": alert.get("market_regime"),
                    "economic_target_2_move_pct": alert.get("economic_target_2_move_pct"),
                    "target_attainability_score": alert.get("target_attainability_score"),
                    "paper_requested": bool(paper_enabled and direction == "LONG"),
                    "paper_engine": (
                        "FREQTRADE_DRY_RUN"
                        if direction == "LONG"
                        else "NO_AUTHORITATIVE_SHORT_ENGINE_V1"
                    ),
                },
            )
            alert["signal_id"] = signal_id
            alert["journey_id"] = journey_id
            alert["_lineage_episode_id"] = episode_id
            alert["_lineage_pair"] = pair
            alert["_lineage_base_asset"] = base_asset
            alert["_lineage_quote_asset"] = quote_asset
            prepared += 1
        except Exception as exc:
            failures += 1
            print(
                f"QUALIFIED LINEAGE {snapshot.symbol}: fail-soft "
                f"{type(exc).__name__}: {exc}"
            )
    return prepared, failures


def _publish_freqtrade_paper_opportunities(
    ranked_opportunities,
    *,
    scan,
    decision_at,
    settings,
) -> tuple[int, int]:
    """Publish post-alert qualified LONG intents to authoritative Freqtrade dry-run."""
    try:
        from app.services.freqtrade_signal_bridge import (
            PaperAdmissionRejected,
            build_signal_id,
            publish_qualified_long,
            to_freqtrade_pair,
        )
        from app.services.intelligence_journey import (
            link_qualified_signal,
            record_paper_admission,
        )
        from app.services.paper_trade_control import paper_trade_enabled

        paper_enabled = paper_trade_enabled()
        cohort_id = canonical_cohort_id(scan.snapshots, decision_at=decision_at)
    except Exception as exc:
        print(
            "Freqtrade paper bridge unavailable; production unaffected:",
            f"{type(exc).__name__}: {exc}",
        )
        return 0, 1

    published = 0
    failures = 0
    for ranked in ranked_opportunities:
        opportunity = ranked.opportunity
        snapshot = opportunity.snapshot
        alert = opportunity.alert
        plan = opportunity.plan
        direction = str(snapshot.trade_direction or "LONG").upper()
        if direction != "LONG":
            print(
                f"FREQTRADE PAPER {snapshot.symbol}: skipped "
                "(v1 authoritative paper engine is spot LONG only)"
            )
            continue
        try:
            episode_id = str(
                alert.get("_lineage_episode_id")
                or canonical_episode_id(
                    scan.snapshots,
                    decision_at=decision_at,
                    symbol=snapshot.symbol,
                )
            )
            base_asset = str(
                alert.get("_lineage_base_asset")
                or snapshot.underlying_asset
                or alert.get("underlying_asset")
                or snapshot.symbol
            )
            quote_asset = str(
                alert.get("_lineage_quote_asset")
                or snapshot.primary_quote_currency
                or alert.get("primary_quote_currency")
                or ("USDT" if str(snapshot.symbol).upper().endswith("USDT") else "USD")
            ).upper()
            pair = str(alert.get("_lineage_pair") or to_freqtrade_pair(base_asset, quote_asset))
            signal_id = str(
                alert.get("signal_id")
                or build_signal_id(
                    episode_id=episode_id,
                    pair=pair,
                    decision_at=decision_at,
                    direction=direction,
                )
            )
            journey_id = str(alert.get("journey_id") or "")
            if not journey_id:
                journey_id = link_qualified_signal(
                    symbol=snapshot.symbol,
                    signal_id=signal_id,
                    observed_at=decision_at,
                    payload={
                        "direction": direction,
                        "profit_rank": ranked.rank,
                        "profit_rank_score": ranked.profit_ranking.total_score,
                        "confidence": int(alert.get("confidence") or 0),
                        "entry_style": plan.entry_style,
                        "valid_now": bool(plan.valid_now),
                        "paper_requested": bool(paper_enabled),
                        "paper_engine": "FREQTRADE_DRY_RUN",
                    },
                )
                alert["signal_id"] = signal_id
                alert["journey_id"] = journey_id
            if not paper_enabled:
                print(
                    f"FREQTRADE PAPER {snapshot.symbol}: PAPER_OFF "
                    f"Signal={signal_id} Journey={journey_id}"
                )
                continue

            signal = publish_qualified_long(
                episode_id=episode_id,
                cohort_id=cohort_id,
                journey_id=journey_id,
                ohm_symbol=snapshot.symbol,
                base_asset=base_asset,
                quote_asset=quote_asset,
                decision_at=decision_at,
                valid_now=bool(plan.valid_now),
                entry_style=plan.entry_style,
                entry_low=plan.entry_low,
                entry_high=plan.entry_high,
                chase_limit=plan.chase_limit,
                stop_price=plan.stop_price,
                target_1=plan.target_1,
                target_2=plan.target_2,
                stake_amount=float(settings.paper_trade_capital_per_trade),
                max_hold_hours=int(settings.paper_trade_max_hold_hours),
                pending_ttl_hours=int(settings.paper_trade_pending_ttl_hours),
                confidence=int(alert.get("confidence") or 0),
                profit_rank=ranked.rank,
                profit_rank_score=float(ranked.profit_ranking.total_score),
                starting_equity=float(settings.paper_trade_starting_equity),
                max_positions=int(settings.paper_trade_max_positions),
            )
            record_paper_admission(
                signal_id=signal_id,
                symbol=snapshot.symbol,
                observed_at=decision_at,
                admitted=True,
                reason="ADMITTED",
                payload={
                    "pair": signal["pair"],
                    "stake_amount": signal["stake_amount"],
                    "profit_rank": ranked.rank,
                },
            )
            published += 1
            print(
                f"FREQTRADE PAPER {snapshot.symbol}: PUBLISHED "
                f"Signal={signal['signal_id']} Pair={signal['pair']} "
                f"Journey={journey_id} Entry={signal['entry_price']}"
            )
        except PaperAdmissionRejected as exc:
            try:
                record_paper_admission(
                    signal_id=signal_id,
                    symbol=snapshot.symbol,
                    observed_at=decision_at,
                    admitted=False,
                    reason=exc.reason,
                    payload={
                        "profit_rank": ranked.rank,
                        "stake_amount": float(settings.paper_trade_capital_per_trade),
                    },
                )
            except Exception:
                pass
            print(
                f"FREQTRADE PAPER {snapshot.symbol}: NOT_ADMITTED "
                f"Reason={exc.reason} Signal={signal_id}"
            )
        except Exception as exc:
            failures += 1
            print(
                f"FREQTRADE PAPER {snapshot.symbol}: fail-soft "
                f"{type(exc).__name__}: {exc}"
            )
    return published, failures


def _target_quality(plan, snapshot):
    if snapshot.trade_direction == "SHORT":
        return evaluate_short_target_attainability(plan, snapshot)
    return evaluate_target_attainability(plan, snapshot)


def _economic_quality(plan, snapshot, account_equity):
    if snapshot.trade_direction == "SHORT":
        return evaluate_economic_quality(
            plan,
            available_capital=account_equity,
            max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
            direction="SHORT",
            leverage=SHORT_VALIDATION_LEVERAGE,
            estimated_margin_cost_pct=SHORT_MARGIN_COST_RESERVE_PCT,
            max_account_risk_at_stop_pct=SHORT_MAX_ACCOUNT_RISK_AT_STOP_PCT,
        )
    return evaluate_economic_quality(
        plan,
        available_capital=account_equity,
        max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
    )


def _telegram_delivery_ready(settings) -> bool:
    return bool(
        getattr(settings, "telegram_enabled", False)
        and getattr(settings, "telegram_bot_token", None)
        and getattr(settings, "telegram_chat_id", None)
    )


def _send_movement_notification(movement, settings) -> tuple[bool, bool]:
    """Return (sent, failed).

    Policy suppression / non-alert mode is not a delivery failure. Exceptions
    from the actual notification path are surfaced and counted.
    """
    if (
        bool(getattr(settings, "opip_actionable_only_alerts", False))
        or str(getattr(settings, "price_movement_mode", "shadow")).lower() != "alert"
        or not _telegram_delivery_ready(settings)
    ):
        return False, False
    try:
        sent = send_price_movement_update(
            movement,
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            cooldown_seconds=int(
                getattr(
                    settings,
                    "price_movement_alert_cooldown_seconds",
                    21_600,
                )
            ),
        )
        return bool(sent), False
    except Exception:
        logger.exception(
            "Price movement Telegram delivery failed symbol=%s stage=%s",
            movement.get("symbol", "UNKNOWN"),
            movement.get("stage", "UNKNOWN"),
        )
        return False, True


def _assess_price_movement(snapshot, settings, market_intelligence=None):
    """Evaluate and persist radar context without affecting candidate gates."""
    if str(getattr(settings, "price_movement_mode", "shadow")).lower() == "off":
        return None
    try:
        previous = get_latest_price_movement(snapshot.symbol)
    except Exception:
        previous = None
    try:
        signal = evaluate_price_movement(
            snapshot,
            market_intelligence=market_intelligence,
            previous=previous,
            watch_score=int(getattr(settings, "price_movement_watch_score", 35)),
            ready_score=int(getattr(settings, "price_movement_ready_score", 70)),
            expiry_hours=int(getattr(settings, "price_movement_expiry_hours", 12)),
        )
    except Exception:
        return None
    if signal is None:
        return None
    payload = signal.as_dict()
    snapshot.price_movement_signal = payload
    try:
        record_price_movement(payload)
    except Exception:
        # Shadow persistence must never block scanning or an approved alert.
        pass
    return payload



def _apply_ranked_action_gates(ranked_opportunities, *, settings, opip=None):
    """Apply scarce-capital/portfolio eligibility in global rank order.

    Approved candidates are added to a projected exposure list so lower-ranked
    signals cannot independently allocate the same capital/position slots.
    A vetoed higher-ranked candidate does not block the next-best candidate.
    """
    try:
        projected_trades = list(get_active_trades())
    except Exception as exc:
        print(
            "ACTION GATE unavailable; no new trade alert authorized:",
            f"{type(exc).__name__}: {exc}",
        )
        reason = f"{type(exc).__name__}: {exc}"
        if opip is not None:
            for ranked in ranked_opportunities:
                opip.record_action_gate(ranked, allowed=None, reason=reason)
        return []

    eligible = []
    for ranked in ranked_opportunities:
        opportunity = ranked.opportunity
        alert = opportunity.alert
        plan = opportunity.plan
        direction = str(opportunity.snapshot.trade_direction or "LONG").upper()
        alert["opportunity_rank"] = ranked.rank
        alert.setdefault("profit_rank", ranked.rank)
        alert["profit_rank_score"] = ranked.profit_ranking.total_score

        try:
            gate = apply_action_gate(
                candidate=alert,
                plan=plan,
                account_capital=float(settings.account_equity),
                active_trades=projected_trades,
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            print(
                f"ACTION GATE ERROR rank={ranked.rank} {direction} {plan.symbol}: "
                f"{reason}"
            )
            if opip is not None:
                opip.record_action_gate(ranked, allowed=None, reason=reason)
            continue
        if opip is not None:
            opip.record_action_gate(
                ranked,
                allowed=bool(gate.allowed),
                reason=str(gate.reason or ""),
            )
        if not gate.allowed:
            print(
                f"ACTION GATE REJECT rank={ranked.rank} {direction} {plan.symbol}: "
                f"{gate.reason}"
            )
            continue

        eligible.append(ranked)
        try:
            remove_entry_watch(plan.symbol, direction)
        except Exception as exc:
            # Accepted trade remains valid; queue cleanup is operational only.
            print(
                f"ENTRY WATCH cleanup failed open {direction} {plan.symbol}: "
                f"{type(exc).__name__}: {exc}"
            )
        projected_trades.append(
            SimpleNamespace(
                symbol=plan.symbol,
                status="active",
                direction=direction,
                capital=float(alert.get("recommended_capital") or 0.0),
                margin_leverage=float(
                    alert.get("margin_leverage")
                    or (2.0 if direction == "SHORT" else 1.0)
                ),
            )
        )
        print(
            f"ACTION GATE PASS rank={ranked.rank} {direction} {plan.symbol}: "
            f"capital={float(alert.get('recommended_capital') or 0.0):.2f}"
        )
    return eligible


def main():
    settings = get_settings()
    scan = scan_market(limit=DEFAULT_UNIQUE_ASSET_LIMIT)
    decision_at = datetime.now(timezone.utc)
    market_regime = evaluate_market_regime(scan.snapshots)
    local_movement_signals = [
        signal
        for snapshot in scan.snapshots
        if (signal := _assess_price_movement(snapshot, settings)) is not None
    ]
    local_movement_counts = Counter(
        str(signal.get("stage") or "UNKNOWN")
        for signal in local_movement_signals
    )
    # Construct the observer before selection so the screening rows and funnel
    # share one scan identity.  No callback is installed while telemetry is
    # dark, preserving the historical selector call exactly.
    opip = build_scan_observer(
        snapshots=scan.snapshots,
        decision_at=decision_at,
        account_equity=getattr(settings, "account_equity", None),
    )
    if opip.telemetry_enabled:
        screening_rows: list[dict] = []
        screening_scan_id = _screening_scan_id(opip)
        screening_callback = _broad_screening_callback(
            rows=screening_rows,
            observed_at=decision_at,
            scan_id=screening_scan_id,
            universe_count=len(scan.snapshots),
        )
        selector_kwargs = {}
        selector_parameters = inspect.signature(select_candidates).parameters
        if "on_evaluated" in selector_parameters:
            selector_kwargs["on_evaluated"] = screening_callback
        if "scan_id" in selector_parameters:
            selector_kwargs["scan_id"] = screening_scan_id
        candidates = select_candidates(scan.snapshots, **selector_kwargs)
        append_screening_evaluations(screening_rows, enabled=True)
    else:
        candidates = select_candidates(scan.snapshots)

    # Wave 8.2 TradingView Intelligence Bridge: augmentation only. This can
    # tag existing native candidates with corroborating evidence. It cannot
    # create or promote a candidate, change direction/score, or bypass the
    # native selector's asset/direction/cap/ranking invariants. A failure here
    # must never abort the scan.
    if getattr(settings, "tradingview_v2_enabled", False):
        try:
            from app.services.tradingview_inbox import (
                merge_native_candidate_evidence,
            )

            attached = merge_native_candidate_evidence(candidates)
            print("===== OHM TRADINGVIEW v2 EVIDENCE =====")
            print("Native candidates tagged with TradingView confirmation:", attached)
        except Exception:
            logger.exception(
                "TradingView v2 evidence merge failed open; continuing with native candidates only"
            )

    # O'Pip qualification funnel: observation only. Every hook below is
    # fail-soft and one-directional - it records what the production path just
    # decided and can never change that decision.
    opip.register_candidates(candidates)
    technical_candidate_count = len(candidates)

    print("OHM AI Opportunity Scan")
    if scan.universe is not None:
        print("===== OHM UNIVERSE =====")
        print("Eligible USD markets:", scan.universe.eligible_usd_markets)
        print("Eligible USDT markets:", scan.universe.eligible_usdt_markets)
        print("Unique underlying assets:", scan.universe.unique_underlying_assets)
        print("Selected liquid assets:", scan.universe.selected_liquid_assets)
        print(
            "USDT/USD conversion:",
            f"{scan.universe.usdt_usd_rate:.6f}"
            if scan.universe.usdt_usd_rate is not None
            else "UNAVAILABLE",
        )
        for warning in scan.universe.warnings:
            print("UNIVERSE WARNING:", warning)
    print("Requested:", scan.requested)
    print("Analyzed:", scan.analyzed)
    print("Skipped:", scan.skipped)
    print("Failed:", scan.failed)
    print("===== OHM PRICE MOVEMENT RADAR =====")
    print("Price movement mode:", getattr(settings, "price_movement_mode", "shadow"))
    print("Price movement WATCH:", local_movement_counts.get("WATCH", 0))
    print("Price movement READY:", local_movement_counts.get("READY", 0))
    print("Price movement CONFIRMED:", local_movement_counts.get("CONFIRMED", 0))
    print("Price movement ACTIVE:", local_movement_counts.get("ACTIVE", 0))
    print("===== OHM MARKET DATA VALIDATION =====")
    print("Validated:", scan.analyzed)
    print("Rejected:", scan.data_quality_rejected)
    print("Warnings:", scan.data_quality_warnings)
    for reason in scan.data_quality_rejections or []:
        print("DATA REJECT:", reason)

    long_count, short_count = _direction_counts(candidates)
    print("Technical shortlist:", len(candidates))
    print("Directional shortlist:", f"LONG={long_count}", f"SHORT={short_count}")
    print("===== OHM MARKET REGIME =====")
    print("Sample:", market_regime.sample_size)
    print("Regime:", market_regime.regime)
    print("BreadthScore:", market_regime.breadth_score if market_regime.breadth_score is not None else "N/A")
    print("AboveEMA20:", market_regime.pct_above_ema20)
    print("AboveEMA50:", market_regime.pct_above_ema50)
    print("AboveEMA200:", market_regime.pct_above_ema200)
    print("Positive24h:", market_regime.pct_positive_momentum_24h)
    print("Positive72h:", market_regime.pct_positive_momentum_72h)
    print("BullishTrend:", market_regime.pct_bullish_trend)

    if not candidates:
        print("No technical candidates.")
        opip.finalize(scan_context=_opip_scan_context(scan, technical_candidate_count))
        _capture_native_scan_cohort(scan, decision_at=decision_at)
        return

    margin_summary = validate_short_margin_eligibility(
        candidates,
        account_leverage_ceiling=getattr(settings, "max_margin_leverage", 3.0),
    )
    print("===== OHM SHORT MARGIN ELIGIBILITY =====")
    print(
        "Margin candidates:",
        f"requested={margin_summary.requested}",
        f"eligible={margin_summary.eligible}",
        f"ineligible={margin_summary.ineligible}",
        f"unavailable={margin_summary.unavailable}",
    )
    for candidate in candidates:
        if candidate.trade_direction == "SHORT":
            print(
                f"MARGIN {candidate.symbol}: Status={candidate.margin_validation_status} "
                f"Venue={candidate.margin_venue_symbol or 'N/A'} "
                f"MaxLeverage={candidate.margin_max_leverage or 'N/A'}x"
            )
            if str(candidate.margin_validation_status or "").upper() != "ELIGIBLE":
                capture_snapshot_decision(
                    candidate,
                    decision="MARGIN_REJECT",
                    market_regime=market_regime.regime,
                    reason=f"margin status {candidate.margin_validation_status}",
                    source="margin_eligibility_gate",
                )
    opip.record_margin(candidates)
    recovered_longs = recover_margin_rejected_longs(candidates)
    candidates = keep_margin_tradeable_candidates(candidates)
    recovered_longs = bound_recovered_longs(
        candidates,
        recovered_longs,
        max_per_direction=MAX_PER_DIRECTION,
    )
    if recovered_longs:
        # The rejected SHORT remains terminal and fail-closed. These LONGs
        # independently cleared the same technical threshold before direction
        # resolution and enter the normal downstream validation path from here.
        opip.register_candidates(recovered_longs)
        opip.record_margin(recovered_longs)
        candidates.extend(recovered_longs)
        for fallback in recovered_longs:
            print(
                f"MARGIN LONG FALLBACK {fallback.symbol}: "
                f"LongScore={fallback.technical_score} "
                "Reason=preferred SHORT was not margin-tradeable"
            )
    if not candidates:
        print("No directionally tradeable candidates after margin eligibility.")
        opip.finalize(scan_context=_opip_scan_context(scan, technical_candidate_count))
        _capture_native_scan_cohort(scan, decision_at=decision_at)
        return

    secondary_summary = confirm_secondary_markets(
        candidates,
        scan.universe.usdt_usd_rate if scan.universe else None,
    )
    print(
        "Secondary confirmations:",
        f"requested={secondary_summary.requested}",
        f"analyzed={secondary_summary.analyzed}",
        f"failed={secondary_summary.failed}",
    )
    for candidate in candidates:
        print(
            f"CROSS PAIR {candidate.underlying_asset or candidate.symbol}: "
            f"Direction={candidate.trade_direction} "
            f"Primary={candidate.primary_pair or candidate.symbol} "
            f"Secondary={candidate.secondary_pair or 'NONE'} "
            f"Combined=${candidate.combined_24h_liquidity_usd:,.2f} "
            f"PrimaryVol={candidate.volume_ratio:.2f}x "
            f"SecondaryVol={candidate.secondary_volume_ratio if candidate.secondary_volume_ratio is not None else 'N/A'} "
            f"Status={candidate.cross_pair_confirmation_status}"
        )

    opip.record_cross_market(candidates)

    execution_requested = len(candidates)
    pre_execution_candidates = list(candidates)
    candidates = deep_validate_candidates(
        candidates,
        settings.account_equity,
        scan.universe.usdt_usd_rate if scan.universe else None,
    )
    survived_execution_keys = {(c.symbol, c.trade_direction) for c in candidates}
    for rejected in pre_execution_candidates:
        if (rejected.symbol, rejected.trade_direction) not in survived_execution_keys:
            capture_snapshot_decision(
                rejected,
                decision="EXECUTION_REJECT",
                market_regime=market_regime.regime,
                reason="filtered by deep execution validation",
                source="execution_quality_gate",
            )

    strict_execution_candidates = []
    for candidate in candidates:
        execution = candidate.execution_validation
        if candidate.trade_direction == "SHORT":
            tradeable, reasons = short_execution_is_tradeable(candidate)
            if not tradeable:
                print(f"SHORT EXECUTION REJECT {candidate.symbol}: {'; '.join(reasons)}")
                capture_snapshot_decision(
                    candidate,
                    decision="EXECUTION_REJECT",
                    market_regime=market_regime.regime,
                    reason="; ".join(reasons),
                    source="short_execution_quality_gate",
                )
                continue
        strict_execution_candidates.append(candidate)
        data_status = candidate.market_data_validation.status if candidate.market_data_validation is not None else "UNAVAILABLE"
        print(
            f"EXECUTION {candidate.symbol}: Direction={candidate.trade_direction} "
            f"Data={data_status} StructuralStatus={execution.status} "
            f"BookCoverage={execution.book_coverage_status} "
            f"Spread={execution.spread_bps if execution.spread_bps is not None else 'N/A'}bps "
            f"BuyCoverage={execution.buy_visible_coverage_pct if execution.buy_visible_coverage_pct is not None else 'N/A'}% "
            f"SellCoverage={execution.sell_visible_coverage_pct if execution.sell_visible_coverage_pct is not None else 'N/A'}% "
            f"RoundTripDrag={execution.estimated_visible_round_trip_market_drag_pct if execution.estimated_visible_round_trip_market_drag_pct is not None else 'N/A'}% "
            f"RecentTradeAge={execution.latest_trade_age_seconds if execution.latest_trade_age_seconds is not None else 'N/A'}s"
        )
    candidates = strict_execution_candidates
    opip.record_execution(pre_execution_candidates)
    print("Execution validation requested:", execution_requested)
    print("Execution structural/short-quality rejects:", execution_requested - len(candidates))
    if not candidates:
        print("No candidates survived execution quality validation.")
        opip.finalize(scan_context=_opip_scan_context(scan, technical_candidate_count))
        _capture_native_scan_cohort(scan, decision_at=decision_at)
        return

    reference_summary = validate_finalist_references(
        candidates,
        scan.universe.usdt_usd_rate if scan.universe else None,
        api_key=getattr(settings, "coingecko_api_key", None),
    )
    print("===== OHM INDEPENDENT REFERENCE VALIDATION =====")
    print("Requested:", reference_summary.requested)
    print("Available:", reference_summary.available)
    print("Unavailable:", reference_summary.unavailable)
    print("Ambiguous:", reference_summary.ambiguous)
    print("API mode:", reference_summary.api_mode)
    for candidate in candidates:
        reference = candidate.independent_market_reference
        print(
            f"REFERENCE {candidate.symbol}: Direction={candidate.trade_direction} "
            f"Status={reference.status} Matches={getattr(reference, 'matched_candidate_count', 0)} "
            f"CoinGecko={reference.coingecko_id or 'N/A'} "
            f"Divergence={reference.price_divergence_pct if reference.price_divergence_pct is not None else 'N/A'}%"
        )

    opip.record_reference(candidates)

    coingecko_global = load_coingecko_global_context(
        api_key=getattr(settings, "coingecko_api_key", None)
    )
    print("CoinGeckoGlobal:", coingecko_global.status)
    print("MarketCap24hChange:", coingecko_global.market_cap_change_24h_pct if coingecko_global.market_cap_change_24h_pct is not None else "N/A")
    print("BTCDominance:", coingecko_global.btc_market_cap_percentage if coingecko_global.btc_market_cap_percentage is not None else "N/A")
    _record_coingecko_health_fail_open(
        settings,
        reference_summary,
        coingecko_global,
    )

    news_summary = validate_finalist_news(
        candidates,
        auth_token=getattr(settings, "cryptopanic_auth_token", None),
        api_plan=getattr(settings, "cryptopanic_api_plan", "developer"),
    )
    catalyst_summary = validate_scheduled_catalysts(
        candidates,
        api_key=getattr(settings, "coinmarketcal_api_key", None),
    )
    print("===== OHM NEWS & CATALYSTS =====")
    print(
        "News summary:",
        f"requested={news_summary.requested}",
        f"available={news_summary.available}",
        f"unavailable={news_summary.unavailable}",
        f"unresolved={getattr(news_summary, 'unresolved', 0)}",
    )
    print(
        "Catalyst summary:",
        f"requested={catalyst_summary.requested}",
        f"available={catalyst_summary.available}",
        f"unresolved={catalyst_summary.unresolved}",
        f"unavailable={catalyst_summary.unavailable}",
    )

    intelligence = enrich_finalist_market_intelligence(candidates, market_regime)
    candidates = list(intelligence.candidates)
    movement_notifications_sent = 0
    movement_notification_failures = 0
    enriched_movement_counts: Counter[str] = Counter()
    for candidate in candidates:
        movement = _assess_price_movement(
            candidate,
            settings,
            getattr(candidate, "_wave8_market_intelligence", None),
        )
        if movement is None:
            continue
        enriched_movement_counts[str(movement.get("stage") or "UNKNOWN")] += 1
        movement_sent, movement_failed = _send_movement_notification(
            movement,
            settings,
        )
        movement_notifications_sent += int(movement_sent)
        movement_notification_failures += int(movement_failed)
    print("===== OHM EXTERNAL MARKET INTELLIGENCE =====")
    print("Evidence records:", len(intelligence.evidence))
    print("Available assessments:", sum(
        assessment.status == "AVAILABLE"
        for assessment in intelligence.assessments.values()
    ))
    print("Finalist cap:", min(len(candidates), 8))
    print(
        "Enriched movement stages:",
        f"WATCH={enriched_movement_counts.get('WATCH', 0)}",
        f"READY={enriched_movement_counts.get('READY', 0)}",
        f"CONFIRMED={enriched_movement_counts.get('CONFIRMED', 0)}",
        f"ACTIVE={enriched_movement_counts.get('ACTIVE', 0)}",
    )
    for candidate in candidates[:8]:
        assessment = intelligence.assessments.get(candidate.symbol)
        if assessment is None:
            continue
        print(
            f"INTELLIGENCE {candidate.symbol}: Direction={candidate.trade_direction} "
            f"Status={assessment.status} ContextScore={assessment.context_score} "
            f"Derivatives={assessment.derivatives_bias} "
            f"Volatility={assessment.volatility_regime} "
            f"Macro={assessment.macro_regime} "
            f"Flow={assessment.flow_bias} "
            f"Sentiment={assessment.sentiment_bias}"
        )

    opip.record_market_intelligence(candidates, intelligence.assessments)

    review = review_candidates(
        candidates,
        settings.openai_model,
        settings.openai_api_key,
        settings.account_equity,
        market_regime_context=intelligence.chief_market_regime_context,
        coingecko_global_context=coingecko_global,
    )
    opip.record_ai_stage(review)
    alerts = qualified_alerts(review)
    print("AI top candidates:", len(review.get("top_candidates", [])))
    print("Qualified alerts before deterministic quality gates:", len(alerts))

    snapshot_by_key = {
        (snapshot.symbol, snapshot.trade_direction): snapshot
        for snapshot in candidates
    }

    sent = 0
    pending_saved = 0
    economic_rejected = 0
    economic_passed = 0
    target_rejected = 0
    target_passed = 0
    qualified_opportunities: list[QualifiedOpportunity] = []

    for alert in alerts:
        direction = str(alert.get("direction", "LONG")).upper()
        snapshot = snapshot_by_key.get((alert["symbol"], direction))
        if snapshot is None:
            print("Snapshot missing for:", alert["symbol"], direction)
            opip.record_snapshot_missing(alert["symbol"], direction)
            continue

        plan = (
            build_entry_exit_plan(snapshot, alert["risk_level"], direction="SHORT")
            if direction == "SHORT"
            else build_entry_exit_plan(snapshot, alert["risk_level"])
        )
        alert["direction"] = direction
        alert["technical_score"] = snapshot.technical_score
        alert["underlying_asset"] = snapshot.underlying_asset or snapshot.symbol
        alert["primary_pair"] = snapshot.primary_pair or snapshot.symbol
        alert["secondary_pair"] = snapshot.secondary_pair
        alert["primary_quote_currency"] = snapshot.primary_quote_currency
        alert["market_regime"] = market_regime.regime
        alert["market_intelligence"] = getattr(
            snapshot,
            "_wave8_market_intelligence",
            None,
        )
        alert["price_movement"] = snapshot.price_movement_signal
        if direction == "SHORT":
            alert["margin_leverage"] = SHORT_VALIDATION_LEVERAGE
            alert["margin_venue_symbol"] = snapshot.margin_venue_symbol

        if bool(getattr(settings, "opip_trade_quality_v2_enabled", False)):
            episode_id = canonical_episode_id(
                scan.snapshots,
                decision_at=decision_at,
                symbol=snapshot.symbol,
            )
            base_asset = str(
                snapshot.underlying_asset
                or alert.get("underlying_asset")
                or snapshot.symbol
            )
            quote_asset = str(
                snapshot.primary_quote_currency
                or alert.get("primary_quote_currency")
                or (
                    "USDT"
                    if str(snapshot.symbol).upper().endswith("USDT")
                    else "USD"
                )
            ).upper()
            pair = to_freqtrade_pair(base_asset, quote_asset)
            candidate_id = build_signal_id(
                episode_id=episode_id,
                pair=pair,
                decision_at=decision_at,
                direction=direction,
            )
            alert["signal_id"] = candidate_id
            alert["_lineage_episode_id"] = episode_id
            alert["_lineage_pair"] = pair
            alert["_lineage_base_asset"] = base_asset
            alert["_lineage_quote_asset"] = quote_asset
            try:
                feature_snapshot = build_trade_feature_snapshot(
                    snapshot,
                    decision_at=decision_at,
                    episode_id=episode_id,
                    candidate_id=candidate_id,
                    regime=market_regime.regime,
                )
                trade_quality = assess_trade_quality(
                    feature_snapshot,
                    plan,
                    min_liquidity_usd=float(settings.signal_quality_min_liquidity_usd),
                )
            except Exception as exc:
                print(
                    f"TRADE QUALITY candidate rejected after evaluator failure "
                    f"{direction} {snapshot.symbol}: {type(exc).__name__}: {exc}"
                )
                capture_snapshot_decision(
                    snapshot,
                    decision="TRADE_QUALITY_UNAVAILABLE",
                    market_regime=market_regime.regime,
                    reason=f"{type(exc).__name__}: {exc}",
                    source="wave9_trade_quality_gate",
                )
                continue
            alert["feature_snapshot_id"] = feature_snapshot.snapshot_id
            alert["continuation_score"] = trade_quality.continuation.score
            alert["continuation_decision"] = trade_quality.continuation.decision
            alert["continuation_evidence_quality"] = (
                trade_quality.continuation.evidence_quality
            )
            alert["entry_quality_score"] = trade_quality.entry.quality_score
            alert["entry_quality_decision"] = trade_quality.entry.decision
            alert["exhaustion_state"] = trade_quality.entry.exhaustion_risk
            alert["trade_quality_actionable"] = trade_quality.actionable
            alert["score_is_probability"] = False
            try:
                alert["trade_quality_evidence_id"] = record_trade_quality_evidence(
                    feature_snapshot=feature_snapshot,
                    assessment=trade_quality,
                    plan=plan,
                    candidate=alert,
                    decision_at=decision_at,
                    market_regime=market_regime.regime,
                )
            except Exception as exc:
                print(
                    f"TRADE QUALITY evidence capture failed open "
                    f"{direction} {snapshot.symbol}: {type(exc).__name__}: {exc}"
                )

            if (
                trade_quality.continuation.decision == "PASS"
                and trade_quality.entry.decision == "WAIT"
            ):
                try:
                    enqueue_entry_watch(
                        symbol=snapshot.symbol,
                        direction=direction,
                        candidate_id=candidate_id,
                        continuation_score=trade_quality.continuation.score,
                        now=decision_at,
                    )
                except Exception as exc:
                    print(
                        f"ENTRY WATCH enqueue failed open {direction} {snapshot.symbol}: "
                        f"{type(exc).__name__}: {exc}"
                    )

            if not trade_quality.actionable:
                quality_reason = (
                    f"Continuation={trade_quality.continuation.decision}/"
                    f"{trade_quality.continuation.score} "
                    f"Entry={trade_quality.entry.decision}/"
                    f"{trade_quality.entry.quality_score} "
                    f"Exhaustion={trade_quality.entry.exhaustion_risk}"
                )
                print(
                    f"TRADE QUALITY MONITOR {direction} {snapshot.symbol}: "
                    f"{quality_reason}"
                )
                capture_snapshot_decision(
                    snapshot,
                    decision="TRADE_QUALITY_MONITOR",
                    market_regime=market_regime.regime,
                    reason=quality_reason,
                    source="wave9_trade_quality_gate",
                )
                continue

            print(
                f"TRADE QUALITY PASS {direction} {snapshot.symbol}: "
                f"Continuation={trade_quality.continuation.score}/100 "
                f"Entry={trade_quality.entry.quality_score}/100 "
                f"Snapshot={feature_snapshot.snapshot_id}"
            )

        target_quality = _target_quality(plan, snapshot)
        opip.record_target_quality(snapshot, target_quality)
        if not target_quality.qualified:
            target_rejected += 1
            rejection_reason = "; ".join(target_quality.rejection_reasons)
            print(
                f"TARGET QUALITY REJECT {direction} {plan.symbol}: "
                f"Score={target_quality.attainability_score} "
                f"Reason={rejection_reason}"
            )
            capture_snapshot_decision(
                snapshot,
                decision="TARGET_REJECT",
                market_regime=market_regime.regime,
                reason=rejection_reason,
                source="target_quality_gate",
            )
            continue
        target_passed += 1
        clearance_label = "support" if direction == "SHORT" else "resistance"
        print(
            f"TARGET QUALITY PASS {direction} {plan.symbol}: "
            f"Score={target_quality.attainability_score} "
            f"T2={target_quality.target_2_atr_multiple:.2f} ATR "
            f"24h {clearance_label} clearance={target_quality.clearance_to_24h_resistance_pct:.2f}% "
            f"72h {clearance_label} clearance={target_quality.clearance_to_72h_resistance_pct:.2f}% "
            f"T2 move={target_quality.target_2_move_pct:.2f}%"
        )

        economic = _economic_quality(plan, snapshot, settings.account_equity)
        opip.record_economic_quality(snapshot, economic)
        if not economic.qualified:
            economic_rejected += 1
            print(f"ECONOMIC REJECT {direction} {plan.symbol}: {economic.rejection_reason}")
            capture_snapshot_decision(
                snapshot,
                decision="ECONOMIC_REJECT",
                market_regime=market_regime.regime,
                reason=str(economic.rejection_reason or "economic gate rejected"),
                source="economic_quality_gate",
            )
            continue
        economic_passed += 1
        print(
            f"ECONOMIC PASS {direction} {plan.symbol}: "
            f"T1={economic.target_1_move_pct:.2f}% T2={economic.target_2_move_pct:.2f}% "
            f"Net@T2=${economic.target_2_net_profit:.2f} "
            f"R/R={plan.reward_to_risk_2:.2f}:1 "
            f"Leverage={getattr(economic, 'leverage', 1.0):.1f}x "
            f"AccountRiskAtStop={getattr(economic, 'account_risk_at_stop_pct', 0.0):.2f}%"
        )

        alert["target_attainability_score"] = target_quality.attainability_score
        alert["target_quality_qualified"] = True
        alert["economic_qualified"] = True
        alert["economic_target_2_move_pct"] = economic.target_2_move_pct
        alert["economic_validation_net_t2"] = economic.target_2_net_profit
        alert["economic_validation_capital"] = economic.recommended_capital
        alert["economic_position_notional"] = getattr(
            economic,
            "position_notional",
            economic.recommended_capital,
        )
        qualified_opportunities.append(
            QualifiedOpportunity(
                alert=alert,
                snapshot=snapshot,
                plan=plan,
                target_quality=target_quality,
                economic_quality=economic,
            )
        )

    ranked_opportunities = rank_profit_opportunities(qualified_opportunities)
    print("===== OHM PROFIT RANKING =====")
    print("Qualified survivors:", len(ranked_opportunities))
    for ranked in ranked_opportunities:
        result = ranked.profit_ranking
        opportunity = ranked.opportunity
        economic = opportunity.economic_quality
        drag = result.measured_execution_drag_pct
        print(
            f"RANK {ranked.rank} {opportunity.snapshot.trade_direction} {result.symbol}: "
            f"Score={result.total_score:.2f} Economic={result.economic_opportunity_score:.2f} "
            f"Target={result.target_quality_score:.2f} Execution={result.execution_quality_score:.2f} "
            f"Technical={result.technical_quality_score:.2f} Evidence={result.evidence_quality_score:.2f} "
            f"T2Move={economic.target_2_move_pct:.2f}% "
            f"ValidationNetT2=${economic.target_2_net_profit:.2f} "
            f"ExecutionDrag={f'{drag:.2f}%' if drag is not None else 'N/A'}"
        )

    if bool(getattr(settings, "opip_global_capital_ranking_enabled", False)):
        capital_ranked = rank_capital_efficiency(ranked_opportunities)
        print("===== O\'PIP GLOBAL CAPITAL EFFICIENCY =====")
        global_ranked_opportunities: list[RankedOpportunity] = []
        global_rank = 0
        for item in capital_ranked:
            original = item.ranked_opportunity
            alert = original.opportunity.alert
            result = item.capital_efficiency
            alert["profit_rank"] = item.original_rank
            alert["capital_efficiency_score"] = result.total_score
            alert["hold_proxy_hours"] = result.hold_proxy_hours
            alert["net_return_velocity_proxy_pct_per_hour"] = (
                result.net_return_velocity_pct_per_hour
            )
            alert["risk_efficiency_ratio"] = result.risk_efficiency_ratio
            alert["capital_deployability_score"] = (
                result.capital_deployability_score
            )
            alert["liquidity_capacity_status"] = result.capacity_status
            alert["liquidity_capacity_ceiling_usd"] = (
                result.liquidity_capacity_ceiling_usd
            )
            alert["liquidity_capacity_utilization_pct"] = (
                result.capacity_utilization_pct
            )
            alert["liquidity_capacity_scalable_fraction"] = (
                result.capacity_scalable_fraction
            )

            if not result.capacity_eligible:
                ceiling_label = (
                    f"${result.liquidity_capacity_ceiling_usd:.2f}"
                    if result.liquidity_capacity_ceiling_usd is not None
                    else "UNKNOWN"
                )
                reason = (
                    f"liquidity capacity {result.capacity_status}; "
                    f"required_notional=${result.required_notional_usd:.2f}; "
                    f"ceiling={ceiling_label}"
                )
                print(
                    f"CAPACITY REJECT "
                    f"{original.opportunity.snapshot.trade_direction} "
                    f"{result.symbol}: {reason}"
                )
                capture_snapshot_decision(
                    original.opportunity.snapshot,
                    decision="CAPACITY_REJECT",
                    market_regime=market_regime.regime,
                    reason=reason,
                    source="wave9_liquidity_capacity_gate",
                    profit_rank_score=original.profit_ranking.total_score,
                )
                continue

            global_rank += 1
            alert["opportunity_rank"] = global_rank
            ceiling_label = (
                f"${result.liquidity_capacity_ceiling_usd:.2f}"
                if result.liquidity_capacity_ceiling_usd is not None
                else "N/A"
            )
            print(
                f"GLOBAL RANK {global_rank} "
                f"{original.opportunity.snapshot.trade_direction} "
                f"{result.symbol}: "
                f"CapitalEfficiency={result.total_score:.2f} "
                f"ProfitRank={item.original_rank} "
                f"Deployability={result.capital_deployability_score:.2f}/15 "
                f"Capacity={result.capacity_status} "
                f"CapacityCeiling={ceiling_label} "
                f"HoldProxy={result.hold_proxy_hours:.2f}h "
                f"NetReturnVelocity={result.net_return_velocity_pct_per_hour:.4f}%/h "
                f"RiskEfficiency={result.risk_efficiency_ratio:.4f}"
            )
            global_ranked_opportunities.append(
                RankedOpportunity(
                    rank=global_rank,
                    opportunity=original.opportunity,
                    profit_ranking=original.profit_ranking,
                )
            )
        ranked_opportunities = global_ranked_opportunities

    else:
        # Compatibility path for legacy test/extension settings only. Ranking
        # may be disabled, but the action gate remains mandatory authority.
        for ranked in ranked_opportunities:
            ranked.opportunity.alert["profit_rank"] = ranked.rank
            ranked.opportunity.alert["profit_rank_score"] = (
                ranked.profit_ranking.total_score
            )

    # A few extension tests replace this helper with the older two-argument
    # seam.  Pass the observer only when the active implementation supports it.
    action_gate_kwargs = {"settings": settings}
    if "opip" in inspect.signature(_apply_ranked_action_gates).parameters:
        action_gate_kwargs["opip"] = opip
    actionable_ranked_opportunities = _apply_ranked_action_gates(
        ranked_opportunities,
        **action_gate_kwargs,
    )
    print(
        "Actionable survivors after capital/portfolio gate:",
        len(actionable_ranked_opportunities),
    )
    ranked_opportunities = actionable_ranked_opportunities

    lineage_prepared, lineage_failures = _prepare_qualified_lineage(
        ranked_opportunities,
        scan=scan,
        decision_at=decision_at,
        settings=settings,
    )
    print("Qualified signal lineages prepared before Telegram:", lineage_prepared)
    print("Qualified signal lineage failures:", lineage_failures)
    opip.record_qualified(ranked_opportunities)

    for ranked in ranked_opportunities:
        opportunity = ranked.opportunity
        alert = opportunity.alert
        plan = opportunity.plan
        direction = opportunity.snapshot.trade_direction
        alert["opportunity_rank"] = ranked.rank
        alert["profit_rank_score"] = ranked.profit_ranking.total_score

        capture_snapshot_decision(
            opportunity.snapshot,
            decision="ENTER_NOW" if plan.valid_now else "WAIT",
            market_regime=market_regime.regime,
            reason=plan.reason,
            source="qualified_profit_rank",
            profit_rank_score=ranked.profit_ranking.total_score,
        )

        if send_trade_plan(
            candidate=alert,
            plan=plan,
            summary=review.get("summary", ""),
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        ):
            sent += 1
            if not plan.valid_now:
                pending_saved += 1

    print("")
    print("===== OHM HIGH-CONVICTION SUMMARY =====")
    print("Economic passes:", economic_passed)
    print("Economic rejects:", economic_rejected)
    print("Target quality passes:", target_passed)
    print("Target quality rejects:", target_rejected)
    print("Pending setups saved:", pending_saved)
    print("Telegram delivery configured:", _telegram_delivery_ready(settings))
    print("Price movement mode:", getattr(settings, "price_movement_mode", "shadow"))
    print("Telegram notifications sent:", sent)
    print("Price movement notifications sent:", movement_notifications_sent)
    print("Price movement notification failures:", movement_notification_failures)
    freqtrade_published, freqtrade_failures = _publish_freqtrade_paper_opportunities(
        ranked_opportunities,
        scan=scan,
        decision_at=decision_at,
        settings=settings,
    )
    shadow_enrolled, shadow_failures = _maybe_enroll_paper_opportunities(
        ranked_opportunities,
        scan=scan,
        decision_at=decision_at,
        settings=settings,
    )
    print("Authoritative Freqtrade paper signals published:", freqtrade_published)
    print("Authoritative Freqtrade bridge failures:", freqtrade_failures)
    print("Shadow simulator lifecycles enrolled:", shadow_enrolled)
    print("Shadow simulator failures:", shadow_failures)
    paper_admission_eligible = opip.record_paper_admission_eligibility(
        ranked_opportunities,
        paper_enabled=_paper_trade_enabled_safe(),
    )
    opip.finalize(
        scan_context=_opip_scan_context(scan, technical_candidate_count),
        paper_admission_eligible=paper_admission_eligible,
    )
    _capture_native_scan_cohort(scan, decision_at=decision_at)


if __name__ == "__main__":
    main()
