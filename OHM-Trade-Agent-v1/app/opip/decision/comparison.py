"""Shadow-vs-legacy decision comparison telemetry.

Before the O'Pip Decision Engine can be made authoritative, it must be shown
to reproduce the current production path: 100% explainable equivalence, or
differences that were reviewed and accepted deliberately. This module produces
the evidence for that argument. It does not act on it - Build 1 never promotes
the engine.

A divergence is recorded, never resolved in favour of either side.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

from app.opip.decision.models import AdmissionDecision, DecisionOutcome


#: Legacy outcome vocabulary recorded by the scan integration.
LEGACY_QUALIFIED = "QUALIFIED"
LEGACY_REJECTED = "REJECTED"
LEGACY_OPERATIONAL_FAILURE = "OPERATIONAL_FAILURE"
LEGACY_UNKNOWN = "UNKNOWN"


def normalize_legacy_decision(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {LEGACY_QUALIFIED, LEGACY_REJECTED, LEGACY_OPERATIONAL_FAILURE}:
        return text
    return LEGACY_UNKNOWN


def compare_candidate(
    *,
    candidate_id: str,
    asset: str,
    pair: str,
    direction: str,
    legacy_decision: Any,
    legacy_terminal_reason: str | None,
    shadow: AdmissionDecision,
    legacy_terminal_gate: str | None = None,
    legacy_terminal_reason_code: str | None = None,
) -> dict[str, Any]:
    """Compare one candidate's legacy and shadow outcomes.

    Two agreements are tracked separately. The *outcome* must match for the
    engine to be considered equivalent at all; the *terminal gate* matching as
    well is the stronger claim that both paths stopped the candidate in the
    same place for the same reason. A build that agrees on the verdict but not
    on the attribution is not ready to become authoritative, and reporting only
    the verdict would hide that.

    An unknown legacy outcome is not counted as a match. Instrumentation that
    lost the legacy answer has not demonstrated equivalence, and pretending
    otherwise would inflate the equivalence rate the promotion decision rests
    on.
    """
    legacy = normalize_legacy_decision(legacy_decision)
    shadow_outcome = shadow.decision.value
    comparable = legacy != LEGACY_UNKNOWN
    matched = comparable and legacy == shadow_outcome
    shadow_gate = (
        shadow.first_terminal_gate.value
        if shadow.first_terminal_gate is not None
        else None
    )
    gate_comparable = comparable and legacy_terminal_gate is not None
    gate_matched = gate_comparable and legacy_terminal_gate == shadow_gate
    return {
        "candidate_id": candidate_id,
        "asset": asset,
        "pair": pair,
        "direction": direction,
        "legacy_decision": legacy,
        "legacy_terminal_gate": legacy_terminal_gate,
        "legacy_terminal_reason_code": legacy_terminal_reason_code,
        "legacy_terminal_reason": legacy_terminal_reason,
        "opip_decision": shadow_outcome,
        "opip_terminal_gate": shadow_gate,
        "opip_terminal_reason_code": (
            shadow.terminal_reason_code.value
            if shadow.terminal_reason_code is not None
            else None
        ),
        "opip_terminal_reason": shadow.terminal_reason,
        "comparable": comparable,
        "divergent": comparable and not matched,
        "gate_comparable": gate_comparable,
        "gate_divergent": gate_comparable and not gate_matched,
    }


def build_comparison_telemetry(
    comparisons: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate per-candidate comparisons into promotion-gate telemetry."""
    total = len(comparisons)
    comparable = [row for row in comparisons if row.get("comparable")]
    divergences = [row for row in comparable if row.get("divergent")]
    matches = len(comparable) - len(divergences)
    divergence_reasons = Counter(
        f"{row.get('legacy_decision')}->{row.get('opip_decision')}"
        f"@{row.get('opip_terminal_reason_code') or 'NONE'}"
        for row in divergences
    )
    gate_comparable = [row for row in comparisons if row.get("gate_comparable")]
    gate_divergences = [row for row in gate_comparable if row.get("gate_divergent")]
    gate_divergence_reasons = Counter(
        f"{row.get('legacy_terminal_gate')}->{row.get('opip_terminal_gate')}"
        for row in gate_divergences
    )
    return {
        "total_comparisons": total,
        "comparable_comparisons": len(comparable),
        "exact_matches": matches,
        "divergences": len(divergences),
        "divergence_rate_pct": (
            round(100.0 * len(divergences) / len(comparable), 4) if comparable else None
        ),
        "divergence_reasons": dict(sorted(divergence_reasons.items())),
        "terminal_gate_comparisons": len(gate_comparable),
        "terminal_gate_matches": len(gate_comparable) - len(gate_divergences),
        "terminal_gate_divergences": len(gate_divergences),
        "terminal_gate_divergence_reasons": dict(
            sorted(gate_divergence_reasons.items())
        ),
        "opip_engine_authoritative": False,
        "promotion_ready": (
            bool(comparable) and not divergences and not gate_divergences
        ),
    }


def summarize_shadow_outcomes(
    decisions: Iterable[AdmissionDecision],
) -> dict[str, int]:
    """Return the shadow engine's own outcome distribution."""
    tally = {member.value: 0 for member in DecisionOutcome}
    for decision in decisions:
        tally[decision.decision.value] += 1
    return tally
