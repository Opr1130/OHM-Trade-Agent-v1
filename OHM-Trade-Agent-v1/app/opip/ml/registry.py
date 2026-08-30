"""Model lifecycle governance for evidence-only O'Pip challengers."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Iterable

from app.opip.ml.contracts import ModelHealth, ModelLifecycle, ModelRegistryRecord
from app.opip.ml.temporal import require_utc


_ALLOWED = {
    ModelLifecycle.REGISTERED: {ModelLifecycle.VALIDATED},
    ModelLifecycle.VALIDATED: {ModelLifecycle.SHADOW},
    ModelLifecycle.CHALLENGER: {ModelLifecycle.SHADOW},
}


def transition_model(
    record: ModelRegistryRecord,
    *,
    target: ModelLifecycle,
) -> ModelRegistryRecord:
    """Apply a non-promotion lifecycle transition.

    Promotion from SHADOW to CHALLENGER is deliberately excluded here because
    it requires registry-wide knowledge. Use promote_single_challenger so the
    one-active-challenger invariant is checked over the complete registry state.
    """
    if target == record.lifecycle:
        return record
    if target == ModelLifecycle.CHALLENGER:
        raise ValueError(
            "CHALLENGER promotion requires registry-wide promote_single_challenger"
        )
    if target not in _ALLOWED.get(record.lifecycle, set()):
        raise ValueError(
            f"invalid model transition {record.lifecycle.value} -> {target.value}"
        )
    return replace(record, lifecycle=target)


def promote_single_challenger(
    records: Iterable[ModelRegistryRecord],
    *,
    model_id: str,
    approval_principal: str,
    approved_at_utc: datetime,
) -> tuple[ModelRegistryRecord, ...]:
    """Promote exactly one SHADOW model in a complete registry view.

    Foundation v1 is persistence-neutral. A future persistent registry must
    execute the equivalent collection-level operation under its transaction or
    lock. This pure operation makes the single-CHALLENGER invariant explicit.
    """
    rows = tuple(records)
    if not str(model_id or "").strip():
        raise ValueError("model_id is required")
    if not str(approval_principal or "").strip():
        raise ValueError("approval_principal is required")
    approved = require_utc(approved_at_utc, field_name="approved_at_utc")

    matching_indexes = [
        index for index, row in enumerate(rows) if row.model_id == model_id
    ]
    if len(matching_indexes) != 1:
        raise ValueError("model_id must identify exactly one registry record")
    target_index = matching_indexes[0]
    target = rows[target_index]
    if target.lifecycle != ModelLifecycle.SHADOW:
        raise ValueError("only a SHADOW model can be promoted to CHALLENGER")
    if target.health in {ModelHealth.SUSPENDED, ModelHealth.RETIRED}:
        raise ValueError("unhealthy model cannot be promoted to CHALLENGER")

    existing = [
        row
        for index, row in enumerate(rows)
        if index != target_index and row.lifecycle == ModelLifecycle.CHALLENGER
    ]
    if existing:
        raise ValueError(
            "another CHALLENGER is already active: "
            + ", ".join(sorted(row.model_id for row in existing))
        )

    promoted = replace(
        target,
        lifecycle=ModelLifecycle.CHALLENGER,
        approval_principal=str(approval_principal),
        approved_at_utc=approved,
    )
    result = tuple(
        promoted if index == target_index else row
        for index, row in enumerate(rows)
    )
    if sum(row.lifecycle == ModelLifecycle.CHALLENGER for row in result) != 1:
        raise ValueError("registry must contain exactly one CHALLENGER after promotion")
    return result


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
    """Statistical degradation cannot override a structural suspension."""
    if sample_count < 0 or minimum_sample_count <= 0:
        raise ValueError("sample counts must be valid")
    if record.health in {ModelHealth.SUSPENDED, ModelHealth.RETIRED}:
        return record
    if sample_count < minimum_sample_count:
        return record
    return replace(record, health=ModelHealth.DEGRADED)
