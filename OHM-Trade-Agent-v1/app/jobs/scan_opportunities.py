from app.core.config import get_settings
from app.scanner.candidates import select_candidates
from app.scanner.global_market_context import load_coingecko_global_context
from app.scanner.market_regime import evaluate_market_regime
from app.scanner.market_scanner import (
    confirm_secondary_markets,
    deep_validate_candidates,
    scan_market,
)
from app.scanner.universe import DEFAULT_UNIQUE_ASSET_LIMIT
from app.scanner.reference_market_validation import validate_finalist_references
from app.scanner.news_context import validate_finalist_news
from app.scanner.scheduled_catalysts import validate_scheduled_catalysts
from app.services.chief_alert_notifier import send_trade_plan
from app.services.chief_analyst import review_candidates
from app.services.economic_quality_gate import evaluate_economic_quality
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.pending_setup_registry import PendingSetup, add_pending_setup
from app.services.recommendation_gate import qualified_alerts
from app.services.target_attainability import evaluate_target_attainability


def main():
    settings = get_settings()

    # ---------------------------------------------------------
    # STEP 1: Scan the market.
    # OHM scans the configured unique-asset universe internally.
    # This does NOT increase the eight-candidate Chief shortlist.
    # ---------------------------------------------------------
    scan = scan_market(limit=DEFAULT_UNIQUE_ASSET_LIMIT)

    # Deterministic breadth uses the complete validated Kraken scan. It does
    # not make another Kraken request and is informational in v1.
    market_regime = evaluate_market_regime(scan.snapshots)

    # ---------------------------------------------------------
    # STEP 2: Local technical screening.
    # Only technically interesting assets continue.
    # ---------------------------------------------------------
    candidates = select_candidates(scan.snapshots)

    print("OHM AI Opportunity Scan")
    if scan.universe is not None:
        print("===== OHM UNIVERSE =====")
        print("Eligible USD markets:", scan.universe.eligible_usd_markets)
        print("Eligible USDT markets:", scan.universe.eligible_usdt_markets)
        print("Unique underlying assets:", scan.universe.unique_underlying_assets)
        print("Selected liquid assets:", scan.universe.selected_liquid_assets)
        print(
            "USDT/USD conversion:",
            (
                f"{scan.universe.usdt_usd_rate:.6f}"
                if scan.universe.usdt_usd_rate is not None
                else "UNAVAILABLE"
            ),
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
    print("Technical shortlist:", len(candidates))
    print("===== OHM MARKET REGIME =====")
    print("Sample:", market_regime.sample_size)
    print("Regime:", market_regime.regime)
    print(
        "BreadthScore:",
        market_regime.breadth_score
        if market_regime.breadth_score is not None
        else "N/A",
    )
    print("AboveEMA20:", market_regime.pct_above_ema20)
    print("AboveEMA50:", market_regime.pct_above_ema50)
    print("AboveEMA200:", market_regime.pct_above_ema200)
    print("Positive24h:", market_regime.pct_positive_momentum_24h)
    print("Positive72h:", market_regime.pct_positive_momentum_72h)
    print("BullishTrend:", market_regime.pct_bullish_trend)

    if not candidates:
        print("No technical candidates.")
        return

    # Secondary OHLC is fetched only for the maximum-eight shortlist.
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
            f"Primary={candidate.primary_pair or candidate.symbol} "
            f"Secondary={candidate.secondary_pair or 'NONE'} "
            f"Combined=${candidate.combined_24h_liquidity_usd:,.2f} "
            f"PrimaryVol={candidate.volume_ratio:.2f}x "
            f"SecondaryVol="
            f"{candidate.secondary_volume_ratio if candidate.secondary_volume_ratio is not None else 'N/A'} "
            f"Status={candidate.cross_pair_confirmation_status}"
        )

    # Deep public execution validation is bounded by the existing maximum-eight
    # shortlist and completes before the single Chief request.
    execution_requested = len(candidates)
    candidates = deep_validate_candidates(
        candidates,
        settings.account_equity,
        scan.universe.usdt_usd_rate if scan.universe else None,
    )
    print("Execution validation requested:", execution_requested)
    print("Execution structural rejects:", execution_requested - len(candidates))
    for candidate in candidates:
        execution = candidate.execution_validation
        data_status = (
            candidate.market_data_validation.status
            if candidate.market_data_validation is not None
            else "UNAVAILABLE"
        )
        print(
            f"EXECUTION {candidate.symbol}: "
            f"Data={data_status} "
            f"StructuralStatus={execution.status} "
            f"BookCoverage={execution.book_coverage_status} "
            f"Spread={execution.spread_bps if execution.spread_bps is not None else 'N/A'}bps "
            f"VisibleAsk=${execution.visible_ask_notional if execution.visible_ask_notional is not None else 0:,.2f} "
            f"VisibleBid=${execution.visible_bid_notional if execution.visible_bid_notional is not None else 0:,.2f} "
            f"AskDepth0.5Complete={execution.ask_depth_050_complete} "
            f"BidDepth0.5Complete={execution.bid_depth_050_complete} "
            f"ValidationNotional=${execution.validation_notional_usd:,.2f} "
            f"BuyCoverage={execution.buy_visible_coverage_pct if execution.buy_visible_coverage_pct is not None else 'N/A'}% "
            f"RoundTripDrag={execution.estimated_visible_round_trip_market_drag_pct if execution.estimated_visible_round_trip_market_drag_pct is not None else 'N/A'}% "
            f"RecentTradeAge={execution.latest_trade_age_seconds if execution.latest_trade_age_seconds is not None else 'N/A'}s"
        )

    if not candidates:
        print("No candidates survived structural execution-data validation.")
        return

    # Independent aggregated reference evidence is one fail-open batch for the
    # maximum-eight finalists. Kraken remains the execution venue.
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
            f"REFERENCE {candidate.symbol}: "
            f"Status={reference.status} "
            f"Matches={getattr(reference, 'matched_candidate_count', 0)} "
            f"CoinGecko={reference.coingecko_id or 'N/A'} "
            f"CGPrice=${reference.reference_price_usd if reference.reference_price_usd is not None else 'N/A'} "
            f"KrakenUSD=${reference.kraken_normalized_price_usd if reference.kraken_normalized_price_usd is not None else 'N/A'} "
            f"Divergence={reference.price_divergence_pct if reference.price_divergence_pct is not None else 'N/A'}% "
            f"Age={reference.age_seconds if reference.age_seconds is not None else 'N/A'}s "
            f"Rank={reference.market_cap_rank if reference.market_cap_rank is not None else 'N/A'}"
        )

    # One optional CoinGecko global request supplements deterministic OHM
    # breadth. It neither duplicates nor replaces the finalist markets batch.
    coingecko_global = load_coingecko_global_context(
        api_key=getattr(settings, "coingecko_api_key", None)
    )
    print("CoinGeckoGlobal:", coingecko_global.status)
    print(
        "MarketCap24hChange:",
        coingecko_global.market_cap_change_24h_pct
        if coingecko_global.market_cap_change_24h_pct is not None
        else "N/A",
    )
    print(
        "BTCDominance:",
        coingecko_global.btc_market_cap_percentage
        if coingecko_global.btc_market_cap_percentage is not None
        else "N/A",
    )

    # External finalist evidence remains fail-open and bounded to one provider
    # batch each. It is context for the existing single Chief comparison.
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
    for candidate in candidates:
        news = candidate.news_context
        catalyst = candidate.scheduled_catalyst_context
        print(
            f"NEWS {candidate.symbol}: "
            f"Status={news.status} "
            f"Posts24h={news.recent_post_count_24h} "
            f"Posts6h={news.recent_post_count_6h} "
            f"LatestAge="
            f"{news.latest_post_age_seconds if news.latest_post_age_seconds is not None else 'N/A'}s"
        )
        nearest = catalyst.nearest_event
        nearest_display = (
            nearest.displayed_date
            if nearest is not None and nearest.is_estimated
            else nearest.date if nearest is not None else None
        )
        print(
            f"CATALYST {candidate.symbol}: "
            f"Status={catalyst.status} "
            f"Mapping={catalyst.mapping_status} "
            f"Slug={catalyst.coinmarketcal_slug or 'N/A'} "
            f"Events7d={catalyst.event_count_next_7d} "
            f"Nearest={nearest_display or 'N/A'}"
        )

    # ---------------------------------------------------------
    # STEP 3: AI Chief review.
    # The Chief compares only the technical shortlist.
    # ---------------------------------------------------------
    review = review_candidates(
        candidates,
        settings.openai_model,
        settings.openai_api_key,
        settings.account_equity,
        market_regime_context=market_regime,
        coingecko_global_context=coingecko_global,
    )

    # ---------------------------------------------------------
    # STEP 4: Existing recommendation gate.
    # Requires ALERT decision, confidence threshold,
    # and acceptable risk level.
    # ---------------------------------------------------------
    alerts = qualified_alerts(review)

    print(
        "AI top candidates:",
        len(review.get("top_candidates", [])),
    )
    print(
        "Qualified alerts before deterministic quality gates:",
        len(alerts),
    )

    snapshot_by_symbol = {
        snapshot.symbol: snapshot
        for snapshot in scan.snapshots
    }

    sent = 0
    pending_saved = 0
    economic_rejected = 0
    economic_passed = 0
    target_rejected = 0
    target_passed = 0

    # ---------------------------------------------------------
    # STEP 5: Build entry/exit plan for every candidate that
    # survived the AI/recommendation gates.
    # ---------------------------------------------------------
    for alert in alerts:
        snapshot = snapshot_by_symbol.get(
            alert["symbol"]
        )

        if snapshot is None:
            print(
                "Snapshot missing for:",
                alert["symbol"],
            )
            continue

        plan = build_entry_exit_plan(
            snapshot,
            alert["risk_level"],
        )
        alert["underlying_asset"] = snapshot.underlying_asset or snapshot.symbol
        alert["primary_pair"] = snapshot.primary_pair or snapshot.symbol
        alert["secondary_pair"] = snapshot.secondary_pair
        alert["primary_quote_currency"] = snapshot.primary_quote_currency

        # Deterministic target realism gate. A failure remains internal and is
        # stopped before pending persistence or any Telegram action.
        target_quality = evaluate_target_attainability(plan, snapshot)

        if not target_quality.qualified:
            target_rejected += 1
            print(
                f"TARGET QUALITY REJECT {plan.symbol}: "
                f"Score={target_quality.attainability_score} "
                f"Reason={'; '.join(target_quality.rejection_reasons)}"
            )
            continue

        target_passed += 1
        print(
            f"TARGET QUALITY PASS {plan.symbol}: "
            f"Score={target_quality.attainability_score} "
            f"T2={target_quality.target_2_atr_multiple:.2f} ATR "
            f"24h resistance clearance="
            f"{target_quality.clearance_to_24h_resistance_pct:.2f}% "
            f"72h resistance clearance="
            f"{target_quality.clearance_to_72h_resistance_pct:.2f}% "
            f"T1 move={target_quality.target_1_move_pct:.2f}% "
            f"24h upside p50/p75/p90="
            f"{snapshot.rolling_24h_upside_median_pct:.2f}/"
            f"{snapshot.rolling_24h_upside_p75_pct:.2f}/"
            f"{snapshot.rolling_24h_upside_p90_pct:.2f}% "
            f"T2 move={target_quality.target_2_move_pct:.2f}% "
            f"72h upside p50/p75/p90="
            f"{snapshot.rolling_72h_upside_median_pct:.2f}/"
            f"{snapshot.rolling_72h_upside_p75_pct:.2f}/"
            f"{snapshot.rolling_72h_upside_p90_pct:.2f}% "
            f"ATR diagnostic only: T1="
            f"{target_quality.target_1_atr_multiple:.2f} "
            f"T2={target_quality.target_2_atr_multiple:.2f}"
        )

        # -----------------------------------------------------
        # STEP 6: HIGH-CONVICTION ECONOMIC QUALITY GATE.
        #
        # This is the important new filter.
        #
        # A technically strong or high-confidence trade can
        # still be rejected if the actual profit opportunity
        # is too small.
        #
        # Current validation capital comes from ACCOUNT_EQUITY.
        # We have set that to $2,000.
        # -----------------------------------------------------
        economic = evaluate_economic_quality(
            plan,
            available_capital=settings.account_equity,
        )

        if not economic.qualified:
            economic_rejected += 1

            # Weak opportunities stay INTERNAL.
            # They do NOT deserve Telegram attention.
            print(
                f"ECONOMIC REJECT {plan.symbol}: "
                f"{economic.rejection_reason}"
            )

            continue

        economic_passed += 1

        print(
            f"ECONOMIC PASS {plan.symbol}: "
            f"T1={economic.target_1_move_pct:.2f}% "
            f"T2={economic.target_2_move_pct:.2f}% "
            f"Net@T2=${economic.target_2_net_profit:.2f} "
            f"R/R={plan.reward_to_risk_2:.2f}:1"
        )

        # -----------------------------------------------------
        # STEP 7: Pending setup.
        #
        # Only ECONOMICALLY QUALIFIED opportunities are allowed
        # into the pending-setup system.
        #
        # Weak WAIT candidates never reach this point.
        # -----------------------------------------------------
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
                confidence=int(
                    alert.get("confidence", 0)
                ),
            )

            add_pending_setup(setup)
            pending_saved += 1

        # -----------------------------------------------------
        # STEP 8: Telegram.
        #
        # For THIS first integration test, any opportunity that
        # reaches here has passed the economic gate.
        #
        # In our NEXT change, Telegram will be restricted to:
        #
        #   ENTER NOW
        #   GET READY
        #   PLACE LIMIT
        #
        # Ordinary WAIT / WATCH / REJECT will remain silent.
        # -----------------------------------------------------
        if send_trade_plan(
            candidate=alert,
            plan=plan,
            summary=review.get("summary", ""),
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        ):
            sent += 1

    # ---------------------------------------------------------
    # STEP 9: Internal scan summary.
    # These statistics remain on the server and do not need
    # to become Telegram notifications.
    # ---------------------------------------------------------
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
