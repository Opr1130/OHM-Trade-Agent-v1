from app.core.config import get_settings
from app.scanner.candidates import select_candidates
from app.scanner.market_scanner import scan_market
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
    # OHM scans up to 100 assets internally.
    # This does NOT mean 100 Telegram notifications.
    # ---------------------------------------------------------
    scan = scan_market(limit=100)

    # ---------------------------------------------------------
    # STEP 2: Local technical screening.
    # Only technically interesting assets continue.
    # ---------------------------------------------------------
    candidates = select_candidates(scan.snapshots)

    print("OHM AI Opportunity Scan")
    print("Requested:", scan.requested)
    print("Analyzed:", scan.analyzed)
    print("Skipped:", scan.skipped)
    print("Failed:", scan.failed)
    print("Technical shortlist:", len(candidates))

    if not candidates:
        print("No technical candidates.")
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
        "Qualified alerts before economic gate:",
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
