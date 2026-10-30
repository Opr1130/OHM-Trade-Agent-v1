"""Promote a verified acquisition manifest into qualification (R07).

The isolated worker produces data; it does not produce decisions. This module is
the only seam that turns acquisition evidence into something the existing
qualification path may consume, and it refuses anything it cannot fully verify:
identity, fence, freshness, coverage and snapshot hashes.

Nothing here ranks, sizes, reviews or notifies. Those authorities stay exactly
where they already are.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from app.scanner.models import MarketSnapshot
from app.services.scan_manifest import (
    ManifestError,
    is_complete,
    read_manifest,
    sha256_path,
)
from app.services.scan_supervisor import attempt_evidence_dir, state_dir


SNAPSHOTS_NAME = "snapshots.jsonl"
PROMOTION_NAME = "promotion.json"


class PromotionRejected(RuntimeError):
    """Acquisition evidence may not be consumed."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(self.reason)


@dataclass(frozen=True)
class PromotedScan:
    """The verified result of one acquisition attempt."""

    attempt_id: str
    fence: int
    snapshots: list[MarketSnapshot]
    coverage: Mapping[str, Any]
    manifest: Mapping[str, Any]
    promoted_at: datetime
    degraded_fields: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return is_complete(self.manifest)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _nested_dataclass(field_name: str) -> type | None:
    """Resolve a known nested snapshot type, or None when not reconstructible."""
    try:
        if field_name == "market_data_validation":
            from app.scanner.market_data_validation import MarketDataValidation

            return MarketDataValidation
        if field_name == "execution_validation":
            from app.scanner.execution_validation import ExecutionValidation

            return ExecutionValidation
        if field_name == "independent_market_reference":
            from app.scanner.reference_market_validation import ReferenceMarketValidation

            return ReferenceMarketValidation
        if field_name == "news_context":
            from app.scanner.news_context import NewsContext

            return NewsContext
        if field_name == "scheduled_catalyst_context":
            from app.scanner.scheduled_catalysts import ScheduledCatalystContext

            return ScheduledCatalystContext
    except Exception:  # noqa: BLE001 - absence is reported, never guessed
        return None
    return None


def _rebuild_snapshot(payload: Mapping[str, Any]) -> tuple[MarketSnapshot, list[str]]:
    """Rebuild one snapshot, never passing a dict where a dataclass belongs."""
    known = {field.name: field for field in fields(MarketSnapshot)}
    kwargs: dict[str, Any] = {}
    degraded: list[str] = []
    for name, value in payload.items():
        if name not in known:
            continue
        if value is not None and is_dataclass(known[name].type) is False and isinstance(
            value, dict
        ):
            target = _nested_dataclass(name)
            if target is None:
                degraded.append(name)
                continue
            try:
                kwargs[name] = target(**value)
            except TypeError:
                degraded.append(name)
            continue
        kwargs[name] = value
    try:
        return MarketSnapshot(**kwargs), degraded
    except TypeError as exc:
        raise PromotionRejected(f"SNAPSHOT_SHAPE_INVALID:{exc}") from exc


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_promoted(
    *,
    settings: Any,
    attempt: Any,
    now: datetime | None = None,
    require_complete: bool = True,
) -> PromotedScan:
    """Verify and load one attempt's acquisition result, or reject it."""
    moment = now or _now()
    if not getattr(attempt, "terminal", False):
        raise PromotionRejected("ATTEMPT_NOT_TERMINAL")
    if getattr(attempt, "outcome", None) not in {"COMPLETED", "PARTIAL"}:
        raise PromotionRejected(f"ATTEMPT_OUTCOME_{getattr(attempt, 'outcome', 'UNKNOWN')}")

    evidence_dir = attempt_evidence_dir(settings, attempt.attempt_id)
    try:
        manifest = read_manifest(evidence_dir)
    except ManifestError as exc:
        raise PromotionRejected(f"MANIFEST_{exc.reason}") from exc

    # Identity: the manifest must describe this exact attempt and fence.
    if str(manifest.get("attempt_id") or "") != str(attempt.attempt_id):
        raise PromotionRejected("MANIFEST_IDENTITY_MISMATCH")
    if int(manifest.get("fence") or 0) != int(getattr(attempt, "fence", 0)):
        raise PromotionRejected("MANIFEST_FENCE_MISMATCH")

    # Timestamps: a scan that is older than the freshness bound may not authorise
    # anything, however complete it looks.
    written = _parse_time(manifest.get("written_at"))
    if written is None:
        raise PromotionRejected("MANIFEST_TIMESTAMP_MISSING")
    max_age = timedelta(
        seconds=2 * int(getattr(settings, "signal_quality_scan_interval_seconds", 600))
    )
    if moment - written > max_age:
        raise PromotionRejected("MANIFEST_STALE")
    if written - moment > timedelta(minutes=5):
        raise PromotionRejected("MANIFEST_FROM_THE_FUTURE")

    # Consumption requires complete coverage. A partial acquisition stays
    # readable as evidence, but it may never be promoted into qualification.
    if require_complete and not is_complete(manifest):
        raise PromotionRejected("COVERAGE_INCOMPLETE")

    snapshots_meta = manifest.get("snapshots")
    if not isinstance(snapshots_meta, Mapping):
        raise PromotionRejected("MANIFEST_SNAPSHOTS_MISSING")
    snapshots_path = evidence_dir / str(snapshots_meta.get("file") or SNAPSHOTS_NAME)
    if not snapshots_path.exists():
        raise PromotionRejected("SNAPSHOTS_ABSENT")
    if str(snapshots_meta.get("sha256") or "") != sha256_path(snapshots_path):
        raise PromotionRejected("SNAPSHOTS_CHECKSUM_MISMATCH")

    snapshots: list[MarketSnapshot] = []
    degraded: list[str] = []
    with snapshots_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PromotionRejected("SNAPSHOTS_UNPARSEABLE") from exc
            if not isinstance(payload, dict):
                raise PromotionRejected("SNAPSHOTS_UNPARSEABLE")
            snapshot, fields_degraded = _rebuild_snapshot(payload)
            snapshots.append(snapshot)
            degraded.extend(fields_degraded)

    declared_rows = int(snapshots_meta.get("rows") or 0)
    if declared_rows != len(snapshots):
        raise PromotionRejected("SNAPSHOTS_ROW_COUNT_MISMATCH")
    coverage = manifest.get("coverage")
    if not isinstance(coverage, Mapping):
        raise PromotionRejected("MANIFEST_COVERAGE_MISSING")
    if int(coverage.get("analyzed") or 0) != len(snapshots):
        raise PromotionRejected("COVERAGE_SNAPSHOT_DISAGREEMENT")

    return PromotedScan(
        attempt_id=attempt.attempt_id,
        fence=int(getattr(attempt, "fence", 0)),
        snapshots=snapshots,
        coverage=dict(coverage),
        manifest=dict(manifest),
        promoted_at=moment,
        degraded_fields=tuple(sorted(set(degraded))),
    )


def promotion_marker(settings: Any, attempt_id: str) -> Path:
    return attempt_evidence_dir(settings, attempt_id) / PROMOTION_NAME


def to_scan_result(promoted: PromotedScan) -> Any:
    """Adapt a promoted scan to the existing ``ScanResult`` shape.

    ``universe`` is intentionally None: universe descriptors are acquisition
    metadata, and the qualification path already treats a missing universe as
    "not available" rather than as an empty one.
    """
    from app.scanner.market_scanner import ScanResult

    coverage = promoted.coverage
    return ScanResult(
        snapshots=list(promoted.snapshots),
        requested=int(coverage.get("requested") or 0),
        analyzed=int(coverage.get("analyzed") or 0),
        skipped=int(coverage.get("skipped") or 0),
        failed=int(coverage.get("failed") or 0),
        skips=[],
        failures=[],
        universe=None,
    )


def already_promoted(settings: Any, attempt_id: str) -> bool:
    return promotion_marker(settings, attempt_id).exists()


def record_promotion(
    settings: Any,
    attempt_id: str,
    *,
    qualified: bool,
    actionable: bool,
    reason_code: str | None = None,
    degraded_fields: tuple[str, ...] = (),
    now: datetime | None = None,
) -> Path:
    """Persist the one-time promotion disposition for an attempt."""
    path = promotion_marker(settings, attempt_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "qualified": bool(qualified),
        "actionable": bool(actionable),
        "reason_code": reason_code,
        "degraded_fields": list(degraded_fields),
        "recorded_at": (now or _now()).isoformat(),
    }
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temp.replace(path)
    return path

