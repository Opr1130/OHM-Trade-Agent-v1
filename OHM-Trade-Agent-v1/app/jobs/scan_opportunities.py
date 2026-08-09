from app.core.config import get_settings
from app.scanner.candidates import select_candidates
from app.scanner.market_scanner import (
    confirm_secondary_markets,
    deep_validate_candidates,
    scan_market,
)
from app.scanner.universe import DEFAULT_UNIQUE_ASSET_LIMIT
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
            f"Status={execution.status} "
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

    # ---------------------------------------------------------
    # STEP 3: AI Chief review.
    # The Chief compares only the technical shortlist.
    # ---------------------------------------------------------
    review = review_candidates(
        candidates,
        settings.openai_model,
        settings.openai_api_key,
        settings.account_equity,
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
