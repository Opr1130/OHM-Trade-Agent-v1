from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

MIN_GLOBAL_SAMPLES = 30
MIN_BUCKET_SAMPLES = 8
MAX_ADJUSTMENT = 0.15


def _score(row: dict[str, Any]) -> float:
    """Return realized net P/L for compatibility with existing callers/tests.

    Live calibration no longer converts realized outcomes to a binary win/loss
    score. Records without realized financial data are not allowed to move live
    sizing weights.
    """
    net = row.get("net_pnl")
    return float(net) if isinstance(net, (int, float)) else 0.0


def calibration_model(records: list[dict[str, Any]], *, min_samples: int = MIN_GLOBAL_SAMPLES) -> dict[str, Any]:
    terminal = [r for r in records if r.get("entered_trade") and r.get("terminal_status")]
    rows = [r for r in terminal if isinstance(r.get("net_pnl"), (int, float))]
    if len(rows) < min_samples:
        return {
            "status": "INSUFFICIENT_DATA",
            "samples": len(rows),
            "terminal_samples": len(terminal),
            "minimum_required": min_samples,
            "weights": {},
            "objective": "realized_net_pnl_expectancy_after_costs",
        }

    net_values = [float(r["net_pnl"]) for r in rows]
    baseline_expectancy = mean(net_values)
    baseline_win_rate = sum(value > 0 for value in net_values) / len(net_values)
    scale = max(1.0, mean(abs(value) for value in net_values))

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        direction = str(row.get("direction") or "UNKNOWN").upper()
        regime = str(row.get("market_regime") or "UNKNOWN").upper()
        buckets[f"direction:{direction}"].append(row)
        buckets[f"regime:{regime}"].append(row)

    weights: dict[str, float] = {}
    evidence: dict[str, dict[str, Any]] = {}
    for key, bucket in buckets.items():
        if len(bucket) < MIN_BUCKET_SAMPLES:
            continue
        bucket_values = [float(r["net_pnl"]) for r in bucket]
        bucket_expectancy = mean(bucket_values)
        normalized_delta = (bucket_expectancy - baseline_expectancy) / scale
        adjustment = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, normalized_delta))
        weights[key] = round(1.0 + adjustment, 4)
        evidence[key] = {
            "samples": len(bucket),
            "success_rate": round(sum(value > 0 for value in bucket_values) / len(bucket_values), 4),
            "financial_samples": len(bucket_values),
            "avg_net_pnl": round(bucket_expectancy, 8),
            "net_pnl_sum": round(sum(bucket_values), 8),
            "expectancy_delta_normalized": round(normalized_delta, 6),
        }

    return {
        "status": "CALIBRATED",
        "samples": len(rows),
        "financial_samples": len(rows),
        "baseline_success_rate": round(baseline_win_rate, 4),
        "baseline_avg_net_pnl": round(baseline_expectancy, 8),
        "objective": "realized_net_pnl_expectancy_after_costs",
        "weights": weights,
        "evidence": evidence,
        "guardrail": {"max_adjustment": MAX_ADJUSTMENT, "minimum_bucket_samples": MIN_BUCKET_SAMPLES},
    }


def _live_multiplier(model: dict[str, Any], *, direction: str, regime: str | None) -> float:
    if model.get("status") != "CALIBRATED":
        return 1.0
    weights = model.get("weights") or {}
    values = [weights.get(f"direction:{direction.upper()}", 1.0)]
    if regime:
        values.append(weights.get(f"regime:{regime.upper()}", 1.0))
    product = 1.0
    for value in values:
        product *= float(value)
    return round(max(0.75, min(1.25, product)), 4)


def calibrated_multiplier(model: dict[str, Any], *, direction: str, regime: str | None) -> float:
    # The daily profitability profile allows behavior to improve from data
    # without code edits. It is generated locally and has the same hard bounds.
    # If it is not mature or not present, preserve the existing live model.
    try:
        from app.services.profitability_learning import learned_multiplier

        persisted = learned_multiplier(direction=direction, regime=regime)
    except Exception:
        persisted = 1.0
    if persisted != 1.0:
        return persisted
    return _live_multiplier(model, direction=direction, regime=regime)
