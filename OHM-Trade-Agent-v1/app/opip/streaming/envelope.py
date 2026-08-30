"""Canonical, versioned, deterministic streaming evidence envelope.

One StreamEnvelope represents one normalized observation (a trade or a
liquidation) from one provider connection. It is immutable once constructed;
nothing in Sequence 4 mutates a sealed envelope or a sealed window.

Identity is fail-closed: an envelope may only carry a canonical_asset_id when
the underlying mapping status is UNIQUE (app.opip.events.contract.
MappingStatus), reusing the same anti-lookahead identity semantics as
Sequence 2/3 rather than inventing a second asset-identity model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import math
from types import MappingProxyType
from typing import Any

from app.opip.events.contract import MappingStatus, parse_utc, require_utc, utc_iso
from app.opip.storage.bounded_jsonl import encode_row
from app.opip.streaming.contract import (
    STREAMING_SCHEMA_VERSION,
    SequenceStatus,
    StreamProvider,
    StreamType,
)


# Sequence statuses whose gap_before/out_of_order/duplicate flags are fixed by
# construction. An envelope built with an inconsistent combination (e.g.
# status=DUPLICATE but duplicate=False) fails closed at construction time
# rather than silently propagating a self-contradictory record.
def _deep_freeze_jsonish(value: Any) -> Any:
    """Deep-freeze JSON-like evidence so frozen envelopes are truly immutable."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("streaming envelope numeric evidence must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("streaming envelope mapping keys must be strings")
            frozen[key] = _deep_freeze_jsonish(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze_jsonish(item) for item in value)
    raise TypeError(
        f"unsupported streaming envelope payload type: {type(value).__name__}"
    )


def _deep_thaw_jsonish(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw_jsonish(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw_jsonish(item) for item in value]
    return value


_EXPECTED_FLAGS: dict[SequenceStatus, tuple[bool, bool, bool]] = {
    # (gap_before, out_of_order, duplicate)
    SequenceStatus.FIRST: (False, False, False),
    SequenceStatus.CONTIGUOUS: (False, False, False),
    SequenceStatus.DUPLICATE: (False, False, True),
    SequenceStatus.GAP: (True, False, False),
    SequenceStatus.OUT_OF_ORDER: (False, True, False),
    SequenceStatus.RESET_NEW_EPOCH: (False, False, False),
    SequenceStatus.UNSUPPORTED: (False, False, False),
}


@dataclass(frozen=True)
class StreamEnvelope:
    provider: StreamProvider
    stream_type: StreamType
    provider_symbol: str
    provider_timestamp_utc: datetime
    ingest_timestamp_utc: datetime
    connection_id: str
    reconnect_epoch: int
    sequence_status: SequenceStatus
    is_aggregate: bool
    # Fail-closed identity: canonical_asset_id is None unless identity_status
    # is UNIQUE. Ticker similarity alone is never treated as identity.
    identity_status: MappingStatus = MappingStatus.UNKNOWN
    canonical_asset_id: str | None = None
    provider_sequence: str | None = None
    gap_before: bool = False
    out_of_order: bool = False
    duplicate: bool = False
    # Free-form, provider-specific payload (raw price/qty/side strings etc.).
    # Feature modules never read this directly; they consume typed
    # TradeObservation/LiquidationObservation built from it by an adapter.
    payload: Mapping[str, Any] = field(default_factory=dict)
    quality: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = STREAMING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not str(self.provider_symbol or "").strip():
            raise ValueError("provider_symbol is required")
        if not str(self.connection_id or "").strip():
            raise ValueError("connection_id is required")
        if int(self.reconnect_epoch) < 0:
            raise ValueError("reconnect_epoch cannot be negative")
        require_utc(self.provider_timestamp_utc, field_name="provider_timestamp_utc")
        require_utc(self.ingest_timestamp_utc, field_name="ingest_timestamp_utc")
        if self.schema_version != STREAMING_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported streaming schema_version={self.schema_version}"
            )

        if self.identity_status != MappingStatus.UNIQUE and self.canonical_asset_id:
            raise ValueError(
                "canonical_asset_id may only be set when identity_status is UNIQUE"
            )
        if self.identity_status == MappingStatus.UNIQUE and not str(
            self.canonical_asset_id or ""
        ).strip():
            raise ValueError("UNIQUE identity_status requires a canonical_asset_id")

        expected = _EXPECTED_FLAGS[self.sequence_status]
        actual = (bool(self.gap_before), bool(self.out_of_order), bool(self.duplicate))
        if actual != expected:
            raise ValueError(
                "gap_before/out_of_order/duplicate flags are inconsistent with "
                f"sequence_status={self.sequence_status.value}: "
                f"expected {expected}, got {actual}"
            )

        object.__setattr__(self, "payload", _deep_freeze_jsonish(dict(self.payload)))
        object.__setattr__(self, "quality", _deep_freeze_jsonish(dict(self.quality)))

    @property
    def has_unique_identity(self) -> bool:
        return self.identity_status == MappingStatus.UNIQUE

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "stream_type": self.stream_type.value,
            "identity_status": self.identity_status.value,
            "canonical_asset_id": self.canonical_asset_id,
            "provider_symbol": self.provider_symbol,
            "provider_timestamp_utc": utc_iso(self.provider_timestamp_utc),
            "ingest_timestamp_utc": utc_iso(self.ingest_timestamp_utc),
            "connection_id": self.connection_id,
            "reconnect_epoch": int(self.reconnect_epoch),
            "provider_sequence": self.provider_sequence,
            "sequence_status": self.sequence_status.value,
            "gap_before": bool(self.gap_before),
            "out_of_order": bool(self.out_of_order),
            "duplicate": bool(self.duplicate),
            "is_aggregate": bool(self.is_aggregate),
            "quality": _deep_thaw_jsonish(self.quality),
            "payload": _deep_thaw_jsonish(self.payload),
            "schema_version": self.schema_version,
        }

    def canonical_bytes(self) -> bytes:
        """Deterministic serialization: sorted keys, stable separators.

        Reuses the same encoder Sequence 2/3 use for durable JSONL rows so
        replay/hash comparisons are consistent across every O'Pip evidence
        family, without this build persisting anything itself.
        """
        return encode_row(self.to_dict())

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StreamEnvelope":
        provider_ts = parse_utc(
            payload.get("provider_timestamp_utc"),
            field_name="provider_timestamp_utc",
        )
        ingest_ts = parse_utc(
            payload.get("ingest_timestamp_utc"),
            field_name="ingest_timestamp_utc",
        )
        if provider_ts is None or ingest_ts is None:
            raise ValueError("streaming envelope timestamps are required")
        return cls(
            provider=StreamProvider(str(payload["provider"])),
            stream_type=StreamType(str(payload["stream_type"])),
            provider_symbol=str(payload["provider_symbol"]),
            provider_timestamp_utc=provider_ts,
            ingest_timestamp_utc=ingest_ts,
            connection_id=str(payload["connection_id"]),
            reconnect_epoch=int(payload["reconnect_epoch"]),
            sequence_status=SequenceStatus(str(payload["sequence_status"])),
            is_aggregate=bool(payload["is_aggregate"]),
            identity_status=MappingStatus(
                str(payload.get("identity_status") or MappingStatus.UNKNOWN.value)
            ),
            canonical_asset_id=(
                str(payload["canonical_asset_id"])
                if payload.get("canonical_asset_id") is not None
                else None
            ),
            provider_sequence=(
                str(payload["provider_sequence"])
                if payload.get("provider_sequence") is not None
                else None
            ),
            gap_before=bool(payload.get("gap_before", False)),
            out_of_order=bool(payload.get("out_of_order", False)),
            duplicate=bool(payload.get("duplicate", False)),
            payload=dict(payload.get("payload") or {}),
            quality=dict(payload.get("quality") or {}),
            schema_version=int(payload.get("schema_version") or STREAMING_SCHEMA_VERSION),
        )
