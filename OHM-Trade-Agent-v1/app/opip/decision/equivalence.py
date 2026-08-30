"""Persistent shadow-equivalence contracts for O'Pip BUILD 5.2A."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any

from app.opip.decision.models_v2 import AdmissionDecisionV2, DecisionRole
from app.opip.decision.serialization import canonical_serialize


EQUIVALENCE_SCHEMA_VERSION = 1


class PairingState(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"


class DivergenceKind(str, Enum):
    EXACT = "EXACT"
    OUTCOME = "OUTCOME"
    TERMINAL_GATE = "TERMINAL_GATE"
    REASON = "REASON"
    GATE_HISTORY = "GATE_HISTORY"
    INSTRUMENTATION_INCOMPLETE = "INSTRUMENTATION_INCOMPLETE"
    PAIRING_INVALID = "PAIRING_INVALID"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at_utc must be timezone-aware")
    return value.astimezone(timezone.utc)


def _gate_history(decision: AdmissionDecisionV2) -> tuple[dict[str, Any], ...]:
    return tuple(item.as_dict() for item in decision.gate_results_ordered)


@dataclass(frozen=True)
class EquivalenceObservation:
    """One immutable production-reference vs shadow comparison.

    Missing or mismatched sides are evidence too. They are recorded as
    instrumentation failures and can never count as equivalence.
    """

    observation_id: str
    observed_at_utc: datetime
    scan_id: str
    candidate_id: str
    production_decision_id: str | None
    shadow_decision_id: str | None
    evidence_hash: str | None
    gate_policy_fingerprint: str | None
    engine_code_fingerprint: str | None
    pair: str | None
    direction: str | None
    pairing_state: PairingState
    pairing_errors: tuple[str, ...]
    outcome_match: bool
    terminal_gate_match: bool
    reason_match: bool
    gate_history_match: bool
    exact_match: bool
    divergence_kind: DivergenceKind
    production_outcome: str | None
    shadow_outcome: str | None
    production_terminal_gate: str | None
    shadow_terminal_gate: str | None
    production_reason_code: str | None
    shadow_reason_code: str | None
    schema_version: int = EQUIVALENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at_utc", _utc(self.observed_at_utc))
        object.__setattr__(self, "pairing_state", PairingState(self.pairing_state))
        object.__setattr__(self, "divergence_kind", DivergenceKind(self.divergence_kind))
        object.__setattr__(
            self,
            "pairing_errors",
            tuple(sorted({str(item) for item in self.pairing_errors if str(item)})),
        )
        if self.schema_version != EQUIVALENCE_SCHEMA_VERSION:
            raise ValueError("unsupported equivalence observation schema")
        if not self.observation_id.startswith("EQO:"):
            raise ValueError("observation_id must use EQO: identity")
        if not self.scan_id or not self.candidate_id:
            raise ValueError("scan_id and candidate_id are required")
        if self.pairing_state is PairingState.COMPLETE and self.pairing_errors:
            raise ValueError("complete pairing cannot carry pairing errors")
        if self.exact_match and self.pairing_state is not PairingState.COMPLETE:
            raise ValueError("only complete pairings can be exact")
        if self.exact_match and self.divergence_kind is not DivergenceKind.EXACT:
            raise ValueError("exact match must use EXACT divergence kind")
        if not self.exact_match and self.divergence_kind is DivergenceKind.EXACT:
            raise ValueError("non-exact observation cannot use EXACT divergence kind")

    @property
    def instrumentation_complete(self) -> bool:
        return self.pairing_state is PairingState.COMPLETE

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "observed_at_utc": self.observed_at_utc.isoformat(),
            "scan_id": self.scan_id,
            "candidate_id": self.candidate_id,
            "production_decision_id": self.production_decision_id,
            "shadow_decision_id": self.shadow_decision_id,
            "evidence_hash": self.evidence_hash,
            "gate_policy_fingerprint": self.gate_policy_fingerprint,
            "engine_code_fingerprint": self.engine_code_fingerprint,
            "pair": self.pair,
            "direction": self.direction,
            "pairing_state": self.pairing_state.value,
            "pairing_errors": list(self.pairing_errors),
            "instrumentation_complete": self.instrumentation_complete,
            "outcome_match": self.outcome_match,
            "terminal_gate_match": self.terminal_gate_match,
            "reason_match": self.reason_match,
            "gate_history_match": self.gate_history_match,
            "exact_match": self.exact_match,
            "divergence_kind": self.divergence_kind.value,
            "production_outcome": self.production_outcome,
            "shadow_outcome": self.shadow_outcome,
            "production_terminal_gate": self.production_terminal_gate,
            "shadow_terminal_gate": self.shadow_terminal_gate,
            "production_reason_code": self.production_reason_code,
            "shadow_reason_code": self.shadow_reason_code,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EquivalenceObservation":
        stamp = datetime.fromisoformat(
            str(payload["observed_at_utc"]).replace("Z", "+00:00")
        )
        return cls(
            observation_id=str(payload["observation_id"]),
            observed_at_utc=stamp,
            scan_id=str(payload["scan_id"]),
            candidate_id=str(payload["candidate_id"]),
            production_decision_id=payload.get("production_decision_id"),
            shadow_decision_id=payload.get("shadow_decision_id"),
            evidence_hash=payload.get("evidence_hash"),
            gate_policy_fingerprint=payload.get("gate_policy_fingerprint"),
            engine_code_fingerprint=payload.get("engine_code_fingerprint"),
            pair=payload.get("pair"),
            direction=payload.get("direction"),
            pairing_state=PairingState(payload["pairing_state"]),
            pairing_errors=tuple(payload.get("pairing_errors") or ()),
            outcome_match=bool(payload.get("outcome_match")),
            terminal_gate_match=bool(payload.get("terminal_gate_match")),
            reason_match=bool(payload.get("reason_match")),
            gate_history_match=bool(payload.get("gate_history_match")),
            exact_match=bool(payload.get("exact_match")),
            divergence_kind=DivergenceKind(payload["divergence_kind"]),
            production_outcome=payload.get("production_outcome"),
            shadow_outcome=payload.get("shadow_outcome"),
            production_terminal_gate=payload.get("production_terminal_gate"),
            shadow_terminal_gate=payload.get("shadow_terminal_gate"),
            production_reason_code=payload.get("production_reason_code"),
            shadow_reason_code=payload.get("shadow_reason_code"),
            schema_version=int(payload.get("schema_version", -1)),
        )


def _decision_gate(decision: AdmissionDecisionV2 | None) -> str | None:
    if decision is None or decision.first_terminal_gate is None:
        return None
    return decision.first_terminal_gate.value


def _observation_id(
    *,
    scan_id: str,
    candidate_id: str,
    production_decision_id: str | None,
    shadow_decision_id: str | None,
    evidence_hash: str | None,
) -> str:
    payload = {
        "scan_id": scan_id,
        "candidate_id": candidate_id,
        "production_decision_id": production_decision_id,
        "shadow_decision_id": shadow_decision_id,
        "evidence_hash": evidence_hash,
    }
    return "EQO:" + hashlib.sha256(
        canonical_serialize(payload).encode("utf-8")
    ).hexdigest()


def build_equivalence_observation(
    *,
    observed_at_utc: datetime,
    scan_id: str,
    production: AdmissionDecisionV2 | None,
    shadow: AdmissionDecisionV2 | None,
    candidate_id: str | None = None,
) -> EquivalenceObservation:
    """Build one fail-closed equivalence observation.

    The two sides must represent the same sealed candidate/evidence/runtime.
    Any missing side is INCOMPLETE; any identity/runtime mismatch is INVALID.
    Neither state is eligible to count as a comparable match.
    """
    if production is None and shadow is None:
        raise ValueError("at least one decision side is required")

    chosen = production or shadow
    assert chosen is not None
    resolved_candidate = str(candidate_id or chosen.candidate_id or "")
    if not resolved_candidate:
        raise ValueError("candidate_id is required")

    errors: list[str] = []
    if production is None:
        errors.append("PRODUCTION_REFERENCE_MISSING")
    if shadow is None:
        errors.append("SHADOW_DECISION_MISSING")

    if production is not None and production.decision_role is not DecisionRole.PRODUCTION_REFERENCE:
        errors.append("PRODUCTION_ROLE_INVALID")
    if shadow is not None and shadow.decision_role is not DecisionRole.SHADOW_ENGINE:
        errors.append("SHADOW_ROLE_INVALID")

    if production is not None and shadow is not None:
        checks = (
            ("CANDIDATE_ID_MISMATCH", production.candidate_id, shadow.candidate_id),
            ("EVIDENCE_HASH_MISMATCH", production.evidence_hash, shadow.evidence_hash),
            (
                "POLICY_FINGERPRINT_MISMATCH",
                production.gate_policy_fingerprint,
                shadow.gate_policy_fingerprint,
            ),
            (
                "ENGINE_CODE_FINGERPRINT_MISMATCH",
                production.engine_code_fingerprint,
                shadow.engine_code_fingerprint,
            ),
            ("PAIR_MISMATCH", production.pair, shadow.pair),
            ("DIRECTION_MISMATCH", production.direction, shadow.direction),
            ("MARKET_TYPE_MISMATCH", production.market_type, shadow.market_type),
        )
        for code, left, right in checks:
            if left != right:
                errors.append(code)

    if production is None or shadow is None:
        pairing_state = PairingState.INCOMPLETE
    elif errors:
        pairing_state = PairingState.INVALID
    else:
        pairing_state = PairingState.COMPLETE

    comparable = pairing_state is PairingState.COMPLETE
    outcome_match = bool(
        comparable and production is not None and shadow is not None
        and production.decision == shadow.decision
    )
    terminal_gate_match = bool(
        comparable and _decision_gate(production) == _decision_gate(shadow)
    )
    reason_match = bool(
        comparable and production is not None and shadow is not None
        and production.terminal_reason_code == shadow.terminal_reason_code
        and production.terminal_reason_class == shadow.terminal_reason_class
    )
    gate_history_match = bool(
        comparable and production is not None and shadow is not None
        and canonical_serialize(_gate_history(production))
        == canonical_serialize(_gate_history(shadow))
    )
    exact = bool(
        outcome_match and terminal_gate_match and reason_match and gate_history_match
    )

    if pairing_state is PairingState.INCOMPLETE:
        kind = DivergenceKind.INSTRUMENTATION_INCOMPLETE
    elif pairing_state is PairingState.INVALID:
        kind = DivergenceKind.PAIRING_INVALID
    elif not outcome_match:
        kind = DivergenceKind.OUTCOME
    elif not terminal_gate_match:
        kind = DivergenceKind.TERMINAL_GATE
    elif not reason_match:
        kind = DivergenceKind.REASON
    elif not gate_history_match:
        kind = DivergenceKind.GATE_HISTORY
    else:
        kind = DivergenceKind.EXACT

    evidence_hash = (
        production.evidence_hash
        if production is not None
        else shadow.evidence_hash if shadow is not None else None
    )
    policy = (
        production.gate_policy_fingerprint
        if production is not None
        else shadow.gate_policy_fingerprint if shadow is not None else None
    )
    code_fingerprint = (
        production.engine_code_fingerprint
        if production is not None
        else shadow.engine_code_fingerprint if shadow is not None else None
    )
    pair = production.pair if production is not None else shadow.pair
    direction = production.direction if production is not None else shadow.direction

    return EquivalenceObservation(
        observation_id=_observation_id(
            scan_id=str(scan_id),
            candidate_id=resolved_candidate,
            production_decision_id=(
                production.decision_id if production is not None else None
            ),
            shadow_decision_id=shadow.decision_id if shadow is not None else None,
            evidence_hash=evidence_hash,
        ),
        observed_at_utc=observed_at_utc,
        scan_id=str(scan_id),
        candidate_id=resolved_candidate,
        production_decision_id=(
            production.decision_id if production is not None else None
        ),
        shadow_decision_id=shadow.decision_id if shadow is not None else None,
        evidence_hash=evidence_hash,
        gate_policy_fingerprint=policy,
        engine_code_fingerprint=code_fingerprint,
        pair=pair,
        direction=direction,
        pairing_state=pairing_state,
        pairing_errors=tuple(errors),
        outcome_match=outcome_match,
        terminal_gate_match=terminal_gate_match,
        reason_match=reason_match,
        gate_history_match=gate_history_match,
        exact_match=exact,
        divergence_kind=kind,
        production_outcome=(
            production.decision.value if production is not None else None
        ),
        shadow_outcome=shadow.decision.value if shadow is not None else None,
        production_terminal_gate=_decision_gate(production),
        shadow_terminal_gate=_decision_gate(shadow),
        production_reason_code=(
            production.terminal_reason_code if production is not None else None
        ),
        shadow_reason_code=shadow.terminal_reason_code if shadow is not None else None,
    )
