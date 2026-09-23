"""Provider isolation for the Intelligence Committee.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Every provider-specific concern lives behind an adapter. The runtime, the
evaluation layer, and the attribution layer never branch on a provider name.

A provider adapter receives an already-screened :class:`ProviderWireRequest`
and returns a :class:`ProviderRawResponse`, or raises a typed
:class:`ProviderInvocationError`. Adapters perform no retry of their own: the
runtime owns bounded, idempotent retry so a lost acknowledgement can never
manufacture a second logical opinion.

No credential is ever constructed, read, or stored here. A seat whose adapter
is absent or unconfigured reports ``UNAVAILABLE`` rather than being silently
substituted with another model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from app.opip.committee.contracts import (
    CostCompleteness,
    ProviderFailureClass,
    ProviderFamily,
)
from app.opip.decision_intelligence.serialization import require_utc

#: Failure classes where a bounded second attempt is worth making. A malformed
#: or schema-invalid response is not retried: the model answered, the contract
#: was not met, and repeating the call spends money without changing that.
RETRYABLE_FAILURE_CLASSES = frozenset(
    {
        ProviderFailureClass.TIMEOUT,
        ProviderFailureClass.RATE_LIMIT,
        ProviderFailureClass.PROVIDER_UNAVAILABLE,
        ProviderFailureClass.INTERNAL_ERROR,
    }
)


class ProviderAvailability(str, Enum):
    """Whether a seated provider can actually be reached right now."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class ProviderInvocationError(Exception):
    """A typed provider failure raised by an adapter."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: ProviderFailureClass,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class


@dataclass(frozen=True)
class ProviderWireRequest:
    """The exact, already-screened payload handed to a provider adapter."""

    case_id: str
    logical_observation_id: str
    model: str
    system_prompt: str
    user_payload: Mapping[str, Any]
    max_output_tokens: int
    timeout_seconds: int

    def __post_init__(self) -> None:
        for field_name in ("case_id", "logical_observation_id", "model", "system_prompt"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.user_payload, Mapping):
            raise ValueError("user_payload must be a mapping")
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        if type(self.timeout_seconds) is not int or self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be a positive integer")


@dataclass(frozen=True)
class ProviderRawResponse:
    """What an adapter returns before validation.

    Everything here is untrusted input until the runtime has validated it.
    """

    reported_provider: str
    reported_model: str
    text: str
    received_at: datetime
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_microunits: int | None = None
    cost_completeness: CostCompleteness = CostCompleteness.UNKNOWN
    raw_response_ref: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("reported_provider", "reported_model"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        object.__setattr__(
            self,
            "received_at",
            require_utc(self.received_at, field_name="received_at"),
        )
        for field_name in (
            "input_tokens",
            "output_tokens",
            "estimated_cost_microunits",
        ):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer")
        if not isinstance(self.cost_completeness, CostCompleteness):
            raise ValueError("invalid cost_completeness")


class ProviderTransport(Protocol):
    """The single injection point between the committee and a model vendor.

    A transport is supplied by whoever owns credentials and network policy. The
    committee plane never reads a credential and never imports a vendor SDK.
    """

    def __call__(self, request: ProviderWireRequest) -> ProviderRawResponse:
        ...


class CommitteeProvider(ABC):
    """One seated committee member.

    Implementations are stateless with respect to other seats: a provider can
    never observe another provider's answer, because it is only ever handed the
    shared evidence snapshot.
    """

    #: The logical family this seat occupies.
    family: ProviderFamily

    @abstractmethod
    def availability(self) -> ProviderAvailability:
        """Report whether this seat can be reached. Never raises."""

    @abstractmethod
    def model_identifier(self) -> str:
        """The model identity that will be requested. Never raises."""

    @abstractmethod
    def invoke(self, request: ProviderWireRequest) -> ProviderRawResponse:
        """Perform exactly one attempt. Raises :class:`ProviderInvocationError`."""

    def estimate_cost_microunits(self, request: ProviderWireRequest) -> int | None:
        """Optional pre-flight cost estimate, or ``None`` when unknown."""
        return None


class UnavailableProvider(CommitteeProvider):
    """A seated provider with no supported integration.

    This is the honest representation of a seat that exists in the architecture
    but cannot be reached: it produces no opinion and is never substituted.
    """

    def __init__(self, family: ProviderFamily, *, model: str = "unavailable") -> None:
        self.family = family
        self._model = model

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability.UNAVAILABLE

    def model_identifier(self) -> str:
        return self._model

    def invoke(self, request: ProviderWireRequest) -> ProviderRawResponse:
        raise ProviderInvocationError(
            f"provider {self.family.value} has no supported integration",
            failure_class=ProviderFailureClass.PROVIDER_UNAVAILABLE,
        )


class TransportBackedProvider(CommitteeProvider):
    """Provider adapter bound to an injected transport.

    The only place a vendor-specific exception becomes a typed committee
    failure class. No retry happens here.
    """

    def __init__(
        self,
        *,
        family: ProviderFamily,
        model: str,
        transport: ProviderTransport,
        cost_estimator: Callable[[ProviderWireRequest], int | None] | None = None,
    ) -> None:
        self.family = family
        self._model = model
        self._transport = transport
        self._cost_estimator = cost_estimator

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability.AVAILABLE

    def model_identifier(self) -> str:
        return self._model

    def estimate_cost_microunits(self, request: ProviderWireRequest) -> int | None:
        if self._cost_estimator is None:
            return None
        try:
            return self._cost_estimator(request)
        except Exception:
            # An unknown estimate must never block a case or invent a cost.
            return None

    def invoke(self, request: ProviderWireRequest) -> ProviderRawResponse:
        try:
            return self._transport(request)
        except ProviderInvocationError:
            raise
        except TimeoutError as exc:
            raise ProviderInvocationError(
                f"{self.family.value} timed out",
                failure_class=ProviderFailureClass.TIMEOUT,
            ) from exc
        except OSError as exc:
            raise ProviderInvocationError(
                f"{self.family.value} transport failed",
                failure_class=ProviderFailureClass.PROVIDER_UNAVAILABLE,
            ) from exc
        except ValueError as exc:
            raise ProviderInvocationError(
                f"{self.family.value} returned an unusable payload",
                failure_class=ProviderFailureClass.MALFORMED_RESPONSE,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - unknown failures stay non-fatal.
            raise ProviderInvocationError(
                f"{self.family.value} raised {type(exc).__name__}",
                failure_class=ProviderFailureClass.INTERNAL_ERROR,
            ) from exc


def resolve_seated_providers(
    *,
    policy_families: tuple[ProviderFamily, ...],
    providers: Mapping[ProviderFamily, CommitteeProvider],
) -> dict[ProviderFamily, CommitteeProvider]:
    """Bind seated families to adapters, defaulting to unavailable.

    Any seated family with no adapter becomes an explicitly unavailable seat.
    No family is ever dropped and no family is ever substituted.
    """
    resolved: dict[ProviderFamily, CommitteeProvider] = {}
    for family in policy_families:
        provider = providers.get(family)
        if provider is None:
            resolved[family] = UnavailableProvider(family)
            continue
        if provider.family is not family:
            raise ValueError(
                f"provider adapter for {family.value} declares family "
                f"{provider.family.value}"
            )
        resolved[family] = provider
    return resolved


__all__ = [
    "CommitteeProvider",
    "ProviderAvailability",
    "ProviderInvocationError",
    "ProviderRawResponse",
    "ProviderTransport",
    "ProviderWireRequest",
    "RETRYABLE_FAILURE_CLASSES",
    "TransportBackedProvider",
    "UnavailableProvider",
    "resolve_seated_providers",
]
