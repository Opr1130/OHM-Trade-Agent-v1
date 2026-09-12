"""Intent / ACK models for the canonical writer IPC."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Priority = Literal["HIGH", "NORMAL", "LOW"]
AckStatus = Literal["OK", "DUPLICATE_OK", "REJECTED", "RETRYABLE"]
EventType = Literal[
    "alert_governor.transition.recorded",
    "alert_governor.reservation.released",
    "alert_governor.capture_gap.recorded",
    # PR3 feature bus. These reuse the existing generic events and watermarks
    # tables, so no physical schema version bump is required. Names are owned
    # by app.opip.contracts.events; the literal is restated here only because
    # the type annotation cannot be computed from that frozenset.
    "market.observation.recorded",
    "market.instrument_version.recorded",
    "feature.snapshot.recorded",
    "feature.checkpoint.recorded",
    "coverage.gap.recorded",
    "feature.restart.recorded",
]
OpsOperation = Literal["RECORD", "RELEASE"]
HandoffStatus = Literal["PENDING", "APPLIED", "SUPERSEDED"]


@dataclass(frozen=True)
class WriterIntent:
    schema_version: int
    priority: Priority
    idempotency_key: str
    event_type: EventType
    payload: dict[str, Any]
    event_time: str | None = None
    causation_id: str | None = None
    correlation_id: str | None = None
    # Present for transition.recorded / reservation.released producer intents.
    ops_handoff: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WriterIntent":
        return cls(
            schema_version=int(raw["schema_version"]),
            priority=str(raw["priority"]),  # type: ignore[arg-type]
            idempotency_key=str(raw["idempotency_key"]),
            event_type=str(raw["event_type"]),  # type: ignore[arg-type]
            payload=dict(raw.get("payload") or {}),
            event_time=(str(raw["event_time"]) if raw.get("event_time") else None),
            causation_id=(str(raw["causation_id"]) if raw.get("causation_id") else None),
            correlation_id=(
                str(raw["correlation_id"]) if raw.get("correlation_id") else None
            ),
            ops_handoff=(
                dict(raw["ops_handoff"]) if isinstance(raw.get("ops_handoff"), dict) else None
            ),
        )


@dataclass(frozen=True)
class WriterAck:
    status: AckStatus
    event_id: str | None = None
    history_epoch: int | None = None
    local_sequence: int | None = None
    error_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WriterAck":
        return cls(
            status=str(raw["status"]),  # type: ignore[arg-type]
            event_id=(str(raw["event_id"]) if raw.get("event_id") else None),
            history_epoch=(
                int(raw["history_epoch"]) if raw.get("history_epoch") is not None else None
            ),
            local_sequence=(
                int(raw["local_sequence"]) if raw.get("local_sequence") is not None else None
            ),
            error_code=(str(raw["error_code"]) if raw.get("error_code") else None),
            detail=(str(raw["detail"]) if raw.get("detail") else None),
        )


@dataclass
class PendingHandoff:
    event_id: str
    operation: OpsOperation
    identity: str
    transition_key: str
    message_id: int | None
    created_new: bool
    reservation_token: str | None
    state_file: str
    status: HandoffStatus
    created_at: str
    applied_at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
