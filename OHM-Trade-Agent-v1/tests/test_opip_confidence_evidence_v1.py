from datetime import datetime, timezone
import json

from app.opip.decision.gates import evaluate_recommendation_gate_item
from app.opip.decision.models import GateStatus, ReasonCode
from app.opip.decision.summary import (
    build_recent_qualification_funnel,
    render_recent_qualification_funnel,
)
from app.services.recommendation_gate import (
    candidate_alert_authorized,
    confidence_below_measurement_boundary,
    qualified_alerts,
)


NOW = datetime(2026, 9, 2, 15, 0, tzinfo=timezone.utc)


def _item(**overrides):
    value = {
        "symbol": "SOLUSD",
        "decision": "alert",
        "risk_level": "low",
        "direction": "LONG",
        "confidence": 84,
    }
    value.update(overrides)
    return value


def test_low_confidence_alert_is_authorized_for_deterministic_review():
    assert candidate_alert_authorized(_item(confidence=60)) is True


def test_confidence_boundary_is_measurement_only():
    assert confidence_below_measurement_boundary(_item(confidence=84)) is True
    assert confidence_below_measurement_boundary(_item(confidence=85)) is False


def test_watch_is_fail_closed_even_with_high_confidence():
    assert candidate_alert_authorized(_item(decision="watch", confidence=100)) is False


def test_reject_is_fail_closed_even_with_high_confidence():
    assert candidate_alert_authorized(_item(decision="reject", confidence=100)) is False


def test_invalid_risk_is_fail_closed():
    assert candidate_alert_authorized(_item(risk_level="high", confidence=100)) is False


def test_invalid_direction_is_fail_closed():
    assert candidate_alert_authorized(_item(direction="SIDEWAYS", confidence=100)) is False


def test_missing_direction_is_fail_closed():
    item = _item(confidence=100)
    item.pop("direction")
    assert candidate_alert_authorized(item) is False
    result = evaluate_recommendation_gate_item(item)
    assert result.status is GateStatus.FAIL
    assert result.reason_code is ReasonCode.AI_DIRECTION_REJECTED


def test_malformed_confidence_is_fail_closed():
    for malformed in (
        "bad",
        True,
        False,
        None,
        100.1,
        -0.1,
        float("inf"),
        float("-inf"),
        float("nan"),
    ):
        assert candidate_alert_authorized(_item(confidence=malformed)) is False
        result = evaluate_recommendation_gate_item(_item(confidence=malformed))
        assert result.status is GateStatus.FAIL
        assert result.reason_code is ReasonCode.AI_CONFIDENCE_INVALID


def test_integral_confidence_values_remain_valid_evidence():
    for confidence in (0, 1, 70, 84, 85, 100, 85.0):
        assert candidate_alert_authorized(_item(confidence=confidence)) is True


def test_low_confidence_gate_pass_is_tagged_as_measurement_evidence():
    result = evaluate_recommendation_gate_item(_item(confidence=70))
    assert result.status is GateStatus.PASS
    assert result.reason_code is ReasonCode.AI_CONFIDENCE_COUNTERFACTUAL
    assert result.metadata["measurement_only_confidence_boundary"] is True
    assert result.metadata["confidence_is_trade_authority"] is False


def test_qualified_alerts_no_longer_uses_85_as_authority():
    result = qualified_alerts({"top_candidates": [_item(confidence=1)]})
    assert len(result) == 1
    assert result[0]["direction"] == "LONG"


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_recent_funnel_aggregates_confidence_and_primary_choke(tmp_path):
    funnel = tmp_path / "funnel.jsonl"
    screening = tmp_path / "screening.jsonl"
    summaries = tmp_path / "summaries.jsonl"
    _write_jsonl(
        screening,
        [
            {
                "observed_at": NOW.isoformat(),
                "outcome": "ADVANCED",
            },
            {
                "observed_at": NOW.isoformat(),
                "outcome": "BELOW_THRESHOLD",
            },
        ],
    )
    _write_jsonl(
        funnel,
        [
            {
                "decision_at_utc": NOW.isoformat(),
                "decision": "REJECTED",
                "terminal_reason_code": "AI_DECISION_WATCH",
                "gate_results": [
                    {
                        "gate": "DETERMINISTIC_QUALITY",
                        "status": "PASS",
                        "reason_code": "GATE_PASSED",
                        "metadata": {},
                    },
                    {
                        "gate": "AI_ELIGIBILITY",
                        "status": "PASS",
                        "reason_code": "GATE_PASSED",
                        "metadata": {},
                    },
                    {
                        "gate": "AI_INVOCATION",
                        "status": "PASS",
                        "reason_code": "GATE_PASSED",
                        "metadata": {"invocation_status": "SUCCEEDED"},
                    },
                    {
                        "gate": "RECOMMENDATION_GATE",
                        "status": "FAIL",
                        "reason_code": "AI_DECISION_WATCH",
                        "metadata": {
                            "ai_decision": "watch",
                            "ai_confidence": 82,
                        },
                    },
                ],
            },
            {
                "decision_at_utc": NOW.isoformat(),
                "decision": "QUALIFIED",
                "terminal_reason_code": "QUALIFIED",
                "gate_results": [
                    {
                        "gate": "DETERMINISTIC_QUALITY",
                        "status": "PASS",
                        "reason_code": "GATE_PASSED",
                        "metadata": {},
                    },
                    {
                        "gate": "RECOMMENDATION_GATE",
                        "status": "PASS",
                        "reason_code": "AI_CONFIDENCE_COUNTERFACTUAL",
                        "metadata": {
                            "ai_decision": "alert",
                            "ai_confidence": 70,
                        },
                    },
                    {
                        "gate": "TARGET_QUALITY",
                        "status": "PASS",
                        "reason_code": "GATE_PASSED",
                        "metadata": {},
                    },
                    {
                        "gate": "ECONOMIC_QUALITY",
                        "status": "PASS",
                        "reason_code": "GATE_PASSED",
                        "metadata": {},
                    },
                    {
                        "gate": "CAPITAL_PORTFOLIO_GATE",
                        "status": "PASS",
                        "reason_code": "GATE_PASSED",
                        "metadata": {},
                    },
                ],
            },
        ],
    )
    _write_jsonl(
        summaries,
        [
            {
                "decision_at_utc": NOW.isoformat(),
                "paper_admission_eligible": 1,
            }
        ],
    )

    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )
    assert report["market_observed"] == 2
    assert report["scanner_selected"] == 1
    assert report["chief_watch"] == 1
    assert report["chief_alert"] == 1
    assert report["confidence_80_84"] == 1
    assert report["confidence_70_79"] == 1
    assert report["qualified_signals"] == 1
    assert report["paper_admission_eligible"] == 1
    assert report["paper_admitted"] == "NOT_INSTRUMENTED"
    assert report["primary_choke"] == "RECOMMENDATION_GATE"
    assert report["funnel_candidates"] == 2
    assert report["funnel_qualified"] == 1
    assert report["funnel_rejected"] == 1
    assert report["funnel_invariant_holds"] is True
    assert report["measurement_only"] is True
    assert report["affects_trade_authority"] is False


def test_recent_funnel_renderer_marks_uninstrumented_stages(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    report = build_recent_qualification_funnel(
        funnel_events_path=empty,
        screening_evaluations_path=empty,
        scan_summaries_path=empty,
        now=NOW,
    )
    rendered = render_recent_qualification_funnel(report)
    assert "OPIP_QUALIFICATION_FUNNEL" in rendered
    assert "trade_quality_pass=NOT_INSTRUMENTED" in rendered
    assert "capacity_reject=NOT_INSTRUMENTED" in rendered
    assert "paper_admitted=NOT_INSTRUMENTED" in rendered
    assert "PRIMARY_CHOKE=NONE" in rendered



def test_action_gate_error_is_not_reported_as_rejection(tmp_path):
    funnel = tmp_path / "funnel.jsonl"
    screening = tmp_path / "screening.jsonl"
    summaries = tmp_path / "summaries.jsonl"
    _write_jsonl(screening, [])
    _write_jsonl(summaries, [])
    _write_jsonl(
        funnel,
        [
            {
                "decision_at_utc": NOW.isoformat(),
                "decision": "OPERATIONAL_FAILURE",
                "first_terminal_gate": "CAPITAL_PORTFOLIO_GATE",
                "terminal_reason_code": "GATE_EVALUATION_ERROR",
                "terminal_reason_class": "OPERATIONAL",
                "gate_results": [
                    {
                        "gate": "CAPITAL_PORTFOLIO_GATE",
                        "status": "ERROR",
                        "reason_code": "GATE_EVALUATION_ERROR",
                        "metadata": {},
                    }
                ],
            }
        ],
    )
    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )
    assert report["action_gate_error"] == 1
    assert report["action_gate_reject"] == 0
    assert report["primary_choke"] == "CAPITAL_PORTFOLIO_GATE"
    assert report["primary_operational_choke"] == "CAPITAL_PORTFOLIO_GATE"



def test_recent_funnel_normalizes_nonpositive_window(tmp_path):
    empty = tmp_path / "empty-window.jsonl"
    empty.write_text("", encoding="utf-8")
    for requested in (0, -5):
        report = build_recent_qualification_funnel(
            funnel_events_path=empty,
            screening_evaluations_path=empty,
            scan_summaries_path=empty,
            now=NOW,
            window_hours=requested,
        )
        assert report["window_hours"] == 1



def test_recent_funnel_ignores_invalid_confidence_evidence(tmp_path):
    funnel = tmp_path / "invalid-confidence.jsonl"
    screening = tmp_path / "screening.jsonl"
    summaries = tmp_path / "summaries.jsonl"
    _write_jsonl(screening, [])
    _write_jsonl(summaries, [])
    _write_jsonl(
        funnel,
        [
            {
                "decision_at_utc": NOW.isoformat(),
                "decision": "REJECTED",
                "terminal_reason_code": "AI_CONFIDENCE_INVALID",
                "gate_results": [
                    {
                        "gate": "RECOMMENDATION_GATE",
                        "status": "FAIL",
                        "reason_code": "AI_CONFIDENCE_INVALID",
                        "metadata": {"ai_decision": "alert", "ai_confidence": value},
                    }
                ],
            }
            for value in (101, True, 84.5, -1)
        ],
    )
    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )
    assert report["confidence_ge_85"] == 0
    assert report["confidence_80_84"] == 0
    assert report["confidence_70_79"] == 0
    assert report["confidence_lt_70"] == 0


def test_recent_funnel_skips_malformed_paper_admission_values(tmp_path):
    funnel = tmp_path / "funnel.jsonl"
    screening = tmp_path / "screening.jsonl"
    summaries = tmp_path / "summaries.jsonl"
    _write_jsonl(funnel, [])
    _write_jsonl(screening, [])
    _write_jsonl(
        summaries,
        [
            {"decision_at_utc": NOW.isoformat(), "paper_admission_eligible": 2},
            {"decision_at_utc": NOW.isoformat(), "paper_admission_eligible": "3"},
            {"decision_at_utc": NOW.isoformat(), "paper_admission_eligible": "unknown"},
            {"decision_at_utc": NOW.isoformat(), "paper_admission_eligible": True},
            {"decision_at_utc": NOW.isoformat(), "paper_admission_eligible": 1.5},
            {"decision_at_utc": NOW.isoformat(), "paper_admission_eligible": -4},
        ],
    )
    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )
    assert report["paper_admission_eligible"] == 5



def test_recent_funnel_separates_broad_search_from_early_watch(tmp_path):
    funnel = tmp_path / "funnel-mixed.jsonl"
    screening = tmp_path / "screening-mixed.jsonl"
    summaries = tmp_path / "summaries-mixed.jsonl"
    _write_jsonl(funnel, [])
    _write_jsonl(summaries, [])
    _write_jsonl(
        screening,
        [
            {
                "observed_at": NOW.isoformat(),
                "scanner_type": "BROAD_SEARCH",
                "outcome": "ADVANCED",
            },
            {
                "observed_at": NOW.isoformat(),
                "scanner_type": "BROAD_SEARCH",
                "outcome": "BELOW_THRESHOLD",
            },
            {
                "observed_at": NOW.isoformat(),
                "scanner_type": "EARLY_WATCH",
                "outcome": "ADVANCED",
            },
            {
                "observed_at": NOW.isoformat(),
                "scanner_type": "EARLY_WATCH",
                "outcome": "BELOW_COARSE_THRESHOLD",
            },
        ],
    )

    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )

    assert report["market_observed"] == 2
    assert report["scanner_selected"] == 1
    assert report["broad_search_observed"] == 2
    assert report["broad_search_threshold_advanced"] == 1
    assert report["early_watch_observed"] == 2
    assert report["early_watch_advanced"] == 1


def test_recent_funnel_uses_actual_first_terminal_gate_for_choke(tmp_path):
    funnel = tmp_path / "funnel-terminal.jsonl"
    screening = tmp_path / "screening-terminal.jsonl"
    summaries = tmp_path / "summaries-terminal.jsonl"
    _write_jsonl(screening, [])
    _write_jsonl(summaries, [])
    rows = []
    for _ in range(3):
        rows.append(
            {
                "decision_at_utc": NOW.isoformat(),
                "decision": "REJECTED",
                "first_terminal_gate": "MARGIN_ELIGIBILITY",
                "terminal_reason_code": "MARGIN_INELIGIBLE",
                "terminal_reason_class": "POLICY",
                "gate_results": [
                    {
                        "gate": "MARGIN_ELIGIBILITY",
                        "status": "FAIL",
                        "reason_code": "MARGIN_INELIGIBLE",
                        "reason_class": "POLICY",
                        "metadata": {},
                    }
                ],
            }
        )
    rows.append(
        {
            "decision_at_utc": NOW.isoformat(),
            "decision": "REJECTED",
            "first_terminal_gate": "DETERMINISTIC_QUALITY",
            "terminal_reason_code": "DETERMINISTIC_VIABILITY_FAILED",
            "terminal_reason_class": "POLICY",
            "gate_results": [
                {
                    "gate": "DETERMINISTIC_QUALITY",
                    "status": "FAIL",
                    "reason_code": "DETERMINISTIC_VIABILITY_FAILED",
                    "reason_class": "POLICY",
                    "metadata": {},
                }
            ],
        }
    )
    _write_jsonl(funnel, rows)

    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )

    assert report["margin_reject"] == 3
    assert report["deterministic_prefilter_reject"] == 1
    assert report["primary_choke"] == "MARGIN_ELIGIBILITY"
    assert report["primary_policy_choke"] == "MARGIN_ELIGIBILITY"
    assert report["terminal_gate_counts"] == {
        "MARGIN_ELIGIBILITY": 3,
        "DETERMINISTIC_QUALITY": 1,
    }
    assert report["funnel_invariant_holds"] is True
