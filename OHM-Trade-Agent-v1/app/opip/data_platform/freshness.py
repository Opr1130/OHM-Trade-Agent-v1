"""Canonical O'Pip dashboard freshness policy.

``ops.dashboard_freshness_v`` (migration 005) is the canonical classification
consumed by Grafana, the API/dashboard read model, stale-data selection, and
``health --require-ready``.  This module is the single Python source of the
policy: SQL migration 005 derives its constants from these exact values and
any policy change must land here first, then be mirrored in a new migration,
with the parity test suite proving equivalence.

Classification contract (fail closed, first failing reason listed):

- ``LIVE``        required stream whose required evidence is present, valid,
                  clean, and no older than ``DEFAULT_MAX_AGE_SECONDS``.
- ``STALE``       required stream whose evidence exists but is older than the
                  default threshold while never exceeding the per-stream
                  maximum; only the per-stream threshold may mark a required
                  stream UNAVAILABLE (never LIVE).
- ``UNAVAILABLE`` required stream missing rows, typed projections, or a clean
                  reconciliation, or whose evidence is invalid or beyond its
                  per-stream threshold, plus reconciliation failures, failed
                  maintenance, configuration drift, and unknown policy rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable

from app.opip.data_platform.streams import STREAM_SPECS


#: Required streams must produce new evidence at least this often to stay LIVE.
DEFAULT_MAX_AGE_SECONDS = 3600
#: Non-required streams report LIVE/STALE against this single threshold.
NON_REQUIRED_MAX_AGE_SECONDS = 86400
#: Every evaluated timestamp must be valid, timezone-aware, and no further
#: into the future than this skew allowance; anything else fails closed.
MAX_FUTURE_SKEW_SECONDS = 300

STATUSES = ("LIVE", "STALE", "UNAVAILABLE")
MAINTENANCE_COMPONENT = "__maintenance__"

#: PostgreSQL equivalent of the per-kind typed watermark subquery in migration
#: 005 ``ops.freshness_typed_watermarks``.  Both sides must classify identical
#: inputs identically; the parity tests assert this mapping stays in sync.
TYPED_WATERMARK_SQL = (
    ("screening", "SELECT max(observed_at) FROM market.screening"),
    ("funnel", "SELECT max(occurred_at) FROM lifecycle.stage_transition"),
    ("intelligence", "SELECT max(observed_at) FROM signal.intelligence_event"),
    ("market_observation", "SELECT max(observed_at) FROM market.observation"),
    ("paper_event", "SELECT max(occurred_at) FROM paper.trade_event"),
)


def stream_threshold_seconds(spec: Any) -> int | None:
    """Return the per-stream freshness threshold in seconds.

    A required stream is LIVE at or below the default threshold and may only
    be marked STALE while below its per-stream threshold.  Non-required
    streams use the single non-required threshold.  ``None`` marks a required
    stream UNAVAILABLE.
    """
    if spec.required:
        return None
    return NON_REQUIRED_MAX_AGE_SECONDS


def stream_policy_snapshot() -> dict[str, dict[str, Any]]:
    """Canonical per-stream policy mirrored by ``ops.required_stream``."""
    return {
        spec.name: {
            "required": bool(spec.required),
            "requires_typed_projection": spec.requires_typed_projection,
            "threshold_seconds": stream_threshold_seconds(spec),
        }
        for spec in STREAM_SPECS
    }


def policy_fingerprint() -> str:
    """Stable hash of the expected policy; drift against it fails closed."""
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
    """Evidence row for one stream; mirrors one ``ops.dashboard_freshness_v`` row."""

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
    """Validate one timestamp; malformed/naive/future values fail closed.

    Malformed and naive values reach PostgreSQL as NULL and are treated as
    missing evidence here; both sides therefore classify them UNAVAILABLE.
    Materially future values (beyond the skew allowance) are invalid on both
    sides; within-skew values are clamped to a zero age on both sides.
    """
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
    """Classify one stream exactly as migration 005 classifies it in SQL."""
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
    if not item.required:
        reference = stamps["last_ingested_at"] or stamps["source_updated_at"]
        if invalid:
            return StreamAssessment(
                "UNAVAILABLE",
                "INVALID_TIMESTAMPS",
                reference,
                _age_seconds(reference, now),
                tuple(invalid),
            )
        if (
            reference is not None
            and _age_seconds(reference, now) <= NON_REQUIRED_MAX_AGE_SECONDS
        ):
            return StreamAssessment(
                "LIVE", None, reference, _age_seconds(reference, now), ()
            )
        return StreamAssessment(
            "STALE", "STALE_DATA", reference, _age_seconds(reference, now), ()
        )
    if stamps["last_ingested_at"] is None:
        return StreamAssessment("UNAVAILABLE", "MISSING_STREAM_ROW", None, None, tuple(invalid))
    if item.requires_typed_projection and stamps["typed_watermark_at"] is None:
        return StreamAssessment(
            "UNAVAILABLE", "MISSING_TYPED_PROJECTION", None, None, tuple(invalid)
        )
    if invalid:
        reference = stamps["typed_watermark_at"] or stamps["last_ingested_at"]
        return StreamAssessment(
            "UNAVAILABLE",
            "INVALID_TIMESTAMPS",
            reference,
            _age_seconds(reference, now),
            tuple(invalid),
        )
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
        return StreamAssessment(
            "UNAVAILABLE", "UNRESOLVED_DEAD_LETTERS", reference, age, ()
        )
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
    """Classify maintenance evidence exactly as migration 005 does in SQL."""
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
    """Build the canonical aggregate consumed by ``health --require-ready``."""
    maintenance = maintenance or MaintenanceInput(None, None, False)
    per_stream = {
        item.stream_name: classify_stream(item, now=now) for item in streams
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
    statuses = [assessment.status for assessment in per_stream.values()]
    statuses.append(maintenance_result.status)
    if "UNAVAILABLE" in statuses:
        overall = "UNAVAILABLE"
    elif "STALE" in statuses:
        overall = "STALE"
    else:
        overall = "LIVE"
    return {
        "status": overall,
        "ready": overall == "LIVE",
        "streams": per_stream,
        "maintenance": maintenance_result,
        "problems": problems,
        "reason": problems[0]["reason"] if problems else "OK",
    }
