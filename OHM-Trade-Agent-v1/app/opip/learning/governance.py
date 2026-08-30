"""Governed learning lifecycle for O'Pip Sequence 5 Wave A2.

The lifecycle is evidence-only. A successful shadow test can become ACCEPTED
only with explicit human approval metadata; no state transition activates a
model, changes policy, or modifies trading authority.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Mapping


class LearningStage(str, Enum):
    OBSERVATION = "OBSERVATION"
    HYPOTHESIS = "HYPOTHESIS"
    SHADOW_TEST = "SHADOW_TEST"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


_ALLOWED_TRANSITIONS = {
    LearningStage.OBSERVATION: {LearningStage.HYPOTHESIS},
    LearningStage.HYPOTHESIS: {LearningStage.SHADOW_TEST},
    LearningStage.SHADOW_TEST: {LearningStage.ACCEPTED, LearningStage.REJECTED},
    LearningStage.ACCEPTED: set(),
    LearningStage.REJECTED: set(),
}


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


@dataclass(frozen=True)
class LearningLifecycleRecord:
    learning_id: str
    stage: LearningStage
    created_at_utc: datetime
    updated_at_utc: datetime
    evidence_ids: tuple[str, ...]
    metrics_json: str
    known_regressions: tuple[str, ...]
    approving_principal: str | None = None
    approved_at_utc: datetime | None = None
    effective_ref: str | None = None
    rollback_ref: str | None = None
    measurement_only: bool = True
    automatic_activation: bool = False
    automatic_promotion: bool = False
    trade_authority_changed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "created_at_utc", _utc(self.created_at_utc, field_name="created_at_utc")
        )
        object.__setattr__(
            self, "updated_at_utc", _utc(self.updated_at_utc, field_name="updated_at_utc")
        )
        object.__setattr__(self, "stage", LearningStage(self.stage))
        object.__setattr__(
            self,
            "evidence_ids",
            tuple(sorted({str(item) for item in self.evidence_ids if str(item)})),
        )
        object.__setattr__(
            self,
            "known_regressions",
            tuple(sorted({str(item) for item in self.known_regressions if str(item)})),
        )
        if not self.learning_id.startswith("LEARN:"):
            raise ValueError("learning_id must use LEARN: identity")
        if self.updated_at_utc < self.created_at_utc:
            raise ValueError("updated_at_utc cannot precede created_at_utc")
        metrics = json.loads(self.metrics_json)
        if not isinstance(metrics, dict):
            raise ValueError("metrics_json must encode an object")
        _canonical_json(metrics)
        approved = self.approved_at_utc
        if approved is not None:
            approved = _utc(approved, field_name="approved_at_utc")
            object.__setattr__(self, "approved_at_utc", approved)
        if self.stage is LearningStage.ACCEPTED:
            if not str(self.approving_principal or "").strip() or approved is None:
                raise ValueError("ACCEPTED learning requires explicit human approval")
        elif self.approving_principal is not None or approved is not None:
            raise ValueError("approval metadata is reserved for ACCEPTED learning")
        if self.automatic_activation or self.automatic_promotion:
            raise ValueError("automatic learning activation/promotion is prohibited")
        if self.trade_authority_changed:
            raise ValueError("learning lifecycle cannot change trading authority")

    @property
    def metrics(self) -> dict[str, Any]:
        value = json.loads(self.metrics_json)
        if not isinstance(value, dict):
            raise ValueError("metrics_json must encode an object")
        return value

    def as_dict(self) -> dict[str, Any]:
        return {
            "learning_id": self.learning_id,
            "stage": self.stage.value,
            "created_at_utc": self.created_at_utc.isoformat(),
            "updated_at_utc": self.updated_at_utc.isoformat(),
            "evidence_ids": list(self.evidence_ids),
            "metrics": self.metrics,
            "known_regressions": list(self.known_regressions),
            "approving_principal": self.approving_principal,
            "approved_at_utc": (
                self.approved_at_utc.isoformat()
                if self.approved_at_utc is not None
                else None
            ),
            "effective_ref": self.effective_ref,
            "rollback_ref": self.rollback_ref,
            "measurement_only": True,
            "automatic_activation": False,
            "automatic_promotion": False,
            "trade_authority_changed": False,
        }


def create_learning_observation(
    *,
    hypothesis_key: str,
    created_at_utc: datetime,
    evidence_ids: tuple[str, ...],
    metrics: Mapping[str, Any] | None = None,
    known_regressions: tuple[str, ...] = (),
) -> LearningLifecycleRecord:
    created = _utc(created_at_utc, field_name="created_at_utc")
    evidence = tuple(sorted({str(item) for item in evidence_ids if str(item)}))
    if not str(hypothesis_key or "").strip():
        raise ValueError("hypothesis_key is required")
    if not evidence:
        raise ValueError("at least one evidence id is required")
    identity_payload = {
        "hypothesis_key": str(hypothesis_key),
        "created_at_utc": created.isoformat(),
        "evidence_ids": list(evidence),
    }
    learning_id = "LEARN:" + hashlib.sha256(
        _canonical_json(identity_payload).encode("utf-8")
    ).hexdigest()[:32]
    return LearningLifecycleRecord(
        learning_id=learning_id,
        stage=LearningStage.OBSERVATION,
        created_at_utc=created,
        updated_at_utc=created,
        evidence_ids=evidence,
        metrics_json=_canonical_json(metrics or {}),
        known_regressions=known_regressions,
    )


def transition_learning(
    record: LearningLifecycleRecord,
    *,
    target: LearningStage,
    updated_at_utc: datetime,
    evidence_ids: tuple[str, ...] | None = None,
    metrics: Mapping[str, Any] | None = None,
    known_regressions: tuple[str, ...] | None = None,
    approving_principal: str | None = None,
    approved_at_utc: datetime | None = None,
    effective_ref: str | None = None,
    rollback_ref: str | None = None,
) -> LearningLifecycleRecord:
    target = LearningStage(target)
    if target not in _ALLOWED_TRANSITIONS[record.stage]:
        raise ValueError(f"invalid learning transition {record.stage.value} -> {target.value}")
    updated = _utc(updated_at_utc, field_name="updated_at_utc")
    combined_evidence = record.evidence_ids
    if evidence_ids is not None:
        combined_evidence = tuple(
            sorted(set(record.evidence_ids) | {str(item) for item in evidence_ids if str(item)})
        )
    new_metrics = record.metrics if metrics is None else dict(metrics)
    regressions = (
        record.known_regressions
        if known_regressions is None
        else tuple(known_regressions)
    )
    return replace(
        record,
        stage=target,
        updated_at_utc=updated,
        evidence_ids=combined_evidence,
        metrics_json=_canonical_json(new_metrics),
        known_regressions=regressions,
        approving_principal=approving_principal,
        approved_at_utc=approved_at_utc,
        effective_ref=effective_ref,
        rollback_ref=rollback_ref,
        automatic_activation=False,
        automatic_promotion=False,
        trade_authority_changed=False,
    )
