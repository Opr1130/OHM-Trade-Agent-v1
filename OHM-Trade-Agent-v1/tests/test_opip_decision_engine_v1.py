"""O'Pip Decision Engine, qualification funnel, and read-model behaviour."""

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.decision import store
from app.opip.decision.comparison import build_comparison_telemetry, compare_candidate
from app.opip.decision.engine import CandidateEvidence, OPipDecisionEngine
from app.opip.decision.explanations import build_zero_trade_explanation
from app.opip.decision.funnel import (
    AI_BUDGET_BLOCKED,
    AI_FAILED,
    AI_SUCCEEDED,
    AIStageEvidence,
    QualificationFunnel,
    counts_by_outcome,
    invariant_holds,
)
from app.opip.decision.gates import (
    evaluate_execution_gate,
    evaluate_margin_gate,
    evaluate_recommendation_gate_item,
)
from app.opip.decision.identity import opip_candidate_id, opip_scan_id
from app.opip.decision.models import (
    AdmissionDecision,
    DecisionOutcome,
    GateName,
    GateResult,
    GateStatus,
    ReasonClass,
    ReasonCode,
    normalized_threshold_distance,
    terminal_attribution,
)
from app.opip.decision.observer import OPipScanObserver
from app.opip.decision.summary import build_scan_summary, render_scan_summary_text
from app.opip.decision.thresholds import AI_MIN_CONFIDENCE
from app.opip.decision.versioning import gate_policy_fingerprint, version_stamp
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.models import MarketSnapshot


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def snapshot(**overrides) -> MarketSnapshot:
    values = dict(
        symbol="SOLUSD", last_price=100.0, ema20=99.0, ema50=95.0, ema200=90.0,
        rsi=58.0, macd_line=1.0, macd_signal=0.5, macd_histogram=0.5,
        atr=2.0, atr_pct=2.0, volume_ratio=1.5, technical_score=90, trend="bullish",
        recent_24h_high=110.0, recent_24h_low=94.0,
        recent_72h_high=116.0, recent_72h_low=88.0,
        momentum_6h_pct=1.0, momentum_24h_pct=3.0, momentum_72h_pct=7.0,
        distance_to_24h_high_pct=10.0, distance_to_72h_high_pct=16.0,
        realized_range_24h_pct=16.0, realized_range_72h_pct=28.0,
        average_hourly_range_24h_pct=1.0, average_hourly_range_72h_pct=1.1,
        rolling_24h_range_median_pct=8.0,
        rolling_24h_range_p75_pct=10.0,
        rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0,
        rolling_72h_range_p75_pct=16.0,
        rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=8.0,
        rolling_24h_upside_p75_pct=10.0,
        rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0,
        rolling_72h_upside_p75_pct=16.0,
        rolling_72h_upside_p90_pct=20.0,
        underlying_asset="SOL",
        primary_pair="SOLUSD",
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def execution(status="VALID", **overrides) -> ExecutionValidation:
    values = dict(
        status=status,
        book_coverage_status="COMPLETE",
        warnings=[],
        spread_bps=8.0,
    )
    values.update(overrides)
    return ExecutionValidation(**values)


# ---------------------------------------------------------------- models ---


def test_reason_codes_are_classified_and_classes_are_not_interchangeable():
    from app.opip.decision.models import reason_class

    assert reason_class(ReasonCode.TARGET_ATTAINABILITY_FAILED) is ReasonClass.POLICY
    assert reason_class(ReasonCode.AI_SERVICE_UNAVAILABLE) is ReasonClass.OPERATIONAL
    assert reason_class(ReasonCode.AI_BUDGET_LIMIT) is ReasonClass.BUDGET
    assert (
        reason_class(ReasonCode.AI_CONFIDENCE_BELOW_THRESHOLD) is ReasonClass.MODEL
    )
    # An unmapped code must never be silently reported as a policy decision.
    assert reason_class("SOMETHING_NEW") is ReasonClass.OPERATIONAL


def test_every_reason_code_has_an_explicit_class():
    from app.opip.decision.models import REASON_CLASSES

    assert set(REASON_CLASSES) == set(ReasonCode)


def test_normalized_threshold_distance_is_signed_and_relative():
    assert normalized_threshold_distance(66, 65) == pytest.approx(1 / 65)
    assert normalized_threshold_distance(64, 65) == pytest.approx(-1 / 65)
    assert normalized_threshold_distance(64, 0) is None
    assert normalized_threshold_distance(None, 65) is None


def test_gate_result_serialization_is_deterministic_and_json_safe():
    result = GateResult.build(
        GateName.ECONOMIC_QUALITY,
        GateStatus.FAIL,
        ReasonCode.ECONOMIC_GATE_FAILED,
        reason="net profit below zero",
        measured_value=float("nan"),
        threshold=0.0,
        evaluated_at=NOW,
        metadata={"bad": float("inf"), "good": 1.5, "nested": {"x": "y"}},
    )
    payload = result.as_dict()
    assert payload["measured_value"] is None
    assert "bad" not in payload["metadata"]
    assert payload["metadata"]["good"] == 1.5
    assert payload["reason_class"] == ReasonClass.POLICY.value
    import json

    assert json.dumps(payload, sort_keys=True, allow_nan=False)


def test_terminal_attribution_stops_at_the_first_failing_gate():
    results = [
        GateResult.build(
            GateName.CANDIDATE_CREATED, GateStatus.PASS, ReasonCode.CANDIDATE_ADMITTED
        ),
        GateResult.build(
            GateName.TARGET_QUALITY,
            GateStatus.FAIL,
            ReasonCode.TARGET_ATTAINABILITY_FAILED,
            reason="too close to resistance",
        ),
        GateResult.build(
            GateName.ECONOMIC_QUALITY,
            GateStatus.FAIL,
            ReasonCode.ECONOMIC_GATE_FAILED,
        ),
    ]
    outcome, gate, code, reason = terminal_attribution(results)
    assert outcome is DecisionOutcome.REJECTED
    assert gate is GateName.TARGET_QUALITY
    assert code is ReasonCode.TARGET_ATTAINABILITY_FAILED
    assert reason == "too close to resistance"


def test_operational_stop_is_not_reported_as_a_policy_rejection():
    results = [
        GateResult.build(
            GateName.AI_INVOCATION,
            GateStatus.FAIL,
            ReasonCode.AI_SERVICE_UNAVAILABLE,
            reason="Chief unavailable",
        )
    ]
    outcome, _, _, _ = terminal_attribution(results)
    assert outcome is DecisionOutcome.OPERATIONAL_FAILURE


def test_budget_suppression_is_distinct_from_service_failure():
    budget, _, budget_code, _ = terminal_attribution(
        [
            GateResult.build(
                GateName.AI_INVOCATION,
                GateStatus.FAIL,
                ReasonCode.AI_BUDGET_LIMIT,
            )
        ]
    )
    outage, _, outage_code, _ = terminal_attribution(
        [
            GateResult.build(
                GateName.AI_INVOCATION,
                GateStatus.FAIL,
                ReasonCode.AI_SERVICE_UNAVAILABLE,
            )
        ]
    )
    assert budget_code is not outage_code
    assert budget is DecisionOutcome.REJECTED
    assert outage is DecisionOutcome.OPERATIONAL_FAILURE


# ----------------------------------------------------------------- gates ---


def test_margin_gate_skips_long_and_fails_ineligible_short():
    assert (
        evaluate_margin_gate(snapshot()).status is GateStatus.SKIPPED
    )
    short = snapshot(trade_direction="SHORT", margin_validation_status="INELIGIBLE")
    result = evaluate_margin_gate(short)
    assert result.status is GateStatus.FAIL
    assert result.reason_code is ReasonCode.MARGIN_INELIGIBLE


def test_execution_gate_reproduces_the_invalid_structural_drop():
    candidate = snapshot()
    candidate.execution_validation = execution(status="INVALID")
    result = evaluate_execution_gate(candidate)
    assert result.status is GateStatus.FAIL
    assert result.reason_code is ReasonCode.EXECUTION_VALIDATION_FAILED


def test_execution_gate_never_refreshes_the_margin_book(monkeypatch):
    """A shadow evaluation must not issue an exchange request."""
    import app.scanner.short_execution_quality as short_quality

    def _explode(*args, **kwargs):
        raise AssertionError("shadow evaluation must not touch the exchange")

    monkeypatch.setattr(short_quality, "_refresh_btln_margin_execution", _explode)
    candidate = snapshot(
        trade_direction="SHORT", margin_validation_status="ELIGIBLE"
    )
    candidate.execution_validation = execution(status="VALID")
    result = evaluate_execution_gate(candidate)
    assert result.status is GateStatus.FAIL
    assert result.reason_code is ReasonCode.SHORT_EXECUTION_QUALITY_FAILED


def test_recommendation_gate_separates_every_ai_failure_mode():
    cases = {
        "watch": ReasonCode.AI_DECISION_WATCH,
        "reject": ReasonCode.AI_DECISION_REJECT,
    }
    for decision, code in cases.items():
        result = evaluate_recommendation_gate_item(
            {"decision": decision, "risk_level": "low", "direction": "LONG",
             "confidence": 95}
        )
        assert result.reason_code is code

    low_confidence = evaluate_recommendation_gate_item(
        {"decision": "alert", "risk_level": "low", "direction": "LONG",
         "confidence": AI_MIN_CONFIDENCE - 1}
    )
    assert low_confidence.reason_code is ReasonCode.AI_CONFIDENCE_BELOW_THRESHOLD
    assert low_confidence.threshold == AI_MIN_CONFIDENCE

    bad_risk = evaluate_recommendation_gate_item(
        {"decision": "alert", "risk_level": "high", "direction": "LONG",
         "confidence": 99}
    )
    assert bad_risk.reason_code is ReasonCode.AI_RISK_LEVEL_REJECTED

    bad_direction = evaluate_recommendation_gate_item(
        {"decision": "alert", "risk_level": "low", "direction": "SIDEWAYS",
         "confidence": 99}
    )
    assert bad_direction.reason_code is ReasonCode.AI_DIRECTION_REJECTED

    passing = evaluate_recommendation_gate_item(
        {"decision": "alert", "risk_level": "low", "direction": "LONG",
         "confidence": AI_MIN_CONFIDENCE}
    )
    assert passing.status is GateStatus.PASS


def test_recommendation_gate_matches_the_production_gate_exactly():
    """The shadow gate must agree with recommendation_gate on every input."""
    from app.services.recommendation_gate import qualified_alerts

    items = [
        {"symbol": "A", "decision": decision, "risk_level": risk,
         "direction": direction, "confidence": confidence}
        for decision in ("alert", "watch", "reject", "")
        for risk in ("low", "medium", "high", "")
        for direction in ("LONG", "SHORT", "SIDEWAYS")
        for confidence in (0, AI_MIN_CONFIDENCE - 1, AI_MIN_CONFIDENCE, 100)
    ]
    for item in items:
        production_passes = bool(qualified_alerts({"top_candidates": [dict(item)]}))
        shadow_passes = (
            evaluate_recommendation_gate_item(dict(item)).status is GateStatus.PASS
        )
        assert production_passes is shadow_passes, item


# ---------------------------------------------------------------- funnel ---


def _funnel() -> QualificationFunnel:
    return QualificationFunnel(scan_id="OPIPS:test", decision_at=NOW, cohort_id="C1")


def test_funnel_assigns_a_terminal_state_to_every_candidate():
    funnel = _funnel()
    funnel.register(symbol="AAAUSD", direction="LONG", asset="AAA", pair="AAAUSD")
    funnel.register(symbol="BBBUSD", direction="SHORT", asset="BBB", pair="BBBUSD")
    funnel.register(symbol="CCCUSD", direction="LONG", asset="CCC", pair="CCCUSD")

    funnel.record(
        "AAAUSD", "LONG",
        GateResult.build(
            GateName.FINAL_QUALIFICATION, GateStatus.PASS, ReasonCode.QUALIFIED
        ),
    )
    funnel.record(
        "BBBUSD", "SHORT",
        GateResult.build(
            GateName.ECONOMIC_QUALITY,
            GateStatus.FAIL,
            ReasonCode.ECONOMIC_GATE_FAILED,
        ),
    )
    # CCCUSD is deliberately left unterminated.

    decisions = funnel.decisions()
    counts = counts_by_outcome(decisions)
    assert counts["entered"] == 3
    assert counts["qualified"] == 1
    assert counts["rejected_by_policy"] == 1
    assert counts["incomplete"] == 1
    assert invariant_holds(counts)
    assert {d.decision for d in decisions} == {
        DecisionOutcome.QUALIFIED,
        DecisionOutcome.REJECTED,
        DecisionOutcome.INCOMPLETE,
    }


def test_unterminated_candidate_is_unresolved_not_rejected():
    funnel = _funnel()
    funnel.register(symbol="ZZZUSD", direction="LONG")
    decision = funnel.decisions()[0]
    assert decision.decision is DecisionOutcome.INCOMPLETE
    assert decision.terminal_reason_code is ReasonCode.FUNNEL_INCOMPLETE
    counts = counts_by_outcome([decision])
    assert counts["rejected_by_policy"] == 0
    assert counts["operationally_unresolved"] == 1


def test_recording_the_same_gate_twice_does_not_duplicate_it():
    funnel = _funnel()
    funnel.register(symbol="AAAUSD", direction="LONG")
    for score in (10, 40):
        funnel.record(
            "AAAUSD", "LONG",
            GateResult.build(
                GateName.TARGET_QUALITY,
                GateStatus.FAIL,
                ReasonCode.TARGET_ATTAINABILITY_FAILED,
                measured_value=score,
                threshold=65,
            ),
        )
    state = funnel.get("AAAUSD", "LONG")
    target_results = [
        result for result in state.gate_results
        if result.gate is GateName.TARGET_QUALITY
    ]
    assert len(target_results) == 1
    assert target_results[0].measured_value == 40


def test_gate_results_are_kept_in_canonical_order():
    funnel = _funnel()
    funnel.register(symbol="AAAUSD", direction="LONG")
    funnel.record(
        "AAAUSD", "LONG",
        GateResult.build(
            GateName.ECONOMIC_QUALITY, GateStatus.PASS, ReasonCode.GATE_PASSED
        ),
    )
    funnel.record(
        "AAAUSD", "LONG",
        GateResult.build(
            GateName.MARGIN_ELIGIBILITY, GateStatus.SKIPPED, ReasonCode.GATE_PASSED
        ),
    )
    gates = [result.gate for result in funnel.get("AAAUSD", "LONG").gate_results]
    assert gates.index(GateName.MARGIN_ELIGIBILITY) < gates.index(
        GateName.ECONOMIC_QUALITY
    )


def test_ai_confidence_summary_is_descriptive_not_calibrated():
    stage = AIStageEvidence(invocation_status=AI_SUCCEEDED)
    stage.confidences.extend([70, 80, 90, 95])
    summary = stage.confidence_summary()
    assert summary["count"] == 4
    assert summary["min"] == 70
    assert summary["max"] == 95
    assert summary["median"] == 85.0
    assert summary["calibrated_probability"] is False


def test_ai_stage_states_are_mutually_exclusive():
    budget = AIStageEvidence(invocation_status=AI_BUDGET_BLOCKED)
    failed = AIStageEvidence(invocation_status=AI_FAILED)
    ok = AIStageEvidence(invocation_status=AI_SUCCEEDED)
    assert (budget.budget_exhausted, budget.unavailable, budget.invoked) == (
        True, False, False
    )
    assert (failed.budget_exhausted, failed.unavailable, failed.invoked) == (
        False, True, False
    )
    assert (ok.budget_exhausted, ok.unavailable, ok.invoked) == (False, False, True)


# --------------------------------------------------------------- summary ---


def _rejected_funnel() -> QualificationFunnel:
    funnel = _funnel()
    for symbol, asset, score in (
        ("RAYUSD", "RAY", 64.78),
        ("AAAUSD", "AAA", 40.0),
    ):
        funnel.register(symbol=symbol, direction="LONG", asset=asset, pair=symbol)
        funnel.record(
            symbol, "LONG",
            GateResult.build(
                GateName.DETERMINISTIC_QUALITY,
                GateStatus.FAIL,
                ReasonCode.DETERMINISTIC_VIABILITY_FAILED,
                reason="no risk level clears both gates",
                measured_value=score,
                threshold=65,
            ),
        )
    return funnel


def test_scan_summary_reports_real_counters_and_nearest_miss():
    summary = build_scan_summary(
        _rejected_funnel(),
        scan_context={"analyzed": 42, "technical_candidates": 2},
    )
    assert summary["funnel"]["entered"] == 2
    assert summary["funnel"]["qualified"] == 0
    assert summary["funnel"]["rejected_by_policy"] == 2
    assert summary["invariant_holds"] is True
    assert summary["terminal"]["dominant_terminal_gate"] == "DETERMINISTIC_QUALITY"
    assert summary["ai_stage_reached"] is False
    nearest = summary["nearest_misses"][0]
    assert nearest["asset"] == "RAY"
    assert nearest["distance_from_threshold_pct"] == pytest.approx(
        abs(64.78 - 65) / 65 * 100, abs=1e-4
    )


def test_rendered_summary_contains_only_real_scan_data():
    summary = build_scan_summary(
        _rejected_funnel(),
        scan_context={"analyzed": 42, "technical_candidates": 2},
    )
    text = render_scan_summary_text(summary)
    assert "O'Pip Qualification Summary" in text
    assert "Directional candidates: 2" in text
    assert "Qualified: 0" in text
    assert "Rejected by policy: 2" in text
    assert "Terminal stage: DETERMINISTIC_QUALITY" in text
    assert "AI stage reached: NO" in text
    assert "RAY" in text
    assert "Funnel invariant holds: YES" in text


# ------------------------------------------------------------ comparison ---


def _decision(outcome: DecisionOutcome) -> AdmissionDecision:
    return AdmissionDecision(
        candidate_id="OPIPC:x", episode_id="EP:1", asset="SOL", pair="SOLUSD",
        market_type="SPOT", direction="LONG", decided_at=NOW.isoformat(),
        decision=outcome,
    )


def test_unknown_legacy_outcome_is_not_counted_as_a_match():
    row = compare_candidate(
        candidate_id="OPIPC:x", asset="SOL", pair="SOLUSD", direction="LONG",
        legacy_decision=None, legacy_terminal_reason=None,
        shadow=_decision(DecisionOutcome.REJECTED),
    )
    assert row["comparable"] is False
    telemetry = build_comparison_telemetry([row])
    assert telemetry["exact_matches"] == 0
    assert telemetry["comparable_comparisons"] == 0
    assert telemetry["promotion_ready"] is False


def test_divergences_are_counted_and_named():
    rows = [
        compare_candidate(
            candidate_id="a", asset="A", pair="AUSD", direction="LONG",
            legacy_decision="QUALIFIED", legacy_terminal_reason=None,
            shadow=_decision(DecisionOutcome.QUALIFIED),
        ),
        compare_candidate(
            candidate_id="b", asset="B", pair="BUSD", direction="LONG",
            legacy_decision="QUALIFIED", legacy_terminal_reason=None,
            shadow=_decision(DecisionOutcome.REJECTED),
        ),
    ]
    telemetry = build_comparison_telemetry(rows)
    assert telemetry["total_comparisons"] == 2
    assert telemetry["exact_matches"] == 1
    assert telemetry["divergences"] == 1
    assert telemetry["divergence_rate_pct"] == 50.0
    assert telemetry["opip_engine_authoritative"] is False
    assert telemetry["divergence_reasons"]


# ------------------------------------------------------------------ store ---


def test_store_is_dark_by_default_and_writes_when_enabled(tmp_path, monkeypatch):
    events = tmp_path / "funnel_events.jsonl"
    monkeypatch.delenv("OPIP_FUNNEL_TELEMETRY_ENABLED", raising=False)
    assert store.opip_funnel_telemetry_enabled() is False
    assert store.append_funnel_events([{"scan_id": "s"}], path=events) == 0
    assert not events.exists()

    assert store.append_funnel_events([{"scan_id": "s"}], path=events, enabled=True) == 1
    assert store.read_jsonl(events) == [{"scan_id": "s"}]


def test_truncated_tail_is_isolated_instead_of_corrupting_the_next_row(tmp_path):
    events = tmp_path / "funnel_events.jsonl"
    events.write_text('{"scan_id": "partial"', encoding="utf-8")
    store.append_funnel_events([{"scan_id": "good"}], path=events, enabled=True)
    rows = store.read_jsonl(events)
    assert rows == [{"scan_id": "good"}]


def test_unserialisable_row_is_dead_lettered_without_losing_the_others(tmp_path):
    events = tmp_path / "funnel_events.jsonl"
    dead = tmp_path / "dead.jsonl"
    written = store.append_funnel_events(
        [{"scan_id": "ok"}, {"scan_id": float("nan")}, {"scan_id": "ok2"}],
        path=events,
        dead_letter_path=dead,
        enabled=True,
    )
    assert written == 2
    assert [row["scan_id"] for row in store.read_jsonl(events)] == ["ok", "ok2"]
    assert dead.exists()


def test_malformed_line_does_not_break_the_read_model(tmp_path):
    events = tmp_path / "funnel_events.jsonl"
    events.write_text('{"scan_id": "a"}\nnot json\n{"scan_id": "b"}\n', encoding="utf-8")
    assert [row["scan_id"] for row in store.read_jsonl(events)] == ["a", "b"]


# ------------------------------------------------------------ explanation ---


def test_zero_trade_explanation_reports_disabled_telemetry(tmp_path):
    result = build_zero_trade_explanation(
        summaries_path=tmp_path / "missing.jsonl",
        telemetry_enabled=False,
    )
    assert result["state"] == "TELEMETRY_DISABLED"
    assert result["qualified"] == 0


def test_zero_trade_explanation_attributes_the_dominant_gate(tmp_path):
    summaries = tmp_path / "scan_summaries.jsonl"
    summary = build_scan_summary(
        _rejected_funnel(),
        scan_context={"analyzed": 42, "technical_candidates": 2},
    )
    store.append_scan_summary(summary, path=summaries, enabled=True)

    result = build_zero_trade_explanation(
        summaries_path=summaries,
        telemetry_enabled=True,
        now=NOW + timedelta(minutes=5),
    )
    assert result["state"] == "FRESH"
    assert result["qualified"] == 0
    assert result["directional_candidates"] == 2
    assert result["dominant_terminal_gate"] == "DETERMINISTIC_QUALITY"
    assert result["ai_invoked"] is False
    assert result["ai_unavailable"] is False
    assert result["ai_budget_exhausted"] is False
    assert result["operational_failure_state"] == "NONE"
    assert result["funnel_invariant_holds"] is True
    assert "DETERMINISTIC_QUALITY" in result["explanation"]
    assert result["gate_policy_version"] == version_stamp()["gate_policy_version"]


def test_zero_trade_explanation_reports_staleness(tmp_path):
    summaries = tmp_path / "scan_summaries.jsonl"
    store.append_scan_summary(
        build_scan_summary(_rejected_funnel()), path=summaries, enabled=True
    )
    result = build_zero_trade_explanation(
        summaries_path=summaries,
        telemetry_enabled=True,
        now=NOW + timedelta(hours=6),
    )
    assert result["state"] == "STALE"


def test_zero_trade_explanation_names_ai_outage_and_budget_separately(tmp_path):
    for status, expect_unavailable, expect_budget in (
        (AI_FAILED, True, False),
        (AI_BUDGET_BLOCKED, False, True),
    ):
        summaries = tmp_path / f"summaries_{status}.jsonl"
        funnel = _funnel()
        funnel.ai_stage.invocation_status = status
        funnel.register(symbol="AAAUSD", direction="LONG")
        funnel.record(
            "AAAUSD", "LONG",
            GateResult.build(
                GateName.AI_INVOCATION,
                GateStatus.FAIL,
                ReasonCode.AI_SERVICE_UNAVAILABLE
                if status == AI_FAILED
                else ReasonCode.AI_BUDGET_LIMIT,
            ),
        )
        store.append_scan_summary(
            build_scan_summary(funnel), path=summaries, enabled=True
        )
        result = build_zero_trade_explanation(
            summaries_path=summaries, telemetry_enabled=True, now=NOW
        )
        assert result["ai_unavailable"] is expect_unavailable
        assert result["ai_budget_exhausted"] is expect_budget


# ----------------------------------------------------------- versioning ----


def test_gate_policy_fingerprint_tracks_the_actual_thresholds(monkeypatch):
    baseline = gate_policy_fingerprint()
    import app.services.recommendation_gate as recommendation_gate
    import app.opip.decision.thresholds as thresholds

    monkeypatch.setattr(recommendation_gate, "MIN_CONFIDENCE", 90)
    monkeypatch.setattr(thresholds, "MIN_CONFIDENCE", 90)
    assert gate_policy_fingerprint() != baseline


def test_version_stamp_declares_prepared_but_unset_ml_fields():
    stamp = version_stamp()
    assert stamp["strategy_version"]
    assert stamp["intelligence_version"]
    assert stamp["gate_policy_version"]
    assert "feature_schema_version" in stamp
    assert stamp["feature_schema_version"] is None
    assert stamp["model_version"] is None


# --------------------------------------------------------------- engine ----


def test_engine_stops_at_the_first_failing_gate_without_later_work():
    short = snapshot(trade_direction="SHORT", margin_validation_status="INELIGIBLE")
    engine = OPipDecisionEngine(account_equity=10_000.0, decision_at=NOW)
    decision = engine.evaluate(CandidateEvidence(snapshot=short, episode_id="EP:1"))
    assert decision.decision is DecisionOutcome.REJECTED
    assert decision.first_terminal_gate is GateName.MARGIN_ELIGIBILITY
    assert not any(
        result.gate is GateName.ECONOMIC_QUALITY for result in decision.gate_results
    )


def test_engine_records_counterfactual_eligibility_without_creating_one():
    candidate = snapshot()
    candidate.execution_validation = execution()
    engine = OPipDecisionEngine(account_equity=10_000.0, decision_at=NOW)
    decision = engine.evaluate(
        CandidateEvidence(snapshot=candidate, episode_id="EP:1")
    )
    assert decision.decision is not DecisionOutcome.COUNTERFACTUAL_ELIGIBLE
    assert isinstance(decision.counterfactual_eligible, bool)


def test_engine_reports_budget_and_outage_distinctly():
    candidate = snapshot()
    candidate.execution_validation = execution()
    for status, code in (
        (AI_BUDGET_BLOCKED, ReasonCode.AI_BUDGET_LIMIT),
        (AI_FAILED, ReasonCode.AI_SERVICE_UNAVAILABLE),
    ):
        engine = OPipDecisionEngine(
            account_equity=10_000.0,
            decision_at=NOW,
            ai_stage=AIStageEvidence(invocation_status=status),
        )
        decision = engine.evaluate(
            CandidateEvidence(snapshot=candidate, episode_id="EP:1")
        )
        if decision.first_terminal_gate is GateName.AI_INVOCATION:
            assert decision.terminal_reason_code is code


# -------------------------------------------------------------- observer ---


def test_observer_finalizes_with_a_holding_invariant(capsys):
    candidate = snapshot()
    candidate.execution_validation = execution()
    observer = OPipScanObserver(
        snapshots=[candidate],
        decision_at=NOW,
        account_equity=10_000.0,
        telemetry_enabled=False,
    )
    observer.register_candidates([candidate])
    observer.record_margin([candidate])
    observer.record_execution([candidate])
    observer.record_ai_stage(
        {
            "top_candidates": [],
            "opip_stage_evidence": {
                "invocation_status": "SKIPPED_NO_ELIGIBLE_CANDIDATES",
                "prefiltered": [
                    {
                        "symbol": "SOLUSD",
                        "direction": "LONG",
                        "reason": "low: economic=net profit below zero",
                        "best_target_quality_score": 60.0,
                        "target_qualified_any": False,
                        "economic_qualified_any": False,
                    }
                ],
                "eligible": [],
                "eligible_candidate_count": 0,
                "returned_candidate_count": 0,
            },
        }
    )
    summary = observer.finalize(scan_context={"analyzed": 1})
    assert summary["invariant_holds"] is True
    assert summary["funnel"]["entered"] == 1
    assert summary["funnel"]["rejected_by_policy"] == 1
    assert summary["ai_stage_reached"] is False
    assert summary["shadow_comparison"]["opip_engine_authoritative"] is False
    output = capsys.readouterr().out
    assert "O'PIP QUALIFICATION FUNNEL" in output


class _ExplodingCandidate:
    """A candidate whose evidence access raises, to prove hooks fail soft."""

    symbol = "BOOMUSD"

    @property
    def trade_direction(self):
        raise RuntimeError("evidence unavailable")


def test_observer_degradation_is_reported_not_raised():
    observer = OPipScanObserver(
        snapshots=[], decision_at=NOW, account_equity=None, telemetry_enabled=False
    )
    observer.record_margin([_ExplodingCandidate()])
    summary = observer.finalize(scan_context={})
    assert summary["scan"]["instrumentation_degraded"] is True


def test_scan_id_is_stable_across_recomputation_within_one_scan():
    first = opip_scan_id(cohort_id="COHORT:a", decision_at=NOW)
    second = opip_scan_id(
        cohort_id="COHORT:a", decision_at=NOW.replace(microsecond=999_999)
    )
    assert first == second


def test_candidate_id_is_stable_and_unique_per_direction():
    long_id = opip_candidate_id(episode_id="EP:1", pair="SOLUSD", direction="LONG")
    short_id = opip_candidate_id(episode_id="EP:1", pair="SOLUSD", direction="SHORT")
    assert long_id != short_id
    assert long_id == opip_candidate_id(
        episode_id="EP:1", pair="SOLUSD", direction="long"
    )


# ---------------------------------------------------- binding constraint ----


def test_binding_constraint_names_the_gate_that_actually_stopped_the_candidate():
    """Quoting a comfortably-passing metric would misreport how close it was."""
    from app.services.chief_analyst import binding_deterministic_constraint
    from app.services.economic_quality_gate import MIN_NET_PROFIT
    from app.services.target_attainability import MIN_QUALIFYING_SCORE

    target_failed = binding_deterministic_constraint(
        {
            "low": {
                "target_quality_qualified": False,
                "target_quality_score": 64,
                "economic_qualified": False,
                "hypothetical_target_2_net_profit_at_assumed_capital": 10.0,
            }
        }
    )
    assert target_failed["binding_metric"] == "TARGET_QUALITY_SCORE"
    assert target_failed["binding_measured"] == 64
    assert target_failed["binding_threshold"] == float(MIN_QUALIFYING_SCORE)

    economics_failed = binding_deterministic_constraint(
        {
            "low": {
                "target_quality_qualified": True,
                "target_quality_score": 96,
                "economic_qualified": False,
                "hypothetical_target_2_net_profit_at_assumed_capital": 12.5,
            }
        }
    )
    assert economics_failed["binding_metric"] == "ECONOMIC_NET_PROFIT_AT_TARGET_2"
    assert economics_failed["binding_measured"] == 12.5
    assert economics_failed["binding_threshold"] == float(MIN_NET_PROFIT)


def test_binding_constraint_takes_the_best_risk_level():
    from app.services.chief_analyst import binding_deterministic_constraint

    result = binding_deterministic_constraint(
        {
            "low": {
                "target_quality_qualified": False,
                "target_quality_score": 40,
                "economic_qualified": False,
                "hypothetical_target_2_net_profit_at_assumed_capital": 5.0,
            },
            "medium": {
                "target_quality_qualified": False,
                "target_quality_score": 64,
                "economic_qualified": False,
                "hypothetical_target_2_net_profit_at_assumed_capital": 9.0,
            },
        }
    )
    assert result["binding_measured"] == 64
    assert result["best_economic_net_profit"] == 9.0


def test_economic_gate_defaults_are_the_named_policy_constants():
    """The named constants must stay the gate's actual defaults."""
    import inspect

    from app.services import economic_quality_gate as gate

    signature = inspect.signature(gate.evaluate_economic_quality)
    assert signature.parameters["min_net_profit"].default == gate.MIN_NET_PROFIT
    assert (
        signature.parameters["min_target_2_move_pct"].default
        == gate.MIN_TARGET_2_MOVE_PCT
    )
    assert (
        signature.parameters["min_reward_to_risk"].default == gate.MIN_REWARD_TO_RISK
    )
    # Pin the live values so this build cannot move a production threshold.
    assert gate.MIN_NET_PROFIT == 75.0
    assert gate.MIN_TARGET_2_MOVE_PCT == 4.0
    assert gate.MIN_REWARD_TO_RISK == 2.5
