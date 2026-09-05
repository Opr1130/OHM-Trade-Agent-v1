"""Build 6 — qualification choke intelligence (measurement-only) regressions.

These tests exercise the diagnose-learning aggregation path only. They do not
change thresholds, ranking, alerts, paper admission, or exchange authority.

Synthetic JSONL scenarios remain as aggregation/edge-case contracts. The
producer-integration tests below cross the real GateResult → QualificationFunnel
→ append_funnel_events → build_recent_qualification_funnel boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
import ast
import json
from pathlib import Path

from app.exchanges.kraken import KrakenAPIError
from app.opip.decision import store
from app.opip.decision.observer import OPipScanObserver
from app.opip.decision.summary import (
    build_recent_qualification_funnel,
    render_recent_qualification_funnel,
)
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.margin_eligibility import validate_short_margin_eligibility
from app.scanner.models import MarketSnapshot
from app.scanner.reference_market_validation import ReferenceMarketValidation
from app.services.chief_analyst import (
    _prefilter_evidence,
    _quality_by_risk_level,
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


# ---------------------------------------------------------------------------
# Producer → persistence → diagnostic integration (real GateResult schema)
# ---------------------------------------------------------------------------


class _MarginFailClient:
    """Kraken margin-pair client that fails closed like a venue outage."""

    def get_asset_pairs(self, execution_venue=None):
        raise KrakenAPIError("margin discovery unavailable")


def _install_funnel_store(monkeypatch, tmp_path):
    """Point production funnel persistence at an isolated temp tree."""
    funnel = tmp_path / "funnel_events.jsonl"
    screening = tmp_path / "screening.jsonl"
    summaries = tmp_path / "scan_summaries.jsonl"
    dead = tmp_path / "dead.jsonl"
    monkeypatch.setattr(store, "FUNNEL_EVENTS_FILE", funnel)
    monkeypatch.setattr(store, "SCAN_SUMMARIES_FILE", summaries)
    monkeypatch.setattr(store, "SCREENING_EVALUATIONS_FILE", screening)
    monkeypatch.setattr(store, "DEAD_LETTER_FILE", dead)
    monkeypatch.setenv("OPIP_FUNNEL_TELEMETRY_ENABLED", "true")
    screening.write_text("", encoding="utf-8")
    return funnel, screening, summaries


def _short_snapshot(*, symbol="SHORTUSD", price=90.0):
    return MarketSnapshot(
        symbol=symbol,
        last_price=price,
        ema20=price * 1.02,
        ema50=price * 1.05,
        ema200=price * 1.10,
        rsi=45.0,
        macd_line=-2.0,
        macd_signal=-1.0,
        macd_histogram=-1.0,
        atr=price * 0.02,
        atr_pct=2.0,
        volume_ratio=1.6,
        technical_score=85,
        trend="bearish",
        recent_24h_high=price * 1.10,
        recent_24h_low=price * 0.90,
        recent_72h_high=price * 1.20,
        recent_72h_low=price * 0.80,
        momentum_6h_pct=-1.0,
        momentum_24h_pct=-3.0,
        momentum_72h_pct=-6.0,
        underlying_asset=symbol.removesuffix("USD"),
        primary_pair=symbol,
        primary_quote_currency="USD",
        combined_24h_liquidity_usd=1_000_000.0,
        ticker_last=price,
        trade_direction="SHORT",
        kraken_public_symbol=f"{symbol.removesuffix('USD')}/USD",
    )


def _long_no_economic_snapshot(*, symbol="RAYUSD", price=100.0):
    """Realistic LONG snapshot that fails the deterministic economic prefilter."""
    snap = MarketSnapshot(
        symbol=symbol,
        last_price=price,
        ema20=price * 0.99,
        ema50=price * 0.95,
        ema200=price * 0.90,
        rsi=55.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=price * 0.0005,
        atr_pct=0.05,
        volume_ratio=1.5,
        technical_score=90,
        trend="bullish",
        recent_24h_high=price * 1.20,
        recent_24h_low=price * 0.94,
        recent_72h_high=price * 1.30,
        recent_72h_low=price * 0.88,
        momentum_6h_pct=1.0,
        momentum_24h_pct=3.0,
        momentum_72h_pct=7.0,
        distance_to_24h_high_pct=16.0,
        distance_to_72h_high_pct=23.0,
        realized_range_24h_pct=16.0,
        realized_range_72h_pct=28.0,
        average_hourly_range_24h_pct=1.0,
        average_hourly_range_72h_pct=1.1,
        rolling_24h_range_median_pct=8.0,
        rolling_24h_range_p75_pct=10.0,
        rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0,
        rolling_72h_range_p75_pct=16.0,
        rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=6.0,
        rolling_24h_upside_p75_pct=8.0,
        rolling_24h_upside_p90_pct=10.0,
        rolling_72h_upside_median_pct=10.0,
        rolling_72h_upside_p75_pct=14.0,
        rolling_72h_upside_p90_pct=18.0,
        underlying_asset=symbol.removesuffix("USD"),
        primary_pair=symbol,
        primary_quote_currency="USD",
        combined_24h_liquidity_usd=5_000_000.0,
        primary_24h_liquidity_usd=5_000_000.0,
        cross_pair_confirmation_status="CONFIRMED",
        ticker_last=price,
        trade_direction="LONG",
    )
    snap.execution_validation = ExecutionValidation(
        status="VALID",
        book_coverage_status="COMPLETE",
        warnings=[],
        validation_notional_usd=2_000.0,
        spread_bps=6.0,
        estimated_visible_round_trip_market_drag_pct=0.25,
        recent_trade_status="FRESH",
    )
    snap.independent_market_reference = ReferenceMarketValidation(
        status="CONFIRMED",
        available=True,
        mapping_status="UNIQUE",
        api_mode="KEYLESS",
        coingecko_id="asset",
        coingecko_name="Asset",
        matched_candidate_count=1,
        reference_price_usd=price,
        kraken_normalized_price_usd=price,
        price_divergence_pct=0.0,
    )
    return snap


def test_build6_producer_margin_unavailable_through_observer_persistence(
    monkeypatch, tmp_path
):
    """Real margin producer → funnel persistence → diagnose aggregation.

    Boundary under test:
    validate_short_margin_eligibility
      → OPipScanObserver.record_margin → evaluate_margin_gate
      → QualificationFunnel.funnel_events (AdmissionDecision.as_dict)
      → store.append_funnel_events
      → build_recent_qualification_funnel / render_recent_qualification_funnel
    """
    funnel, screening, summaries = _install_funnel_store(monkeypatch, tmp_path)
    candidate = _short_snapshot()
    validate_short_margin_eligibility(
        [candidate], client=_MarginFailClient()
    )
    assert candidate.margin_validation_status == "UNAVAILABLE"

    observer = OPipScanObserver(
        snapshots=[candidate],
        decision_at=NOW,
        account_equity=10_000.0,
        telemetry_enabled=True,
    )
    observer.register_candidates([candidate])
    observer.record_margin([candidate])
    observer.finalize(scan_context={}, print_summary=False)

    persisted = store.read_jsonl(funnel)
    assert len(persisted) == 1
    assert persisted[0]["first_terminal_gate"] == "MARGIN_ELIGIBILITY"
    assert persisted[0]["terminal_reason_code"] == "MARGIN_VALIDATION_UNAVAILABLE"
    assert persisted[0]["decision"] == "OPERATIONAL_FAILURE"
    # GateResult came from evaluate_margin_gate, not a handcrafted dict.
    margin_gate = next(
        gate
        for gate in persisted[0]["gate_results"]
        if gate["gate"] == "MARGIN_ELIGIBILITY"
    )
    assert margin_gate["status"] == "FAIL"
    assert margin_gate["reason_code"] == "MARGIN_VALIDATION_UNAVAILABLE"
    assert margin_gate["reason_class"] == "OPERATIONAL"

    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )
    rendered = render_recent_qualification_funnel(report)

    assert report["margin_error"] == 1
    assert report["margin_reject"] == 0
    assert report["choke_analysis"]["margin_eligibility"]["errors"] == 1
    assert report["choke_analysis"]["margin_eligibility"][
        "rejection_reason_counts"
    ] == {"MARGIN_VALIDATION_UNAVAILABLE": 1}
    assert report["terminal_gate_counts"] == {"MARGIN_ELIGIBILITY": 1}
    assert report["funnel_invariant_holds"] is True
    assert "MARGIN_ERRORS=1" in rendered
    assert "MARGIN_VALIDATION_UNAVAILABLE" in rendered
    assert "PRIMARY_OPERATIONAL_CHOKE=MARGIN_ELIGIBILITY" in rendered


def test_build6_producer_deterministic_policy_reject_through_observer_persistence(
    monkeypatch, tmp_path
):
    """Real prefilter producer → observer persistence → diagnose aggregation.

    Boundary under test:
    chief_analyst._quality_by_risk_level + _prefilter_evidence
      → OPipScanObserver._record_prefilter (production GateResult builder)
      → QualificationFunnel.funnel_events (AdmissionDecision.as_dict)
      → store.append_funnel_events
      → build_recent_qualification_funnel / render_recent_qualification_funnel
    """
    funnel, screening, summaries = _install_funnel_store(monkeypatch, tmp_path)
    candidate = _long_no_economic_snapshot()
    account_equity = 10_000.0
    quality_by_risk_level, viable = _quality_by_risk_level(
        candidate, account_equity
    )
    assert viable is False
    prefilter_row = _prefilter_evidence(candidate, quality_by_risk_level)
    assert prefilter_row["binding_metric"]
    assert prefilter_row["binding_measured"] is not None
    assert prefilter_row["binding_threshold"] is not None
    assert prefilter_row["risk_levels"]

    observer = OPipScanObserver(
        snapshots=[candidate],
        decision_at=NOW,
        account_equity=account_equity,
        telemetry_enabled=True,
    )
    observer.register_candidates([candidate])
    # LONG margin gate is SKIPPED (not applicable); deterministic is terminal.
    observer.record_margin([candidate])
    observer._record_prefilter([prefilter_row])
    observer.finalize(scan_context={}, print_summary=False)

    persisted = store.read_jsonl(funnel)
    assert len(persisted) == 1
    event = persisted[0]
    assert event["first_terminal_gate"] == "DETERMINISTIC_QUALITY"
    assert event["terminal_reason_code"] == "DETERMINISTIC_VIABILITY_FAILED"
    assert event["decision"] == "REJECTED"
    det_gate = next(
        gate
        for gate in event["gate_results"]
        if gate["gate"] == "DETERMINISTIC_QUALITY"
    )
    assert det_gate["status"] == "FAIL"
    assert det_gate["reason_class"] == "POLICY"
    assert det_gate["metadata"]["binding_metric"] == prefilter_row["binding_metric"]
    assert det_gate["measured_value"] == prefilter_row["binding_measured"]
    assert det_gate["threshold"] == prefilter_row["binding_threshold"]
    assert set(det_gate["metadata"]["risk_levels"]) == set(
        prefilter_row["risk_levels"]
    )
    assert det_gate["threshold_distance"] is not None

    report = build_recent_qualification_funnel(
        funnel_events_path=funnel,
        screening_evaluations_path=screening,
        scan_summaries_path=summaries,
        now=NOW,
    )
    rendered = render_recent_qualification_funnel(report)
    deterministic = report["choke_analysis"]["deterministic_viability"]
    sample = deterministic["policy_reject_samples"][0]

    assert report["deterministic_policy_reject_count"] == 1
    assert report["deterministic_operational_error_count"] == 0
    assert deterministic["rejects"] == 1
    assert deterministic["errors"] == 0
    assert sample["binding_metric"] == str(
        prefilter_row["binding_metric"]
    ).upper()
    assert sample["measured_value"] == prefilter_row["binding_measured"]
    assert sample["threshold"] == prefilter_row["binding_threshold"]
    assert set(sample["risk_levels_evaluated"]) == {
        str(level).upper() for level in prefilter_row["risk_levels"]
    }
    expected_gap_pct = round(abs(float(det_gate["threshold_distance"])) * 100.0, 4)
    assert deterministic["nearest_threshold_gap_pct"] == expected_gap_pct
    assert deterministic["median_threshold_gap_pct"] == expected_gap_pct
    assert "DETERMINISTIC_POLICY_REJECT_COUNT=1" in rendered
    assert "DETERMINISTIC_OPERATIONAL_ERROR_COUNT=0" in rendered
    assert sample["binding_metric"] in rendered


def test_build6_producer_deterministic_operational_error_is_not_emitted_by_gate():
    """Document the production boundary for deterministic operational errors.

    ``evaluate_deterministic_quality_gate`` and ``OPipScanObserver._record_prefilter``
    emit POLICY FAIL / PASS only. Shadow-engine exceptions become
    ``FINAL_QUALIFICATION`` ERROR, not ``DETERMINISTIC_QUALITY`` ERROR.
    Aggregation coverage for DETERMINISTIC operational errors therefore remains
    in the synthetic contract tests above; no alternate production GateResult
    producer exists for this gate/status pair.
    """
    from app.opip.decision.gates import evaluate_deterministic_quality_gate
    from app.opip.decision.models import GateStatus, ReasonCode

    candidate = _long_no_economic_snapshot()
    result = evaluate_deterministic_quality_gate(
        candidate, account_equity=10_000.0, evaluated_at=NOW
    )
    assert result.status is GateStatus.FAIL
    assert result.reason_code is ReasonCode.DETERMINISTIC_VIABILITY_FAILED
    assert result.status is not GateStatus.ERROR
