"""Build 6 — qualification choke intelligence (measurement-only) regressions.

These tests exercise the diagnose-learning aggregation path only. They do not
change thresholds, ranking, alerts, paper admission, or exchange authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
import ast
import json
from pathlib import Path

from app.opip.decision.summary import (
    build_recent_qualification_funnel,
    render_recent_qualification_funnel,
)


NOW = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)
SUMMARY_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "opip"
    / "decision"
    / "summary.py"
)


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


def _margin_policy_row():
    return {
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
    }


def _margin_operational_row():
    return {
        "decision_at_utc": NOW.isoformat(),
        "decision": "OPERATIONAL_FAILURE",
        "first_terminal_gate": "MARGIN_ELIGIBILITY",
        "terminal_reason_code": "MARGIN_VALIDATION_UNAVAILABLE",
        "terminal_reason_class": "OPERATIONAL",
        "gate_results": [
            {
                "gate": "MARGIN_ELIGIBILITY",
                "status": "FAIL",
                "reason_code": "MARGIN_VALIDATION_UNAVAILABLE",
                "reason_class": "OPERATIONAL",
                "metadata": {"margin_validation_status": "UNAVAILABLE"},
            }
        ],
    }


def _deterministic_policy_row(
    *,
    binding_metric="ECONOMIC_REWARD_TO_RISK",
    measured=2.3,
    threshold=2.5,
    distance=-0.08,
    risk_levels=None,
    risk_levels_evaluated=None,
):
    metadata = {"binding_metric": binding_metric}
    if risk_levels is not None:
        metadata["risk_levels"] = risk_levels
    if risk_levels_evaluated is not None:
        metadata["risk_levels_evaluated"] = risk_levels_evaluated
    return {
        "decision_at_utc": NOW.isoformat(),
        "decision": "REJECTED",
        "first_terminal_gate": "DETERMINISTIC_QUALITY",
        "terminal_reason_code": "DETERMINISTIC_VIABILITY_FAILED",
        "terminal_reason_class": "POLICY",
        "gate_results": [
            {
                "gate": "MARGIN_ELIGIBILITY",
                "status": "PASS",
                "reason_code": "GATE_PASSED",
                "reason_class": "POLICY",
                "metadata": {"margin_validation_status": "ELIGIBLE"},
            },
            {
                "gate": "DETERMINISTIC_QUALITY",
                "status": "FAIL",
                "reason_code": "DETERMINISTIC_VIABILITY_FAILED",
                "reason_class": "POLICY",
                "measured_value": measured,
                "threshold": threshold,
                "threshold_distance": distance,
                "metadata": metadata,
            },
        ],
    }


def _deterministic_operational_row():
    return {
        "decision_at_utc": NOW.isoformat(),
        "decision": "OPERATIONAL_FAILURE",
        "first_terminal_gate": "DETERMINISTIC_QUALITY",
        "terminal_reason_code": "GATE_EVALUATION_ERROR",
        "terminal_reason_class": "OPERATIONAL",
        "gate_results": [
            {
                "gate": "MARGIN_ELIGIBILITY",
                "status": "PASS",
                "reason_code": "GATE_PASSED",
                "reason_class": "POLICY",
                "metadata": {"margin_validation_status": "ELIGIBLE"},
            },
            {
                "gate": "DETERMINISTIC_QUALITY",
                "status": "ERROR",
                "reason_code": "GATE_EVALUATION_ERROR",
                "reason_class": "OPERATIONAL",
                "threshold_distance": -0.99,
                "metadata": {"binding_metric": "SHOULD_NOT_COUNT"},
            },
        ],
    }


def _qualified_row():
    return {
        "decision_at_utc": NOW.isoformat(),
        "decision": "QUALIFIED",
        "first_terminal_gate": None,
        "terminal_reason_code": None,
        "terminal_reason_class": None,
        "gate_results": [
            {
                "gate": "MARGIN_ELIGIBILITY",
                "status": "PASS",
                "reason_code": "GATE_PASSED",
                "reason_class": "POLICY",
                "metadata": {"margin_validation_status": "ELIGIBLE"},
            },
            {
                "gate": "DETERMINISTIC_QUALITY",
                "status": "PASS",
                "reason_code": "GATE_PASSED",
                "reason_class": "POLICY",
                "metadata": {},
            },
        ],
    }


def test_build6_attributes_margin_and_deterministic_chokes(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(
        funnel,
        [
            _margin_policy_row(),
            _margin_operational_row(),
            _deterministic_policy_row(
                risk_levels={
                    "low": {"target_qualified": False},
                    "medium": {"target_qualified": False},
                }
            ),
            _deterministic_policy_row(
                binding_metric="TARGET_T2_ATTAINABILITY",
                measured=0.95,
                threshold=1.0,
                distance=-0.05,
                risk_levels_evaluated=["low"],
            ),
        ],
    )

    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )

    assert report["primary_choke"] == "MARGIN_ELIGIBILITY"
    assert report["margin_reject"] == 1
    assert report["margin_error"] == 1
    choke = report["choke_analysis"]
    assert choke["measurement_only"] is True
    assert choke["policy_change_authorized"] is False
    assert choke["margin_eligibility"]["evaluated"] == 4
    assert choke["margin_eligibility"]["passed"] == 2
    assert choke["margin_eligibility"]["rejects"] == 1
    assert choke["margin_eligibility"]["errors"] == 1
    assert choke["margin_eligibility"]["rejection_status_counts"] == {
        "INELIGIBLE": 1,
        "UNAVAILABLE": 1,
    }
    assert choke["margin_eligibility"]["rejection_reason_counts"] == {
        "MARGIN_INELIGIBLE": 1,
        "MARGIN_VALIDATION_UNAVAILABLE": 1,
    }
    assert choke["margin_eligibility"]["rejection_reason_pct"] == {
        "MARGIN_INELIGIBLE": 50.0,
        "MARGIN_VALIDATION_UNAVAILABLE": 50.0,
    }
    deterministic = choke["deterministic_viability"]
    assert deterministic["rejects"] == 2
    assert deterministic["deterministic_policy_reject_count"] == 2
    assert deterministic["deterministic_operational_error_count"] == 0
    assert report["deterministic_policy_reject_count"] == 2
    assert report["deterministic_operational_error_count"] == 0
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
    assert deterministic["policy_reject_samples"][0]["measured_value"] == 2.3
    assert deterministic["policy_reject_samples"][0]["threshold"] == 2.5


def test_build6_choke_renderer_is_explicitly_measurement_only(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(
        funnel,
        [
            _margin_policy_row(),
            _deterministic_operational_row(),
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
    assert "MARGIN_REJECTION_REASON_PCT" in rendered
    assert "DETERMINISTIC_CHOKE_DETAIL" in rendered
    assert "deterministic_prefilter_error=1" in rendered
    assert "DETERMINISTIC_ERRORS=1" in rendered
    assert "DETERMINISTIC_POLICY_REJECT_COUNT=0" in rendered
    assert "DETERMINISTIC_OPERATIONAL_ERROR_COUNT=1" in rendered
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
    assert all(
        sample["threshold_distance"] is None
        for sample in deterministic["policy_reject_samples"]
    )


def test_build6_separates_deterministic_errors_from_policy_rejects(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(funnel, [_deterministic_operational_row()])
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
    assert deterministic["binding_metric_counts"] == {}
    assert deterministic["threshold_distance_samples"] == 0
    assert report["primary_operational_choke"] == "DETERMINISTIC_QUALITY"


def test_build6_margin_unavailable_reason_code_is_operational_without_reason_class(
    tmp_path,
):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(
        funnel,
        [
            {
                "decision_at_utc": NOW.isoformat(),
                "decision": "OPERATIONAL_FAILURE",
                "first_terminal_gate": "MARGIN_ELIGIBILITY",
                "terminal_reason_code": "MARGIN_VALIDATION_UNAVAILABLE",
                "terminal_reason_class": "OPERATIONAL",
                "gate_results": [
                    {
                        "gate": "MARGIN_ELIGIBILITY",
                        "status": "FAIL",
                        "reason_code": "MARGIN_VALIDATION_UNAVAILABLE",
                        "metadata": {
                            "margin_validation_status": "UNAVAILABLE",
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

    assert report["margin_reject"] == 0
    assert report["margin_error"] == 1
    margin = report["choke_analysis"]["margin_eligibility"]
    assert margin["rejects"] == 0
    assert margin["errors"] == 1
    assert margin["rejection_reason_counts"] == {
        "MARGIN_VALIDATION_UNAVAILABLE": 1,
    }


def test_build6_e2e_scenario1_margin_policy_reject_not_double_counted(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(funnel, [_margin_policy_row()])
    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )
    assert report["terminal_gate_counts"] == {"MARGIN_ELIGIBILITY": 1}
    assert report["margin_reject"] == 1
    assert report["deterministic_prefilter_reject"] == 0
    assert report["funnel_invariant_holds"] is True
    rendered = render_recent_qualification_funnel(report)
    assert "PRIMARY_CHOKE=MARGIN_ELIGIBILITY" in rendered


def test_build6_e2e_scenario2_deterministic_policy_reject_fields(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(
        funnel,
        [
            _deterministic_policy_row(
                binding_metric="ECONOMIC_REWARD_TO_RISK",
                measured=1.8,
                threshold=2.0,
                distance=-0.1,
                risk_levels={"low": {}, "medium": {}},
            )
        ],
    )
    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )
    sample = report["choke_analysis"]["deterministic_viability"][
        "policy_reject_samples"
    ][0]
    assert sample["binding_metric"] == "ECONOMIC_REWARD_TO_RISK"
    assert sample["measured_value"] == 1.8
    assert sample["threshold"] == 2.0
    assert sample["threshold_distance"] == -0.1
    assert sample["risk_levels_evaluated"] == ["LOW", "MEDIUM"]
    assert report["deterministic_policy_reject_count"] == 1
    assert report["choke_analysis"]["deterministic_viability"][
        "nearest_threshold_gap_pct"
    ] == 10.0


def test_build6_e2e_scenario3_deterministic_operational_error(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(funnel, [_deterministic_operational_row()])
    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )
    assert report["deterministic_operational_error_count"] == 1
    assert report["deterministic_policy_reject_count"] == 0
    assert report["choke_analysis"]["deterministic_viability"][
        "policy_reject_samples"
    ] == []


def test_build6_e2e_scenario4_malformed_evidence_excluded_from_gaps(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(
        funnel,
        [
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
                        "measured_value": "not-a-number",
                        "threshold": float("nan"),
                        "threshold_distance": float("inf"),
                        "metadata": {"binding_metric": "TARGET_QUALITY_SCORE"},
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
    deterministic = report["choke_analysis"]["deterministic_viability"]
    assert deterministic["rejects"] == 1
    assert deterministic["threshold_distance_samples"] == 0
    sample = deterministic["policy_reject_samples"][0]
    assert sample["measured_value"] is None
    assert sample["threshold"] is None
    assert sample["threshold_distance"] is None


def test_build6_e2e_scenario5_passing_candidate_no_false_reject(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    _write_jsonl(funnel, [_qualified_row()])
    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )
    assert report["funnel_qualified"] == 1
    assert report["margin_reject"] == 0
    assert report["deterministic_prefilter_reject"] == 0
    assert report["terminal_gate_counts"] == {}
    assert report["choke_analysis"]["deterministic_viability"]["rejects"] == 0


def test_build6_e2e_scenario6_aggregate_funnel_invariant(tmp_path):
    funnel, screening, summaries = _paths(tmp_path)
    rows = [
        _margin_policy_row(),
        _margin_operational_row(),
        _deterministic_policy_row(
            risk_levels_evaluated=["low"],
            distance=-0.04,
        ),
        _deterministic_operational_row(),
        _qualified_row(),
        _qualified_row(),
    ]
    _write_jsonl(funnel, rows)
    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )
    assert report["funnel_candidates"] == 6
    assert report["funnel_qualified"] == 2
    assert report["funnel_rejected"] == 2
    assert report["funnel_operational_failure"] == 2
    assert report["funnel_invariant_holds"] is True
    assert report["funnel_candidates"] == (
        report["funnel_qualified"]
        + report["funnel_rejected"]
        + report["funnel_operational_failure"]
        + report["funnel_incomplete"]
    )
    # First-terminal accounting: no double count across terminal gates.
    assert sum(report["terminal_gate_counts"].values()) == 4
    assert report["margin_pass"] == 4  # two deterministic + two qualified


def test_build6_measurement_only_summary_has_no_exchange_authority_imports():
    source = SUMMARY_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            if node.module.startswith("app."):
                imported.add(node.module)
    forbidden = {
        "app.exchanges",
        "app.exchanges.kraken",
        "app.services.kraken_order",
        "app.services.paper_trade_control",
        "app.services.telegram_command_center",
    }
    assert not (imported & forbidden)
    assert "measurement_only" in source
    assert "policy_change_authorized" in source
