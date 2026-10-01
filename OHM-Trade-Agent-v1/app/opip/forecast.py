"""The pure v1 Forecast Engine (R3 F6).

One pure engine sits after the F5 Feasibility & Safety seam. It consumes an F5
``FeasibilityDecision`` whose overall disposition is ``FEASIBLE``, an ACTIVE F4
``OpportunityEpisode`` for lineage, a deterministic ``ForecastInputVector`` and
an explicit UTC ``evaluation_time``, and it returns an immutable
``ForecastDecision``.

Design boundaries enforced here:

* Missing evidence is never favorable evidence. With no registered calibrated
  model the engine returns ``INSUFFICIENT_EVIDENCE`` with the machine-readable
  reason ``NO_CALIBRATED_MODEL``. A ``RESEARCH_ONLY`` artifact is refused the
  same way. Only a structurally valid, compatible ``CALIBRATED_SHADOW`` artifact
  whose evaluation report is present in the supplied trusted metadata *and* whose
  kind has an explicitly registered trusted adapter may produce a ``FORECAST``.
  Even then the result stays shadow / non-authoritative.
* An upstream F5 ``VETO`` or ``INSUFFICIENT_EVIDENCE`` is not converted into an
  F6 abstention: it fails closed with :class:`ForecastContractError` so the
  upstream attribution is never lost.
* The engine is pure over its declared inputs: it reads no clock, environment,
  filesystem, database or network, opens no model file, uses no subprocess or
  randomness, and holds no mutable global state. ``evaluation_time`` is the only
  timing input.
* No score is promoted into a probability. The engine derives probability only
  from a trusted adapter's validated output, never from a confidence, a ranking
  score or a Committee rubric.

SHADOW / NON-AUTHORITATIVE. This engine is a research artifact. It allocates no
capital, ranks nothing, reserves no cash, sizes no position, and places no paper
or funded order. It is not wired into ``run_cycle`` or ``scan_opportunities``,
it activates no Feature Bus, and it writes no canonical evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Protocol

from app.opip.contracts import (
    FeasibilityDecision,
    FeasibilityDisposition,
    ForecastAbstentionReason,
    ForecastContractError,
    ForecastDecision,
    ForecastInputVector,
    ForecastModelArtifact,
    ForecastModelKind,
    ForecastModelOutput,
    ForecastModelStatus,
    ForecastPolicy,
    ForecastRequest,
    ForecastStatus,
    OpportunityEpisode,
    OpportunityLifecycleState,
    forecast_decision_identity,
    forecast_horizon_identity,
    require_forecast_enum,
    require_forecast_utc,
)


class ForecastModelAdapter(Protocol):
    """The trusted model protocol.

    An adapter is an explicitly implemented, in-repository object. There is no
    dynamic import of an attacker-controlled name, no ``eval``/``exec`` and no
    pickle; the artifact only supplies deterministic JSON-safe parameters.
    """

    kind: ForecastModelKind

    def predict(self, request: ForecastRequest) -> ForecastModelOutput:  # pragma: no cover
        ...


def _require_adapter(adapter: object) -> ForecastModelAdapter:
    """Validate one trusted adapter by duck-typing its declared kind and predict."""
    kind = getattr(adapter, "kind", None)
    predict = getattr(adapter, "predict", None)
    if kind is None or not callable(predict):
        raise ForecastContractError(
            "a model adapter must implement kind and predict(request)"
        )
    require_forecast_enum(ForecastModelKind, kind, field_name="adapter.kind")
    return adapter  # type: ignore[return-value]


@dataclass(frozen=True)
class TrustedForecastModelRegistry:
    """The explicit, immutable trusted model registry.

    It maps a model *kind* to an explicitly implemented adapter and carries the
    set of evaluation-report ids that the surrounding trusted metadata vouches
    for. A production registry is constructed with ``allow_test_adapters=False``
    and therefore refuses to hold a ``SYNTHETIC_TEST_ONLY`` adapter at all, so a
    synthetic/test-only model can never execute in production.
    """

    adapters: Mapping[ForecastModelKind, ForecastModelAdapter] = field(
        default_factory=dict
    )
    known_evaluation_report_ids: frozenset[str] = frozenset()
    allow_test_adapters: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.adapters, Mapping):
            raise ForecastContractError("adapters must be a mapping")
        normalized: dict[ForecastModelKind, ForecastModelAdapter] = {}
        for key, adapter in self.adapters.items():
            kind = require_forecast_enum(ForecastModelKind, key, field_name="adapter kind")
            _require_adapter(adapter)
            if kind is ForecastModelKind.SYNTHETIC_TEST_ONLY and not self.allow_test_adapters:
                raise ForecastContractError(
                    "the production registry refuses a SYNTHETIC_TEST_ONLY adapter"
                )
            normalized[kind] = adapter
        object.__setattr__(self, "adapters", MappingProxyType(normalized))
        reports = frozenset(
            str(item) for item in self.known_evaluation_report_ids
        )
        for report in reports:
            if report == "" or report != report.strip():
                raise ForecastContractError(
                    "known evaluation report ids must be non-empty tokens"
                )
        object.__setattr__(self, "known_evaluation_report_ids", reports)
        if not isinstance(self.allow_test_adapters, bool):
            raise ForecastContractError("allow_test_adapters must be a bool")

    @classmethod
    def empty(cls) -> "TrustedForecastModelRegistry":
        return cls(adapters={}, known_evaluation_report_ids=frozenset())

    def register(self, adapter: ForecastModelAdapter) -> "TrustedForecastModelRegistry":
        """Return a new registry with ``adapter`` added; refuse a test-only adapter.

        Registration is explicit and additive: the production registry never
        auto-promotes a model. A ``SYNTHETIC_TEST_ONLY`` adapter is refused unless
        this registry is an explicit, test-constructed registry.
        """
        checked = _require_adapter(adapter)
        kind = require_forecast_enum(
            ForecastModelKind, getattr(checked, "kind"), field_name="adapter.kind"
        )
        if kind is ForecastModelKind.SYNTHETIC_TEST_ONLY and not self.allow_test_adapters:
            raise ForecastContractError(
                "a SYNTHETIC_TEST_ONLY adapter cannot enter the production registry"
            )
        merged = dict(self.adapters)
        merged[kind] = checked
        return TrustedForecastModelRegistry(
            adapters=merged,
            known_evaluation_report_ids=self.known_evaluation_report_ids,
            allow_test_adapters=self.allow_test_adapters,
        )

    def vouch_for_report(self, report_id: str) -> "TrustedForecastModelRegistry":
        return TrustedForecastModelRegistry(
            adapters=dict(self.adapters),
            known_evaluation_report_ids=self.known_evaluation_report_ids | {report_id},
            allow_test_adapters=self.allow_test_adapters,
        )

    def adapter_for(self, kind: ForecastModelKind) -> ForecastModelAdapter | None:
        return self.adapters.get(kind)

    @property
    def registered_adapter_count(self) -> int:
        return len(self.adapters)


PRODUCTION_MODEL_REGISTRY = TrustedForecastModelRegistry.empty()


def _require_policy(policy: object) -> ForecastPolicy:
    if not isinstance(policy, ForecastPolicy):
        raise ForecastContractError("policy must be a ForecastPolicy")
    return policy


def _require_active_episode(episode: object) -> OpportunityEpisode:
    if not isinstance(episode, OpportunityEpisode):
        raise ForecastContractError(
            "episode must be an F4 OpportunityEpisode"
        )
    if episode.lifecycle_state is not OpportunityLifecycleState.ACTIVE:
        raise ForecastContractError(
            "F6 evaluates only the FEASIBLE decision of an ACTIVE F4 episode"
        )
    return episode


def _require_feasible_decision(
    feasibility: object, episode: OpportunityEpisode
) -> FeasibilityDecision:
    """F6 consumes exactly an F5 decision whose disposition is ``FEASIBLE``.

    An upstream ``VETO`` or ``INSUFFICIENT_EVIDENCE`` fails closed here rather
    than being converted into an F6 abstention, and the preserved F4/F5 lineage
    must agree with the supplied episode so a foreign decision cannot be stamped
    with this episode's lineage.
    """
    if not isinstance(feasibility, FeasibilityDecision):
        raise ForecastContractError("feasibility must be an F5 FeasibilityDecision")
    if feasibility.disposition is not FeasibilityDisposition.FEASIBLE:
        raise ForecastContractError(
            "F6 requires an F5 disposition of FEASIBLE; a VETO or "
            "INSUFFICIENT_EVIDENCE fails closed"
        )
    if feasibility.episode_id != episode.episode_id:
        raise ForecastContractError("feasibility decision episode lineage does not match")
    if feasibility.source_claim_id != episode.source_claim_id:
        raise ForecastContractError("feasibility decision claim lineage does not match")
    if feasibility.instrument_version_id != episode.instrument_version_id:
        raise ForecastContractError(
            "feasibility decision instrument version does not match"
        )
    if feasibility.venue_instrument_id != episode.venue_instrument_id:
        raise ForecastContractError("feasibility decision venue instrument does not match")
    if feasibility.detector_snapshot_id != episode.snapshot_id:
        raise ForecastContractError(
            "feasibility decision detector snapshot does not match"
        )
    return feasibility


def _require_inputs(inputs: object) -> ForecastInputVector:
    if not isinstance(inputs, ForecastInputVector):
        raise ForecastContractError("inputs must be a ForecastInputVector")
    return inputs


def _require_input_point_in_time(
    inputs: ForecastInputVector, evaluation_time: datetime
) -> None:
    """Reject any feature or source cutoff that was not available at decision time.

    Only point-in-time evidence available at the declared evaluation time may
    enter a forecast: a feature whose availability, or a source cutoff, is later
    than the evaluation time is a future-leakage defect and fails closed.
    """
    if inputs.source_cutoff > evaluation_time:
        raise ForecastContractError(
            "input source cutoff must not exceed the evaluation time"
        )
    for feature in inputs.features:
        if feature.available_at_utc > evaluation_time:
            raise ForecastContractError(
                f"feature {feature.name!r} was not available at the evaluation time"
            )


def _require_upstream_point_in_time(
    episode: OpportunityEpisode,
    feasibility: FeasibilityDecision,
    evaluation_time: datetime,
) -> None:
    """Reject an upstream F4/F5 input that was not available at the forecast instant.

    The F5 decision and the F4 episode must have existed no later than the
    forecast evaluation time; otherwise stamping their lineage into a forecast
    would introduce future leakage into the forecast identity.
    """
    if feasibility.evaluation_time > evaluation_time:
        raise ForecastContractError(
            "the F5 decision was not available at the forecast evaluation time"
        )
    if episode.claim_evaluation_cutoff > evaluation_time:
        raise ForecastContractError(
            "the F4 episode was not available at the forecast evaluation time"
        )


def _require_artifact_compatible(
    artifact: ForecastModelArtifact,
    inputs: ForecastInputVector,
    evaluation_time: datetime,
) -> None:
    """Enforce exact model/input compatibility as a structural invariant."""
    if artifact.input_schema_id != inputs.input_schema_id:
        raise ForecastContractError("model input schema id does not match the inputs")
    if artifact.input_schema_version != inputs.input_schema_version:
        raise ForecastContractError("model input schema version does not match the inputs")
    if artifact.input_schema_fingerprint != inputs.input_schema_fingerprint:
        raise ForecastContractError(
            "model input schema fingerprint does not match the inputs"
        )
    if artifact.horizon_contract != inputs.horizon:
        raise ForecastContractError("model horizon contract does not match the inputs")
    if artifact.training_cutoff > evaluation_time:
        raise ForecastContractError(
            "a model artifact trained after the evaluation time cannot be applied"
        )
    if artifact.expires_at is not None and artifact.expires_at <= evaluation_time:
        raise ForecastContractError("the model artifact has expired")


def _validate_output(output: object) -> ForecastModelOutput:
    if not isinstance(output, ForecastModelOutput):
        raise ForecastContractError(
            "a trusted adapter must return a ForecastModelOutput"
        )
    return output


def _abstain(
    *,
    policy: ForecastPolicy,
    episode_id: str,
    feasibility_decision_id: str,
    input_fingerprint: str,
    evaluation_time: datetime,
    reason: ForecastAbstentionReason,
    model_artifact_id: str | None,
) -> ForecastDecision:
    identity = forecast_decision_identity(
        decision_schema_version=policy.decision_schema_version,
        status=ForecastStatus.INSUFFICIENT_EVIDENCE,
        abstention_reason=reason,
        episode_id=episode_id,
        feasibility_decision_id=feasibility_decision_id,
        input_fingerprint=input_fingerprint,
        evaluation_time=evaluation_time,
        horizon_identity=None,
        model_artifact_id=model_artifact_id,
        engine_version=policy.engine_version,
        policy_version=policy.policy_version,
        valid_until=None,
        entry_distribution=None,
        post_fill_distribution=None,
        expected_return_unconditional=None,
        expected_return_conditional_on_fill=None,
        uncertainty=None,
        evidence_references=(),
    )
    return ForecastDecision(
        decision_id=identity,
        decision_schema_version=policy.decision_schema_version,
        engine_version=policy.engine_version,
        policy_version=policy.policy_version,
        status=ForecastStatus.INSUFFICIENT_EVIDENCE,
        abstention_reason=reason,
        episode_id=episode_id,
        feasibility_decision_id=feasibility_decision_id,
        input_fingerprint=input_fingerprint,
        evaluation_time=evaluation_time,
        horizon=None,
        valid_until=None,
        model_artifact_id=model_artifact_id,
        entry_distribution=None,
        post_fill_distribution=None,
        expected_return_unconditional=None,
    )


def evaluate_forecast(
    episode: OpportunityEpisode,
    feasibility: FeasibilityDecision,
    inputs: ForecastInputVector,
    evaluation_time: datetime,
    policy: ForecastPolicy,
    model_artifact: ForecastModelArtifact | None = None,
    registry: TrustedForecastModelRegistry | None = None,
) -> ForecastDecision:
    """Evaluate one forecast request. Pure and deterministic.

    ``registry`` is the supplied trusted metadata. When omitted, the empty
    production registry is used, so the result is deterministically
    ``INSUFFICIENT_EVIDENCE``.
    """
    policy = _require_policy(policy)
    evaluation_time = require_forecast_utc(evaluation_time, field_name="evaluation_time")
    episode = _require_active_episode(episode)
    feasibility = _require_feasible_decision(feasibility, episode)
    inputs = _require_inputs(inputs)
    _require_input_point_in_time(inputs, evaluation_time)
    _require_upstream_point_in_time(episode, feasibility, evaluation_time)

    request = ForecastRequest(
        episode_id=episode.episode_id,
        feasibility_decision_id=feasibility.decision_id,
        input_vector=inputs,
        evaluation_time=evaluation_time,
        policy=policy,
        model_artifact=model_artifact,
    )

    if model_artifact is None:
        return _abstain(
            policy=policy,
            episode_id=request.episode_id,
            feasibility_decision_id=request.feasibility_decision_id,
            input_fingerprint=request.input_fingerprint,
            evaluation_time=evaluation_time,
            reason=ForecastAbstentionReason.NO_CALIBRATED_MODEL,
            model_artifact_id=None,
        )

    if not isinstance(model_artifact, ForecastModelArtifact):
        raise ForecastContractError("model_artifact must be a ForecastModelArtifact")

    _require_artifact_compatible(model_artifact, inputs, evaluation_time)

    if model_artifact.model_status is ForecastModelStatus.RESEARCH_ONLY:
        return _abstain(
            policy=policy,
            episode_id=request.episode_id,
            feasibility_decision_id=request.feasibility_decision_id,
            input_fingerprint=request.input_fingerprint,
            evaluation_time=evaluation_time,
            reason=ForecastAbstentionReason.MODEL_NOT_CALIBRATED_FOR_SHADOW_AUTHORITY,
            model_artifact_id=model_artifact.artifact_id,
        )

    active_registry = registry if registry is not None else PRODUCTION_MODEL_REGISTRY
    if not isinstance(active_registry, TrustedForecastModelRegistry):
        raise ForecastContractError(
            "registry must be a TrustedForecastModelRegistry"
        )
    if model_artifact.calibration_report_id not in (
        active_registry.known_evaluation_report_ids
    ):
        return _abstain(
            policy=policy,
            episode_id=request.episode_id,
            feasibility_decision_id=request.feasibility_decision_id,
            input_fingerprint=request.input_fingerprint,
            evaluation_time=evaluation_time,
            reason=ForecastAbstentionReason.CALIBRATION_REPORT_UNTRUSTED,
            model_artifact_id=model_artifact.artifact_id,
        )

    adapter = active_registry.adapter_for(model_artifact.model_kind)
    if adapter is None:
        return _abstain(
            policy=policy,
            episode_id=request.episode_id,
            feasibility_decision_id=request.feasibility_decision_id,
            input_fingerprint=request.input_fingerprint,
            evaluation_time=evaluation_time,
            reason=ForecastAbstentionReason.UNSUPPORTED_MODEL_KIND,
            model_artifact_id=model_artifact.artifact_id,
        )

    output = _validate_output(adapter.predict(request))
    if output.uncertainty is None:
        return _abstain(
            policy=policy,
            episode_id=request.episode_id,
            feasibility_decision_id=request.feasibility_decision_id,
            input_fingerprint=request.input_fingerprint,
            evaluation_time=evaluation_time,
            reason=ForecastAbstentionReason.UNCERTAINTY_UNAVAILABLE,
            model_artifact_id=model_artifact.artifact_id,
        )

    valid_until = evaluation_time + timedelta(seconds=inputs.horizon.validity_seconds)
    identity = forecast_decision_identity(
        decision_schema_version=policy.decision_schema_version,
        status=ForecastStatus.FORECAST,
        abstention_reason=None,
        episode_id=request.episode_id,
        feasibility_decision_id=request.feasibility_decision_id,
        input_fingerprint=request.input_fingerprint,
        evaluation_time=evaluation_time,
        horizon_identity=forecast_horizon_identity(inputs.horizon),
        model_artifact_id=model_artifact.artifact_id,
        engine_version=policy.engine_version,
        policy_version=policy.policy_version,
        valid_until=valid_until,
        entry_distribution=output.entry_distribution,
        post_fill_distribution=output.post_fill_distribution,
        expected_return_unconditional=output.expected_return_unconditional,
        expected_return_conditional_on_fill=output.expected_return_conditional_on_fill,
        uncertainty=output.uncertainty,
        evidence_references=(model_artifact.calibration_report_id,),
    )
    return ForecastDecision(
        decision_id=identity,
        decision_schema_version=policy.decision_schema_version,
        engine_version=policy.engine_version,
        policy_version=policy.policy_version,
        status=ForecastStatus.FORECAST,
        abstention_reason=None,
        episode_id=request.episode_id,
        feasibility_decision_id=request.feasibility_decision_id,
        input_fingerprint=request.input_fingerprint,
        evaluation_time=evaluation_time,
        horizon=inputs.horizon,
        valid_until=valid_until,
        model_artifact_id=model_artifact.artifact_id,
        entry_distribution=output.entry_distribution,
        post_fill_distribution=output.post_fill_distribution,
        expected_return_unconditional=output.expected_return_unconditional,
        expected_return_conditional_on_fill=output.expected_return_conditional_on_fill,
        uncertainty=output.uncertainty,
        evidence_references=(model_artifact.calibration_report_id,),
    )


__all__ = [
    "ForecastModelAdapter",
    "PRODUCTION_MODEL_REGISTRY",
    "TrustedForecastModelRegistry",
    "evaluate_forecast",
]
