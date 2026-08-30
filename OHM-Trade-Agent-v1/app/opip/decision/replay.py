"""Compatible-runtime historical replay for O'Pip Decision V2."""
from __future__ import annotations

from dataclasses import fields as dataclass_fields
from types import SimpleNamespace
from typing import Any, Mapping

from app.opip.decision.engine import CandidateEvidence, OPipDecisionEngine
from app.opip.decision.evidence import OPipDecisionEvidence
from app.opip.decision.funnel import AIStageEvidence
from app.opip.decision.models_v2 import (
    AdmissionDecisionV2,
    DecisionRole,
    ENGINE_VERSION,
    from_v1_decision,
)
from app.opip.decision.versioning import app_code_fingerprint, gate_policy_fingerprint


class PolicyVersionMismatchError(RuntimeError):
    pass


class EngineVersionMismatchError(RuntimeError):
    pass


class EngineCodeFingerprintMismatchError(RuntimeError):
    pass


class ExactHistoricalReplayUnavailableError(RuntimeError):
    pass


def _namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(
            **{key: _namespace(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value


def _restore_ai_stage(payload: Mapping[str, Any] | None) -> AIStageEvidence:
    if not payload:
        return AIStageEvidence()
    allowed = {field.name for field in dataclass_fields(AIStageEvidence)}
    values = {key: value for key, value in payload.items() if key in allowed}
    if "confidences" in values:
        values["confidences"] = list(values["confidences"] or [])
    return AIStageEvidence(**values)


def verify_exact_replay_runtime(
    evidence: OPipDecisionEvidence,
    *,
    engine_version: str = ENGINE_VERSION,
) -> None:
    """Fail rather than pretend current code is an old policy/runtime."""
    evidence.gate_policy_snapshot.validate_integrity()
    if gate_policy_fingerprint() != evidence.gate_policy_snapshot.policy_fingerprint:
        raise PolicyVersionMismatchError(
            "frozen policy differs from this runtime; exact replay requires "
            "a matching policy/code checkout"
        )
    if engine_version != ENGINE_VERSION:
        raise EngineVersionMismatchError(
            f"requested engine {engine_version} != {ENGINE_VERSION}"
        )
    current_code = app_code_fingerprint()
    if current_code != evidence.engine_code_fingerprint:
        raise EngineCodeFingerprintMismatchError(
            "application code fingerprint differs from frozen evidence; "
            "exact replay requires the matching source checkout"
        )


def replay_decision(
    evidence: OPipDecisionEvidence,
    *,
    engine_version: str = ENGINE_VERSION,
    decision_role: DecisionRole = DecisionRole.SHADOW_ENGINE,
) -> AdmissionDecisionV2:
    """Re-evaluate only the sealed bundle; no current-state lookup is allowed."""
    verify_exact_replay_runtime(evidence, engine_version=engine_version)

    snapshot = _namespace(evidence.candidate_snapshot)
    ai_stage = _restore_ai_stage(evidence.ai_evidence)
    engine = OPipDecisionEngine(
        account_equity=evidence.account_equity,
        decision_at=evidence.decision_time_utc,
        ai_stage=ai_stage,
    )
    v1 = engine.evaluate(
        CandidateEvidence(
            snapshot=snapshot,
            episode_id=evidence.episode_id,
            signal_id=evidence.signal_id,
            asset_display_name=evidence.asset_display_name,
            pair=evidence.pair,
            ai_item=evidence.ai_item,
            market_intelligence=evidence.market_intelligence,
        )
    )
    return from_v1_decision(
        v1,
        evidence=evidence,
        decision_role=decision_role,
        engine_version=engine_version,
    )
