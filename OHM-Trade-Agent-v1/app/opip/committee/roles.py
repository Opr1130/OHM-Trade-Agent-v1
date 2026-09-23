"""Role-based committee identity.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

A committee seat is a **role**, not a model vendor. The role states what question
a seat is answering; the model registry (see :mod:`app.opip.committee.registry`)
states which governed provider/model route is permitted to answer it. Those two
identities are deliberately separate:

* a role's result is attributable to the role regardless of which approved model
  served it, so a provider swap does not silently change what the committee
  claims to have measured;
* a provider identity can never be used to stand in for a missing role, which is
  why :attr:`ProviderFamily.INDEPENDENT_REVIEWER` is a reserved *provider* seat
  and not a role here;
* the bull case, the bear case, and the risk critique are separate roles with
  separate contracts. Collapsing them into one "analysis" seat would destroy the
  disagreement structure the attribution layer measures.

An optional role is not a role that may be invented. ``EVENT_SENTIMENT_ANALYST``
depends on qualified retained event/sentiment evidence; when that evidence does
not exist the role resolves to an explicit ``UNKNOWN`` result rather than a
fabricated opinion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from app.opip.decision_intelligence.serialization import stable_hash

#: Schema version for the role-spec contract.
COMMITTEE_ROLE_SPEC_SCHEMA_VERSION = 1

#: Identity domain for a single role's contract.
ROLE_SPEC_IDENTITY_DOMAIN = "COMMITTEE-ROLE-SPEC"

#: Identity domain for a canonical role-spec set.
ROLE_SPEC_SET_IDENTITY_DOMAIN = "COMMITTEE-ROLE-SPECS"


class CommitteeRole(str, Enum):
    """The governed roles the committee may seat.

    A role is an analytical responsibility, not a provider. Nothing here grants
    admission, ranking, sizing, protection, or execution meaning: every role
    produces advisory research output only.
    """

    REGIME_ANALYST = "REGIME_ANALYST"
    LIQUIDITY_STRUCTURE_ANALYST = "LIQUIDITY_STRUCTURE_ANALYST"
    EVENT_SENTIMENT_ANALYST = "EVENT_SENTIMENT_ANALYST"
    BULL_ADVOCATE = "BULL_ADVOCATE"
    BEAR_ADVOCATE = "BEAR_ADVOCATE"
    RISK_CRITIC = "RISK_CRITIC"
    DECISION_SYNTHESIZER = "DECISION_SYNTHESIZER"


class RoleRequirement(str, Enum):
    """Whether a role must produce a result for a case to be complete.

    ``OPTIONAL`` does not mean skippable-by-omission. An optional role whose
    required evidence is absent must report ``UNKNOWN`` explicitly, so the gap is
    visible in the case outcome rather than being silently absent from it.
    """

    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


#: Roles that must always be seated. A case missing any of these is incomplete
#: and must say so, because the bull/bear/risk contrast is the point of the
#: committee, not an optional extra.
REQUIRED_ROLES: frozenset[CommitteeRole] = frozenset(
    {
        CommitteeRole.REGIME_ANALYST,
        CommitteeRole.LIQUIDITY_STRUCTURE_ANALYST,
        CommitteeRole.BULL_ADVOCATE,
        CommitteeRole.BEAR_ADVOCATE,
        CommitteeRole.RISK_CRITIC,
        CommitteeRole.DECISION_SYNTHESIZER,
    }
)

#: Roles that may legitimately have no opinion because the evidence they need may
#: genuinely not exist. They must still be reported, as ``UNKNOWN``.
OPTIONAL_ROLES: frozenset[CommitteeRole] = frozenset(
    {CommitteeRole.EVENT_SENTIMENT_ANALYST}
)

#: Evidence a role needs before its opinion means anything. Used to decide
#: whether an optional role can answer at all, rather than letting it invent a
#: view from unrelated evidence.
ROLE_EVIDENCE_DEPENDENCY: Mapping[CommitteeRole, str] = {
    CommitteeRole.EVENT_SENTIMENT_ANALYST: "qualified_retained_event_evidence",
    CommitteeRole.REGIME_ANALYST: "market_regime_evidence",
    CommitteeRole.LIQUIDITY_STRUCTURE_ANALYST: "liquidity_structure_evidence",
    CommitteeRole.BULL_ADVOCATE: "screened_evidence_view",
    CommitteeRole.BEAR_ADVOCATE: "screened_evidence_view",
    CommitteeRole.RISK_CRITIC: "screened_evidence_view",
    CommitteeRole.DECISION_SYNTHESIZER: "role_opinions",
}


@dataclass(frozen=True)
class CommitteeRoleSpec:
    """The versioned contract for one role.

    The prompt and schema versions travel with the role so a result can never be
    attributed to a prompt or schema other than the one that produced it.
    """

    role: CommitteeRole
    role_version: str
    requirement: RoleRequirement
    prompt_template_id: str
    prompt_version: str
    schema_version: int = COMMITTEE_ROLE_SPEC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.role, CommitteeRole):
            raise ValueError("invalid committee role")
        if not isinstance(self.requirement, RoleRequirement):
            raise ValueError("invalid role requirement")
        for field_name in ("role_version", "prompt_template_id", "prompt_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("schema_version must be a positive integer")

    @property
    def evidence_dependency(self) -> str:
        """The evidence this role needs before its opinion is meaningful."""
        return ROLE_EVIDENCE_DEPENDENCY[self.role]

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "role": self.role,
            "role_version": self.role_version,
            "requirement": self.requirement,
            "prompt_template_id": self.prompt_template_id,
            "prompt_version": self.prompt_version,
            "evidence_dependency": self.evidence_dependency,
        }

    @property
    def spec_hash(self) -> str:
        """Content-derived identity of this role contract.

        Uses its own domain: a spec and a spec *set* are different record kinds,
        and the repository's convention is one domain per kind so two kinds can
        never collide on a digest.
        """
        return stable_hash(ROLE_SPEC_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class RoleSpecSet:
    """A versioned, complete set of role contracts for a committee policy.

    A set is *complete* only when every required role is present. Partial sets
    are permitted during construction of a policy, but :meth:`validate_complete`
    must pass before a case may be run, so a policy cannot silently omit the bear
    case and still produce a "committee" result.
    """

    spec_set_version: str
    specs: tuple[CommitteeRoleSpec, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.spec_set_version, str) or not self.spec_set_version.strip():
            raise ValueError("spec_set_version is required")
        if not isinstance(self.specs, tuple) or not self.specs:
            raise ValueError("RoleSpecSet requires at least one role spec")
        seen: set[CommitteeRole] = set()
        for spec in self.specs:
            if not isinstance(spec, CommitteeRoleSpec):
                raise ValueError("specs must be CommitteeRoleSpec values")
            if spec.role in seen:
                raise ValueError(f"duplicate role spec for {spec.role.value}")
            seen.add(spec.role)
        for role in REQUIRED_ROLES:
            if role in seen:
                spec = next(item for item in self.specs if item.role is role)
                if spec.requirement is not RoleRequirement.REQUIRED:
                    raise ValueError(
                        f"{role.value} is a required role and cannot be declared optional"
                    )
        for role in OPTIONAL_ROLES:
            if role in seen:
                spec = next(item for item in self.specs if item.role is role)
                if spec.requirement is not RoleRequirement.OPTIONAL:
                    raise ValueError(
                        f"{role.value} depends on evidence that may not exist and "
                        "must be declared optional"
                    )

    def spec_for(self, role: CommitteeRole) -> CommitteeRoleSpec | None:
        for spec in self.specs:
            if spec.role is role:
                return spec
        return None

    def roles(self) -> tuple[CommitteeRole, ...]:
        return tuple(spec.role for spec in self.specs)

    def missing_required_roles(self) -> tuple[CommitteeRole, ...]:
        """Required roles this set does not seat, in a stable order."""
        present = {spec.role for spec in self.specs}
        return tuple(
            role for role in CommitteeRole if role in REQUIRED_ROLES and role not in present
        )

    def validate_complete(self) -> None:
        """Fail closed unless every required role is seated."""
        missing = self.missing_required_roles()
        if missing:
            raise ValueError(
                "a committee case requires every required role; missing "
                f"{[role.value for role in missing]}"
            )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "spec_set_version": self.spec_set_version,
            "specs": tuple(spec.identity_payload() for spec in self.specs),
        }

    @property
    def spec_set_hash(self) -> str:
        return stable_hash(ROLE_SPEC_SET_IDENTITY_DOMAIN, self.identity_payload())


def default_role_spec_set(version: str = "committee-roles-v1") -> RoleSpecSet:
    """The canonical role set.

    Each role carries its own prompt template so one role's instructions can be
    revised without silently changing another role's meaning.
    """
    return RoleSpecSet(
        spec_set_version=version,
        specs=(
            CommitteeRoleSpec(
                role=CommitteeRole.REGIME_ANALYST,
                role_version="1",
                requirement=RoleRequirement.REQUIRED,
                prompt_template_id="committee.role.regime_analyst",
                prompt_version="1",
            ),
            CommitteeRoleSpec(
                role=CommitteeRole.LIQUIDITY_STRUCTURE_ANALYST,
                role_version="1",
                requirement=RoleRequirement.REQUIRED,
                prompt_template_id="committee.role.liquidity_structure",
                prompt_version="1",
            ),
            CommitteeRoleSpec(
                role=CommitteeRole.EVENT_SENTIMENT_ANALYST,
                role_version="1",
                requirement=RoleRequirement.OPTIONAL,
                prompt_template_id="committee.role.event_sentiment",
                prompt_version="1",
            ),
            CommitteeRoleSpec(
                role=CommitteeRole.BULL_ADVOCATE,
                role_version="1",
                requirement=RoleRequirement.REQUIRED,
                prompt_template_id="committee.role.bull_advocate",
                prompt_version="1",
            ),
            CommitteeRoleSpec(
                role=CommitteeRole.BEAR_ADVOCATE,
                role_version="1",
                requirement=RoleRequirement.REQUIRED,
                prompt_template_id="committee.role.bear_advocate",
                prompt_version="1",
            ),
            CommitteeRoleSpec(
                role=CommitteeRole.RISK_CRITIC,
                role_version="1",
                requirement=RoleRequirement.REQUIRED,
                prompt_template_id="committee.role.risk_critic",
                prompt_version="1",
            ),
            CommitteeRoleSpec(
                role=CommitteeRole.DECISION_SYNTHESIZER,
                role_version="1",
                requirement=RoleRequirement.REQUIRED,
                prompt_template_id="committee.role.decision_synthesizer",
                prompt_version="1",
            ),
        ),
    )


__all__ = [
    "COMMITTEE_ROLE_SPEC_SCHEMA_VERSION",
    "OPTIONAL_ROLES",
    "REQUIRED_ROLES",
    "ROLE_EVIDENCE_DEPENDENCY",
    "ROLE_SPEC_IDENTITY_DOMAIN",
    "ROLE_SPEC_SET_IDENTITY_DOMAIN",
    "CommitteeRole",
    "CommitteeRoleSpec",
    "RoleRequirement",
    "RoleSpecSet",
    "default_role_spec_set",
]
