from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Iterable


MIN_BUCKET_SAMPLES = 8
MISS_THRESHOLD_PCT = 2.0
AVOIDED_LOSS_THRESHOLD_PCT = -2.0


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _moves(row: dict[str, Any]) -> dict[str, float]:
    observations = row.get("observations") or {}
    result: dict[str, float] = {}
    for horizon, item in observations.items():
        if not isinstance(item, dict):
            continue
        move = _num(item.get("directional_move_pct"))
        if move is not None:
            result[str(horizon)] = move
    return result


def classify_shadow_decision(row: dict[str, Any]) -> dict[str, Any]:
    """Classify one shadow decision from future directional price movement.

    This is intentionally outcome-observation only. It does not assert that a
    missed move would have been executable or profitable after fees; it labels
    candidates for later review and calibration evidence.
    """
    decision = str(row.get("decision") or "UNKNOWN").upper()
    moves = _moves(row)
    if not moves:
        return {
            "status": "PENDING",
            "decision": decision,
            "best_move_pct": None,
            "worst_move_pct": None,
            "missed_profitable_opportunity": False,
            "correct_wait_or_reject": False,
        }

    best = max(moves.values())
    worst = min(moves.values())
    is_non_entry = decision in {"WAIT", "REJECT", "TARGET_REJECT", "ECONOMIC_REJECT", "EXECUTION_REJECT", "MARGIN_REJECT"}
    missed = is_non_entry and best >= MISS_THRESHOLD_PCT
    correct_avoid = is_non_entry and best < MISS_THRESHOLD_PCT and worst <= AVOIDED_LOSS_THRESHOLD_PCT

    # For ENTER decisions, compare immediate reference against delayed shadow
    # points. Positive delayed edge means a later entry would have provided a
    # better directional starting point for the same eventual move.
    immediate_vs_wait: dict[str, float] = {}
    if decision in {"ENTER", "ENTER_NOW", "ALERT"}:
        terminal = moves.get("24h")
        if terminal is not None:
            for horizon in ("5m", "15m", "30m", "1h"):
                if horizon in moves:
                    immediate_vs_wait[horizon] = round(terminal - moves[horizon], 6)

    return {
        "status": "EVALUATED",
        "decision": decision,
        "best_move_pct": round(best, 6),
        "worst_move_pct": round(worst, 6),
        "missed_profitable_opportunity": missed,
        "correct_wait_or_reject": correct_avoid,
        "immediate_vs_wait_edge_pct": immediate_vs_wait,
    }


def decision_quality_summary(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    evaluated = []
    by_decision: dict[str, list[dict[str, Any]]] = defaultdict(list)
    wait_edges: dict[str, list[float]] = defaultdict(list)

    for row in records:
        classified = classify_shadow_decision(row)
        if classified["status"] != "EVALUATED":
            continue
        evaluated.append(classified)
        by_decision[classified["decision"]].append(classified)
        for horizon, edge in classified.get("immediate_vs_wait_edge_pct", {}).items():
            wait_edges[horizon].append(float(edge))

    missed = sum(bool(r["missed_profitable_opportunity"]) for r in evaluated)
    correct_avoid = sum(bool(r["correct_wait_or_reject"]) for r in evaluated)

    return {
        "status": "OK" if evaluated else "NO_EVALUATED_SHADOWS",
        "evaluated": len(evaluated),
        "missed_profitable_opportunities": missed,
        "correct_wait_or_reject": correct_avoid,
        "by_decision": {
            key: {
                "count": len(rows),
                "avg_best_move_pct": round(mean(r["best_move_pct"] for r in rows), 6),
                "avg_worst_move_pct": round(mean(r["worst_move_pct"] for r in rows), 6),
                "missed": sum(bool(r["missed_profitable_opportunity"]) for r in rows),
                "correct_avoid": sum(bool(r["correct_wait_or_reject"]) for r in rows),
            }
            for key, rows in sorted(by_decision.items())
        },
        "immediate_vs_wait": {
            horizon: {
                "samples": len(values),
                "avg_delayed_edge_pct": round(mean(values), 6),
                "sufficient_samples": len(values) >= MIN_BUCKET_SAMPLES,
            }
            for horizon, values in sorted(wait_edges.items())
        },
        "thresholds": {
            "miss_threshold_pct": MISS_THRESHOLD_PCT,
            "avoided_loss_threshold_pct": AVOIDED_LOSS_THRESHOLD_PCT,
            "minimum_bucket_samples": MIN_BUCKET_SAMPLES,
        },
    }
