"""Immutable T0 evidence for O'Pip Decision V2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from typing import Any

from app.opip.decision.identity import normalize_direction
from app.opip.decision.policy_snapshot import GatePolicySnapshot
from app.opip.decision.serialization import canonical_data, canonical_serialize
from app.opip.decision.versioning import app_code_fingerprint


EVIDENCE_SCHEMA_VERSION = 2


class EvidenceCompleteness(str, Enum):
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"
    INCOMPLETE = "INCOMPLETE"
    # Reserved compatibility value for pre-construction capture failures.
    # A successfully constructed OPipDecisionEvidence is syntactically usable
    # by definition and therefore never emits this state.
    UNUSABLE = "UNUSABLE"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("decision_time_utc must be timezone-aware")
    return value.astimezone(timezone.utc)


def _object_json(value: Any, *, field_name: str) -> str:
    normalized = canonical_data(value)
    if not isinstance(normalized, dict):
        raise TypeError(f"{field_name} must serialize to an object")
    return canonical_serialize(normalized)


def _optional_json(value: Any) -> str | None:
    return None if value is None else canonical_serialize(value)


@dataclass(frozen=True)
class OPipDecisionEvidence:
    """Sealed evidence. JSON fields are strings so callers cannot mutate T0."""

    decision_time_utc: datetime
    episode_id: str
    candidate_id: str
    canonical_asset_id: str
    pair: str
    market_type: str
    direction: str
    asset_identity_provenance: tuple[tuple[str, str], ...]
    candidate_snapshot_json: str
    gate_policy_snapshot: GatePolicySnapshot
    engine_code_fingerprint: str

    cohort_id: str | None = None
    signal_id: str | None = None
    asset_display_name: str | None = None
    account_equity: float | None = None
    ai_evidence_json: str | None = None
    ai_item_json: str | None = None
    market_intelligence_json: str | None = None

    provider_health_snapshot_ref: str | None = None
    event_snapshot_ref: str | None = None
    risk_snapshot_ref: str | None = None
    streaming_snapshot_ref: str | None = None

    declared_missing_evidence: tuple[str, ...] = ()
    degraded_evidence: tuple[str, ...] = ()
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_time_utc", _utc(self.decision_time_utc))
        object.__setattr__(self, "direction", normalize_direction(self.direction))
        object.__setattr__(self, "pair", str(self.pair or "").upper())
        object.__setattr__(self, "market_type", str(self.market_type or "").upper())
        object.__setattr__(
            self, "canonical_asset_id", str(self.canonical_asset_id or "").strip()
        )
        object.__setattr__(
            self,
            "asset_identity_provenance",
            tuple((str(key), str(value)) for key, value in self.asset_identity_provenance),
        )
        object.__setattr__(
            self,
            "declared_missing_evidence",
            tuple(sorted({str(item) for item in self.declared_missing_evidence if str(item)})),
        )
        object.__setattr__(
            self,
            "degraded_evidence",
            tuple(sorted({str(item) for item in self.degraded_evidence if str(item)})),
        )

        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported Decision V2 evidence schema")
        if self.direction not in {"LONG", "SHORT"}:
            raise ValueError("direction must be LONG or SHORT")
        if not self.episode_id or not self.candidate_id:
            raise ValueError("episode_id and candidate_id are required")
        if not self.canonical_asset_id or not self.pair or not self.market_type:
            raise ValueError("canonical identity, pair and market_type are required")
        if not self.asset_identity_provenance:
            raise ValueError("asset identity provenance is required")
        if not str(self.engine_code_fingerprint or "").startswith("ACF:"):
            raise ValueError("engine_code_fingerprint must use ACF: identity")
        if self.account_equity is not None:
            equity = float(self.account_equity)
            if not math.isfinite(equity) or equity < 0:
                raise ValueError("account_equity must be finite and non-negative")

        snapshot = json.loads(self.candidate_snapshot_json)
        if not isinstance(snapshot, dict):
            raise ValueError("candidate_snapshot_json must encode an object")
        canonical_serialize(snapshot)
        for optional in (self.ai_evidence_json, self.market_intelligence_json):
            if optional is not None:
                canonical_serialize(json.loads(optional))
        if self.ai_item_json is not None:
            ai_item = json.loads(self.ai_item_json)
            if not isinstance(ai_item, dict):
                raise ValueError("ai_item_json must encode an object")
            canonical_serialize(ai_item)
        self.gate_policy_snapshot.validate_integrity()

    @classmethod
    def build(
        cls,
        *,
        decision_time_utc: datetime,
        episode_id: str,
        candidate_id: str,
        canonical_asset_id: str,
        pair: str,
        market_type: str,
        direction: str,
        asset_identity_provenance: tuple[tuple[str, str], ...],
        candidate_snapshot: Any,
        gate_policy_snapshot: GatePolicySnapshot,
        engine_code_fingerprint: str | None = None,
        cohort_id: str | None = None,
        signal_id: str | None = None,
        asset_display_name: str | None = None,
        account_equity: float | None = None,
        ai_evidence: Any = None,
        ai_item: Any = None,
        market_intelligence: Any = None,
        provider_health_snapshot_ref: str | None = None,
        event_snapshot_ref: str | None = None,
        risk_snapshot_ref: str | None = None,
        streaming_snapshot_ref: str | None = None,
        declared_missing_evidence: tuple[str, ...] = (),
        degraded_evidence: tuple[str, ...] = (),
    ) -> "OPipDecisionEvidence":
        return cls(
            decision_time_utc=decision_time_utc,
            episode_id=str(episode_id),
            candidate_id=str(candidate_id),
            canonical_asset_id=str(canonical_asset_id),
            pair=str(pair),
            market_type=str(market_type),
            direction=str(direction),
            asset_identity_provenance=asset_identity_provenance,
            candidate_snapshot_json=_object_json(
                candidate_snapshot, field_name="candidate_snapshot"
            ),
            gate_policy_snapshot=gate_policy_snapshot,
            engine_code_fingerprint=str(
                engine_code_fingerprint or app_code_fingerprint()
            ),
            cohort_id=cohort_id,
            signal_id=signal_id,
            asset_display_name=asset_display_name,
            account_equity=account_equity,
            ai_evidence_json=_optional_json(ai_evidence),
            ai_item_json=(
                None if ai_item is None else _object_json(ai_item, field_name="ai_item")
            ),
            market_intelligence_json=_optional_json(market_intelligence),
            provider_health_snapshot_ref=provider_health_snapshot_ref,
            event_snapshot_ref=event_snapshot_ref,
            risk_snapshot_ref=risk_snapshot_ref,
            streaming_snapshot_ref=streaming_snapshot_ref,
            declared_missing_evidence=declared_missing_evidence,
            degraded_evidence=degraded_evidence,
        )

    @property
    def candidate_snapshot(self) -> dict[str, Any]:
        return json.loads(self.candidate_snapshot_json)

    @property
    def ai_evidence(self) -> dict[str, Any] | None:
        if self.ai_evidence_json is None:
            return None
        value = json.loads(self.ai_evidence_json)
        if not isinstance(value, dict):
            raise ValueError("ai_evidence must decode to an object")
        return value

    @property
    def ai_item(self) -> dict[str, Any] | None:
        if self.ai_item_json is None:
            return None
        value = json.loads(self.ai_item_json)
        if not isinstance(value, dict):
            raise ValueError("ai_item must decode to an object")
        return value

    @property
    def market_intelligence(self) -> Any:
        return (
            json.loads(self.market_intelligence_json)
            if self.market_intelligence_json is not None
            else None
        )

    @property
    def computed_missing_evidence(self) -> tuple[str, ...]:
        refs = {
            "provider_health_snapshot_ref": self.provider_health_snapshot_ref,
            "event_snapshot_ref": self.event_snapshot_ref,
            "risk_snapshot_ref": self.risk_snapshot_ref,
            "streaming_snapshot_ref": self.streaming_snapshot_ref,
        }
        missing = set(self.declared_missing_evidence)
        for name in self.gate_policy_snapshot.required_evidence_refs:
            if name == "candidate_snapshot":
                continue
            if not refs.get(name):
                missing.add(name)
        return tuple(sorted(missing))

    @property
    def evidence_completeness(self) -> EvidenceCompleteness:
        if self.computed_missing_evidence:
            return EvidenceCompleteness.INCOMPLETE
        if self.degraded_evidence:
            return EvidenceCompleteness.DEGRADED
        return EvidenceCompleteness.COMPLETE

    def hash_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_time_utc": self.decision_time_utc,
            "episode_id": self.episode_id,
            "cohort_id": self.cohort_id,
            "candidate_id": self.candidate_id,
            "signal_id": self.signal_id,
            "canonical_asset_id": self.canonical_asset_id,
            "asset_display_name": self.asset_display_name,
            "pair": self.pair,
            "market_type": self.market_type,
            "direction": self.direction,
            "asset_identity_provenance": self.asset_identity_provenance,
            "candidate_snapshot": self.candidate_snapshot,
            "gate_policy_snapshot": self.gate_policy_snapshot.as_dict(),
            "engine_code_fingerprint": self.engine_code_fingerprint,
            "account_equity": self.account_equity,
            "ai_evidence": self.ai_evidence,
            "ai_item": self.ai_item,
            "market_intelligence": self.market_intelligence,
            "provider_health_snapshot_ref": self.provider_health_snapshot_ref,
            "event_snapshot_ref": self.event_snapshot_ref,
            "risk_snapshot_ref": self.risk_snapshot_ref,
            "streaming_snapshot_ref": self.streaming_snapshot_ref,
            "declared_missing_evidence": self.declared_missing_evidence,
            "degraded_evidence": self.degraded_evidence,
        }

    @property
    def evidence_hash(self) -> str:
        return "EVH:" + hashlib.sha256(
            canonical_serialize(self.hash_payload()).encode("utf-8")
        ).hexdigest()

    @property
    def evidence_snapshot_id(self) -> str:
        return self.evidence_hash

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.hash_payload(),
            "evidence_snapshot_id": self.evidence_snapshot_id,
            "evidence_hash": self.evidence_hash,
            "evidence_completeness": self.evidence_completeness.value,
            "computed_missing_evidence": list(self.computed_missing_evidence),
        }
