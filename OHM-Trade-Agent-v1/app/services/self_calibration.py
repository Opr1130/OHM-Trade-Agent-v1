from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean
from typing import Any

MIN_GLOBAL_SAMPLES = 30
MIN_BUCKET_SAMPLES = 30
MAX_ADJUSTMENT = 0.15


def _finite_net(row: dict[str, Any]) -> float | None:
    net = row.get("net_pnl")
    if not isinstance(net, (int, float)):
        return None
    numeric = float(net)
    return numeric if math.isfinite(numeric) else None


def _score(row: dict[str, Any]) -> float:
    """Return finite realized net P/L for compatibility with callers/tests."""
    net = _finite_net(row)
    return net if net is not None else 0.0


def calibration_model(records: list[dict[str, Any]], *, min_samples: int = MIN_GLOBAL_SAMPLES) -> dict[str, Any]:
    terminal = [r for r in records if r.get("entered_trade") and r.get("terminal_status")]
    rows = [r for r in terminal if _finite_net(r) is not None]
    if len(rows) < min_samples:
        return {
            "status": "INSUFFICIENT_DATA",
            "samples": len(rows),
            "terminal_samples": len(terminal),
            "minimum_required": min_samples,
            "weights": {},
            "objective": "realized_net_pnl_expectancy_after_costs",
        }

    net_values = [_finite_net(r) for r in rows]
    net_values = [value for value in net_values if value is not None]
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
        finite_bucket = [r for r in bucket if _finite_net(r) is not None]
        if len(finite_bucket) < MIN_BUCKET_SAMPLES:
            continue
        bucket_values = [_finite_net(r) for r in finite_bucket]
        bucket_values = [value for value in bucket_values if value is not None]
        bucket_expectancy = mean(bucket_values)
        normalized_delta = (bucket_expectancy - baseline_expectancy) / scale
        if not math.isfinite(normalized_delta):
            continue
        adjustment = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, normalized_delta))
        weight = 1.0 + adjustment
        if not math.isfinite(weight):
            continue
        weights[key] = round(weight, 4)
        evidence[key] = {
            "samples": len(finite_bucket),
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
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(numeric) or not 0.75 <= numeric <= 1.25:
            return 1.0
        product *= numeric
    if not math.isfinite(product):
        return 1.0
    return round(max(0.75, min(1.25, product)), 4)


def calibrated_multiplier(model: dict[str, Any], *, direction: str, regime: str | None) -> float:
    # The daily profitability profile is bounded sizing-only learning. Both the
    # persisted profile and this model require >=30 finite resolved financial
    # samples per bucket before any live sizing multiplier can move.
    try:
        from app.services.profitability_learning import learned_multiplier

        persisted = learned_multiplier(direction=direction, regime=regime)
    except Exception:
        persisted = 1.0
    if persisted != 1.0:
        return persisted
    return _live_multiplier(model, direction=direction, regime=regime)
