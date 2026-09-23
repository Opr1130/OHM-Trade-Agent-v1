"""Deterministic, offline provider doubles.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These providers make the committee fully exercisable without a network call, a
credential, or a paid API: CI, offline bake-off runs, and adversarial leakage
tests all drive the runtime through them.

Nothing here reaches a vendor. A scripted provider is only ever used when a
caller explicitly seats it, and a seat with no adapter stays unavailable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.opip.committee.contracts import (
    CostCompleteness,
    ProviderFailureClass,
    ProviderFamily,
)
from app.opip.committee.providers import (
    CommitteeProvider,
    ProviderAvailability,
    ProviderInvocationError,
    ProviderRawResponse,
    ProviderWireRequest,
)


@dataclass
class ScriptedAnswer:
    """One canned provider reply, or one canned failure."""

    text: str | None = None
    failure_class: ProviderFailureClass | None = None
    failure_message: str = "scripted failure"
    reported_provider: str | None = None
    reported_model: str | None = None
    input_tokens: int | None = 100
    output_tokens: int | None = 50
    estimated_cost_microunits: int | None = 1_000
    cost_completeness: CostCompleteness = CostCompleteness.COMPLETE
    received_at: datetime | None = None


class ScriptedCommitteeProvider(CommitteeProvider):
    """A provider that replays a fixed script.

    The final answer repeats once the script is exhausted, so a bounded retry
    loop terminates deterministically instead of raising unexpectedly.
    """

    def __init__(
        self,
        *,
        family: ProviderFamily,
        model: str,
        answers: Sequence[ScriptedAnswer],
        availability: ProviderAvailability = ProviderAvailability.AVAILABLE,
        estimated_cost_microunits: int | None = None,
        cost_override: int | None = None,
    ) -> None:
        if not answers:
            raise ValueError("a scripted provider requires at least one answer")
        self.family = family
        self._model = model
        self._answers = tuple(answers)
        self._availability = availability
        self._estimated_cost_microunits = estimated_cost_microunits
        #: When set, reported cost differs from the modelled answer's cost, so a
        #: provider whose actual spend exceeds its pre-flight estimate can be
        #: exercised.
        self._cost_override = cost_override
        self.calls: list[ProviderWireRequest] = []

    def availability(self) -> ProviderAvailability:
        return self._availability

    def model_identifier(self) -> str:
        return self._model

    def estimate_cost_microunits(self, request: ProviderWireRequest) -> int | None:
        return self._estimated_cost_microunits

    def invoke(self, request: ProviderWireRequest) -> ProviderRawResponse:
        self.calls.append(request)
        index = min(len(self.calls), len(self._answers)) - 1
        answer = self._answers[index]
        if answer.failure_class is not None:
            raise ProviderInvocationError(
                answer.failure_message, failure_class=answer.failure_class
            )
        received_at = answer.received_at or datetime.now(timezone.utc)
        return ProviderRawResponse(
            reported_provider=answer.reported_provider or self.family.value,
            reported_model=answer.reported_model or self._model,
            text=answer.text if answer.text is not None else "",
            received_at=received_at,
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
        estimated_cost_microunits=(
            answer.estimated_cost_microunits
            if self._cost_override is None
            else self._cost_override
        ),
            cost_completeness=answer.cost_completeness,
        )


@dataclass
class RecordingTransport:
    """A transport that records wire payloads and returns one canned response.

    Used to prove properties of exactly what would have left the process.
    """

    reported_provider: str = ProviderFamily.OPENAI.value
    reported_model: str = "recorded-model"
    text: str = "{}"
    received_at: datetime | None = None
    error: Exception | None = None
    requests: list[ProviderWireRequest] = field(default_factory=list)

    def __call__(self, request: ProviderWireRequest) -> ProviderRawResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return ProviderRawResponse(
            reported_provider=self.reported_provider,
            reported_model=self.reported_model,
            text=self.text,
            received_at=self.received_at or datetime.now(timezone.utc),
        )


def opinion_json(
    *,
    assessment: str = "SUPPORTIVE",
    evidence_sufficiency: str = "SUFFICIENT",
    hypothesis: str = "the recorded evidence is consistent with the working hypothesis",
    confidence: int | None = 60,
    lists: Mapping[str, Sequence[str]] | None = None,
    recommended_research_action: str = "NO_ACTION",
    abstention_reason: str | None = None,
    overrides: Mapping[str, Any] | None = None,
    drop: Sequence[str] = (),
) -> str:
    """Build a valid opinion document, optionally mutated for adversarial tests.

    The repeated string-list fields are supplied through a single ``lists``
    mapping rather than one parameter each, which keeps the builder's signature
    small enough to review at a glance.
    """
    payload: dict[str, Any] = {
        "schema_version": 1,
        "evidence_sufficiency": evidence_sufficiency,
        "assessment": assessment,
        "hypothesis": hypothesis,
        "confidence": confidence,
        "recommended_research_action": recommended_research_action,
        "abstention_reason": abstention_reason,
    }
    for list_field in _OPINION_LIST_FIELDS:
        payload[list_field] = list(dict(lists or {}).get(list_field, ()))
    for key in drop:
        payload.pop(key, None)
    if overrides:
        payload.update(dict(overrides))
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


#: The opinion fields that carry repeated strings rather than a scalar.
_OPINION_LIST_FIELDS = (
    "supporting_evidence_refs",
    "contradicting_evidence_refs",
    "major_assumptions",
    "risk_factors",
    "missing_evidence",
    "alternative_explanations",
)


__all__ = [
    "RecordingTransport",
    "ScriptedAnswer",
    "ScriptedCommitteeProvider",
    "opinion_json",
]
