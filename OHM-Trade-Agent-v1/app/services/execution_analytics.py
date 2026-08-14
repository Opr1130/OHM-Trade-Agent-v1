from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Iterable

from app.services.execution_learning_registry import get_execution_records


MIN_EXECUTION_SAMPLES = 5


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    slippage = [_num(row.get("fill_vs_limit_pct")) for row in rows]
    slippage = [value for value in slippage if value is not None]
    order_latency = [_num(row.get("order_to_first_fill_seconds")) for row in rows]
    order_latency = [value for value in order_latency if value is not None and value >= 0]
    idea_latency = [_num(row.get("idea_to_first_fill_seconds")) for row in rows]
    idea_latency = [value for value in idea_latency if value is not None and value >= 0]
    fees = []
    for row in rows:
        entry_fee = _num(row.get("entry_fee")) or 0.0
        exit_fee = _num(row.get("exit_fee")) or 0.0
        if row.get("entry_fee") is not None or row.get("exit_fee") is not None:
            fees.append(entry_fee + exit_fee)
    return {
        "count": len(rows),
        "slippage_samples": len(slippage),
        "avg_fill_vs_limit_pct": round(mean(slippage), 6) if slippage else None,
        "adverse_fill_rate_pct": round(sum(value > 0 for value in slippage) / len(slippage) * 100.0, 2) if slippage else None,
        "order_latency_samples": len(order_latency),
        "avg_order_to_first_fill_seconds": round(mean(order_latency), 3) if order_latency else None,
        "avg_idea_to_first_fill_seconds": round(mean(idea_latency), 3) if idea_latency else None,
        "fee_samples": len(fees),
        "total_fees": round(sum(fees), 8) if fees else None,
        "avg_round_trip_fees": round(mean(fees), 8) if fees else None,
        "sufficient_samples": len(rows) >= MIN_EXECUTION_SAMPLES,
    }


def execution_quality_summary(records: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = list(records if records is not None else get_execution_records())
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_direction: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol[str(row.get("symbol") or "UNKNOWN").upper()].append(row)
        by_direction[str(row.get("direction") or "UNKNOWN").upper()].append(row)
    return {
        "status": "OK" if rows else "NO_EXECUTION_RECORDS",
        "records": len(rows),
        "overall": _summary(rows),
        "by_symbol": {key: _summary(value) for key, value in sorted(by_symbol.items())},
        "by_direction": {key: _summary(value) for key, value in sorted(by_direction.items())},
        "minimum_execution_samples": MIN_EXECUTION_SAMPLES,
    }
