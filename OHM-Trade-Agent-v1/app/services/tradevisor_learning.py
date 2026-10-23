"""Prospective learning for Tradevisor observations.

MEASUREMENT ONLY - NO POLICY PROMOTION, NO TRADE AUTHORITY.

Evidence ownership
------------------
Nothing new is stored here. The durable evidence already exists on
``tradevisor_inbox`` rows: the normalized source signal and the frozen
``decision_record`` written at decision time. This module only *derives*
measurements from those rows, so there is no second evidence store and no
competing canonical writer.

No look-ahead
-------------
An outcome is measured only from candles that start strictly AFTER the frozen
decision time, and only once the measurement window has elapsed. The decision
record never contains a realized price, a fill or an outcome, so a late replay
cannot see the future it is scoring.

No synthetic fills
------------------
A price touch is recorded as a touch, never as a fill
(``fill_attribution = NONE_TOUCH_INFERRED``). Touch-based expectancy is
labelled as an estimate.

Release compatibility
---------------------
The learning worker must run the exact production SHA. Classification is NOT
re-implemented here: it is re-exported from the learning plane's existing owner
``app.opip.learning.job_disposition`` so the Python gate, the shell gate
(``deploy/learning/opip-learning-job.sh``) and every other learning job can
never disagree. An unverifiable identity fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

#: Single Python owner of exact-SHA learning compatibility. Re-exported, not
#: duplicated: a second implementation could silently drift from the shell gate.
#: ``RELEASE_DRIFT`` and ``RELEASE_UNVERIFIED`` are explicit re-exports (the
#: redundant alias form), because callers import them from this module and
#: ``test_release_classification_is_the_learning_plane_owner_not_a_copy`` asserts
#: their identity against the owner module.
from app.opip.learning.job_disposition import (
    RELEASE_CURRENT,
    RELEASE_DRIFT as RELEASE_DRIFT,
    RELEASE_UNVERIFIED as RELEASE_UNVERIFIED,
    classify_release_compatibility as _owner_classify_release_compatibility,
)
from app.services.tradevisor_evaluation import ACTIONABLE_DECISIONS

#: Public name used by this module. It is the owner's function object, not a
#: wrapper and not a copy, so the two can never diverge.
classify_release_compatibility = _owner_classify_release_compatibility

COHORT_CORROBORATED = "TRADEVISOR_CORROBORATED"
COHORT_SHADOW = "TRADEVISOR_ONLY_SHADOW"

OUTCOME_PENDING = "PENDING"
OUTCOME_MEASURED = "MEASURED"
OUTCOME_NO_ENTRY_TOUCH = "NO_ENTRY_TOUCH"
OUTCOME_WINDOW_INCOMPLETE = "WINDOW_INCOMPLETE"

FILL_ATTRIBUTION_NONE = "NONE_TOUCH_INFERRED"

#: A touch-based expectancy is an estimate from frozen plans, not realized P&L.
EXPECTANCY_BASIS = "TOUCH_BASED_ESTIMATE_NOT_REALIZED"

DEFAULT_MEASUREMENT_HOURS = 24


def cohort_for(record: Mapping[str, Any]) -> str:
    decision = _decision_of(record)
    return COHORT_CORROBORATED if decision in ACTIONABLE_DECISIONS else COHORT_SHADOW


def _decision_of(record: Mapping[str, Any]) -> str:
    direct = record.get("decision")
    if isinstance(direct, str) and direct:
        return direct
    frozen = record.get("decision_record")
    if isinstance(frozen, Mapping):
        return str(frozen.get("decision") or "")
    return ""


def _frozen(record: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = record.get("decision_record")
    return frozen if isinstance(frozen, Mapping) else {}


def _parse(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _candle_time(candle: Any) -> datetime | None:
    for attribute in ("open_time", "time", "timestamp", "closed_at"):
        value = getattr(candle, attribute, None)
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return None


def evaluate_outcome(
    record: Mapping[str, Any],
    *,
    now: datetime,
    candles_provider: Callable[[str, datetime], Iterable[Any]] | None = None,
    measurement_hours: int = DEFAULT_MEASUREMENT_HOURS,
) -> dict[str, Any]:
    """Measure one frozen record, strictly forward in time."""
    frozen = _frozen(record)
    decided_at = _parse(frozen.get("evaluated_at")) or _parse(record.get("processed_at"))
    decision = _decision_of(record)
    plan = frozen.get("plan") if isinstance(frozen.get("plan"), Mapping) else None
    evidence = frozen.get("evidence") if isinstance(frozen.get("evidence"), Mapping) else {}
    qualification = (
        evidence.get("advisory_qualification")
        if isinstance(evidence.get("advisory_qualification"), Mapping)
        else {}
    )
    # G-02/G-04: a WATCH is eligible for prospective *hypothesis* measurement
    # only when the evaluator explicitly froze an otherwise-qualified entry
    # and withheld actionable authority. An arbitrary rejected WATCH with a
    # plan must never be silently counted as a hypothetical trade.
    shadow_hypothesis = (
        decision == "WATCH"
        and frozen.get("rejection_code") == "ADVISORY_EVIDENCE_INCOMPLETE"
        and qualification.get("status") == "SHADOW_ONLY"
        and evidence.get("shadow_proposed_action") in ACTIONABLE_DECISIONS
    )
    signal = record.get("signal") if isinstance(record.get("signal"), Mapping) else {}
    market = str(frozen.get("market") or signal.get("market") or "")

    base: dict[str, Any] = {
        "schema_version": "tradevisor-outcome-v1",
        "dedup_id": record.get("dedup_id") or frozen.get("dedup_id"),
        "cohort": cohort_for(record),
        "decision": decision,
        "market": market,
        "instrument": frozen.get("instrument"),
        "horizon": frozen.get("horizon"),
        "market_regime": frozen.get("market_regime"),
        "decided_at": decided_at.isoformat() if decided_at else None,
        "fill_attribution": FILL_ATTRIBUTION_NONE,
        "measurement_hours": measurement_hours,
        "measurement_subject": (
            "SHADOW_HYPOTHESIS" if shadow_hypothesis else "ACTIONABLE_RECOMMENDATION"
            if decision in ACTIONABLE_DECISIONS else "NON_ACTIONABLE_OBSERVATION"
        ),
    }

    if decided_at is None or not isinstance(plan, Mapping):
        base.update(status=OUTCOME_PENDING, reason="no frozen actionable plan")
        return base
    if decision not in ACTIONABLE_DECISIONS and not shadow_hypothesis:
        base.update(status=OUTCOME_NO_ENTRY_TOUCH, reason="non-actionable evidence only")
        return base
    if candles_provider is None:
        base.update(status=OUTCOME_PENDING, reason="no candle provider configured")
        return base

    window_end = decided_at + timedelta(hours=measurement_hours)
    if now < window_end:
        base.update(status=OUTCOME_WINDOW_INCOMPLETE, window_end=window_end.isoformat())
        return base

    try:
        candles = list(candles_provider(market, decided_at))
    except Exception as exc:  # noqa: BLE001 - unreadable candles are not an outcome
        base.update(
            status=OUTCOME_PENDING, reason=f"{type(exc).__name__} reading candles"
        )
        return base
    if not candles:
        base.update(status=OUTCOME_PENDING, reason="no candles returned")
        return base

    entry_low = float(plan.get("entry_low") or 0.0)
    entry_high = float(plan.get("entry_high") or 0.0)
    stop = float(plan.get("stop_price") or 0.0)
    target_1 = float(plan.get("target_1") or 0.0)
    target_2 = float(plan.get("target_2") or 0.0)
    if not (0 < entry_low <= entry_high < target_1 < target_2 and stop > 0):
        base.update(status=OUTCOME_PENDING, reason="frozen plan is not measurable")
        return base

    # Only candles that OPEN at or after the decision instant are admissible:
    # the decision bar itself is excluded so the outcome cannot see the price
    # that produced the decision.
    admissible = []
    for candle in candles:
        when = _candle_time(candle)
        if when is None or when < decided_at or when > window_end:
            continue
        admissible.append((when, candle))
    if not admissible:
        base.update(status=OUTCOME_PENDING, reason="no admissible forward candles")
        return base

    entry_reference = entry_high
    entry_touched = False
    mfe_pct = 0.0
    mae_pct = 0.0
    t1_at: datetime | None = None
    t2_at: datetime | None = None
    stop_at: datetime | None = None
    for when, candle in admissible:
        high = float(getattr(candle, "high", 0.0) or 0.0)
        low = float(getattr(candle, "low", 0.0) or 0.0)
        if not entry_touched:
            if not (low <= entry_high and high >= entry_low):
                continue
            entry_touched = True
        mfe_pct = max(mfe_pct, (high - entry_reference) / entry_reference * 100.0)
        mae_pct = min(mae_pct, (low - entry_reference) / entry_reference * 100.0)
        if t1_at is None and high >= target_1:
            t1_at = when
        if t2_at is None and high >= target_2:
            t2_at = when
        if stop_at is None and low <= stop:
            stop_at = when

    if not entry_touched:
        base.update(
            status=OUTCOME_NO_ENTRY_TOUCH, reason="the entry zone was never traded"
        )
        return base

    invalidation_first = stop_at is not None and (t1_at is None or stop_at <= t1_at)
    allocation = frozen.get("allocation")
    capital = float((allocation or {}).get("recommended_capital") or 0.0)
    base.update(
        status=OUTCOME_MEASURED,
        entry_reference=round(entry_reference, 8),
        entry_touched=True,
        mfe_pct=round(mfe_pct, 4),
        mae_pct=round(mae_pct, 4),
        target_1_touched=t1_at is not None,
        target_2_touched=t2_at is not None,
        stop_touched=stop_at is not None,
        time_to_target_1_seconds=(
            int((t1_at - decided_at).total_seconds()) if t1_at else None
        ),
        time_to_target_2_seconds=(
            int((t2_at - decided_at).total_seconds()) if t2_at else None
        ),
        time_to_invalidation_seconds=(
            int((stop_at - decided_at).total_seconds()) if stop_at else None
        ),
        false_positive=bool(invalidation_first),
        false_positive_definition=(
            "stop zone traded before Target 1 inside the measurement window"
        ),
        capital_occupancy_usd_seconds=round(
            capital * measurement_hours * 3600.0, 2
        ),
        basis=EXPECTANCY_BASIS,
    )
    return base


@dataclass(frozen=True)
class LearningReport:
    status: str
    release_compatibility: str
    cohorts: dict[str, Any]
    incremental_value: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "tradevisor-learning-v1",
            "status": self.status,
            "release_compatibility": self.release_compatibility,
            "cohorts": self.cohorts,
            "incremental_value": self.incremental_value,
            "policy_promotion": "NONE",
            "trade_authority_changed": False,
            "basis": EXPECTANCY_BASIS,
        }


def _mean(values: list[float]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    if not usable:
        return None
    return round(sum(usable) / len(usable), 4)


def summarize_cohorts(
    records: Iterable[Mapping[str, Any]],
    *,
    outcomes: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Count-based cohort metrics. No metric is invented when data is absent."""
    measured = outcomes or {}
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        cohort = cohort_for(record)
        bucket = buckets.setdefault(
            cohort,
            {
                "observed": 0,
                "actionable": 0,
                "rejected": 0,
                "rejection_reasons": {},
                "measured": 0,
                "entry_never_touched": 0,
                "false_positives": 0,
                "mfe_pct": [],
                "mae_pct": [],
                "time_to_target_1_seconds": [],
                "time_to_invalidation_seconds": [],
                "capital_occupancy_usd_seconds": 0.0,
                "estimated_net_usd": 0.0,
                "by_instrument": {},
                "by_regime": {},
            },
        )
        bucket["observed"] += 1
        decision = _decision_of(record)
        frozen = _frozen(record)
        instrument = str(frozen.get("instrument") or "UNKNOWN")
        regime = str(frozen.get("market_regime") or "UNKNOWN")
        actionable = decision in ACTIONABLE_DECISIONS
        if actionable:
            bucket["actionable"] += 1
        else:
            bucket["rejected"] += 1
            code = str(frozen.get("rejection_code") or decision or "UNKNOWN")
            bucket["rejection_reasons"][code] = (
                bucket["rejection_reasons"].get(code, 0) + 1
            )

        outcome = measured.get(str(record.get("dedup_id"))) or {}
        if outcome.get("status") == OUTCOME_MEASURED:
            bucket["measured"] += 1
            bucket["mfe_pct"].append(outcome.get("mfe_pct"))
            bucket["mae_pct"].append(outcome.get("mae_pct"))
            if outcome.get("time_to_target_1_seconds") is not None:
                bucket["time_to_target_1_seconds"].append(
                    outcome["time_to_target_1_seconds"]
                )
            if outcome.get("time_to_invalidation_seconds") is not None:
                bucket["time_to_invalidation_seconds"].append(
                    outcome["time_to_invalidation_seconds"]
                )
            bucket["capital_occupancy_usd_seconds"] += float(
                outcome.get("capital_occupancy_usd_seconds") or 0.0
            )
            if outcome.get("false_positive"):
                bucket["false_positives"] += 1
            allocation = frozen.get("allocation") or {}
            economics = frozen.get("economics") or {}
            envelope = float(economics.get("recommended_capital") or 0.0)
            capital = float(allocation.get("recommended_capital") or 0.0)
            scale = (capital / envelope) if envelope > 0 else 0.0
            net_t1 = float(economics.get("target_1_net_profit") or 0.0) * scale
            planned_loss = float(allocation.get("risk_dollars") or 0.0)
            if outcome.get("false_positive"):
                bucket["estimated_net_usd"] -= planned_loss
            elif outcome.get("target_1_touched"):
                bucket["estimated_net_usd"] += net_t1
        elif outcome.get("status") == OUTCOME_NO_ENTRY_TOUCH:
            bucket["entry_never_touched"] += 1

        for container, key in (
            (bucket["by_instrument"], instrument),
            (bucket["by_regime"], regime),
        ):
            entry = container.setdefault(
                key, {"observed": 0, "actionable": 0, "false_positives": 0}
            )
            entry["observed"] += 1
            if actionable:
                entry["actionable"] += 1
            if outcome.get("false_positive"):
                entry["false_positives"] += 1

    summary: dict[str, Any] = {}
    for cohort, bucket in buckets.items():
        measured_count = bucket["measured"]
        summary[cohort] = {
            "observed": bucket["observed"],
            "actionable": bucket["actionable"],
            "rejected": bucket["rejected"],
            "rejection_reasons": dict(sorted(bucket["rejection_reasons"].items())),
            "measured": measured_count,
            "entry_never_touched": bucket["entry_never_touched"],
            "false_positives": bucket["false_positives"],
            "false_positive_definition": (
                "stop zone traded before Target 1 inside the measurement window"
            ),
            "false_positive_rate": (
                round(bucket["false_positives"] / measured_count, 4)
                if measured_count
                else None
            ),
            "mean_mfe_pct": _mean(bucket["mfe_pct"]),
            "mean_mae_pct": _mean(bucket["mae_pct"]),
            "mean_time_to_target_1_seconds": _mean(bucket["time_to_target_1_seconds"]),
            "mean_time_to_invalidation_seconds": _mean(
                bucket["time_to_invalidation_seconds"]
            ),
            "capital_occupancy_usd_seconds": round(
                bucket["capital_occupancy_usd_seconds"], 2
            ),
            "estimated_net_usd": round(bucket["estimated_net_usd"], 2),
            "estimated_net_basis": EXPECTANCY_BASIS,
            "by_instrument": bucket["by_instrument"],
            "by_regime": bucket["by_regime"],
        }
    return summary


def build_learning_report(
    records: Iterable[Mapping[str, Any]],
    *,
    deployed_sha: object,
    expected_sha: object,
    outcomes: Mapping[str, Mapping[str, Any]] | None = None,
    native_metrics: Mapping[str, Any] | None = None,
) -> LearningReport:
    """Build the cohort report, refusing to compute across a release drift."""
    release = classify_release_compatibility(deployed_sha, expected_sha)
    if release != RELEASE_CURRENT:
        # Fail closed: an unverified or drifted worker must not publish metrics
        # that appear comparable with production outcomes.
        return LearningReport(
            status=release,
            release_compatibility=release,
            cohorts={},
            incremental_value={
                "status": "WITHHELD_RELEASE_DRIFT",
                "reason": "learning worker is not the exact production release",
            },
        )

    rows = list(records)
    cohorts = summarize_cohorts(rows, outcomes=outcomes)
    status = "NO_EVIDENCE" if not rows else RELEASE_CURRENT

    corroborated = cohorts.get(COHORT_CORROBORATED, {})
    if native_metrics is None:
        incremental: dict[str, Any] = {
            "status": "NATIVE_COHORT_NOT_AVAILABLE",
            "reason": (
                "native-cohort metrics are produced on the learning/analytics "
                "plane; pass them in to compare"
            ),
        }
    elif not corroborated.get("measured"):
        incremental = {
            "status": "INSUFFICIENT_TRADEVISOR_OUTCOMES",
            "reason": "no measured Tradevisor outcomes to compare yet",
        }
    else:
        incremental = {
            "status": "COMPARABLE",
            "tradevisor_corroborated_net_usd": corroborated.get("estimated_net_usd"),
            "tradevisor_false_positive_rate": corroborated.get("false_positive_rate"),
            "native_net_usd": native_metrics.get("estimated_net_usd"),
            "native_false_positive_rate": native_metrics.get("false_positive_rate"),
            "note": (
                "comparison is descriptive; it never promotes policy and both "
                "sides must come from matched populations"
            ),
        }

    return LearningReport(
        status=status,
        release_compatibility=release,
        cohorts=cohorts,
        incremental_value=incremental,
    )


