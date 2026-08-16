import logging

from app.core.config import get_settings
from app.scanner.directional_candidates import select_directional_candidates
from app.scanner.global_market_context import load_coingecko_global_context
from app.scanner.margin_eligibility import (
    keep_margin_tradeable_candidates,
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
from app.services.chief_alert_notifier import send_trade_plan
from app.services.chief_analyst import (
    SHORT_MARGIN_COST_RESERVE_PCT,
    SHORT_MAX_ACCOUNT_RISK_AT_STOP_PCT,
    SHORT_VALIDATION_LEVERAGE,
    review_candidates,
)
from app.services.economic_quality_gate import evaluate_economic_quality
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.market_intelligence_integration import enrich_finalist_market_intelligence
from app.services.pending_setup_registry import PendingSetup, add_pending_setup
from app.services.profit_ranking import QualifiedOpportunity, rank_profit_opportunities
from app.services.recommendation_gate import qualified_alerts
from app.services.shadow_decision_capture import capture_snapshot_decision
from app.services.short_target_attainability import evaluate_short_target_attainability
from app.services.target_attainability import evaluate_target_attainability


logger = logging.getLogger(__name__)


# Backwards-compatible test/extension seam. Production default is the new mixed
# directional selector; existing callers that patch select_candidates still work.
select_candidates = select_directional_candidates


def _direction_counts(candidates):
    return (
        sum(c.trade_direction == "LONG" for c in candidates),
        sum(c.trade_direction == "SHORT" for c in candidates),
    )


def _target_quality(plan, snapshot):
    if snapshot.trade_direction == "SHORT":
        return evaluate_short_target_attainability(plan, snapshot)
    return evaluate_target_attainability(plan, snapshot)


def _economic_quality(plan, snapshot, account_equity):
    if snapshot.trade_direction == "SHORT":
        return evaluate_economic_quality(
            plan,
            available_capital=account_equity,
            direction="SHORT",
            leverage=SHORT_VALIDATION_LEVERAGE,
            estimated_margin_cost_pct=SHORT_MARGIN_COST_RESERVE_PCT,
            max_account_risk_at_stop_pct=SHORT_MAX_ACCOUNT_RISK_AT_STOP_PCT,
        )
    return evaluate_economic_quality(plan, available_capital=account_equity)


def main():
    settings = get_settings()
    scan = scan_market(limit=DEFAULT_UNIQUE_ASSET_LIMIT)
    market_regime = evaluate_market_regime(scan.snapshots)
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
    candidates = keep_margin_tradeable_candidates(candidates)
    if not candidates:
        print("No directionally tradeable candidates after margin eligibility.")
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
    print("Execution validation requested:", execution_requested)
    print("Execution structural/short-quality rejects:", execution_requested - len(candidates))
    if not candidates:
        print("No candidates survived execution quality validation.")
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

    coingecko_global = load_coingecko_global_context(
        api_key=getattr(settings, "coingecko_api_key", None)
    )
    print("CoinGeckoGlobal:", coingecko_global.status)
    print("MarketCap24hChange:", coingecko_global.market_cap_change_24h_pct if coingecko_global.market_cap_change_24h_pct is not None else "N/A")
    print("BTCDominance:", coingecko_global.btc_market_cap_percentage if coingecko_global.btc_market_cap_percentage is not None else "N/A")

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
    print("===== OHM EXTERNAL MARKET INTELLIGENCE =====")
    print("Evidence records:", len(intelligence.evidence))
    print("Available assessments:", sum(
        assessment.status == "AVAILABLE"
        for assessment in intelligence.assessments.values()
    ))
    print("Finalist cap:", min(len(candidates), 8))
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

    review = review_candidates(
        candidates,
        settings.openai_model,
        settings.openai_api_key,
        settings.account_equity,
        market_regime_context=intelligence.chief_market_regime_context,
        coingecko_global_context=coingecko_global,
    )
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
        if direction == "SHORT":
            alert["margin_leverage"] = SHORT_VALIDATION_LEVERAGE
            alert["margin_venue_symbol"] = snapshot.margin_venue_symbol

        target_quality = _target_quality(plan, snapshot)
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

    for ranked in ranked_opportunities:
        opportunity = ranked.opportunity
        alert = opportunity.alert
        plan = opportunity.plan
        direction = opportunity.snapshot.trade_direction
        alert["profit_rank"] = ranked.rank
        alert["profit_rank_score"] = ranked.profit_ranking.total_score

        capture_snapshot_decision(
            opportunity.snapshot,
            decision="ENTER_NOW" if plan.valid_now else "WAIT",
            market_regime=market_regime.regime,
            reason=plan.reason,
            source="qualified_profit_rank",
            profit_rank_score=ranked.profit_ranking.total_score,
        )

        if not plan.valid_now:
            setup = PendingSetup(
                symbol=plan.symbol,
                entry_low=plan.entry_low,
                entry_high=plan.entry_high,
                chase_limit=plan.chase_limit,
                stop_price=plan.stop_price,
                target_1=plan.target_1,
                target_2=plan.target_2,
                risk_level=plan.risk_level,
                confidence=int(alert.get("confidence", 0)),
                direction=direction,
                margin_leverage=(SHORT_VALIDATION_LEVERAGE if direction == "SHORT" else 1.0),
            )
            add_pending_setup(setup)
            alert["trade_id"] = setup.trade_id
            pending_saved += 1

        if send_trade_plan(
            candidate=alert,
            plan=plan,
            summary=review.get("summary", ""),
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        ):
            sent += 1

    print("")
    print("===== OHM HIGH-CONVICTION SUMMARY =====")
    print("Economic passes:", economic_passed)
    print("Economic rejects:", economic_rejected)
    print("Target quality passes:", target_passed)
    print("Target quality rejects:", target_rejected)
    print("Pending setups saved:", pending_saved)
    print("Telegram notifications sent:", sent)


if __name__ == "__main__":
    main()
