"""Canonical immutable decision contract for O'Pip Sequence 5 BUILD 5.1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any

from app.opip.decision.evidence import OPipDecisionEvidence
from app.opip.decision.models import (
    AdmissionDecision,
    DecisionOutcome,
    GateName,
    GateResult,
    GATE_INDEX,
    ReasonClass,
)
from app.opip.decision.serialization import canonical_serialize
from app.opip.decision.versioning import FEATURE_SCHEMA_VERSION, MODEL_VERSION


DECISION_SCHEMA_VERSION = 2
ENGINE_VERSION = "OPIP-DECISION-ENGINE-V1-SHADOW"


class DecisionRole(str, Enum):
    PRODUCTION_REFERENCE = "PRODUCTION_REFERENCE"
    SHADOW_ENGINE = "SHADOW_ENGINE"
    CHAMPION = "CHAMPION"
    CHALLENGER = "CHALLENGER"


def build_decision_id(
    *,
    candidate_id: str,
    decision_role: DecisionRole | str,
    engine_version: str,
    gate_policy_fingerprint: str,
    evidence_hash: str,
) -> str:
    role = DecisionRole(decision_role).value
    basis = "|".join(
        (
            str(candidate_id),
            role,
            str(engine_version),
            str(gate_policy_fingerprint),
            str(evidence_hash),
        )
    )
    return "DEC:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AdmissionDecisionV2:
    decision_id: str
    candidate_id: str
    episode_id: str
    cohort_id: str | None
    signal_id: str | None
    canonical_asset_id: str
    asset_display_name: str | None
    pair: str
    market_type: str
    direction: str
    decided_at_utc: datetime
    decision_role: DecisionRole
    decision: DecisionOutcome
    first_terminal_gate: GateName | None
    terminal_reason_code: str | None
    terminal_reason_class: ReasonClass
    terminal_reason: str
    gate_results_ordered: tuple[GateResult, ...]
    counterfactual_eligible: bool
    evidence_snapshot_id: str
    evidence_hash: str
    evidence_completeness: str
    strategy_version: str
    intelligence_version: str
    gate_policy_version: str
    gate_policy_fingerprint: str
    policy_snapshot_hash: str
    engine_version: str
    feature_schema_version: str | None = FEATURE_SCHEMA_VERSION
    model_version: str | None = MODEL_VERSION
    schema_version: int = DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.decided_at_utc.tzinfo is None or self.decided_at_utc.utcoffset() is None:
            raise ValueError("decided_at_utc must be timezone-aware")
        object.__setattr__(
            self, "decided_at_utc", self.decided_at_utc.astimezone(timezone.utc)
        )
        object.__setattr__(self, "decision_role", DecisionRole(self.decision_role))
        object.__setattr__(self, "decision", DecisionOutcome(self.decision))

        if self.schema_version != DECISION_SCHEMA_VERSION:
            raise ValueError("unsupported Decision V2 schema")
        if not self.decision_id.startswith("DEC:"):
            raise ValueError("decision_id must use DEC: identity")
        if not self.evidence_hash.startswith("EVH:"):
            raise ValueError("evidence_hash must use EVH: identity")
        if self.evidence_snapshot_id != self.evidence_hash:
            raise ValueError("evidence_snapshot_id must retain the full evidence hash")

        indexes = [GATE_INDEX.get(result.gate, -1) for result in self.gate_results_ordered]
        if indexes != sorted(indexes):
            raise ValueError("gate_results_ordered must follow canonical GATE_ORDER")

    @property
    def is_admitted(self) -> bool:
        return self.decision is DecisionOutcome.QUALIFIED

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "candidate_id": self.candidate_id,
            "episode_id": self.episode_id,
            "cohort_id": self.cohort_id,
            "signal_id": self.signal_id,
            "canonical_asset_id": self.canonical_asset_id,
            "asset_display_name": self.asset_display_name,
            "pair": self.pair,
            "market_type": self.market_type,
            "direction": self.direction,
            "decided_at_utc": self.decided_at_utc,
            "decision_role": self.decision_role.value,
            "decision": self.decision.value,
            "is_admitted": self.is_admitted,
            "first_terminal_gate": (
                self.first_terminal_gate.value if self.first_terminal_gate else None
            ),
            "terminal_reason_code": self.terminal_reason_code,
            "terminal_reason_class": self.terminal_reason_class.value,
            "terminal_reason": self.terminal_reason,
            "gate_results_ordered": [
                result.as_dict() for result in self.gate_results_ordered
            ],
            "counterfactual_eligible": self.counterfactual_eligible,
            "evidence_snapshot_id": self.evidence_snapshot_id,
            "evidence_hash": self.evidence_hash,
            "evidence_completeness": self.evidence_completeness,
            "strategy_version": self.strategy_version,
            "intelligence_version": self.intelligence_version,
            "gate_policy_version": self.gate_policy_version,
            "gate_policy_fingerprint": self.gate_policy_fingerprint,
            "policy_snapshot_hash": self.policy_snapshot_hash,
            "engine_version": self.engine_version,
            "feature_schema_version": self.feature_schema_version,
            "model_version": self.model_version,
        }

    @property
    def canonical_json(self) -> str:
        return canonical_serialize(self.as_dict())


def from_v1_decision(
    decision: AdmissionDecision,
    *,
    evidence: OPipDecisionEvidence,
    decision_role: DecisionRole,
    engine_version: str = ENGINE_VERSION,
) -> AdmissionDecisionV2:
    fingerprint = evidence.gate_policy_snapshot.policy_fingerprint
    return AdmissionDecisionV2(
        decision_id=build_decision_id(
            candidate_id=decision.candidate_id,
            decision_role=decision_role,
            engine_version=engine_version,
            gate_policy_fingerprint=fingerprint,
            evidence_hash=evidence.evidence_hash,
        ),
        candidate_id=decision.candidate_id,
        episode_id=str(decision.episode_id or evidence.episode_id),
        cohort_id=evidence.cohort_id,
        signal_id=decision.signal_id or evidence.signal_id,
        canonical_asset_id=evidence.canonical_asset_id,
        asset_display_name=decision.asset_display_name or evidence.asset_display_name,
        pair=decision.pair,
        market_type=decision.market_type,
        direction=decision.direction,
        decided_at_utc=evidence.decision_time_utc,
        decision_role=decision_role,
        decision=decision.decision,
        first_terminal_gate=decision.first_terminal_gate,
        terminal_reason_code=(
            decision.terminal_reason_code.value
            if decision.terminal_reason_code is not None
            else None
        ),
        terminal_reason_class=decision.terminal_reason_class,
        terminal_reason=decision.terminal_reason,
        gate_results_ordered=tuple(decision.gate_results),
        counterfactual_eligible=decision.counterfactual_eligible,
        evidence_snapshot_id=evidence.evidence_snapshot_id,
        evidence_hash=evidence.evidence_hash,
        evidence_completeness=evidence.evidence_completeness.value,
        strategy_version=decision.strategy_version,
        intelligence_version=decision.intelligence_version,
        gate_policy_version=decision.gate_policy_version,
        gate_policy_fingerprint=fingerprint,
        policy_snapshot_hash=evidence.gate_policy_snapshot.snapshot_hash,
        engine_version=engine_version,
    )
