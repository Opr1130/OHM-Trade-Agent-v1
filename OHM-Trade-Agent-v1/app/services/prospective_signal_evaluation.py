from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from app.services.explosion_learning import OUTCOME_FILE as EXPLOSION_OUTCOME_FILE
from app.services.explosion_precursor_learning import (
    PRECURSOR_FILE,
    precursor_outcome_rows,
)
from app.services.movement_discovery_learning_capture import DETECTION_FILE as MOVEMENT_DETECTION_FILE
from app.services.movement_discovery_outcomes import OUTCOME_FILE as MOVEMENT_OUTCOME_FILE


MIN_PROSPECTIVE_BUCKET_SAMPLES = 30
HORIZONS = ("1h", "4h", "12h", "24h")


def _read_jsonl(path: Path, record_type: str | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if record_type is None or row.get("record_type") == record_type:
                    rows.append(row)
    except OSError:
        return []
    return rows


def _metrics(rows: list[dict[str, Any]], *, minimum_samples: int) -> dict[str, Any]:
    if not rows:
        return {
            "samples": 0,
            "sufficient_samples": False,
            "avg_mfe_pct": None,
            "avg_mae_pct": None,
            "hit_5pct_rate": None,
            "hit_10pct_rate": None,
            "hit_20pct_rate": None,
        }
    mfe = [float(row.get("mfe_pct") or 0.0) for row in rows]
    mae = [float(row.get("mae_pct") or 0.0) for row in rows]
    return {
        "samples": len(rows),
        "sufficient_samples": len(rows) >= minimum_samples,
        "avg_mfe_pct": round(mean(mfe), 4),
        "avg_mae_pct": round(mean(mae), 4),
        "hit_5pct_rate": round(sum(bool(row.get("hit_5pct")) for row in rows) / len(rows), 4),
        "hit_10pct_rate": round(sum(bool(row.get("hit_10pct")) for row in rows) / len(rows), 4),
        "hit_20pct_rate": round(sum(bool(row.get("hit_20pct")) for row in rows) / len(rows), 4),
    }


def build_precursor_watch_comparison(
    *,
    outcome_file: Path | None = None,
    precursor_file: Path | None = None,
    minimum_samples: int = MIN_PROSPECTIVE_BUCKET_SAMPLES,
) -> dict[str, Any]:
    """Compare Wave 5.1 WATCH against same-cohort NO_WATCH prospectively."""
    joined = precursor_outcome_rows(
        outcome_file=outcome_file or EXPLOSION_OUTCOME_FILE,
        precursor_file=precursor_file or PRECURSOR_FILE,
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in joined:
        if str(row.get("phase") or "").upper() not in {"DORMANT", "IGNITION"}:
            continue
        horizon = str(row.get("horizon") or "")
        if horizon not in HORIZONS:
            continue
        label = "WATCH" if row.get("precursor_watch") is True else "NO_WATCH"
        grouped[(horizon, label)].append(row)

    horizons: dict[str, Any] = {}
    all_sufficient = True
    for horizon in HORIZONS:
        watch = _metrics(grouped[(horizon, "WATCH")], minimum_samples=minimum_samples)
        baseline = _metrics(grouped[(horizon, "NO_WATCH")], minimum_samples=minimum_samples)
        sufficient = watch["sufficient_samples"] and baseline["sufficient_samples"]
        all_sufficient = all_sufficient and sufficient
        comparison: dict[str, Any] = {
            "watch": watch,
            "no_watch": baseline,
            "sufficient_for_comparison": sufficient,
            "mfe_delta_pct": None,
            "hit_5_delta": None,
            "hit_10_delta": None,
        }
        if sufficient:
            comparison["mfe_delta_pct"] = round(float(watch["avg_mfe_pct"]) - float(baseline["avg_mfe_pct"]), 4)
            comparison["hit_5_delta"] = round(float(watch["hit_5pct_rate"]) - float(baseline["hit_5pct_rate"]), 4)
            comparison["hit_10_delta"] = round(float(watch["hit_10pct_rate"]) - float(baseline["hit_10pct_rate"]), 4)
        horizons[horizon] = comparison

    return {
        "status": "READY_FOR_HUMAN_REVIEW" if all_sufficient and joined else "BUILDING_EVIDENCE",
        "joined_outcome_rows": len(joined),
        "minimum_samples_per_bucket": minimum_samples,
        "same_cohort_baseline": True,
        "horizons": horizons,
        "auto_promotion_allowed": False,
        "production_thresholds_changed": False,
    }


def _liquidity_band(value: Any) -> str:
    try:
        liquidity = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if liquidity < 50_000:
        return "LT_50K"
    if liquidity < 250_000:
        return "50K_250K"
    if liquidity < 1_000_000:
        return "250K_1M"
    if liquidity < 10_000_000:
        return "1M_10M"
    return "GE_10M"


def build_movement_discovery_comparison(
    *,
    outcome_file: Path | None = None,
    detection_file: Path | None = None,
    minimum_samples: int = MIN_PROSPECTIVE_BUCKET_SAMPLES,
) -> dict[str, Any]:
    """Compare READY/eligible discovery cohorts against WATCH prospectively."""
    detections = _read_jsonl(detection_file or MOVEMENT_DETECTION_FILE, "DETECTION")
    detection_by_id = {
        str(row.get("detection_id")): row
        for row in detections
        if row.get("detection_id")
    }
    outcomes = _read_jsonl(outcome_file or MOVEMENT_OUTCOME_FILE, "OUTCOME")
    enriched: list[dict[str, Any]] = []
    for outcome in outcomes:
        detection = detection_by_id.get(str(outcome.get("detection_id") or ""), {})
        row = dict(outcome)
        for field in ("stage", "alert_eligible", "liquidity_24h_usd_approx", "entry_recommendation"):
            if row.get(field) is None and detection.get(field) is not None:
                row[field] = detection.get(field)
        enriched.append(row)

    stage_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    eligibility_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    liquidity_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in enriched:
        horizon = str(row.get("horizon") or "")
        if horizon not in HORIZONS:
            continue
        stage = str(row.get("stage") or "UNKNOWN").upper()
        stage_groups[(horizon, stage)].append(row)
        eligibility = "ALERT_ELIGIBLE" if row.get("alert_eligible") is True else "NOT_ALERT_ELIGIBLE"
        eligibility_groups[(horizon, eligibility)].append(row)
        liquidity_groups[(horizon, _liquidity_band(row.get("liquidity_24h_usd_approx")))].append(row)

    horizons: dict[str, Any] = {}
    all_sufficient = True
    for horizon in HORIZONS:
        ready = _metrics(stage_groups[(horizon, "READY")], minimum_samples=minimum_samples)
        watch = _metrics(stage_groups[(horizon, "WATCH")], minimum_samples=minimum_samples)
        eligible = _metrics(eligibility_groups[(horizon, "ALERT_ELIGIBLE")], minimum_samples=minimum_samples)
        not_eligible = _metrics(eligibility_groups[(horizon, "NOT_ALERT_ELIGIBLE")], minimum_samples=minimum_samples)
        sufficient = ready["sufficient_samples"] and watch["sufficient_samples"]
        all_sufficient = all_sufficient and sufficient
        horizons[horizon] = {
            "ready": ready,
            "watch": watch,
            "ready_vs_watch_sufficient": sufficient,
            "ready_mfe_delta_pct": (
                round(float(ready["avg_mfe_pct"]) - float(watch["avg_mfe_pct"]), 4)
                if sufficient else None
            ),
            "ready_hit_5_delta": (
                round(float(ready["hit_5pct_rate"]) - float(watch["hit_5pct_rate"]), 4)
                if sufficient else None
            ),
            "alert_eligible": eligible,
            "not_alert_eligible": not_eligible,
            "liquidity_bands": {
                band: _metrics(liquidity_groups[(horizon, band)], minimum_samples=minimum_samples)
                for band in ("LT_50K", "50K_250K", "250K_1M", "1M_10M", "GE_10M", "UNKNOWN")
                if liquidity_groups[(horizon, band)]
            },
        }

    return {
        "status": "READY_FOR_HUMAN_REVIEW" if all_sufficient and enriched else "BUILDING_EVIDENCE",
        "outcome_rows": len(enriched),
        "minimum_samples_per_bucket": minimum_samples,
        "horizons": horizons,
        "auto_promotion_allowed": False,
        "production_thresholds_changed": False,
    }
