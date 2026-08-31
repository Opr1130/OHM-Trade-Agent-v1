import json
from datetime import datetime, timezone

from app.opip.decision.explanations import (
    STATE_TELEMETRY_DISABLED,
    _explanation_sentence,
    build_zero_trade_explanation,
)
from app.opip.decision.funnel import counts_by_outcome, invariant_holds
from app.opip.decision.models import (
    AdmissionDecision,
    DecisionOutcome,
    ReasonCode,
    normalized_threshold_distance,
)
from app.services.chief_analyst import binding_deterministic_constraint


def _level(
    *,
    target_qualified=True,
    target_score=80.0,
    economic_qualified=False,
    economic_rejection="",
    reward_to_risk=3.0,
    target_move=5.0,
    net_profit=100.0,
    stop_risk=2.0,
    max_stop_risk=5.0,
):
    return {
        "target_quality_qualified": target_qualified,
        "target_quality_score": target_score,
        "economic_qualified": economic_qualified,
        "economic_rejection": economic_rejection,
        "economic_reward_to_risk_2": reward_to_risk,
        "economic_target_2_move_pct": target_move,
        "hypothetical_target_2_net_profit_at_assumed_capital": net_profit,
        "economic_account_risk_at_stop_pct": stop_risk,
        "economic_max_account_risk_at_stop_pct": max_stop_risk,
    }


def test_binding_constraint_uses_reward_to_risk_rejection():
    quality = {
        "low": _level(
            economic_rejection="Reward/risk 2.00:1 is below minimum 2.50:1",
            reward_to_risk=2.0,
            net_profit=120.0,
        ),
        "medium": _level(
            target_qualified=False,
            target_score=60.0,
            economic_rejection="Projected net profit $50.00 is below minimum $75.00",
            net_profit=50.0,
        ),
    }
    binding = binding_deterministic_constraint(quality)
    assert binding["binding_metric"] == "ECONOMIC_REWARD_TO_RISK"
    assert binding["binding_measured"] == 2.0
    assert binding["binding_threshold"] == 2.5
    assert binding["binding_higher_is_better"] is True


def test_binding_constraint_uses_target_move_rejection():
    quality = {
        "low": _level(
            economic_rejection="Projected Target 2 move 3.20% is below minimum 4.00%",
            target_move=3.2,
            net_profit=120.0,
        )
    }
    binding = binding_deterministic_constraint(quality)
    assert binding["binding_metric"] == "ECONOMIC_TARGET_2_MOVE_PCT"
    assert binding["binding_measured"] == 3.2
    assert binding["binding_threshold"] == 4.0
    assert binding["binding_higher_is_better"] is True


def test_binding_constraint_uses_net_profit_rejection():
    quality = {
        "low": _level(
            economic_rejection=(
                "Projected net profit $60.00 at the 20% capital envelope "
                "is below minimum $75.00"
            ),
            net_profit=60.0,
        )
    }
    binding = binding_deterministic_constraint(quality)
    assert binding["binding_metric"] == "ECONOMIC_NET_PROFIT_AT_TARGET_2"
    assert binding["binding_measured"] == 60.0
    assert binding["binding_threshold"] == 75.0
    assert binding["binding_higher_is_better"] is True


def test_binding_constraint_uses_stop_exposure_and_lower_is_better():
    quality = {
        "low": _level(
            economic_rejection="stop exposure 6.00% exceeds maximum 5.00% of account equity",
            stop_risk=6.0,
            max_stop_risk=5.0,
            net_profit=150.0,
        )
    }
    binding = binding_deterministic_constraint(quality)
    assert binding["binding_metric"] == "ECONOMIC_ACCOUNT_RISK_AT_STOP_PCT"
    assert binding["binding_measured"] == 6.0
    assert binding["binding_threshold"] == 5.0
    assert binding["binding_higher_is_better"] is False
    assert normalized_threshold_distance(
        binding["binding_measured"],
        binding["binding_threshold"],
        higher_is_better=binding["binding_higher_is_better"],
    ) == -0.2


def _decision(reason_code):
    return AdmissionDecision(
        candidate_id=f"candidate-{reason_code.value}",
        episode_id="episode",
        asset="BTC",
        pair="BTCUSD",
        market_type="SPOT",
        direction="LONG",
        decided_at=datetime.now(timezone.utc).isoformat(),
        decision=DecisionOutcome.REJECTED,
        terminal_reason_code=reason_code,
    )


def test_terminal_counts_separate_policy_budget_and_model_rejections():
    decisions = [
        _decision(ReasonCode.ECONOMIC_GATE_FAILED),
        _decision(ReasonCode.AI_BUDGET_LIMIT),
        _decision(ReasonCode.AI_CONFIDENCE_BELOW_THRESHOLD),
    ]
    counts = counts_by_outcome(decisions)
    assert counts["entered"] == 3
    assert counts["rejected_total"] == 3
    assert counts["rejected_by_policy"] == 1
    assert counts["rejected_by_budget"] == 1
    assert counts["rejected_by_model"] == 1
    assert counts["operationally_unresolved"] == 0
    assert invariant_holds(counts) is True


def test_mixed_cohort_budget_explanation_only_attributes_ai_eligible_subset():
    summary = {
        "funnel": {"entered": 8, "qualified": 0},
        "terminal": {
            "dominant_terminal_gate": "DETERMINISTIC_QUALITY",
            "top_reasons": {"DETERMINISTIC_VIABILITY_FAILED": 5},
        },
        "ai_stage": {
            "budget_exhausted": True,
            "unavailable": False,
            "eligible_candidates_before_ai": 3,
        },
    }
    sentence = _explanation_sentence(summary)
    assert "3 of 8 directional candidates reached Chief review" in sentence
    assert "5 stopped earlier in the qualification funnel" in sentence
    assert "All 8" not in sentence


def test_mixed_cohort_unavailable_explanation_only_attributes_ai_eligible_subset():
    summary = {
        "funnel": {"entered": 8, "qualified": 0},
        "terminal": {
            "dominant_terminal_gate": "DETERMINISTIC_QUALITY",
            "top_reasons": {"DETERMINISTIC_VIABILITY_FAILED": 5},
        },
        "ai_stage": {
            "budget_exhausted": False,
            "unavailable": True,
            "failure_type": "TimeoutError",
            "eligible_candidates_before_ai": 3,
        },
    }
    sentence = _explanation_sentence(summary)
    assert "3 of 8 directional candidates reached Chief review" in sentence
    assert "AI service was TimeoutError" in sentence
    assert "5 stopped earlier in the qualification funnel" in sentence
    assert "All 8" not in sentence


def test_disabled_telemetry_ignores_historical_summary(tmp_path):
    summaries = tmp_path / "scan_summaries.jsonl"
    summaries.write_text(
        json.dumps(
            {
                "record_type": "OPIP_SCAN_SUMMARY",
                "scan_id": "old-scan",
                "decision_at_utc": datetime.now(timezone.utc).isoformat(),
                "funnel": {"entered": 1, "qualified": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    explanation = build_zero_trade_explanation(
        summaries_path=summaries,
        telemetry_enabled=False,
    )

    assert explanation["state"] == STATE_TELEMETRY_DISABLED
    assert explanation["telemetry_enabled"] is False
    assert explanation["last_scan_at_utc"] is None
    assert explanation["qualified"] == 0
    assert "not enabled" in explanation["explanation"]
