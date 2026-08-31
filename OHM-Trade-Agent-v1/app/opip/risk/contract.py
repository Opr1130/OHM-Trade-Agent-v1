"""Canonical O'Pip Event Risk Shield contract (Sequence 3).

The shield produces deterministic human-review states only. It has no order,
trade-entry, ranking, or exchange authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import hashlib
from typing import Any

from app.opip.events.contract import (
    EventClass,
    EventSeverity,
    EventType,
    parse_utc,
    require_utc,
    stable_payload_hash,
    utc_iso,
)


SCHEMA_VERSION = 1
POLICY_VERSION = "opip-event-risk-policy-v1"


class RiskState(str, Enum):
    NONE = "NONE"
    WATCH = "WATCH"
    AVOID_NEW_ENTRY = "AVOID_NEW_ENTRY"
    PROTECT_REVIEW = "PROTECT_REVIEW"
    EXIT_REVIEW = "EXIT_REVIEW"


RISK_STATE_RANK: dict[RiskState, int] = {
    RiskState.NONE: 0,
    RiskState.WATCH: 1,
    RiskState.AVOID_NEW_ENTRY: 2,
    RiskState.PROTECT_REVIEW: 3,
    RiskState.EXIT_REVIEW: 4,
}


def risk_state_rank(state: RiskState) -> int:
    return RISK_STATE_RANK[state]


class ExposureFamily(str, Enum):
    REAL_ADVISORY = "REAL_ADVISORY"
    PAPER = "PAPER"


class ExposureState(str, Enum):
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    CLOSED = "CLOSED"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Relevance(str, Enum):
    DIRECT_ASSET = "DIRECT_ASSET"
    ECOSYSTEM = "ECOSYSTEM"
    VENUE = "VENUE"
    MARKET_WIDE = "MARKET_WIDE"
    MACRO = "MACRO"
    UNRELATED = "UNRELATED"


class EvidenceConfidence(str, Enum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ExposureView:
    """Immutable point-in-time exposure view used by deterministic policy."""

    exposure_id: str
    exposure_family: ExposureFamily
    exposure_state: ExposureState
    symbol: str
    base_asset: str
    direction: Direction
    status: str
    snapshot_at_utc: datetime
    source_registry: str = "unknown"
    canonical_asset_id: str | None = None
    canonical_asset_name: str | None = None
    identity_status: str = "UNKNOWN"
    venue: str | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    opened_at_utc: datetime | None = None
    verification_status: str = "NOT_REQUIRED"
    verification_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("exposure_id", "symbol", "base_asset"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        require_utc(self.snapshot_at_utc, field_name="snapshot_at_utc")
        if self.opened_at_utc is not None:
            require_utc(self.opened_at_utc, field_name="opened_at_utc")

    @property
    def pending(self) -> bool:
        return self.exposure_state is ExposureState.PENDING

    @property
    def is_real_advisory(self) -> bool:
        return self.exposure_family is ExposureFamily.REAL_ADVISORY

    def evidence_snapshot(self) -> dict[str, Any]:
        """Stable subset consumed by policy / assessment identity."""
        return {
            "exposure_id": self.exposure_id,
            "exposure_family": self.exposure_family.value,
            "exposure_state": self.exposure_state.value,
            "direction": self.direction.value,
            "pending": bool(self.pending),
            "status": self.status,
            "canonical_asset_id": self.canonical_asset_id,
            "entry_price": self.entry_price,
            "verification_status": self.verification_status,
        }

    def full_snapshot(self) -> dict[str, Any]:
        """Complete T0 exposure snapshot. Historical replay reads this only."""
        return {
            **self.evidence_snapshot(),
            "symbol": self.symbol,
            "base_asset": self.base_asset,
            "source_registry": self.source_registry,
            "canonical_asset_name": self.canonical_asset_name,
            "identity_status": self.identity_status,
            "venue": self.venue,
            "stop_price": self.stop_price,
            "opened_at_utc": utc_iso(self.opened_at_utc),
            "snapshot_at_utc": utc_iso(self.snapshot_at_utc),
            "verification_reason": self.verification_reason,
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> "ExposureView":
        snapshot_at = parse_utc(payload.get("snapshot_at_utc"), field_name="snapshot_at_utc")
        opened_at = parse_utc(payload.get("opened_at_utc"), field_name="opened_at_utc")
        if snapshot_at is None:
            raise ValueError("exposure snapshot_at_utc is required")
        return cls(
            exposure_id=str(payload["exposure_id"]),
            exposure_family=ExposureFamily(str(payload["exposure_family"])),
            exposure_state=ExposureState(str(payload["exposure_state"])),
            symbol=str(payload["symbol"]),
            base_asset=str(payload["base_asset"]),
            direction=Direction(str(payload["direction"])),
            status=str(payload.get("status") or ""),
            snapshot_at_utc=snapshot_at,
            source_registry=str(payload.get("source_registry") or "t0_replay"),
            canonical_asset_id=(
                str(payload["canonical_asset_id"])
                if payload.get("canonical_asset_id") is not None
                else None
            ),
            canonical_asset_name=(
                str(payload["canonical_asset_name"])
                if payload.get("canonical_asset_name") is not None
                else None
            ),
            identity_status=str(payload.get("identity_status") or "UNKNOWN"),
            venue=str(payload["venue"]) if payload.get("venue") is not None else None,
            entry_price=(
                float(payload["entry_price"])
                if payload.get("entry_price") is not None
                else None
            ),
            stop_price=(
                float(payload["stop_price"])
                if payload.get("stop_price") is not None
                else None
            ),
            opened_at_utc=opened_at,
            verification_status=str(payload.get("verification_status") or "NOT_REQUIRED"),
            verification_reason=(
                str(payload["verification_reason"])
                if payload.get("verification_reason") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class RiskAssessment:
    """Canonical, replayable Sequence 3 event-level risk assessment."""

    assessment_id: str
    event_id: str
    effective_event_id: str
    decision_at_utc: datetime
    exposure_id: str
    exposure_family: ExposureFamily
    exposure_state: ExposureState
    pending: bool
    direction: Direction
    event_class: EventClass
    event_type: EventType
    event_severity: EventSeverity
    relevance: Relevance
    risk_state: RiskState
    risk_score: float
    policy_version: str
    input_evidence_hash: str
    created_at_utc: datetime
    canonical_asset_id: str | None = None
    event_revision_of: str | None = None
    # Kept for backward compatibility with BUILD 3.2; it now means source-event
    # age and is always equal to event_age_seconds when newly produced.
    freshness_seconds: float | None = None
    event_age_seconds: float | None = None
    evidence_age_seconds: float | None = None
    ingestion_lag_seconds: float | None = None
    event_source_time_utc: datetime | None = None
    event_decision_visible_at_utc: datetime | None = None
    event_expires_at_utc: datetime | None = None
    provider: str | None = None
    provider_health_state: str | None = None
    evidence_confidence: EvidenceConfidence = EvidenceConfidence.NORMAL
    reasons: tuple[str, ...] = ()
    deterministic_rules_triggered: tuple[str, ...] = ()
    supporting_evidence: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "assessment_id",
            "event_id",
            "effective_event_id",
            "exposure_id",
            "policy_version",
            "input_evidence_hash",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        require_utc(self.decision_at_utc, field_name="decision_at_utc")
        require_utc(self.created_at_utc, field_name="created_at_utc")
        for name in (
            "event_source_time_utc",
            "event_decision_visible_at_utc",
            "event_expires_at_utc",
        ):
            value = getattr(self, name)
            if value is not None:
                require_utc(value, field_name=name)
        if not 0.0 <= float(self.risk_score) <= 1.0:
            raise ValueError("risk_score must be within 0.0..1.0")
        for name in (
            "freshness_seconds",
            "event_age_seconds",
            "evidence_age_seconds",
            "ingestion_lag_seconds",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported risk assessment schema_version={self.schema_version}"
            )

    @property
    def is_actionable_review(self) -> bool:
        return self.risk_state in {
            RiskState.AVOID_NEW_ENTRY,
            RiskState.PROTECT_REVIEW,
            RiskState.EXIT_REVIEW,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["exposure_family"] = self.exposure_family.value
        payload["exposure_state"] = self.exposure_state.value
        payload["direction"] = self.direction.value
        payload["event_class"] = self.event_class.value
        payload["event_type"] = self.event_type.value
        payload["event_severity"] = self.event_severity.value
        payload["relevance"] = self.relevance.value
        payload["risk_state"] = self.risk_state.value
        payload["evidence_confidence"] = self.evidence_confidence.value
        for name in (
            "decision_at_utc",
            "created_at_utc",
            "event_source_time_utc",
            "event_decision_visible_at_utc",
            "event_expires_at_utc",
        ):
            payload[name] = utc_iso(getattr(self, name))
        for name in (
            "reasons",
            "deterministic_rules_triggered",
            "supporting_evidence",
            "warnings",
        ):
            payload[name] = list(getattr(self, name))
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RiskAssessment":
        decision_at = parse_utc(payload.get("decision_at_utc"), field_name="decision_at_utc")
        created_at = parse_utc(payload.get("created_at_utc"), field_name="created_at_utc")
        if decision_at is None or created_at is None:
            raise ValueError("risk assessment timestamps are required")
        event_age = payload.get("event_age_seconds")
        if event_age is None:
            event_age = payload.get("freshness_seconds")
        return cls(
            assessment_id=str(payload["assessment_id"]),
            event_id=str(payload["event_id"]),
            effective_event_id=str(payload["effective_event_id"]),
            decision_at_utc=decision_at,
            exposure_id=str(payload["exposure_id"]),
            exposure_family=ExposureFamily(str(payload["exposure_family"])),
            exposure_state=ExposureState(str(payload["exposure_state"])),
            pending=bool(payload["pending"]),
            direction=Direction(str(payload["direction"])),
            event_class=EventClass(str(payload["event_class"])),
            event_type=EventType(str(payload["event_type"])),
            event_severity=EventSeverity(str(payload["event_severity"])),
            relevance=Relevance(str(payload["relevance"])),
            risk_state=RiskState(str(payload["risk_state"])),
            risk_score=float(payload["risk_score"]),
            policy_version=str(payload["policy_version"]),
            input_evidence_hash=str(payload["input_evidence_hash"]),
            created_at_utc=created_at,
            canonical_asset_id=(str(payload["canonical_asset_id"]) if payload.get("canonical_asset_id") is not None else None),
            event_revision_of=(str(payload["event_revision_of"]) if payload.get("event_revision_of") is not None else None),
            freshness_seconds=(float(event_age) if event_age is not None else None),
            event_age_seconds=(float(event_age) if event_age is not None else None),
            evidence_age_seconds=(float(payload["evidence_age_seconds"]) if payload.get("evidence_age_seconds") is not None else None),
            ingestion_lag_seconds=(float(payload["ingestion_lag_seconds"]) if payload.get("ingestion_lag_seconds") is not None else None),
            event_source_time_utc=parse_utc(payload.get("event_source_time_utc"), field_name="event_source_time_utc"),
            event_decision_visible_at_utc=parse_utc(payload.get("event_decision_visible_at_utc"), field_name="event_decision_visible_at_utc"),
            event_expires_at_utc=parse_utc(payload.get("event_expires_at_utc"), field_name="event_expires_at_utc"),
            provider=(str(payload["provider"]) if payload.get("provider") is not None else None),
            provider_health_state=(str(payload["provider_health_state"]) if payload.get("provider_health_state") is not None else None),
            evidence_confidence=EvidenceConfidence(str(payload.get("evidence_confidence") or EvidenceConfidence.NORMAL.value)),
            reasons=tuple(str(item) for item in (payload.get("reasons") or [])),
            deterministic_rules_triggered=tuple(str(item) for item in (payload.get("deterministic_rules_triggered") or [])),
            supporting_evidence=tuple(str(item) for item in (payload.get("supporting_evidence") or [])),
            warnings=tuple(str(item) for item in (payload.get("warnings") or [])),
            schema_version=int(payload.get("schema_version") or SCHEMA_VERSION),
        )


def build_input_evidence_hash(
    *,
    exposure_snapshot: dict[str, Any],
    effective_event_id: str,
    event_severity: EventSeverity,
    event_type: EventType,
    relevance: Relevance,
    stale_event: bool,
    provider_health_state: str | None,
    policy_version: str,
) -> str:
    """Hash the deterministic policy inputs, excluding continuously drifting age."""
    return stable_payload_hash(
        {
            "exposure": exposure_snapshot,
            "effective_event_id": effective_event_id,
            "event_severity": event_severity.value,
            "event_type": event_type.value,
            "relevance": relevance.value,
            "stale_event": bool(stale_event),
            "provider_health_state": provider_health_state,
            "policy_version": policy_version,
        }
    )


def build_assessment_id(
    *, exposure_id: str, effective_event_id: str, input_evidence_hash: str
) -> str:
    material = "\n".join(
        (str(exposure_id), str(effective_event_id), str(input_evidence_hash))
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()
