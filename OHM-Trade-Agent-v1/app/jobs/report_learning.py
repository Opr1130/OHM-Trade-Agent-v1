from __future__ import annotations

from app.services.counterfactual_gate_audit import build_counterfactual_gate_audit
from app.services.decision_learning import decision_quality_summary
from app.services.profitability_learning import build_profitability_profile
from app.services.shadow_learning import get_shadow_records
from app.services.signal_quality_audit import build_signal_quality_audit
from app.services.target_v2_coverage_summary import build_target_v2_coverage_summary


def main() -> None:
    profile = build_profitability_profile(persist=True)
    trade = profile.get("trade_calibration") or {}
    shadow = profile.get("shadow_learning") or {}
    timing = profile.get("timing_learning") or {}
    loss = profile.get("loss_learning") or {}
    attribution = profile.get("market_intelligence_attribution") or {}
    actual_attribution = attribution.get("actual_trade_attribution") or {}
    shadow_attribution = attribution.get("shadow_confirmation") or {}
    promotion = attribution.get("promotion") or {}
    shadow_records = get_shadow_records()
    decision = decision_quality_summary(shadow_records)
    signal_quality = build_signal_quality_audit()
    counterfactual = build_counterfactual_gate_audit(shadow_records)
    target_v2 = build_target_v2_coverage_summary(shadow_records)

    print("===== OHM PROFITABILITY SELF-LEARNING =====")
    print("Generated:", profile.get("generated_at"))
    print("Paid AI calls:", profile.get("paid_ai_calls", 0))
    print("Objective:", profile.get("objective"))
    print()
    print("TRADE CALIBRATION")
    print("Status:", trade.get("status"))
    print("Samples:", trade.get("samples", 0))
    print("Baseline net win rate:", trade.get("baseline_net_win_rate"))
    print("Learned weights:", profile.get("weights") or {})
    print()
    print("SIGNAL QUALITY AUDIT V1")
    print("Status:", signal_quality.get("status"))
    print("Resolved entered:", signal_quality.get("resolved_entered", 0))
    print("Resolved no-entry:", signal_quality.get("resolved_no_entry", 0))
    print("Target 1 success %:", signal_quality.get("target_1_success_rate_pct"))
    print("Target 2 success %:", signal_quality.get("target_2_success_rate_pct"))
    print("Stop-before-target %:", signal_quality.get("stop_before_target_rate_pct"))
    print("Entry conversion %:", signal_quality.get("entry_conversion_rate_pct"))
    print("Average MFE %:", signal_quality.get("average_mfe_pct"))
    print("Average MAE %:", signal_quality.get("average_mae_pct"))
    print("Failure reasons:", signal_quality.get("failure_reasons") or {})
    print("Automatic tuning applied:", signal_quality.get("automatic_tuning_applied", False))
    print()
    print("COUNTERFACTUAL GATE AUDIT V1.1")
    print("Status:", counterfactual.get("status"))
    print("Evaluated rejections:", counterfactual.get("evaluated_rejections", 0))
    print("Pending rejections:", counterfactual.get("pending_rejections", 0))
    print("Classifications:", counterfactual.get("classifications") or {})
    print("Gate-family attribution:")
    for family, stats in (counterfactual.get("by_gate_family") or {}).items():
        print(
            f"  {family}: samples={stats.get('samples', 0)} "
            f"false-like={stats.get('false_reject_like', 0)} "
            f"false-like-rate={stats.get('false_reject_like_rate_pct')}% "
            f"good={stats.get('good_rejects', 0)} "
            f"safety-upside={stats.get('safety_rejects_with_later_upside', 0)}"
        )
    print("Decision attribution:")
    for name, stats in (counterfactual.get("by_decision") or {}).items():
        print(
            f"  {name}: samples={stats.get('samples', 0)} "
            f"false-like={stats.get('false_reject_like', 0)} "
            f"false-like-rate={stats.get('false_reject_like_rate_pct')}% "
            f"good={stats.get('good_rejects', 0)} "
            f"safety-upside={stats.get('safety_rejects_with_later_upside', 0)}"
        )
    interpretation = counterfactual.get("interpretation") or {}
    print("Heuristic only:", interpretation.get("heuristic_only", True))
    print("Price path is execution proof:", not interpretation.get("price_path_is_not_execution_proof", True))
    print("Safety gates remain authoritative:", interpretation.get("safety_gates_remain_authoritative", True))
    print("Automatic tuning applied:", interpretation.get("automatic_tuning_applied", False))
    print()
    print("TARGET ATTAINABILITY V2 SHADOW CHALLENGER")
    print("Status:", target_v2.get("status"))
    overall = target_v2.get("overall") or {}
    print("Shadow samples:", overall.get("samples", 0))
    print("Validated samples:", overall.get("validated", 0))
    print("Overall T1 hit rate %:", overall.get("t1_shadow_hit_rate_pct"))
    print("Overall target classes:", overall.get("target_classes") or {})
    print("Coverage by pipeline segment:")
    for segment, stats in (target_v2.get("segments") or {}).items():
        print(
            f"  {segment}: samples={stats.get('samples', 0)} "
            f"validated={stats.get('validated', 0)} "
            f"T1-hit-rate={stats.get('t1_shadow_hit_rate_pct')}% "
            f"classes={stats.get('target_classes') or {}}"
        )
    print("Coverage by AI decision:")
    for name, stats in (target_v2.get("ai_decisions") or {}).items():
        print(
            f"  {name}: samples={stats.get('samples', 0)} "
            f"validated={stats.get('validated', 0)} "
            f"T1-hit-rate={stats.get('t1_shadow_hit_rate_pct')}% "
            f"classes={stats.get('target_classes') or {}}"
        )
    print("Production target gate changed:", target_v2.get("production_target_gate_changed", False))
    print("Production decisions changed:", target_v2.get("production_decisions_changed", False))
    print("Automatic promotion:", target_v2.get("automatic_promotion", False))
    print("Shadow only:", target_v2.get("shadow_only", True))
    print()
    print("SHADOW / MISSED OPPORTUNITY LEARNING")
    print("Samples:", shadow.get("samples", 0))
    print("Decision accuracy %:", shadow.get("decision_accuracy_pct"))
    print("Missed profitable opportunities:", shadow.get("missed_profitable", 0))
    print("Correct rejections/waits:", shadow.get("correct_rejections", 0))
    print()
    print("DECISION QUALITY / IMMEDIATE VS WAIT")
    print("Evaluated shadows:", decision.get("evaluated", 0))
    print("Missed profitable opportunities:", decision.get("missed_profitable_opportunities", 0))
    print("Correct wait/reject decisions:", decision.get("correct_wait_or_reject", 0))
    by_decision = decision.get("by_decision") or {}
    if by_decision:
        print("Decision coverage:")
        for name, stats in by_decision.items():
            print(
                f"  {name}: samples={stats.get('count', 0)} "
                f"missed={stats.get('missed', 0)} "
                f"correct_avoid={stats.get('correct_avoid', 0)}"
            )
    for horizon, stats in (decision.get("immediate_vs_wait") or {}).items():
        print(
            f"Wait {horizon}: samples={stats.get('samples', 0)} "
            f"avg delayed edge={stats.get('avg_delayed_edge_pct')}% "
            f"better-rate={stats.get('wait_was_better_rate_pct')}% "
            f"sufficient={stats.get('sufficient_samples')}"
        )
    print()
    print("EXECUTION TIMING")
    print("Financial samples:", timing.get("samples", 0))
    print("Idea->order winners sec:", timing.get("avg_idea_to_order_seconds_winners"))
    print("Idea->order losers sec:", timing.get("avg_idea_to_order_seconds_losers"))
    print("Order->fill winners sec:", timing.get("avg_order_to_fill_seconds_winners"))
    print("Order->fill losers sec:", timing.get("avg_order_to_fill_seconds_losers"))
    print("Net P/L per capital-hour:", timing.get("avg_net_pnl_per_capital_hour"))
    print()
    print("LOSS HARDENING")
    print("Losing trades:", loss.get("losing_trades", 0))
    print("Potentially avoidable:", loss.get("potentially_avoidable_losses", 0))
    print("Potentially avoidable loss $:", loss.get("potentially_avoidable_loss_dollars", 0.0))
    print("Heuristic only:", loss.get("heuristic_only", True))
    print()
    print("WAVE 8.1 MARKET INTELLIGENCE ATTRIBUTION")
    print("Status:", attribution.get("status"))
    print("Actual trade samples:", actual_attribution.get("samples", 0))
    print("Shadow confirmation samples:", shadow_attribution.get("samples", 0))
    print("Proposed review-only weights:", attribution.get("proposed_weights") or {})
    print("Promotion status:", promotion.get("status"))
    print("Automatically applied:", promotion.get("automatically_applied", False))


if __name__ == "__main__":
    main()
