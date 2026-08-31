"""Immutable decision-time attribution for O'Pip Event Risk Shield."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.opip.events.contract import parse_utc, require_utc, utc_iso
from app.opip.risk.contract import (
    Direction,
    ExposureFamily,
    ExposureState,
    RiskState,
)


T0_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class T0Attribution:
    attribution_id: str
    assessment_id: str
    decision_at_utc: datetime
    event_id: str
    effective_event_id: str
    exposure_id: str
    exposure_family: ExposureFamily
    exposure_state: ExposureState
    pending: bool
    direction: Direction
    risk_state: RiskState
    policy_version: str
    input_evidence_hash: str
    created_at_utc: datetime
    exposure_snapshot: dict[str, Any] = field(default_factory=dict)
    provider_health_snapshot: dict[str, Any] = field(default_factory=dict)
    event_visibility: dict[str, Any] = field(default_factory=dict)
    policy_input_snapshot: dict[str, Any] = field(default_factory=dict)
    market_context: dict[str, Any] = field(default_factory=dict)
    deterministic_rules_triggered: tuple[str, ...] = ()
    entry_price: float | None = None
    current_price_at_t0: float | None = None
    position_age_seconds: float | None = None
    event_revision_of: str | None = None
    notification_decision: str = "NOT_EVALUATED"
    notification_status: str = "NONE"
    schema_version: int = T0_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("attribution_id", "assessment_id", "exposure_id"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        require_utc(self.decision_at_utc, field_name="decision_at_utc")
        require_utc(self.created_at_utc, field_name="created_at_utc")
        if not self.exposure_snapshot:
            raise ValueError("T0 attribution requires the exposure snapshot used by policy")
        if not self.policy_input_snapshot:
            raise ValueError("T0 attribution requires deterministic policy inputs")
        if self.position_age_seconds is not None and self.position_age_seconds < 0:
            raise ValueError("position_age_seconds cannot be negative")
        if self.schema_version != T0_SCHEMA_VERSION:
            raise ValueError(f"unsupported T0 schema_version={self.schema_version}")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["exposure_family"] = self.exposure_family.value
        payload["exposure_state"] = self.exposure_state.value
        payload["direction"] = self.direction.value
        payload["risk_state"] = self.risk_state.value
        payload["decision_at_utc"] = utc_iso(self.decision_at_utc)
        payload["created_at_utc"] = utc_iso(self.created_at_utc)
        payload["deterministic_rules_triggered"] = list(self.deterministic_rules_triggered)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "T0Attribution":
        decision_at = parse_utc(payload.get("decision_at_utc"), field_name="decision_at_utc")
        created_at = parse_utc(payload.get("created_at_utc"), field_name="created_at_utc")
        if decision_at is None or created_at is None:
            raise ValueError("T0 attribution timestamps are required")
        policy_inputs = dict(payload.get("policy_input_snapshot") or {})
        # BUILD 3.2 rows created before hardening are readable but cannot be
        # claimed as fully replayable. Preserve a marker instead of guessing.
        if not policy_inputs:
            policy_inputs = {"legacy_unreplayable": True}
        return cls(
            attribution_id=str(payload["attribution_id"]),
            assessment_id=str(payload["assessment_id"]),
            decision_at_utc=decision_at,
            event_id=str(payload["event_id"]),
            effective_event_id=str(payload["effective_event_id"]),
            exposure_id=str(payload["exposure_id"]),
            exposure_family=ExposureFamily(str(payload["exposure_family"])),
            exposure_state=ExposureState(str(payload["exposure_state"])),
            pending=bool(payload.get("pending")),
            direction=Direction(str(payload["direction"])),
            risk_state=RiskState(str(payload["risk_state"])),
            policy_version=str(payload["policy_version"]),
            input_evidence_hash=str(payload["input_evidence_hash"]),
            created_at_utc=created_at,
            exposure_snapshot=dict(payload.get("exposure_snapshot") or {}),
            provider_health_snapshot=dict(payload.get("provider_health_snapshot") or {}),
            event_visibility=dict(payload.get("event_visibility") or {}),
            policy_input_snapshot=policy_inputs,
            market_context=dict(payload.get("market_context") or {}),
            deterministic_rules_triggered=tuple(
                str(item) for item in (payload.get("deterministic_rules_triggered") or [])
            ),
            entry_price=(float(payload["entry_price"]) if payload.get("entry_price") is not None else None),
            current_price_at_t0=(float(payload["current_price_at_t0"]) if payload.get("current_price_at_t0") is not None else None),
            position_age_seconds=(float(payload["position_age_seconds"]) if payload.get("position_age_seconds") is not None else None),
            event_revision_of=(str(payload["event_revision_of"]) if payload.get("event_revision_of") is not None else None),
            notification_decision=str(payload.get("notification_decision") or "NOT_EVALUATED"),
            notification_status=str(payload.get("notification_status") or "NONE"),
            schema_version=int(payload.get("schema_version") or T0_SCHEMA_VERSION),
        )
