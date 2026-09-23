"""Phase-C sealed prospective experiment registration and population separation.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Phase C is the only path to evidence that could ever support a profitability
claim, so the experiments must be sealed before results are seen and the two
evidence populations must never mix:

* **Registration is frozen at T0.** A registration binds the corpus version, the
  role routes, the prompt and schema hashes, the research mapping, the horizon,
  and the exact release identity. Because it participates in its own identity, a
  change after sealing produces a different registration rather than a quiet edit.

* **Automatic selection is forbidden.** A sealed experiment names its routes; it
  cannot choose a winner and then declare itself prospective.

* **Populations stay separate.** Retrospective records are research evidence about
  case-control behaviour; prospective records are sealed predictions joined to
  matured outcomes. This module refuses to mix them, and refuses to let a
  retrospective record enter a prospective tally.

* **Maturity is enforced.** A prospective record whose horizon has not elapsed is
  pending, never scored. A drifted release is ineligible, never counted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Iterable, Sequence

from app.opip.committee.contracts import EvaluationPhase
from app.opip.committee.roles import CommitteeRole
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

REGISTRATION_SCHEMA_VERSION = 1
REGISTRATION_IDENTITY_DOMAIN = "COMMITTEE-EXPERIMENT-REGISTRATION"

#: A registration change invalidates the seal, so the outcome is explicit rather
#: than a silent re-registration.
COMPROMISED = "COMPROMISED"


class ExperimentError(ValueError):
    """A prospective experiment contract was violated."""


class PopulationScope(str, Enum):
    """Which evidence population a record belongs to.

    Declared rather than inferred, so a record cannot drift between populations
    depending on who is reading it.
    """

    RETROSPECTIVE = "RETROSPECTIVE"
    PROSPECTIVE = "PROSPECTIVE"


class ProspectiveRecordState(str, Enum):
    """The lifecycle of one prospective record.

    ``INELIGIBLE`` is the home of a drifted release. It is deliberately not a
    failure state: a drifted case is excluded from trust and economic metrics by
    construction, and remains visible for accountability.
    """

    SEALED = "SEALED"
    PENDING_MATURITY = "PENDING_MATURITY"
    MATURED = "MATURED"
    INELIGIBLE = "INELIGIBLE"


@dataclass(frozen=True)
class ResearchMapping:
    """How a role's advisory output maps onto the experiment's research question.

    Frozen with the registration so the interpretation of a result cannot be
    chosen once the result is known.
    """

    mapping_version: str
    supportive_disposition: str
    opposing_disposition: str

    def __post_init__(self) -> None:
        for field_name in (
            "mapping_version",
            "supportive_disposition",
            "opposing_disposition",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "mapping_version": self.mapping_version,
            "supportive_disposition": self.supportive_disposition,
            "opposing_disposition": self.opposing_disposition,
        }


@dataclass(frozen=True)
class RouteSeal:
    """The exact governed route sealed for one role at T0."""

    role: CommitteeRole
    registry_version: str
    primary_entry_id: str
    fallback_entry_id: str | None
    prompt_hash: str
    schema_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, CommitteeRole):
            raise ValueError("invalid role")
        for field_name in (
            "registry_version",
            "primary_entry_id",
            "prompt_hash",
            "schema_hash",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if self.fallback_entry_id is not None and not (
            isinstance(self.fallback_entry_id, str) and self.fallback_entry_id.strip()
        ):
            raise ValueError("fallback_entry_id must be a non-empty string or null")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "registry_version": self.registry_version,
            "primary_entry_id": self.primary_entry_id,
            "fallback_entry_id": self.fallback_entry_id,
            "prompt_hash": self.prompt_hash,
            "schema_hash": self.schema_hash,
        }


@dataclass(frozen=True)
class ExperimentRegistration:
    """A sealed prospective experiment.

    Everything an evaluator could otherwise choose after seeing results is fixed
    here: the corpus, the routes, the research mapping, the horizon, the stopping
    rule, and the exact release identity that is permitted to score it.
    """

    experiment_id: str
    corpus_version: str
    corpus_hash: str
    release_sha: str
    horizon_seconds: int
    stopping_rule: str
    research_mapping: ResearchMapping
    routes: tuple[RouteSeal, ...]
    registered_at: datetime
    schema_version: int = REGISTRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REGISTRATION_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported ExperimentRegistration schema_version")
        for field_name in (
            "experiment_id",
            "corpus_version",
            "corpus_hash",
            "release_sha",
            "stopping_rule",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if type(self.horizon_seconds) is not int or self.horizon_seconds < 1:
            raise ExperimentError("horizon_seconds must be a positive integer")
        if not isinstance(self.research_mapping, ResearchMapping):
            raise ExperimentError("research_mapping is required")
        if not isinstance(self.routes, tuple) or not self.routes:
            raise ExperimentError("a registration seals at least one route")
        seen: set[CommitteeRole] = set()
        for route in self.routes:
            if not isinstance(route, RouteSeal):
                raise ExperimentError("routes must be RouteSeal values")
            if route.role in seen:
                raise ExperimentError(
                    f"duplicate sealed route for {route.role.value}; automatic "
                    "selection would be ambiguous"
                )
            seen.add(route.role)
        object.__setattr__(
            self,
            "registered_at",
            require_utc(self.registered_at, field_name="registered_at"),
        )

    @property
    def experiment_scope(self) -> PopulationScope:
        """A registration is always prospective evidence.

        A retrospective container cannot be registered as an experiment, which is
        what keeps the two populations from being conflated at the source.
        """
        return PopulationScope.PROSPECTIVE

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "corpus_version": self.corpus_version,
            "corpus_hash": self.corpus_hash,
            "release_sha": self.release_sha,
            "horizon_seconds": self.horizon_seconds,
            "stopping_rule": self.stopping_rule,
            "research_mapping": self.research_mapping.identity_payload(),
            "routes": tuple(route.identity_payload() for route in self.routes),
            "registered_at": self.registered_at,
        }

    @property
    def registration_id(self) -> str:
        return stable_hash(REGISTRATION_IDENTITY_DOMAIN, self.identity_payload())

    def resealed(self, **changes: Any) -> "ExperimentRegistration":
        """Produce a new registration, never mutate this one.

        Exposed so an intended change is expressible, while the identity changes
        with it and the previous seal stays auditable.
        """
        return ExperimentRegistration(
            experiment_id=changes.get("experiment_id", self.experiment_id),
            corpus_version=changes.get("corpus_version", self.corpus_version),
            corpus_hash=changes.get("corpus_hash", self.corpus_hash),
            release_sha=changes.get("release_sha", self.release_sha),
            horizon_seconds=changes.get("horizon_seconds", self.horizon_seconds),
            stopping_rule=changes.get("stopping_rule", self.stopping_rule),
            research_mapping=changes.get("research_mapping", self.research_mapping),
            routes=changes.get("routes", self.routes),
            registered_at=changes.get("registered_at", self.registered_at),
        )

    def verify_unchanged(self, other: "ExperimentRegistration") -> None:
        """Fail closed when a registration was edited after sealing."""
        if other.registration_id != self.registration_id:
            raise ExperimentError(
                "the registration changed after sealing; a sealed experiment "
                f"cannot be edited in place (expected {self.registration_id}, "
                f"got {other.registration_id}). Register a new experiment or mark "
                f"{COMPROMISED}."
            )

    def maturity_at(self, *, sealed_at: datetime) -> datetime:
        """When an outcome for a prediction sealed at ``sealed_at`` becomes mature."""
        sealed = require_utc(sealed_at, field_name="sealed_at")
        return sealed + timedelta(seconds=self.horizon_seconds)

    def state_for(
        self,
        *,
        sealed_at: datetime,
        observed_release_sha: str,
        evaluated_at: datetime,
        matured: bool,
    ) -> ProspectiveRecordState:
        """Classify one prospective record without ever scoring a drifted one."""
        moment = require_utc(evaluated_at, field_name="evaluated_at")
        if observed_release_sha != self.release_sha:
            return ProspectiveRecordState.INELIGIBLE
        if moment < self.maturity_at(sealed_at=sealed_at):
            return ProspectiveRecordState.PENDING_MATURITY
        return (
            ProspectiveRecordState.MATURED
            if matured
            else ProspectiveRecordState.PENDING_MATURITY
        )


@dataclass(frozen=True)
class PopulationRecord:
    """One evidence record together with the population it belongs to."""

    record_id: str
    scope: PopulationScope
    phase: EvaluationPhase
    prospective_state: ProspectiveRecordState | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.record_id, str) or not self.record_id.strip():
            raise ValueError("record_id is required")
        if not isinstance(self.scope, PopulationScope):
            raise ValueError("invalid scope")
        if not isinstance(self.phase, EvaluationPhase):
            raise ValueError("invalid phase")
        if self.scope is PopulationScope.RETROSPECTIVE:
            if self.phase is not EvaluationPhase.RETROSPECTIVE:
                raise ExperimentError(
                    "a retrospective record cannot carry a prospective phase; the "
                    "populations must not be mixed"
                )
        if self.scope is PopulationScope.PROSPECTIVE and (
            self.phase is not EvaluationPhase.PROSPECTIVE
        ):
            raise ExperimentError(
                "a prospective record cannot carry a retrospective phase"
            )
        if self.scope is PopulationScope.PROSPECTIVE and self.prospective_state is None:
            raise ExperimentError(
                "a prospective record must declare its maturity state, so a "
                "pending record cannot be counted as matured by omission"
            )
        if self.scope is PopulationScope.RETROSPECTIVE and self.prospective_state is not None:
            raise ExperimentError(
                "a retrospective record has no prospective maturity state"
            )


@dataclass(frozen=True)
class ProspectiveTally:
    """Counts over the prospective population only.

    A retrospective or ineligible record is excluded by construction rather than
    by a filter a caller might forget, so a drifted or unevaluated case can never
    contribute to a prospective claim.
    """

    sealed: int = 0
    pending_maturity: int = 0
    matured: int = 0
    ineligible: int = 0

    @classmethod
    def from_records(cls, records: Iterable[PopulationRecord]) -> "ProspectiveTally":
        sealed = pending = matured = ineligible = 0
        for record in records:
            if record.scope is not PopulationScope.PROSPECTIVE:
                # Excluded, not silently counted: the population boundary is the
                # point of the tally.
                continue
            if record.prospective_state is ProspectiveRecordState.SEALED:
                sealed += 1
            elif record.prospective_state is ProspectiveRecordState.PENDING_MATURITY:
                pending += 1
            elif record.prospective_state is ProspectiveRecordState.MATURED:
                matured += 1
            elif record.prospective_state is ProspectiveRecordState.INELIGIBLE:
                ineligible += 1
        return cls(
            sealed=sealed,
            pending_maturity=pending,
            matured=matured,
            ineligible=ineligible,
        )

    @property
    def total(self) -> int:
        return self.sealed + self.pending_maturity + self.matured + self.ineligible

    @property
    def scorable(self) -> int:
        """Only matured records may contribute to a prospective claim."""
        return self.matured


def build_registration(
    *,
    experiment_id: str,
    corpus_version: str,
    corpus_hash: str,
    release_sha: str,
    horizon_seconds: int,
    stopping_rule: str,
    research_mapping: ResearchMapping,
    routes: Sequence[RouteSeal],
    registered_at: datetime,
) -> ExperimentRegistration:
    """Seal a prospective experiment before any outcome is observed."""
    return ExperimentRegistration(
        experiment_id=experiment_id,
        corpus_version=corpus_version,
        corpus_hash=corpus_hash,
        release_sha=release_sha,
        horizon_seconds=horizon_seconds,
        stopping_rule=stopping_rule,
        research_mapping=research_mapping,
        routes=tuple(routes),
        registered_at=registered_at,
    )


__all__ = [
    "COMPROMISED",
    "REGISTRATION_IDENTITY_DOMAIN",
    "REGISTRATION_SCHEMA_VERSION",
    "ExperimentError",
    "ExperimentRegistration",
    "PopulationRecord",
    "PopulationScope",
    "ProspectiveRecordState",
    "ProspectiveTally",
    "ResearchMapping",
    "RouteSeal",
    "build_registration",
]
