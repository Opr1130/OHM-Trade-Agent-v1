"""Mature Discovery V2-01 forward outcomes on the learning worker.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

Reads persisted Broad Search screening rows and full-market observations,
writes joinable outcome/attribution records, and never touches ranking,
alerts, or trading authority. Failures are bounded and must not abort the
broader opportunity-intelligence cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from app.opip.decision.store import read_jsonl
from app.opip.discovery.attribution import attribution_record
from app.opip.discovery.constants import DISCOVERY_FORWARD_READ_GRACE
from app.opip.discovery.earliness import earliness_metrics
from app.opip.discovery.outcomes import label_screening_observation
from app.opip.discovery.store import (
    append_discovery_attributions,
    append_discovery_forward_outcomes,
    read_discovery_forward_outcomes,
)
from app.opip.early.point_in_time import parse_timestamp
from app.services.signal_quality_phase2 import build_timelines, read_observations


DEFAULT_DATA_ROOT = Path("/app/data")
DEFAULT_SCREENING = Path("/app/data/opip/qualification/screening_evaluations.jsonl")
DEFAULT_OBSERVATIONS = Path("/app/data/full_market_observations.jsonl")
BOUNDED_MAX_ROWS = 400


def _cursor_path(output_dir: Path) -> Path:
    return output_dir / ".discovery_outcomes.cursor.json"


def _load_cursor(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cursor(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), sort_keys=True), encoding="utf-8")


def _already_labeled(path: Path) -> set[str]:
    labeled: set[str] = set()
    for row in read_discovery_forward_outcomes(path=path, limit=FORWARD_LOOKBACK):
        obs = str(row.get("observation_id") or "")
        if obs:
            labeled.add(obs)
    return labeled


FORWARD_LOOKBACK = 20_000


def build_discovery_outcomes_bounded(
    *,
    screening_path: Path = DEFAULT_SCREENING,
    observation_path: Path = DEFAULT_OBSERVATIONS,
    output_dir: Path | None = None,
    max_rows: int = BOUNDED_MAX_ROWS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Label a bounded batch of screening rows. Never raises for missing files."""
    labeled_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    root = output_dir or Path("/app/data/opip/discovery")
    outcomes_path = root / "forward_outcomes.jsonl"
    attributions_path = root / "attributions.jsonl"
    cursor_path = _cursor_path(root)
    summary = {
        "measurement_only": True,
        "trade_authority_changed": False,
        "evaluated": 0,
        "skipped_already_labeled": 0,
        "skipped_no_identity": 0,
        "written_outcomes": 0,
        "written_attributions": 0,
    }
    if max_rows < 1:
        return summary
    screening_rows = [
        row
        for row in read_jsonl(screening_path, limit=max(max_rows * 4, 2_000))
        if str(row.get("scanner_type") or "") == "BROAD_SEARCH"
    ]
    if not screening_rows:
        return summary

    labeled = set()
    if outcomes_path.exists():
        labeled = _already_labeled(outcomes_path)
    cursor = _load_cursor(cursor_path)
    cursor_at = parse_timestamp(cursor.get("observed_at"))

    pending: list[dict[str, Any]] = []
    for row in screening_rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        obs_id = str((metadata or {}).get("observation_id") or "")
        if not obs_id:
            summary["skipped_no_identity"] += 1
            continue
        if obs_id in labeled:
            summary["skipped_already_labeled"] += 1
            continue
        observed_at = parse_timestamp(row.get("observed_at"))
        if cursor_at is not None and observed_at is not None and observed_at < cursor_at:
            continue
        pending.append(row)
        if len(pending) >= max_rows:
            break
    if not pending:
        return summary

    symbols = {
        str(row.get("venue_instrument_id") or "").upper()
        for row in pending
        if str(row.get("venue_instrument_id") or "").strip()
    }
    times = [
        parsed
        for parsed in (parse_timestamp(row.get("observed_at")) for row in pending)
        if parsed is not None
    ]
    # Decision timestamps are not the forward window. Phase 3C uses
    # max(decision_time) + grace so post-scan prints remain available.
    latest_decision = max(times) if times else labeled_at
    ingestion = read_observations(
        observation_path,
        symbols=symbols or None,
        start_at=min(times) if times else None,
        end_at=latest_decision + DISCOVERY_FORWARD_READ_GRACE,
    )
    timelines = build_timelines(ingestion.observations)

    outcome_rows: list[dict[str, Any]] = []
    attribution_rows: list[dict[str, Any]] = []
    last_observed = cursor.get("observed_at")
    by_instrument: dict[str, list[dict[str, Any]]] = {}
    for row in screening_rows:
        venue_id = str(row.get("venue_instrument_id") or "")
        if venue_id:
            by_instrument.setdefault(venue_id, []).append(row)

    for row in pending:
        venue_id = str(row.get("venue_instrument_id") or "").upper()
        timeline = timelines.get(venue_id)
        outcome = label_screening_observation(row, timeline, labeled_at=labeled_at)
        primary = (outcome.get("horizons") or {}).get("12h") or {}
        favorable_at = primary.get("favorable_barrier_at") or primary.get("mfe_at")
        favorable_price = None
        last_forward = primary.get("last_forward_price")
        if primary.get("mfe_pct") is not None and outcome.get("reference_price"):
            direction = str(outcome.get("direction") or "LONG")
            ref = float(outcome["reference_price"])
            mfe = float(primary["mfe_pct"])
            if direction == "SHORT":
                favorable_price = ref * (1.0 - mfe / 100.0)
            else:
                favorable_price = ref * (1.0 + mfe / 100.0)
        outcome["earliness"] = earliness_metrics(
            by_instrument.get(str(row.get("venue_instrument_id") or ""), []),
            direction=str(outcome.get("direction") or "LONG"),
            favorable_price=favorable_price if favorable_price else last_forward,
            favorable_at=favorable_at,
        )
        outcome_rows.append(outcome)
        attribution_rows.append(
            attribution_record(row, labeled_at=labeled_at)
        )
        last_observed = outcome.get("observed_at") or last_observed

    written_outcomes = append_discovery_forward_outcomes(
        outcome_rows, path=outcomes_path
    )
    written_attr = append_discovery_attributions(
        attribution_rows, path=attributions_path
    )
    _write_cursor(
        cursor_path,
        {"observed_at": last_observed, "evaluated_at": labeled_at.isoformat()},
    )
    summary["evaluated"] = len(pending)
    summary["written_outcomes"] = written_outcomes
    summary["written_attributions"] = written_attr
    return summary
