from datetime import datetime, timezone
import json

from app.opip.decision.summary import (
    build_recent_qualification_funnel,
    render_recent_qualification_funnel,
)


NOW = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _paths(tmp_path):
    funnel = tmp_path / "funnel.jsonl"
    screening = tmp_path / "screening.jsonl"
    summaries = tmp_path / "summaries.jsonl"
    _write_jsonl(screening, [])
    _write_jsonl(summaries, [])
    return funnel, screening, summaries


def test_build6_attributes_margin_and_deterministic_chokes(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(
        funnel,
        [
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
                        "metadata": {
                            "margin_validation_status": "INELIGIBLE",
                            "margin_venue_symbol": "AAAUSD:BTNL",
                        },
                    }
                ],
            },
            {
                "decision_at_utc": NOW.isoformat(),
                "decision": "OPERATIONAL_FAILURE",
                "first_terminal_gate": "MARGIN_ELIGIBILITY",
                "terminal_reason_code": "MARGIN_VALIDATION_UNAVAILABLE",
                "terminal_reason_class": "OPERATIONAL",
                "gate_results": [
                    {
                        "gate": "MARGIN_ELIGIBILITY",
                        "status": "ERROR",
                        "reason_code": "MARGIN_VALIDATION_UNAVAILABLE",
                        "reason_class": "OPERATIONAL",
                        "metadata": {
                            "margin_validation_status": "UNAVAILABLE",
                        },
                    }
                ],
            },
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
                        "measured_value": 2.3,
                        "threshold": 2.5,
                        "threshold_distance": -0.08,
                        "metadata": {
                            "binding_metric": "ECONOMIC_REWARD_TO_RISK",
                            "risk_levels_evaluated": ["low", "medium"],
                        },
                    }
                ],
            },
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
                        "measured_value": 0.95,
                        "threshold": 1.0,
                        "threshold_distance": -0.05,
                        "metadata": {
                            "binding_metric": "TARGET_T2_ATTAINABILITY",
                            "risk_levels_evaluated": ["low"],
                        },
                    }
                ],
            },
        ],
    )

    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )

    assert report["primary_choke"] == "MARGIN_ELIGIBILITY"
    choke = report["choke_analysis"]
    assert choke["measurement_only"] is True
    assert choke["policy_change_authorized"] is False
    assert choke["margin_eligibility"] == {
        "rejects": 1,
        "errors": 1,
        "rejection_status_counts": {
            "INELIGIBLE": 1,
            "UNAVAILABLE": 1,
        },
        "rejection_reason_counts": {
            "MARGIN_INELIGIBLE": 1,
            "MARGIN_VALIDATION_UNAVAILABLE": 1,
        },
    }
    deterministic = choke["deterministic_viability"]
    assert deterministic["rejects"] == 2
    assert deterministic["binding_metric_counts"] == {
        "ECONOMIC_REWARD_TO_RISK": 1,
        "TARGET_T2_ATTAINABILITY": 1,
    }
    assert deterministic["risk_level_evaluation_counts"] == {
        "LOW": 2,
        "MEDIUM": 1,
    }
    assert deterministic["threshold_distance_samples"] == 2
    assert deterministic["nearest_threshold_gap_pct"] == 5.0
    assert deterministic["median_threshold_gap_pct"] == 6.5


def test_build6_choke_renderer_is_explicitly_measurement_only(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(
        funnel,
        [
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
                        "metadata": {
                            "margin_validation_status": "INELIGIBLE",
                        },
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
    rendered = render_recent_qualification_funnel(report)

    assert "MARGIN_CHOKE_DETAIL" in rendered
    assert "INELIGIBLE=1" in rendered
    assert "DETERMINISTIC_CHOKE_DETAIL" in rendered
    assert "CHOKE_ANALYSIS_POLICY_CHANGE_AUTHORIZED=NO" in rendered


def test_build6_ignores_nonfinite_or_malformed_threshold_distance(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    rows = []
    for value in (True, "bad", "nan", "inf", None):
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
                        "threshold_distance": value,
                        "metadata": {
                            "binding_metric": "ECONOMIC_NET_PROFIT_AT_TARGET_2",
                        },
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
    deterministic = report["choke_analysis"]["deterministic_viability"]
    assert deterministic["threshold_distance_samples"] == 0
    assert deterministic["nearest_threshold_gap_pct"] is None
    assert deterministic["median_threshold_gap_pct"] is None



def test_build6_separates_deterministic_errors_from_policy_rejects(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(
        funnel,
        [
            {
                "decision_at_utc": NOW.isoformat(),
                "decision": "OPERATIONAL_FAILURE",
                "first_terminal_gate": "DETERMINISTIC_QUALITY",
                "terminal_reason_code": "GATE_EVALUATION_ERROR",
                "terminal_reason_class": "OPERATIONAL",
                "gate_results": [
                    {
                        "gate": "DETERMINISTIC_QUALITY",
                        "status": "ERROR",
                        "reason_code": "GATE_EVALUATION_ERROR",
                        "reason_class": "OPERATIONAL",
                        "metadata": {
                            "binding_metric": "UNKNOWN",
                        },
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

    assert report["deterministic_prefilter_reject"] == 0
    assert report["deterministic_prefilter_error"] == 1
    deterministic = report["choke_analysis"]["deterministic_viability"]
    assert deterministic["rejects"] == 0
    assert deterministic["errors"] == 1
    assert report["primary_operational_choke"] == "DETERMINISTIC_QUALITY"
