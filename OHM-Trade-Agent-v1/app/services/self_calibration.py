from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

MIN_GLOBAL_SAMPLES = 30
MIN_BUCKET_SAMPLES = 8
MAX_ADJUSTMENT = 0.15


def _score(row: dict[str, Any]) -> float:
    """Net-after-cost outcome is the primary calibration truth.

    The score remains binary for conservative sample efficiency, but a trade is
    only a success when its realized net P/L is positive. Legacy records that
    lack financial data fall back to T2 observation and never override newer
    fee-aware evidence.
    """
    net = row.get("net_pnl")
    if isinstance(net, (int, float)):
        return 1.0 if net > 0 else 0.0
    return 1.0 if row.get("target_2_observed") else 0.0


def calibration_model(records: list[dict[str, Any]], *, min_samples: int = MIN_GLOBAL_SAMPLES) -> dict[str, Any]:
    rows = [r for r in records if r.get("entered_trade") and r.get("terminal_status")]
    if len(rows) < min_samples:
        return {"status": "INSUFFICIENT_DATA", "samples": len(rows), "minimum_required": min_samples, "weights": {}}
    baseline = sum(_score(r) for r in rows) / len(rows)
    financial = [r for r in rows if isinstance(r.get("net_pnl"), (int, float))]
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
        rate = sum(_score(r) for r in bucket) / len(bucket)
        adjustment = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, rate - baseline))
        weights[key] = round(1.0 + adjustment, 4)
        net_values = [float(r["net_pnl"]) for r in bucket if isinstance(r.get("net_pnl"), (int, float))]
        evidence[key] = {
            "samples": len(bucket),
            "success_rate": round(rate, 4),
            "financial_samples": len(net_values),
            "avg_net_pnl": round(mean(net_values), 8) if net_values else None,
        }
    return {
        "status": "CALIBRATED",
        "samples": len(rows),
        "financial_samples": len(financial),
        "baseline_success_rate": round(baseline, 4),
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
