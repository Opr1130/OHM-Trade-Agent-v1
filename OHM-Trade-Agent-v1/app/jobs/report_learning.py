from __future__ import annotations

from app.services.profitability_learning import build_profitability_profile


def main() -> None:
    profile = build_profitability_profile(persist=True)
    trade = profile.get("trade_calibration") or {}
    shadow = profile.get("shadow_learning") or {}
    timing = profile.get("timing_learning") or {}
    loss = profile.get("loss_learning") or {}

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
    print("SHADOW / MISSED OPPORTUNITY LEARNING")
    print("Samples:", shadow.get("samples", 0))
    print("Decision accuracy %:", shadow.get("decision_accuracy_pct"))
    print("Missed profitable opportunities:", shadow.get("missed_profitable", 0))
    print("Correct rejections/waits:", shadow.get("correct_rejections", 0))
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


if __name__ == "__main__":
    main()
