"""Canonical O'Pip dashboard freshness policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable

from app.opip.data_platform.streams import STREAM_SPECS


DEFAULT_MAX_AGE_SECONDS = 3600
NON_REQUIRED_MAX_AGE_SECONDS = 86400
MAX_FUTURE_SKEW_SECONDS = 300

STATUSES = ("LIVE", "STALE", "UNAVAILABLE")
MAINTENANCE_COMPONENT = "__maintenance__"

TYPED_WATERMARK_SQL = (
    ("screening", "SELECT max(observed_at) FROM market.screening"),
    ("funnel", "SELECT max(occurred_at) FROM lifecycle.stage_transition"),
    ("intelligence", "SELECT max(observed_at) FROM signal.intelligence_event"),
    ("market_observation", "SELECT max(observed_at) FROM market.observation"),
    ("paper_event", "SELECT max(occurred_at) FROM paper.trade_event"),
)


def stream_threshold_seconds(spec: Any) -> int | None:
    """Return the per-stream threshold; required streams fail closed at default age."""
    if spec.required:
        return None
    return NON_REQUIRED_MAX_AGE_SECONDS


def stream_policy_snapshot() -> dict[str, dict[str, Any]]:
    """Return canonical per-stream policy mirrored by ops.required_stream."""
    return {
        spec.name: {
            "required": bool(spec.required),
            "requires_typed_projection": spec.requires_typed_projection,
            "threshold_seconds": stream_threshold_seconds(spec),
        }
        for spec in STREAM_SPECS
    }


def policy_fingerprint() -> str:
    """Return a stable hash of the expected freshness policy."""
    snapshot = {
        "default_max_age_seconds": DEFAULT_MAX_AGE_SECONDS,
        "non_required_max_age_seconds": NON_REQUIRED_MAX_AGE_SECONDS,
        "max_future_skew_seconds": MAX_FUTURE_SKEW_SECONDS,
        "maintenance_statuses": ("SUCCESS",),
        "streams": stream_policy_snapshot(),
    }
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class StreamAssessment:
    status: str
    reason: str | None
    reference_at: datetime | None
    age_seconds: float | None
    invalid_timestamps: tuple[str, ...]


@dataclass(frozen=True)
class MaintenanceAssessment:
    status: str
    reason: str | None
    configuration_drift: bool


@dataclass(frozen=True)
class StreamInput:
    stream_name: str
    required: bool
    requires_typed_projection: bool
    threshold_seconds: int | None
    source_updated_at: datetime | None
    last_ingested_at: datetime | None
    typed_watermark_at: datetime | None
    last_polled_at: datetime | None
    unresolved_dead_letters: int
    last_reconciliation_status: str | None
    last_reconciled_at: datetime | None
    policy_present: bool = True


@dataclass(frozen=True)
class MaintenanceInput:
    latest_status: str | None
    latest_finished_at: datetime | None
    configuration_drift: bool
    required: bool = True


def _timestamp_status(
    value: Any,
    field: str,
    now: datetime,
    invalid: list[str],
) -> datetime | None:
    """Validate one timestamp and mark malformed, naive, or too-future values."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        invalid.append(field)
        return None
    stamp = value.astimezone(timezone.utc)
    if (stamp - now).total_seconds() > MAX_FUTURE_SKEW_SECONDS:
        invalid.append(field)
    return stamp


def _age_seconds(stamp: datetime | None, now: datetime) -> float | None:
    if stamp is None:
        return None
    return max(0.0, (now - stamp).total_seconds())


def classify_stream(item: StreamInput, *, now: datetime) -> StreamAssessment:
    """Classify one stream according to the fail-closed freshness contract."""
    invalid: list[str] = []
    stamps = {
        field: _timestamp_status(value, field, now, invalid)
        for field, value in (
            ("source_updated_at", item.source_updated_at),
            ("last_ingested_at", item.last_ingested_at),
            ("typed_watermark_at", item.typed_watermark_at),
            ("last_polled_at", item.last_polled_at),
            ("last_reconciled_at", item.last_reconciled_at),
        )
    }

    if not item.policy_present:
        return StreamAssessment("UNAVAILABLE", "UNKNOWN_STREAM_POLICY", None, None, ())

    if invalid:
        reference = stamps["typed_watermark_at"] or stamps["last_ingested_at"] or stamps["source_updated_at"]
        return StreamAssessment(
            "UNAVAILABLE",
            "INVALID_TIMESTAMPS",
            reference,
            _age_seconds(reference, now),
            tuple(invalid),
        )

    if not item.required:
        reference = stamps["last_ingested_at"] or stamps["source_updated_at"]
        age = _age_seconds(reference, now)
        if reference is not None and age is not None and age <= NON_REQUIRED_MAX_AGE_SECONDS:
            return StreamAssessment("LIVE", None, reference, age, ())
        return StreamAssessment("STALE", "STALE_DATA", reference, age, ())

    if stamps["last_ingested_at"] is None:
        return StreamAssessment("UNAVAILABLE", "MISSING_STREAM_ROW", None, None, ())
    if item.requires_typed_projection and stamps["typed_watermark_at"] is None:
        return StreamAssessment("UNAVAILABLE", "MISSING_TYPED_PROJECTION", None, None, ())

    status = item.last_reconciliation_status
    if status == "ERROR":
        return StreamAssessment("UNAVAILABLE", "RECONCILIATION_ERROR", None, None, ())
    if status is None or status != "CLEAN":
        return StreamAssessment("UNAVAILABLE", "RECONCILIATION_UNKNOWN", None, None, ())
    if stamps["last_reconciled_at"] is None:
        return StreamAssessment("UNAVAILABLE", "MISSING_RECONCILIATION", None, None, ())

    reference = (
        stamps["typed_watermark_at"]
        if item.requires_typed_projection
        else stamps["last_ingested_at"]
    )
    age = _age_seconds(reference, now)

    if item.unresolved_dead_letters > 0:
        return StreamAssessment("UNAVAILABLE", "UNRESOLVED_DEAD_LETTERS", reference, age, ())

    assert reference is not None and age is not None
    if age > DEFAULT_MAX_AGE_SECONDS:
        threshold = item.threshold_seconds
        if threshold is None or age > threshold:
            return StreamAssessment(
                "UNAVAILABLE", "PER_STREAM_THRESHOLD_EXCEEDED", reference, age, ()
            )
        return StreamAssessment("STALE", "STALE_DATA", reference, age, ())

    return StreamAssessment("LIVE", None, reference, age, ())


def classify_maintenance(item: MaintenanceInput, *, now: datetime) -> MaintenanceAssessment:
    """Classify the latest maintenance evidence."""
    if item.configuration_drift:
        return MaintenanceAssessment("UNAVAILABLE", "CONFIGURATION_DRIFT", True)

    invalid: list[str] = []
    finished = _timestamp_status(item.latest_finished_at, "finished_at", now, invalid)
    if invalid:
        return MaintenanceAssessment("UNAVAILABLE", "INVALID_TIMESTAMPS", False)
    if item.latest_status is None:
        return MaintenanceAssessment("UNAVAILABLE", "MAINTENANCE_NEVER_RAN", False)
    if item.latest_status != "SUCCESS":
        return MaintenanceAssessment("UNAVAILABLE", "MAINTENANCE_FAILED", False)
    if finished is None:
        return MaintenanceAssessment("UNAVAILABLE", "INVALID_TIMESTAMPS", False)
    if _age_seconds(finished, now) > DEFAULT_MAX_AGE_SECONDS:
        return MaintenanceAssessment("UNAVAILABLE", "MAINTENANCE_STALE", False)
    return MaintenanceAssessment("LIVE", None, False)


def classify_freshness(
    streams: Iterable[StreamInput],
    maintenance: MaintenanceInput | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Build aggregate freshness while excluding optional streams from readiness."""
    maintenance = maintenance or MaintenanceInput(None, None, False)
    stream_items = list(streams)
    per_stream = {
        item.stream_name: classify_stream(item, now=now) for item in stream_items
    }
    maintenance_result = classify_maintenance(maintenance, now=now)

    problems: list[dict[str, str]] = [
        {"stream": name, "reason": assessment.reason}
        for name, assessment in sorted(per_stream.items())
        if assessment.reason is not None
    ]
    if maintenance_result.reason is not None:
        problems.append(
            {"stream": MAINTENANCE_COMPONENT, "reason": maintenance_result.reason}
        )

    gating_statuses = [
        per_stream[item.stream_name].status
        for item in stream_items
        if item.required or not item.policy_present
    ]
    gating_statuses.append(maintenance_result.status)

    if "UNAVAILABLE" in gating_statuses:
        overall = "UNAVAILABLE"
    elif "STALE" in gating_statuses:
        overall = "STALE"
    else:
        overall = "LIVE"

    component_status = {name: assessment.status for name, assessment in per_stream.items()}
    component_status[MAINTENANCE_COMPONENT] = maintenance_result.status

    if overall == "LIVE":
        reason = "OK"
    else:
        reason = next(
            (
                problem["reason"]
                for problem in problems
                if component_status.get(problem["stream"]) == overall
            ),
            problems[0]["reason"] if problems else "READ_UNAVAILABLE",
        )

    return {
        "status": overall,
        "ready": overall == "LIVE",
        "streams": per_stream,
        "maintenance": maintenance_result,
        "problems": problems,
        "reason": reason,
    }
