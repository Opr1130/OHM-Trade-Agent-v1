"""Canonical O'Pip event/evidence contract.

Provider time describes the world. ingest_time_utc describes when O'Pip
received the payload. normalized_at_utc describes canonicalization.
persisted_at_utc is stamped only by the durable store. Point-in-time consumers
gate exclusively on decision_visible_at_utc from a successfully persisted row.

External intelligence is evidence only. This module has no trading authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any


SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, 2}
NORMALIZER_VERSION = "opip-event-normalizer-v2"
ADAPTER_VERSION = "opip-event-adapter-v2"


class EventClass(str, Enum):
    NEWS = "NEWS"
    CATALYST = "CATALYST"


class EventType(str, Enum):
    NEWS_GENERAL = "NEWS_GENERAL"
    NEWS_LISTING = "NEWS_LISTING"
    NEWS_REGULATORY = "NEWS_REGULATORY"
    NEWS_SECURITY = "NEWS_SECURITY"
    CATALYST_GENERAL = "CATALYST_GENERAL"
    TOKEN_UNLOCK = "TOKEN_UNLOCK"
    MAINNET = "MAINNET"
    FORK = "FORK"
    LISTING = "LISTING"
    GOVERNANCE = "GOVERNANCE"
    AIRDROP = "AIRDROP"
    PARTNERSHIP = "PARTNERSHIP"
    OTHER = "OTHER"


class EventSeverity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class MappingStatus(str, Enum):
    UNIQUE = "UNIQUE"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


class IngestOutcome(str, Enum):
    NORMALIZED = "NORMALIZED"
    DUPLICATE = "DUPLICATE"
    REVISION = "REVISION"
    MALFORMED_PAYLOAD = "MALFORMED_PAYLOAD"
    MISSING_TIMESTAMP = "MISSING_TIMESTAMP"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    NORMALIZATION_ERROR = "NORMALIZATION_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"


def require_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return require_utc(value, field_name="datetime").isoformat()


def parse_utc(value: str | datetime | None, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return require_utc(value, field_name=field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid ISO-8601 timestamp") from exc
    return require_utc(parsed, field_name=field_name)


def stable_payload_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_event_id(dedupe_key: str, payload_hash: str) -> str:
    material = f"{dedupe_key}\n{payload_hash}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class EventIdentity:
    source_symbol: str | None = None
    source_name: str | None = None
    provider_asset_id: str | None = None
    canonical_asset_id: str | None = None
    canonical_asset_name: str | None = None
    mapping_status: MappingStatus = MappingStatus.UNKNOWN
    mapping_confidence: float | None = None
    identity_learned_at_utc: datetime | None = None
    mapping_provenance: str | None = None
    venue: str | None = None
    venue_symbol: str | None = None
    instrument_type: str | None = None
    chain_id: str | None = None
    contract_address: str | None = None
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.identity_learned_at_utc is not None:
            require_utc(
                self.identity_learned_at_utc,
                field_name="identity_learned_at_utc",
            )
        if self.mapping_confidence is not None and not (
            0.0 <= float(self.mapping_confidence) <= 1.0
        ):
            raise ValueError("mapping_confidence must be between 0 and 1")
        if self.mapping_status == MappingStatus.UNIQUE:
            if (
                not str(self.canonical_asset_id or "").strip()
                or not str(self.canonical_asset_name or "").strip()
                or self.identity_learned_at_utc is None
            ):
                raise ValueError(
                    "UNIQUE identity requires canonical id/name and knowledge time"
                )
        if bool(self.chain_id) != bool(self.contract_address):
            raise ValueError(
                "on-chain identity requires both chain_id and contract_address"
            )


@dataclass(frozen=True)
class EventProvenance:
    provider: str
    provider_event_id: str | None
    provider_asset_id: str | None
    source_reference: str | None
    source_sequence: str | None
    canonical_payload_hash: str
    source_payload_hash: str
    adapter_version: str = ADAPTER_VERSION

    def __post_init__(self) -> None:
        if not str(self.provider or "").strip():
            raise ValueError("provenance.provider is required")
        if not str(self.canonical_payload_hash or "").strip():
            raise ValueError("provenance.canonical_payload_hash is required")
        if not str(self.source_payload_hash or "").strip():
            raise ValueError("provenance.source_payload_hash is required")
        if not str(self.adapter_version or "").strip():
            raise ValueError("provenance.adapter_version is required")


@dataclass(frozen=True)
class OPipEvent:
    event_id: str
    dedupe_key: str
    provider: str
    provider_event_id: str | None
    event_class: EventClass
    payload_hash: str
    source_event_time_utc: datetime
    ingest_time_utc: datetime
    normalized_at_utc: datetime
    identity: EventIdentity
    headline: str
    event_type: EventType = EventType.OTHER
    severity: EventSeverity = EventSeverity.INFO
    provenance: EventProvenance | None = None
    source_sequence: str | None = None
    summary: str | None = None
    source_reference: str | None = None
    # Sanitized provider-specific evidence needed for future audit/replay.
    # Secrets and credentials must never be placed here.
    source_metadata: dict[str, Any] = field(default_factory=dict)
    numeric: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    expires_at_utc: datetime | None = None
    persisted_at_utc: datetime | None = None
    decision_visible_at_utc: datetime | None = None
    revision_of: str | None = None
    schema_version: int = SCHEMA_VERSION
    normalizer_version: str = NORMALIZER_VERSION

    def __post_init__(self) -> None:
        for name in ("event_id", "dedupe_key", "provider", "payload_hash"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported O'Pip event schema_version={self.schema_version}"
            )
        if self.schema_version >= 2 and self.provenance is None:
            raise ValueError("schema v2 events require canonical provenance")
        if self.provenance is not None:
            if self.provenance.provider != self.provider:
                raise ValueError("event provider must match provenance.provider")
            if self.provenance.provider_event_id != self.provider_event_id:
                raise ValueError(
                    "provider_event_id must match provenance.provider_event_id"
                )
            if self.provenance.canonical_payload_hash != self.payload_hash:
                raise ValueError(
                    "payload_hash must match provenance.canonical_payload_hash"
                )
        if not str(self.normalizer_version or "").strip():
            raise ValueError("normalizer_version is required")
        for name in (
            "source_event_time_utc",
            "ingest_time_utc",
            "normalized_at_utc",
        ):
            require_utc(getattr(self, name), field_name=name)
        for name in (
            "expires_at_utc",
            "persisted_at_utc",
            "decision_visible_at_utc",
        ):
            value = getattr(self, name)
            if value is not None:
                require_utc(value, field_name=name)
        if self.normalized_at_utc < self.ingest_time_utc:
            raise ValueError("normalized_at_utc cannot precede ingest_time_utc")
        if self.persisted_at_utc is None and self.decision_visible_at_utc is not None:
            raise ValueError(
                "decision_visible_at_utc cannot exist before successful persistence"
            )
        if self.persisted_at_utc is not None:
            expected = max(
                self.ingest_time_utc,
                self.normalized_at_utc,
                self.persisted_at_utc,
            )
            if self.decision_visible_at_utc != expected:
                raise ValueError(
                    "decision_visible_at_utc must equal max(ingest, normalized, persisted)"
                )
        if self.identity.identity_learned_at_utc is not None:
            if (
                self.identity.mapping_status == MappingStatus.UNIQUE
                and self.identity.identity_learned_at_utc > self.ingest_time_utc
            ):
                raise ValueError(
                    "identity learned after ingestion cannot resolve this event"
                )

    def with_persistence(self, persisted_at: datetime) -> "OPipEvent":
        persisted = require_utc(persisted_at, field_name="persisted_at_utc")
        if persisted < self.normalized_at_utc:
            raise ValueError("persisted_at_utc cannot precede normalized_at_utc")
        visible = max(self.ingest_time_utc, self.normalized_at_utc, persisted)
        return replace(
            self,
            persisted_at_utc=persisted,
            decision_visible_at_utc=visible,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event_class"] = self.event_class.value
        payload["event_type"] = self.event_type.value
        payload["severity"] = self.severity.value
        payload["identity"]["mapping_status"] = self.identity.mapping_status.value
        for key in (
            "source_event_time_utc",
            "ingest_time_utc",
            "normalized_at_utc",
            "expires_at_utc",
            "persisted_at_utc",
            "decision_visible_at_utc",
        ):
            payload[key] = utc_iso(getattr(self, key))
        payload["identity"]["identity_learned_at_utc"] = utc_iso(
            self.identity.identity_learned_at_utc
        )
        payload["identity"]["aliases"] = list(self.identity.aliases)
        payload["warnings"] = list(self.warnings)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OPipEvent":
        identity_raw = payload.get("identity")
        if not isinstance(identity_raw, dict):
            raise ValueError("event identity must be an object")
        identity = EventIdentity(
            source_symbol=identity_raw.get("source_symbol"),
            source_name=identity_raw.get("source_name"),
            provider_asset_id=identity_raw.get("provider_asset_id"),
            canonical_asset_id=identity_raw.get("canonical_asset_id"),
            canonical_asset_name=identity_raw.get("canonical_asset_name"),
            mapping_status=MappingStatus(
                str(identity_raw.get("mapping_status") or MappingStatus.UNKNOWN.value)
            ),
            mapping_confidence=identity_raw.get("mapping_confidence"),
            identity_learned_at_utc=parse_utc(
                identity_raw.get("identity_learned_at_utc"),
                field_name="identity.identity_learned_at_utc",
            ),
            mapping_provenance=identity_raw.get("mapping_provenance"),
            venue=identity_raw.get("venue"),
            venue_symbol=identity_raw.get("venue_symbol"),
            instrument_type=identity_raw.get("instrument_type"),
            chain_id=identity_raw.get("chain_id"),
            contract_address=identity_raw.get("contract_address"),
            aliases=tuple(
                str(item)
                for item in (identity_raw.get("aliases") or [])
                if str(item).strip()
            ),
        )
        source_event_time = parse_utc(
            payload["source_event_time_utc"],
            field_name="source_event_time_utc",
        )
        ingest_time = parse_utc(
            payload["ingest_time_utc"],
            field_name="ingest_time_utc",
        )
        normalized_at = parse_utc(
            payload["normalized_at_utc"],
            field_name="normalized_at_utc",
        )
        if source_event_time is None or ingest_time is None or normalized_at is None:
            raise ValueError("required event timestamps cannot be null")
        schema_version = int(
            payload["schema_version"]
            if "schema_version" in payload
            else 1
        )
        provenance_raw = payload.get("provenance")
        provenance = None
        if isinstance(provenance_raw, dict):
            provenance = EventProvenance(
                provider=str(provenance_raw.get("provider") or payload["provider"]),
                provider_event_id=(
                    str(provenance_raw["provider_event_id"])
                    if provenance_raw.get("provider_event_id") is not None
                    else None
                ),
                provider_asset_id=(
                    str(provenance_raw["provider_asset_id"])
                    if provenance_raw.get("provider_asset_id") is not None
                    else None
                ),
                source_reference=(
                    str(provenance_raw["source_reference"])
                    if provenance_raw.get("source_reference") is not None
                    else None
                ),
                source_sequence=(
                    str(provenance_raw["source_sequence"])
                    if provenance_raw.get("source_sequence") is not None
                    else None
                ),
                canonical_payload_hash=str(
                    provenance_raw.get("canonical_payload_hash")
                    or provenance_raw.get("raw_payload_hash")
                    or payload["payload_hash"]
                ),
                source_payload_hash=str(
                    provenance_raw.get("source_payload_hash")
                    or provenance_raw.get("raw_payload_hash")
                    or payload["payload_hash"]
                ),
                adapter_version=str(
                    provenance_raw.get("adapter_version")
                    or ADAPTER_VERSION
                ),
            )

        return cls(
            event_id=str(payload["event_id"]),
            dedupe_key=str(payload["dedupe_key"]),
            provider=str(payload["provider"]),
            provider_event_id=(
                str(payload["provider_event_id"])
                if payload.get("provider_event_id") is not None
                else None
            ),
            source_sequence=(
                str(payload["source_sequence"])
                if payload.get("source_sequence") is not None
                else None
            ),
            event_class=EventClass(str(payload["event_class"])),
            payload_hash=str(payload["payload_hash"]),
            source_event_time_utc=source_event_time,
            ingest_time_utc=ingest_time,
            normalized_at_utc=normalized_at,
            identity=identity,
            headline=str(payload.get("headline") or ""),
            event_type=EventType(
                str(
                    payload.get("event_type")
                    or (
                        EventType.NEWS_GENERAL.value
                        if str(payload.get("event_class")) == EventClass.NEWS.value
                        else EventType.CATALYST_GENERAL.value
                    )
                )
            ),
            severity=EventSeverity(
                str(payload.get("severity") or EventSeverity.INFO.value)
            ),
            provenance=provenance,
            summary=(
                str(payload["summary"]) if payload.get("summary") is not None else None
            ),
            source_reference=(
                str(payload["source_reference"])
                if payload.get("source_reference") is not None
                else None
            ),
            source_metadata=(
                dict(payload.get("source_metadata") or {})
                if isinstance(payload.get("source_metadata") or {}, dict)
                else {}
            ),
            numeric=(
                dict(payload.get("numeric") or {})
                if isinstance(payload.get("numeric") or {}, dict)
                else {}
            ),
            warnings=tuple(str(item) for item in (payload.get("warnings") or [])),
            expires_at_utc=parse_utc(
                payload.get("expires_at_utc"),
                field_name="expires_at_utc",
            ),
            persisted_at_utc=parse_utc(
                payload.get("persisted_at_utc"),
                field_name="persisted_at_utc",
            ),
            decision_visible_at_utc=parse_utc(
                payload.get("decision_visible_at_utc"),
                field_name="decision_visible_at_utc",
            ),
            revision_of=(
                str(payload["revision_of"])
                if payload.get("revision_of") is not None
                else None
            ),
            schema_version=schema_version,
            normalizer_version=str(
                payload["normalizer_version"]
                if "normalizer_version" in payload
                else NORMALIZER_VERSION
            ),
        )
