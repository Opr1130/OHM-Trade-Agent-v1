"""Machine-readable and human-readable summary of one instrumented scan.

The operator-facing block this renders is the direct replacement for
``AI top candidates: 0`` as an explanation of a zero-trade scan. Every number
in it comes from the funnel; nothing is hard-coded.
"""

from __future__ import annotations

from collections import Counter
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
    lines.append(f"Rejected by policy: {funnel.get('rejected_by_policy', 0)}")
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
