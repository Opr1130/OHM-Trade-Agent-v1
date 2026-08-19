from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean
from typing import Any, Iterable

from app.services.decision_learning import (
    AVOIDED_LOSS_THRESHOLD_PCT,
    MISS_THRESHOLD_PCT,
    classify_shadow_decision,
    is_non_entry_decision,
)


SAFETY_DECISIONS = {"MARGIN_REJECT", "EXECUTION_REJECT", "ECONOMIC_REJECT"}
TARGET_DECISIONS = {"TARGET_REJECT"}
AI_DECISIONS = {
    "AI_REJECT",
    "AI_WATCH",
    "AI_CONFIDENCE_REJECT",
    "AI_RISK_REJECT",
    "AI_NOT_SELECTED",
}


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _moves(row: dict[str, Any]) -> dict[str, float]:
    observations = row.get("observations") or {}
    result: dict[str, float] = {}
    for horizon, payload in observations.items():
        if not isinstance(payload, dict):
            continue
        move = _num(payload.get("directional_move_pct"))
        if move is not None:
            result[str(horizon)] = move
    return result


def _gate_family(decision: str) -> str:
    if decision in SAFETY_DECISIONS:
        return "SAFETY"
    if decision in TARGET_DECISIONS:
        return "TARGET"
    if decision in AI_DECISIONS:
        return "AI"
    if decision == "WAIT":
        return "TIMING"
    if decision.endswith("_REJECT") or decision in {"REJECT", "NO_TRADE"}:
        return "OTHER_GATE"
    return "NON_REJECTION"


def _timing_shape(moves: dict[str, float]) -> tuple[bool, str | None]:
    if not moves:
        return False, None
    ordered = [h for h in ("5m", "15m", "30m", "1h", "4h", "24h") if h in moves]
    if len(ordered) < 2:
        return False, None
    first = ordered[0]
    later = ordered[1:]
    first_move = moves[first]
    later_best = max(moves[h] for h in later)
    return first_move <= 0 and later_best >= MISS_THRESHOLD_PCT, first


def classify_counterfactual_rejection(row: dict[str, Any]) -> dict[str, Any]:
    decision = str(row.get("decision") or "UNKNOWN").upper()
    if not is_non_entry_decision(decision):
        return {
            "status": "NOT_APPLICABLE",
            "decision": decision,
            "gate_family": _gate_family(decision),
            "classification": "NON_REJECTION",
            "heuristic_only": True,
        }

    base = classify_shadow_decision(row)
    if base.get("status") != "EVALUATED":
        return {
            "status": "PENDING",
            "decision": decision,
            "gate_family": _gate_family(decision),
            "classification": "PENDING",
            "heuristic_only": True,
        }

    best = _num(base.get("best_move_pct"))
    worst = _num(base.get("worst_move_pct"))
    moves = _moves(row)
    timing_reversal, initial_horizon = _timing_shape(moves)
    gate_family = _gate_family(decision)

    classification = "NEUTRAL_REJECT"
    rationale = "Future sampled path did not clearly validate or invalidate the rejection."

    if gate_family == "SAFETY" and best is not None and best >= MISS_THRESHOLD_PCT:
        classification = "SAFETY_REJECT_WITH_LATER_UPSIDE"
        rationale = "Price later moved favorably, but the original safety/economic gate remains authoritative."
    elif timing_reversal:
        classification = "RIGHT_IDEA_WRONG_ENTRY"
        rationale = f"Initial {initial_horizon} move was non-positive while a later sampled horizon exceeded the miss threshold."
    elif gate_family == "TARGET" and best is not None and 0 < best < MISS_THRESHOLD_PCT and (worst is None or worst > AVOIDED_LOSS_THRESHOLD_PCT):
        classification = "TARGET_TOO_AMBITIOUS_HEURISTIC"
        rationale = "The path was favorable but did not reach the audit's profitability threshold; target design may have been too ambitious."
    elif best is not None and best >= MISS_THRESHOLD_PCT and (worst is None or worst > AVOIDED_LOSS_THRESHOLD_PCT):
        classification = "FALSE_REJECT_HEURISTIC"
        rationale = "Later sampled path showed material favorable movement without a sampled avoided-loss event."
    elif best is not None and best < MISS_THRESHOLD_PCT and worst is not None and worst <= AVOIDED_LOSS_THRESHOLD_PCT:
        classification = "GOOD_REJECT"
        rationale = "The rejection avoided a materially adverse sampled path without missing material upside."

    return {
        "status": "EVALUATED",
        "decision": decision,
        "gate_family": gate_family,
        "classification": classification,
        "best_move_pct": best,
        "worst_move_pct": worst,
        "miss_threshold_pct": MISS_THRESHOLD_PCT,
        "avoided_loss_threshold_pct": AVOIDED_LOSS_THRESHOLD_PCT,
        "rationale": rationale,
        "heuristic_only": True,
        "does_not_prove_executability": True,
    }


def build_counterfactual_gate_audit(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    evaluated: list[dict[str, Any]] = []
    pending = 0
    by_decision: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    classifications = Counter()

    for row in records:
        result = classify_counterfactual_rejection(row)
        if result["status"] == "PENDING":
            pending += 1
            continue
        if result["status"] != "EVALUATED":
            continue
        evaluated.append(result)
        by_decision[result["decision"]].append(result)
        by_family[result["gate_family"]].append(result)
        classifications[result["classification"]] += 1

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        false_like = sum(r["classification"] in {"FALSE_REJECT_HEURISTIC", "RIGHT_IDEA_WRONG_ENTRY", "TARGET_TOO_AMBITIOUS_HEURISTIC"} for r in rows)
        safety_upside = sum(r["classification"] == "SAFETY_REJECT_WITH_LATER_UPSIDE" for r in rows)
        good = sum(r["classification"] == "GOOD_REJECT" for r in rows)
        bests = [float(r["best_move_pct"]) for r in rows if r.get("best_move_pct") is not None]
        worsts = [float(r["worst_move_pct"]) for r in rows if r.get("worst_move_pct") is not None]
        return {
            "samples": len(rows),
            "false_reject_like": false_like,
            "false_reject_like_rate_pct": round(false_like / len(rows) * 100.0, 2) if rows else None,
            "good_rejects": good,
            "safety_rejects_with_later_upside": safety_upside,
            "avg_best_move_pct": round(mean(bests), 6) if bests else None,
            "avg_worst_move_pct": round(mean(worsts), 6) if worsts else None,
        }

    return {
        "version": "counterfactual-gate-audit-v1.1",
        "status": "OK" if evaluated else "NO_EVALUATED_REJECTIONS",
        "evaluated_rejections": len(evaluated),
        "pending_rejections": pending,
        "classifications": dict(classifications.most_common()),
        "by_decision": {key: summarize(rows) for key, rows in sorted(by_decision.items())},
        "by_gate_family": {key: summarize(rows) for key, rows in sorted(by_family.items())},
        "interpretation": {
            "heuristic_only": True,
            "price_path_is_not_execution_proof": True,
            "safety_gates_remain_authoritative": True,
            "automatic_tuning_applied": False,
            "advisory_only": True,
        },
    }
