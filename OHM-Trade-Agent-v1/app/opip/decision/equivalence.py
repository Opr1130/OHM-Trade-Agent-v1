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


def _sha(prefix: str, value: Any) -> str:
    return prefix + hashlib.sha256(
        canonical_serialize(value).encode("utf-8")
    ).hexdigest()


def _gate_history(decision: AdmissionDecisionV2) -> tuple[dict[str, Any], ...]:
    return tuple(item.as_dict() for item in decision.gate_results_ordered)


def _decision_hash(decision: AdmissionDecisionV2 | None) -> str | None:
    return None if decision is None else _sha("DCH:", decision.as_dict())


def _gate_history_hash(decision: AdmissionDecisionV2 | None) -> str | None:
    return None if decision is None else _sha("GHH:", _gate_history(decision))


@dataclass(frozen=True)
class EquivalenceObservation:
    """One immutable production-reference vs shadow comparison."""

    observation_id: str
    observed_at_utc: datetime
    scan_id: str
    candidate_id: str
    production_decision_id: str | None
    shadow_decision_id: str | None
    production_decision_hash: str | None
    shadow_decision_hash: str | None
    production_gate_history_hash: str | None
    shadow_gate_history_hash: str | None
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
    production_reason_class: str | None
    shadow_reason_class: str | None
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
        if not self.scan_id or not self.candidate_id:
            raise ValueError("scan_id and candidate_id are required")

        if self.pairing_state is PairingState.COMPLETE:
            if self.pairing_errors:
                raise ValueError("complete pairing cannot carry pairing errors")
            required = (
                self.production_decision_id,
                self.shadow_decision_id,
                self.production_decision_hash,
                self.shadow_decision_hash,
                self.production_gate_history_hash,
                self.shadow_gate_history_hash,
                self.evidence_hash,
                self.gate_policy_fingerprint,
                self.engine_code_fingerprint,
                self.pair,
                self.direction,
            )
            if any(value is None or str(value) == "" for value in required):
                raise ValueError("complete pairing is missing immutable comparison evidence")
            expected_outcome = self.production_outcome == self.shadow_outcome
            expected_gate = self.production_terminal_gate == self.shadow_terminal_gate
            expected_reason = (
                self.production_reason_code == self.shadow_reason_code
                and self.production_reason_class == self.shadow_reason_class
            )
            expected_history = (
                self.production_gate_history_hash == self.shadow_gate_history_hash
            )
            expected_exact = (
                expected_outcome
                and expected_gate
                and expected_reason
                and expected_history
            )
            actual = (
                self.outcome_match,
                self.terminal_gate_match,
                self.reason_match,
                self.gate_history_match,
                self.exact_match,
            )
            expected = (
                expected_outcome,
                expected_gate,
                expected_reason,
                expected_history,
                expected_exact,
            )
            if actual != expected:
                raise ValueError("equivalence match flags are inconsistent with evidence")
            if not expected_outcome:
                expected_kind = DivergenceKind.OUTCOME
            elif not expected_gate:
                expected_kind = DivergenceKind.TERMINAL_GATE
            elif not expected_reason:
                expected_kind = DivergenceKind.REASON
            elif not expected_history:
                expected_kind = DivergenceKind.GATE_HISTORY
            else:
                expected_kind = DivergenceKind.EXACT
            if self.divergence_kind is not expected_kind:
                raise ValueError("divergence kind is inconsistent with comparison evidence")
        else:
            if any(
                (
                    self.outcome_match,
                    self.terminal_gate_match,
                    self.reason_match,
                    self.gate_history_match,
                    self.exact_match,
                )
            ):
                raise ValueError("non-comparable pairing cannot carry match flags")
            expected_kind = (
                DivergenceKind.INSTRUMENTATION_INCOMPLETE
                if self.pairing_state is PairingState.INCOMPLETE
                else DivergenceKind.PAIRING_INVALID
            )
            if self.divergence_kind is not expected_kind:
                raise ValueError("non-comparable pairing has invalid divergence kind")

        if self.observation_id != self.calculated_observation_id:
            raise ValueError("equivalence observation content hash mismatch")

    @property
    def instrumentation_complete(self) -> bool:
        return self.pairing_state is PairingState.COMPLETE

    def identity_payload(self) -> dict[str, Any]:
        """Content identity excluding wall-clock observation time."""
        return {
            "schema_version": self.schema_version,
            "scan_id": self.scan_id,
            "candidate_id": self.candidate_id,
            "production_decision_id": self.production_decision_id,
            "shadow_decision_id": self.shadow_decision_id,
            "production_decision_hash": self.production_decision_hash,
            "shadow_decision_hash": self.shadow_decision_hash,
            "production_gate_history_hash": self.production_gate_history_hash,
            "shadow_gate_history_hash": self.shadow_gate_history_hash,
            "evidence_hash": self.evidence_hash,
            "gate_policy_fingerprint": self.gate_policy_fingerprint,
            "engine_code_fingerprint": self.engine_code_fingerprint,
            "pair": self.pair,
            "direction": self.direction,
            "pairing_state": self.pairing_state.value,
            "pairing_errors": list(self.pairing_errors),
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
            "production_reason_class": self.production_reason_class,
            "shadow_reason_class": self.shadow_reason_class,
        }

    @property
    def calculated_observation_id(self) -> str:
        return _sha("EQO:", self.identity_payload())

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.identity_payload(),
            "observation_id": self.observation_id,
            "observed_at_utc": self.observed_at_utc.isoformat(),
            "instrumentation_complete": self.instrumentation_complete,
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
            production_decision_hash=payload.get("production_decision_hash"),
            shadow_decision_hash=payload.get("shadow_decision_hash"),
            production_gate_history_hash=payload.get("production_gate_history_hash"),
            shadow_gate_history_hash=payload.get("shadow_gate_history_hash"),
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
            production_reason_class=payload.get("production_reason_class"),
            shadow_reason_class=payload.get("shadow_reason_class"),
            schema_version=int(payload.get("schema_version", -1)),
        )


def _decision_gate(decision: AdmissionDecisionV2 | None) -> str | None:
    if decision is None or decision.first_terminal_gate is None:
        return None
    return decision.first_terminal_gate.value


def _reason_class(decision: AdmissionDecisionV2 | None) -> str | None:
    return None if decision is None else decision.terminal_reason_class.value


def build_equivalence_observation(
    *,
    observed_at_utc: datetime,
    scan_id: str,
    production: AdmissionDecisionV2 | None,
    shadow: AdmissionDecisionV2 | None,
    candidate_id: str | None = None,
) -> EquivalenceObservation:
    """Build one fail-closed equivalence observation."""
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
        state = PairingState.INCOMPLETE
    elif errors:
        state = PairingState.INVALID
    else:
        state = PairingState.COMPLETE

    comparable = state is PairingState.COMPLETE
    production_history_hash = _gate_history_hash(production)
    shadow_history_hash = _gate_history_hash(shadow)
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
        comparable
        and production_history_hash is not None
        and production_history_hash == shadow_history_hash
    )
    exact = bool(
        outcome_match and terminal_gate_match and reason_match and gate_history_match
    )

    if state is PairingState.INCOMPLETE:
        kind = DivergenceKind.INSTRUMENTATION_INCOMPLETE
    elif state is PairingState.INVALID:
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

    values = dict(
        observation_id="EQO:" + ("0" * 64),
        observed_at_utc=observed_at_utc,
        scan_id=str(scan_id),
        candidate_id=resolved_candidate,
        production_decision_id=(
            production.decision_id if production is not None else None
        ),
        shadow_decision_id=shadow.decision_id if shadow is not None else None,
        production_decision_hash=_decision_hash(production),
        shadow_decision_hash=_decision_hash(shadow),
        production_gate_history_hash=production_history_hash,
        shadow_gate_history_hash=shadow_history_hash,
        evidence_hash=evidence_hash,
        gate_policy_fingerprint=policy,
        engine_code_fingerprint=code_fingerprint,
        pair=pair,
        direction=direction,
        pairing_state=state,
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
        production_reason_class=_reason_class(production),
        shadow_reason_class=_reason_class(shadow),
    )
    # Build once with a placeholder only to calculate the content identity
    # without permitting a caller-supplied observation ID.
    payload = {
        key: value
        for key, value in values.items()
        if key not in {"observation_id", "observed_at_utc"}
    }
    payload["schema_version"] = EQUIVALENCE_SCHEMA_VERSION
    values["observation_id"] = _sha(
        "EQO:",
        {
            **payload,
            "pairing_state": state.value,
            "pairing_errors": list(tuple(sorted(errors))),
            "divergence_kind": kind.value,
        },
    )
    return EquivalenceObservation(**values)
