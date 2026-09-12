"""Canonical persistence for feature-bus evidence (PR3 slice F).

Ruling D2: SQLite through the canonical writer is the authoritative store for
feature-bus evidence. PR3 does not introduce a second intermediate truth system
that PR4 would then have to migrate. Bounded JSONL survives here in exactly one
role — a non-authoritative diagnostic spool for writes the writer refused —
and it is never read back as truth.

Ruling D1: no physical schema change. These events reuse the existing generic
``events`` table and the existing ``watermarks`` mechanism, on their own stream.
The only writer changes are an additive accepted-event-type set and validation
that feature-bus traffic stays LOW priority with no operational handoff.

Capture is off by default and fails closed: it requires both
``OPIP_FEATURE_BUS_MODE=shadow`` and ``OPIP_CANONICAL_WRITER_MODE=shadow``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.opip.canonical.bridge import resolve_writer_mode
from app.opip.canonical.client import CanonicalWriterClient, WriterClient
from app.opip.canonical.models import WriterAck, WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION, canonical_dir
from app.opip.canonical.writer import MAX_PAYLOAD_BYTES
from app.opip.contracts.events import (
    COVERAGE_GAP_RECORDED,
    FEATURE_BUS_PRIORITY,
    FEATURE_CHECKPOINT_RECORDED,
    FEATURE_RESTART_RECORDED,
    FEATURE_SNAPSHOT_RECORDED,
    MARKET_OBSERVATION_RECORDED,
    checkpoint_idempotency_key,
    coverage_gap_idempotency_key,
    observation_idempotency_key,
    restart_idempotency_key,
    snapshot_idempotency_key,
)
from app.opip.contracts.features import FeatureSnapshot, FeatureStateCheckpoint
from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.contracts.observation import Observation
from app.opip.contracts.serialization import canonical_json_bytes, iso_z
from app.opip.market.aggregates import CoverageGap
from app.opip.storage.bounded_jsonl import BoundedJsonlArchive, encode_row, parse_json_object_line

logger = logging.getLogger(__name__)

FEATURE_BUS_SPOOL_PREFIX = "feature-bus-rejected"
FEATURE_BUS_SPOOL_MAX_BYTES = 4 * 1024 * 1024
FEATURE_BUS_SPOOL_KEEP_LINES = 2_000

_client_override: WriterClient | None = None


def set_writer_client_for_tests(client: WriterClient | None) -> None:
    global _client_override
    _client_override = client


def resolve_feature_bus_mode(settings: Any | None = None) -> str:
    if settings is not None:
        mode = str(getattr(settings, "opip_feature_bus_mode", "off") or "off")
    else:
        try:
            from app.core.config import get_settings

            mode = str(get_settings().opip_feature_bus_mode or "off")
        except Exception:
            mode = "off"
    mode = mode.strip().lower()
    return mode if mode in {"off", "shadow"} else "off"


def feature_bus_capture_enabled(settings: Any | None = None) -> bool:
    """Both gates must be shadow. Either being off means no capture."""
    return (
        resolve_feature_bus_mode(settings) == "shadow"
        and resolve_writer_mode(settings) == "shadow"
    )


@dataclass(frozen=True)
class PublishOutcome:
    """What happened to one feature-bus write, always explicitly."""

    event_type: str
    idempotency_key: str
    status: str
    event_id: str | None = None
    watermark: ConsumedInputWatermark | None = None
    error_code: str | None = None
    payload_bytes: int = 0

    @property
    def committed(self) -> bool:
        return self.status in {"OK", "DUPLICATE_OK"}


def _payload_size(payload: Mapping[str, Any]) -> int:
    return len(canonical_json_bytes(payload))


def build_intent(
    *,
    event_type: str,
    idempotency_key: str,
    payload: Mapping[str, Any],
    event_time: datetime | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> WriterIntent:
    """Construct a feature-bus intent. Always LOW priority, never a handoff."""
    body = dict(payload)
    size = _payload_size(body)
    if size > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"{event_type} payload is {size} bytes, above the "
            f"{MAX_PAYLOAD_BYTES} byte canonical writer bound"
        )
    return WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority=FEATURE_BUS_PRIORITY,
        idempotency_key=idempotency_key,
        event_type=event_type,  # type: ignore[arg-type]
        payload=body,
        event_time=iso_z(event_time) if event_time is not None else None,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def observation_intent(observation: Observation) -> WriterIntent:
    return build_intent(
        event_type=MARKET_OBSERVATION_RECORDED,
        idempotency_key=observation_idempotency_key(observation),
        payload=observation.to_dict(),
        event_time=observation.source_event_time,
    )


def snapshot_intent(snapshot: FeatureSnapshot) -> WriterIntent:
    return build_intent(
        event_type=FEATURE_SNAPSHOT_RECORDED,
        idempotency_key=snapshot_idempotency_key(snapshot),
        payload=snapshot.to_dict(),
        event_time=snapshot.evaluation_cutoff,
        correlation_id=snapshot.instrument_version_id,
    )


def checkpoint_intent(checkpoint: FeatureStateCheckpoint) -> WriterIntent:
    return build_intent(
        event_type=FEATURE_CHECKPOINT_RECORDED,
        idempotency_key=checkpoint_idempotency_key(checkpoint),
        payload=checkpoint.to_dict(),
        event_time=checkpoint.created_at_utc,
        correlation_id=checkpoint.instrument_version_id,
    )


def coverage_gap_intent(
    gap: CoverageGap,
    *,
    instrument_version_id: str,
    venue_instrument_id: str,
    detected_at_utc: datetime,
) -> WriterIntent:
    payload = {
        "record_type": "CoverageGap",
        "schema_version": SCHEMA_VERSION,
        "instrument_version_id": instrument_version_id,
        "venue_instrument_id": venue_instrument_id,
        "detected_at_utc": iso_z(detected_at_utc),
        **gap.to_dict(),
    }
    return build_intent(
        event_type=COVERAGE_GAP_RECORDED,
        idempotency_key=coverage_gap_idempotency_key(
            instrument_version_id=instrument_version_id,
            aggregate_interval_seconds=gap.interval_seconds,
            first_missing_utc=gap.first_missing_utc,
            missing_interval_count=gap.missing_interval_count,
        ),
        payload=payload,
        event_time=gap.first_missing_utc,
        correlation_id=instrument_version_id,
    )


def restart_intent(
    disposition: Mapping[str, Any],
    *,
    watermark: ConsumedInputWatermark,
    recorded_at_utc: datetime,
) -> WriterIntent:
    payload = {
        "record_type": "FeatureRestart",
        "schema_version": SCHEMA_VERSION,
        "recorded_at_utc": iso_z(recorded_at_utc),
        **dict(disposition),
    }
    return build_intent(
        event_type=FEATURE_RESTART_RECORDED,
        idempotency_key=restart_idempotency_key(
            instrument_version_id=str(disposition["instrument_version_id"]),
            feature_version=str(disposition["feature_version"]),
            restart_state=str(disposition["restart_state"]),
            watermark=watermark,
        ),
        payload=payload,
        event_time=recorded_at_utc,
        correlation_id=str(disposition["instrument_version_id"]),
    )


def _spool_path(base: Path | None = None) -> Path:
    return Path(base or canonical_dir()) / "feature_bus_rejected.jsonl"


class FeatureBusPublisher:
    """Writes feature-bus evidence through the canonical writer.

    When capture is disabled every call is a recorded no-op rather than a silent
    one, so a disabled run is distinguishable from a run that wrote nothing
    because nothing happened.
    """

    def __init__(
        self,
        client: WriterClient | None = None,
        *,
        settings: Any | None = None,
        spool_dir: Path | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._spool_dir = spool_dir
        self._enabled = (
            bool(enabled)
            if enabled is not None
            else feature_bus_capture_enabled(settings)
        )
        self.outcomes: list[PublishOutcome] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _resolve_client(self) -> WriterClient:
        if self._client is not None:
            return self._client
        if _client_override is not None:
            return _client_override
        return CanonicalWriterClient()

    def _archive(self) -> BoundedJsonlArchive:
        path = _spool_path(self._spool_dir)
        return BoundedJsonlArchive(
            data_file=path,
            archive_dir=path.parent / "feature_bus_rejected_archive",
            max_bytes=FEATURE_BUS_SPOOL_MAX_BYTES,
            keep_lines=FEATURE_BUS_SPOOL_KEEP_LINES,
            archive_prefix=FEATURE_BUS_SPOOL_PREFIX,
            parse_line=parse_json_object_line,
        )

    def _spool(self, intent: WriterIntent, *, reason: str) -> None:
        """Diagnostic only. Never read back as authoritative evidence."""
        try:
            self._archive().append_encoded_locked(
                encode_row(
                    {
                        "event_type": intent.event_type,
                        "idempotency_key": intent.idempotency_key,
                        "reason": reason,
                        "payload": intent.payload,
                    }
                )
            )
        except Exception:  # pragma: no cover - spooling must never raise
            logger.warning(
                "feature bus diagnostic spool failed for %s", intent.idempotency_key
            )

    def publish(self, intent: WriterIntent) -> PublishOutcome:
        size = _payload_size(intent.payload)
        if not self._enabled:
            outcome = PublishOutcome(
                event_type=str(intent.event_type),
                idempotency_key=intent.idempotency_key,
                status="DISABLED",
                payload_bytes=size,
            )
            self.outcomes.append(outcome)
            return outcome
        try:
            ack: WriterAck = self._resolve_client().submit(intent)
        except Exception as exc:
            self._spool(intent, reason=f"submit_failed:{type(exc).__name__}")
            outcome = PublishOutcome(
                event_type=str(intent.event_type),
                idempotency_key=intent.idempotency_key,
                status="SPOOLED",
                error_code=type(exc).__name__,
                payload_bytes=size,
            )
            self.outcomes.append(outcome)
            return outcome

        watermark: ConsumedInputWatermark | None = None
        if ack.history_epoch is not None and ack.local_sequence is not None:
            watermark = ConsumedInputWatermark(
                history_epoch=int(ack.history_epoch),
                local_sequence=int(ack.local_sequence),
            )
        if ack.status == "REJECTED":
            self._spool(intent, reason=f"rejected:{ack.error_code}")
        outcome = PublishOutcome(
            event_type=str(intent.event_type),
            idempotency_key=intent.idempotency_key,
            status=str(ack.status),
            event_id=ack.event_id,
            watermark=watermark,
            error_code=ack.error_code,
            payload_bytes=size,
        )
        self.outcomes.append(outcome)
        return outcome

    def publish_observations(
        self, observations: Sequence[Observation]
    ) -> list[PublishOutcome]:
        return [self.publish(observation_intent(item)) for item in observations]

    def publish_snapshot(self, snapshot: FeatureSnapshot) -> PublishOutcome:
        return self.publish(snapshot_intent(snapshot))

    def publish_checkpoint(
        self, checkpoint: FeatureStateCheckpoint
    ) -> PublishOutcome:
        return self.publish(checkpoint_intent(checkpoint))

    def publish_coverage_gaps(
        self,
        gaps: Sequence[CoverageGap],
        *,
        instrument_version_id: str,
        venue_instrument_id: str,
        detected_at_utc: datetime,
    ) -> list[PublishOutcome]:
        return [
            self.publish(
                coverage_gap_intent(
                    gap,
                    instrument_version_id=instrument_version_id,
                    venue_instrument_id=venue_instrument_id,
                    detected_at_utc=detected_at_utc,
                )
            )
            for gap in gaps
        ]

    def publish_restart(
        self,
        disposition: Mapping[str, Any],
        *,
        watermark: ConsumedInputWatermark,
        recorded_at_utc: datetime,
    ) -> PublishOutcome:
        return self.publish(
            restart_intent(
                disposition, watermark=watermark, recorded_at_utc=recorded_at_utc
            )
        )

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        return dict(sorted(counts.items()))


__all__ = [
    "FEATURE_BUS_SPOOL_PREFIX",
    "FeatureBusPublisher",
    "PublishOutcome",
    "build_intent",
    "checkpoint_intent",
    "coverage_gap_intent",
    "feature_bus_capture_enabled",
    "observation_intent",
    "resolve_feature_bus_mode",
    "restart_intent",
    "set_writer_client_for_tests",
    "snapshot_intent",
]
