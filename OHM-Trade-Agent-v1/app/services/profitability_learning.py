from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from app.services.decision_learning import is_non_entry_decision
from app.services.execution_learning_registry import get_execution_records
from app.services.market_intelligence_attribution import (
    MAX_COMBINED_MULTIPLIER as MAX_INTELLIGENCE_MULTIPLIER,
    MAX_PROPOSED_WEIGHT_ADJUSTMENT as MAX_INTELLIGENCE_WEIGHT_ADJUSTMENT,
    MIN_ACTUAL_BUCKET_SAMPLES as MIN_INTELLIGENCE_BUCKET_SAMPLES,
    MIN_ACTUAL_SAMPLES as MIN_INTELLIGENCE_TRADE_SAMPLES,
    MIN_COMBINED_MULTIPLIER as MIN_INTELLIGENCE_MULTIPLIER,
    build_market_intelligence_attribution,
)
from app.services.registry_io import load_json, registry_lock, save_json_atomic
from app.services.shadow_learning import get_shadow_records
from app.services.trade_outcome_registry import get_outcomes


PROFILE_FILE = Path("/app/data/strategy_calibration_profile.json")
LOCK_FILE = PROFILE_FILE.parent / ".strategy_calibration_profile.lock"
MIN_TRADE_SAMPLES = 30
MIN_BUCKET_SAMPLES = 30
MIN_SHADOW_SAMPLES = 12
MAX_WEIGHT_ADJUSTMENT = 0.15
MAX_TIMING_ADJUSTMENT = 0.12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _bounded_weight(delta: float, ceiling: float = MAX_WEIGHT_ADJUSTMENT) -> float:
    return round(1.0 + max(-ceiling, min(ceiling, delta)), 4)


def _financial_trade_rows(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in outcomes
        if row.get("entered_trade") and row.get("terminal_status") and _num(row.get("net_pnl")) is not None
    ]


def _net_success(row: dict[str, Any]) -> float:
    """Compatibility/reporting helper; sizing is driven by expectancy below."""
    net = _num(row.get("net_pnl"))
    return 1.0 if net is not None and net > 0 else 0.0


def _expectancy_adjustment(bucket: list[dict[str, Any]], all_rows: list[dict[str, Any]]) -> tuple[float, float, float]:
    """Return bounded-relative expectancy signal and the underlying means.

    Dollar expectancy is the primary realized objective. The relative delta is
    normalized by average absolute realized P/L so one unusually large trade
    cannot create an unbounded live sizing change; the final weight is still
    constrained by MAX_WEIGHT_ADJUSTMENT.
    """
    global_values = [float(row["net_pnl"]) for row in all_rows]
    bucket_values = [float(row["net_pnl"]) for row in bucket]
    baseline = mean(global_values)
    bucket_expectancy = mean(bucket_values)
    scale = max(1.0, mean(abs(value) for value in global_values))
    return (bucket_expectancy - baseline) / scale, baseline, bucket_expectancy


def _trade_bucket_weights(rows: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, Any]]:
    if len(rows) < MIN_TRADE_SAMPLES:
        return {}, {"status": "INSUFFICIENT_DATA", "samples": len(rows), "minimum_required": MIN_TRADE_SAMPLES}
    baseline_expectancy = mean(float(row["net_pnl"]) for row in rows)
    baseline_win_rate = mean(_net_success(row) for row in rows)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[f"direction:{str(row.get('direction') or 'UNKNOWN').upper()}"].append(row)
        buckets[f"regime:{str(row.get('market_regime') or 'UNKNOWN').upper()}"].append(row)
    weights: dict[str, float] = {}
    evidence: dict[str, Any] = {}
    for name, bucket in buckets.items():
        if len(bucket) < MIN_BUCKET_SAMPLES:
            continue
        expectancy_delta, _, bucket_expectancy = _expectancy_adjustment(bucket, rows)
        success = mean(_net_success(row) for row in bucket)
        weights[name] = _bounded_weight(expectancy_delta)
        net_values = [float(row["net_pnl"]) for row in bucket]
        evidence[name] = {
            "samples": len(bucket),
            "net_win_rate": round(success, 4),
            "avg_net_pnl": round(bucket_expectancy, 8),
            "net_pnl_sum": round(sum(net_values), 8),
            "expectancy_delta_normalized": round(expectancy_delta, 6),
        }
    return weights, {
        "status": "CALIBRATED",
        "samples": len(rows),
        "baseline_net_win_rate": round(baseline_win_rate, 4),
        "baseline_avg_net_pnl": round(baseline_expectancy, 8),
        "objective": "realized_net_pnl_expectancy_after_costs",
        "minimum_bucket_samples": MIN_BUCKET_SAMPLES,
        "evidence": evidence,
    }


def _shadow_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    evaluable = []
    for row in records:
        obs = row.get("observations") or {}
        final = obs.get("24h") or obs.get("4h") or obs.get("1h")
        if final and _num(final.get("directional_move_pct")) is not None:
            evaluable.append((row, float(final["directional_move_pct"])))
    if not evaluable:
        return {"status": "NO_DATA", "samples": 0, "decision_accuracy_pct": None, "missed_profitable": 0, "correct_rejections": 0}

    correct = 0
    missed = 0
    correct_rejects = 0
    by_decision: dict[str, list[float]] = defaultdict(list)
    for row, move in evaluable:
        decision = str(row.get("decision") or "UNKNOWN").upper()
        by_decision[decision].append(move)
        if is_non_entry_decision(decision):
            if move <= 0:
                correct += 1
                correct_rejects += 1
            else:
                missed += 1
        elif decision in {"ENTER", "ENTER_NOW", "PLACE_LIMIT", "QUALIFIED"} and move > 0:
            correct += 1

    return {
        "status": "OK",
        "samples": len(evaluable),
        "decision_accuracy_pct": round(correct / len(evaluable) * 100.0, 2),
        "missed_profitable": missed,
        "correct_rejections": correct_rejects,
        "by_decision": {
            name: {
                "samples": len(values),
                "avg_directional_move_pct": round(mean(values), 4),
                "positive_rate_pct": round(sum(v > 0 for v in values) / len(values) * 100.0, 2),
            }
            for name, values in sorted(by_decision.items())
        },
    }


def _timing_metrics(executions: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    outcome_by_id = {str(r.get("trade_id")): r for r in outcomes if r.get("trade_id")}
    rows = []
    for execution in executions:
        outcome = outcome_by_id.get(str(execution.get("trade_id")))
        if not outcome or _num(outcome.get("net_pnl")) is None:
            continue
        rows.append((execution, float(outcome["net_pnl"])))
    if not rows:
        return {"status": "NO_FINANCIAL_EXECUTIONS", "samples": 0}

    winners = [execution for execution, pnl in rows if pnl > 0]
    losers = [execution for execution, pnl in rows if pnl <= 0]

    def avg(field: str, group: list[dict[str, Any]]) -> float | None:
        vals = [_num(row.get(field)) for row in group]
        vals = [v for v in vals if v is not None]
        return round(mean(vals), 3) if vals else None

    capital_efficiency = []
    for execution, pnl in rows:
        holding = _num(execution.get("holding_seconds"))
        quantity = _num(execution.get("quantity"))
        fill_price = _num(execution.get("fill_price"))
        if holding and holding > 0 and quantity and fill_price:
            capital = quantity * fill_price
            if capital > 0:
                capital_efficiency.append(pnl / (capital * (holding / 3600.0)))

    return {
        "status": "OK",
        "samples": len(rows),
        "avg_idea_to_order_seconds_winners": avg("idea_to_order_seconds", winners),
        "avg_idea_to_order_seconds_losers": avg("idea_to_order_seconds", losers),
        "avg_order_to_fill_seconds_winners": avg("order_to_first_fill_seconds", winners),
        "avg_order_to_fill_seconds_losers": avg("order_to_first_fill_seconds", losers),
        "avg_fill_vs_limit_pct_winners": avg("fill_vs_limit_pct", winners),
        "avg_fill_vs_limit_pct_losers": avg("fill_vs_limit_pct", losers),
        "avg_net_pnl_per_capital_hour": round(mean(capital_efficiency), 10) if capital_efficiency else None,
    }


def _loss_learning(rows: list[dict[str, Any]]) -> dict[str, Any]:
    losses = [row for row in rows if float(row.get("net_pnl") or 0.0) < 0]
    avoidable = 0
    avoidable_dollars = 0.0
    reasons: dict[str, int] = defaultdict(int)
    for row in losses:
        reason = None
        mae = _num(row.get("mae_pct"))
        mfe = _num(row.get("mfe_pct"))
        if row.get("stop_observed") and mfe is not None and mfe <= 0.25:
            reason = "no_meaningful_favorable_excursion_before_stop"
        elif mae is not None and mfe is not None and mae > max(2.0, mfe * 2.0):
            reason = "adverse_excursion_dominated"
        if reason:
            avoidable += 1
            avoidable_dollars += abs(float(row["net_pnl"]))
            reasons[reason] += 1
    return {
        "losing_trades": len(losses),
        "potentially_avoidable_losses": avoidable,
        "potentially_avoidable_loss_dollars": round(avoidable_dollars, 8),
        "heuristic_only": True,
        "reasons": dict(sorted(reasons.items())),
    }


def build_profitability_profile(
    *,
    outcomes: list[dict[str, Any]] | None = None,
    executions: list[dict[str, Any]] | None = None,
    shadows: list[dict[str, Any]] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    outcomes = outcomes if outcomes is not None else get_outcomes()
    executions = executions if executions is not None else get_execution_records()
    shadows = shadows if shadows is not None else get_shadow_records()
    financial_rows = _financial_trade_rows(outcomes)
    weights, trade_evidence = _trade_bucket_weights(financial_rows)
    intelligence = build_market_intelligence_attribution(outcomes=outcomes, shadows=shadows)
    profile = {
        "schema_version": 2,
        "generated_at": _now(),
        "version": "profitability-learning-v2",
        "objective": "maximize realized net P/L expectancy after known costs while preserving hard risk rules",
        "paid_ai_calls": 0,
        "trade_calibration": trade_evidence,
        "shadow_learning": _shadow_quality(shadows),
        "timing_learning": _timing_metrics(executions, outcomes),
        "loss_learning": _loss_learning(financial_rows),
        "market_intelligence_attribution": intelligence,
        "weights": weights,
        "guardrails": {
            "max_live_weight_adjustment": MAX_WEIGHT_ADJUSTMENT,
            "minimum_trade_samples": MIN_TRADE_SAMPLES,
            "minimum_bucket_samples": MIN_BUCKET_SAMPLES,
            "hard_risk_rules_mutable": False,
            "automatic_gate_or_threshold_changes": False,
            "market_intelligence_max_combined_multiplier": MAX_INTELLIGENCE_MULTIPLIER,
            "market_intelligence_max_weight_adjustment": MAX_INTELLIGENCE_WEIGHT_ADJUSTMENT,
            "market_intelligence_min_actual_trade_samples": MIN_INTELLIGENCE_TRADE_SAMPLES,
            "market_intelligence_min_actual_bucket_samples": MIN_INTELLIGENCE_BUCKET_SAMPLES,
        },
    }
    if persist:
        with registry_lock(LOCK_FILE):
            save_json_atomic(PROFILE_FILE, profile)
    return profile


def learned_multiplier(*, direction: str, regime: str | None) -> float:
    try:
        with registry_lock(LOCK_FILE):
            profile = load_json(PROFILE_FILE)
    except (OSError, TimeoutError):
        return 1.0
    weights = profile.get("weights") or {}
    values = [weights.get(f"direction:{direction.upper()}", 1.0)]
    if regime:
        values.append(weights.get(f"regime:{regime.upper()}", 1.0))
    result = 1.0
    for value in values:
        result *= float(value)
    return round(max(0.75, min(1.25, result)), 4)
