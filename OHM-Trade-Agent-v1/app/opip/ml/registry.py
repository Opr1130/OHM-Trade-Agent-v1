"""Model lifecycle governance for evidence-only O'Pip challengers."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.opip.ml.contracts import ModelHealth, ModelLifecycle, ModelRegistryRecord
from app.opip.ml.temporal import require_utc


_ALLOWED = {
    ModelLifecycle.REGISTERED: {ModelLifecycle.VALIDATED},
    ModelLifecycle.VALIDATED: {ModelLifecycle.SHADOW},
    ModelLifecycle.SHADOW: {ModelLifecycle.CHALLENGER},
    ModelLifecycle.CHALLENGER: {ModelLifecycle.SHADOW},
}


def transition_model(
    record: ModelRegistryRecord,
    *,
    target: ModelLifecycle,
    approval_principal: str | None = None,
    approved_at_utc: datetime | None = None,
) -> ModelRegistryRecord:
    """Apply a controlled lifecycle transition.

    Promotion into CHALLENGER requires explicit human approval metadata.
    There is intentionally no automatic-promotion code path.
    """
    if target == record.lifecycle:
        return record
    if target not in _ALLOWED.get(record.lifecycle, set()):
        raise ValueError(
            f"invalid model transition {record.lifecycle.value} -> {target.value}"
        )
    principal = record.approval_principal
    approved = record.approved_at_utc
    if target == ModelLifecycle.CHALLENGER:
        if not str(approval_principal or "").strip() or approved_at_utc is None:
            raise ValueError("CHALLENGER promotion requires explicit approval metadata")
        principal = str(approval_principal)
        approved = require_utc(approved_at_utc, field_name="approved_at_utc")
    return replace(
        record,
        lifecycle=target,
        approval_principal=principal,
        approved_at_utc=approved,
    )


def apply_structural_health_failure(
    record: ModelRegistryRecord, *, reason: str
) -> ModelRegistryRecord:
    """Suspend evidence on objective structural failures only."""
    if not str(reason or "").strip():
        raise ValueError("reason is required")
    return replace(record, health=ModelHealth.SUSPENDED)


def mark_statistical_degradation(
    record: ModelRegistryRecord, *, sample_count: int, minimum_sample_count: int
) -> ModelRegistryRecord:
    """Statistical degradation never promotes or immediately suspends a model."""
    if sample_count < 0 or minimum_sample_count <= 0:
        raise ValueError("sample counts must be valid")
    if sample_count < minimum_sample_count:
        return record
    return replace(record, health=ModelHealth.DEGRADED)
