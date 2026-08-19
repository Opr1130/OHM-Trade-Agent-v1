from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Iterable


AI_DECISIONS = {
    "AI_WATCH",
    "AI_REJECT",
    "AI_DIRECTION_REJECT",
    "AI_RISK_REJECT",
    "AI_CONFIDENCE_REJECT",
    "AI_NOT_SELECTED",
}


def _payload(row: dict[str, Any]) -> dict[str, Any] | None:
    direct = row.get("target_v2_shadow")
    if isinstance(direct, dict):
        return direct
    intelligence = row.get("market_intelligence")
    if isinstance(intelligence, dict):
        legacy = intelligence.get("target_v2_shadow")
        if isinstance(legacy, dict):
            return legacy
    return None


def _best_move(row: dict[str, Any]) -> float | None:
    values: list[float] = []
    for item in (row.get("observations") or {}).values():
        if isinstance(item, dict) and isinstance(item.get("directional_move_pct"), (int, float)):
            values.append(float(item["directional_move_pct"]))
    return max(values) if values else None


def _segment(row: dict[str, Any]) -> str:
    decision = str(row.get("decision") or "").upper()
    source = str(row.get("source") or "")
    if decision in AI_DECISIONS or source == "chief_ai_review":
        return "AI_NON_ENTRY"
    if decision == "TARGET_REJECT" or source == "target_quality_gate":
        return "TARGET_V1_REJECT"
    if decision in {"ENTER_NOW", "WAIT"} or source == "qualified_profit_rank":
        return "QUALIFIED_SURVIVOR"
    return "OTHER"


def _stats(entries: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    validated = 0
    hits = 0
    scores: list[float] = []
    by_class: dict[str, int] = defaultdict(int)
    for row, payload in entries:
        label = str(payload.get("target_class") or "UNKNOWN")
        by_class[label] += 1
        score = payload.get("confidence_score")
        if isinstance(score, (int, float)):
            scores.append(float(score))
        best = _best_move(row)
        proposed = payload.get("proposed_t1_move_pct")
        if best is not None and isinstance(proposed, (int, float)):
            validated += 1
            if best >= float(proposed):
                hits += 1
    return {
        "samples": len(entries),
        "validated": validated,
        "t1_shadow_hit_rate_pct": round(hits / validated * 100.0, 2) if validated else None,
        "average_confidence_score": round(mean(scores), 2) if scores else None,
        "target_classes": dict(sorted(by_class.items())),
    }


def build_target_v2_coverage_summary(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    all_entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
    segmented: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    by_ai_decision: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)

    for row in records:
        payload = _payload(row)
        if payload is None:
            continue
        entry = (row, payload)
        all_entries.append(entry)
        segment = _segment(row)
        segmented[segment].append(entry)
        decision = str(row.get("decision") or "").upper()
        if segment == "AI_NON_ENTRY":
            by_ai_decision[decision].append(entry)

    return {
        "version": "target-v2-shadow-coverage-v1",
        "status": "OK" if all_entries else "NO_SHADOW_SAMPLES",
        "overall": _stats(all_entries),
        "segments": {name: _stats(rows) for name, rows in sorted(segmented.items())},
        "ai_decisions": {name: _stats(rows) for name, rows in sorted(by_ai_decision.items())},
        "production_target_gate_changed": False,
        "production_decisions_changed": False,
        "automatic_promotion": False,
        "shadow_only": True,
    }
