"""Governed AI model registry and role routing.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This is the committee's own governed release artifact. It is **not** the Cursor
development-agent model routing, and it is **not** a place to record a vendor
price guess: pricing lives in :mod:`app.opip.committee.pricing` as configuration.

The registry answers exactly one question: *which* provider/model route is
permitted to answer a given role, under which prompt/schema/budget, as of when.
Everything a result needs to be attributable travels in the route, so a result
can never be explained by ambient state that was not part of the contract:

* a role's route is **primary plus at most one approved fallback** - no chain of
  escalating models, because an unbounded chain is an unbounded spend and an
  unbounded change of reasoning effort;
* the fallback receives **the same request, the same total deadline, and the same
  total monetary and token reservation**. It never receives a fresh budget, so a
  fallback cannot double the cost of a case;
* an unregistered provider/model alias is **rejected**, and a suspended or
  expired entry is refused rather than used, so version drift surfaces as a
  governed refusal instead of a silently different model answering the role;
* an entry carries its own review/expiry date, so "approved once" cannot mean
  "approved forever".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from app.opip.committee.contracts import ProviderFamily
from app.opip.committee.roles import CommitteeRole
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

#: Schema version for a registry entry and for a registry release.
MODEL_REGISTRY_ENTRY_SCHEMA_VERSION = 1
MODEL_REGISTRY_SCHEMA_VERSION = 1

MODEL_REGISTRY_ENTRY_IDENTITY_DOMAIN = "COMMITTEE-MODEL-ENTRY"
MODEL_REGISTRY_IDENTITY_DOMAIN = "COMMITTEE-MODEL-REGISTRY"
ROLE_ROUTE_IDENTITY_DOMAIN = "COMMITTEE-ROLE-ROUTE"


class RegistryError(ValueError):
    """A route could not be resolved under the governed registry."""


class ApprovalState(str, Enum):
    """Governed approval state of a registry entry.

    Only ``APPROVED`` may be routed to. ``PROVISIONAL`` is a bake-off research
    state and is explicitly not production approval, so it is refused here rather
    than being usable by accident.
    """

    PROVISIONAL = "PROVISIONAL"
    APPROVED = "APPROVED"
    SUSPENDED = "SUSPENDED"
    ROLLED_BACK = "ROLLED_BACK"


class ReasoningMode(str, Enum):
    """Declared reasoning effort. A route may not silently change it."""

    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ModelIdKind(str, Enum):
    """Whether a model id is pinned or floats.

    The requirement is *provider-defined fixed/versioned model id*, not literally a
    date suffix: some providers use a dated snapshot id and others ship a fixed
    versioned id with no date in it. What must never be accepted is a rolling alias,
    because an alias that silently points at a newer model would change what
    answered a role without changing the registry - exactly the drift the registry
    exists to prevent.
    """

    FIXED = "FIXED"
    ROLLING_ALIAS = "ROLLING_ALIAS"


@dataclass(frozen=True)
class ModelRegistryEntry:
    """One governed provider/model entry for one role."""

    entry_id: str
    role: CommitteeRole
    provider_family: ProviderFamily
    model_id: str
    endpoint: str
    prompt_hash: str
    schema_hash: str
    owner: str
    approval: ApprovalState
    effective_from: datetime
    review_by: datetime
    #: Whether ``model_id`` is a pinned id or a rolling alias. A rolling alias is
    #: refused at routing time, since it would let the served model drift without
    #: the registry recording a change.
    model_id_kind: ModelIdKind = ModelIdKind.FIXED
    reasoning_mode: ReasoningMode = ReasoningMode.NONE
    max_output_tokens: int | None = None
    deadline_seconds: int | None = None
    max_cost_microunits: int | None = None
    data_retention_route: str | None = None
    provider_reported_version: str | None = None
    rollback_ref: str | None = None
    schema_version: int = MODEL_REGISTRY_ENTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_REGISTRY_ENTRY_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported ModelRegistryEntry schema_version")
        if not isinstance(self.role, CommitteeRole):
            raise ValueError("invalid role")
        if not isinstance(self.provider_family, ProviderFamily):
            raise ValueError("invalid provider_family")
        if not isinstance(self.approval, ApprovalState):
            raise ValueError("invalid approval state")
        if not isinstance(self.reasoning_mode, ReasoningMode):
            raise ValueError("invalid reasoning_mode")
        if not isinstance(self.model_id_kind, ModelIdKind):
            raise ValueError("invalid model_id_kind")
        if self.model_id_kind is ModelIdKind.ROLLING_ALIAS:
            # Refused at construction, not merely at routing: a registry that can
            # hold an alias is a registry whose entries can silently drift.
            raise RegistryError(
                f"entry {self.entry_id!r} declares a rolling alias "
                f"({self.model_id!r}); a governed entry requires a provider-defined "
                "fixed or versioned model id"
            )
        for field_name in (
            "entry_id",
            "model_id",
            "endpoint",
            "prompt_hash",
            "schema_hash",
            "owner",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        for field_name in (
            "max_output_tokens",
            "deadline_seconds",
            "max_cost_microunits",
        ):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{field_name} must be a positive integer or null")
        object.__setattr__(
            self,
            "effective_from",
            require_utc(self.effective_from, field_name="effective_from"),
        )
        object.__setattr__(
            self,
            "review_by",
            require_utc(self.review_by, field_name="review_by"),
        )
        if self.review_by <= self.effective_from:
            raise ValueError("review_by must be after effective_from")

    def is_usable_at(self, moment: datetime) -> bool:
        """Whether this entry may serve a role at ``moment``.

        A suspended or rolled-back entry, an entry that is not yet effective, and
        an entry past its review date are all refused. Expiry is a governed
        refusal, not a warning, so an unreviewed model cannot quietly keep
        answering a role indefinitely.
        """
        at = require_utc(moment, field_name="moment")
        if self.approval is not ApprovalState.APPROVED:
            return False
        return self.effective_from <= at < self.review_by

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "entry_id": self.entry_id,
            "role": self.role,
            "provider_family": self.provider_family,
            "model_id": self.model_id,
            "endpoint": self.endpoint,
            "prompt_hash": self.prompt_hash,
            "schema_hash": self.schema_hash,
            "owner": self.owner,
            "approval": self.approval,
            "effective_from": self.effective_from,
            "review_by": self.review_by,
            "model_id_kind": self.model_id_kind,
            "reasoning_mode": self.reasoning_mode,
            "max_output_tokens": self.max_output_tokens,
            "deadline_seconds": self.deadline_seconds,
            "max_cost_microunits": self.max_cost_microunits,
            "data_retention_route": self.data_retention_route,
            "provider_reported_version": self.provider_reported_version,
            "rollback_ref": self.rollback_ref,
        }

    @property
    def entry_hash(self) -> str:
        return stable_hash(MODEL_REGISTRY_ENTRY_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class RoleRoute:
    """The resolved, bounded route for one role.

    ``fallback`` is at most one entry and is subject to the *same* request,
    deadline, token reservation, and monetary reservation as the primary. There
    is deliberately no third option and no ability to grant the fallback a fresh
    budget, so a route cannot escalate cost or reasoning effort on its own.
    """

    role: CommitteeRole
    primary: ModelRegistryEntry
    fallback: ModelRegistryEntry | None
    registry_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, CommitteeRole):
            raise ValueError("invalid role")
        if not isinstance(self.primary, ModelRegistryEntry):
            raise ValueError("primary must be a ModelRegistryEntry")
        if self.primary.role is not self.role:
            raise ValueError("primary entry does not serve this role")
        if self.fallback is not None:
            if not isinstance(self.fallback, ModelRegistryEntry):
                raise ValueError("fallback must be a ModelRegistryEntry or null")
            if self.fallback.role is not self.role:
                raise ValueError("fallback entry does not serve this role")
            if self.fallback.entry_id == self.primary.entry_id:
                raise ValueError("a fallback cannot be the primary entry")
        if not isinstance(self.registry_version, str) or not self.registry_version.strip():
            raise ValueError("registry_version is required")

    @property
    def entries(self) -> tuple[ModelRegistryEntry, ...]:
        """Primary first, then an optional fallback. Never more than two."""
        if self.fallback is None:
            return (self.primary,)
        return (self.primary, self.fallback)

    @property
    def shared_budget(self) -> Mapping[str, int | None]:
        """The reservation both attempts share.

        Derived from the primary so a fallback cannot enlarge the budget: the
        ceiling is a property of the role's route, not of an individual attempt.
        """
        return {
            "max_output_tokens": self.primary.max_output_tokens,
            "deadline_seconds": self.primary.deadline_seconds,
            "max_cost_microunits": self.primary.max_cost_microunits,
        }

    def identity_payload(self) -> dict[str, Any]:
        return {
            "registry_version": self.registry_version,
            "role": self.role,
            "primary": self.primary.entry_hash,
            "fallback": None if self.fallback is None else self.fallback.entry_hash,
        }

    @property
    def route_hash(self) -> str:
        return stable_hash(ROLE_ROUTE_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class ModelRegistry:
    """A versioned release of role routes.

    Routes are declared as ``role -> (primary, at most one fallback)``. The
    registry refuses to resolve a role that has no approved route at the moment
    asked, so a missing or expired approval is a visible failure rather than an
    unregistered model being used because it happens to be configured.
    """

    registry_version: str
    entries: tuple[ModelRegistryEntry, ...]
    routes: Mapping[CommitteeRole, tuple[str, str | None]]
    schema_version: int = MODEL_REGISTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_REGISTRY_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported ModelRegistry schema_version")
        if not isinstance(self.registry_version, str) or not self.registry_version.strip():
            raise ValueError("registry_version is required")
        if not isinstance(self.entries, tuple):
            raise ValueError("entries must be a tuple")
        if not isinstance(self.routes, Mapping):
            raise ValueError("routes must be a mapping of role to (primary, fallback)")
        seen: set[str] = set()
        for entry in self.entries:
            if not isinstance(entry, ModelRegistryEntry):
                raise ValueError("entries must be ModelRegistryEntry values")
            if entry.entry_id in seen:
                raise ValueError(f"duplicate registry entry id {entry.entry_id!r}")
            seen.add(entry.entry_id)
        for role, route in self.routes.items():
            if not isinstance(role, CommitteeRole):
                raise ValueError("invalid route role")
            primary_id, fallback_id = route
            if primary_id not in seen:
                raise RegistryError(
                    f"route for {role.value} names unregistered primary {primary_id!r}"
                )
            if fallback_id is not None:
                if fallback_id not in seen:
                    raise RegistryError(
                        f"route for {role.value} names unregistered fallback "
                        f"{fallback_id!r}"
                    )
                if fallback_id == primary_id:
                    raise ValueError(
                        f"route for {role.value} cannot fall back to its primary"
                    )

    def entry(self, entry_id: str) -> ModelRegistryEntry | None:
        for entry in self.entries:
            if entry.entry_id == entry_id:
                return entry
        return None

    def route_for(self, role: CommitteeRole, *, at: datetime) -> RoleRoute:
        """Resolve the approved route for a role at a moment, or fail closed.

        Refusal reasons are distinct so an operator can tell an unregistered role
        apart from an expired or suspended approval, and apart from a route whose
        models are not yet effective.
        """
        if not isinstance(role, CommitteeRole):
            raise RegistryError("invalid role")
        if role not in self.routes:
            raise RegistryError(
                f"no governed route is registered for role {role.value}"
            )
        primary_id, fallback_id = self.routes[role]
        primary = self.entry(primary_id)
        if primary is None:
            raise RegistryError(
                f"route for {role.value} names unregistered primary {primary_id!r}"
            )
        if not primary.is_usable_at(at):
            raise RegistryError(
                f"primary entry {primary.entry_id!r} for {role.value} is not usable at "
                "the requested time (approval state, effective_from, or review_by)"
            )
        fallback = None
        if fallback_id is not None:
            candidate = self.entry(fallback_id)
            if candidate is None:
                raise RegistryError(
                    f"route for {role.value} names unregistered fallback "
                    f"{fallback_id!r}"
                )
            # An unusable fallback is dropped, not substituted. The primary is
            # still a governed route, and inventing a third option is forbidden.
            if candidate.is_usable_at(at):
                fallback = candidate
        return RoleRoute(
            role=role,
            primary=primary,
            fallback=fallback,
            registry_version=self.registry_version,
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registry_version": self.registry_version,
            "entries": tuple(entry.entry_hash for entry in self.entries),
            "routes": tuple(
                sorted(
                    (role.value, primary_id, fallback_id)
                    for role, (primary_id, fallback_id) in self.routes.items()
                )
            ),
        }

    @property
    def registry_hash(self) -> str:
        return stable_hash(MODEL_REGISTRY_IDENTITY_DOMAIN, self.identity_payload())

    def __hash__(self) -> int:
        """Hash by content, not by field identity.

        ``routes`` is a mapping, so the generated dataclass hash would raise on
        an unhashable field. Hashing the content-derived registry identity keeps
        the type usable as a set/dict key without making identity depend on the
        mapping's insertion order.
        """
        return hash(self.registry_hash)


#: The two governed provider families approved for the initial shadow bake-off.
APPROVED_SHADOW_MODELS: Mapping[ProviderFamily, str] = {
    ProviderFamily.OPENAI: "gpt-5.6-terra",
    ProviderFamily.ANTHROPIC: "claude-sonnet-5",
}

#: Approved role route table: each role names a primary family, and the other
#: approved family is its single fallback.
#:
#: The primaries are deliberately distributed across both vendors. If every role
#: used the same primary, the fallback would almost never answer and the committee
#: population would contain only one vendor's opinions - so there would be no
#: genuinely independent vendor evidence for the disagreement matrix to measure.
#: Alternating the primaries makes both vendors answer real roles while every role
#: still has at most one approved fallback.
APPROVED_SHADOW_ROLE_PRIMARIES: Mapping[CommitteeRole, ProviderFamily] = {
    CommitteeRole.REGIME_ANALYST: ProviderFamily.OPENAI,
    CommitteeRole.LIQUIDITY_STRUCTURE_ANALYST: ProviderFamily.ANTHROPIC,
    CommitteeRole.EVENT_SENTIMENT_ANALYST: ProviderFamily.OPENAI,
    CommitteeRole.BULL_ADVOCATE: ProviderFamily.ANTHROPIC,
    CommitteeRole.BEAR_ADVOCATE: ProviderFamily.OPENAI,
    CommitteeRole.RISK_CRITIC: ProviderFamily.ANTHROPIC,
    CommitteeRole.DECISION_SYNTHESIZER: ProviderFamily.OPENAI,
}

#: Initial economic ceilings, as approved. Microunits are 1e-6 of a currency unit,
#: so $0.50 is 500_000 and $10 is 10_000_000.
APPROVED_MAX_CASE_COST_MICROUNITS = 500_000
APPROVED_MAX_DAILY_COST_MICROUNITS = 10_000_000

#: Initial reasoning effort for the approved routes.
APPROVED_SHADOW_REASONING_MODE = ReasoningMode.LOW


def default_shadow_registry(
    *,
    registry_version: str = "committee-shadow-registry-v1",
    effective_from: datetime,
    review_by: datetime,
    prompt_hash: str = "COMMITTEE-PROMPT:pending",
    schema_hash: str = "COMMITTEE-SCHEMA:pending",
    owner: str = "owner",
) -> ModelRegistry:
    """Build the approved initial shadow registry.

    Every entry pins a provider-defined fixed model id, declares ``LOW`` reasoning,
    and carries the approved per-case ceiling. No entry is an alias, and each role
    gets at most one fallback from the other vendor.
    """
    entries: list[ModelRegistryEntry] = []
    routes: dict[CommitteeRole, tuple[str, str | None]] = {}
    # Imported lazily to keep the registry free of a module-level dependency on the
    # transport layer, while still recording the exact allowlisted endpoint so a
    # registry entry and the egress allowlist cannot disagree.
    from app.opip.committee.transports import ALLOWED_ENDPOINTS

    for role, primary_family in APPROVED_SHADOW_ROLE_PRIMARIES.items():
        fallback_family = next(
            family for family in APPROVED_SHADOW_MODELS if family is not primary_family
        )
        primary_id = f"{role.value.lower()}:{primary_family.value}"
        fallback_id = f"{role.value.lower()}:{fallback_family.value}"
        for entry_id, family in ((primary_id, primary_family), (fallback_id, fallback_family)):
            entries.append(
                ModelRegistryEntry(
                    entry_id=entry_id,
                    role=role,
                    provider_family=family,
                    model_id=APPROVED_SHADOW_MODELS[family],
                    endpoint=ALLOWED_ENDPOINTS[family],
                    prompt_hash=prompt_hash,
                    schema_hash=schema_hash,
                    owner=owner,
                    approval=ApprovalState.PROVISIONAL,
                    effective_from=effective_from,
                    review_by=review_by,
                    model_id_kind=ModelIdKind.FIXED,
                    reasoning_mode=APPROVED_SHADOW_REASONING_MODE,
                    max_cost_microunits=APPROVED_MAX_CASE_COST_MICROUNITS,
                )
            )
        routes[role] = (primary_id, fallback_id)
    return ModelRegistry(
        registry_version=registry_version,
        entries=tuple(entries),
        routes=routes,
    )


def assert_result_served_by_route(
    *,
    route: RoleRoute,
    provider_family: ProviderFamily,
    model_id: str,
) -> None:
    """Fail closed unless a served identity is one this route is permitted to use.

    A response whose served provider/model is not on the route must be refused
    rather than attributed, because attributing it would credit a role with an
    opinion from a model the role was never approved to use.
    """
    for entry in route.entries:
        if entry.provider_family is provider_family and entry.model_id == model_id:
            return
    raise RegistryError(
        f"served identity ({provider_family.value}, {model_id!r}) is not on the "
        f"governed route for {route.role.value}"
    )


__all__ = [
    "APPROVED_MAX_CASE_COST_MICROUNITS",
    "APPROVED_MAX_DAILY_COST_MICROUNITS",
    "APPROVED_SHADOW_MODELS",
    "APPROVED_SHADOW_REASONING_MODE",
    "APPROVED_SHADOW_ROLE_PRIMARIES",
    "MODEL_REGISTRY_ENTRY_SCHEMA_VERSION",
    "MODEL_REGISTRY_SCHEMA_VERSION",
    "MODEL_REGISTRY_ENTRY_IDENTITY_DOMAIN",
    "MODEL_REGISTRY_IDENTITY_DOMAIN",
    "ROLE_ROUTE_IDENTITY_DOMAIN",
    "ApprovalState",
    "ModelIdKind",
    "ModelRegistry",
    "ModelRegistryEntry",
    "ReasoningMode",
    "RegistryError",
    "RoleRoute",
    "assert_result_served_by_route",
    "default_shadow_registry",
]
