from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from app.services.trade_outcome_registry import get_outcomes


MIN_RELIABLE_SAMPLES = 30


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 2)


def _bucket_numeric(value: Any, *, step: int = 10) -> str:
    if not isinstance(value, (int, float)):
        return "unknown"
    low = int(float(value) // step) * step
    high = low + step - 1
    return f"{low}-{high}"


def _nested_value(item: dict[str, Any], *path: str) -> Any:
    current: Any = item
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _price_movement_state(item: dict[str, Any]) -> str:
    value = item.get("price_movement")
    if not isinstance(value, dict):
        return "unknown"
    for key in ("state", "classification", "readiness", "status"):
        candidate = value.get(key)
        if candidate not in (None, ""):
            return str(candidate).upper()
    return "unknown"


def _intelligence_bias(item: dict[str, Any]) -> str:
    value = item.get("market_intelligence")
    if not isinstance(value, dict):
        return "unknown"
    for path in (
        ("bias",),
        ("directional_bias",),
        ("summary", "bias"),
        ("integration", "bias"),
    ):
        candidate = _nested_value(value, *path)
        if candidate not in (None, ""):
            return str(candidate).upper()
    return "unknown"


def _classify(item: dict[str, Any]) -> str:
    entered = bool(item.get("entered_trade"))
    terminal = bool(item.get("terminal_status"))
    t1 = bool(item.get("target_1_observed"))
    t2 = bool(item.get("target_2_observed"))
    stop = bool(item.get("stop_observed"))

    if not terminal:
        return "OPEN_OR_UNRESOLVED"
    if not entered:
        status = str(item.get("terminal_status") or item.get("setup_status") or "").lower()
        if "expir" in status:
            return "NO_ENTRY_EXPIRED"
        if "cancel" in status or "reject" in status:
            return "NO_ENTRY_CANCELLED"
        return "NO_ENTRY_TERMINAL"
    if t2:
        return "TARGET_2_REACHED"
    if t1:
        return "TARGET_1_ONLY"
    if stop:
        return "STOP_BEFORE_TARGET"
    return "ENTERED_NO_TARGET"


def _failure_reason(item: dict[str, Any], classification: str) -> str | None:
    if classification == "STOP_BEFORE_TARGET":
        mae = item.get("mae_pct")
        mfe = item.get("mfe_pct")
        if isinstance(mfe, (int, float)) and float(mfe) < 0.25:
            return "IMMEDIATE_DIRECTIONAL_FAILURE"
        if isinstance(mae, (int, float)) and isinstance(mfe, (int, float)) and float(mae) > float(mfe) * 2:
            return "ADVERSE_DOMINANCE"
        return "STOPPED_WITHOUT_TARGET"
    if classification == "ENTERED_NO_TARGET":
        mfe = item.get("mfe_pct")
        if isinstance(mfe, (int, float)) and float(mfe) < 0.25:
            return "INSUFFICIENT_FOLLOW_THROUGH"
        return "TERMINAL_WITHOUT_TARGET_CONFIRMATION"
    if classification.startswith("NO_ENTRY"):
        return "ENTRY_TIMING_OR_SETUP_FAILURE"
    return None


def _breakdown(records: Iterable[dict[str, Any]], key_fn) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in records:
        buckets[str(key_fn(item))].append(item)

    result: dict[str, dict[str, Any]] = {}
    for label, rows in sorted(buckets.items()):
        entered = [row for row in rows if row.get("entered_trade")]
        resolved_entered = [row for row in entered if row.get("terminal_status")]
        t1_success = sum(bool(row.get("target_1_observed")) for row in resolved_entered)
        t2_success = sum(bool(row.get("target_2_observed")) for row in resolved_entered)
        false_positive = sum(_classify(row) == "STOP_BEFORE_TARGET" for row in resolved_entered)
        result[label] = {
            "records": len(rows),
            "resolved_entered": len(resolved_entered),
            "target_1_success_rate_pct": _pct(t1_success, len(resolved_entered)),
            "target_2_success_rate_pct": _pct(t2_success, len(resolved_entered)),
            "stop_before_target_rate_pct": _pct(false_positive, len(resolved_entered)),
        }
    return result


def build_signal_quality_audit(
    outcomes: list[dict[str, Any]] | None = None,
    *,
    minimum_reliable_samples: int = MIN_RELIABLE_SAMPLES,
) -> dict[str, Any]:
    """Build a read-only diagnostic view of OHM signal quality.

    This audit never changes thresholds, ranks, gates, or alerts. It summarizes
    already-captured outcome evidence so future tuning can be based on observed
    failure modes instead of anecdotal signal accuracy.
    """

    records = list(get_outcomes() if outcomes is None else outcomes)
    terminal = [item for item in records if item.get("terminal_status")]
    resolved_entered = [item for item in terminal if item.get("entered_trade")]
    resolved_no_entry = [item for item in terminal if not item.get("entered_trade")]

    classifications = Counter(_classify(item) for item in records)
    failures = Counter()
    for item in terminal:
        reason = _failure_reason(item, _classify(item))
        if reason:
            failures[reason] += 1

    t1_success = sum(bool(item.get("target_1_observed")) for item in resolved_entered)
    t2_success = sum(bool(item.get("target_2_observed")) for item in resolved_entered)
    stop_before_target = sum(_classify(item) == "STOP_BEFORE_TARGET" for item in resolved_entered)

    mfe_values = [float(item["mfe_pct"]) for item in resolved_entered if isinstance(item.get("mfe_pct"), (int, float))]
    mae_values = [float(item["mae_pct"]) for item in resolved_entered if isinstance(item.get("mae_pct"), (int, float))]

    status = "RELIABLE" if len(resolved_entered) >= minimum_reliable_samples else "PROVISIONAL"

    return {
        "version": "signal-quality-audit-v1",
        "status": status,
        "minimum_reliable_samples": minimum_reliable_samples,
        "records": len(records),
        "terminal_records": len(terminal),
        "resolved_entered": len(resolved_entered),
        "resolved_no_entry": len(resolved_no_entry),
        "quality_definition": "Target 1 observed on a resolved entered trade; Target 2 reported separately.",
        "target_1_success_rate_pct": _pct(t1_success, len(resolved_entered)),
        "target_2_success_rate_pct": _pct(t2_success, len(resolved_entered)),
        "stop_before_target_rate_pct": _pct(stop_before_target, len(resolved_entered)),
        "entry_conversion_rate_pct": _pct(len(resolved_entered), len(terminal)),
        "average_mfe_pct": round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else None,
        "average_mae_pct": round(sum(mae_values) / len(mae_values), 4) if mae_values else None,
        "classifications": dict(sorted(classifications.items())),
        "failure_reasons": dict(failures.most_common()),
        "breakdowns": {
            "direction": _breakdown(terminal, lambda item: str(item.get("direction") or "unknown").upper()),
            "market_regime": _breakdown(terminal, lambda item: str(item.get("market_regime") or "unknown").upper()),
            "technical_score": _breakdown(terminal, lambda item: _bucket_numeric(item.get("technical_score"))),
            "target_attainability_score": _breakdown(terminal, lambda item: _bucket_numeric(item.get("target_attainability_score"))),
            "profit_rank": _breakdown(terminal, lambda item: str(item.get("profit_rank") if item.get("profit_rank") is not None else "unknown")),
            "price_movement_state": _breakdown(terminal, _price_movement_state),
            "market_intelligence_bias": _breakdown(terminal, _intelligence_bias),
        },
        "automatic_tuning_applied": False,
        "advisory_only": True,
    }
