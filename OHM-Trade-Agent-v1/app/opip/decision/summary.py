"""Machine-readable and human-readable summary of one instrumented scan.

The operator-facing block this renders is the direct replacement for
``AI top candidates: 0`` as an explanation of a zero-trade scan. Every number
in it comes from the funnel; nothing is hard-coded.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.opip.decision.comparison import build_comparison_telemetry
from app.opip.decision.funnel import (
    QualificationFunnel,
    counts_by_outcome,
    invariant_holds,
    reason_class_counts,
)
from app.opip.decision.models import (
    GATE_INDEX,
    AdmissionDecision,
    DecisionOutcome,
    GateName,
    GateStatus,
)
from app.opip.decision.versioning import version_stamp
from app.services.recommendation_gate import parse_confidence


MAX_NEAREST_MISSES = 5
MAX_TOP_REASONS = 6


def _deepest_stage(decisions: Sequence[AdmissionDecision]) -> str | None:
    deepest: GateName | None = None
    for decision in decisions:
        gate = decision.deepest_gate
        if gate is None:
            continue
        if deepest is None or GATE_INDEX.get(gate, -1) > GATE_INDEX.get(deepest, -1):
            deepest = gate
    return deepest.value if deepest is not None else None


def _ai_stage_reached(decisions: Sequence[AdmissionDecision]) -> bool:
    """Whether any candidate actually got as far as a Chief invocation.

    A SKIPPED invocation means no candidate reached the stage, so only a PASS
    or a FAIL counts as having reached it.
    """
    return any(
        result.gate is GateName.AI_INVOCATION
        and result.status in {GateStatus.PASS, GateStatus.FAIL}
        for decision in decisions
        for result in decision.gate_results
    )


def _nearest_misses(decisions: Sequence[AdmissionDecision]) -> list[dict[str, Any]]:
    """Return the rejected candidates closest to clearing their terminal gate.

    Only candidates whose terminal gate produced a comparable measurement and
    threshold can have a distance; the rest are omitted rather than given a
    fabricated one.
    """
    scored: list[tuple[float, dict[str, Any]]] = []
    for decision in decisions:
        if decision.decision is not DecisionOutcome.REJECTED:
            continue
        terminal = next(
            (
                result
                for result in decision.gate_results
                if decision.first_terminal_gate is not None
                and result.gate is decision.first_terminal_gate
            ),
            None,
        )
        if terminal is None or terminal.threshold_distance is None:
            continue
        distance = abs(float(terminal.threshold_distance))
        scored.append(
            (
                distance,
                {
                    "candidate_id": decision.candidate_id,
                    "asset": decision.asset,
                    "asset_display_name": decision.asset_display_name,
                    "pair": decision.pair,
                    "direction": decision.direction,
                    "gate": terminal.gate.value,
                    "reason_code": terminal.reason_code.value,
                    "measured_value": terminal.measured_value,
                    "threshold": terminal.threshold,
                    "distance_from_threshold_pct": round(distance * 100.0, 4),
                },
            )
        )
    scored.sort(key=lambda item: item[0])
    return [row for _, row in scored[:MAX_NEAREST_MISSES]]


def build_scan_summary(
    funnel: QualificationFunnel,
    *,
    decisions: Sequence[AdmissionDecision] | None = None,
    comparisons: Sequence[Mapping[str, Any]] = (),
    scan_context: Mapping[str, Any] | None = None,
    paper_admission_eligible: int = 0,
) -> dict[str, Any]:
    """Return the persistable, machine-readable summary of one scan."""
    resolved = list(decisions if decisions is not None else funnel.decisions())
    counts = counts_by_outcome(resolved)
    non_qualified = [
        decision
        for decision in resolved
        if decision.decision is not DecisionOutcome.QUALIFIED
    ]
    terminal_gates = Counter(
        decision.first_terminal_gate.value
        for decision in non_qualified
        if decision.first_terminal_gate is not None
    )
    terminal_reasons = Counter(
        decision.terminal_reason_code.value
        for decision in non_qualified
        if decision.terminal_reason_code is not None
    )
    directions = Counter(decision.direction for decision in resolved)

    summary: dict[str, Any] = {
        "record_type": "OPIP_SCAN_SUMMARY",
        "scan_id": funnel.scan_id,
        "cohort_id": funnel.cohort_id,
        "decision_at_utc": funnel.decision_at_iso,
        "scan": {
            "long_candidates": directions.get("LONG", 0),
            "short_candidates": directions.get("SHORT", 0),
            **{str(key): value for key, value in (scan_context or {}).items()},
        },
        "funnel": counts,
        "invariant_holds": invariant_holds(counts),
        "terminal": {
            "deepest_stage_reached": _deepest_stage(resolved),
            "dominant_terminal_gate": (
                terminal_gates.most_common(1)[0][0] if terminal_gates else None
            ),
            "terminal_gates": dict(sorted(terminal_gates.items())),
            "top_reasons": dict(terminal_reasons.most_common(MAX_TOP_REASONS)),
            "reason_classes": reason_class_counts(resolved),
        },
        "nearest_misses": _nearest_misses(resolved),
        "ai_stage": funnel.ai_stage.as_dict(),
        "ai_stage_reached": _ai_stage_reached(resolved),
        "paper_admission_eligible": int(paper_admission_eligible),
        "shadow_comparison": build_comparison_telemetry(list(comparisons)),
    }
    summary.update(version_stamp())
    return summary


def render_scan_summary_text(summary: Mapping[str, Any]) -> str:
    """Render the operator-facing O'Pip qualification summary.

    Every value is read from ``summary``; there is no example data here.
    """
    funnel = summary.get("funnel") or {}
    terminal = summary.get("terminal") or {}
    ai_stage = summary.get("ai_stage") or {}
    scan = summary.get("scan") or {}

    lines: list[str] = ["O'Pip Qualification Summary", ""]
    lines.append(f"Directional candidates: {funnel.get('entered', 0)}")
    lines.append(f"Qualified: {funnel.get('qualified', 0)}")
    lines.append(f"Rejected total: {funnel.get('rejected_total', 0)}")
    lines.append(f"Rejected by policy: {funnel.get('rejected_by_policy', 0)}")
    lines.append(f"Budget suppressions: {funnel.get('rejected_by_budget', 0)}")
    lines.append(f"Model stops: {funnel.get('rejected_by_model', 0)}")
    if funnel.get("rejected_other"):
        lines.append(f"Other rejected: {funnel.get('rejected_other', 0)}")
    lines.append(f"Operational failures: {funnel.get('operational_failures', 0)}")
    if funnel.get("incomplete"):
        lines.append(f"Incomplete (unattributed): {funnel.get('incomplete', 0)}")
    lines.append(
        f"Directional split: LONG={scan.get('long_candidates', 0)} "
        f"SHORT={scan.get('short_candidates', 0)}"
    )
    lines.append("")
    lines.append(f"Terminal stage: {terminal.get('dominant_terminal_gate') or 'NONE'}")
    lines.append(f"Deepest stage reached: {terminal.get('deepest_stage_reached') or 'NONE'}")

    top_reasons = terminal.get("top_reasons") or {}
    if top_reasons:
        lines.append("")
        lines.append("Top reasons:")
        for code, count in top_reasons.items():
            lines.append(f"  {code}: {count}")

    lines.append("")
    lines.append(
        "AI stage reached: " + ("YES" if summary.get("ai_stage_reached") else "NO")
    )
    lines.append(f"AI invocation status: {ai_stage.get('invocation_status', 'UNKNOWN')}")
    if ai_stage.get("failure_type"):
        lines.append(f"AI failure type: {ai_stage['failure_type']}")
    lines.append(
        "AI eligible candidates before review: "
        f"{ai_stage.get('eligible_candidates_before_ai', 0)}"
    )
    lines.append(
        f"AI candidates returned: {ai_stage.get('candidates_returned_by_ai', 0)}"
    )

    nearest = summary.get("nearest_misses") or []
    if nearest:
        lines.append("")
        lines.append("Nearest candidate:")
        first = nearest[0]
        label = first.get("asset_display_name") or first.get("asset")
        lines.append(
            f"  {label} — {first.get('pair')} {first.get('direction')}"
        )
        lines.append(f"  Gate: {first.get('gate')}")
        lines.append(
            "  Distance from threshold: "
            f"{first.get('distance_from_threshold_pct')}%"
        )

    comparison = summary.get("shadow_comparison") or {}
    lines.append("")
    lines.append(
        "Shadow comparison: "
        f"comparisons={comparison.get('total_comparisons', 0)} "
        f"matches={comparison.get('exact_matches', 0)} "
        f"divergences={comparison.get('divergences', 0)} "
        f"authoritative={comparison.get('opip_engine_authoritative', False)}"
    )
    lines.append(
        f"Funnel invariant holds: {'YES' if summary.get('invariant_holds') else 'NO'}"
    )
    return "\n".join(lines)


def _parse_utc_timestamp(value: Any) -> datetime | None:

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _first_terminal_gate_from_row(row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    explicit = str(row.get("first_terminal_gate") or "").strip()
    explicit_class = str(row.get("terminal_reason_class") or "").strip()
    if explicit and explicit_class:
        return explicit, explicit_class

    gate_results = row.get("gate_results") or []
    if not isinstance(gate_results, list):
        return explicit or None, explicit_class or None

    if explicit:
        for gate in gate_results:
            if not isinstance(gate, Mapping):
                continue
            if str(gate.get("gate") or "") != explicit:
                continue
            if str(gate.get("status") or "") not in {"FAIL", "ERROR"}:
                continue
            return (
                explicit,
                str(gate.get("reason_class") or "") or None,
            )
        return explicit, None

    for gate in gate_results:
        if not isinstance(gate, Mapping):
            continue
        if str(gate.get("status") or "") in {"FAIL", "ERROR"}:
            return (
                str(gate.get("gate") or "") or None,
                str(gate.get("reason_class") or "") or None,
            )
    return None, None


def _finite_float(value: Any) -> float | None:
    """Return a finite float for diagnostics, otherwise no measurement."""
    if isinstance(value, bool):
        return None
    try:
        resolved = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if resolved != resolved or resolved in {float("inf"), float("-inf")}:
        return None
    return resolved


def build_recent_qualification_funnel(
    *,
    funnel_events_path=None,
    screening_evaluations_path=None,
    scan_summaries_path=None,
    now=None,
    window_hours: int = 24,
) -> dict[str, Any]:
    """Aggregate persisted O'Pip evidence into a recent qualification funnel.

    Read-only diagnostics only. It does not feed any ranking, qualification,
    alert, paper-admission, or exchange path.
    """
    from app.opip.decision.store import (
        FUNNEL_EVENTS_FILE,
        SCAN_SUMMARIES_FILE,
        SCREENING_EVALUATIONS_FILE,
        read_jsonl,
    )

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)
    effective_window_hours = max(1, int(window_hours))
    cutoff = current - timedelta(hours=effective_window_hours)

    def recent(rows):
        result = []
        for row in rows:
            observed = _parse_utc_timestamp(
                row.get("decision_at_utc") or row.get("observed_at")
            )
            if observed is not None and observed >= cutoff:
                result.append(row)
        return result

    events = recent(read_jsonl(funnel_events_path or FUNNEL_EVENTS_FILE))
    screenings = recent(
        read_jsonl(screening_evaluations_path or SCREENING_EVALUATIONS_FILE)
    )
    summaries = recent(read_jsonl(scan_summaries_path or SCAN_SUMMARIES_FILE))

    broad_screenings = [
        row
        for row in screenings
        if str(row.get("scanner_type") or "BROAD_SEARCH").upper()
        == "BROAD_SEARCH"
    ]
    early_watch_screenings = [
        row
        for row in screenings
        if str(row.get("scanner_type") or "").upper() == "EARLY_WATCH"
    ]

    counts = Counter()
    rejections = Counter()
    terminal_gates = Counter()
    policy_terminal_gates = Counter()
    operational_terminal_gates = Counter()
    margin_rejection_statuses = Counter()
    margin_rejection_reasons = Counter()
    deterministic_binding_metrics = Counter()
    deterministic_risk_levels = Counter()
    deterministic_threshold_distances: list[float] = []
    # Emit a stable zero-valued schema even when a stage has no observations.
    # This keeps diagnostics machine-readable and avoids absence being confused
    # with "not instrumented" or an aggregation error.
    for key in (
        "market_observed",
        "scanner_selected",
        "broad_search_observed",
        "broad_search_threshold_advanced",
        "broad_search_below_threshold",
        "early_watch_observed",
        "early_watch_advanced",
        "funnel_candidates",
        "funnel_qualified",
        "funnel_rejected",
        "funnel_operational_failure",
        "funnel_incomplete",
        "margin_pass",
        "margin_reject",
        "margin_error",
        "execution_pass",
        "execution_reject",
        "execution_error",
        "cross_market_pass",
        "cross_market_skipped",
        "reference_pass",
        "reference_skipped",
        "market_intelligence_pass",
        "market_intelligence_skipped",
        "deterministic_prefilter_pass",
        "deterministic_prefilter_reject",
        "deterministic_prefilter_error",
        "chief_eligible",
        "chief_invoked",
        "chief_succeeded",
        "chief_failed",
        "chief_budget_blocked",
        "chief_cache_reused",
        "chief_alert",
        "chief_watch",
        "chief_reject",
        "confidence_ge_85",
        "confidence_80_84",
        "confidence_70_79",
        "confidence_lt_70",
        "target_pass",
        "target_reject",
        "economic_pass",
        "economic_reject",
        "action_gate_pass",
        "action_gate_reject",
        "action_gate_error",
        "qualified_signals",
        "paper_admission_eligible",
    ):
        counts[key] = 0

    counts["broad_search_observed"] = len(broad_screenings)
    counts["broad_search_threshold_advanced"] = sum(
        1
        for row in broad_screenings
        if str(row.get("outcome") or "") == "ADVANCED"
    )
    counts["broad_search_below_threshold"] = sum(
        1
        for row in broad_screenings
        if str(row.get("outcome") or "") == "BELOW_THRESHOLD"
    )
    counts["early_watch_observed"] = len(early_watch_screenings)
    counts["early_watch_advanced"] = sum(
        1
        for row in early_watch_screenings
        if str(row.get("outcome") or "") == "ADVANCED"
    )
    # Backward-compatible aliases now explicitly mean Broad Search only.
    counts["market_observed"] = counts["broad_search_observed"]
    counts["scanner_selected"] = counts["broad_search_threshold_advanced"]

    counts["funnel_candidates"] = len(events)

    for row in events:
        decision = str(row.get("decision") or "")
        if decision == "QUALIFIED":
            counts["funnel_qualified"] += 1
        elif decision == "REJECTED":
            counts["funnel_rejected"] += 1
        elif decision == "OPERATIONAL_FAILURE":
            counts["funnel_operational_failure"] += 1
        elif decision == "INCOMPLETE":
            counts["funnel_incomplete"] += 1

        first_terminal_gate, terminal_class = _first_terminal_gate_from_row(row)
        if first_terminal_gate:
            terminal_gates[first_terminal_gate] += 1
            if terminal_class == "POLICY":
                policy_terminal_gates[first_terminal_gate] += 1
            elif terminal_class == "OPERATIONAL":
                operational_terminal_gates[first_terminal_gate] += 1

        decision = str(row.get("decision") or "")
        if decision == "QUALIFIED":
            counts["qualified_signals"] += 1
        reason = str(row.get("terminal_reason_code") or "")
        if decision != "QUALIFIED" and reason:
            rejections[reason] += 1

        gate_results = row.get("gate_results") or []
        if not isinstance(gate_results, list):
            continue
        for gate in gate_results:
            if not isinstance(gate, Mapping):
                continue
            name = str(gate.get("gate") or "")
            status = str(gate.get("status") or "")
            reason_code = str(gate.get("reason_code") or "")
            metadata = gate.get("metadata") or {}
            if not isinstance(metadata, Mapping):
                metadata = {}

            if name == "MARGIN_ELIGIBILITY":
                if status == "PASS":
                    counts["margin_pass"] += 1
                elif status == "FAIL":
                    counts["margin_reject"] += 1
                elif status == "ERROR":
                    counts["margin_error"] += 1
                if status in {"FAIL", "ERROR"}:
                    margin_status = str(
                        metadata.get("margin_validation_status")
                        or reason_code
                        or "UNKNOWN"
                    ).upper()
                    margin_rejection_statuses[margin_status] += 1
                    margin_rejection_reasons[reason_code or "UNKNOWN"] += 1
            elif name == "EXECUTION_VALIDATION":
                if status == "PASS":
                    counts["execution_pass"] += 1
                elif status == "FAIL":
                    counts["execution_reject"] += 1
                elif status == "ERROR":
                    counts["execution_error"] += 1
            elif name == "CROSS_MARKET_CONFIRMATION":
                if status == "PASS":
                    counts["cross_market_pass"] += 1
                elif status == "SKIPPED":
                    counts["cross_market_skipped"] += 1
            elif name == "REFERENCE_VALIDATION":
                if status == "PASS":
                    counts["reference_pass"] += 1
                elif status == "SKIPPED":
                    counts["reference_skipped"] += 1
            elif name == "MARKET_INTELLIGENCE":
                if status == "PASS":
                    counts["market_intelligence_pass"] += 1
                elif status == "SKIPPED":
                    counts["market_intelligence_skipped"] += 1
            elif name == "DETERMINISTIC_QUALITY":
                if status == "PASS":
                    counts["deterministic_prefilter_pass"] += 1
                elif status == "FAIL":
                    counts["deterministic_prefilter_reject"] += 1
                elif status == "ERROR":
                    counts["deterministic_prefilter_error"] += 1
                if status in {"FAIL", "ERROR"}:
                    binding_metric = str(
                        metadata.get("binding_metric") or "UNKNOWN"
                    ).upper()
                    deterministic_binding_metrics[binding_metric] += 1
                    persisted_risk_levels = metadata.get("risk_levels")
                    if isinstance(persisted_risk_levels, Mapping):
                        risk_level_names = persisted_risk_levels.keys()
                    else:
                        risk_level_names = (
                            metadata.get("risk_levels_evaluated") or []
                        )
                    if isinstance(
                        risk_level_names,
                        (list, tuple, set, type({}.keys())),
                    ):
                        for risk_level in risk_level_names:
                            label = str(risk_level or "").strip().upper()
                            if label:
                                deterministic_risk_levels[label] += 1
                    distance = _finite_float(gate.get("threshold_distance"))
                    if distance is not None:
                        deterministic_threshold_distances.append(abs(distance))
            elif name == "AI_ELIGIBILITY" and status == "PASS":
                counts["chief_eligible"] += 1
            elif name == "AI_INVOCATION":
                if status == "PASS":
                    counts["chief_invoked"] += 1
                    invocation = str(metadata.get("invocation_status") or "")
                    if invocation in {"SUCCEEDED", "CACHE_REUSED"}:
                        counts["chief_succeeded"] += 1
                    if invocation == "CACHE_REUSED":
                        counts["chief_cache_reused"] += 1
                elif reason_code == "AI_BUDGET_LIMIT":
                    counts["chief_budget_blocked"] += 1
                elif status in {"FAIL", "ERROR"}:
                    counts["chief_failed"] += 1
            elif name == "RECOMMENDATION_GATE":
                ai_decision = str(metadata.get("ai_decision") or "").lower()
                if ai_decision == "alert":
                    counts["chief_alert"] += 1
                elif ai_decision == "watch":
                    counts["chief_watch"] += 1
                elif ai_decision:
                    counts["chief_reject"] += 1
                confidence_value = parse_confidence(
                    {"confidence": metadata.get("ai_confidence")}
                )
                if confidence_value is not None:
                    if confidence_value >= 85:
                        counts["confidence_ge_85"] += 1
                    elif confidence_value >= 80:
                        counts["confidence_80_84"] += 1
                    elif confidence_value >= 70:
                        counts["confidence_70_79"] += 1
                    else:
                        counts["confidence_lt_70"] += 1
            elif name == "TARGET_QUALITY":
                if status == "PASS":
                    counts["target_pass"] += 1
                elif status == "FAIL":
                    counts["target_reject"] += 1
            elif name == "ECONOMIC_QUALITY":
                if status == "PASS":
                    counts["economic_pass"] += 1
                elif status == "FAIL":
                    counts["economic_reject"] += 1
            elif name == "CAPITAL_PORTFOLIO_GATE":
                if status == "PASS":
                    counts["action_gate_pass"] += 1
                elif status == "FAIL":
                    counts["action_gate_reject"] += 1
                elif status == "ERROR":
                    counts["action_gate_error"] += 1

    def _paper_admission_value(row: Mapping[str, Any]) -> int:
        raw = row.get("paper_admission_eligible")
        if isinstance(raw, bool):
            return 0
        if isinstance(raw, float) and not raw.is_integer():
            return 0
        try:
            value = int(raw or 0)
        except (TypeError, ValueError, OverflowError):
            return 0
        return value if value >= 0 else 0

    counts["paper_admission_eligible"] = sum(
        _paper_admission_value(row) for row in summaries
    )

    def _dominant(counter: Counter) -> str:
        return counter.most_common(1)[0][0] if counter else "NONE"

    primary_choke = _dominant(terminal_gates)
    primary_policy_choke = _dominant(policy_terminal_gates)
    primary_operational_choke = _dominant(operational_terminal_gates)
    funnel_invariant_holds = counts["funnel_candidates"] == (
        counts["funnel_qualified"]
        + counts["funnel_rejected"]
        + counts["funnel_operational_failure"]
        + counts["funnel_incomplete"]
    )

    deterministic_threshold_distances.sort()
    deterministic_nearest_gap_pct = (
        round(deterministic_threshold_distances[0] * 100.0, 4)
        if deterministic_threshold_distances
        else None
    )
    deterministic_median_gap_pct = None
    if deterministic_threshold_distances:
        midpoint = len(deterministic_threshold_distances) // 2
        if len(deterministic_threshold_distances) % 2:
            median_distance = deterministic_threshold_distances[midpoint]
        else:
            median_distance = (
                deterministic_threshold_distances[midpoint - 1]
                + deterministic_threshold_distances[midpoint]
            ) / 2.0
        deterministic_median_gap_pct = round(median_distance * 100.0, 4)

    return {
        "window_hours": effective_window_hours,
        "generated_at_utc": current.isoformat(),
        **dict(counts),
        "trade_quality_pass": "NOT_INSTRUMENTED",
        "trade_quality_reject": "NOT_INSTRUMENTED",
        "capacity_pass": "NOT_INSTRUMENTED",
        "capacity_reject": "NOT_INSTRUMENTED",
        "paper_admitted": "NOT_INSTRUMENTED",
        "primary_choke": primary_choke,
        "primary_policy_choke": primary_policy_choke,
        "primary_operational_choke": primary_operational_choke,
        "terminal_gate_counts": dict(terminal_gates.most_common()),
        "policy_terminal_gate_counts": dict(policy_terminal_gates.most_common()),
        "operational_terminal_gate_counts": dict(
            operational_terminal_gates.most_common()
        ),
        "funnel_invariant_holds": funnel_invariant_holds,
        "top_rejection_reasons": dict(rejections.most_common(10)),
        "choke_analysis": {
            "margin_eligibility": {
                "rejects": counts["margin_reject"],
                "errors": counts["margin_error"],
                "rejection_status_counts": dict(
                    margin_rejection_statuses.most_common()
                ),
                "rejection_reason_counts": dict(
                    margin_rejection_reasons.most_common()
                ),
            },
            "deterministic_viability": {
                "rejects": counts["deterministic_prefilter_reject"],
                "errors": counts["deterministic_prefilter_error"],
                "binding_metric_counts": dict(
                    deterministic_binding_metrics.most_common()
                ),
                "risk_level_evaluation_counts": dict(
                    deterministic_risk_levels.most_common()
                ),
                "threshold_distance_samples": len(
                    deterministic_threshold_distances
                ),
                "nearest_threshold_gap_pct": deterministic_nearest_gap_pct,
                "median_threshold_gap_pct": deterministic_median_gap_pct,
            },
            "measurement_only": True,
            "policy_change_authorized": False,
        },
        "measurement_only": True,
        "affects_trade_authority": False,
    }


def render_recent_qualification_funnel(report: Mapping[str, Any]) -> str:
    """Render a compact diagnostics block for /diagnose-learning."""
    keys = (
        "market_observed",
        "scanner_selected",
        "broad_search_observed",
        "broad_search_threshold_advanced",
        "broad_search_below_threshold",
        "early_watch_observed",
        "early_watch_advanced",
        "funnel_candidates",
        "funnel_qualified",
        "funnel_rejected",
        "funnel_operational_failure",
        "funnel_incomplete",
        "margin_pass",
        "margin_reject",
        "margin_error",
        "execution_pass",
        "execution_reject",
        "execution_error",
        "cross_market_pass",
        "cross_market_skipped",
        "reference_pass",
        "reference_skipped",
        "market_intelligence_pass",
        "market_intelligence_skipped",
        "deterministic_prefilter_pass",
        "deterministic_prefilter_reject",
        "deterministic_prefilter_error",
        "chief_eligible",
        "chief_invoked",
        "chief_succeeded",
        "chief_failed",
        "chief_budget_blocked",
        "chief_cache_reused",
        "chief_alert",
        "chief_watch",
        "chief_reject",
        "confidence_ge_85",
        "confidence_80_84",
        "confidence_70_79",
        "confidence_lt_70",
        "target_pass",
        "target_reject",
        "economic_pass",
        "economic_reject",
        "trade_quality_pass",
        "trade_quality_reject",
        "capacity_pass",
        "capacity_reject",
        "action_gate_pass",
        "action_gate_reject",
        "action_gate_error",
        "qualified_signals",
        "paper_admission_eligible",
        "paper_admitted",
    )
    lines = ["OPIP_QUALIFICATION_FUNNEL"]
    for key in keys:
        lines.append(f"{key}={report.get(key, 0)}")
    lines.append(f"PRIMARY_CHOKE={report.get('primary_choke', 'NONE')}")
    lines.append(
        f"PRIMARY_POLICY_CHOKE={report.get('primary_policy_choke', 'NONE')}"
    )
    lines.append(
        "PRIMARY_OPERATIONAL_CHOKE="
        f"{report.get('primary_operational_choke', 'NONE')}"
    )
    choke = report.get("choke_analysis") or {}
    margin = choke.get("margin_eligibility") or {}
    deterministic = choke.get("deterministic_viability") or {}
    lines.append("MARGIN_CHOKE_DETAIL")
    margin_statuses = margin.get("rejection_status_counts") or {}
    if margin_statuses:
        for status_name, count in margin_statuses.items():
            lines.append(f"{status_name}={count}")
    else:
        lines.append("NONE=0")
    lines.append("DETERMINISTIC_CHOKE_DETAIL")
    lines.append(
        "DETERMINISTIC_REJECTS="
        f"{deterministic.get('rejects', 0)}"
    )
    lines.append(
        "DETERMINISTIC_ERRORS="
        f"{deterministic.get('errors', 0)}"
    )
    binding_metrics = deterministic.get("binding_metric_counts") or {}
    if binding_metrics:
        for metric, count in binding_metrics.items():
            lines.append(f"{metric}={count}")
    else:
        lines.append("NONE=0")
    lines.append(
        "DETERMINISTIC_NEAREST_THRESHOLD_GAP_PCT="
        f"{deterministic.get('nearest_threshold_gap_pct')}"
    )
    lines.append(
        "DETERMINISTIC_MEDIAN_THRESHOLD_GAP_PCT="
        f"{deterministic.get('median_threshold_gap_pct')}"
    )
    lines.append(
        "CHOKE_ANALYSIS_POLICY_CHANGE_AUTHORIZED="
        f"{'YES' if choke.get('policy_change_authorized') else 'NO'}"
    )
    lines.append(
        "FUNNEL_INVARIANT_HOLDS="
        f"{'YES' if report.get('funnel_invariant_holds') else 'NO'}"
    )
    lines.append("TOP_REJECTION_REASONS")
    reasons = report.get("top_rejection_reasons") or {}
    if reasons:
        for reason, count in reasons.items():
            lines.append(f"{reason}={count}")
    else:
        lines.append("NONE=0")
    return "\n".join(lines)
